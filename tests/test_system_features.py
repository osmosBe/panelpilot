import importlib.util
import json
from pathlib import Path
import struct
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import zlib

from fastapi.testclient import TestClient
from pydantic import ValidationError
from panelpilot.admin import Engine, STATE, APT, REBOOT, render_maintenance
from panelpilot.admin_client import DemoAdmin, AdminClient, AdminUnavailable
from panelpilot.app import create_app
from panelpilot.auth import Auth, make_credentials
from panelpilot.controller import DemoController
from panelpilot.photos import PhotoStore, MAX_UPLOAD
from panelpilot.storage import write_json
from panelpilot.system_models import Maintenance, NFS


def png(width=2,height=2):
    def chunk(kind,value): return struct.pack('!I',len(value))+kind+value+struct.pack('!I',zlib.crc32(kind+value)&0xffffffff)
    return b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('!2I5B',width,height,8,2,0,0,0))+chunk(b'IDAT',zlib.compress(b'\0\xff\0\0\0\xff\0'*2))+chunk(b'IEND',b'')


class FeatureApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup); self.root=Path(self.tmp.name)
        write_json(self.root/'auth.json',make_credentials('a-long-test-password'))
        self.photos=PhotoStore(self.root/'photos')
        self.client=TestClient(create_app(DemoController(self.root/'settings.json'),Auth(self.root/'auth.json'),demo=False,
                              admin=DemoAdmin(self.root/'system.json'),photos=self.photos),base_url='https://testserver',client=('127.0.0.1',1234))
        self.addCleanup(self.client.close)

    def login(self):
        result=self.client.post('/api/login',json={'username':'admin','password':'a-long-test-password'})
        self.assertEqual(result.status_code,200,result.text)
        self.client.headers['x-csrf-token']=result.json()['csrf']

    def test_upload_requires_auth_csrf_origin_and_byte_limit(self):
        self.assertEqual(self.client.post('/api/photos/upload',content=png()).status_code,401)
        self.login(); token=self.client.headers.pop('x-csrf-token')
        self.assertEqual(self.client.post('/api/photos/upload',content=png()).status_code,403)
        self.client.headers['x-csrf-token']=token
        self.assertEqual(self.client.post('/api/photos/upload',content=png(),headers={'origin':'https://other.example'}).status_code,403)
        self.assertEqual(self.client.post('/api/photos/upload',content=b'x',headers={'content-length':str(MAX_UPLOAD+1)}).status_code,413)

    def test_actual_image_upload_list_read_delete_and_no_path_traversal(self):
        self.login()
        result=self.client.post('/api/photos/upload',content=png(),headers={'x-filename':'family.png','content-type':'image/png'})
        self.assertEqual(result.status_code,200,result.text); name=result.json()['id']
        self.assertEqual(self.client.get('/api/photos/local').json()['files'][0]['name'],'family.png')
        self.assertEqual(self.client.get('/api/photos/local/'+name).content,png())
        self.assertEqual(self.client.get('/api/photos/local/not-an-id.png').status_code,404)
        self.assertEqual(self.client.request('DELETE','/api/photos/local/'+name,json={}).status_code,200)
        self.assertEqual(self.client.get('/api/photos/local').json()['files'],[])

    def test_fake_images_rejected_and_temporary_files_cleaned(self):
        self.login()
        for data in (b'<svg onload="alert(1)"/>',b'not an image',png(100000,100000)):
            response=self.client.post('/api/photos/upload',content=data,headers={'content-type':'image/png'})
            self.assertEqual(response.status_code,422,response.text)
        self.assertEqual(list(self.photos.root.glob('.upload-*')),[])

    def test_chunked_upload_limit_and_disk_quota_are_enforced(self):
        self.login()
        with patch('panelpilot.app.MAX_UPLOAD',100):
            response=self.client.post('/api/photos/upload',content=iter([b'x'*60,b'x'*60]))
            self.assertEqual(response.status_code,413,response.text)
        with patch('panelpilot.photos.QUOTA',1):
            response=self.client.post('/api/photos/upload',content=png())
            self.assertEqual(response.status_code,422,response.text)
        self.assertEqual(self.photos.listing()['files'],[])
        self.assertEqual(list(self.photos.root.glob('.upload-*')),[])

    def test_uploaded_gallery_does_not_follow_file_symlinks(self):
        self.login()
        secret=self.root/'outside'; secret.write_text('not a photo')
        name='a'*32+'.png'; (self.photos.root/name).symlink_to(secret)
        self.assertEqual(self.client.get('/api/photos/local/'+name).status_code,404)
        self.assertEqual(self.photos.listing()['files'],[])

    def test_system_settings_require_auth_and_validate_before_helper(self):
        self.assertEqual(self.client.get('/api/system').status_code,401)
        self.login(); m=Maintenance().model_dump(); m['reboot_time']='04:30'
        self.assertEqual(self.client.put('/api/system/maintenance',json=m).status_code,200)
        self.assertEqual(self.client.get('/api/system').json()['maintenance']['reboot_time'],'04:30')
        m['reboot_time']='05:00\nExecStart=/bin/false'
        self.assertEqual(self.client.put('/api/system/maintenance',json=m).status_code,422)
        self.assertEqual(self.client.put('/api/system/nfs',json={'host':'--help','export':'/photos'}).status_code,422)


class SystemEngineTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup); self.root=Path(self.tmp.name)
        self.calls=[]; self.fail=False
        def runner(*args,check=True,timeout=30):
            self.calls.append(args)
            if self.fail and 'restart' in args and args[-1]=='apt-daily-upgrade.timer':
                self.fail=False; raise subprocess.CalledProcessError(1,args)
            out='192.168.10.2 STREAM nas\n' if args[0]=='/usr/bin/getent' else ''
            return subprocess.CompletedProcess(args,0,out,'')
        self.engine=Engine({'mountpoint':'/mnt/panelpilot_photos','user':'panel','photo_probe':'/opt/probe.py'},self.root,runner)
        write_json(self.engine.path(STATE),{'maintenance':Maintenance().model_dump(),'nfs':NFS().model_dump()})

    def test_schedule_render_and_apply_never_starts_reboot_service(self):
        value=Maintenance(update_time='00:10',reboot_time='04:30',reboot_day='Sat',only_ac=False)
        files=render_maintenance(value)
        self.assertIn('23:40:00',files['/etc/systemd/system/apt-daily.timer.d/90-panelpilot.conf'])
        self.assertIn('Persistent=false',files[REBOOT]); self.assertIn('Automatic-Reboot "false"',files[APT])
        self.engine.maintenance(value.model_dump())
        self.assertEqual(self.engine.state()['maintenance']['reboot_day'],'Sat')
        self.assertFalse(any(('start' in c or 'restart' in c) and 'panelpilot-weekly-reboot.service' in c for c in self.calls))

    def test_failed_schedule_apply_restores_files_and_saved_state(self):
        old='original apt config\n'; target=self.engine.path(APT); target.parent.mkdir(parents=True); target.write_text(old)
        self.fail=True
        with self.assertRaises(RuntimeError): self.engine.maintenance(Maintenance(update_time='04:00').model_dump())
        self.assertEqual(target.read_text(),old)
        self.assertEqual(self.engine.state()['maintenance']['update_time'],'03:00')

    def test_nfs_mounts_are_readonly_and_no_arbitrary_options_or_actions(self):
        nfs=NFS(enabled=True,host='nas.local',export='/photos')
        self.assertEqual(self.engine.source(nfs),'192.168.10.2:/photos')
        self.engine.nfs(nfs.model_dump())
        text=self.engine.path(self.engine.mount_file).read_text()
        self.assertIn('ro,nosuid,nodev,noexec',text)
        self.assertIn('What=192.168.10.2:/photos',text)
        with self.assertRaises(ValueError): self.engine.call({'action':'execute','data':{'command':'id'}})
        with self.assertRaises(ValidationError): NFS.model_validate({'host':'nas.local','export':'/photos','options':'rw'})

    def test_public_nfs_addresses_are_rejected(self):
        self.engine.run=lambda *args,**kwargs:subprocess.CompletedProcess(args,0,'203.0.113.5 STREAM nas\n','')
        with self.assertRaises(ValueError): self.engine.source(NFS(host='nas.example',export='/photos'))

    def test_testmount_cleanup_requires_token_and_expires_after_client_loss(self):
        value=self.engine.nfs_begin(NFS(host='nas.local',export='/photos').model_dump())
        with self.assertRaises(ValueError): self.engine.nfs_end({'token':'wrong'})
        self.assertIsNotNone(self.engine.lease)
        with patch('panelpilot.admin.time.monotonic',return_value=self.engine.lease[1]+1): self.engine.cleanup()
        self.assertIsNone(self.engine.lease)

    def test_unprivileged_read_failure_still_releases_testmount(self):
        client=AdminClient()
        with patch.object(client,'call',side_effect=[{'path':'/run/panelpilot-admin/nfs-test','token':'a'*64},{'success':True}]) as call:
            with patch('panelpilot.admin_client.subprocess.run',side_effect=subprocess.TimeoutExpired('photo-probe',12)):
                with self.assertRaises(AdminUnavailable): client.test_nfs(NFS(host='nas.local',export='/photos').model_dump())
            self.assertEqual(call.call_args.args,('nfs.test.end',{'token':'a'*64}))

    def test_untrusted_paths_and_invalid_calendar_inputs_rejected(self):
        for value in ('/photos\nWhere=/etc','/photos/../etc','/photos%h','relative'):
            with self.assertRaises(ValidationError): NFS(export=value)
        for value in ('25:00','01:60','5:00','05:00\n'):
            with self.assertRaises(ValidationError): Maintenance(update_time=value)
        with self.assertRaises(ValidationError): Maintenance(timezone='../../etc/passwd')


class PortableSetupTests(unittest.TestCase):
    def test_autologin_preserves_case_sensitive_gdm_keys(self):
        spec=importlib.util.spec_from_file_location('setup_script',Path(__file__).resolve().parents[1]/'scripts/setup.py')
        module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'custom.conf'; path.write_text('[daemon]\nWaylandEnable=true\n[security]\nDisallowTCP=true\n')
            module.configure_gdm(path,'panel')
            self.assertIn('AutomaticLoginEnable = true',path.read_text())
            self.assertIn('AutomaticLogin = panel',path.read_text())
            self.assertIn('WaylandEnable = true',path.read_text())
            self.assertIn('DisallowTCP = true',path.read_text())

    def test_supported_platform_is_explicit_not_any_linux_or_python(self):
        spec=importlib.util.spec_from_file_location('setup_script',Path(__file__).resolve().parents[1]/'scripts/setup.py')
        module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
        self.assertTrue(module.supported({'ID':'ubuntu','VERSION_CODENAME':'noble'},'aarch64'))
        self.assertTrue(module.supported({'ID':'zorin','UBUNTU_CODENAME':'noble'},'x86_64'))
        self.assertFalse(module.supported({'ID':'ubuntu','VERSION_CODENAME':'resolute'},'aarch64'))
        self.assertFalse(module.supported({'ID':'debian','VERSION_CODENAME':'noble'},'aarch64'))


if __name__=='__main__': unittest.main()
