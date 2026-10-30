#!/usr/bin/env python3
"""ESP32-S3-Knoten des Testaufbaus flashen und einrichten.

  python3 knoten.py ports                      # angeschlossene Boards anzeigen
  python3 knoten.py flashen sender             # Firmware auf einen Knoten
  python3 knoten.py einrichten alle            # WLAN/Laptop-IP/node_id schreiben
  python3 knoten.py mac sender --speichern     # Sender-MAC in testaufbau.json
  python3 knoten.py --trocken einrichten alle  # nur Befehle anzeigen

Ruft esptool und firmware/esp32-csi-node/provision.py auf. Das WLAN-Passwort
wird nur als Datei übergeben (nie auf der Kommandozeile).
"""

import argparse
import glob
import json
import os
import re
import shlex
import subprocess
import sys

from ssense import konfig

HIER = os.path.dirname(os.path.abspath(__file__))
PROVISION = os.path.normpath(os.path.join(HIER, "..", "firmware", "esp32-csi-node", "provision.py"))

# Offsets aus partitions_display.csv (8 MB), siehe firmware/esp32-csi-node/README.md
FLASH_DATEIEN = [
    ("0x0", "bootloader/bootloader.bin"),
    ("0x8000", "partition_table/partition-table.bin"),
    ("0xf000", "ota_data_initial.bin"),
    ("0x20000", "esp32-csi-node.bin"),
]


def serielle_ports():
    by_id = sorted(glob.glob("/dev/serial/by-id/*"))
    roh = sorted(glob.glob("/dev/ttyUSB*") + glob.glob("/dev/ttyACM*"))
    return by_id, roh


def ziel_knoten(k, name):
    if name == "alle":
        # Sender zuerst, damit seine MAC für die Empfänger bekannt sein kann
        return sorted(k["knoten"], key=lambda n: n["rolle"] != "sender")
    return [konfig.knoten_nach_name(k, name)]


def port_pruefen(n, trocken):
    port = n.get("port", "")
    if trocken or os.path.exists(port):
        return port
    by_id, roh = serielle_ports()
    raise konfig.KonfigFehler(
        "%s: Port %r nicht gefunden. Angeschlossen: %s" % (
            n["name"], port, ", ".join(by_id + roh) or "nichts"))


def ausfuehren(cmd, trocken):
    print("$ " + " ".join(shlex.quote(c) for c in cmd), flush=True)
    if trocken:
        return 0
    return subprocess.call(cmd)


def flash_befehl(k, n, port):
    build = konfig.pfad_relativ(k, k["firmware"]["build_verzeichnis"])
    cmd = [sys.executable, "-m", "esptool", "--chip", "esp32s3", "--port", port,
           "--baud", "460800", "write_flash", "--flash_mode", "dio",
           "--flash_size", k["firmware"].get("flash_groesse", "8MB")]
    fehlend = []
    for offset, datei in FLASH_DATEIEN:
        pfad = os.path.join(build, datei)
        if not os.path.isfile(pfad):
            fehlend.append(pfad)
        cmd += [offset, pfad]
    return cmd, fehlend


def provision_befehl(k, n, port):
    netz = k["netz"]
    cmd = [sys.executable, PROVISION, "--port", port, "--chip", "esp32s3", "--reset",
           "--ssid", netz["ssid"],
           "--password-file", konfig.pfad_relativ(k, netz["passwort_datei"]),
           "--target-ip", netz["laptop_ip"],
           "--target-port", str(netz["udp_port"]),
           "--node-id", str(n["node_id"]),
           "--edge-tier", "2"]
    if k["link_modus"] == "sender" and n["rolle"] == "empfaenger":
        if not k.get("sender_mac"):
            raise konfig.KonfigFehler(
                "link_modus 'sender' braucht sender_mac. Erst: python3 knoten.py mac "
                "<sender-name> --speichern")
        cmd += ["--filter-mac", k["sender_mac"].upper()]
    return cmd


def mac_lesen(port):
    out = subprocess.run([sys.executable, "-m", "esptool", "--port", port, "read_mac"],
                         capture_output=True, text=True)
    treffer = re.findall(r"MAC:\s*([0-9a-fA-F:]{17})", out.stdout)
    if out.returncode != 0 or not treffer:
        sys.stderr.write(out.stdout + out.stderr)
        raise RuntimeError("MAC konnte nicht gelesen werden (Port %s)" % port)
    return treffer[0].upper()


