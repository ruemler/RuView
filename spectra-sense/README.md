# Spectra Sense: erster Testaufbau mit RuView

Dieser Ordner macht aus RuView einen sofort nutzbaren Messplatz für die ersten
Präsenz- und Bewegungstests mit WLAN-CSI (Phase 1–2 des Prototypenplans):

| Teil | Rolle |
|---|---|
| 3–6× ESP32-S3-DevKitC-1 (Modul WROOM-1**U**, externe 2,4-GHz-Antenne) | 1 Sender + bis zu 4 Empfänger, am USB-Hub |
| FRITZ!Box 7490, **offline** | Test-Access-Point, fester Kanal 6, 2,4 GHz, 20 MHz |
| HP 250 G7 mit Ubuntu | Flashen, Aufnahme, Auswertung |
| 2× HLK-LD2410 (optional) | Radar-Referenz für "Person anwesend" |

Inhalt:

| Datei | Zweck |
|---|---|
| `testaufbau.json` | **Die eine Stelle**, an der der Aufbau beschrieben wird (Netz, Knoten, Ports) |
| `firmware_bauen.sh` | Baut die Firmware in Docker, richtige Variante für das DevKitC |
| `knoten.py` | Ports anzeigen, Firmware flashen, WLAN/Laptop-IP/Knoten-ID einrichten |
| `aufnahme.py` | Live-Tabelle aller Knoten, optional Live-Kurve (`--kurve`), Aufnahme in `aufnahmen/*.csi.jsonl` und eine Zeile im Messprotokoll |
| `testaufbau-1board.json` | Variante für den Start mit nur einem Board (FRITZ!Box als Quelle) |
| `auswertung.py` | Kalibrierung (leerer Raum) und Auswertung frei/belegt, CSV, Diagramm |
| `simulator.py` | Synthetische Knoten zum Üben ohne Hardware |
| `tests/` | Selbsttests (`python3 -m unittest discover -s tests`) |

Ehrliche Einordnung: Das Auswerteverfahren ist ein einfacher, nachvollziehbarer
Schwellwert auf der Amplitudenschwankung (Details in `ssense/merkmale.py`). Es
erkennt Bewegung gut und ruhiges Sitzen nur eingeschränkt, deshalb die
Haltezeit. Es zählt keine Personen und ortet nicht. Alle Zahlen, die die
Werkzeuge ausgeben, gelten nur für deinen Raum und deinen Aufbau.

---

## 1. Aufbau im Raum

```
          Fenster
   empf-1 ─────────────── empf-2        Empfänger in den Ecken,
     │                       │          1–1,5 m hoch, Antenne senkrecht,
     │        sender         │          ≥ 30 cm Abstand zu Metall
     │      (Raummitte)      │
     │                       │          FRITZ!Box im Raum an einer Seite,
   empf-3 ─────────────── empf-4        nicht hinter Metall
           Tür     [FRITZ!Box]
                       │ LAN-Kabel
                    Laptop (Ubuntu) ── aktiver USB-Hub ── alle ESP32 (Strom + Flashen)
```

* **Antenne immer zuerst anschrauben**, dann Strom. Ein WROOM-1U ohne Antenne
  kann beim Senden die HF-Stufe beschädigen.
* Aktiven USB-Hub mit eigenem Netzteil nehmen (5 Boards + 2 Radare).
* Am DevKitC den USB-Anschluss **"UART"** verwenden (nicht "USB"). Darüber
  laufen Flashen und Log-Ausgabe.
* Nach dem Einrichten brauchen die Knoten nur noch Strom; die Daten kommen per
  WLAN. Die Boards können also auch an USB-Netzteilen im Raum hängen.

## 2. FRITZ!Box 7490 als Test-AP (offline)

1. Kein Kabel in den DSL/WAN-Anschluss. Laptop per **LAN-Kabel** an LAN 1.
2. `http://192.168.178.1` öffnen (ggf. vorher auf Werkseinstellungen setzen).
3. **WLAN → Funknetz**: nur das **2,4-GHz**-Funknetz aktiv, 5 GHz aus.
   Name z. B. `SpectraTest` (muss zu `netz.ssid` in `testaufbau.json` passen).
4. **WLAN → Funkkanal → Funkkanal-Einstellungen anpassen**:
   * Autokanal **aus**, Kanal **6** fest.
   * WLAN-Standard **802.11 n+g** (ohne b: 802.11b-Pakete liefern kein CSI).
   * Falls angeboten: 40-MHz-Kanalbandbreite deaktivieren (20 MHz).
