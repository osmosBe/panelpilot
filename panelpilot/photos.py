"""Managed local uploads, separate from read-only NFS and arbitrary photo paths."""
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import uuid
from .storage import write_json

MAX_UPLOAD=20*1024*1024
QUOTA=1024*1024*1024
MAX_FILES=500
NAME=re.compile(r'[a-f0-9]{32}\.(?:jpg|png|webp)')


class PhotoStore:
    def __init__(self,root,validator=None):
        self.root=Path(root); self.root.mkdir(parents=True,exist_ok=True)
        self.validator=validator or Path(__file__).parent/'display/validate_photo.py'

    @contextmanager
    def locked(self):
        with (self.root/'.upload.lock').open('a') as handle:
            fcntl.flock(handle,fcntl.LOCK_EX)
            yield

    def metadata(self):
        p=self.root/'.names.json'
        return json.loads(p.read_text()) if p.exists() else {}

    def file(self,name):
        if not NAME.fullmatch(name): raise ValueError('Ungültige Foto-ID.')
        path=self.root/name
        if path.is_symlink() or not path.is_file(): raise FileNotFoundError(name)
        return path

    def listing(self):
        with self.locked():
            names=self.metadata(); files=[]
            for path in sorted(self.root.iterdir()):
                if NAME.fullmatch(path.name) and path.is_file() and not path.is_symlink():
                    files.append({'id':path.name,'name':names.get(path.name,path.name),'size':path.stat().st_size,'url':'/api/photos/local/'+path.name})
            return {'files':files,'used_bytes':sum(f['size'] for f in files),'quota_bytes':QUOTA,
                    'max_upload_bytes':MAX_UPLOAD,'max_files':MAX_FILES,'directory':str(self.root)}

    def temporary(self):
        fd,path=tempfile.mkstemp(prefix='.upload-',dir=self.root)
        return os.fdopen(fd,'wb'),Path(path)

    def finish(self,temporary,title):
        try:
            result=subprocess.run(['/usr/bin/python3',str(self.validator),str(temporary)],capture_output=True,text=True,timeout=15,check=True)
            extension=json.loads(result.stdout)['extension']
        except (subprocess.SubprocessError,ValueError,KeyError) as exc:
            raise ValueError('Kein lesbares JPEG-, PNG- oder WebP-Bild (maximal 32 Megapixel).') from exc
        if extension not in ('jpg','png','webp'): raise ValueError('Ungültiges Bildformat.')
        with self.locked():
            files=[p for p in self.root.iterdir() if NAME.fullmatch(p.name) and p.is_file() and not p.is_symlink()]
            size=temporary.stat().st_size
            if not 0<size<=MAX_UPLOAD: raise ValueError('Ein Foto darf höchstens 20 MiB groß sein.')
            if len(files)>=MAX_FILES or sum(p.stat().st_size for p in files)+size>QUOTA or shutil.disk_usage(self.root).free<256*1024*1024:
                raise ValueError('Fotospeicher voll. Bitte zuerst lokale Fotos löschen.')
            name=uuid.uuid4().hex+'.'+extension
            title=re.sub(r'[\x00-\x1f\x7f]','',title.replace('\\','/').split('/')[-1])[:128] or 'Foto'
            names=self.metadata(); names[name]=title
            temporary.chmod(0o600); temporary.replace(self.root/name)
            try: write_json(self.root/'.names.json',names)
            except Exception:
                (self.root/name).unlink(missing_ok=True)
                raise
        return {'id':name,'name':title,'size':size}

    def delete(self,name):
        with self.locked():
            self.file(name).unlink()
            names=self.metadata(); names.pop(name,None); write_json(self.root/'.names.json',names)
