"""Messprotokoll: eine CSV-Zeile pro Aufnahme (übernommen vom Laptop-Stand).

Das Protokoll ist das Laborbuch der Messreihe. Es hält fest, was im Raum los
war (Raum, Personen, Aktivität, Türen, Knotenpositionen), damit Aufnahmen
später ohne Rätselraten ausgewertet werden können. Es liegt standardmäßig in
`aufnahmen/` und bleibt damit wie die Aufnahmen selbst lokal (gitignore).
"""

import csv
import os
import time

SPALTEN = ("datei", "start", "dauer_s", "raum", "label", "personen", "tueren",
           "knoten", "pakete", "notiz")


def eintragen(pfad, **werte):
    """Hängt eine Zeile an; legt die Datei mit Kopfzeile an, falls nötig."""
    unbekannt = set(werte) - set(SPALTEN)
    if unbekannt:
        raise ValueError("unbekannte Spalten: %s" % ", ".join(sorted(unbekannt)))
    if "start" in werte and isinstance(werte["start"], (int, float)):
        werte["start"] = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(werte["start"]))
    ordner = os.path.dirname(pfad)
    if ordner:
        os.makedirs(ordner, exist_ok=True)
    neu = not os.path.exists(pfad) or os.path.getsize(pfad) == 0
    with open(pfad, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=SPALTEN)
        if neu:
            w.writeheader()
        w.writerow({s: werte.get(s, "") for s in SPALTEN})


def lesen(pfad):
    with open(pfad, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))
