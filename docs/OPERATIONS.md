# Betrieb

## Einstellungen und Wirkung

| Einstellung | Wirkung beim Übernehmen |
|---|---|
| Dashboard-URL | Startet nur den Kioskbrowser mit der neuen URL neu |
| Helligkeit, Zeiten, Fotopfad, Ruhemodus | Lädt nur die Anzeige neu |
| Voice aus | Stoppt den Sprachdienst und verhindert weitere Starts, auch nach Login/Neustart |
| Voice ein | Hebt die Startsperre auf und startet den bestehenden Dienst |
| Drehung | Ändert GNOMEs Drehsperre im Panelkonto |
| Passwort | Ersetzt den Passwort-Hash und meldet bestehende Websitzungen ab |
| Update-/Neustartzeitplan | Lokaler Systemhelfer schreibt die festen APT-/systemd-Regeln und aktiviert die Timer |
| NFS speichern | Prüft Lesbarkeit, übernimmt einen lesenden NFS-Automount; Fotoquelle separat auswählen |
| Foto-Upload / Löschen | Verändert nur den verwalteten lokalen Fotoordner, niemals NAS-Dateien |

Ein dunkler Bildschirm verwendet minimale Restbeleuchtung. Auf manchen Convertibles
trennt vollständiges Abschalten den Touchscreen. Deshalb ist dieser Modus kein DPMS-
Standby. „Dashboard bleibt sichtbar“ unterdrückt die Ruheanzeige, erhält aber die
Helligkeitssteuerung beim Wechsel in die Panelsitzung.

Helligkeit wird nur in der aktiven Panelsitzung gesetzt. Ist gerade ein anderer
Linux-Benutzer aktiv, wird sie beim Zurückwechseln übernommen. PipeWire-Mikrofone können
ebenfalls an die aktive Sitzung gebunden sein. Ein eingeschalteter Voice-Dienst kann
dann bis zum Sitzungswechsel warten. PanelPilot konfiguriert keine Audio-Geräterechte.

## Dateien

- `/opt/panelpilot/venv`: installierte Python-Anwendung und festgelegte Abhängigkeiten
- `/etc/panelpilot/runtime.json`: lokale Servicezuordnung, erlaubte Hosts und Startargumente
- `/etc/panelpilot/certificate.pem`, `key.pem`: Zertifikat und privater Schlüssel
- `<config-dir>/settings.json`: für Owner und Panel beschreibbare Einstellungen
- `<config-dir>/anwenden`: nach manueller Bearbeitung eine Übernahme anfordern
- `<panel-home>/.local/state/panelpilot`: Passwort-Hash, Sperrmarkierungen und Übernahmestatus
- `<panel-home>/.config/systemd/user/*/60-panelpilot.conf`: erzeugte Dienst-Overrides

Die Weboberfläche bearbeitet ausschließlich die definierten Settings. Die zusätzliche
Datei `runtime.json` ist eine lokale Administratorkonfiguration und kein Web-Eingabefeld.
Vorhandene Basisdienste bleiben erhalten. Für die verwalteten Optionen haben die
PanelPilot-Overrides Vorrang; alte Editor-Optionen in den Basisdiensten ändern diese
Werte daher nicht. Für manuelle Änderungen die neue `settings.json` verwenden.

Die Bereiche **Fotospeicher** und **Updates & Neustart** benötigen den installierten
Systemhelfer für NFS bzw. [OS-Wartung](MAINTENANCE.md). Uploads benötigen ihn nicht.
Kameraerkennung und freie Shell-Befehle gehören nicht zur Weboberfläche.

Weitere Dateien: `/etc/panelpilot/admin.json` (root-eigene Helper-Zuordnung),
`/var/lib/panelpilot-admin/state.json` (gespeicherte Zeitplan-/NFS-Einstellungen),
`<panel-home>/.local/state/panelpilot/photos` (lokale Uploads).
Systemhelfer prüfen: `systemctl status panelpilot-admin.service` und
`journalctl -u panelpilot-admin.service`.

## Fotos

JPEG, PNG und WebP; bis 2.000 Fotos, 2.000 Ordner, 12 Ebenen und 50.000 Einträge pro Scan.
Bilder über 50 MB oder 100 Megapixel werden übersprungen. Symlink-Schleifen und
Dubletten werden über Geräte-/Inode-Kennungen vermieden. Bei einer nicht erreichbaren
Freigabe bleibt ein bereits geladenes Foto stehen; ohne Foto folgt die dunkle Anzeige.
Eine gemischte lokale/NAS-Galerie kann weiterhin lokale Fotos zeigen. Langsame Zugriffe
laufen außerhalb der Touch-Oberfläche. Der Web-Lesetest hat ein Zeitlimit.

Ein Netzwerkpfad muss im Dateisystem eingebunden und für das Panelkonto lesbar sein.
`smb://`-Adressen sind keine Fotopfade. Unterordner benötigen Durchquerungsrechte.

Uploads unterstützen bis zu 20 MiB und 32 Megapixel pro JPEG/PNG/WebP, insgesamt 1 GiB
und 500 Fotos. Lokale Fotos sind eine eigenständige Quelle; bei einem NAS-Ausfall kann
man im Web darauf umstellen. Es erfolgt kein automatisches Umschalten der Fotoquelle.
HEIC vom Handy muss vor dem Upload in JPEG/PNG umgewandelt werden. Beim Reverse Proxy
eine Uploadgrenze von mindestens 20 MiB und ausreichend lange Zeitlimits einstellen.

## Dienststatus und Passwort zurücksetzen

Innerhalb der Panelsitzung:

```sh
systemctl --user status panelpilot.service
journalctl --user -u panelpilot.service -n 50
/opt/panelpilot/venv/bin/python -m panelpilot reset-password
```

Beim Aufruf aus einem anderen Konto vorher zur Identität des Panelkontos wechseln.
Passwörter werden interaktiv abgefragt und nicht als Kommandozeilenargument übergeben.
Ein Passwortwechsel aktualisiert nicht die alte initiale Klartext-Zugangsdatei; diese
kann der Owner nach erfolgreicher Einrichtung löschen.

## Zertifikat und Hosts

Ein vertrauenswürdiges Zertifikat samt Schlüssel kann ein Administrator unter den
bestehenden Pfaden in `/etc/panelpilot` einsetzen. Besitzer `root`, Gruppe des Panelkontos,
Dateirechte `0640`; danach den Webdienst neu starten. Automatische Zertifikatserneuerung
ist nicht eingebaut. Bei geändertem DNS-Namen oder neuer IP außerdem die SAN-Namen des
Zertifikats und `allowed_hosts` in `runtime.json` aktualisieren. Dafür sind Adminrechte
nötig. Die Webverwaltung ist für das eigene LAN gedacht, nicht als öffentlicher Dienst.
