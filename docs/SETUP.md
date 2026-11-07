# Ersteinrichtung und Upgrade

## Zielplattform

Ubuntu **Desktop 24.04 LTS**, GNOME/GDM und systemd, amd64 oder arm64; Zorin OS 18 mit
Ubuntu-Basis Noble wird ebenfalls akzeptiert. Internetzugang und ein vorhandenes
Administratorkonto werden benötigt. Ubuntu Server, Raspberry Pi OS und andere Desktops
werden von diesem Installer bewusst nicht verändert.

Ein Raspberry Pi mit Ubuntu Desktop ist ein Ziel für die ARM64-Installation. Die
kombinierte Installation und physische Touch-/Audiofunktionen wurden noch nicht auf
einem Pi getestet. Canonical führt unterstützte Modelle und RAM-Anforderungen in seiner
[Hardwaretabelle](https://ubuntu.com/hardware/docs/boards/how-to/ubuntu_supported/raspberry-pi/).
Auch der vollständige Ablauf auf einem frisch installierten zweiten PC benötigt noch
einen Abnahmetest. Der bestehende Zorin-Kiosk dient als Entwicklungs- und Upgrade-Gerät.

Ubuntu 26.04 wird vorerst abgewiesen: dessen Standard-Python 3.14 liegt außerhalb der
vom gepinnten Voice-Assistant-Setup genannten Python-Versionen 3.11–3.13.

## Ein Befehl für die Bausteine

Quellarchiv entpacken, ins Projektverzeichnis wechseln und zuerst den Plan prüfen:

```sh
python3 scripts/setup.py --owner "$USER" --hostname panel.local \
  --url https://homeassistant.example --voice
```

Zur Einrichtung denselben Aufruf mit `sudo` und `--install` verwenden:

```sh
sudo python3 scripts/setup.py --install --owner "$USER" --hostname panel.local \
  --url https://homeassistant.example --voice
```

`panel.local` und die Home-Assistant-Adresse sind Platzhalter. Der Panelname muss im
eigenen Netz auflösbar sein; standardmäßig verwendet der Installer den vorhandenen
Rechnernamen mit `.local`. Zusätzliche Adressen können mehrfach mit `--address` angegeben
werden. Ohne diese Option werden lokale private IPv4-Adressen ins Zertifikat aufgenommen.
Der Installer ändert keine Router-DNS-Einträge und keine Firewall.

`--user` wählt das neue Kioskkonto (Standard `ha-panel`). `--owner` bezeichnet das
bestehende persönliche Konto für Zugangsdaten und Einstellungen. Ohne `--voice` entfallen
Voice-Installation und Mikrofonfunktion. Der spätere Einbau einer bisher fehlenden
Voice-Komponente ist noch kein Web-Assistent; ein Upgrade erhält die vorhandenen Rollen.

Der Aufruf richtet Folgendes ein:

1. Betriebssystemabhängigkeiten und eine isolierte PanelPilot-Python-Umgebung.
2. Ein eigenes Konto ohne Administratorrechte und mit automatischer GDM-Anmeldung.
3. Keine Bildschirmsperre und keinen automatischen Suspend, auch auf Akku. Deckel-,
   Power- und Standbytasten werden für den Kioskbetrieb ignoriert.
4. Browser im Kioskmodus mit eigenem Profil und Start nach der grafischen Anmeldung.
5. Ruheanzeige mit Touch-Rückkehr; automatische Drehung, soweit Hardware vorhanden ist.
6. Optional Linux Voice Assistant v1.1.18 aus dem festgelegten Upstream-Commit
   `b55d36af522c263eeed4e023d6ebf30da47e4710`, als unprivilegierter Panelbenutzer gebaut.
   Mikrofon, Audioausgabe und Netzwerkschnittstelle verwenden die Upstream-Automatiken.
7. HTTPS-Webverwaltung, eigener Zugang, lokaler Systemhelfer und Update-Zeitplan.

Es erfolgt **kein automatischer Neustart während der Einrichtung**. Nach erfolgreichem
Abschluss selbst neu starten, damit Autologin und logind-Regeln wirksam werden.
Zugangsdaten liegen im Home-Verzeichnis des Owners in `PanelPilot-Zugang.txt` (0600).
Beim selbst signierten Zertifikat den Fingerabdruck mit dieser Datei vergleichen.

Ein vorhandener Brave-Browser wird verwendet. Sonst installiert Ubuntu Chromium als
Snap; sein Profil liegt unter `~/snap/chromium/common/panelpilot-profile`. Snap aktualisiert
Chromium nach seinem eigenen Zeitplan, unabhängig von den APT-Einstellungen im Web.

## Nach dem ersten Start

- Im Kioskbrowser einmal bei Home Assistant anmelden und angemeldet bleiben.
- Falls Voice installiert wurde: den Satellite in Home Assistant über die ESPHome-
  Integration (Port 6053) verbinden und die Sprachpipeline auswählen.
- Unter **Fotospeicher** lokale Fotos hochladen oder NFS testen und speichern. Anschließend
  die gewünschte Quelle wählen und unter **Anzeige & Fotos** übernehmen.
- Unter **Updates & Neustart** Zeitzone, Updatezeit und Neustarttag prüfen. Ausgangswerte
  sind täglich 03:00 und Sonntag 05:00, Europe/Vienna, OS-Updates nur am Netzteil.
- Touch, Drehung, Audio und Verhalten ohne Netzverbindung am Zielgerät prüfen.

## Bestehendes Panel aktualisieren

Ein erneuter `setup.py --install`-Aufruf mit demselben Panelkonto erkennt eine vorhandene
PanelPilot-Installation und aktualisiert die Webverwaltung/Systemkomponenten. Er legt
keinen neuen Benutzer an und überschreibt keine Browser-, Audio- oder Autologin-Konfiguration.
`--voice`, `--url` und `--hostname` sind dabei keine nachträglichen Änderungsbefehle.

Für ein vorbereitetes Offline-Wheel-Verzeichnis:

```sh
sudo python3 scripts/upgrade.py --wheelhouse /pfad/zu/wheels
```

Das Upgrade sichert `/opt/panelpilot` und betroffene Konfiguration unter
`/var/backups/panelpilot-upgrade/`. Bei einem abgefangenen Fehler stellt es den vorherigen
Anwendungsstand wieder her. Passwörter, Browserprofil und Fotos werden nicht ersetzt.
Ein erfolgreiches Upgrade meldet aktive Webdienste; nach einem Neustart muss das Gesamtsystem
auf der jeweiligen Hardware separat geprüft werden.

Für die bewusste Übernahme einer schon vorhandenen NFS-Unit kann beim ersten Upgrade
`--mountpoint /mnt/existing_photos` angegeben werden. Zulässig ist genau ein direktes
Unterverzeichnis unter `/mnt` aus Buchstaben, Ziffern und Unterstrichen. Andere Mounts
werden nicht angefasst. Nach Einrichtung ist dieser Zielpfad Administrator-Konfiguration.

## Sicherungen und Grenzen

Die Ersteinrichtung speichert die vorherigen GDM-/logind-Dateien unter
`/var/backups/panelpilot-setup/`. Bereits installierte Pakete, das neue Benutzerkonto und
dessen Daten werden bei einem Fehler nicht automatisch gelöscht. Eine unterbrochene
Ersteinrichtung kann daher lokale Bereinigung erfordern; die Sicherung ist kein Systemabbild.
Kamera, BIOS und Akkuladeschwellen werden nicht verändert.

Weitere Quellen: [Linux Voice Assistant](https://github.com/OHF-Voice/linux-voice-assistant/tree/v1.1.18),
[Chromium auf Ubuntu](https://packages.ubuntu.com/noble/chromium-browser),
[GNOME-Autologin](https://help.gnome.org/system-admin-guide/login-automatic.html).
