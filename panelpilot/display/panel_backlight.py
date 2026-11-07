#!/usr/bin/python3
"""Show a touch-dismissable idle display while preserving microphone input."""
import argparse
import json
import os
from pathlib import Path
import signal

import gi
from gi.repository import Gio, GLib

BACKLIGHT = Path('/sys/class/backlight')


def proxy(bus, name, path, interface):
    result = Gio.DBusProxy.new_for_bus_sync(
        bus, Gio.DBusProxyFlags.NONE, None, name, path, interface, None)
    if not result.get_name_owner():
        raise RuntimeError('D-Bus service unavailable: ' + name)
    return result


def call(obj, method, signature=None, value=None):
    params = GLib.Variant(signature, value) if signature else None
    return obj.call_sync(method, params, Gio.DBusCallFlags.NONE, 3000, None)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--test-seconds', type=int, default=0)
    parser.add_argument('--idle-seconds', type=int, default=300)
    parser.add_argument('--blank-level', type=int, default=0)
    parser.add_argument('--black-overlay', action='store_true')
    parser.add_argument('--photo-directory', type=Path)
    parser.add_argument('--slide-seconds', type=int, default=30)
    parser.add_argument('--photo-brightness', type=int, default=20)
    parser.add_argument('--restore', action='store_true')
    parser.add_argument('--awake-brightness', type=int)
    parser.add_argument('--set-awake', action='store_true')
    parser.add_argument('--awake-only', action='store_true')
    parser.add_argument('--backlight-device')
    parser.add_argument('--state-file', type=Path, default=Path.home() / '.local/state/ha-panel/backlight.json')
    args = parser.parse_args()
    if args.awake_brightness is not None and not 1 <= args.awake_brightness <= 100:
        parser.error('Awake brightness must be 1 to 100.')
    candidates = sorted(BACKLIGHT.iterdir())
    if args.backlight_device:
        candidates = [p for p in candidates if p.name == args.backlight_device]
    preferred = [p for p in candidates if p.name == 'intel_backlight']
    backlight = (preferred or candidates)[0] if candidates else None
    if args.backlight_device and backlight is None:
        parser.error('Selected backlight device not found.')
    if args.test_seconds and not 10 <= args.test_seconds <= 60:
        parser.error('Test duration must be 10 to 60 seconds.')
    if args.idle_seconds < 1:
        parser.error('Idle timeout must be positive.')
    if args.slide_seconds < 2 or not 1 <= args.photo_brightness <= 100:
        parser.error('Slide duration must be at least 2 seconds; brightness must be 1 to 100.')
    if args.photo_directory:
        args.photo_directory = args.photo_directory.expanduser()
        if not args.photo_directory.is_absolute():
            parser.error('Photo directory must be an absolute local or mounted path.')
    maximum = int((backlight / 'max_brightness').read_text()) if backlight else 100
    if not 0 <= args.blank_level <= maximum:
        parser.error('Blank level is outside the backlight range.')
    manager = proxy(Gio.BusType.SYSTEM, 'org.freedesktop.login1', '/org/freedesktop/login1', 'org.freedesktop.login1.Manager')
    user_path = call(manager, 'GetUser', '(u)', (os.getuid(),)).unpack()[0]
    user = proxy(Gio.BusType.SYSTEM, 'org.freedesktop.login1', user_path, 'org.freedesktop.login1.User')
    session_path = user.get_cached_property('Display').unpack()[1]
    if session_path == '/':
        raise RuntimeError('No graphical login session is available.')
    session = proxy(Gio.BusType.SYSTEM, 'org.freedesktop.login1', session_path, 'org.freedesktop.login1.Session')
    if session.get_cached_property('User').unpack()[0] != os.getuid():
        raise RuntimeError('Refusing to change another user\'s backlight.')
    overlay = None
    slideshow = None

    def session_active():
        return bool(session.get_cached_property('Active').unpack())

    def brightness(value):
        if backlight:
            call(session, 'SetBrightness', '(ssu)', ('backlight', backlight.name, value))

    def awake_brightness():
        if args.awake_brightness is not None and session_active():
            brightness(max(1, round(maximum * args.awake_brightness / 100)))

    def restore():
        if slideshow is not None:
            slideshow.stop()
        if overlay is not None:
            overlay.hide()
        if args.state_file.exists() and session_active():
            saved = int(json.loads(args.state_file.read_text())['brightness'])
            if args.awake_brightness is not None:
                saved = round(maximum * args.awake_brightness / 100)
            brightness(max(1, min(maximum, saved)))
            args.state_file.unlink()
            print('Backlight restored.', flush=True)

    restore()
    if args.set_awake:
        awake_brightness()
        return
    if args.restore:
        return
    awake_brightness()
    if args.black_overlay or args.photo_directory:
        gi.require_version('Gtk', '3.0')
        gi.require_version('Gdk', '3.0')
        from gi.repository import Gdk, Gtk
        overlay = Gtk.Window(title='PanelPilot Ruheanzeige')
        overlay.set_decorated(False)
        overlay.set_skip_taskbar_hint(True)
        overlay.set_skip_pager_hint(True)
        overlay.set_keep_above(True)
        css = Gtk.CssProvider()
        css.load_from_data(b'window { background-color: #000000; }')
        overlay.get_style_context().add_provider(css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        overlay.connect('delete-event', lambda *unused: True)
        overlay.connect('realize', lambda window: window.get_window().set_cursor(
            Gdk.Cursor.new_from_name(window.get_display(), 'none')))
    idle = proxy(Gio.BusType.SESSION, 'org.gnome.Mutter.IdleMonitor', '/org/gnome/Mutter/IdleMonitor/Core', 'org.gnome.Mutter.IdleMonitor')
    loop = GLib.MainLoop()
    idle_watch = 0
    active_watch = 0
    failed = False

    def photo_available(available):
        nonlocal failed
        if not session_active() or not args.state_file.exists():
            return
        level = max(1, round(maximum * args.photo_brightness / 100)) if available else args.blank_level
        try:
            brightness(level)
        except GLib.Error as exc:
            print('Photo brightness failed: ' + str(exc), flush=True)
            failed = True
            loop.quit()

    if args.photo_directory:
        from panel_slideshow import PhotoSlideshow
        slideshow = PhotoSlideshow(args.photo_directory, args.slide_seconds,
                                   photo_available, diagnostics=bool(args.test_seconds))
        if backlight is None:
            slideshow.widget.set_opacity(args.photo_brightness/100)
        overlay.add(slideshow.widget)

    def blank():
        nonlocal active_watch
        if not session_active() or args.state_file.exists():
            return GLib.SOURCE_REMOVE
        value = int((backlight / 'brightness').read_text()) if backlight else 100
        if value <= 0:
            raise RuntimeError('Backlight is already at zero; no restore level known.')
        args.state_file.parent.mkdir(parents=True, exist_ok=True)
        temporary = args.state_file.with_suffix('.tmp')
        temporary.write_text(json.dumps({'brightness': value}) + '\n')
        temporary.replace(args.state_file)
        active_watch = call(idle, 'AddUserActiveWatch').unpack()[0]
        brightness(args.blank_level)
        if overlay is not None:
            overlay.fullscreen()
            overlay.show_all()
            overlay.present()
        if slideshow is not None:
            slideshow.start()
        print('Idle display active; waiting for normal desktop input.', flush=True)
        return GLib.SOURCE_REMOVE

    def signalled(obj, sender, name, parameters):
        nonlocal active_watch, failed
        if name != 'WatchFired':
            return
        watch = parameters.unpack()[0]
        try:
            if watch == active_watch:
                active_watch = 0
                restore()
                print('Desktop input detected; backlight awake.', flush=True)
            elif watch == idle_watch:
                blank()
        except (OSError, ValueError, GLib.Error, RuntimeError) as exc:
            print('Backlight control failed: ' + str(exc), flush=True)
            failed = True
            loop.quit()

    def end():
        loop.quit()
        return GLib.SOURCE_REMOVE

    def session_changed(obj, changed, invalidated):
        nonlocal idle_watch, active_watch, failed
        if 'Active' not in changed.unpack():
            return
        try:
            restore()
            awake_brightness()
            if active_watch:
                call(idle, 'RemoveWatch', '(u)', (active_watch,))
                active_watch = 0
            if idle_watch:
                call(idle, 'RemoveWatch', '(u)', (idle_watch,))
                idle_watch = 0
            if session_active() and not args.test_seconds and not args.awake_only:
                idle_watch = call(idle, 'AddIdleWatch', '(t)', (args.idle_seconds * 1000,)).unpack()[0]
        except (OSError, ValueError, GLib.Error) as exc:
            print('Session display transition failed: ' + str(exc), flush=True)
            failed = True
            loop.quit()

    def test_blank():
        nonlocal failed
        if not session_active():
            return GLib.SOURCE_CONTINUE
        try:
            return blank()
        except (OSError, ValueError, GLib.Error, RuntimeError) as exc:
            print('Backlight test failed: ' + str(exc), flush=True)
            failed = True
            loop.quit()
            return GLib.SOURCE_REMOVE

    idle.connect('g-signal', signalled)
    session.connect('g-properties-changed', session_changed)
    GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGTERM, end)
    GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGINT, end)
    if args.test_seconds:
        GLib.timeout_add_seconds(8, test_blank)
        GLib.timeout_add_seconds(args.test_seconds, end)
    elif not args.awake_only:
        idle_watch = call(idle, 'AddIdleWatch', '(t)', (args.idle_seconds * 1000,)).unpack()[0]
    print('Backlight controller ready; no raw input devices are opened.', flush=True)
    try:
        loop.run()
    finally:
        restore()
        for watch in (idle_watch, active_watch):
            if watch:
                call(idle, 'RemoveWatch', '(u)', (watch,))
    if failed:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
