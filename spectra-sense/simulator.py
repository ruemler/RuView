#!/usr/bin/env python3
"""SYNTHETISCHER Datengenerator: spielt den Testaufbau ohne Hardware nach.

Sendet ADR-018-CSI-Pakete und Vitals-/LD2410-Pakete für alle Knoten aus
testaufbau.json an den Laptop (Standard 127.0.0.1). Damit lassen sich
aufnahme.py und auswertung.py vor dem ersten Hardwaretest ausprobieren.

  python3 simulator.py --szenario leer:60,gehen:30,sitzen:30

Die Daten sind ein grobes Modell (Grundprofil + Rauschen + Modulation durch
Bewegung/Atmung). Ergebnisse damit sind SYNTHETIC und sagen nichts über die
Leistung mit echter Hardware aus.
"""

import argparse
import math
import random
import socket
import sys
import time

from ssense import konfig, protokoll

N_SUB = 64
GUARD = set([0] + list(range(27, 38)))   # leere Subträger wie bei HT20-LLTF
STAERKE = {"leer": 0.0, "gehen": 0.15, "tuer": 0.12, "sitzen": 0.02, "stehen": 0.03}


def parse_szenario(text):
    teile = []
    for stueck in text.split(","):
        name, _, sek = stueck.partition(":")
        name = name.strip()
        if name not in STAERKE:
            raise ValueError("unbekannte Phase %r (bekannt: %s)" % (name, ", ".join(STAERKE)))
        teile.append((name, float(sek or 30)))
    return teile


class KnotenModell:
    def __init__(self, node_id, rng):
        self.node = node_id
        self.rng = rng
        self.basis = [0.0 if k in GUARD else
                      22 + 9 * math.sin(k / 6.0 + node_id) + rng.uniform(-3, 3)
                      for k in range(N_SUB)]
        self.phase = [rng.uniform(0, 2 * math.pi) for _ in range(N_SUB)]
        self.gewicht = [rng.uniform(0.2, 1.0) for _ in range(N_SUB)]
        self.naehe = rng.uniform(0.6, 1.0)     # wie stark dieser Link betroffen ist
        self.seq = 0

    def frame(self, t, phase):
        rng = self.rng
        s = STAERKE[phase] * self.naehe
        gain = 1.0 + rng.gauss(0, 0.03)       # AGC-Schwankung (gemeinsam)
        if phase in ("sitzen", "stehen"):
            mod_f = 0.25                       # Atmung ~15/min
            if int(t) % 12 == 0:               # gelegentliche kleine Bewegung
                s *= 4
        else:
            mod_f = 0.8 + 0.4 * math.sin(t / 3.0)
        iq = bytearray()
        for k in range(N_SUB):
            b = self.basis[k]
            if b == 0.0:
                iq += b"\x00\x00"
                continue
            m = s * self.gewicht[k] * math.sin(2 * math.pi * mod_f * t + self.phase[k])
            amp = b * gain * (1 + m + rng.gauss(0, 0.012))
            phi = self.phase[k] + rng.gauss(0, 0.05)
            i = max(-127, min(127, int(round(amp * math.cos(phi)))))
            q = max(-127, min(127, int(round(amp * math.sin(phi)))))
            iq += bytes([i & 0xFF, q & 0xFF])
        self.seq += 1
        rssi = -48 - int(4 * self.naehe) + int(rng.gauss(0, 1))
        return protokoll.build_csi(self.node, self.seq, iq, N_SUB, rssi=rssi, freq=2437)


def erzeuge(k, szenario, rate_hz=50.0, seed=1, t0=0.0):
    """Generator über (t, node, datagramm) in Zeitreihenfolge."""
    rng = random.Random(seed)
    modelle = [KnotenModell(n["node_id"], rng) for n in k["knoten"]]
    radar = {n["node_id"]: bool(n.get("ld2410")) for n in k["knoten"]}
    t = t0
    dt = 1.0 / rate_hz
    for phase, dauer in parse_szenario(szenario) if isinstance(szenario, str) else szenario:
        ende = t + dauer
        naechste_vitals = t
        while t < ende:
            for m in modelle:
                yield t + rng.uniform(0, dt * 0.5), m.node, m.frame(t, phase)
            if t >= naechste_vitals:
                da = phase != "leer"
                for m in modelle:
                    if radar[m.node]:
                        pkt = protokoll.build_fused(m.node, da, da, motion=phase in ("gehen", "tuer"),
                                                    distance_cm=150.0 if da else 0.0,
                                                    ts_ms=int(t * 1000))
                    else:
                        pkt = protokoll.build_vitals(m.node, da, motion=phase in ("gehen", "tuer"),
                                                     ts_ms=int(t * 1000))
                    yield t, m.node, pkt
                naechste_vitals += 1.0
            t += dt


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--konfig")
    ap.add_argument("--szenario", default="leer:60,gehen:30,sitzen:30,leer:30",
                    help="Phasen name:sekunden, Namen: %s" % ", ".join(STAERKE))
    ap.add_argument("--ziel", default="127.0.0.1")
    ap.add_argument("--rate", type=float, default=50.0, help="Frames/s je Knoten")
    ap.add_argument("--seed", type=int, default=1)
    a = ap.parse_args(argv)
    k = konfig.laden(a.konfig)
    port = int(k["netz"]["udp_port"])
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    print("SYNTHETISCH: sende %s an %s:%d (Strg+C beendet)" % (a.szenario, a.ziel, port))
    start = time.time()
    phase_alt = None
    grenzen, acc = [], 0.0
    for name, d in parse_szenario(a.szenario):
        grenzen.append((acc, name))
        acc += d
    try:
        for t, _, daten in erzeuge(k, a.szenario, a.rate, a.seed):
            warte = start + t - time.time()
            if warte > 0:
                time.sleep(warte)
            phase = [n for g, n in grenzen if g <= t][-1]
            if phase != phase_alt:
                print("  t=%5.0f s  Phase: %s" % (t, phase), flush=True)
                phase_alt = phase
            sock.sendto(daten, (a.ziel, port))
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
