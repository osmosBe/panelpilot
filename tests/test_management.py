import json
from pathlib import Path
import subprocess
import tempfile
import unittest

from fastapi.testclient import TestClient
from pydantic import ValidationError

from panelpilot.app import create_app
from panelpilot.auth import Auth, make_credentials
from panelpilot.controller import Controller, DemoController, ApplyError, revision, quote_argument
from panelpilot.models import Settings
from panelpilot.storage import write_json


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        root = Path(self.folder.name)
        write_json(root/'auth.json', make_credentials('test-password-long-enough'))
        self.auth = Auth(root/'auth.json')
        self.backend = DemoController(root/'settings.json')
        self.client = TestClient(create_app(self.backend, self.auth, demo=False), base_url='https://testserver', client=('127.0.0.1', 4567))
        self.addCleanup(self.client.close)

    def login(self):
        result = self.client.post('/api/login', json={'username':'admin','password':'test-password-long-enough'})
        self.assertEqual(result.status_code, 200, result.text)
        self.client.headers['x-csrf-token'] = result.json()['csrf']
        return result

    def test_authentication_cookie_and_no_anonymous_configuration(self):
        self.assertEqual(self.client.get('/api/settings').status_code, 401)
        result = self.login()
        cookie = result.headers['set-cookie'].lower()
        for option in ('httponly', 'secure', 'samesite=strict'):
            self.assertIn(option, cookie)
        settings = self.client.get('/api/settings')
        self.assertEqual(settings.status_code, 200)
        self.assertEqual(settings.headers['cache-control'], 'no-store')
        self.assertIn("frame-ancestors 'none'", settings.headers['content-security-policy'])

    def test_save_and_stale_revision_conflict(self):
        self.login()
        body = self.client.get('/api/settings').json()
        body['settings']['photo_brightness'] = 9
        result = self.client.put('/api/settings', json=body)
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(self.backend.settings().photo_brightness, 9)
        self.assertEqual(self.client.put('/api/settings', json=body).status_code, 409)

    def test_csrf_origin_and_content_type(self):
        self.login()
        body = self.client.get('/api/settings').json()
        del self.client.headers['x-csrf-token']
        self.assertEqual(self.client.put('/api/settings', json=body).status_code, 403)
        self.assertEqual(self.client.post('/api/login', json={}, headers={'Origin':'https://evil.example'}).status_code, 403)
        self.assertEqual(self.client.post('/api/login', content='a=b', headers={'Content-Type':'application/x-www-form-urlencoded'}).status_code, 415)

    def test_payload_limits_host_and_public_network(self):
        self.assertEqual(self.client.post('/api/login', content='x'*17000, headers={'Content-Type':'application/json'}).status_code, 413)
        self.assertEqual(self.client.get('/', headers={'host':'evil.example'}).status_code, 400)
        with TestClient(create_app(self.backend,self.auth,demo=False), base_url='https://testserver', client=('203.0.113.4',1234)) as external:
            self.assertEqual(external.get('/').status_code, 403)

    def test_password_change_revokes_sessions_and_validation_never_echoes_password(self):
        self.login()
        old = self.client.cookies.get('panelpilot_session')
        sentinel='a-very-sensitive-password-value'
        result=self.client.post('/api/password', json={'current_password':sentinel,'new_password':'short'})
        self.assertEqual(result.status_code,422)
        self.assertNotIn(sentinel,result.text)
        result=self.client.post('/api/password',json={'current_password':'test-password-long-enough','new_password':'replacement-password-long'})
        self.assertEqual(result.status_code,200)
        self.assertIsNone(self.auth.session(old))
        self.assertNotIn('replacement-password-long',self.auth.path.read_text())
        self.assertEqual(self.client.get('/api/settings').status_code,401)

    def test_login_rate_limit_and_logout(self):
        self.login()
        token=self.client.cookies.get('panelpilot_session')
        self.assertEqual(self.client.post('/api/logout',json={}).status_code,200)
        self.assertIsNone(self.auth.session(token))
        for unused in range(5):
            self.assertEqual(self.client.post('/api/login',json={'username':'admin','password':'wrong'}).status_code,401)
        self.assertEqual(self.client.post('/api/login',json={'username':'admin','password':'wrong'}).status_code,429)


