"""Live-Kurve: Bewegungsindex und RSSI je Knoten über die letzten Sekunden.

Ergänzt die Live-Tabelle von aufnahme.py (Idee vom Laptop-Stand). Braucht
matplotlib (`sudo apt install python3-matplotlib`); ohne matplotlib meldet
`starten()` einen Fehler, und aufnahme.py läuft mit der Tabelle weiter.
"""

from collections import deque


class Verlauf:
    """Ringpuffer (t, index, rssi) je Knoten, unabhängig von matplotlib."""

    def __init__(self, sekunden=60.0):
        self.sekunden = sekunden
        self.punkte = {}

    def add(self, node, t, index, rssi):
        q = self.punkte.setdefault(node, deque())
        q.append((t, index, rssi))
        while q and t - q[0][0] > self.sekunden:
            q.popleft()

    def reihe(self, node, jetzt):
        """Gibt (sekunden_relativ, index, rssi) als Listen zurück."""
        q = self.punkte.get(node, ())
        return ([t - jetzt for t, _, _ in q],
                [float("nan") if i is None else i for _, i, _ in q],
                [float("nan") if r is None else r for _, _, r in q])


class Kurve:
    def __init__(self, namen, schwellen, sekunden=60.0):
        self.namen = namen          # node_id -> Knoten-Dict
        self.schwellen = schwellen
        self.verlauf = Verlauf(sekunden)
        self._plt = None

    def starten(self):
        import matplotlib.pyplot as plt  # nur hier, damit der Rest ohne läuft
        self._plt = plt
        plt.ion()
        self.fig, (self.ax_i, self.ax_r) = plt.subplots(2, 1, sharex=True, figsize=(9, 6))
        self.fig.canvas.manager.set_window_title("Spectra Sense live")
        self.ax_i.set_ylabel("Bewegungsindex")
        self.ax_r.set_ylabel("RSSI (dBm)")
        self.ax_r.set_xlabel("Sekunden (0 = jetzt)")
        self.linien = {}
        for nid, n in sorted(self.namen.items()):
            (li,) = self.ax_i.plot([], [], label=n["name"])
            (lr,) = self.ax_r.plot([], [], label=n["name"], color=li.get_color())
            self.linien[nid] = (li, lr)
            thr = self.schwellen.get(nid)
            if thr is not None:
                self.ax_i.axhline(thr, color=li.get_color(), linestyle="--", linewidth=0.8)
        self.ax_i.legend(loc="upper left", fontsize="small")
        self.ax_i.set_xlim(-self.verlauf.sekunden, 0)
        self.fig.tight_layout()

    def offen(self):
        return self._plt is not None and self._plt.fignum_exists(self.fig.number)

    def zeichnen(self, jetzt):
        if not self.offen():
            return
        for nid, (li, lr) in self.linien.items():
            x, idx, rssi = self.verlauf.reihe(nid, jetzt)
            li.set_data(x, idx)
            lr.set_data(x, rssi)
        for ax in (self.ax_i, self.ax_r):
            ax.relim()
            ax.autoscale_view(scalex=False)
        self.fig.canvas.draw_idle()
        self._plt.pause(0.001)
