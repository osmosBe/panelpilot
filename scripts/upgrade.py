#!/usr/bin/env python3
"""Upgrade an existing PanelPilot installation with backup and rollback."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import pwd
import shutil
import subprocess
import sys
import tomllib

BASE=Path(__file__).resolve().parents[1]
PREFIX=Path('/opt/panelpilot')
BACKUPS=Path('/var/backups/panelpilot-upgrade')


def run(*args,check=True,timeout=120):
    return subprocess.run(args,check=check,timeout=timeout)


def main():
    p=argparse.ArgumentParser(description=__doc__); p.add_argument('--wheelhouse',required=True); p.add_argument('--mountpoint',default='/mnt/panelpilot_photos')
    args=p.parse_args()
    if os.geteuid()!=0: p.error('Run using sudo or pkexec.')
    runtime=json.loads(Path('/etc/panelpilot/runtime.json').read_text()); user=pwd.getpwnam(runtime['user'])
    version=tomllib.loads((BASE/'pyproject.toml').read_text())['project']['version']
    wheels=list(Path(args.wheelhouse).resolve().glob('panelpilot-'+version+'-*.whl'))
    if len(wheels)!=1: p.error('Build exactly one matching release wheel first.')
    backup=BACKUPS/datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'); backup.mkdir(parents=True,mode=0o700)
    unitdir=Path(runtime['unit_directory'])
    files=['/etc/panelpilot/runtime.json','/etc/panelpilot/admin.json','/var/lib/panelpilot-admin/state.json',
           '/etc/systemd/system/panelpilot-admin.service','/etc/systemd/system/panelpilot-weekly-reboot.service',
           '/usr/local/lib/panelpilot/weekly-reboot.py',str(unitdir/'panelpilot.service')]
    original={name:Path(name).read_text() if Path(name).exists() else None for name in files}
    (backup/'files.json').write_text(json.dumps(original,indent=2)+'\n')
    shutil.copytree(PREFIX,backup/'panelpilot',symlinks=True)
    uid=str(user.pw_uid)
    usercmd=['runuser','-u',user.pw_name,'--','env','XDG_RUNTIME_DIR=/run/user/'+uid,'DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/'+uid+'/bus','systemctl','--user']
    online=Path('/run/user/'+uid+'/bus').exists()
    admin_active=subprocess.run(['systemctl','is-active','--quiet','panelpilot-admin.service']).returncode==0
    try:
        if online: run(*usercmd,'stop','panelpilot.service')
        if admin_active: run('systemctl','stop','panelpilot-admin.service')
        run(str(PREFIX/'venv/bin/python'),'-m','pip','install','--no-index','--find-links',str(Path(args.wheelhouse).resolve()),'-r',str(BASE/'requirements.lock'))
        run(str(PREFIX/'venv/bin/python'),'-m','pip','install','--no-index','--no-deps','--force-reinstall',str(wheels[0]))
        unit=unitdir/'panelpilot.service'
        unit.write_text(unit.read_text().replace('MemoryMax=256M','MemoryMax=512M')); os.chown(unit,user.pw_uid,user.pw_gid)
        run(str(PREFIX/'venv/bin/python'),str(BASE/'scripts/system_install.py'),'--mountpoint',args.mountpoint)
        if online:
            run(*usercmd,'daemon-reload'); run(*usercmd,'start','panelpilot.service'); run(*usercmd,'is-active','panelpilot.service')
        print(json.dumps({'version':version,'backup':str(backup),'reboot_requested':False}),flush=True)
    except BaseException:
        run('systemctl','disable','--now','panelpilot-admin.service',check=False)
        if online: run(*usercmd,'stop','panelpilot.service',check=False)
        shutil.rmtree(PREFIX); shutil.copytree(backup/'panelpilot',PREFIX,symlinks=True)
        for name,content in original.items():
            path=Path(name)
            if content is None: path.unlink(missing_ok=True)
            else:
                path.parent.mkdir(parents=True,exist_ok=True); path.write_text(content)
        os.chown('/etc/panelpilot/runtime.json',0,user.pw_gid); Path('/etc/panelpilot/runtime.json').chmod(0o640)
        os.chown(unitdir/'panelpilot.service',user.pw_uid,user.pw_gid)
        run('systemctl','daemon-reload')
        if admin_active: run('systemctl','enable','--now','panelpilot-admin.service')
        if online: run(*usercmd,'daemon-reload'); run(*usercmd,'start','panelpilot.service')
        print('Previous application restored. Backup: '+str(backup),flush=True)
        raise


if __name__=='__main__': main()
