"""Einfache Bewegungs-/Präsenzerkennung aus CSI-Amplituden.

Verfahren (bewusst simpel und nachvollziehbar, kein trainiertes Modell):

1. Jeder CSI-Frame wird auf seine mittlere Amplitude normiert. Das entfernt
   gemeinsame Pegelsprünge der ESP32-Verstärkungsregelung (AGC).
   Pro Knoten zählt nur die häufigste Frame-Art (Anzahl Subträger, z. B. 128
   bei HT20). Ein Board empfängt mehrere Arten (64/128/192) auch von
   verschiedenen Sendern; gemischt würden sie den Index künstlich aufblähen
   (gemessen 07.11.: Leerraum-Index ~0,18 statt ~0,03-0,15).
2. Pro Knoten und Zeitfenster (Standard 1 s) wird je Subträger die
   Standardabweichung der normierten Amplitude berechnet und über die
   Subträger gemittelt: der "Bewegungsindex". Ruhiger Raum -> klein,
   Bewegung im Funkfeld -> groß.
3. Kalibrierung: aus einer Leerraum-Aufnahme wird je Knoten eine Schwelle
   abgeleitet: max(p99, Median + 5 * robuste Streuung).
4. Bewegung = Index über Schwelle. "Belegt" = in den letzten `halte_s`
   Sekunden gab es Bewegung (Haltezeit, weil ruhig sitzende Menschen kaum
   Signal erzeugen).

Das ist ein Referenzverfahren für erste Tests. Aussagen zur Genauigkeit
gelten nur für eigene, mit Referenz (LD2410/Protokoll) erhobene Messungen.
"""

import math
import statistics

MIN_FRAMES = 8            # weniger Frames pro Fenster -> kein Indexwert
MIN_SUB_AMPLITUDE = 2.0   # Subträger darunter gelten als leer/Guard


def normalized(amps):
    """Amplituden durch ihren Mittelwert teilen (None, wenn Frame leer)."""
    used = [a for a in amps if a > 0.0]
    if not used:
        return None
    mean = sum(used) / len(used)
    if mean <= 0.0:
        return None
    return [a / mean for a in amps]


def motion_index(frames):
    """Bewegungsindex für die Amplituden-Frames eines Fensters (oder None)."""
    rows = [normalized(f) for f in frames]
    rows = [r for r in rows if r is not None]
    if len(rows) < MIN_FRAMES:
        return None
    width = min(len(r) for r in rows)
    raw_means = [statistics.fmean(f[i] for f in frames) for i in range(width)]
    stds = []
    for i in range(width):
        if raw_means[i] < MIN_SUB_AMPLITUDE:
            continue
        stds.append(statistics.pstdev(r[i] for r in rows))
    if not stds:
        return None
    return statistics.fmean(stds)


def quantile(values, q):
    vals = sorted(values)
    if not vals:
        raise ValueError("keine Werte")
    pos = (len(vals) - 1) * q
    lo = math.floor(pos)
    hi = math.ceil(pos)
    return vals[lo] + (vals[hi] - vals[lo]) * (pos - lo)


def threshold_from_empty(values):
    """Schwelle aus Indexwerten eines leeren Raums."""
    if len(values) < 10:
        raise ValueError("zu wenige Leerraum-Fenster (%d, mindestens 10)" % len(values))
    med = statistics.median(values)
    mad = statistics.median(abs(v - med) for v in values) * 1.4826
    return max(quantile(values, 0.99), med + 5.0 * mad)


class Windower:
    """Sammelt Amplituden-Frames je Knoten und liefert Fenster-Indizes."""

    def __init__(self, fenster_s=1.0):
        self.fenster_s = fenster_s
        self._start = {}
        self._frames = {}
        self._laengen = {}   # node -> {n_sub: Anzahl}, für die Haupt-Frame-Art

    def haupt_laenge(self, node):
        """Häufigste Frame-Länge (Subträger) eines Knotens bisher, sonst None."""
        z = self._laengen.get(node)
        if not z:
            return None
        return max(z.items(), key=lambda kv: (kv[1], kv[0]))[0]

    def _index(self, node, frames):
        laenge = self.haupt_laenge(node)
        if laenge is not None:
            frames = [f for f in frames if len(f) == laenge]
        return motion_index(frames)

    def add(self, node, t, amps):
        """Frame hinzufügen; gibt (node, t_ende, index, n_frames) zurück, wenn
        dadurch ein Fenster abgeschlossen wurde, sonst None."""
        z = self._laengen.setdefault(node, {})
        z[len(amps)] = z.get(len(amps), 0) + 1
        start = self._start.get(node)
        if start is None:
            self._start[node] = t
            self._frames[node] = [amps]
            return None
        if t - start < self.fenster_s:
            self._frames[node].append(amps)
            return None
        frames = self._frames[node]
        result = (node, start + self.fenster_s, self._index(node, frames), len(frames))
        # Bei Lücken das neue Fenster am aktuellen Frame beginnen.
        self._start[node] = start + self.fenster_s if t - start < 2 * self.fenster_s else t
        self._frames[node] = [amps]
        return result


class Detector:
    """Schwellwert + Haltezeit je Knoten, Raumstatus über alle Knoten."""

    def __init__(self, schwellen, halte_s=30.0, min_knoten=1):
        self.schwellen = dict(schwellen)
        self.halte_s = halte_s
        self.min_knoten = min_knoten
        self._last_motion = {}

    def update(self, node, t, index):
        """Fensterwert verarbeiten. Gibt True/False für Bewegung zurück,
        None wenn für den Knoten keine Schwelle bekannt ist."""
        thr = self.schwellen.get(node)
        if thr is None or index is None:
            return None
        motion = index > thr
        if motion:
            self._last_motion[node] = t
        return motion

    def node_occupied(self, node, t):
        last = self._last_motion.get(node)
        return last is not None and t - last <= self.halte_s

    def room_occupied(self, t):
        n = sum(1 for node in self.schwellen if self.node_occupied(node, t))
        return n >= self.min_knoten
