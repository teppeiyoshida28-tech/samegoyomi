"""Continuous 2-D illustration field; NOT a forecast or a water-speed model.

The approved image trace is read only. A harmonic streamfunction diverts a
uniform example flow around exposed outlines, with floating constant island
boundaries (zero discrete circulation). Near shore, directions are blended
toward the exact outline's tangent. Submerged roots and depths are not inputs.

Requires the offline NumPy/OpenCV environment documented in REPORT_live_ocean_map.md.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import math
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
from trace_source import load_trace
import cv2
import numpy as np

SOURCE = HERE.parents[1] / "engine" / "map_trace.json"
OUTPUT = HERE / "normalized-reference.generated.json"
H = 6.0
NX, NY = 155, 227
SCALE = 32767
CLEARANCE = 9.0
X, Y = np.meshgrid(np.arange(NX) * H, np.arange(NY) * H)
EPS = 1e-8


def polygon_edges(poly):
    return zip(poly, np.roll(poly, -1, axis=0))


def exact_geometry(features):
    polygons = [np.asarray(f["points"], np.float32) for f in features if f["kind"] == "exposed"]
    land = np.zeros((NY, NX), bool)
    min_d2 = np.full((NY, NX), np.inf)
    normal_x, normal_y = np.zeros_like(X), np.zeros_like(Y)
    for poly in polygons:
        lo, hi = poly.min(axis=0), poly.max(axis=0)
        candidates = np.argwhere((X >= lo[0]) & (X <= hi[0]) & (Y >= lo[1]) & (Y <= hi[1]))
        for row, col in candidates:
            if cv2.pointPolygonTest(poly, (float(X[row, col]), float(Y[row, col])), False) >= 0:
                land[row, col] = True
        for a, b in polygon_edges(poly):
            delta = b - a
            denominator = float(delta @ delta)
            if denominator == 0:
                continue
            t = np.clip(((X - a[0]) * delta[0] + (Y - a[1]) * delta[1]) / denominator, 0, 1)
            dx, dy = X - (a[0] + t * delta[0]), Y - (a[1] + t * delta[1])
            d2 = dx * dx + dy * dy
            update = d2 < min_d2
            normal_x[update], normal_y[update] = dx[update], dy[update]
            min_d2[update] = d2[update]
    distance = np.sqrt(min_d2)
    normal_x /= np.maximum(distance, EPS)
    normal_y /= np.maximum(distance, EPS)
    normal_x[land] *= -1
    normal_y[land] *= -1
    signed_distance = np.where(land, -distance, distance)
    blocked = land | (distance <= CLEARANCE)
    count, labels = cv2.connectedComponents(blocked.astype(np.uint8), connectivity=4)
    return polygons, land, signed_distance, normal_x, normal_y, blocked, labels, count - 1


def solve_streamfunction(direction, blocked, labels, component_count):
    angle = math.radians(direction)
    base = np.array([math.sin(angle), -math.cos(angle)])
    psi = base[0] * Y - base[1] * X
    free = ~blocked
    free[[0, -1], :] = False
    free[:, [0, -1]] = False
    yy, xx = np.indices(psi.shape)
    parities = [free & ((xx + yy) % 2 == parity) for parity in (0, 1)]
    conductors = []
    for component in range(1, component_count + 1):
        inside = labels == component
        indices = np.flatnonzero(inside)
        neighbors = np.concatenate([np.flatnonzero(free & np.roll(inside, shift, axis))
                                    for shift, axis in ((-1, 0), (1, 0), (-1, 1), (1, 1))])
        psi.flat[indices] = psi.flat[indices].mean()
        conductors.append((indices, neighbors))
    residual, circulation_residual = math.inf, math.inf
    omega = 1.91
    for iteration in range(1, 12001):
        for parity in parities:
            average = (np.roll(psi, 1, 0) + np.roll(psi, -1, 0) +
                       np.roll(psi, 1, 1) + np.roll(psi, -1, 1)) * .25
            psi[parity] += omega * (average[parity] - psi[parity])
        # Floating equipotential boundaries avoid adding an arbitrary vortex.
        for indices, neighbors in conductors:
            old = psi.flat[indices[0]]
            psi.flat[indices] = old + .7 * (psi.flat[neighbors].mean() - old)
        if iteration % 50 == 0:
            average = (np.roll(psi, 1, 0) + np.roll(psi, -1, 0) +
                       np.roll(psi, 1, 1) + np.roll(psi, -1, 1)) * .25
            residual = float(np.max(np.abs(psi[free] - average[free])))
            circulation_residual = max(abs(float(psi.flat[neighbors].mean() - psi.flat[indices[0]]))
                                       for indices, neighbors in conductors)
            if residual < 0.0002 and circulation_residual < 0.0002:
                break
    assert residual < .002, f"Streamfunction did not converge: {residual}"
    raw = np.stack([np.gradient(psi, H, axis=0), -np.gradient(psi, H, axis=1)], axis=-1)
    return raw, base, {"iterations": iteration, "maximumLaplaceResidual": round(residual, 8),
                       "maximumFloatingBoundaryResidual": round(circulation_residual, 8)}


def displayed_directions(raw, signed_distance, normal_x, normal_y, blocked):
    vector = raw / np.maximum(np.linalg.norm(raw, axis=-1, keepdims=True), EPS)
    # Symmetric tangent projection also works when the complete field is reversed.
    t = np.clip((signed_distance - 18) / 54, 0, 1)
    weight = 1 - t * t * (3 - 2 * t)
    normal_component = vector[..., 0] * normal_x + vector[..., 1] * normal_y
    vector[..., 0] -= normal_component * normal_x * weight
    vector[..., 1] -= normal_component * normal_y * weight
    norm = np.linalg.norm(vector, axis=-1, keepdims=True)
    vector = np.divide(vector, norm, out=np.zeros_like(vector), where=norm > 1e-6)
    vector[blocked] = 0
    quantized = np.rint(vector * SCALE).astype("<i2")
    return quantized, quantized.astype(float) / SCALE


def sample(field, positions):
    """Reference bilinear sampler followed by normalization, for fixed art speed."""
    gx, gy = positions[:, 0] / H, positions[:, 1] / H
    valid = (gx >= 0) & (gx < NX - 1) & (gy >= 0) & (gy < NY - 1)
    ix = np.clip(np.floor(gx).astype(int), 0, NX - 2)
    iy = np.clip(np.floor(gy).astype(int), 0, NY - 2)
    tx, ty = (gx - ix)[:, None], (gy - iy)[:, None]
    value = ((1 - ty) * ((1 - tx) * field[iy, ix] + tx * field[iy, ix + 1]) +
             ty * ((1 - tx) * field[iy + 1, ix] + tx * field[iy + 1, ix + 1]))
    norm = np.linalg.norm(value, axis=1)
    valid &= norm > .025
    value = np.divide(value, norm[:, None], out=np.zeros_like(value), where=norm[:, None] > .025)
    return value, valid


def cross(a, b, c):
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


def intersects(a, b, c, d):
    if (max(a[0], b[0]) < min(c[0], d[0]) or max(c[0], d[0]) < min(a[0], b[0]) or
            max(a[1], b[1]) < min(c[1], d[1]) or max(c[1], d[1]) < min(a[1], b[1])):
        return False
    return cross(a, b, c) * cross(a, b, d) <= 0 and cross(c, d, a) * cross(c, d, b) <= 0


def trajectory_check(field, polygons):
    sx, sy = np.meshgrid(np.arange(183, 918, 30), np.arange(525, 1345, 30))
    positions = np.column_stack([sx.ravel(), sy.ravel()]).astype(float)
    active = np.ones(len(positions), bool)
    polygon_info = [(p, p.min(axis=0), p.max(axis=0)) for p in polygons]
    initial_land = 0
    for i, p in enumerate(positions):
        if any(cv2.pointPolygonTest(poly, tuple(p), False) >= 0 for poly in polygons):
            active[i] = False
            initial_land += 1
    _, initial_valid = sample(field, positions)
    initial_field_invalid = int(np.count_nonzero(active & ~initial_valid))
    active &= initial_valid
    stats = {"seedCount": len(positions), "initialLandSeedsExcluded": initial_land,
             "initialZeroFieldSeedsExcluded": initial_field_invalid,
             "rk2StepPx": 2.0, "maximumStepsPerSeed": 700,
             "testedSegments": 0, "landCrossingCount": 0, "zeroVectorStops": 0,
             "boundaryExits": 0,
             "minimumSampledCoastDistancePx": 9999.0}
    for _ in range(700):
        indices = np.flatnonzero(active)
        if len(indices) == 0:
            break
        old = positions[indices]
        first, valid1 = sample(field, old)
        mid = old + first
        second, valid2 = sample(field, mid)
        new = old + second * 2
        valid = valid1 & valid2
        boundary_exit = ((mid[:, 0] < 165) | (mid[:, 0] > 921) | (mid[:, 1] < 510) | (mid[:, 1] >= 1356) |
                         (new[:, 0] < 165) | (new[:, 0] > 921) | (new[:, 1] < 510) | (new[:, 1] >= 1356))
        stats["zeroVectorStops"] += int(np.count_nonzero(~valid & ~boundary_exit))
        stats["boundaryExits"] += int(np.count_nonzero(boundary_exit))
        active[indices[~valid | boundary_exit]] = False
        for poly, lo, hi in polygon_info:
            # Exact segment tests only need polygons whose boxes meet the segment.
            candidates = np.flatnonzero(valid & (np.maximum(old[:, 0], new[:, 0]) >= lo[0]) &
                                       (np.minimum(old[:, 0], new[:, 0]) <= hi[0]) &
                                       (np.maximum(old[:, 1], new[:, 1]) >= lo[1]) &
                                       (np.minimum(old[:, 1], new[:, 1]) <= hi[1]))
            for j in candidates:
                if (cv2.pointPolygonTest(poly, tuple(new[j]), False) >= 0 or
                        any(intersects(old[j], new[j], a, b) for a, b in polygon_edges(poly))):
                    stats["landCrossingCount"] += 1
                    active[indices[j]] = False
                    valid[j] = False
            nearby = np.flatnonzero(valid & (new[:, 0] >= lo[0] - 20) & (new[:, 0] <= hi[0] + 20) &
                                    (new[:, 1] >= lo[1] - 20) & (new[:, 1] <= hi[1] + 20))
            for j in nearby:
                distance = -cv2.pointPolygonTest(poly, tuple(new[j]), True)
                stats["minimumSampledCoastDistancePx"] = min(stats["minimumSampledCoastDistancePx"], distance)
        stats["testedSegments"] += int(np.count_nonzero(valid))
        positions[indices] = new
        visible = (new[:, 0] >= 165) & (new[:, 0] <= 921) & (new[:, 1] >= 510) & (new[:, 1] <= 1356)
        active[indices[~visible]] = False
    stats["minimumSampledCoastDistancePx"] = round(stats["minimumSampledCoastDistancePx"], 3)
    stats["activeAtEnd"] = int(np.count_nonzero(active))
    return stats


def coast_check(field, polygons):
    positions, normals = [], []
    for poly in polygons:
        for a, b in polygon_edges(poly):
            delta = b - a
            span = float(np.linalg.norm(delta))
            if span < 3:
                continue
            normal = np.array([-delta[1], delta[0]]) / span
            center = (a + b) / 2
            if cv2.pointPolygonTest(poly, tuple(center + normal), False) >= 0:
                normal *= -1
            for t in np.linspace(.2, .8, max(1, math.ceil(span / 12))):
                q = a * (1 - t) + b * t + normal * 18
                if q[1] >= 510 and not any(cv2.pointPolygonTest(other, tuple(q), False) >= 0 for other in polygons):
                    positions.append(q)
                    normals.append(normal)
    # A sample offset from one short edge can be nearer another edge or rock.
    # Evaluate the normal of the *closest actual outline*, not that seed edge.
    starts = np.concatenate(polygons)
    ends = np.concatenate([np.roll(poly, -1, axis=0) for poly in polygons])
    delta = ends - starts
    denominator = np.maximum(np.sum(delta * delta, axis=1), EPS)
    nearest_normals, nearest_distances = [], []
    for p in positions:
        t = np.clip(np.sum((p - starts) * delta, axis=1) / denominator, 0, 1)
        displacement = p - (starts + t[:, None] * delta)
        d2 = np.sum(displacement * displacement, axis=1)
        i = int(np.argmin(d2))
        distance = math.sqrt(float(d2[i]))
        nearest_distances.append(distance)
        nearest_normals.append(displacement[i] / max(distance, EPS))
    value, valid = sample(field, np.asarray(positions))
    valid &= np.asarray(nearest_distances) >= 12
    normal_components = np.sum(value * np.asarray(nearest_normals), axis=1)[valid]
    return {"offsetFromSourceOutlinePx": 18, "sampleCount": len(positions),
            "normalReference": "closest actual outline; omit samples less than 12px from another edge",
            "validSamples": int(np.count_nonzero(valid)),
            "maximumAbsoluteNormalComponent": round(float(np.max(np.abs(normal_components))), 4),
            "medianAbsoluteNormalComponent": round(float(np.median(np.abs(normal_components))), 4),
            "p95AbsoluteNormalComponent": round(float(np.percentile(np.abs(normal_components), 95)), 4)}


def validate_field(raw, field, base, blocked, land, polygons, main_island):
    free_interior = cv2.erode((~blocked).astype(np.uint8), np.ones((5, 5), np.uint8)).astype(bool)
    free_interior[[0, -1], :] = False
    free_interior[:, [0, -1]] = False
    divergence = np.gradient(raw[..., 0], H, axis=1) + np.gradient(raw[..., 1], H, axis=0)
    display_divergence = np.gradient(field[..., 0], H, axis=1) + np.gradient(field[..., 1], H, axis=0)
    lateral = np.array([-base[1], base[0]])
    along = main_island @ base
    across = main_island @ lateral
    upstream = along.min() - 100
    qs = np.linspace(across.min(), across.max(), 13)
    positions = np.asarray([base * upstream + lateral * q for q in qs])
    directions, valid = sample(field, positions)
    angles = np.degrees(np.arccos(np.clip(directions[valid] @ base, -1, 1)))
    return {"nonzeroLandNodeCount": int(np.count_nonzero(np.linalg.norm(field[land], axis=1))),
            "rawStreamfunctionDivergenceRms": float(np.sqrt(np.mean(divergence[free_interior] ** 2))),
            "displayDirectionDivergenceRmsPerPixel": round(float(np.sqrt(np.mean(display_divergence[free_interior] ** 2))), 6),
            "upstreamDeflection": {"upstreamOffsetPx": 100, "validSamples": int(np.count_nonzero(valid)),
                                   "maximumDegrees": round(float(angles.max()), 3),
                                   "medianDegrees": round(float(np.median(angles)), 3)},
            "coast": coast_check(field, polygons),
            "trajectory": trajectory_check(field, polygons)}


def main():
    global SOURCE, OUTPUT
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trace", type=Path, default=SOURCE)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()
    SOURCE, OUTPUT = args.trace.resolve(), args.output.resolve()
    source_bytes = SOURCE.read_bytes()
    data = load_trace(SOURCE)
    geometry = exact_geometry(data["features"])
    polygons, land, distance, normal_x, normal_y, blocked, labels, component_count = geometry
    main_island = next(np.asarray(f["points"], float) for f in data["features"] if f["id"] == "island")
    fields, checks, decoded = {}, {}, {}
    for name, direction in (("ne", 45), ("west", 270)):
        raw, base, solver = solve_streamfunction(direction, blocked, labels, component_count)
        quantized, field = displayed_directions(raw, distance, normal_x, normal_y, blocked)
        decoded[name] = field
        fields[name] = {"encoding": "int16le-interleaved-uv", "scale": SCALE,
                        "data": base64.b64encode(quantized.tobytes(order="C")).decode("ascii")}
        checks[name] = {"solver": solver, **validate_field(raw, field, base, blocked, land, polygons, main_island)}
        print(name, json.dumps(checks[name], ensure_ascii=False), flush=True)
    checks["sw"] = {"derivedByNegation": "ne", "trajectory": trajectory_check(-decoded["ne"], polygons),
                    "coast": coast_check(-decoded["ne"], polygons)}
    print("sw", json.dumps(checks["sw"], ensure_ascii=False), flush=True)
    result = {
        "schemaVersion": 1, "purpose": "continuous_2d_current_illustration_not_forecast",
        "coordinateSystem": "source_image_pixels_x_right_y_down",
        "grid": {"origin": [0, 0], "spacing": H, "nx": NX, "ny": NY, "order": "row-major"},
        "viewBox": [165, 270, 756, 1086], "drawBounds": [165, 510, 756, 846],
        "source": {"file": SOURCE.name, "sha256": data["source"]["sha256"],
                   "traceSha256": data["source"]["sha256"],
                   "width": 921, "height": 1356, "georeferenced": False},
        "fields": fields,
        "cases": {"ne": {"field": "ne", "sign": 1, "direction": 45},
                  "sw": {"field": "ne", "sign": -1, "direction": 225},
                  "west": {"field": "west", "sign": 1, "direction": 270}},
        "landMask": {"encoding": "uint8", "data": base64.b64encode(land.astype(np.uint8).tobytes()).decode("ascii")},
        "model": {"method": "harmonic_streamfunction_with_floating_island_boundaries_and_nearshore_tangent_blend",
                  "obstacles": "exposed source outlines only", "exposedPolygonCount": len(polygons),
                  "solverComponentCount": component_count, "coastClearancePx": CLEARANCE,
                  "tangentBlendFullBelowPx": 18, "tangentBlendEndsAtPx": 72,
                  "usesDepth": False, "usesSubmergedRoots": False, "usesLiveCurrentData": False,
                  "physicalSpeedUnits": None, "valuesAreIllustrationOnly": True,
                  "notAConservativePhysicalVelocityField": True},
        "rendering": {"sample": "bilinear then normalize; case.sign multiplies both components",
                      "zeroVector": "invalid on land, coast-clearance buffer or a stagnation point; do not seed there",
                      "integration": "RK2, maximum 2 source pixels per substep, clamp frame delta",
                      "clipping": "mask all exposed outlines plus y<510, including the complete particle trail",
                      "speed": "one fixed visual speed; no kt or depth-dependent speed",
                      "north": "source-image north, not a verified geographic bearing"},
        "validation": {"sourceUnchanged": SOURCE.read_bytes() == source_bytes,
                       "landNodeCount": int(np.count_nonzero(land)),
                       "checks": checks},
    }
    result["validation"]["passed"] = (result["validation"]["sourceUnchanged"] and
                                       all(c["trajectory"]["landCrossingCount"] == 0 for c in checks.values()) and
                                       all(checks[n]["nonzeroLandNodeCount"] == 0 for n in ("ne", "west")))
    encoded = json.dumps(result, ensure_ascii=False, separators=(",", ":")) + "\n"
    assert len(encoded.encode("utf-8")) < 500_000
    assert result["validation"]["passed"], "Field failed geometry validation; not written."
    OUTPUT.write_text(encoded, encoding="utf-8")
    print(f"Wrote {OUTPUT.name}: {len(encoded.encode('utf-8')):,} bytes", flush=True)


if __name__ == "__main__":
    main()
