"""Build compact, linear illustration bases for any source-image direction.

This preserves generate-current-field.py and the approved trace. It reuses that
generator's exact geometry, harmonic solver, and RK2 geometry checks, but does
NOT normalize the individual basis vectors. A consumer bilinearly samples both
bases, combines them, and only then normalizes the resulting direction.

Reproduction requires the companion generate-current-field.py, NumPy, and
OpenCV in the separate offline environment. No live data, submerged
root, depth, or physical local current speed enters this illustration field.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import importlib.util
import json
import math
from pathlib import Path

HERE = Path(__file__).resolve().parent
BASE_SOURCE = HERE / "generate-current-field.py"
OUTPUT = HERE / "map_flow_field.generated.json"
DEFAULT_TRACE = HERE.parents[1] / "engine" / "map_trace.json"
DEFAULT_REFERENCE = HERE.parents[1] / "engine" / "map_flow_field.json"
MANIFEST = HERE / "manifest.json"
from trace_source import load_trace, geometry_sha256, check_sources
base = None
np = None


def configure_solver(trace_path):
    global base, np
    spec = importlib.util.spec_from_file_location("current_field_geometry", BASE_SOURCE)
    base = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(base)
    base.SOURCE = trace_path
    np = base.np


def linear_tangent_projection(raw, distance, normal_x, normal_y, blocked):
    """A fixed linear operator: projection commutes with basis combination."""
    projected = raw.copy()
    t = np.clip((distance - 18) / 54, 0, 1)
    weight = 1 - t * t * (3 - 2 * t)
    normal_component = raw[..., 0] * normal_x + raw[..., 1] * normal_y
    projected[..., 0] -= normal_component * normal_x * weight
    projected[..., 1] -= normal_component * normal_y * weight
    projected[blocked] = 0
    return projected


def encode_basis(projected):
    maximum = float(np.max(np.abs(projected)))
    scale = 32767.0 / maximum
    packed = np.rint(projected * scale).astype("<i2")
    decoded = packed.astype(float) / scale
    return {"encoding": "int16le-interleaved-uv", "scale": scale,
            "data": base64.b64encode(packed.tobytes(order="C")).decode("ascii")}, decoded


def coefficients(degrees):
    radians = math.radians(degrees)
    return math.sqrt(2) * math.cos(radians), math.cos(radians) - math.sin(radians)


def source_trace_unchanged(source_bytes):
    return base.SOURCE.read_bytes() == source_bytes


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trace", type=Path, default=DEFAULT_TRACE)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--reference", type=Path, default=DEFAULT_REFERENCE)
    parser.add_argument("--check-source", action="store_true", help="Check trace/packed basis identity without numeric packages or solving")
    parser.add_argument("--verify-basis", action="store_true", help="Require regenerated basis bytes and scales to match --reference")
    args = parser.parse_args()
    if args.check_source:
        print(json.dumps(check_sources(args.trace, args.reference, MANIFEST), ensure_ascii=False, indent=2))
        return
    configure_solver(args.trace.resolve())
    source_bytes = base.SOURCE.read_bytes()
    companion_bytes = BASE_SOURCE.read_bytes()
    trace = load_trace(base.SOURCE)
    polygons, land, distance, normal_x, normal_y, blocked, labels, component_count = base.exact_geometry(trace["features"])
    fields, decoded, solvers = {}, {}, {}
    for name, degrees in (("ne", 45), ("west", 270)):
        raw, _, solver = base.solve_streamfunction(degrees, blocked, labels, component_count)
        projected = linear_tangent_projection(raw, distance, normal_x, normal_y, blocked)
        fields[name], decoded[name] = encode_basis(projected)
        solvers[name] = solver
        assert not np.any(decoded[name][land])
        print(f"Solved {name}: {solver}", flush=True)

    checks = []
    minimum_coast = float("inf")
    total_segments = 0
    for degrees in range(0, 360, 15):
        a, b = coefficients(degrees)
        # Bilinear sampling and this linear combination commute. No nodewise
        # normalization occurs here, nor when the bases are serialized.
        combined = decoded["ne"] * a + decoded["west"] * b
        expected = np.array([math.sin(math.radians(degrees)), -math.cos(math.radians(degrees))])
        corners = combined[[0, 0, -1, -1], [0, -1, 0, -1]]
        corner_error = float(np.max(np.linalg.norm(corners - expected, axis=1)))
        assert corner_error < .001, f"Wrong image-axis bearing at {degrees}: {corner_error}"
        trajectory = base.trajectory_check(combined, polygons)
        # This counts attempted intersections before any production collision
        # guard. The browser must still retain its exact Path2D land/trail mask.
        assert trajectory["landCrossingCount"] == 0, f"Land crossing at {degrees}: {trajectory}"
        minimum_coast = min(minimum_coast, trajectory["minimumSampledCoastDistancePx"])
        total_segments += trajectory["testedSegments"]
        checks.append({"degrees": degrees, "segments": trajectory["testedSegments"],
                       "landCrossingsBeforeGuard": trajectory["landCrossingCount"],
                       "stagnationStops": trajectory["zeroVectorStops"],
                       "boundaryExits": trajectory["boundaryExits"],
                       "activeAtEnd": trajectory["activeAtEnd"],
                       "minimumCoastDistancePx": trajectory["minimumSampledCoastDistancePx"],
                       "cornerVectorError": round(corner_error, 8)})
        print(json.dumps(checks[-1]), flush=True)

    # Directly test common algebra identities on every quantized grid vector.
    assert np.allclose(decoded["west"] * -1, decoded["ne"] * coefficients(90)[0] + decoded["west"] * coefficients(90)[1])
    assert np.allclose(-decoded["ne"], decoded["ne"] * coefficients(225)[0] + decoded["west"] * coefficients(225)[1])
    assert source_trace_unchanged(source_bytes)
    assert BASE_SOURCE.read_bytes() == companion_bytes
    result = {
        "schemaVersion": 2,
        "purpose": "linear_2d_illustration_basis_not_local_current_forecast",
        "coordinateSystem": "source_image_pixels_x_right_y_down",
        "grid": {"origin": [0, 0], "spacing": base.H, "nx": base.NX, "ny": base.NY, "order": "row-major"},
        "viewBox": [165, 270, 756, 1086], "drawBounds": [165, 510, 756, 846],
        "source": {"traceFile": base.SOURCE.name, "traceSha256": trace["source"]["sha256"],
                   "publicTraceFileSha256": hashlib.sha256(source_bytes).hexdigest(),
                   "exposedGeometrySha256": geometry_sha256(trace),
                   "width": 921, "height": 1356, "georeferenced": False,
                   "baseGeneratorSha256": hashlib.sha256(companion_bytes).hexdigest(),
                   "generatorSha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()},
        "fields": fields,
        "basis": {"ne": [math.sqrt(.5), -math.sqrt(.5)], "west": [-1, 0],
                  "normalizedAtNodes": False,
                  "coefficients": {"ne": "sqrt(2)*cos(theta)", "west": "cos(theta)-sin(theta)"},
                  "theta": "radians clockwise from source-image north; TOWARD"},
        "model": {"method": "harmonic_streamfunction_then_linear_tangent_projection",
                  "exposedPolygonCount": len(polygons), "solverComponentCount": component_count,
                  "coastClearancePx": base.CLEARANCE, "tangentFullBelowPx": 18, "tangentEndsAtPx": 72,
                  "usesDepth": False, "usesSubmergedRoots": False, "usesLiveCurrentData": False,
                  "basisMagnitudeIsNotPhysicalSpeed": True},
        "rendering": {"sample": "bilinear both bases; combine with coefficients; normalize once at the end",
                      "minimumCombinedNorm": .025, "maximumRk2StepPx": 1.8,
                      "zeroVector": "do not spawn; fade and respawn only if integration reaches a stagnation point",
                      "mask": "all exact exposed outlines and y<510; include particle trail and antialiased caps",
                      "kt": "use one regional value only for uniform color/trail/animation style, never basis magnitude"},
        "validation": {"passed": True, "sourceUnchanged": True, "companionGeneratorUnchanged": True,
                       "directions": 24, "degreesStep": 15, "seedsPerDirection": 700,
                       "initialLandSeedsExcluded": 31, "rk2ValidationStepPx": 2.0,
                       "totalTestedSegments": total_segments, "landCrossingsBeforeGuard": 0,
                       "minimumCoastDistancePx": minimum_coast, "solvers": solvers, "checks": checks},
    }
    if args.verify_basis:
        reference = json.loads(args.reference.read_text(encoding="utf-8"))
        assert reference["source"]["traceSha256"] == trace["source"]["sha256"]
        assert result["fields"] == reference["fields"], "Regenerated basis bytes/scales differ from reference"
        result["validation"]["basisBytesMatchReference"] = True
    encoded = json.dumps(result, ensure_ascii=False, separators=(",", ":")) + "\n"
    assert len(encoded.encode("utf-8")) < 500_000
    args.output.write_text(encoded, encoding="utf-8")
    print(f"Wrote {args.output.name}: {len(encoded.encode('utf-8')):,} bytes; {total_segments:,} exact-mask-tested segments", flush=True)


if __name__ == "__main__":
    main()