5. **WLAN → Sicherheit**: WPA2 (CCMP), Passwort setzen. WPS aus.
6. Gastzugang, Mesh-Repeater-Funktion, WLAN-Zeitschaltung aus.
7. **Heimnetz → Netzwerk**: Laptop suchen → bearbeiten → "Diesem Netzwerkgerät
   immer die gleiche IPv4-Adresse zuweisen". Diese Adresse (`ip -4 addr` am
   Laptop) in `testaufbau.json` unter `netz.laptop_ip` eintragen.

## 3. Laptop (Ubuntu) vorbereiten

```bash
sudo apt update
sudo apt install -y git docker.io python3-venv python3-matplotlib
sudo usermod -aG dialout,docker $USER        # danach ab- und wieder anmelden

git clone https://github.com/ruemler/RuView.git
cd RuView && git checkout claude/project-thread-lzl3tn
cd spectra-sense

python3 -m venv --system-site-packages ~/spectra-venv
source ~/spectra-venv/bin/activate          # in jedem neuen Terminal
pip install "esptool>=5" esp-idf-nvs-partition-gen pyserial

# WLAN-Passwort der FRITZ!Box in eine nur für dich lesbare Datei
install -m 600 /dev/null ~/.spectra-wlan-pass && nano ~/.spectra-wlan-pass

# Falls die Ubuntu-Firewall aktiv ist: UDP 5005 aus dem Testnetz erlauben
sudo ufw allow from 192.168.178.0/24 to any port 5005 proto udp

# Selbsttest der Werkzeuge (ohne Hardware, ~15 s)
python3 -m unittest discover -s tests
```

## 4. Firmware bauen

```bash
./firmware_bauen.sh
```

Lädt beim ersten Mal das ESP-IDF-Image (~3 GB) und baut
`firmware/esp32-csi-node/build/`. Das Skript nutzt die DevKitC-Variante ohne
Display. Mit der Standardvariante liefert das DevKitC 0 CSI-Pakete/s.

## 5. `testaufbau.json` anpassen

Alle Boards einstecken, dann:

```bash
python3 knoten.py ports
```

Die Pfade unter `/dev/serial/by-id/...` sind stabil (bleiben gleich, egal in
welcher Reihenfolge die Boards eingesteckt werden). Trage sie bei `port` ein.
Boards mit Aufkleber beschriften (sender, empf-1 ...). Felder:

* `netz.laptop_ip`: feste IP des Laptops aus Schritt 2.7.
* `link_modus`: zuerst `"ap"` lassen (siehe Abschnitt 9).
* `knoten[].node_id`: eindeutig 1–255; `ld2410: true` an Knoten mit Radar.
* `knoten[].position`: Freitext, landet in jeder Aufnahme.
* Weniger Boards? Einträge einfach löschen.

## 6. Flashen und Einrichten

```bash
python3 knoten.py --trocken flashen alle     # zeigt nur, was passieren würde
python3 knoten.py flashen alle
python3 knoten.py einrichten alle            # SSID, Passwortdatei, Laptop-IP, node_id
```

Klappt das Flashen nicht: BOOT-Taste halten, RST kurz drücken, BOOT loslassen,
nochmal. Einzelne Knoten gehen mit Namen statt `alle`, z. B.
`python3 knoten.py einrichten empf-2`.

**Boards nacheinander am selben USB-Anschluss:** Statt alle gleichzeitig am
Hub geht auch eins nach dem anderen. Dann mit `--port` den gerade belegten
Port angeben (`python3 knoten.py ports` zeigt ihn) und Board für Board
flashen und einrichten, danach abstecken und beschriften:

```bash
python3 knoten.py --port /dev/ttyACM0 flashen sender
python3 knoten.py --port /dev/ttyACM0 einrichten sender
# nächstes Board anstecken
python3 knoten.py --port /dev/ttyACM0 flashen empf-1
python3 knoten.py --port /dev/ttyACM0 einrichten empf-1
```

Nach dem Einrichten brauchen die Boards nur noch Strom; die Daten kommen per
WLAN. Einrichten setzt die fertige FRITZ!Box (Abschnitt 2), die Passwortdatei
und `netz.laptop_ip` voraus; flashen geht schon vorher.

## 7. Erster Funktionstest

Raum verlassen und alle Knoten **im leeren Raum** einschalten (die Firmware
lernt in den ersten 60 s den Ruhezustand für ihr eigenes Präsenzbit). Dann:

```bash
python3 aufnahme.py --nur-live
```

Erwartung je Knoten: `Pkt/s` mindestens 20 (typisch 30–50), `Kan` = 6,
RSSI etwa −30 bis −70. Gehe durch den Raum: die Spalte `Index` sollte deutlich
steigen. `LD2410` zeigt `ja/nein` an Knoten mit Radar. Bei `KEINE DATEN` siehe
Fehlersuche.

## 8. Messreihe und Auswertung

