"""One schematic point registry for scoring and rendering.

Coordinates are inherited model offsets, not surveyed GPS positions. Updating
them requires independent calibration and a model version/evaluation review.
"""
import json
from pathlib import Path

CATALOG = json.loads(Path(__file__).with_name("map_points.json").read_text(encoding="utf-8"))
POINTS = CATALOG["points"]
SHELF_RADII_M = {int(k): v for k, v in CATALOG["shelf_radii_m"].items()}
