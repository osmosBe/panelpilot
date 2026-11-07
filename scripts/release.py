#!/usr/bin/env python3
"""Build a source release from an explicit allowlist, never from the whole workspace."""
import hashlib
from pathlib import Path
import re
import zipfile
import tomllib

ROOT=Path(__file__).resolve().parents[1]
VERSION=tomllib.loads((ROOT/'pyproject.toml').read_text())['project']['version']
top=['README.md','LICENSE','pyproject.toml','requirements.lock','.gitignore','.github/workflows/tests.yml']
paths=[ROOT/name for name in top]
patterns={'panelpilot':['*.py','static/*.html','static/*.css','static/*.js','static/*.svg','display/*.py'],
          'tests':['*.py'],'scripts':['*.py'],'docs':['*.md','*.jpg','*.png'],
          'system':['*.py','*.conf','*.timer','*.service','99zz-panelpilot-maintenance']}
for directory,globs in patterns.items():
    for glob in globs:
        paths.extend((ROOT/directory).glob(glob))
paths=sorted(set(paths))
for path in paths:
    if path.is_symlink() or not path.resolve().is_relative_to(ROOT):
        raise SystemExit('Refusing linked/outside release file: '+str(path))
    if path.suffix not in ('.jpg','.png'):
        text=path.read_text()
        if re.search(r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----',text):
            raise SystemExit('Private key detected: '+str(path))
        if re.search(r'gh[pousr]_[A-Za-z0-9]{30,}',text):
            raise SystemExit('Potential access token detected: '+str(path))
out=ROOT/'dist'; out.mkdir(exist_ok=True)
archive=out/('panelpilot-'+VERSION+'-source.zip')
with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED) as bundle:
    for path in paths:
        name='panelpilot-'+VERSION+'/'+path.relative_to(ROOT).as_posix()
        info=zipfile.ZipInfo(name,date_time=(2026,1,1,0,0,0))
        info.compress_type=zipfile.ZIP_DEFLATED
        info.external_attr=0o100644<<16
        bundle.writestr(info,path.read_bytes())
digest=hashlib.sha256(archive.read_bytes()).hexdigest()
(out/(archive.name+'.sha256')).write_text(digest+'  '+archive.name+'\n')
print(f'Source release: {archive.name}; {len(paths)} files; SHA256 {digest}')
