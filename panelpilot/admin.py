"""Root-only local broker. No command, target-path or mount-option parameters."""
from datetime import datetime, timezone
import ipaddress
import json
import os
from pathlib import Path
import re
import secrets
import socket
import socketserver
import struct
import subprocess
import threading
import time

from .storage import atomic_write, write_json
from .system_models import Maintenance, NFS

CONFIG=Path('/etc/panelpilot/admin.json')
STATE='/var/lib/panelpilot-admin/state.json'
APT='/etc/apt/apt.conf.d/99zz-panelpilot-maintenance'
DOWNLOAD='/etc/systemd/system/apt-daily.timer.d/90-panelpilot.conf'
UPGRADE='/etc/systemd/system/apt-daily-upgrade.timer.d/90-panelpilot.conf'
AC_CONDITION='/etc/systemd/system/apt-daily-upgrade.service.d/90-panelpilot.conf'
REBOOT='/etc/systemd/system/panelpilot-weekly-reboot.timer'
TIMERS=('apt-daily.timer','apt-daily-upgrade.timer','panelpilot-weekly-reboot.timer')
ORIGINS=('Ubuntu:${distro_codename}','Ubuntu:${distro_codename}-security','Ubuntu:${distro_codename}-updates',
         'UbuntuESMApps:${distro_codename}-apps-security','UbuntuESM:${distro_codename}-infra-security',
         'LP-PPA-zorinos-stable:${distro_codename}','LP-PPA-zorinos-patches:${distro_codename}',
         'LP-PPA-zorinos-apps:${distro_codename}','LP-PPA-zorinos-drivers:${distro_codename}','Brave Software:stable')


def command(*args,check=True,timeout=30):
    return subprocess.run(args,check=check,timeout=timeout,text=True,capture_output=True,
                          env={'PATH':'/usr/sbin:/usr/bin:/sbin:/bin','LANG':'C.UTF-8'},cwd='/')


def timer(calendar,persistent=True,random='5m',unit=None):
    text='[Timer]\nOnCalendar=\nOnCalendar='+calendar+'\nRandomizedDelaySec='+random+'\nAccuracySec=1s\nPersistent='+('true' if persistent else 'false')+'\n'
    if unit: text+='Unit='+unit+'\n[Install]\nWantedBy=timers.target\n'
    return text


def render_maintenance(value):
    hh,mm=map(int,value.update_time.split(':')); prior=(hh*60+mm-30)%(24*60)
    enabled='1' if value.enabled else '0'
    apt='APT::Periodic::Enable "1";\nAPT::Periodic::Update-Package-Lists "'+enabled+'";\nAPT::Periodic::Unattended-Upgrade "'+enabled+'";\n'
    apt+='#clear Unattended-Upgrade::Allowed-Origins;\n#clear Unattended-Upgrade::Origins-Pattern;\nUnattended-Upgrade::Allowed-Origins {\n'
    apt+=''.join(' "'+origin+'";\n' for origin in ORIGINS)+'};\n'
    for key,setting in {'Automatic-Reboot':False,'InstallOnShutdown':False,'OnlyOnACPower':value.only_ac,'Skip-Updates-On-Metered-Connections':True,'MinimalSteps':True,'DevRelease':False}.items():
        apt+='Unattended-Upgrade::'+key+' "'+str(setting).lower()+'";\n'
    return {APT:apt,
            DOWNLOAD:timer(f'*-*-* {prior//60:02}:{prior%60:02}:00 {value.timezone}'),
            UPGRADE:timer(f'*-*-* {value.update_time}:00 {value.timezone}'),
            AC_CONDITION:'[Unit]\nConditionACPower=\n'+('ConditionACPower=true\n' if value.only_ac else ''),
            REBOOT:timer(f'{value.reboot_day} *-*-* {value.reboot_time}:00 {value.timezone}',False,'0','panelpilot-weekly-reboot.service')}


