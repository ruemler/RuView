#!/usr/bin/env python3
"""Kalibrierung und Auswertung von Aufnahmen (*.csi.jsonl).

  # 1) Schwellen aus einer Leerraum-Aufnahme ableiten
  python3 auswertung.py kalibrieren aufnahmen/*_leer.csi.jsonl -o kalibrierung.json

  # 2) Aufnahmen auswerten (Tabelle, optional CSV und Diagramm)
  python3 auswertung.py auswerten aufnahmen/*.csi.jsonl -k kalibrierung.json \\
      --csv ergebnis.csv --diagramm ergebnis.png

Ergebnisse sind eigene Messwerte für genau diesen Raum und Aufbau. Sie sind
keine allgemeine Genauigkeitsaussage; als MEASURED gelten sie nur zusammen mit
den Aufnahmedateien und diesem Befehl als Reproduktionsweg.
"""

import argparse
import csv
import json
import os
import sys
import time
from collections import defaultdict

from ssense import aufzeichnung, merkmale

REFERENZ_MAX_ALTER_S = 3.0


def fenster_indizes(pfad, fenster_s):
    """Alle Fenster-Indizes einer Aufnahme: {node: [(t, index), ...]}."""
    meta, pakete = aufzeichnung.lesen(pfad)
    w = merkmale.Windower(fenster_s)
    out = defaultdict(list)
    for p in pakete:
        if p.get("typ") != "csi":
            continue
        r = w.add(p["node"], p["t"], aufzeichnung.amplituden(p))
        if r and r[2] is not None:
            out[r[0]].append((r[1], r[2]))
    return meta, out


def kalibrieren(a):
    alle = defaultdict(list)
    for pfad in a.dateien:
        meta, idx = fenster_indizes(pfad, a.fenster)
        if meta.get("label") != "leer" and not a.erzwingen:
            print("%s hat Label '%s', nicht 'leer'. Kalibrierung braucht einen leeren "
                  "Raum (--erzwingen übergeht das)." % (pfad, meta.get("label")), file=sys.stderr)
            return 2
        for node, werte in idx.items():
            alle[node].extend(v for _, v in werte)
    if not alle:
        print("Keine CSI-Daten in den Dateien.", file=sys.stderr)
        return 2
    schwellen, info = {}, {}
    for node, werte in sorted(alle.items()):
        try:
            thr = merkmale.threshold_from_empty(werte) * a.faktor
        except ValueError as e:
            print("Knoten %d übersprungen: %s" % (node, e), file=sys.stderr)
            continue
        schwellen[str(node)] = round(thr, 6)
        info[str(node)] = {"fenster": len(werte),
                           "median": round(merkmale.quantile(werte, 0.5), 6),
                           "p99": round(merkmale.quantile(werte, 0.99), 6)}
        print("Knoten %3d: %4d Fenster, Median %.4f, p99 %.4f -> Schwelle %.4f"
              % (node, len(werte), info[str(node)]["median"], info[str(node)]["p99"], thr))
    with open(a.ausgabe, "w", encoding="utf-8") as f:
        json.dump({"erstellt": time.strftime("%Y-%m-%d %H:%M:%S"),
                   "quellen": [os.path.basename(p) for p in a.dateien],
                   "fenster_s": a.fenster, "faktor": a.faktor,
                   "schwellen": schwellen, "statistik": info}, f, indent=2)
    print("gespeichert: %s" % a.ausgabe)
    return 0


