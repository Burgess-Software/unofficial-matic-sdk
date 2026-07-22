"""Decode private collection-response files into canonical map mosaics."""

from __future__ import annotations

import sys
from pathlib import Path

from matic_sdk.maps import MapCollectionState, build_mosaics, save_mosaics


def main() -> None:
    if len(sys.argv) < 3:
        raise SystemExit("usage: decode_maps.py OUTPUT_DIR CAPTURE.pb [CAPTURE.pb ...]")
    state = MapCollectionState()
    for filename in sys.argv[2:]:
        state.apply_message(Path(filename).read_bytes())
    for path in save_mosaics(build_mosaics(state.tiles), Path(sys.argv[1])):
        print(path)


if __name__ == "__main__":
    main()
