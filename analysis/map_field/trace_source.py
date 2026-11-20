"""Read the approved exposed outlines without NumPy, OpenCV, or source images."""

import base64
import hashlib
import json
import math
from pathlib import Path
import re

TOKEN = re.compile(r"[A-Za-z]|[-+]?(?:\d*\.\d+|\d+\.?\d*)(?:[eE][-+]?\d+)?")


def path_points(path):
    """Accept the published absolute M/L/Z polygon format; never infer curves."""
    tokens = TOKEN.findall(path)
    if TOKEN.sub("", path).replace(",", "").strip():
        raise ValueError("Unexpected SVG path characters")
    if not tokens or tokens[0] != "M" or tokens[-1] != "Z":
        raise ValueError("Exposed outline must be an absolute closed M/L/Z path")
    points, i = [], 0
    while i < len(tokens) - 1:
        command = "M" if not points else "L"
        if tokens[i] != command or i + 2 >= len(tokens) - 1:
            raise ValueError("Only one M followed by explicit L pairs is supported")
        point = [float(tokens[i + 1]), float(tokens[i + 2])]
        if not all(math.isfinite(value) for value in point):
            raise ValueError("Non-finite outline coordinate")
        points.append(point)
        i += 3
    if i != len(tokens) - 1 or len(points) < 3:
        raise ValueError("Incomplete exposed polygon")
    return points


def load_trace(path):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    source_sha = data["source"]["sha256"]
    if not re.fullmatch(r"[a-f0-9]{64}", source_sha):
        raise ValueError("Missing canonical trace source hash")
    features = []
    for feature in data["features"]:
        if feature["kind"] != "exposed":
            continue
        if feature.get("closed") is not True:
            raise ValueError("An exposed outline is not closed")
        features.append({"id": feature["id"], "kind": "exposed", "closed": True,
                         "points": path_points(feature["path"])})
    if not features or not any(feature["id"] == "island" for feature in features):
        raise ValueError("Main island/exposed geometry missing")
    return {"source": data["source"], "features": features}


def geometry_sha256(trace):
    payload = json.dumps(trace["features"], ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def check_sources(trace_path, reference_path, manifest_path):
    """Cheap source/basis identity checks; deliberately no numeric dependencies."""
    trace = load_trace(trace_path)
    reference = json.loads(Path(reference_path).read_text(encoding="utf-8"))
    manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    assert trace["source"]["sha256"] == reference["source"]["traceSha256"] == manifest["traceSourceSha256"]
    assert geometry_sha256(trace) == manifest["exposedGeometrySha256"]
    assert len(trace["features"]) == manifest["exposedPolygonCount"]
    assert sum(len(feature["points"]) for feature in trace["features"]) == manifest["exposedPointCount"]
    assert reference["grid"] == manifest["grid"]
    for name, expected in manifest["bases"].items():
        field = reference["fields"][name]
        decoded = base64.b64decode(field["data"], validate=True)
        assert field["encoding"] == "int16le-interleaved-uv"
        assert len(decoded) == reference["grid"]["nx"] * reference["grid"]["ny"] * 2 * 2
        assert field["scale"] == expected["scale"]
        assert hashlib.sha256(decoded).hexdigest() == expected["bytesSha256"]
    return {"passed": True, "exposedPolygons": len(trace["features"]),
            "exposedPoints": sum(len(feature["points"]) for feature in trace["features"]),
            "traceSha256": trace["source"]["sha256"],
            "exposedGeometrySha256": geometry_sha256(trace),
            "checkedBases": list(manifest["bases"])}