class Engine:
    def __init__(self,config,root=Path('/'),runner=command):
        self.config,self.root,self.run=config,Path(root),runner
        self.mountpoint=config['mountpoint']
        if not re.fullmatch(r'/mnt/[A-Za-z0-9_]+',self.mountpoint): raise ValueError('Invalid administrator mountpoint')
        self.mount_unit='mnt-'+self.mountpoint.split('/')[-1]+'.mount'
        self.auto_unit=self.mount_unit.replace('.mount','.automount')
        self.mount_file='/etc/systemd/system/'+self.mount_unit
        self.auto_file='/etc/systemd/system/'+self.auto_unit
        self.lock=threading.Lock()
        self.lease=None

    def path(self,value): return self.root/value.lstrip('/')

    def state(self):
        return json.loads(self.path(STATE).read_text())

    def unit_state(self,units):
        return {u:{'enabled':self.run('/usr/bin/systemctl','is-enabled',u,check=False).returncode==0,
                   'active':self.run('/usr/bin/systemctl','is-active',u,check=False).returncode==0} for u in units}

    def transaction(self,files,units,apply,new_state):
        old={name:self.path(name).read_text() if self.path(name).exists() else None for name in files}
        states=self.unit_state(units)
        backup=self.path('/var/backups/panelpilot-admin')/datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
        backup.mkdir(parents=True,mode=0o700)
        write_json(backup/'snapshot.json',{'files':old,'units':states,'state':self.state()})
        try:
            for name,text in files.items():
                if self.path(name).is_symlink(): raise ValueError('Symlink als Systemdatei nicht erlaubt.')
                atomic_write(self.path(name),text,0o644)
            self.run('/usr/bin/systemctl','daemon-reload')
            apply()
            write_json(self.path(STATE),new_state)
        except Exception as exc:
            errors=[]
            for unit in units:
                try: self.run('/usr/bin/systemctl','stop',unit,check=False)
                except subprocess.SubprocessError: errors.append(unit)
            for name,text in old.items():
                try:
                    if text is None: self.path(name).unlink(missing_ok=True)
                    else: atomic_write(self.path(name),text,0o644)
                except OSError: errors.append(name)
            self.run('/usr/bin/systemctl','daemon-reload',check=False)
            for unit,status in states.items():
                try:
                    self.run('/usr/bin/systemctl','enable' if status['enabled'] else 'disable',unit,check=False)
                    # These are timers/mounts, never the reboot service itself.
                    self.run('/usr/bin/systemctl','restart' if status['active'] else 'stop',unit,check=False)
                except subprocess.SubprocessError: errors.append(unit)
            message='Übernahme fehlgeschlagen; vorherige Konfiguration wiederhergestellt.'
            if errors: message='Übernahme fehlgeschlagen; Rücknahme unvollständig. Systemprotokoll prüfen.'
            raise RuntimeError(message) from exc

    def maintenance(self,data):
        value=Maintenance.model_validate(data)
        if value.model_dump()==self.state()['maintenance'] and all(self.path(p).exists() and self.path(p).read_text()==text for p,text in render_maintenance(value).items()):
            return self.status()
        def apply():
            self.run('/usr/bin/systemd-analyze','verify',REBOOT)
            for unit,enabled in zip(TIMERS,(value.enabled,value.enabled,value.reboot_enabled)):
                self.run('/usr/bin/systemctl','enable' if enabled else 'disable',unit)
                self.run('/usr/bin/systemctl','restart' if enabled else 'stop',unit)
            if not value.reboot_enabled:
                self.run('/usr/bin/systemctl','stop','panelpilot-weekly-reboot.service')
        state=self.state(); state['maintenance']=value.model_dump()
        self.transaction(render_maintenance(value),TIMERS,apply,state)
        return self.status()

    def source(self,value):
        value.require_source()
        result=self.run('/usr/bin/getent','ahostsv4',value.host,timeout=5)
        addresses={line.split()[0] for line in result.stdout.splitlines() if line.strip()}
        private=[ipaddress.ip_network(x) for x in ('10.0.0.0/8','172.16.0.0/12','192.168.0.0/16')]
        if not addresses or any(not any(ipaddress.ip_address(a) in net for net in private) for a in addresses):
            raise ValueError('Der NFS-Server muss auf eine private IPv4-Adresse im Heimnetz auflösen.')
        # Pin the checked address for this mount; no second DNS lookup by mount.nfs.
        return sorted(addresses)[0]+':'+value.export

    def options(self,value):
        return 'ro,nosuid,nodev,noexec,vers='+value.version+',proto=tcp,soft,timeo=20,retrans=2,retry=0'

    def nfs_begin(self,data):
        value=NFS.model_validate(data); source=self.source(value)
        point=self.path('/run/panelpilot-admin/nfs-test'); point.mkdir(parents=True,exist_ok=True,mode=0o755)
        if self.lease or os.path.ismount(point): raise RuntimeError('Eine vorherige NFS-Prüfung ist noch aktiv. Bitte kurz warten.')
        try:
            self.run('/usr/bin/mount','-t','nfs','-o',self.options(value),source,str(point),timeout=25)
        except Exception:
            if os.path.ismount(point): self.run('/usr/bin/umount',str(point),timeout=15)
            raise
        token=secrets.token_hex(32); self.lease=(token,time.monotonic()+45)
        return {'token':token,'path':str(point)}

    def nfs_end(self,data):
        if set(data)!={'token'} or not isinstance(data['token'],str) or not self.lease or not secrets.compare_digest(self.lease[0],data['token']):
            raise ValueError('Ungültige NFS-Prüfsitzung.')
        point=self.path('/run/panelpilot-admin/nfs-test')
        if os.path.ismount(point): self.run('/usr/bin/umount',str(point),timeout=15)
        self.lease=None
        return {'success':True}

    def cleanup(self):
        # Clean up even if the web client is disconnected or restarted during its probe.
        with self.lock:
            if self.lease and self.lease[1]<=time.monotonic():
                self.nfs_end({'token':self.lease[0]})

    def nfs(self,data):
        value=NFS.model_validate(data)
        if value.enabled:
            source=self.source(value)
        else: source='127.0.0.1:/disabled'
        point=self.path(self.mountpoint); point.mkdir(parents=True,exist_ok=True,mode=0o755)
        files={self.mount_file:'[Unit]\nDescription=PanelPilot read-only network photos\nWants=network-online.target\nAfter=network-online.target\n[Mount]\nWhat='+source+'\nWhere='+self.mountpoint+'\nType=nfs\nOptions='+self.options(value)+'\nTimeoutSec=30\n',
               self.auto_file:'[Unit]\nDescription=PanelPilot photo automount\n[Automount]\nWhere='+self.mountpoint+'\nTimeoutIdleSec=120\n[Install]\nWantedBy=multi-user.target\n'}
        if value.model_dump()==self.state()['nfs'] and all(self.path(p).exists() and self.path(p).read_text()==text for p,text in files.items()):
            return self.status()
        def apply():
            self.run('/usr/bin/systemctl','stop',self.auto_unit,self.mount_unit,timeout=40)
            self.run('/usr/bin/systemd-analyze','verify',self.mount_file,self.auto_file)
            self.run('/usr/bin/systemctl','enable' if value.enabled else 'disable',self.auto_unit)
            if value.enabled: self.run('/usr/bin/systemctl','start',self.auto_unit)
        state=self.state(); state['nfs']=value.model_dump()
        self.transaction(files,(self.auto_unit,self.mount_unit),apply,state)
        return self.status()

    def status(self):
        state=self.state(); timers={}
        for unit in TIMERS:
            result=self.run('/usr/bin/systemctl','show',unit,'-p','NextElapseUSecRealtime','-p','ActiveState','-p','LastTriggerUSec',check=False)
            timers[unit]=dict(line.split('=',1) for line in result.stdout.splitlines() if '=' in line)
        mounted=self.run('/usr/bin/findmnt','-rn','-M',self.mountpoint,'-t','nfs,nfs4','-o','SOURCE',check=False)
        return {**state,'mountpoint':self.mountpoint,'mounted':mounted.returncode==0 and bool(mounted.stdout.strip()),
                'timers':timers,'snap':Path('/snap/bin/chromium').exists(),'demo':False}

    def call(self,request):
        if set(request)!={'action','data'} or not isinstance(request['data'],dict): raise ValueError('Ungültige Anfrage.')
        with self.lock:
            if request['action']=='status': return self.status()
            if request['action']=='maintenance.apply': return self.maintenance(request['data'])
            if request['action']=='nfs.apply': return self.nfs(request['data'])
            if request['action']=='nfs.test.begin': return self.nfs_begin(request['data'])
            if request['action']=='nfs.test.end': return self.nfs_end(request['data'])
            raise ValueError('Unbekannte Systemaktion.')