Labels: `leer`, `gehen`, `sitzen`, `stehen`, `tuer` (betreten/verlassen),
`gemischt`, `test`. `--vorlauf` gibt dir Zeit, den Raum zu verlassen oder
dich hinzusetzen.

```bash
python3 aufnahme.py --label leer   --dauer 180 --vorlauf 20   # Kalibrierung
python3 aufnahme.py --label leer   --dauer 120 --vorlauf 20   # Prüfung (andere Datei!)
python3 aufnahme.py --label gehen  --dauer 60  --vorlauf 5  --notiz "normal, Kreis"
python3 aufnahme.py --label sitzen --dauer 180 --vorlauf 10 --notiz "Tisch, Laptoparbeit"
python3 aufnahme.py --label stehen --dauer 60  --vorlauf 10
python3 aufnahme.py --label leer   --dauer 120 --vorlauf 20   # Drift am Ende

python3 auswertung.py kalibrieren aufnahmen/<erste>_leer.csi.jsonl -o kalibrierung.json
python3 auswertung.py auswerten aufnahmen/*.csi.jsonl -k kalibrierung.json \
    --csv ergebnis.csv --diagramm ergebnis.png
```

Die Tabelle zeigt pro Aufnahme, wie viele Sekunden "Raum belegt" waren und wie
gut das zum Label, zum LD2410 und zum Firmware-Präsenzbit passt. Die
Kalibrierdatei wird markiert, weil sie gegen sich selbst nichts aussagt.
Stellschrauben:

* `--halte 30`: so lange bleibt "belegt" nach der letzten Bewegung stehen.
  Länger = ruhiges Sitzen wird besser gehalten, Raum wird später frei.
* `kalibrieren --faktor 1.3`: höhere Schwelle, weniger Fehlalarme im leeren Raum.
* `--min-knoten 2`: zwei Knoten müssen Bewegung sehen.

Live mit Kalibrierung: `python3 aufnahme.py --nur-live --kalibrierung kalibrierung.json`
zeigt pro Knoten `frei / BEWEGUNG / belegt` und den Raumstatus.

**Live-Kurve:** `--kurve` öffnet zusätzlich ein Fenster mit Bewegungsindex
und RSSI je Knoten über die letzten 60 s (gestrichelt: Schwelle aus der
Kalibrierung). Braucht `python3-matplotlib`.

**Messprotokoll:** Jede Aufnahme hängt eine Zeile an
`aufnahmen/messprotokoll.csv` an (Datei, Startzeit, Dauer, Raum, Label,
Personen, Türen, Knotenpositionen, Paketzahl, Notiz). Die Angaben kommen aus
`--raum`, `--personen` (bei `leer` automatisch 0), `--tueren` und `--notiz`:

```bash
python3 aufnahme.py --label gehen --dauer 60 --vorlauf 5 \
    --raum buero --personen 1 --tueren zu --notiz "Kreis um den Tisch" --kurve
```

Das Protokoll liegt wie die Aufnahmen nur lokal (gitignore).

Speicherbedarf: etwa 280 MB pro Stunde bei 5 Knoten.

## 8a. Mit nur einem Board starten

Für die ersten Messungen reicht ein einzelnes ESP32-S3 als Empfänger; die
FRITZ!Box ist dann die Quelle (`link_modus: "ap"`). Statt `testaufbau.json`
die Datei `testaufbau-1board.json` verwenden. Bei `knoten.py` steht
`--konfig` **vor** dem Unterbefehl, bei `aufnahme.py` und `simulator.py`
irgendwo:

```bash
python3 knoten.py --konfig testaufbau-1board.json ports
python3 knoten.py --konfig testaufbau-1board.json flashen empf-1
python3 knoten.py --konfig testaufbau-1board.json einrichten empf-1
python3 aufnahme.py --konfig testaufbau-1board.json --nur-live --kurve
```
 Boards mit eingebautem USB-Seriell-
Wandler (z. B. Waveshare ESP32-S3 N16R8) erscheinen meist als `/dev/ttyACM0`;
den Port mit `python3 knoten.py ports` prüfen und in der Datei anpassen.
Die PCB-Antenne reicht für Phase 1. Mit einem Board gibt es nur eine
Funkstrecke: Bewegung wird erkannt, eine Ortung im Raum nicht.

## 8b. Nur Pakete der FRITZ!Box auswerten (`ap_mac`)

Im Modus `ap` hört jedes Board ohne Filter alle WLAN-Pakete im Raum mit, auch
die der anderen Boards (die selbst ~50 Pakete/s senden). Jedes Paket kommt
dann über einen anderen Funkweg, der Bewegungsindex ist schon im leeren Raum
hoch und Bewegung geht darin unter. Abhilfe: die 2,4-GHz-BSSID der FRITZ!Box als
Filter eintragen und alle Boards neu einrichten (kein neues Flashen nötig).

