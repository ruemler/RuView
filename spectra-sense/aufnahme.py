#!/usr/bin/env python3
"""Live-Anzeige und Aufnahme der ESP32-CSI-Ströme auf dem Laptop.

Beispiele:
  # Nur schauen, ob alle Knoten senden (nichts wird gespeichert)
  python3 aufnahme.py --nur-live

  # 2 Minuten leerer Raum, 15 s Vorlauf zum Verlassen des Raums
  python3 aufnahme.py --label leer --dauer 120 --vorlauf 15

  # Mit Kalibrierung: Live-Status frei / Bewegung / belegt
  python3 aufnahme.py --label gehen --dauer 60 --kalibrierung kalibrierung.json
"""

import argparse
import ipaddress
import json
import os
import socket
import sys
import time
from collections import defaultdict

from ssense import aufzeichnung, konfig, merkmale, protokoll

LABELS = ("leer", "gehen", "sitzen", "stehen", "tuer", "gemischt", "test")


def lade_schwellen(pfad):
    with open(pfad, encoding="utf-8") as f:
        daten = json.load(f)
    return {int(k): float(v) for k, v in daten["schwellen"].items()}


class Live:
    """Zustand je Knoten für die Live-Tabelle."""

    def __init__(self, k, schwellen, fenster_s, halte_s, min_knoten):
        self.k = k
        self.namen = konfig.knoten_nach_id(k)
        self.windower = merkmale.Windower(fenster_s)
        self.detector = merkmale.Detector(schwellen, halte_s, min_knoten)
        self.zaehler = defaultdict(int)       # Pakete im aktuellen Sekundenintervall
        self.gesamt = defaultdict(int)
        self.rssi = {}
        self.kanal = {}
        self.index = {}
        self.bewegung = {}
        self.vitals = {}
        self.unbekannt = set()
        self.warnungen = set()

    def paket(self, t, pkt):
        node = pkt["node"]
        if node not in self.namen:
            self.unbekannt.add(node)
        if pkt["typ"] == "csi":
            self.zaehler[node] += 1
            self.gesamt[node] += 1
            self.rssi[node] = pkt["rssi"]
            self.kanal[node] = pkt["kanal"]
            if pkt["kanal"] and pkt["kanal"] != self.k["netz"]["kanal"]:
                self.warnungen.add("Knoten %d meldet Kanal %d statt %d"
                                   % (node, pkt["kanal"], self.k["netz"]["kanal"]))
            fertig = self.windower.add(node, t, protokoll.amplitudes(pkt["iq"], pkt["n_sub"]))
            if fertig:
                n, t_end, idx, _ = fertig
                self.index[n] = idx
                self.bewegung[n] = self.detector.update(n, t_end, idx)
        else:
            self.vitals[node] = pkt

    def tabelle(self, t, dt):
        z = []
        z.append("%-8s %-10s %6s %5s %4s %8s %8s %-9s %-6s %-6s"
                 % ("Knoten", "Rolle", "Pkt/s", "RSSI", "Kan", "Index", "Schwelle",
                    "Status", "FW", "LD2410"))
        for nid, n in sorted(self.namen.items()):
            pps = self.zaehler.get(nid, 0) / dt if dt > 0 else 0.0
            idx = self.index.get(nid)
            thr = self.detector.schwellen.get(nid)
            if self.gesamt.get(nid, 0) == 0:
                status = "KEINE DATEN"
            elif thr is None:
                status = "unkalibr."
            elif self.bewegung.get(nid):
                status = "BEWEGUNG"
            elif self.detector.node_occupied(nid, t):
                status = "belegt"
            else:
                status = "frei"
            v = self.vitals.get(nid, {})
            fw = "-" if not v else ("ja" if v.get("fw_praesenz") else "nein")
            radar = "-"
            if "radar_praesenz" in v:
                radar = "ja" if v["radar_praesenz"] else "nein"
            z.append("%-8s %-10s %6.1f %5s %4s %8s %8s %-9s %-6s %-6s" % (
                n["name"][:8], n["rolle"], pps, self.rssi.get(nid, "-"),
                self.kanal.get(nid, "-"),
                "-" if idx is None else "%.4f" % idx,
                "-" if thr is None else "%.4f" % thr,
                status, fw, radar))
        if self.detector.schwellen:
            z.append("")
            z.append("Raum: %s" % ("BELEGT" if self.detector.room_occupied(t) else "frei"))
        if self.unbekannt:
            z.append("Hinweis: Pakete von nicht konfigurierten node_ids %s"
                     % sorted(self.unbekannt))
        for w in sorted(self.warnungen):
            z.append("Warnung: " + w)
        self.zaehler.clear()
        return "\n".join(z)