def main():
    if os.geteuid()!=0: raise SystemExit('The system helper must run as root.')
    config=json.loads(CONFIG.read_text()); engine=Engine(config)
    class Handler(socketserver.StreamRequestHandler):
        def handle(self):
            self.request.settimeout(180)
            pid,uid,gid=struct.unpack('3i',self.request.getsockopt(socket.SOL_SOCKET,socket.SO_PEERCRED,12))
            if uid not in (0,config['uid']): return
            try:
                raw=self.rfile.readline(16385)
                if len(raw)>16384 or not raw.endswith(b'\n'): raise ValueError('Anfrage zu groß.')
                result={'ok':True,'result':engine.call(json.loads(raw))}
            except Exception as exc:
                print(type(exc).__name__+': '+str(exc),flush=True)
                if isinstance(exc,subprocess.CalledProcessError) and exc.stderr:
                    print(exc.stderr[-2000:],flush=True)
                message=str(exc) if isinstance(exc,(ValueError,RuntimeError)) else 'Systemaktion fehlgeschlagen. Systemprotokoll prüfen.'
                result={'ok':False,'error':message[:500]}
            self.wfile.write(json.dumps(result).encode()+b'\n')
    path=Path('/run/panelpilot-admin/control.sock'); path.unlink(missing_ok=True)
    class Server(socketserver.ThreadingUnixStreamServer):
        def service_actions(self):
            try: engine.cleanup()
            except Exception as exc: print('NFS test cleanup: '+str(exc),flush=True)
    # A previous process may have stopped between mounting and cleaning its test folder.
    point=Path('/run/panelpilot-admin/nfs-test')
    if os.path.ismount(point): command('/usr/bin/umount',str(point),timeout=15)
    with Server(str(path),Handler) as server:
        os.chown(path,0,config['gid']); path.chmod(0o660)
        server.daemon_threads=True
        server.serve_forever()


if __name__=='__main__': main()
