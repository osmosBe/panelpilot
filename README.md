# PanelPilot

A small, local web interface for managing a Linux touchscreen with a Home Assistant
kiosk, a photo slideshow, and a separate Linux Voice Assistant. Works on phones and
desktops, with its own login and HTTPS. No cloud services, external fonts, or analytics.

**Language:** The management dashboard is currently available in German only.
This README is in English; the detailed guides linked below are currently in German.

![PanelPilot overview with sample data](docs/desktop.jpg)

## Personal notes

I started this project because I wasn't happy with the existing solutions for Android
tablets. So I repurposed an old ThinkPad Yoga 12 and used AI to build this. I probably
won't spend much time maintaining it, but someone else might find it useful.

## What you can configure

- Separate brightness levels for the dashboard and slideshow
- Idle timeout, slide interval, and idle mode: photos, a black screen, or an always-visible dashboard
- Photo source: a local folder or an already-mounted network share
- Home Assistant URL, including a direct dashboard path
- Voice Assistant on/off, with the setting preserved across restarts
- Automatic screen rotation and the management password
- Status of all three services, a photo readability check, and a browser-only restart
- Daily OS updates, an AC-power-only policy, and a weekly reboot with configurable times and time zone
- Test and configure a read-only NFS connection and select it as the photo source
- Upload, view, and delete photos from a phone or computer; use a local gallery without a NAS

Touch, mouse, or keyboard input exits the idle display. Voice control continues
independently. Photos load in the background, fit fully on screen, and use the correct
orientation. Directory symlinks are followed with limits on cycles and scan size.
Synology `@eaDir` and `#recycle` directories are excluded.

## Status and requirements

**Version 0.2.0** includes the web interface, a system helper, and a combined installer.
Tested on Zorin OS 18.1 / Ubuntu 24.04, GNOME 46, Wayland, and systemd.
The display uses GNOME's IdleMonitor and logind. Without `/sys/class/backlight`,
the idle display uses software dimming for photos; dashboard brightness must then be
adjusted on the monitor. Other desktop environments are not supported.

The [combined installer](docs/SETUP.md) sets up a dedicated kiosk account, automatic
login, power management, browser, display, optional Voice Assistant, web interface,
and OS maintenance on Ubuntu Desktop 24.04 / Zorin 18. Target architectures are
amd64 and arm64. The full fresh-install process and Raspberry Pi hardware have not
yet been validated on a second device. Signing in to Home Assistant and connecting
the voice pipeline remain one-time steps on the target system.

```sh
sudo python3 scripts/setup.py --install --owner "$USER" \
  --hostname panel.local --url https://homeassistant.example --voice
```

Without `--install`, the script only checks the system. Without `--voice`, it installs
a panel without microphone functionality. See [initial setup](docs/SETUP.md),
[installation on an existing panel](docs/INSTALLATION.md),
[operations](docs/OPERATIONS.md), and [architecture](docs/ARCHITECTURE.md).

An [OS maintenance profile](docs/MAINTENANCE.md) is included: daily unattended
upgrades from stable sources and a weekly reboot on Sundays at 05:00, with safeguards
for running package installations. In version 0.2, this is managed under
“Updates & Neustart” (Updates & Restart) in the web interface.
The web application runs without root privileges; a local system service handles
only the predefined scheduling and NFS actions.

## Demo without hardware changes

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements.lock
.venv/bin/python -m panelpilot serve --demo --port 8765
```

Open `http://127.0.0.1:8765`. Demo login: **demo / demo-panelpilot**.
The demo binds only to loopback, writes only to `.demo-state/`, and does not call
any panel services. The sample data and demo password are never used for the
production management service.

## Development

```sh
.venv/bin/pip install -e '.[test]'
.venv/bin/python -m unittest discover -s tests -v
```

Tests use temporary files and simulated service calls. They cover login, cookies,
CSRF, origin and host checks, input validation, conflicts between browser sessions,
password changes, service states, command argument quoting, and rollback on errors.
The native display also requires the GTK packages listed in the installation guide.

## Publishing

This directory is the standalone project. Publish only its source files, not the
parent setup directory or a local installation.
`scripts/release.py` builds a source archive from an explicit list of project files
and checks for private keys and certain GitHub token formats. These checks are
limited and do not replace a full secret scan. The `.gitignore` excludes the virtual
environment, certificates, credential files, demo data, and local settings.

```sh
python3 scripts/release.py
```

MIT license. Home Assistant, Linux Voice Assistant, and the Python/GTK dependencies
are separate projects with their own licenses. No third-party Voice Assistant
source code is bundled.