def auswerten_datei(pfad, schwellen, fenster_s, halte_s, min_knoten):
    """Zeitreihe in 1-s-Schritten: Raumstatus, Referenzen, Knotenindizes."""
    meta, pakete = aufzeichnung.lesen(pfad)
    w = merkmale.Windower(fenster_s)
    det = merkmale.Detector(schwellen, halte_s, min_knoten)
    radar = {}   # node -> (t, praesenz)
    fw = {}      # node -> (t, praesenz)
    index = {}
    zeilen = []
    naechster = None
    for p in pakete:
        t = p["t"]
        if naechster is None:
            naechster = t + 1.0
        while t >= naechster:
            zeilen.append(_zeile(naechster, det, index, radar, fw))
            naechster += 1.0
        if p.get("typ") == "csi":
            r = w.add(p["node"], t, aufzeichnung.amplituden(p))
            if r and r[2] is not None:
                index[r[0]] = r[2]
                det.update(r[0], r[1], r[2])
        elif p.get("typ") == "vitals":
            fw[p["node"]] = (t, bool(p.get("fw_praesenz")))
            if "radar_praesenz" in p:
                radar[p["node"]] = (t, bool(p["radar_praesenz"]))
    return meta, zeilen


def _aktuell(tabelle, t):
    werte = [v for (ts, v) in tabelle.values() if t - ts <= REFERENZ_MAX_ALTER_S]
    return None if not werte else any(werte)


def _zeile(t, det, index, radar, fw):
    return {"t": t, "raum_belegt": det.room_occupied(t),
            "radar": _aktuell(radar, t), "firmware": _aktuell(fw, t),
            "index": dict(index),
            "belegt_knoten": {n: det.node_occupied(n, t) for n in det.schwellen}}


def _quote(zeilen, schluessel, erwartet):
    paare = [(z["raum_belegt"], z[schluessel] if schluessel else erwartet) for z in zeilen]
    paare = [(a, b) for a, b in paare if b is not None]
    if not paare:
        return None, 0
    return sum(1 for a, b in paare if a == b) / len(paare), len(paare)


def auswerten(a):
    with open(a.kalibrierung, encoding="utf-8") as f:
        kal = json.load(f)
    schwellen = {int(k): float(v) for k, v in kal["schwellen"].items()}
    fenster = kal.get("fenster_s", 1.0)
    alle = []
    kal_quellen = set(kal.get("quellen", []))
    print("Eigene Messung, Raum und Aufbau spezifisch; keine allgemeine Genauigkeitsaussage.\n")
    print("%-34s %-8s %5s %7s %10s %10s %10s" % (
        "Datei", "Label", "Sek.", "belegt", "vs.Label", "vs.LD2410", "vs.FW"))
    for pfad in a.dateien:
        meta, zeilen = auswerten_datei(pfad, schwellen, fenster, a.halte, a.min_knoten)
        label = meta.get("label", "?")
        if not zeilen:
            print("%-34s keine Daten" % os.path.basename(pfad)[:34])
            continue
        anteil = sum(z["raum_belegt"] for z in zeilen) / len(zeilen)
        erwartet = None if label in ("gemischt", "test", "?") else (label != "leer")
        q_label, _ = _quote(zeilen, None, erwartet) if erwartet is not None else (None, 0)
        q_radar, n_radar = _quote(zeilen, "radar", None)
        q_fw, _ = _quote(zeilen, "firmware", None)
        fmt = lambda q: "-" if q is None else "%.0f %%" % (100 * q)
        print("%-34s %-8s %5d %6.0f%% %10s %10s %10s%s" % (
            os.path.basename(pfad)[:34], label, len(zeilen), 100 * anteil,
            fmt(q_label), fmt(q_radar), fmt(q_fw),
            "  (Kalibrierdatei, nicht aussagekräftig)"
            if os.path.basename(pfad) in kal_quellen else ""))
        for z in zeilen:
            z["datei"] = os.path.basename(pfad)
            z["label"] = label
        alle.extend(zeilen)
    print("\nvs.Label: Anteil Sekunden, in denen 'Raum belegt' zum Aufnahme-Label passt")
    print("          (leer -> frei erwartet, gehen/sitzen/stehen/tuer -> belegt).")
    print("vs.LD2410 / vs.FW: Übereinstimmung mit Radar-Referenz bzw. Firmware-Präsenzbit.")
    print("Hinweis: die Haltezeit (%.0f s) macht die ersten Sekunden nach Bewegungsende "
          "absichtlich 'belegt'." % a.halte)

    if a.csv and alle:
        knoten = sorted(schwellen)
        with open(a.csv, "w", newline="", encoding="utf-8") as f:
            wr = csv.writer(f, delimiter=";")
            wr.writerow(["datei", "label", "t", "raum_belegt", "ld2410", "firmware"]
                        + ["index_%d" % n for n in knoten])
            for z in alle:
                wr.writerow([z["datei"], z["label"], "%.1f" % z["t"], int(z["raum_belegt"]),
                             "" if z["radar"] is None else int(z["radar"]),
                             "" if z["firmware"] is None else int(z["firmware"])]
                            + ["" if z["index"].get(n) is None else "%.5f" % z["index"][n]
                               for n in knoten])
        print("CSV: %s" % a.csv)
    if a.diagramm and alle:
        diagramm(alle, schwellen, a.diagramm)
    return 0


