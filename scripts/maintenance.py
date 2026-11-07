#!/usr/bin/python3
"""Opt-in OS maintenance: stable unattended updates and a weekly reboot timer."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import runpy
import shutil
import subprocess
import time
from zoneinfo import ZoneInfo

BASE = Path(__file__).resolve().parents[1]
FILES = {
    '99zz-panelpilot-maintenance': '/etc/apt/apt.conf.d/99zz-panelpilot-maintenance',
    'apt-download-schedule.conf': '/etc/systemd/system/apt-daily.timer.d/90-panelpilot.conf',
    'apt-upgrade-schedule.conf': '/etc/systemd/system/apt-daily-upgrade.timer.d/90-panelpilot.conf',
    'panelpilot-weekly-reboot.timer': '/etc/systemd/system/panelpilot-weekly-reboot.timer',
    'panelpilot-weekly-reboot.service': '/etc/systemd/system/panelpilot-weekly-reboot.service',
    'weekly-reboot.py': '/usr/local/lib/panelpilot/weekly-reboot.py',
}
TIMERS = ['apt-daily.timer', 'apt-daily-upgrade.timer', 'panelpilot-weekly-reboot.timer']
UNITS = TIMERS + ['unattended-upgrades.service']
BACKUPS = Path('/var/backups/panelpilot-maintenance')


def run(*args, check=True, timeout=30, **kwargs):
    return subprocess.run(args, check=check, text=True, timeout=timeout, **kwargs)


def restore(backup):
    backup = backup.resolve()
    if backup.parent != BACKUPS:
        raise SystemExit('Expected a backup directly under '+str(BACKUPS))
    manifest = json.loads((backup/'manifest.json').read_text())
    if set(manifest['files']) != set(FILES.values()) or set(manifest['units']) != set(UNITS):
        raise SystemExit('Unexpected restore manifest.')
    run('systemctl','disable','--now','panelpilot-weekly-reboot.timer',check=False)
    # Stop a waiting guard too, so rollback cannot leave a delayed reboot request.
    run('systemctl','stop','panelpilot-weekly-reboot.service',check=False)
    for name, existed in manifest['files'].items():
        target=Path(name)
        if existed:
            shutil.copy2(backup/name.lstrip('/'),target)
        else:
            target.unlink(missing_ok=True)
    run('systemctl','daemon-reload')
    for unit, state in manifest['units'].items():
        if unit == 'panelpilot-weekly-reboot.timer' and not Path(FILES[unit]).exists():
            continue
        run('systemctl','enable' if state['enabled'] else 'disable',unit,check=False)
        run('systemctl','restart' if state['active'] else 'stop',unit,check=False)
    print('Previous update configuration and timer states restored. No reboot requested.')


def install(zone):
    ZoneInfo(zone)
    run('dpkg-query','-W','unattended-upgrades',capture_output=True)
    if shutil.which('on_ac_power') and run('on_ac_power',check=False).returncode == 1:
        raise SystemExit('Connect AC power for unattended-upgrade validation; no configuration changed.')
    run('systemd-analyze','calendar','Sun *-*-* 05:00:00 '+zone,capture_output=True)
    run('systemd-analyze','verify',str(BASE/'system/panelpilot-weekly-reboot.timer'),str(BASE/'system/panelpilot-weekly-reboot.service'),capture_output=True)
    pending_locks = runpy.run_path(str(BASE/'system/weekly-reboot.py'))['pending_locks']
    deadline = time.monotonic()+300
    while busy := pending_locks():
        if time.monotonic() >= deadline:
            raise SystemExit('Package manager remained busy; no configuration changed. Retry later.')
        print('Waiting for package manager before configuration: '+', '.join(busy),flush=True)
        time.sleep(15)
    BACKUPS.mkdir(parents=True,exist_ok=True,mode=0o700)
    backup=BACKUPS/datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    backup.mkdir(mode=0o700)
    manifest={'timezone':zone,'files':{},'units':{}}
    for target_name in FILES.values():
        target=Path(target_name)
        if target.is_symlink():
            raise SystemExit('Refusing to overwrite symlink: '+target_name)
        manifest['files'][target_name]=target.exists()
        if target.exists():
            saved=backup/target_name.lstrip('/'); saved.parent.mkdir(parents=True,exist_ok=True)
            shutil.copy2(target,saved)
    for unit in UNITS:
        manifest['units'][unit]={
            'enabled':run('systemctl','is-enabled',unit,check=False,capture_output=True).returncode==0,
            'active':run('systemctl','is-active',unit,check=False,capture_output=True).returncode==0,
        }
    (backup/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    try:
        for source,target_name in FILES.items():
            target=Path(target_name); target.parent.mkdir(parents=True,exist_ok=True)
            target.write_text((BASE/'system'/source).read_text().replace('Europe/Vienna',zone))
            target.chmod(0o644); os.chown(target,0,0)
        run('apt-config','dump',capture_output=True)
        import apt_pkg
        apt_pkg.init_config()
        assert apt_pkg.config.find_i('APT::Periodic::Unattended-Upgrade') == 1
        assert not apt_pkg.config.find_b('Unattended-Upgrade::Automatic-Reboot',True)
        assert apt_pkg.config.find_b('Unattended-Upgrade::OnlyOnACPower')
        assert 'Brave Software:stable' in apt_pkg.config.value_list('Unattended-Upgrade::Allowed-Origins')
        run('/usr/bin/python3',FILES['weekly-reboot.py'],'--check-only')
        # Validation can write normal diagnostic logs, but installs no packages.
        with (backup/'unattended-dry-run.log').open('w') as log:
            result=run('unattended-upgrade','--dry-run','--debug',timeout=600,stdout=log,stderr=subprocess.STDOUT,check=False)
        if result.returncode:
            print('\n'.join((backup/'unattended-dry-run.log').read_text().splitlines()[-15:]),flush=True)
            raise RuntimeError('Unattended-upgrades dry-run failed; see '+str(backup/'unattended-dry-run.log'))
        run('systemctl','daemon-reload')
        run('systemd-analyze','verify',FILES['panelpilot-weekly-reboot.timer'],FILES['panelpilot-weekly-reboot.service'])
        run('systemctl','enable',*UNITS)
        run('systemctl','start','unattended-upgrades.service')
        # Never start panelpilot-weekly-reboot.service here.
        run('systemctl','restart',*TIMERS)
        run('systemctl','is-active',*TIMERS)
        details=run('systemctl','show','panelpilot-weekly-reboot.timer','-p','NextElapseUSecRealtime','-p','Persistent','-p','AccuracyUSec','-p','TimersCalendar',capture_output=True).stdout
        text=(backup/'unattended-dry-run.log').read_text()
        summary=[line for line in text.splitlines() if any(part in line for part in ('Allowed origins are','Packages that will be upgraded','No packages found that can be upgraded','All upgrades installed','dry-run'))]
        report={'backup':str(backup),'timezone':zone,'reboot':'Sunday 05:00 every week','dry_run_exit_code':result.returncode,'timer':details,'dry_run_summary':summary[-10:]}
        (backup/'result.json').write_text(json.dumps(report,indent=2)+'\n')
        print(json.dumps(report,indent=2),flush=True)
    except BaseException:
        restore(backup)
        raise


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    commands=parser.add_subparsers(dest='action',required=True)
    setup=commands.add_parser('install'); setup.add_argument('--timezone',default='Europe/Vienna')
    undo=commands.add_parser('restore'); undo.add_argument('backup',type=Path)
    args=parser.parse_args()
    if os.geteuid()!=0:
        parser.error('Run using sudo or pkexec.')
    if args.action=='install': install(args.timezone)
    else: restore(args.backup)
