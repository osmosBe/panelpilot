#!/usr/bin/python3
"""Disconnect PanelPilot; keep original services, settings and all photo files."""
import argparse
import json
import os
from pathlib import Path
import pwd
import subprocess

if os.geteuid()!=0:
    raise SystemExit('Run using sudo or pkexec.')
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--no-restart',action='store_true',help='Do not restart original services (for a full system rollback).')
args=parser.parse_args()
if Path('/etc/systemd/system/panelpilot-admin.service').exists():
    subprocess.run(['systemctl','disable','--now','panelpilot-admin.service'],check=True,timeout=30)
runtime=json.loads(Path('/etc/panelpilot/runtime.json').read_text())
account=pwd.getpwnam(runtime['user'])
unitdir=Path(account.pw_dir)/'.config/systemd/user'
if str(unitdir)!=runtime['unit_directory']:
    raise SystemExit('Unexpected unit directory.')
bus='/run/user/'+str(account.pw_uid)
command=['runuser','-u',account.pw_name,'--','env','XDG_RUNTIME_DIR='+bus,
         'DBUS_SESSION_BUS_ADDRESS=unix:path='+bus+'/bus','systemctl','--user']
connected=Path(bus+'/bus').exists()
names=('panelpilot.service','panelpilot-apply.path','panelpilot-apply.service')
if connected:
    subprocess.run(command+['disable','--now',*names],check=False,timeout=90)
for name in names:
    for path in (unitdir/name,unitdir/'default.target.wants'/name):
        path.unlink(missing_ok=True)
for unit in runtime['units'].values():
    if '/' in unit or not unit.endswith('.service'):
        raise SystemExit('Unexpected service name.')
    path=unitdir/(unit+'.d')/'60-panelpilot.conf'
    if path.exists():
        if not path.read_text().startswith('# Managed by PanelPilot.'):
            raise SystemExit('Override has changed; review before removing: '+str(path))
        path.unlink()
if connected:
    subprocess.run(command+['daemon-reload'],check=True,timeout=20)
    if not args.no_restart:
        subprocess.run(command+['restart',*runtime['units'].values()],check=False,timeout=60)
print('PanelPilot stopped and disconnected. Original panel services are restored.')
print('Application, credentials and configuration files are retained for review.')
print('No photos or browser profiles were changed.')
print('Existing NFS mounts and OS maintenance timers are retained; disable them in the web interface before uninstalling if desired.')
