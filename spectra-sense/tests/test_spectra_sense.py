"""SYNTHETIC: Tests der Laptop-Werkzeuge ohne Hardware.

  cd spectra-sense && python3 -m unittest discover -s tests -v
"""

import contextlib
import copy
import io
import json
import os
import socket
import sys
import tempfile
import threading
import time
import unittest

HIER = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HIER)

import auswertung  # noqa: E402
import aufnahme  # noqa: E402
import knoten  # noqa: E402
import simulator  # noqa: E402
from ssense import aufzeichnung, konfig, merkmale, protokoll  # noqa: E402


def still(fn, *args):
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
        rc = fn(*args)
    return rc, out.getvalue()


def schreibe_aufnahme(pfad, k, label, szenario, seed=1):
    s = aufzeichnung.Schreiber(pfad, {"label": label})
    for t, _, daten in simulator.erzeuge(k, szenario, seed=seed):
        s.paket(t, protokoll.parse(daten))
    s.schliessen()


class ProtokollTest(unittest.TestCase):
    def test_csi_roundtrip(self):
        iq = bytes([3, 4] * 64)
        pkt = protokoll.parse(protokoll.build_csi(7, 42, iq, 64, rssi=-61, freq=2437))
        self.assertEqual((pkt["node"], pkt["seq"], pkt["rssi"], pkt["kanal"]), (7, 42, -61, 6))
        self.assertEqual(protokoll.amplitudes(pkt["iq"], 64), [5.0] * 64)

    def test_negative_iq_is_signed(self):
        pkt = protokoll.parse(protokoll.build_csi(1, 1, bytes([0xFD, 0xFC]), 1))
        self.assertEqual(protokoll.amplitudes(pkt["iq"], 1), [5.0])

    def test_fused_carries_ld2410(self):
        pkt = protokoll.parse(protokoll.build_fused(11, False, True, distance_cm=120.0))
        self.assertEqual(pkt["radar_typ"], "LD2410")
        self.assertTrue(pkt["radar_praesenz"])
        self.assertFalse(pkt["fw_praesenz"])
        self.assertEqual(pkt["radar_distanz_cm"], 120.0)

    def test_vitals(self):
        pkt = protokoll.parse(protokoll.build_vitals(3, True, motion=True))
        self.assertTrue(pkt["fw_praesenz"] and pkt["fw_bewegung"])
        self.assertNotIn("radar_praesenz", pkt)

    def test_rejects_garbage(self):
        self.assertIsNone(protokoll.parse(b""))
        self.assertIsNone(protokoll.parse(b"\x01\x00\x11\xc5" + b"\x00" * 10))
        truncated = protokoll.build_csi(1, 1, bytes(128), 64)[:-1]
        self.assertIsNone(protokoll.parse(truncated))
        bad_ant = bytearray(protokoll.build_csi(1, 1, bytes(128), 64))
        bad_ant[5] = 9
        self.assertIsNone(protokoll.parse(bytes(bad_ant)))
        self.assertIsNone(protokoll.parse(b"\x07\x00\x11\xc5" + bytes(60)))


class KonfigTest(unittest.TestCase):
    def setUp(self):
        self.k = konfig.laden()

    def test_default_config_valid(self):
        self.assertEqual(self.k["netz"]["kanal"], 6)
        self.assertEqual(sum(n["rolle"] == "sender" for n in self.k["knoten"]), 1)

    def fehler(self, aendern):
        k = copy.deepcopy(self.k)
        aendern(k)
        with self.assertRaises(konfig.KonfigFehler):
            konfig.pruefen(k)

    def test_rejections(self):
        self.fehler(lambda k: k["netz"].update(kanal=36))
        self.fehler(lambda k: k["netz"].update(laptop_ip="8.8.8.8"))
        self.fehler(lambda k: k.update(link_modus="mesh"))
        self.fehler(lambda k: k.update(sender_mac="zz"))
        self.fehler(lambda k: k["knoten"][1].update(node_id=1))
        self.fehler(lambda k: k["knoten"][1].update(name="sender"))
        self.fehler(lambda k: k["knoten"][1].update(name="../x"))

        def zwei_sender(k):
            k["link_modus"] = "sender"
            k["knoten"][1]["rolle"] = "sender"
        self.fehler(zwei_sender)