def quelle_erlaubt(addr, netz):
    ip = ipaddress.ip_address(addr)
    return ip.is_loopback or ip in netz


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--konfig", help="Pfad zu testaufbau.json")
    ap.add_argument("--label", choices=LABELS,
                    help="Was passiert im Raum während der Aufnahme")
    ap.add_argument("--notiz", default="", help="Freitext, z. B. 'Person geht Tür->Fenster'")
    ap.add_argument("--dauer", type=float, default=120.0, help="Sekunden (Standard 120)")
    ap.add_argument("--vorlauf", type=float, default=0.0,
                    help="Wartezeit vor Aufnahmebeginn in s (Raum verlassen o. ä.)")
    ap.add_argument("--ausgabe", default="aufnahmen", help="Zielordner")
    ap.add_argument("--kalibrierung", help="kalibrierung.json aus auswertung.py")
    ap.add_argument("--nur-live", action="store_true", help="nichts speichern")
    ap.add_argument("--bind", default="0.0.0.0", help="lokale Adresse (Standard alle)")
    ap.add_argument("--port", type=int, help="UDP-Port (Standard aus Konfig)")
    ap.add_argument("--ohne-anzeige", action="store_true", help="keine Live-Tabelle")
    a = ap.parse_args(argv)

    if not a.nur_live and not a.label:
        ap.error("--label ist für Aufnahmen nötig (oder --nur-live verwenden)")

    k = konfig.laden(a.konfig)
    netz = ipaddress.ip_network(k["netz"].get("erlaubtes_netz", "192.168.178.0/24"),
                                strict=False)
    port = a.port or int(k["netz"]["udp_port"])
    ausw = k.get("auswertung", {})
    schwellen = lade_schwellen(a.kalibrierung) if a.kalibrierung else {}
    live = Live(k, schwellen, ausw.get("fenster_s", 1.0), ausw.get("halte_s", 30.0),
                ausw.get("min_knoten", 1))

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.bind((a.bind, port))
    except OSError as e:
        print("UDP-Port %d nicht verfügbar: %s (läuft schon eine Aufnahme oder der "
              "sensing-server?)" % (port, e), file=sys.stderr)
        return 2
    sock.settimeout(0.2)

    if a.vorlauf > 0:
        ende_vorlauf = time.time() + a.vorlauf
        while time.time() < ende_vorlauf:
            print("\rAufnahme startet in %3.0f s ..." % (ende_vorlauf - time.time()),
                  end="", flush=True)
            time.sleep(0.5)
        print()
        # Pakete aus dem Vorlauf verwerfen
        sock.setblocking(False)
        try:
            while True:
                sock.recvfrom(4096)
        except (BlockingIOError, OSError):
            pass
        sock.settimeout(0.2)

    schreiber = None
    if not a.nur_live:
        os.makedirs(a.ausgabe, exist_ok=True)
        name = "%s_%s.csi.jsonl" % (time.strftime("%Y%m%d-%H%M%S"), a.label)
        pfad = os.path.join(a.ausgabe, name)
        schreiber = aufzeichnung.Schreiber(pfad, {
            "label": a.label, "notiz": a.notiz, "dauer_s": a.dauer,
            "link_modus": k["link_modus"], "kanal": k["netz"]["kanal"],
            "knoten": [{x: n.get(x) for x in ("name", "node_id", "rolle", "position", "ld2410")}
                       for n in k["knoten"]],
        })
        print("Aufnahme '%s' -> %s" % (a.label, pfad))

    start = time.time()
    letzte_anzeige = start
    abgewiesen = 0
    try:
        while True:
            jetzt = time.time()
            if not a.nur_live and jetzt - start >= a.dauer:
                break
            try:
                daten, (addr, _) = sock.recvfrom(4096)
            except socket.timeout:
                daten = None
            jetzt = time.time()
            if daten is not None:
                if not quelle_erlaubt(addr, netz):
                    abgewiesen += 1
                else:
                    pkt = protokoll.parse(daten)
                    if pkt is not None:
                        live.paket(jetzt, pkt)
                        if schreiber:
                            schreiber.paket(jetzt, pkt)
            if not a.ohne_anzeige and jetzt - letzte_anzeige >= 1.0:
                kopf = "Spectra Sense Testaufbau | Modus %s | Kanal %d | %s" % (
                    k["link_modus"], k["netz"]["kanal"],
                    "LIVE (Strg+C beendet)" if a.nur_live else
                    "Aufnahme '%s' %3.0f/%3.0f s" % (a.label, jetzt - start, a.dauer))
                if abgewiesen:
                    kopf += " | %d Pakete aus fremdem Netz verworfen" % abgewiesen
                print("\033[2J\033[H" + kopf + "\n\n" + live.tabelle(jetzt, jetzt - letzte_anzeige),
                      flush=True)
                letzte_anzeige = jetzt
    except KeyboardInterrupt:
        print("\nabgebrochen")
    finally:
        sock.close()
        if schreiber:
            schreiber.schliessen()

    dauer = max(time.time() - start, 1e-6)
    print("\nZusammenfassung (%.0f s):" % dauer)
    for nid, n in sorted(live.namen.items()):
        anz = live.gesamt.get(nid, 0)
        hinweis = ""
        if anz == 0:
            hinweis = "  <- keine CSI-Daten! Siehe Anleitung, Abschnitt Fehlersuche"
        elif anz / dauer < 10:
            hinweis = "  <- wenig Daten, siehe Fehlersuche (csi-udp-poke)"
        print("  %-8s node %3d: %6d CSI-Pakete (%.1f/s)%s" % (n["name"], nid, anz, anz / dauer, hinweis))
    if schreiber:
        print("gespeichert: %s (%d Zeilen)" % (schreiber.pfad, schreiber.zeilen))
    return 0


if __name__ == "__main__":
    sys.exit(main())
