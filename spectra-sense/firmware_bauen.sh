#!/usr/bin/env bash
# Baut die RuView-ESP32-S3-Firmware für das ESP32-S3-DevKitC-1 (ohne Display)
# im offiziellen ESP-IDF-Docker-Container. Ergebnis: firmware/esp32-csi-node/build/
#
# Die DevKitC-Überlagerung ist Pflicht: die Standard-Firmware erkennt auf dem
# DevKitC fälschlich ein Display und liefert dann 0 CSI-Pakete/s
# (siehe firmware/esp32-csi-node/sdkconfig.defaults.devkitc).
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
FW="$REPO/firmware/esp32-csi-node"
DEFAULTS="sdkconfig.defaults;sdkconfig.defaults.devkitc"

command -v docker >/dev/null || { echo "Docker fehlt: sudo apt install docker.io" >&2; exit 2; }

docker run --rm -v "$FW:/project" -w /project espressif/idf:v5.4 bash -c "
  set -e
  rm -rf build sdkconfig
  idf.py -DSDKCONFIG_DEFAULTS='$DEFAULTS' set-target esp32s3
  idf.py -DSDKCONFIG_DEFAULTS='$DEFAULTS' build
  chown -R $(id -u):$(id -g) build sdkconfig managed_components 2>/dev/null || true
"

for f in bootloader/bootloader.bin partition_table/partition-table.bin ota_data_initial.bin esp32-csi-node.bin; do
  test -s "$FW/build/$f" || { echo "fehlt: build/$f" >&2; exit 1; }
done
grep -q '^# CONFIG_DISPLAY_ENABLE is not set' "$FW/sdkconfig" \
  || { echo "WARNUNG: Display-Unterstützung ist aktiv - DevKitC liefert dann keine CSI-Daten" >&2; exit 1; }
echo "Fertig: $FW/build (DevKitC-Variante, Display aus)"
