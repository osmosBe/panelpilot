# Architektur und Grenzen

```text
Browser im LAN -- HTTPS + Sitzung + CSRF --> PanelPilot (Panelkonto)
                                             |
                            validierte settings.json + systemd-Overrides
                                             |
                      Kioskbrowser / Anzeige / vorhandener Voice-Dienst
```

FastAPI/Uvicorn liefern die lokale Oberfläche und eine JSON-API. Statische Ressourcen
liegen vollständig im Paket. Es werden keine externen Skripte, Schriftarten oder Bilder
im Verwaltungsbrowser geladen. NAS-Fotos bleiben beim Anzeigeprozess; die API liefert
eine Anzahl und einen Ladestatus. Lokale Uploads und deren Vorschaubilder sind dagegen
über authentifizierte Endpunkte abrufbar. Zugangsdaten des Home-Assistant-Browsers werden nicht
gelesen oder exportiert.

Der Controller verwendet feste Dienstrollen und Argumentlisten ohne Shell. Nutzereingaben
werden streng validiert. `%`- und `$`-Expansion in systemd-Argumenten wird maskiert.
Änderungen werden unter einer Prozesssperre ausgeführt. Eine Revision verhindert, dass
eine veraltete Browseransicht neuere Einstellungen überschreibt. Vor einem fehlgeschlagenen
Übernahmeversuch vorhandene Overrides und Einstellungen werden wiederhergestellt.
Systemd-Neustarts sind nicht atomar; bei Problemen meldet die API eine fehlgeschlagene
oder unvollständige Rücknahme. Bei Prozessabbruch/Stromausfall stellt der nächste
Webdienststart den gespeicherten Sollzustand wieder her.

Passwörter liegen als gesalzener scrypt-Hash vor. Sitzungen sind zufällig, serverseitig,
acht Stunden gültig und auf 128 begrenzt; Dienstneustart und Passwortwechsel melden sie
ab. Secure/HttpOnly/SameSite-Cookies, CSRF-Token, Origin-/Host-Prüfung, Login-Drosselung,
begrenzte Anfragegröße, CSP und eingeschränkte Clientnetze schützen die Verwaltung.
Die Regeln sind kein Ersatz für einen externen Security-Audit. Ein kompromittierter
Verwaltungsprozess besitzt die Rechte des Panelkontos und Zugriff auf die unten beschriebenen
Systemaktionen, aber keinen allgemeinen root-Befehlszugang.

## Lokaler Systemhelfer

`panelpilot-admin.service` läuft als root und lauscht ausschließlich auf
`/run/panelpilot-admin/control.sock`. Dateirechte und Linux `SO_PEERCRED` lassen nur den
konfigurierten Panelbenutzer und root zu. Es gibt keinen TCP-Port und keine freie Shell-API.
Erlaubt sind nur Status, Update-Zeitplan übernehmen, NFS testen und NFS übernehmen.
Beide Seiten validieren Eingaben; Zielpfade und Dienstnamen kommen aus root-eigenen Dateien.
Ein kompromittiertes Panelkonto kann somit diese begrenzten Systemaktionen auslösen.

NFS verwendet private IPv4-Adressen, einen festen Mountpunkt und feste Optionen
`ro,nosuid,nodev,noexec`. Die nach DNS-Auflösung geprüfte Adresse wird für den Mount
festgehalten. Bei geänderter NAS-IP muss die NFS-Konfiguration neu übernommen werden.
Ein Testmount wird nach der Prüfung wieder entfernt. Der Webdienst führt den Lesetest
selbst mit seinen vorhandenen Panelbenutzer-Rechten aus; der root-Helfer benötigt keinen
Benutzerwechsel. Ein kurzlebiger Prüf-Token ordnet das Aushängen dem richtigen Test zu.
Nach Ausfall des Webclients räumt der Helfer die Testfreigabe nach 45 Sekunden auf.
Die Helper-Unit darf keinen privaten Mount-Namespace erzeugen, weil Mounts für die
grafische Benutzersitzung sichtbar sein müssen.

Zeitpläne starten ausschließlich Timer, niemals sofort den Reboot-Dienst. Änderungen
sichern Dateien und Aktivierungszustände unter `/var/backups/panelpilot-admin/` und nehmen
sie bei abgefangenen Fehlern zurück. Stromausfall mitten in einer Systemänderung ist keine
atomare Transaktion; bei Inkonsistenzen lokale Sicherung und Journal prüfen.

Uploads werden gestreamt, pro Prozess serialisiert und auf 20 MiB, 32 Megapixel,
500 Bilder und insgesamt 1 GiB begrenzt. Eine separate unprivilegierte GdkPixbuf-Prüfung
decodiert JPEG/PNG/WebP mit Speicher-/CPU-/Zeitlimit. Gespeichert werden zufällige IDs;
Nutzernamen werden nur als Text angezeigt. Kein Upload-Endpunkt schreibt auf NFS.

Getestet werden API, Controller und ein echter GNOME-Panelbetrieb. Andere Desktops,
Bildschirmtreiber, Geräte mit mehreren Backlights und Reverse-Proxy-Installationen sind
nicht validiert. Die anfängliche Hardwarevalidierung sollte deshalb nicht entfallen.
`setup.py` konfiguriert Autologin und Suspend für ein neues Kioskkonto. BIOS, Ladeschwellen
und Kamera bleiben unverändert. Der vollständige frische Setup-Ablauf sowie ARM64-Geräte
brauchen noch einen Abnahmetest; eine unterstützte Architektur ersetzt keinen Hardwaretest.
