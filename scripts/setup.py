#!/usr/bin/env python3
"""One-entry installer for Ubuntu 24.04-based GNOME panels (amd64/arm64)."""
import argparse
import configparser
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import platform
import pwd
import re
import shutil
import subprocess
import sys
from urllib.parse import urlsplit

BASE=Path(__file__).resolve().parents[1]
RECORD=Path('/var/lib/panelpilot-installer/state.json')
VOICE_VERSION='v1.1.18'
VOICE_COMMIT='b55d36af522c263eeed4e023d6ebf30da47e4710'


def run(*args,timeout=1800,capture=False):
    print('Running: '+str(args[0])+' '+str(args[1] if len(args)>1 else ''),flush=True)
    return subprocess.run([str(a) for a in args],check=True,timeout=timeout,text=True,capture_output=capture)


def os_release(path=Path('/etc/os-release')):
    return dict(line.split('=',1)[0:1]+[line.split('=',1)[1].strip('"')] for line in path.read_text().splitlines() if '=' in line)


def supported(info,machine):
    return (info.get('UBUNTU_CODENAME',info.get('VERSION_CODENAME'))=='noble'
            and info.get('ID') in ('ubuntu','zorin') and machine in ('x86_64','aarch64'))


def quote(value): return '"'+str(value).replace('\\','\\\\').replace('"','\\"').replace('%','%%').replace('$','$$')+'"'


