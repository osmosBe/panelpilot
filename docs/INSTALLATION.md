# Installation auf einem bestehenden Panel

Für ein frisch installiertes Ubuntu/Zorin zuerst [Ersteinrichtung](SETUP.md) lesen.
Dieses Dokument beschreibt die Übernahme bereits vorhandener Paneldienste.

PanelPilot 0.2 verwaltet vorhandene systemd-Benutzerdienste in einer GNOME-Sitzung.
Die Weboberfläche führt keine Befehle als root aus. Der einmalige Installer benötigt
Administratorrechte für `/opt`, das lokale Zertifikat und die gemeinsame Konfiguration.

## Voraussetzungen

- Ubuntu 24.04 / Zorin OS 18.1 mit GNOME und Wayland; Python 3.11 oder neuer
- Ein bestehender, angemeldeter Kioskbenutzer ohne Administratorrechte
- Bestehende Benutzerdienste: Browser und Ruheanzeige; Voice optional mit `--without-voice` abwählen
- Browser-`ExecStart` mit separater HTTP(S)-URL als letztem Argument
- Für physische Helligkeitsregelung ein Linux-Backlight-Gerät mit logind-Zugriff; sonst Foto-Abdunklung per Software
- GNOME-Standby so eingestellt, dass der Rechner für den Sprachdienst wach bleibt
- Lokale Fotos oder ein erreichbarer Fotopfad; NFS kann anschließend im Web eingerichtet werden

Benötigte Betriebssystem-Pakete:

```sh
sudo apt install python3-venv python3-gi python3-cairo python3-gi-cairo gir1.2-gtk-3.0 openssl acl
```

Home Assistant läuft separat. Für die Sprachkomponente siehe das offizielle Projekt
[OHF-Voice/linux-voice-assistant](https://github.com/OHF-Voice/linux-voice-assistant).
PanelPilot übernimmt dessen vorhandenen Dienst unverändert, ergänzt aber eine
dauerhafte Einschaltbedingung. Autologin und Sprachpipeline werden hier nicht angelegt.

## Projekt bauen

Im PanelPilot-Projektverzeichnis:

```sh
python3 -m venv .venv
.venv/bin/pip install 'setuptools>=77' wheel
.venv/bin/pip download -r requirements.lock --dest /tmp/panelpilot-wheels
.venv/bin/pip wheel --no-deps --no-build-isolation . --wheel-dir /tmp/panelpilot-wheels
```

Den folgenden Befehl an die eigenen Linux-Konten und den DNS-Namen anpassen.
`panel-owner` ist ein Platzhalter für den normalen lokalen Benutzer. `panel.local`
muss im eigenen Netzwerk zum Panel auflösen. Zusätzliche IP-Adressen lassen sich mit
`--address` in das Zertifikat aufnehmen.

```sh
sudo python3 scripts/install.py \
  --user ha-panel \
  --owner panel-owner \
  --hostname panel.local \
  --config-dir /srv/panelpilot-config \
  --wheelhouse /tmp/panelpilot-wheels
sudo /opt/panelpilot/venv/bin/python scripts/system_install.py --fresh-maintenance
```

Abweichende Dienstenamen: `--kiosk-unit`, `--display-unit`, `--voice-unit`.
Der Installer überschreibt keine vorhandene PanelPilot-Installation. Er übernimmt
URL, Fotopfad, Zeiten, Fotohelligkeit und Drehung aus der vorhandenen Konfiguration.
Die Dashboard-Helligkeit wird aus dem gespeicherten bzw. aktuellen Backlight-Wert ermittelt.

## Erster Zugriff

- URL: `https://panel.local:8443` mit dem bei der Installation angegebenen Namen
- Zugang: `~/PanelPilot-Zugang.txt` im Home-Verzeichnis des angegebenen Owners, Modus `0600`
- Öffentliches Zertifikat: `~/PanelPilot-Zertifikat.crt`
- Passwort anschließend unter **Zugang** ändern; Sitzungen werden dabei ungültig

Das lokal erzeugte Zertifikat gilt ein Jahr und ist selbst signiert. Browser zeigen
daher zunächst eine Zertifikatsmeldung. Vergleiche den SHA-256-Fingerabdruck mit der
lokalen Zugangsdatei oder hinterlege das Zertifikat gezielt auf deinen eigenen Geräten.
Für ein bereits vorhandenes vertrauenswürdiges Zertifikat siehe das Betriebsdokument.

Der Dienst lauscht auf IPv4-Port 8443. Nur Loopback, RFC1918-Adressen und lokale
IPv6-Bereiche sind in der Anwendung zugelassen; Proxy-Header werden nicht vertraut.
Es werden keine Router-Portfreigaben oder Firewall-Regeln eingerichtet.

## Rücknahme

```sh
sudo python3 scripts/uninstall.py
```

Dies stoppt die Webverwaltung und entfernt ausschließlich ihre Benutzerdienste und
Overrides. Der lokale Systemhelfer wird ebenfalls deaktiviert; bestehende NFS-Mounts
und OS-Wartungstimer bleiben erhalten. Die ursprünglichen Paneldienste werden wieder gestartet. App-Dateien,
lokale Einstellungen, Zugangsdaten und Fotos bleiben erhalten. Eine Rücknahme kann
damit den zuvor über die Weboberfläche ausgeschalteten Sprachdienst wieder starten.
Bei einer fehlgeschlagenen Installation lässt sich das gleiche Skript ab dem Zeitpunkt
verwenden, an dem `/etc/panelpilot/runtime.json` angelegt wurde; frühere Fehler verändern
die Paneldienste nicht und können nur die neue `/opt/panelpilot`-Umgebung hinterlassen.

`scripts/upgrade.py` aktualisiert eine vorhandene Installation mit vorheriger Sicherung.
Die Webverwaltung installiert keine neuen Ubuntu-Hauptversionen und aktualisiert sich
nicht selbst. Siehe [Ersteinrichtung und Upgrade](SETUP.md).
