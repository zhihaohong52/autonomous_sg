#!/usr/bin/env bash
# Downloads the challenge datasets too large to commit (GitHub caps files at 100 MB).
# Source: AUTONOMOUS SG 2026 shared Drive folder 1wOt4Q_HFT3vzLCwKaq9AYqXGi4O_vhjN
set -euo pipefail
cd "$(dirname "$0")/.."

fetch() {
  local id=$1 out=$2
  if [[ -s $out ]]; then echo "skip $out (exists)"; return; fi
  echo "fetch $out"
  curl -fL --progress-bar -o "$out" "https://drive.usercontent.google.com/download?id=${id}&export=download&confirm=t"
}

fetch 1KQmmvlu6UseG_wKY5M4-uXK8H3Ul2p7L data/challenge1/train_data.csv
fetch 19EXPtnNUTIb6ETLMwOI0Z_3-8HzBCAHU data/challenge1/test_data.csv
fetch 1O9gcWoM_irmD14alDn_TZzKpyaKAJM2J data/challenge5/Flight_Data_With_Weather_Final.csv