class KnotenTest(unittest.TestCase):
    def setUp(self):
        self.k = konfig.laden()

    def test_ap_mode_has_no_filter(self):
        n = konfig.knoten_nach_name(self.k, "empf-1")
        cmd = knoten.provision_befehl(self.k, n, "/dev/ttyUSB1")
        self.assertIn("--password-file", cmd)
        self.assertNotIn("--password", cmd)
        self.assertNotIn("--filter-mac", cmd)
        self.assertEqual(cmd[cmd.index("--node-id") + 1], "11")
        self.assertIn("--reset", cmd)

    def test_sender_mode_filters_receivers_only(self):
        k = copy.deepcopy(self.k)
        k["link_modus"] = "sender"
        with self.assertRaises(konfig.KonfigFehler):
            knoten.provision_befehl(k, konfig.knoten_nach_name(k, "empf-1"), "p")
        k["sender_mac"] = "aa:bb:cc:dd:ee:ff"
        cmd = knoten.provision_befehl(k, konfig.knoten_nach_name(k, "empf-1"), "p")
        self.assertEqual(cmd[cmd.index("--filter-mac") + 1], "AA:BB:CC:DD:EE:FF")
        cmd = knoten.provision_befehl(k, konfig.knoten_nach_name(k, "sender"), "p")
        self.assertNotIn("--filter-mac", cmd)

    def test_flash_offsets(self):
        cmd, _ = knoten.flash_befehl(self.k, self.k["knoten"][0], "/dev/ttyUSB0")
        self.assertIn("0x20000", cmd)
        self.assertTrue(cmd[cmd.index("0x20000") + 1].endswith("esp32-csi-node.bin"))
        self.assertEqual(cmd[cmd.index("--flash_size") + 1], "8MB")

    def test_provision_script_exists(self):
        self.assertTrue(os.path.isfile(knoten.PROVISION))

    def test_dry_run_all(self):
        rc, out = still(knoten.main, ["--trocken", "einrichten", "alle"])
        self.assertEqual(rc, 0)
        self.assertEqual(out.count("provision.py"), 5)
        self.assertLess(out.index("== sender"), out.index("== empf-1"))


class MerkmaleTest(unittest.TestCase):
    def test_threshold_needs_data(self):
        with self.assertRaises(ValueError):
            merkmale.threshold_from_empty([0.1] * 5)

    def test_gain_jump_is_not_motion(self):
        base = [10.0 + i % 7 for i in range(64)]
        frames = [[a * (1.0 if j % 2 else 1.5) for a in base] for j in range(20)]
        self.assertLess(merkmale.motion_index(frames), 1e-9)

    def test_detector_hold(self):
        d = merkmale.Detector({1: 0.05}, halte_s=10)
        self.assertTrue(d.update(1, 100.0, 0.2))
        self.assertTrue(d.room_occupied(105.0))
        self.assertFalse(d.update(1, 106.0, 0.01))
        self.assertFalse(d.room_occupied(111.0))
        self.assertIsNone(d.update(99, 1.0, 0.2))


