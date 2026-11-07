#!/usr/bin/env python3
"""Install the narrow local system helper using the installed PanelPilot Python."""
import argparse
import configparser
import json
import os
from pathlib import Path
import pwd
import re
import shutil
import subprocess
from panelpilot.admin import Engine, STATE, render_maintenance
from panelpilot.storage import atomic_write, write_json
from panelpilot.system_models import Maintenance, NFS

BASE=Path(__file__).resolve().parents[1]


def run(*args,**kwargs): return subprocess.run(args,check=True,text=True,timeout=60,**kwargs)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mountpoint',default='/mnt/panelpilot_photos')
    parser.add_argument('--fresh-maintenance',action='store_true')
    args=parser.parse_args()
    if os.geteuid()!=0: parser.error('Run as root with the installed PanelPilot Python.')
    runtime=json.loads(Path('/etc/panelpilot/runtime.json').read_text())
    user=pwd.getpwnam(runtime['user'])
    config_path=Path('/etc/panelpilot/admin.json')
    if config_path.exists():
        config=json.loads(config_path.read_text())
        if config['uid']!=user.pw_uid: parser.error('System helper belongs to a different panel account.')
    else:
        config={'user':user.pw_name,'uid':user.pw_uid,'gid':user.pw_gid,'mountpoint':args.mountpoint,
                'photo_probe':str(Path(runtime['display_script']).with_name('photo_probe.py'))}
    engine=Engine(config)
    uploads=Path(runtime.get('upload_directory',str(Path(runtime['state_directory'])/'photos')))
    uploads.mkdir(parents=True,exist_ok=True,mode=0o700); os.chown(uploads,user.pw_uid,user.pw_gid)
    runtime['upload_directory']=str(uploads)
    write_json(Path('/etc/panelpilot/runtime.json'),runtime,0o640)
    os.chown('/etc/panelpilot/runtime.json',0,user.pw_gid)
    write_json(config_path,config)
    state=Path(STATE); state.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    if not state.exists():
        maintenance=Maintenance(); nfs=NFS()
        upgrade=Path('/etc/systemd/system/apt-daily-upgrade.timer.d/90-panelpilot.conf')
        reboot=Path('/etc/systemd/system/panelpilot-weekly-reboot.timer')
        if upgrade.exists():
            match=re.search(r'OnCalendar=\*-\*-\* (\d\d:\d\d):\d\d (\S+)',upgrade.read_text())
            if match: maintenance.update_time,maintenance.timezone=match.groups()
        if reboot.exists():
            match=re.search(r'OnCalendar=(Mon|Tue|Wed|Thu|Fri|Sat|Sun) \*-\*-\* (\d\d:\d\d):\d\d (\S+)',reboot.read_text())
            if match: maintenance.reboot_day,maintenance.reboot_time,maintenance.timezone=match.groups()
        for field,unit in [('enabled','apt-daily-upgrade.timer'),('reboot_enabled','panelpilot-weekly-reboot.timer')]:
            if not args.fresh_maintenance:
                setattr(maintenance,field,subprocess.run(['systemctl','is-enabled',unit],capture_output=True).returncode==0)
        apt=Path('/etc/apt/apt.conf.d/99zz-panelpilot-maintenance')
        if apt.exists(): maintenance.only_ac='OnlyOnACPower "false"' not in apt.read_text()
        mount=Path(engine.mount_file)
        if mount.exists():
            ini=configparser.ConfigParser(interpolation=None); ini.read(mount)
            what=ini.get('Mount','What',fallback='')
            if ':' in what:
                host,export=what.split(':',1); options=ini.get('Mount','Options',fallback='')
                version=next((v.split('=',1)[1] for v in options.split(',') if v.startswith(('vers=','nfsvers='))),'3')
                nfs=NFS(enabled=True,host=host,export=export,version=version)
        write_json(state,{'maintenance':maintenance.model_dump(),'nfs':nfs.model_dump()})
    dest=Path('/usr/local/lib/panelpilot/weekly-reboot.py'); dest.parent.mkdir(parents=True,exist_ok=True)
    shutil.copyfile(BASE/'system/weekly-reboot.py',dest); dest.chmod(0o644)
    service=Path('/etc/systemd/system/panelpilot-weekly-reboot.service')
    if not service.exists(): atomic_write(service,(BASE/'system/panelpilot-weekly-reboot.service').read_text(),0o644)
    unit='''[Unit]
Description=PanelPilot local system management
After=local-fs.target
[Service]
Type=simple
User=root
Group={group}
ExecStart=/opt/panelpilot/venv/bin/python -m panelpilot.admin
RuntimeDirectory=panelpilot-admin
RuntimeDirectoryMode=0750
UMask=0077
NoNewPrivileges=yes
RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6 AF_NETLINK
Restart=on-failure
RestartSec=5
TasksMax=64
MemoryMax=256M
[Install]
WantedBy=multi-user.target
'''.format(group=user.pw_gid)
    atomic_write(Path('/etc/systemd/system/panelpilot-admin.service'),unit,0o644)
    run('systemctl','daemon-reload')
    run('systemd-analyze','verify','/etc/systemd/system/panelpilot-admin.service')
    if args.fresh_maintenance:
        engine.maintenance(engine.state()['maintenance'])
    run('systemctl','enable','--now','panelpilot-admin.service')
    run('systemctl','restart','panelpilot-admin.service')
    print('Local system management installed. No reboot requested.')


if __name__=='__main__': main()
