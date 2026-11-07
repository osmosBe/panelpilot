"""Unprivileged client; root helper only accepts a fixed action vocabulary."""
import json
from pathlib import Path
import socket
import subprocess
from .storage import write_json
from .system_models import Maintenance, NFS

SOCKET = '/run/panelpilot-admin/control.sock'


class AdminUnavailable(Exception):
    pass


class AdminClient:
    def test_nfs(self,data):
        lease=self.call('nfs.test.begin',data)
        try:
            # This process already runs as the panel user. No root-side identity switch.
            helper=Path(__file__).parent/'display/photo_probe.py'
            result=subprocess.run(['/usr/bin/python3',str(helper),lease['path']],text=True,capture_output=True,timeout=12,check=True)
            return json.loads(result.stdout)
        except (OSError,ValueError,subprocess.SubprocessError) as exc:
            raise AdminUnavailable('Die Testfreigabe ist für den Panelbenutzer nicht lesbar. NAS-Rechte prüfen.') from exc
        finally:
            self.call('nfs.test.end',{'token':lease['token']})

    def call(self, action, data=None):
        try:
            with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as client:
                client.settimeout(150)
                client.connect(SOCKET)
                client.sendall(json.dumps({'action':action,'data':data or {}}).encode()+b'\n')
                with client.makefile('rb') as stream:
                    raw=stream.readline(65537)
                if len(raw)>65536: raise ValueError('oversize response')
                result=json.loads(raw)
        except (OSError,ValueError) as exc:
            raise AdminUnavailable('Systemverwaltung momentan nicht erreichbar.') from exc
        if not result.get('ok'):
            raise AdminUnavailable(result.get('error','Systemänderung fehlgeschlagen.'))
        return result['result']


class DemoAdmin:
    def __init__(self,path): self.path=Path(path)

    def test_nfs(self,data):
        NFS.model_validate(data).require_source()
        return {'status':'ready','count':12,'demo':True}

    def call(self,action,data=None):
        state=json.loads(self.path.read_text()) if self.path.exists() else {'maintenance':Maintenance().model_dump(),'nfs':NFS().model_dump()}
        if action=='maintenance.apply': state['maintenance']=Maintenance.model_validate(data).model_dump()
        elif action=='nfs.apply': state['nfs']=NFS.model_validate(data).model_dump()
        elif action!='status': raise AdminUnavailable('Unbekannte Aktion.')
        write_json(self.path,state)
        return {**state,'mountpoint':'/mnt/panelpilot-photos','mounted':state['nfs']['enabled'],
                'timers':{},'snap':False,'demo':True}