class SettingsTests(unittest.TestCase):
    def test_invalid_values_and_command_injection_are_rejected(self):
        invalid = [{'photo_brightness':0},{'dashboard_brightness':101},{'voice_enabled':'false'},
                   {'dashboard_url':'javascript:alert(1)'},{'dashboard_url':'https://user:password@example.org'},
                   {'dashboard_url':'https://example.org\nExecStart=/bin/false'},
                   {'photo_directory':'smb://example.org/photo'},{'photo_directory':'/tmp/../etc'},
                   {'photo_directory':'/tmp/x\n[Service]'},{'command':'/bin/sh'}]
        for patch in invalid:
            with self.subTest(patch=patch), self.assertRaises(ValidationError):
                Settings.model_validate({**Settings().model_dump(),**patch})

    def test_systemd_expansions_are_escaped(self):
        text=quote_argument('https://example.org/a%20b?q=$HOME&x="x"')
        self.assertEqual(text,'"https://example.org/a%%20b?q=$$HOME&x=\\"x\\""')


class FakeRunner:
    def __init__(self):
        self.calls=[]
        self.fail_once=False

    def __call__(self,*args,check=True,timeout=15):
        self.calls.append(args)
        if self.fail_once and args[:3]==('systemctl','--user','restart') and args[-1]=='display.service':
            self.fail_once=False
            raise subprocess.CalledProcessError(1,args)
        if args[:2]==('gsettings','get'):
            out='false\n'
        elif args[:2]==('loginctl','show-user'):
            out='1\n'
        elif args[:2]==('loginctl','show-session'):
            out='no\n'
        else:
            out=''
        return subprocess.CompletedProcess(args,0,out,'')


class ControllerTests(unittest.TestCase):
    def setUp(self):
        self.folder=tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        root=Path(self.folder.name)
        self.runner=FakeRunner()
        self.path=root/'settings.json'
        write_json(self.path,Settings().model_dump())
        self.backend=Controller(self.path,root/'state',{'units':{'kiosk':'kiosk.service','display':'display.service','voice':'voice.service'},'unit_directory':str(root/'units'),'browser_command':['/usr/bin/chromium','--kiosk'],'display_script':'/opt/panelpilot/display/panel_backlight.py'},self.runner)

    def save(self,**changes):
        prior=self.backend.settings()
        new=Settings.model_validate({**prior.model_dump(),**changes})
        return self.backend.save(new,revision(prior))

    def test_voice_off_persists_and_on_defers_in_inactive_session(self):
        self.save(voice_enabled=False)
        self.assertTrue((self.backend.state_dir/'voice-disabled').exists())
        self.assertIn('ConditionPathExists=!',self.backend.dropin('voice').read_text())
        self.assertIn(('systemctl','--user','stop','voice.service'),self.runner.calls)
        self.backend.reconcile()
        self.assertTrue((self.backend.state_dir/'voice-disabled').exists())
        result=self.save(voice_enabled=True)
        self.assertFalse((self.backend.state_dir/'voice-disabled').exists())
        self.assertEqual(len(result['warnings']),1)

    def test_noop_and_display_change_never_restart_voice_or_browser(self):
        self.backend.reconcile()
        self.runner.calls.clear()
        self.save(photo_brightness=12)
        self.assertIn(('systemctl','--user','restart','display.service'),self.runner.calls)
        self.assertNotIn(('systemctl','--user','restart','voice.service'),self.runner.calls)
        self.assertNotIn(('systemctl','--user','restart','kiosk.service'),self.runner.calls)
        self.runner.calls.clear()
        self.save()
        self.assertFalse(any('restart' in cmd for cmd in self.runner.calls))

    def test_failed_apply_restores_settings_and_dropins(self):
        self.backend.reconcile()
        before=self.path.read_bytes()
        dropin=self.backend.dropin('display').read_bytes()
        self.runner.fail_once=True
        with self.assertRaises(ApplyError):
            self.save(photo_brightness=11)
        self.assertEqual(self.path.read_bytes(),before)
        self.assertEqual(self.backend.dropin('display').read_bytes(),dropin)

    def test_url_and_path_with_spaces_are_single_literal_arguments(self):
        self.save(dashboard_url='https://example.org/a%20b?q=$HOME',photo_directory='/srv/My Photos/"family"')
        self.assertIn('a%%20b?q=$$HOME',self.backend.dropin('kiosk').read_text())
        self.assertIn('"/srv/My Photos/\\"family\\""',self.backend.dropin('display').read_text())

    def test_permanent_dashboard_keeps_brightness_controller(self):
        self.save(idle_mode='off')
        self.assertIn('--awake-only',self.backend.dropin('display').read_text())
        self.assertFalse((self.backend.state_dir/'display-disabled').exists())
        self.assertNotIn(('systemctl','--user','stop','display.service'),self.runner.calls)


if __name__=='__main__':
    unittest.main()