```bash
nmcli -f SSID,BSSID,CHAN dev wifi list | grep SpectraTest   # BSSID ablesen
# in testaufbau.json: "ap_mac": "AA:BB:CC:DD:EE:FF"
python3 knoten.py --port /dev/ttyACM0 einrichten sender     # je Board, danach RST
```

Danach neu kalibrieren: Eine Kalibrierung ohne Filter passt nicht mehr.

## 9. Sender-Modus (fester Sender statt FRITZ!Box als Quelle)

Im Modus `ap` misst jeder Empfänger die Funkstrecke FRITZ!Box → Empfänger. Im
Modus `sender` werten die Empfänger nur Pakete des Sender-ESP32 aus, damit sind
die Funkstrecken Sender → Empfänger klar definiert:

```bash
python3 knoten.py mac sender --speichern      # trägt sender_mac ein
# in testaufbau.json: "link_modus": "sender"
python3 knoten.py einrichten alle
python3 aufnahme.py --nur-live
```

Der Sender erzeugt dabei automatisch ~50 kleine Pakete pro Sekunde. Dieser Modus
braucht die Firmware aus diesem Branch: Hier wurde korrigiert, dass der
MAC-Filter erst nach der 50-Hz-Drosselung griff und so die meisten
Sender-Pakete verwarf. Ist die Paketrate auf den Empfängern dauerhaft unter 20/s,
zurück auf `"ap"` stellen und neu einrichten. Ein Moduswechsel braucht immer eine
neue Kalibrierung.

## 10. LD2410 als Referenz anschließen

| LD2410 | DevKitC-1 |
|---|---|
| VCC | 5V |
| GND | GND |
| TX | GPIO **18** (ESP-RX) |
| RX | GPIO **17** (ESP-TX) |

Das Radar muss **vor dem Einschalten** angeschlossen sein: die Firmware sucht es
nur beim Start (256000 Baud, Werkseinstellung). Im seriellen Log steht dann eine
Zeile mit `LD2410`. Danach sendet der Knoten erweiterte Pakete mit dem
Radar-Präsenzbit, das `aufnahme.py` und `auswertung.py` als Referenz nutzen.
Radar so ausrichten, dass es den Raum, nicht den Flur sieht.

## 11. Ohne Hardware üben

```bash
python3 simulator.py --szenario leer:60,gehen:30,sitzen:60,leer:30 &
python3 aufnahme.py --label gemischt --dauer 170
```

Die Daten sind synthetisch und sagen nichts über echte Leistung aus.

## 12. Fehlersuche

| Symptom | Prüfen |
|---|---|
| `KEINE DATEN` bei einem Knoten | Serielles Log: `python3 -m serial.tools.miniterm <port> 115200`. Steht dort keine WLAN-Verbindung: SSID/Passwortdatei. Steht `CSI cb #...`, aber nichts kommt an: `netz.laptop_ip`, Firewall (`sudo ufw status`). |
| 0 Pakete auf allen Knoten | Firmware ohne DevKitC-Variante gebaut? `./firmware_bauen.sh` prüft das. |
| Wenig Pakete (< 20/s) | Zusätzlichen Verkehr erzeugen: Knoten-IP aus FRITZ!Box-Liste, dann `python3 ../scripts/csi-udp-poke.py <knoten-ip> --hz 50 --seconds 60`. 802.11b in der FRITZ!Box aus? |
| Warnung "meldet Kanal X statt 6" | FRITZ!Box-Autokanal ist noch an. |
| Flashen: "Permission denied" | Gruppe `dialout` (neu anmelden). |
| Board erscheint nicht als Port | Anderes USB-Kabel (Ladekabel ohne Datenleitungen), UART-Buchse benutzen; `sudo dmesg -w` beim Einstecken. Bei CH340/CH343-Konflikten: `sudo apt remove brltty`. |
| Leerer Raum wird "belegt" | Ventilator/Vorhang/Heizung in Bewegung? `--faktor 1.3` beim Kalibrieren, Kalibrierung zur gleichen Tageszeit wiederholen. |
| Ruhiges Sitzen wird "frei" | Erwartbar bei reiner Amplitudenschwankung; `--halte` erhöhen, Empfänger näher an Arbeitsplätze. |

## 13. Datenschutz

CSI-Aufnahmen verraten Anwesenheit, Bewegung und Atmung und sind damit
personenbezogen. Sie bleiben in `aufnahmen/` auf dem Laptop; `*.csi.jsonl`,
`aufnahmen/`, Kalibrierungen und Ergebnisse sind von Git ausgeschlossen. Nur mit
Einverständnis der Personen im Raum aufnehmen.
