"""Aufnahmedateien (*.csi.jsonl) schreiben und lesen.

Zeile 1:  {"typ": "meta", "label": ..., "start": ..., "knoten": [...], ...}
danach:   {"t": <Laptop-Zeit s>, "typ": "csi", "node": .., "iq": <base64>, ...}
          {"t": ..., "typ": "vitals", "node": .., "fw_praesenz": .., ...}

CSI-Aufnahmen sind personenbezogene Daten (Anwesenheit, Bewegung, Atmung).
Sie bleiben lokal; *.csi.jsonl ist in .gitignore ausgeschlossen.
"""

import base64
import json
import time

from . import protokoll


class Schreiber:
    def __init__(self, pfad, meta):
        self.pfad = pfad
        self._f = open(pfad, "w", encoding="utf-8")
        kopf = {"typ": "meta", "format": "spectra-sense/1", "start": time.time()}
        kopf.update(meta)
        self._f.write(json.dumps(kopf, ensure_ascii=False) + "\n")
        self.zeilen = 0

    def paket(self, t, pkt):
        zeile = {"t": round(t, 4)}
        for k, v in pkt.items():
            zeile[k] = base64.b64encode(v).decode("ascii") if k == "iq" else v
        self._f.write(json.dumps(zeile, separators=(",", ":")) + "\n")
        self.zeilen += 1

    def schliessen(self):
        self._f.close()


def lesen(pfad):
    """Gibt (meta, Iterator über Pakete) zurück; iq ist wieder bytes."""
    f = open(pfad, encoding="utf-8")
    erste = f.readline()
    meta = json.loads(erste) if erste.strip() else {}
    if meta.get("typ") != "meta":
        raise ValueError("%s: keine spectra-sense-Aufnahme (Metazeile fehlt)" % pfad)

    def pakete():
        with f:
            for nr, zeile in enumerate(f, start=2):
                if not zeile.strip():
                    continue
                try:
                    p = json.loads(zeile)
                except json.JSONDecodeError:
                    # z. B. abgebrochene letzte Zeile nach Stromausfall
                    continue
                if p.get("typ") == "csi":
                    p["iq"] = base64.b64decode(p["iq"])
                yield p

    return meta, pakete()


def amplituden(pkt):
    return protokoll.amplitudes(pkt["iq"], pkt["n_sub"])
