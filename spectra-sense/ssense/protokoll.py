"""UDP-Paketformate der RuView-ESP32-Firmware (nur Standardbibliothek).

Quelle der Formate (bei Abweichungen gilt die Firmware):
  firmware/esp32-csi-node/main/csi_collector.c      0xC5110001 Roh-CSI (ADR-018)
  firmware/esp32-csi-node/main/edge_processing.h    0xC5110002 Vitals (32 B)
                                                    0xC5110004 Fused Vitals inkl.
                                                    mmWave/LD2410 (48 B)
Alle anderen Magics (Feature-Vektor, komprimiert, WASM, Sync) werden ignoriert.
"""

import math
import struct

CSI_MAGIC = 0xC5110001
VITALS_MAGIC = 0xC5110002
FUSED_MAGIC = 0xC5110004

CSI_HDR = struct.Struct("<IBBHIIbbBB")          # 20 Byte
VITALS = struct.Struct("<IBBHIbB2xffII")        # 32 Byte
FUSED = struct.Struct("<IBBHIbBBBffIfffBBHI")   # 48 Byte

MAX_ANTENNAS = 4
MAX_SUBCARRIERS = 256

# mmwave_type_t aus mmwave_sensor.h (nur für die Anzeige)
MMWAVE_TYPES = {0: "keiner", 1: "MR60BHA2", 2: "LD2410"}


def freq_to_channel(freq_mhz):
    """802.11-Kanal zu einer Mittenfrequenz (0, falls unbekannt)."""
    if freq_mhz == 2484:
        return 14
    if 2412 <= freq_mhz <= 2472:
        return (freq_mhz - 2407) // 5
    if 5000 <= freq_mhz <= 5900:
        return (freq_mhz - 5000) // 5
    return 0


def parse(data):
    """Ein UDP-Datagramm dekodieren. Gibt dict oder None (unbekannt/defekt)."""
    if len(data) < 4:
        return None
    magic = struct.unpack_from("<I", data)[0]
    if magic == CSI_MAGIC:
        return _parse_csi(data)
    if magic == VITALS_MAGIC and len(data) >= VITALS.size:
        return _parse_vitals(data)
    if magic == FUSED_MAGIC and len(data) >= FUSED.size:
        return _parse_fused(data)
    return None


def _parse_csi(data):
    if len(data) < CSI_HDR.size:
        return None
    (_, node, n_ant, n_sub, freq, seq, rssi, noise, ppdu, flags) = CSI_HDR.unpack_from(data)
    if not 1 <= n_ant <= MAX_ANTENNAS or not 1 <= n_sub <= MAX_SUBCARRIERS:
        return None
    n_bytes = n_ant * n_sub * 2
    iq = bytes(data[CSI_HDR.size:CSI_HDR.size + n_bytes])
    if len(iq) < n_bytes:
        return None
    return {
        "typ": "csi", "node": node, "n_ant": n_ant, "n_sub": n_sub,
        "freq": freq, "kanal": freq_to_channel(freq), "seq": seq,
        "rssi": rssi, "noise": noise, "flags": flags, "iq": iq,
    }


def _parse_vitals(data):
    (_, node, flags, br, hr, rssi, n_pers, motion, presence, ts, _) = VITALS.unpack_from(data)
    return {
        "typ": "vitals", "node": node, "rssi": rssi,
        "fw_praesenz": bool(flags & 0x01), "fw_bewegung": bool(flags & 0x04),
        "motion_energy": _finite(motion), "presence_score": _finite(presence),
        "ts_ms": ts,
    }


def _parse_fused(data):
    f = FUSED.unpack_from(data)
    (_, node, flags, br, hr, rssi, n_pers, mw_type, fusion, motion, presence, ts,
     mw_hr, mw_br, mw_dist, mw_targets, mw_conf, _, _) = f
    return {
        "typ": "vitals", "node": node, "rssi": rssi,
        "fw_praesenz": bool(flags & 0x01), "fw_bewegung": bool(flags & 0x04),
        "motion_energy": _finite(motion), "presence_score": _finite(presence),
        "ts_ms": ts,
        "radar_typ": MMWAVE_TYPES.get(mw_type, str(mw_type)),
        "radar_praesenz": bool(flags & 0x08),
        "radar_distanz_cm": _finite(mw_dist),
    }


def _finite(x):
    return x if math.isfinite(x) else 0.0


def amplitudes(iq, n_sub, antenna=0):
    """Betrag je Subträger für eine Antenne aus int8-I/Q-Paaren."""
    start = antenna * n_sub * 2
    raw = struct.unpack_from("<%db" % (n_sub * 2), iq, start)
    return [math.hypot(raw[i], raw[i + 1]) for i in range(0, len(raw), 2)]


# --- Paketbau (für Simulator und Tests) -------------------------------------

def build_csi(node, seq, iq, n_sub, rssi=-50, freq=2437, n_ant=1, flags=0):
    hdr = CSI_HDR.pack(CSI_MAGIC, node, n_ant, n_sub, freq, seq, rssi, -92, 0, flags)
    return hdr + bytes(iq)


def build_vitals(node, presence, motion=False, rssi=-50, motion_energy=0.0,
                 presence_score=0.0, ts_ms=0):
    flags = (0x01 if presence else 0) | (0x04 if motion else 0)
    return VITALS.pack(VITALS_MAGIC, node, flags, 0, 0, rssi, 1 if presence else 0,
                       motion_energy, presence_score, ts_ms, 0)


def build_fused(node, presence, radar_presence, motion=False, rssi=-50,
                distance_cm=0.0, ts_ms=0):
    flags = ((0x01 if presence else 0) | (0x04 if motion else 0)
             | (0x08 if radar_presence else 0))
    return FUSED.pack(FUSED_MAGIC, node, flags, 0, 0, rssi, 1 if presence else 0,
                      2, 50, 0.0, 0.0, ts_ms, 0.0, 0.0, distance_cm,
                      1 if radar_presence else 0, 80, 0, 0)
