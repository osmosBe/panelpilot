#!/usr/bin/python3
"""Install PanelPilot alongside existing systemd user panel services, offline."""
import argparse
import configparser
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import pwd
import re
import secrets
import shlex
import socket
import subprocess
import sys

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(BASE))
from panelpilot.auth import make_credentials
from panelpilot.storage import write_json, atomic_write


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--user', required=True, help='Existing non-root kiosk user')
    parser.add_argument('--owner', required=True, help='Local owner who can edit settings')
    parser.add_argument('--hostname', required=True, help='LAN DNS name of this panel')
    parser.add_argument('--address', action='append', default=[], help='Additional IP address for TLS and Host validation')
    parser.add_argument('--port', type=int, default=8443)
    parser.add_argument('--config-dir', default='/srv/panelpilot-config')
    parser.add_argument('--wheelhouse', required=True)
    parser.add_argument('--kiosk-unit', default='ha-kiosk.service')
    parser.add_argument('--display-unit', default='ha-panel-display.service')
    parser.add_argument('--voice-unit', default='linux-voice-assistant.service')
    parser.add_argument('--without-voice', action='store_true')
    args=parser.parse_args()
    if os.geteuid()!=0:
        parser.error('Run using sudo or pkexec.')
    if not re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?', args.hostname):
        parser.error('Invalid hostname.')
    if not 1024<=args.port<=65535:
        parser.error('Choose an unprivileged TCP port (1024..65535).')
    for address in args.address:
        ipaddress.ip_address(address)
    user, owner=pwd.getpwnam(args.user),pwd.getpwnam(args.owner)
    if user.pw_uid==0 or owner.pw_uid==0:
        parser.error('The panel and owner accounts must be non-root.')
    units={'kiosk':args.kiosk_unit,'display':args.display_unit,'voice':args.voice_unit}
    if args.without_voice: units.pop('voice')
    if any(not re.fullmatch(r'[A-Za-z0-9_.@-]+\.service',name) for name in units.values()):
        parser.error('Invalid user service name.')
    unitdir=Path(user.pw_dir)/'.config/systemd/user'
    prefix=Path('/opt/panelpilot')
    config=Path(args.config_dir)
    state=Path(user.pw_dir)/'.local/state/panelpilot'
    private=Path('/etc/panelpilot')
    credential=Path(owner.pw_dir)/'PanelPilot-Zugang.txt'
    public_certificate=Path(owner.pw_dir)/'PanelPilot-Zertifikat.crt'
    if not config.is_absolute() or any(path.exists() or path.is_symlink() for path in (prefix,config,state,private,credential,public_certificate)):
        parser.error('Install paths must be absolute and unused; existing data is never overwritten.')
    for name in units.values():
        if not (unitdir/name).is_file():
            parser.error('Missing existing panel service: '+str(unitdir/name))
        if (unitdir/(name+'.d')/'60-panelpilot.conf').exists():
            parser.error('A PanelPilot override already exists.')
    for name in ('panelpilot.service','panelpilot-apply.service','panelpilot-apply.path'):
        if (unitdir/name).exists() or (unitdir/name).is_symlink():
            parser.error('A PanelPilot service already exists.')
    with socket.socket() as listener:
        listener.bind(('0.0.0.0',args.port))
    wheelhouse=Path(args.wheelhouse).resolve()
    from panelpilot import __version__
    wheels=list(wheelhouse.glob('panelpilot-'+__version__+'-*.whl'))
    if len(wheels)!=1:
        parser.error('Build the release wheel in the wheelhouse first.')
    runtime='/run/user/'+str(user.pw_uid)
    usercmd=['runuser','-u',args.user,'--','env','XDG_RUNTIME_DIR='+runtime,
             'DBUS_SESSION_BUS_ADDRESS=unix:path='+runtime+'/bus']
    online=Path(runtime+'/bus').exists()
    def as_user(*command,**kwargs):
        return subprocess.run(usercmd+list(command),text=True,check=True,timeout=60,**kwargs)
    def exec_args(unit):
        ini=configparser.ConfigParser(interpolation=None,strict=False)
        ini.read(unitdir/unit)
        line=ini['Service']['ExecStart']
        tokens=shlex.split(line)
        return [part.replace('%h',user.pw_dir).replace('%t',runtime) for part in tokens]
    browser=exec_args(units['kiosk'])
    if not browser[-1].startswith(('http://','https://')):
        parser.error('Kiosk ExecStart must end with a separate HTTP(S) URL argument.')
    display=exec_args(units['display'])
    def option(name,default):
        return display[display.index(name)+1] if name in display else default
    backlights=list(Path('/sys/class/backlight').iterdir())
    backlight=next((p for p in backlights if p.name=='intel_backlight'),backlights[0] if backlights else None)
    maximum=int((backlight/'max_brightness').read_text()) if backlight else 100
    level=int((backlight/'brightness').read_text()) if backlight else 100
    restore=Path(user.pw_dir)/'.local/state/ha-panel/backlight.json'
    if restore.exists() and backlight:
        level=json.loads(restore.read_text())['brightness']
    rotation=as_user('gsettings','get','org.gnome.settings-daemon.peripherals.touchscreen','orientation-lock',capture_output=True).stdout.strip() if online else 'false'
    desired={'dashboard_url':browser[-1],'dashboard_brightness':max(1,min(100,round(level*100/maximum))),
             'photo_brightness':int(option('--photo-brightness','20')),
             'idle_seconds':int(option('--idle-seconds','300')),
             'slide_seconds':int(option('--slide-seconds','30')),
             'photo_directory':option('--photo-directory','/srv/panel-photos'),
             'idle_mode':'photos' if '--photo-directory' in display else 'black',
             'voice_enabled':not args.without_voice,'auto_rotate':rotation=='false'}
    prefix.mkdir(mode=0o755)
    subprocess.run(['/usr/bin/python3','-m','venv',str(prefix/'venv')],check=True,timeout=90)
    python=str(prefix/'venv/bin/python')
    subprocess.run([python,'-m','pip','install','--no-index','--find-links',str(wheelhouse),
                    '-r',str(BASE/'requirements.lock'),str(wheels[0])],check=True,timeout=120)
    subprocess.run([python,'-c','import json,sys; from panelpilot.models import Settings; Settings.model_validate(json.loads(sys.stdin.read()))'],input=json.dumps(desired),text=True,check=True,timeout=10)
    display_script=subprocess.check_output([python,'-c',"import panelpilot;from pathlib import Path; print(Path(panelpilot.__file__).parent/'display/panel_backlight.py')"],text=True).strip()
    private.mkdir(mode=0o750)
    os.chown(private,0,user.pw_gid)
    for parent in (Path(user.pw_dir)/'.local',Path(user.pw_dir)/'.local/state'):
        if not parent.exists(): parent.mkdir(); os.chown(parent,user.pw_uid,user.pw_gid)
    state.mkdir(parents=True,mode=0o700)
    os.chown(state,user.pw_uid,user.pw_gid)
    uploads=state/'photos'; uploads.mkdir(mode=0o700); os.chown(uploads,user.pw_uid,user.pw_gid)
    if desired['photo_directory']=='/srv/panel-photos': desired['photo_directory']=str(uploads)
    config.mkdir(parents=True,mode=0o2770)
    os.chown(config,owner.pw_uid,user.pw_gid)
    config.chmod(0o2770)
    # Atomic replacements by the panel must remain editable by the owner.
    subprocess.run(['setfacl','-m',f'u:{args.owner}:rwx,d:u::rwx,d:u:{args.owner}:rwx,d:g::rwx,d:o::---',str(config)],check=True)
    write_json(config/'settings.json',desired,0o660)
    os.chown(config/'settings.json',owner.pw_uid,user.pw_gid)
    password=secrets.token_urlsafe(24)
    write_json(state/'auth.json',make_credentials(password))
    os.chown(state/'auth.json',user.pw_uid,user.pw_gid)
    hosts=list(dict.fromkeys([args.hostname,'localhost','127.0.0.1',*args.address]))
    runtime_config={'units':units,'unit_directory':str(unitdir),'browser_command':browser[:-1],
                    'display_script':display_script,'allowed_hosts':hosts,'user':args.user,
                    'config_directory':str(config),'state_directory':str(state),'upload_directory':str(uploads),'port':args.port}
    write_json(private/'runtime.json',runtime_config,0o640)
    os.chown(private/'runtime.json',0,user.pw_gid)
    sans=['DNS:'+args.hostname,'DNS:localhost','IP:127.0.0.1']+['IP:'+value for value in args.address]
    subprocess.run(['openssl','req','-x509','-newkey','rsa:3072','-sha256','-nodes','-days','365',
                    '-keyout',str(private/'key.pem'),'-out',str(private/'certificate.pem'),
                    '-subj','/CN='+args.hostname,'-addext','subjectAltName='+','.join(sans)],
                   check=True,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,timeout=30)
    for name in ('key.pem','certificate.pem'):
        path=private/name; path.chmod(0o640); os.chown(path,0,user.pw_gid)
    atomic_write(public_certificate,(private/'certificate.pem').read_text(),0o644)
    os.chown(public_certificate,owner.pw_uid,owner.pw_gid)
    def q(value):
        return '"'+str(value).replace('\\','\\\\').replace('"','\\"').replace('%','%%').replace('$','$$')+'"'
    env=''.join('Environment='+q(key+'='+value)+'\n' for key,value in {
        'PANELPILOT_RUNTIME':str(private/'runtime.json'),'PANELPILOT_SETTINGS':str(config/'settings.json'),
        'PANELPILOT_STATE':str(state)}.items())
    command=q(python)+' -m panelpilot'
    web='[Unit]\nDescription=PanelPilot local web management\nAfter=graphical-session.target\n\n[Service]\nType=simple\n'+env+'ExecStartPre='+command+' apply\nExecStart='+command+' serve --host 0.0.0.0 --port '+str(args.port)+' --certificate '+q(private/'certificate.pem')+' --key '+q(private/'key.pem')+'\nRestart=on-failure\nRestartSec=5\nUMask=0077\nNoNewPrivileges=yes\nRestrictAddressFamilies=AF_UNIX AF_INET AF_INET6\nMemoryMax=512M\nTasksMax=64\n\n[Install]\nWantedBy=default.target\n'
    apply='[Unit]\nDescription=Apply locally edited PanelPilot settings\n\n[Service]\nType=oneshot\n'+env+'ExecStart='+command+' apply\nTimeoutStartSec=120\nUMask=0077\nNoNewPrivileges=yes\n'
    watcher='[Unit]\nDescription=Watch local PanelPilot apply requests\n\n[Path]\nPathChanged='+str(config/'apply.request')+'\nUnit=panelpilot-apply.service\n\n[Install]\nWantedBy=default.target\n'
    for name,content in [('panelpilot.service',web),('panelpilot-apply.service',apply),('panelpilot-apply.path',watcher)]:
        path=unitdir/name; atomic_write(path,content,0o644); os.chown(path,user.pw_uid,user.pw_gid)
    atomic_write(config/'apply.request','',0o660); os.chown(config/'apply.request',owner.pw_uid,user.pw_gid)
    script='#!/bin/sh\nset -eu\nprintf "%s\\n" "$(date +%s%N)" > '+shlex.quote(str(config/'apply.request'))+'\nprintf "%s\\n" "Übernahme angefordert. Ergebnis siehe Webverwaltung oder PanelPilot-Dienstprotokoll."\n'
    atomic_write(config/'anwenden',script,0o750); os.chown(config/'anwenden',owner.pw_uid,user.pw_gid)
    atomic_write(config/'README.txt','PanelPilot-Einstellungen\n\nsettings.json ist die gemeinsame Konfiguration für Weboberfläche und lokalen Editor.\nNach manuellen Änderungen ./anwenden ausführen. Webänderungen werden direkt übernommen.\nPanelPilot-Overrides haben Vorrang vor den gleichnamigen Optionen in alten Basisdiensten.\n',0o640)
    os.chown(config/'README.txt',owner.pw_uid,user.pw_gid)
    fingerprint=subprocess.check_output(['openssl','x509','-in',str(private/'certificate.pem'),'-noout','-fingerprint','-sha256'],text=True).strip()
    atomic_write(credential,'PanelPilot – lokaler Verwaltungszugang\n\nAdresse: https://'+args.hostname+':'+str(args.port)+'\nBenutzer: admin\nPasswort: '+password+'\n\nSelbst signiertes HTTPS-Zertifikat. Fingerabdruck:\n'+fingerprint+'\n\nDas Passwort kann in der Webverwaltung unter Zugang geändert werden.\nDiese Datei und die lokalen Konfigurationsordner niemals veröffentlichen.\n',0o600)
    os.chown(credential,owner.pw_uid,owner.pw_gid)
    if online:
        as_user('systemd-analyze','--user','verify',str(unitdir/'panelpilot.service'),str(unitdir/'panelpilot-apply.service'),str(unitdir/'panelpilot-apply.path'))
        as_user('systemctl','--user','daemon-reload')
        as_user('systemctl','--user','enable','--now','panelpilot.service','panelpilot-apply.path')
        as_user('systemctl','--user','is-active','panelpilot.service')
    else:
        wants=unitdir/'default.target.wants'; wants.mkdir(exist_ok=True); os.chown(wants,user.pw_uid,user.pw_gid)
        for name in ('panelpilot.service','panelpilot-apply.path'):
            (wants/name).symlink_to('../'+name); os.lchown(wants/name,user.pw_uid,user.pw_gid)
        print('Services enabled for the next graphical login; no reboot requested.')
    print('PanelPilot installed: https://'+args.hostname+':'+str(args.port))
    print('Private access details: '+str(credential))


if __name__=='__main__':
    main()