def diagramm(zeilen, schwellen, pfad):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        print("matplotlib fehlt (sudo apt install python3-matplotlib); kein Diagramm.",
              file=sys.stderr)
        return
    knoten = sorted(schwellen)
    fig, achsen = plt.subplots(len(knoten) + 1, 1, sharex=True,
                               figsize=(12, 2 + 1.6 * len(knoten)))
    x = list(range(len(zeilen)))
    for ax, n in zip(achsen, knoten):
        ax.plot(x, [z["index"].get(n, float("nan")) for z in zeilen], lw=0.8, label="Index")
        ax.axhline(schwellen[n], color="tab:red", lw=0.8, ls="--", label="Schwelle")
        ax.set_ylabel("Knoten %d" % n)
    ax = achsen[-1]
    ax.step(x, [int(z["raum_belegt"]) for z in zeilen], where="post", label="CSI: belegt")
    ax.step(x, [0.5 * int(bool(z["radar"])) for z in zeilen], where="post", label="LD2410 (0,5)")
    ax.set_yticks([0, 0.5, 1])
    ax.legend(loc="upper right", fontsize=8)
    grenzen = [i for i in range(1, len(zeilen)) if zeilen[i]["datei"] != zeilen[i - 1]["datei"]]
    for ax in achsen:
        for g in grenzen:
            ax.axvline(g, color="grey", lw=0.6)
    achsen[0].set_title("Bewegungsindex je Knoten und Raumstatus (Sekunden, Dateien aneinandergereiht)")
    achsen[-1].set_xlabel("Sekunde")
    fig.tight_layout()
    fig.savefig(pfad, dpi=120)
    print("Diagramm: %s" % pfad)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="befehl", required=True)
    k = sub.add_parser("kalibrieren", help="Schwellen aus Leerraum-Aufnahme")
    k.add_argument("dateien", nargs="+")
    k.add_argument("-o", "--ausgabe", default="kalibrierung.json")
    k.add_argument("--fenster", type=float, default=1.0, help="Fensterlänge s")
    k.add_argument("--faktor", type=float, default=1.0,
                   help="Schwelle multiplizieren (>1 weniger Fehlalarme)")
    k.add_argument("--erzwingen", action="store_true", help="auch ohne Label 'leer'")
    k.set_defaults(fn=kalibrieren)
    w = sub.add_parser("auswerten", help="Aufnahmen gegen Kalibrierung auswerten")
    w.add_argument("dateien", nargs="+")
    w.add_argument("-k", "--kalibrierung", default="kalibrierung.json")
    w.add_argument("--halte", type=float, default=30.0, help="Haltezeit s (Standard 30)")
    w.add_argument("--min-knoten", type=int, default=1,
                   help="so viele Knoten müssen Bewegung sehen (Standard 1)")
    w.add_argument("--csv")
    w.add_argument("--diagramm", help="PNG-Datei (braucht matplotlib)")
    w.set_defaults(fn=auswerten)
    a = ap.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
