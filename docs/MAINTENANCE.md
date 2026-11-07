# Automatische OS-Updates und wöchentlicher Neustart

Ab PanelPilot 0.2 lassen sich Updatezeit, Netzbetrieb-Regel, Neustarttag, Neustartzeit und
Zeitzone unter **Updates & Neustart** bearbeiten. Der gemeinsame Installer richtet den
lokalen Systemhelfer dafür ein. Änderungen sichern die vorherigen Dateien unter
`/var/backups/panelpilot-admin/` und lösen keinen sofortigen Neustart aus.
Chromium-Snap-Updates verwenden weiterhin den eigenen Snap-Zeitplan.

Die folgende Anleitung beschreibt das zusätzlich nutzbare eigenständige Wartungsprofil
ohne Web-Systemhelfer. Nach Änderungen über den Web-Systemhelfer dessen Sicherungen und
gespeicherten Zustand ebenfalls berücksichtigen; nicht nur das alte Profil zurückspielen.

Optionales Wartungsprofil für ein dauerhaft betriebenes Ubuntu-/Zorin-Panel. Es wird
separat mit Administratorrechten eingerichtet; die Webverwaltung erhält dafür keine
zusätzlichen Rechte. Voraussetzung: `unattended-upgrades` ist installiert.
Für die Einrichtung ein Notebook ans Netzteil anschließen, damit die vollständige
Simulation unter den regulären Stromversorgungsregeln durchlaufen kann.

```sh
sudo python3 scripts/maintenance.py install --timezone Europe/Vienna
```

Die Installation sichert die bisherigen Dateien und Timerzustände, prüft die Konfiguration
mit `unattended-upgrade --dry-run --debug` und aktiviert danach ausschließlich die Timer.
Sie installiert während dieser Prüfung keine Paketupdates und fordert keinen Neustart an.

## Zeitplan

| Vorgang | Zeit in der gewählten Zeitzone |
|---|---|
| Paketlisten aktualisieren | Täglich 02:30, bis zu fünf Minuten Zufallsverzögerung |
| Freigegebene Paketupdates installieren | Täglich 03:00, bis zu fünf Minuten Zufallsverzögerung |
| Gerät neu starten | Jeden Sonntag 05:00, auch ohne Neustartanforderung durch Updates |

Der Neustarttimer verwendet `AccuracySec=1s`, keine Zufallsverzögerung und `Persistent=false`.
Ein ausgeschaltetes Gerät holt einen verpassten Neustart daher nicht beim Einschalten nach.
Die Update-Timer bleiben persistent und dürfen verpasste Update-Läufe nachholen.
Updates erfolgen gemäß Distributionsvorgaben am Netzteil und nicht über als getaktet
markierte Verbindungen. Ein ausgeschaltetes Gerät wird nicht automatisch eingeschaltet.

## Paketumfang

Das Profil erlaubt Ubuntu-Sicherheitsupdates und reguläre stabile Updates der aktuellen
Ubuntu-Basis, gegebenenfalls vorhandene ESM-Sicherheitsquellen, die offiziellen
Zorin-PPAs `stable`, `patches`, `apps`, `drivers` sowie den stabilen Brave-Browser.
Es aktiviert keine neuen Paketquellen und erlaubt weder `proposed` noch `backports`.
APT-Signaturprüfungen und Regeln für problematische/abhängige Pakete bleiben aktiv.
Ein Wechsel auf eine andere Ubuntu-/Zorin-Hauptversion wird damit nicht ausgelöst.

PanelPilot und der separat in einer Python-Umgebung installierte Linux Voice Assistant
werden dadurch **nicht** aktualisiert. Dafür bleiben kontrollierte Anwendungsupdates
mit Versionierung und Rücknahme nötig. Zusätzliche Drittanbieter-Repositories sowie
Flatpak-/Snap-Apps werden von diesem APT-Profil nicht verwaltet.

## Schutz laufender Installationen

Vor dem Neustart prüft der Helfer die tatsächlichen POSIX-Sperren von APT, dpkg und
unattended-upgrades. Die Existenz einer Lockdatei allein ist kein Hindernis.
Bei laufenden Paketoperationen prüft er alle 30 Sekunden erneut, höchstens eine Stunde.
Bleibt eine Operation dauerhaft blockiert, wird der Neustart für diese Woche ausgelassen
und der Grund im Journal protokolliert. Lockdateien werden niemals gelöscht und es gibt
keinen erzwungenen Neustart. Der reguläre Shutdown-Schutz von unattended-upgrades bleibt
aktiv. Die Neustartzeit kann sich deshalb bei laufenden Updates verschieben.

## Kontrolle

```sh
systemctl list-timers apt-daily.timer apt-daily-upgrade.timer panelpilot-weekly-reboot.timer
systemctl cat panelpilot-weekly-reboot.timer
sudo python3 /usr/local/lib/panelpilot/weekly-reboot.py --check-only
journalctl -u panelpilot-weekly-reboot.service
sudo tail /var/log/unattended-upgrades/unattended-upgrades.log
```

`--check-only` führt ausschließlich die Lockprüfung aus. Den Neustartdienst zum Testen
nicht manuell starten: ein normaler Start dieses Dienstes fordert tatsächlich den
Neustart an. Die automatisierten Tests verwenden dafür ausdrücklich einen simulierten
Befehlsaufruf.

## Rücknahme

Der Installer nennt seinen Sicherungsordner unter `/var/backups/panelpilot-maintenance/`.
Mit dem genauen Ordnerpfad lässt sich der vorige Zustand wiederherstellen:

```sh
sudo python3 scripts/maintenance.py restore /var/backups/panelpilot-maintenance/ZEITSTEMPEL
```

Dabei werden der neue Neustarttimer und ein eventuell wartender Neustarthelfer gestoppt,
die vorherigen Dateien und Aktivierungszustände wiederhergestellt und die ursprünglichen
APT-Timer neu geladen. Eine Deinstallation der Webverwaltung allein entfernt dieses
unabhängige OS-Wartungsprofil nicht.

Quellen: [Ubuntu – automatische Updates](https://documentation.ubuntu.com/server/how-to/software/automatic-updates/),
[systemd.timer](https://www.freedesktop.org/software/systemd/man/255/systemd.timer.html).
