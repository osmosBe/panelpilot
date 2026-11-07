"""Read only metadata and decode one photo; never return paths or photo contents."""
import json
import sys
from panel_slideshow import PhotoLoader, scan_photos

paths, status = scan_photos(sys.argv[1])
picture, decoded = PhotoLoader(sys.argv[1]).next_photo() if paths else (None, status)
print(json.dumps({'count': len(paths), 'status': decoded if paths else status}))