class EndeZuEndeTest(unittest.TestCase):
    """Simulierte Aufnahmen -> Kalibrierung -> Auswertung."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.k = konfig.laden()
        d = cls.tmp.name
        cls.leer = os.path.join(d, "a_leer.csi.jsonl")
        cls.gehen = os.path.join(d, "b_gehen.csi.jsonl")
        cls.leer2 = os.path.join(d, "c_leer.csi.jsonl")
        schreibe_aufnahme(cls.leer, cls.k, "leer", "leer:60", seed=1)
        schreibe_aufnahme(cls.gehen, cls.k, "gehen", "gehen:30", seed=1)
        schreibe_aufnahme(cls.leer2, cls.k, "leer", "leer:30", seed=1)
        cls.kal = os.path.join(d, "kal.json")
        rc, cls.kal_out = still(auswertung.main, ["kalibrieren", cls.leer, "-o", cls.kal])
        assert rc == 0, cls.kal_out

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_calibration_covers_all_nodes(self):
        with open(self.kal) as f:
            kal = json.load(f)
        self.assertEqual(sorted(int(n) for n in kal["schwellen"]), [1, 11, 12, 13, 14])

    def test_calibration_refuses_non_empty_label(self):
        rc, out = still(auswertung.main, ["kalibrieren", self.gehen, "-o",
                                          os.path.join(self.tmp.name, "x.json")])
        self.assertEqual(rc, 2)
        self.assertIn("leer", out)

    def test_walking_detected_and_empty_room_free(self):
        csv_pfad = os.path.join(self.tmp.name, "e.csv")
        rc, out = still(auswertung.main, ["auswerten", self.gehen, self.leer2, "-k", self.kal,
                                          "--halte", "5", "--csv", csv_pfad])
        self.assertEqual(rc, 0, out)
        _, zeilen = auswertung.auswerten_datei(self.gehen, self._schwellen(), 1.0, 5.0, 1)
        self.assertGreater(sum(z["raum_belegt"] for z in zeilen) / len(zeilen), 0.9)
        self.assertTrue(all(z["radar"] for z in zeilen[1:]))
        _, zeilen = auswertung.auswerten_datei(self.leer2, self._schwellen(), 1.0, 5.0, 1)
        self.assertLess(sum(z["raum_belegt"] for z in zeilen) / len(zeilen), 0.1)
        with open(csv_pfad) as f:
            kopf = f.readline().strip().split(";")
        self.assertEqual(kopf[:3], ["datei", "label", "t"])
        self.assertIn("index_11", kopf)

    def test_truncated_last_line_is_tolerated(self):
        kaputt = os.path.join(self.tmp.name, "kaputt.csi.jsonl")
        with open(self.gehen) as src, open(kaputt, "w") as dst:
            dst.write(src.read()[:-40])
        meta, pakete = aufzeichnung.lesen(kaputt)
        self.assertEqual(meta["label"], "gehen")
        self.assertGreater(sum(1 for _ in pakete), 1000)

    def _schwellen(self):
        with open(self.kal) as f:
            return {int(k): v for k, v in json.load(f)["schwellen"].items()}


class UdpTest(unittest.TestCase):
    """aufnahme.py empfängt echte UDP-Datagramme auf Loopback."""

    def test_record_over_udp(self):
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        k = konfig.laden()
        stop = threading.Event()

        def senden():
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as tx:
                start = time.time()
                for t, _, daten in simulator.erzeuge(k, "gehen:3"):
                    if stop.is_set():
                        return
                    warte = start + 0.3 + t - time.time()
                    if warte > 0:
                        time.sleep(warte)
                    tx.sendto(daten, ("127.0.0.1", port))
                    tx.sendto(b"garbage", ("127.0.0.1", port))

        with tempfile.TemporaryDirectory() as d:
            th = threading.Thread(target=senden, daemon=True)
            th.start()
            rc, out = still(aufnahme.main, ["--label", "test", "--dauer", "2.5", "--port",
                                            str(port), "--bind", "127.0.0.1", "--ausgabe", d,
                                            "--ohne-anzeige"])
            stop.set()
            th.join(5)
            self.assertEqual(rc, 0, out)
            dateien = os.listdir(d)
            self.assertEqual(len(dateien), 1)
            self.assertTrue(dateien[0].endswith("_test.csi.jsonl"))
            meta, pakete = aufzeichnung.lesen(os.path.join(d, dateien[0]))
            knoten_ids = {p["node"] for p in pakete if p["typ"] == "csi"}
            self.assertEqual(knoten_ids, {1, 11, 12, 13, 14})
            self.assertEqual(meta["kanal"], 6)

    def test_foreign_source_rejected(self):
        import ipaddress
        netz = ipaddress.ip_network("192.168.178.0/24")
        self.assertTrue(aufnahme.quelle_erlaubt("192.168.178.31", netz))
        self.assertTrue(aufnahme.quelle_erlaubt("127.0.0.1", netz))
        self.assertFalse(aufnahme.quelle_erlaubt("10.0.0.5", netz))


if __name__ == "__main__":
    unittest.main()