def cmd_ports(k, a):
    by_id, roh = serielle_ports()
    print("Stabile Namen (/dev/serial/by-id, empfohlen für testaufbau.json):")
    for p in by_id:
        print("  %s -> %s" % (p, os.path.realpath(p)))
    if not by_id:
        print("  (keine)")
    print("Rohe Ports:", ", ".join(roh) or "(keine)")
    print("\nKonfiguriert:")
    for n in k["knoten"]:
        print("  %-8s %-10s %s %s" % (n["name"], n["rolle"], n.get("port"),
                                      "ok" if os.path.exists(n.get("port", "")) else "FEHLT"))
    return 0


def cmd_mac(k, a):
    n = konfig.knoten_nach_name(k, a.knoten)
    port = port_pruefen(n, False)
    mac = mac_lesen(port)
    print("%s (%s): WLAN-STA-MAC %s" % (n["name"], port, mac))
    if a.speichern:
        if n["rolle"] != "sender":
            print("--speichern gilt nur für den Sender-Knoten.", file=sys.stderr)
            return 2
        with open(k["_pfad"], encoding="utf-8") as f:
            roh = json.load(f)
        roh["sender_mac"] = mac
        with open(k["_pfad"], "w", encoding="utf-8") as f:
            json.dump(roh, f, indent=2, ensure_ascii=False)
            f.write("\n")
        print("sender_mac in %s gespeichert." % k["_pfad"])
    return 0


def cmd_flashen(k, a):
    for n in ziel_knoten(k, a.knoten):
        port = port_pruefen(n, a.trocken)
        cmd, fehlend = flash_befehl(k, n, port)
        if fehlend and not a.trocken:
            print("Firmware fehlt: %s\nErst ./firmware_bauen.sh ausführen." % ", ".join(fehlend),
                  file=sys.stderr)
            return 2
        print("\n== %s (%s) flashen ==" % (n["name"], n["rolle"]))
        if ausfuehren(cmd, a.trocken) != 0:
            print("Flashen von %s fehlgeschlagen. Tipp: BOOT halten, RST drücken, "
                  "BOOT loslassen, dann erneut." % n["name"], file=sys.stderr)
            return 1
    return 0


def cmd_einrichten(k, a):
    pw = konfig.pfad_relativ(k, k["netz"]["passwort_datei"])
    if not a.trocken and not os.path.isfile(pw):
        print("Passwortdatei %s fehlt. Anlegen mit:\n  install -m 600 /dev/null %s && "
              "nano %s" % (pw, pw, pw), file=sys.stderr)
        return 2
    for n in ziel_knoten(k, a.knoten):
        port = port_pruefen(n, a.trocken)
        print("\n== %s (node_id %d, %s) einrichten ==" % (n["name"], n["node_id"], n["rolle"]))
        if ausfuehren(provision_befehl(k, n, port), a.trocken) != 0:
            print("Einrichten von %s fehlgeschlagen." % n["name"], file=sys.stderr)
            return 1
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--konfig", help="Pfad zu testaufbau.json")
    ap.add_argument("--trocken", action="store_true", help="Befehle nur anzeigen")
    sub = ap.add_subparsers(dest="befehl", required=True)
    sub.add_parser("ports", help="serielle Ports anzeigen").set_defaults(fn=cmd_ports)
    m = sub.add_parser("mac", help="WLAN-MAC eines Knotens lesen")
    m.add_argument("knoten")
    m.add_argument("--speichern", action="store_true", help="als sender_mac speichern")
    m.set_defaults(fn=cmd_mac)
    f = sub.add_parser("flashen", help="Firmware flashen")
    f.add_argument("knoten", help="Knotenname oder 'alle'")
    f.set_defaults(fn=cmd_flashen)
    e = sub.add_parser("einrichten", help="WLAN, Laptop-IP, node_id per NVS schreiben")
    e.add_argument("knoten", help="Knotenname oder 'alle'")
    e.set_defaults(fn=cmd_einrichten)
    a = ap.parse_args(argv)
    try:
        k = konfig.laden(a.konfig)
        return a.fn(k, a)
    except (konfig.KonfigFehler, RuntimeError) as e:
        print("Fehler: %s" % e, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
