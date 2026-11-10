"""Laden und Prüfen der Testaufbau-Konfiguration (testaufbau.json)."""

import ipaddress
import json
import os
import re

ROLLEN = ("sender", "empfaenger")
LINK_MODI = ("ap", "sender")
MAC_RE = re.compile(r"^[0-9A-Fa-f]{2}(:[0-9A-Fa-f]{2}){5}$")
NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,32}$")

HIER = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STANDARD_DATEI = os.path.join(HIER, "testaufbau.json")


class KonfigFehler(ValueError):
    pass


def laden(pfad=None):
    pfad = pfad or STANDARD_DATEI
    with open(pfad, encoding="utf-8") as f:
        daten = json.load(f)
    pruefen(daten)
    daten["_pfad"] = os.path.abspath(pfad)
    return daten


def pruefen(k):
    netz = k.get("netz") or {}
    for feld in ("ssid", "kanal", "laptop_ip", "udp_port", "passwort_datei"):
        if feld not in netz:
            raise KonfigFehler("netz.%s fehlt" % feld)
    if not 1 <= int(netz["kanal"]) <= 13:
        raise KonfigFehler("netz.kanal muss 1-13 sein (2,4 GHz)")
    ip = ipaddress.ip_address(netz["laptop_ip"])
    if not ip.is_private:
        raise KonfigFehler("netz.laptop_ip muss eine private Adresse sein")
    netzwerk = ipaddress.ip_network(netz.get("erlaubtes_netz", "192.168.178.0/24"), strict=False)
    if ip not in netzwerk:
        raise KonfigFehler("netz.laptop_ip liegt nicht in netz.erlaubtes_netz")
    if not 1024 <= int(netz["udp_port"]) <= 65535:
        raise KonfigFehler("netz.udp_port muss 1024-65535 sein")

    if k.get("link_modus") not in LINK_MODI:
        raise KonfigFehler("link_modus muss 'ap' oder 'sender' sein")
    mac = k.get("sender_mac", "")
    if mac and not MAC_RE.match(mac):
        raise KonfigFehler("sender_mac muss AA:BB:CC:DD:EE:FF sein")
    ap_mac = k.get("ap_mac", "")
    if ap_mac and not MAC_RE.match(ap_mac):
        raise KonfigFehler("ap_mac muss AA:BB:CC:DD:EE:FF sein")

    knoten = k.get("knoten") or []
    if not knoten:
        raise KonfigFehler("keine knoten definiert")
    namen, ids = set(), set()
    for n in knoten:
        name = n.get("name", "")
        if not NAME_RE.match(name):
            raise KonfigFehler("ungültiger Knotenname %r" % name)
        if name in namen:
            raise KonfigFehler("Knotenname doppelt: %s" % name)
        namen.add(name)
        nid = n.get("node_id")
        if not isinstance(nid, int) or not 1 <= nid <= 255:
            raise KonfigFehler("%s: node_id muss 1-255 sein" % name)
        if nid in ids:
            raise KonfigFehler("node_id doppelt: %d" % nid)
        ids.add(nid)
        if n.get("rolle") not in ROLLEN:
            raise KonfigFehler("%s: rolle muss 'sender' oder 'empfaenger' sein" % name)
    if k["link_modus"] == "sender":
        if sum(1 for n in knoten if n["rolle"] == "sender") != 1:
            raise KonfigFehler("link_modus 'sender' braucht genau einen Knoten mit rolle 'sender'")


def knoten_nach_id(k):
    return {n["node_id"]: n for n in k["knoten"]}


def knoten_nach_name(k, name):
    for n in k["knoten"]:
        if n["name"] == name:
            return n
    raise KonfigFehler("unbekannter Knoten %r (vorhanden: %s)"
                       % (name, ", ".join(n["name"] for n in k["knoten"])))


def pfad_relativ(k, pfad):
    """Pfad relativ zur Konfigurationsdatei auflösen, ~ expandieren."""
    pfad = os.path.expanduser(pfad)
    if os.path.isabs(pfad):
        return pfad
    return os.path.normpath(os.path.join(os.path.dirname(k["_pfad"]), pfad))