def configure_gdm(path,user):
    ini=configparser.ConfigParser(interpolation=None)
    ini.optionxform=str  # GDM/GLib key names are case-sensitive.
    ini.read(path)
    if not ini.has_section('daemon'): ini.add_section('daemon')
    ini['daemon']['AutomaticLoginEnable']='true'; ini['daemon']['AutomaticLogin']=user
    with Path(path).open('w') as out: ini.write(out)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--install',action='store_true',help='Apply the printed setup; without this flag only inspect.')
    parser.add_argument('--owner',required=True,help='Existing local administrator/owner (not root)')
    parser.add_argument('--user',default='ha-panel')
    parser.add_argument('--hostname',default=platform.node()+'.local')
    parser.add_argument('--url',default='http://homeassistant.local:8123')
    parser.add_argument('--voice',action='store_true',help='Install the pinned Linux Voice Assistant as the panel user')
    parser.add_argument('--address',action='append',default=[])
    args=parser.parse_args()
    if not supported(os_release(),platform.machine()): parser.error('Supported: Ubuntu 24.04 / Zorin 18 (Noble), amd64 or arm64.')
    if not Path('/usr/bin/gnome-shell').exists() or not Path('/etc/gdm3/custom.conf').exists():
        parser.error('Install Ubuntu Desktop with GNOME/GDM first. A headless/server OS is not sufficient.')
    if not re.fullmatch(r'[a-z_][a-z0-9_-]{0,31}',args.user): parser.error('Invalid panel account name.')
    try: owner=pwd.getpwnam(args.owner)
    except KeyError: parser.error('Owner account does not exist.')
    if owner.pw_uid==0 or args.owner==args.user: parser.error('Use separate non-root owner and panel accounts.')
    if not re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?',args.hostname): parser.error('Invalid hostname.')
    url=urlsplit(args.url)
    if url.scheme not in ('http','https') or not url.hostname or url.username or url.password or re.search(r'[\s\x00-\x1f\x7f\\]',args.url):
        parser.error('Use an HTTP(S) Home Assistant URL without credentials.')
    print(json.dumps({'os':os_release().get('PRETTY_NAME'),'architecture':platform.machine(),'panel_user':args.user,
        'owner':args.owner,'hostname':args.hostname,'voice':args.voice,'autologin':True,
        'screen_lock':False,'automatic_suspend':False,'lid_and_power_buttons':'ignored',
        'maintenance':'daily updates 03:00; Sunday reboot 05:00 Europe/Vienna, configurable in web UI',
        'immediate_reboot':False},indent=2),flush=True)
    if not args.install:
        print('Inspection only. Add --install to apply.'); return
    if os.geteuid()!=0: parser.error('Installation requires sudo or pkexec.')
    old=json.loads(RECORD.read_text()) if RECORD.exists() else None
    if old and (old['user']!=args.user or old['owner']!=args.owner): parser.error('Existing setup belongs to different accounts.')
    existing_panelpilot=Path('/etc/panelpilot/runtime.json').exists()
    if existing_panelpilot:
        runtime=json.loads(Path('/etc/panelpilot/runtime.json').read_text())
        if runtime['user']!=args.user: parser.error('Existing PanelPilot belongs to a different panel account.')
    if not old and not existing_panelpilot:
        try: pwd.getpwnam(args.user)
        except KeyError: pass
        else: parser.error('Panel account already exists. Use scripts/install.py for an existing kiosk, or choose a new account.')
    packages=['python3-venv','python3-gi','python3-cairo','python3-gi-cairo','gir1.2-gtk-3.0','gir1.2-gdkpixbuf-2.0',
              'openssl','acl','nfs-common','unattended-upgrades','powermgmt-base','avahi-daemon','libnss-mdns','iio-sensor-proxy']
    if args.voice:
        packages+=['git','python3-dev','build-essential','libmpv-dev','libasound2-plugins','pulseaudio-utils','alsa-utils','avahi-utils','ca-certificates','iproute2','procps']
    run('apt-get','update'); run('apt-get','install','-y',*packages)
    cache=Path('/var/cache/panelpilot'); cache.mkdir(parents=True,exist_ok=True)
    builder=cache/'build'; wheels=cache/'wheels'; wheels.mkdir(exist_ok=True)
    if not (builder/'bin/python').exists(): run('/usr/bin/python3','-m','venv',builder)
    run(builder/'bin/python','-m','pip','install','setuptools>=77','wheel')
    run(builder/'bin/python','-m','pip','download','-r',BASE/'requirements.lock','--dest',wheels)
    run(builder/'bin/python','-m','pip','wheel','--no-deps','--no-build-isolation',BASE,'--wheel-dir',wheels)
    if existing_panelpilot:
        run('/usr/bin/python3',BASE/'scripts/upgrade.py','--wheelhouse',wheels)
        print('Existing panel upgraded; its accounts, autologin, photos and voice setup were preserved.'); return
    if not old:
        backup=Path('/var/backups/panelpilot-setup')/datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
        backup.mkdir(parents=True,mode=0o700)
        system_files=['/etc/gdm3/custom.conf','/etc/systemd/logind.conf.d/90-panelpilot.conf']
        original={f:Path(f).read_text() if Path(f).exists() else None for f in system_files}
        (backup/'files.json').write_text(json.dumps(original,indent=2)+'\n')
        old={'user':args.user,'owner':args.owner,'backup':str(backup),'complete':False}
        RECORD.parent.mkdir(parents=True,exist_ok=True); RECORD.write_text(json.dumps(old,indent=2)+'\n'); RECORD.chmod(0o600)
    try: user=pwd.getpwnam(args.user)
    except KeyError:
        run('adduser','--disabled-password','--gecos','PanelPilot kiosk',args.user); user=pwd.getpwnam(args.user)
    home=Path(user.pw_dir); unitdir=home/'.config/systemd/user'
    def write_user(path,content,mode=0o644):
        parent=path.parent
        while not parent.exists():
            parent.mkdir(parents=True,exist_ok=True)
            # Chown the complete new subtree below the user's home below.
        path.write_text(content); path.chmod(mode); os.chown(path,user.pw_uid,user.pw_gid)
    def as_user(*cmd,**kwargs): return run('runuser','-u',args.user,'--',*cmd,**kwargs)
    browser=shutil.which('brave-browser') or shutil.which('chromium') or shutil.which('chromium-browser')
    if not browser:
        run('apt-get','install','-y','chromium-browser'); browser=shutil.which('chromium-browser')
    if not browser: raise RuntimeError('Browser installation failed.')
    profile=home/('snap/chromium/common/panelpilot-profile' if 'chromium' in browser else '.local/share/panelpilot-browser')
    browser_args=[browser,'--kiosk','--no-first-run','--disable-session-crashed-bubble','--password-store=basic','--user-data-dir='+str(profile),args.url]
    header='[Unit]\nAfter=graphical-session.target\n[Service]\nType=simple\nRestart=on-failure\nRestartSec=5\n'
    write_user(unitdir/'ha-kiosk.service',header+'ExecStart='+' '.join(map(quote,browser_args))+'\n')
    uploads=home/'.local/state/panelpilot/photos'
    # The PanelPilot adapter replaces this placeholder before the first display start.
    display_script='/opt/panelpilot/venv/lib/python3.12/site-packages/panelpilot/display/panel_backlight.py'
    write_user(unitdir/'ha-panel-display.service',header+'ExecStart=/usr/bin/python3 '+quote(display_script)+' --black-overlay --blank-level 1 --photo-directory '+quote(uploads)+'\n')
    units=['ha-kiosk.service','ha-panel-display.service']
    if args.voice:
        for folder in (home/'.local/state/linux-voice-assistant',home/'.local/share/linux-voice-assistant'):
            folder.mkdir(parents=True,exist_ok=True)
        source=Path('/opt/linux-voice-assistant-'+VOICE_VERSION)
        if not source.exists(): source.mkdir(); os.chown(source,user.pw_uid,user.pw_gid)
        if not (source/'.git').exists():
            as_user('git','-C',source,'init'); as_user('git','-C',source,'remote','add','origin','https://github.com/OHF-Voice/linux-voice-assistant.git')
            as_user('git','-C',source,'fetch','--depth','1','origin','refs/tags/'+VOICE_VERSION)
            as_user('git','-C',source,'checkout','--detach','FETCH_HEAD')
        if as_user('git','-C',source,'rev-parse','HEAD',capture=True).stdout.strip()!=VOICE_COMMIT: raise RuntimeError('Voice source revision does not match the pinned release.')
        if not (source/'.venv/bin/python').exists(): as_user('/usr/bin/python3','-m','venv',source/'.venv')
        as_user(source/'.venv/bin/python','-m','pip','install','--upgrade','pip','setuptools','wheel')
        as_user(source/'.venv/bin/python','-m','pip','install','-e',source)
        cmd=[str(source/'.venv/bin/python'),'-m','linux_voice_assistant','--name','PanelPilot','--wake-model','hey_jarvis','--disable-peripheral-api',
             '--preferences-file',str(home/'.local/state/linux-voice-assistant/preferences.json'),'--download-dir',str(home/'.local/share/linux-voice-assistant')]
        write_user(unitdir/'linux-voice-assistant.service',header+'ExecStart='+' '.join(map(quote,cmd))+'\n')
        units.append('linux-voice-assistant.service')
    start='#!/bin/sh\n'
    start+='dbus-update-activation-environment --systemd DISPLAY WAYLAND_DISPLAY XDG_CURRENT_DESKTOP XDG_SESSION_TYPE\n'
    start+='systemctl --user start panelpilot.service\n'
    start+='systemctl --user start '+' '.join(units)+'\n'
    write_user(home/'.local/bin/start-panelpilot',start,0o755)
    write_user(home/'.config/autostart/panelpilot.desktop','[Desktop Entry]\nType=Application\nName=PanelPilot\nExec='+str(home/'.local/bin/start-panelpilot')+'\nX-GNOME-Autostart-enabled=true\n')
    # This is a newly created dedicated account; fix ownership of its setup directories.
    run('chown','-R',args.user+':'+str(user.pw_gid),home)
    for schema,key,value in [('org.gnome.desktop.session','idle-delay','0'),('org.gnome.desktop.screensaver','lock-enabled','false'),
        ('org.gnome.desktop.lockdown','disable-lock-screen','true'),('org.gnome.settings-daemon.plugins.power','sleep-inactive-ac-type','nothing'),
        ('org.gnome.settings-daemon.plugins.power','sleep-inactive-battery-type','nothing'),('org.gnome.settings-daemon.peripherals.touchscreen','orientation-lock','false')]:
        as_user('dbus-run-session','--','gsettings','set',schema,key,value)
    configure_gdm(Path('/etc/gdm3/custom.conf'),args.user)
    power=Path('/etc/systemd/logind.conf.d/90-panelpilot.conf'); power.parent.mkdir(exist_ok=True)
    power.write_text('[Login]\nHandleLidSwitch=ignore\nHandleLidSwitchExternalPower=ignore\nHandleLidSwitchDocked=ignore\nHandlePowerKey=ignore\nHandleSuspendKey=ignore\nIdleAction=ignore\n')
    addresses=args.address
    if not addresses:
        import ipaddress
        addresses=[a for a in run('hostname','-I',capture=True).stdout.split() if isinstance(ipaddress.ip_address(a),ipaddress.IPv4Address) and ipaddress.ip_address(a).is_private]
    command=['/usr/bin/python3',str(BASE/'scripts/install.py'),'--user',args.user,'--owner',args.owner,'--hostname',args.hostname,'--wheelhouse',str(wheels)]
    for address in addresses: command+=['--address',address]
    if not args.voice: command+=['--without-voice']
    run(*command)
    run('/opt/panelpilot/venv/bin/python',BASE/'scripts/system_install.py','--fresh-maintenance')
    old['complete']=True; RECORD.write_text(json.dumps(old,indent=2)+'\n')
    print('Installation complete. No reboot requested. Reboot when ready for the dedicated panel autologin.')
    print('Access details: '+str(Path(owner.pw_dir)/'PanelPilot-Zugang.txt'))
    if args.voice: print('After login, add this satellite in Home Assistant via ESPHome (port 6053) and select its voice pipeline.')


if __name__=='__main__': main()
