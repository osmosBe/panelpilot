"""Responsive local/mounted-folder photo slideshow for the panel idle window."""
import os
from pathlib import Path
import random
import threading

import cairo
import gi
gi.require_version('Gtk', '3.0')
gi.require_version('Gdk', '3.0')
gi.require_version('GdkPixbuf', '2.0')
gi.require_foreign('cairo')
from gi.repository import Gdk, GdkPixbuf, GLib, Gtk

PHOTO_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.webp'}
IGNORED_DIRECTORIES = {'@eaDir', '#recycle'}


def scan_photos(directory):
    """Follow local directory links with cycle and size limits, in the worker."""
    pending = [(Path(directory), 0)]
    visited = set()
    seen_files = set()
    paths = []
    unavailable = False
    entries_seen = 0
    while pending and len(visited) < 2000 and len(paths) < 2000 and entries_seen < 50000:
        folder, depth = pending.pop()
        try:
            info = folder.stat()
            identity = (info.st_dev, info.st_ino)
            if identity in visited:
                continue
            visited.add(identity)
            with os.scandir(folder) as entries:
                for entry in entries:
                    entries_seen += 1
                    if len(paths) >= 2000 or entries_seen > 50000:
                        break
                    # Synology creates image thumbnails and keeps deleted photos
                    # in these directories; neither belongs in the album itself.
                    if entry.name in IGNORED_DIRECTORIES:
                        continue
                    try:
                        if entry.is_dir(follow_symlinks=True):
                            if depth < 12:
                                pending.append((Path(entry.path), depth + 1))
                        elif Path(entry.name).suffix.lower() in PHOTO_EXTENSIONS and entry.is_file(follow_symlinks=True):
                            file_info = entry.stat(follow_symlinks=True)
                            file_id = (file_info.st_dev, file_info.st_ino)
                            if file_id not in seen_files:
                                seen_files.add(file_id)
                                paths.append(Path(entry.path))
                    except OSError:
                        unavailable = True
        except OSError:
            unavailable = True
    return paths, ('ready' if paths else 'unavailable' if unavailable else 'empty')


class PhotoLoader:
    """Single-worker loader; no network-filesystem I/O on the GTK thread."""
    def __init__(self, directory):
        self.directory = Path(directory)
        self.remaining = []
        self.previous = None

    def next_photo(self):
        paths, status = scan_photos(self.directory)
        if not paths:
            return None, status
        available = set(paths)
        self.remaining = [p for p in self.remaining if p in available]
        if not self.remaining:
            self.remaining = paths
            random.shuffle(self.remaining)
            if len(paths) > 1 and self.remaining[-1] == self.previous:
                self.remaining[0], self.remaining[-1] = self.remaining[-1], self.remaining[0]
        while self.remaining:
            path = self.remaining.pop()
            try:
                if path.stat().st_size > 50 * 1024 * 1024:
                    continue
                info = GdkPixbuf.Pixbuf.get_file_info(str(path))
                if not info or not info[0] or info[1] * info[2] > 100_000_000:
                    continue
                photo = GdkPixbuf.Pixbuf.new_from_file_at_scale(
                    str(path), min(info[1], 2560), min(info[2], 2560), True)
                photo = photo.apply_embedded_orientation() or photo
                self.previous = path
                return photo, 'ready'
            except (OSError, GLib.Error):
                continue
        return None, 'unreadable'


def fit_rectangle(image_width, image_height, width, height):
    scale = min(width / image_width, height / image_height)
    return (width - image_width * scale) / 2, (height - image_height * scale) / 2, scale


class PhotoSlideshow:
    def __init__(self, directory, seconds, on_available, diagnostics=False):
        self.loader = PhotoLoader(directory)
        self.seconds = seconds
        self.on_available = on_available
        self.diagnostics = diagnostics
        self.widget = Gtk.DrawingArea()
        self.widget.connect('draw', self._draw)
        self.photo = None
        self.active = False
        self.loading = False
        self.generation = 0
        self.timer = 0

    def start(self):
        self.stop()
        self.active = True
        self._request_next()
        self.timer = GLib.timeout_add_seconds(self.seconds, self._request_next)

    def stop(self):
        self.active = False
        self.generation += 1
        if self.timer:
            GLib.source_remove(self.timer)
            self.timer = 0
        self.photo = None
        self.widget.queue_draw()

    def _request_next(self):
        if not self.active:
            return GLib.SOURCE_REMOVE
        if not self.loading:
            self.loading = True
            generation = self.generation
            threading.Thread(target=self._load, args=(generation,), daemon=True).start()
        return GLib.SOURCE_CONTINUE

    def _load(self, generation):
        try:
            photo, status = self.loader.next_photo()
        except Exception:
            photo, status = None, 'unavailable'
        GLib.idle_add(self._accept, generation, photo, status)

    def _accept(self, generation, photo, status):
        self.loading = False
        if not self.active:
            return GLib.SOURCE_REMOVE
        if generation != self.generation:
            self._request_next()
            return GLib.SOURCE_REMOVE
        if status == 'unavailable' and self.photo is not None:
            # Keep the current image while an external share is unavailable.
            return GLib.SOURCE_REMOVE
        first_photo = self.photo is None
        self.photo = photo
        self.widget.queue_draw()
        self.on_available(photo is not None)
        if photo is not None and (self.diagnostics or first_photo):
            print('Photo loaded for idle display.', flush=True)
        return GLib.SOURCE_REMOVE

    def _draw(self, widget, context):
        context.set_source_rgb(0, 0, 0)
        context.paint()
        if self.photo is None:
            return False
        x, y, scale = fit_rectangle(
            self.photo.get_width(), self.photo.get_height(),
            widget.get_allocated_width(), widget.get_allocated_height())
        context.save()
        context.translate(x, y)
        context.scale(scale, scale)
        Gdk.cairo_set_source_pixbuf(context, self.photo, 0, 0)
        context.get_source().set_filter(cairo.FILTER_BILINEAR)
        context.paint()
        context.restore()
        return False
