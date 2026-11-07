"""Decode a bounded upload in an isolated, unprivileged system-Python process."""
import json
import resource
import sys
import gi
gi.require_version('GdkPixbuf','2.0')
from gi.repository import GdkPixbuf

resource.setrlimit(resource.RLIMIT_AS,(512*1024*1024,512*1024*1024))
resource.setrlimit(resource.RLIMIT_CPU,(10,10))
fmt,width,height=GdkPixbuf.Pixbuf.get_file_info(sys.argv[1])
formats={'jpeg':'jpg','png':'png','webp':'webp'}
if not fmt or fmt.get_name() not in formats or width<1 or height<1 or width*height>32_000_000:
    raise SystemExit('JPEG, PNG oder WebP mit höchstens 32 Megapixeln erforderlich.')
image=GdkPixbuf.Pixbuf.new_from_file(sys.argv[1])
print(json.dumps({'extension':formats[fmt.get_name()],'width':width,'height':height}))
