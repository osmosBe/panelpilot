"""Bounded, non-root systemd/GNOME adapter. No shell or arbitrary command API."""
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import subprocess

from .models import Settings
from .storage import atomic_write, write_json


class Conflict(Exception):
    pass


class ApplyError(Exception):
    pass


def quote_argument(value):
    # systemd performs specifier and environment expansion even inside quotes.
    return '"' + str(value).replace('\\', '\\\\').replace('"', '\\"').replace('%', '%%').replace('$', '$$') + '"'


def revision(settings):
    return hashlib.sha256(settings.model_dump_json().encode()).hexdigest()


def command(*args, check=True, timeout=15):
    return subprocess.run(args, text=True, capture_output=True, timeout=timeout, check=check)


class Controller:
    def __init__(self, settings_path, state_dir, runtime, runner=command):
        self.settings_path, self.state_dir = Path(settings_path), Path(state_dir)
        self.runtime, self.run = runtime, runner
        self.units = runtime['units']
        self.unit_dir = Path(runtime['unit_directory'])
        self.state_dir.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def locked(self):
        with (self.state_dir/'apply.lock').open('a') as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            yield

    def settings(self):
        return Settings.model_validate_json(self.settings_path.read_text())

    def read(self):
        settings = self.settings()
        return {'settings': settings.model_dump(), 'revision': revision(settings)}

    def render(self, settings):
        browser = self.runtime['browser_command'] + [settings.dashboard_url]
        display = ['/usr/bin/python3', self.runtime['display_script'],
                   '--idle-seconds', str(settings.idle_seconds), '--blank-level', '1',
                   '--black-overlay', '--awake-brightness', str(settings.dashboard_brightness),
                   '--slide-seconds', str(settings.slide_seconds),
                   '--photo-brightness', str(settings.photo_brightness)]
        if settings.idle_mode == 'photos':
            display += ['--photo-directory', settings.photo_directory]
        elif settings.idle_mode == 'off':
            display += ['--awake-only']
        restore = ['/usr/bin/python3', self.runtime['display_script'], '--restore',
                   '--awake-brightness', str(settings.dashboard_brightness)]
        header = '# Managed by PanelPilot. Change settings.json or use the web interface.\n'
        rendered = {
            'kiosk': header+'[Service]\nExecStart=\nExecStart='+ ' '.join(map(quote_argument, browser))+'\n',
            'display': header+'[Unit]\nConditionPathExists=!'+str(self.state_dir/'display-disabled')+'\n[Service]\nExecStart=\nExecStart='+ ' '.join(map(quote_argument, display))+'\nExecStopPost=\nExecStopPost='+ ' '.join(map(quote_argument, restore))+'\n',
            'voice': header+'[Unit]\nConditionPathExists=!'+str(self.state_dir/'voice-disabled')+'\n',
        }
        return {role:text for role,text in rendered.items() if role in self.units}

    def dropin(self, role):
        return self.unit_dir/(self.units[role]+'.d')/'60-panelpilot.conf'

    def panel_active(self):
        result = self.run('loginctl', 'show-user', str(os.getuid()), '-p', 'Display', '--value', check=False)
        session = result.stdout.strip()
        if not session:
            return False
        return self.run('loginctl', 'show-session', session, '-p', 'Active', '--value', check=False).stdout.strip() == 'yes'

    def save(self, settings, expected):
        with self.locked():
            previous = self.settings()
            if expected != revision(previous):
                raise Conflict('Die Einstellungen wurden inzwischen geändert. Bitte neu laden.')
            return self._apply(settings, previous)

    def reconcile(self):
        with self.locked():
            current = self.settings()
            prior = self.state_dir/'last-applied.json'
            previous = Settings.model_validate_json(prior.read_text()) if prior.exists() else current
            return self._apply(current, previous)

    def _apply(self, settings, previous):
        rendered = self.render(settings)
        paths = [self.dropin(role) for role in self.units]
        paths += [self.state_dir/'voice-disabled', self.state_dir/'display-disabled']
        old_files = {p: p.read_text() if p.exists() else None for p in paths}
        changed = {role for role, text in rendered.items() if old_files[self.dropin(role)] != text}
        restarted, warnings = [], []
        schema = 'org.gnome.settings-daemon.peripherals.touchscreen'
        old_rotation = self.run('gsettings', 'get', schema, 'orientation-lock', check=False).stdout.strip()
        old_settings = self.settings_path.read_text()
        try:
            for role in changed:
                atomic_write(self.dropin(role), rendered[role], 0o644)
            for enabled, name in [(settings.voice_enabled, 'voice'), (True, 'display')]:
                marker = self.state_dir/(name+'-disabled')
                if enabled:
                    marker.unlink(missing_ok=True)
                else:
                    atomic_write(marker, 'Disabled by PanelPilot\n')
            if changed:
                self.run('systemctl', '--user', 'daemon-reload')
            desired_rotation = 'false' if settings.auto_rotate else 'true'
            if old_rotation != desired_rotation:
                self.run('gsettings', 'set', schema, 'orientation-lock', desired_rotation)
            graphical = bool(self.run('loginctl','show-user',str(os.getuid()),'-p','Display','--value',check=False).stdout.strip())
            if 'voice' in self.units and not settings.voice_enabled:
                self.run('systemctl', '--user', 'stop', self.units['voice'])
            elif 'voice' in self.units and graphical and (not previous.voice_enabled or old_files[self.state_dir/'voice-disabled'] is not None):
                result = self.run('systemctl', '--user', 'restart', self.units['voice'], check=False)
                restarted.append('voice')
                if result.returncode or not self.panel_active():
                    warnings.append('Sprache ist eingeschaltet. Das Mikrofon wird verfügbar, sobald die Panelsitzung aktiv ist.')
            if 'display' in changed and graphical:
                self.run('systemctl', '--user', 'restart', self.units['display'])
                restarted.append('display')
            if graphical and (settings.dashboard_url != previous.dashboard_url or ('kiosk' in changed and old_files[self.dropin('kiosk')] is not None)):
                self.run('systemctl', '--user', 'restart', self.units['kiosk'])
                restarted.append('kiosk')
            write_json(self.settings_path, settings.model_dump(), 0o660)
            write_json(self.state_dir/'last-applied.json', settings.model_dump())
            result = {**self.read(), 'restarted': restarted, 'warnings': warnings, 'success': True}
            write_json(self.state_dir/'last-result.json', {'success': True, 'restarted': restarted, 'warnings': warnings})
            return result
        except Exception as exc:
            rollback_errors = []
            for path, content in old_files.items():
                try:
                    if content is None:
                        path.unlink(missing_ok=True)
                    else:
                        atomic_write(path, content, 0o644 if path.name.endswith('.conf') else 0o600)
                except OSError:
                    rollback_errors.append('Dateien')
            atomic_write(self.settings_path, old_settings, 0o660)
            try:
                self.run('systemctl', '--user', 'daemon-reload')
                if old_rotation in ('true', 'false'):
                    self.run('gsettings', 'set', schema, 'orientation-lock', old_rotation)
                for role in ('kiosk', 'display', 'voice'):
                    if role not in self.units: continue
                    enabled = previous.voice_enabled if role == 'voice' else True
                    self.run('systemctl', '--user', 'restart' if enabled else 'stop', self.units[role], check=False)
            except Exception:
                rollback_errors.append('Dienste')
            message = 'Übernahme fehlgeschlagen; vorherige Konfiguration wiederhergestellt.'
            if rollback_errors:
                message = 'Übernahme fehlgeschlagen; Rücknahme unvollständig. Bitte die lokalen Dienste prüfen.'
            write_json(self.state_dir/'last-result.json', {'success': False, 'message': message})
            raise ApplyError(message) from exc

    def status(self):
        states = {}
        for role, unit in self.units.items():
            result = self.run('systemctl', '--user', 'show', unit, '-p', 'ActiveState', '-p', 'SubState', check=False, timeout=4)
            states[role] = dict(line.split('=', 1) for line in result.stdout.splitlines() if '=' in line)
        return {'services': states, 'panel_active': self.panel_active(), 'demo': False,
                'capabilities':{'backlight':any(Path('/sys/class/backlight').iterdir()),'voice':'voice' in self.units}}

    def photos(self):
        helper = Path(self.runtime['display_script']).with_name('photo_probe.py')
        try:
            result = self.run('/usr/bin/python3', str(helper), self.settings().photo_directory, timeout=10)
            return json.loads(result.stdout)
        except (subprocess.SubprocessError, ValueError, OSError):
            return {'status': 'unavailable', 'count': None, 'message': 'Fotoordner momentan nicht erreichbar.'}

    def reload_dashboard(self):
        with self.locked():
            self.run('systemctl', '--user', 'restart', self.units['kiosk'])


class DemoController:
    """Explicit demo mode: never starts services or reads the host photo folders."""
    def __init__(self, path):
        self.path = Path(path)
        if not self.path.exists():
            write_json(self.path, Settings().model_dump())

    def settings(self):
        return Settings.model_validate_json(self.path.read_text())

    def read(self):
        value = self.settings()
        return {'settings': value.model_dump(), 'revision': revision(value)}

    def save(self, settings, expected):
        if expected != revision(self.settings()):
            raise Conflict('Die Einstellungen wurden inzwischen geändert. Bitte neu laden.')
        write_json(self.path, settings.model_dump())
        return {**self.read(), 'restarted': [], 'warnings': [], 'success': True}

    def status(self):
        return {'services': {name: {'ActiveState': 'inactive' if name == 'voice' and not self.settings().voice_enabled else 'active', 'SubState': 'running'} for name in ('kiosk','display','voice')}, 'panel_active': True, 'demo': True}

    def photos(self):
        return {'status': 'ready', 'count': 12, 'message': 'Beispieldaten der Demo'}

    def reload_dashboard(self):
        pass
