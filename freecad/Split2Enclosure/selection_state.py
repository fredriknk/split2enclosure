"""Stable, conservative persistence for per-contour GUI assignments."""

import json
import math


CONTOUR_STATE_VERSION = 1
_VALID_SIDES = {"negative", "positive", "none"}


def _vector_values(vector):
    return [float(vector.x), float(vector.y), float(vector.z)]


def contour_fingerprint(contour):
    """Describe a contour without relying on FreeCAD's edge/face numbering."""

    bounds = contour.wire.BoundBox
    center = contour.wire.CenterOfMass
    return {
        "kind": str(contour.kind),
        "area": float(contour.area),
        "length": float(contour.length),
        "center": _vector_values(center),
        "size": [
            float(bounds.XLength),
            float(bounds.YLength),
            float(bounds.ZLength),
        ],
    }


def encode_contour_state(contours, sides, snaps):
    """Serialize all contour choices together with geometry fingerprints."""

    records = []
    for contour in contours:
        side = str(sides.get(contour.index, "none"))
        if side not in _VALID_SIDES:
            side = "none"
        records.append(
            {
                "fingerprint": contour_fingerprint(contour),
                "side": side,
                "snap": bool(snaps.get(contour.index, False)),
            }
        )
    return json.dumps(
        {"version": CONTOUR_STATE_VERSION, "contours": records},
        separators=(",", ":"),
        sort_keys=True,
    )


def _relative_difference(first, second, floor=1e-9):
    return abs(float(first) - float(second)) / max(
        abs(float(first)), abs(float(second)), floor
    )


def _distance(first, second):
    return math.sqrt(sum((float(a) - float(b)) ** 2 for a, b in zip(first, second)))


def _model_scale(fingerprints):
    points = []
    for fingerprint in fingerprints:
        center = fingerprint["center"]
        size = fingerprint["size"]
        points.extend(
            [
                [center[axis] - size[axis] * 0.5 for axis in range(3)],
                [center[axis] + size[axis] * 0.5 for axis in range(3)],
            ]
        )
    if not points:
        return 1.0
    spans = [
        max(point[axis] for point in points) - min(point[axis] for point in points)
        for axis in range(3)
    ]
    return max(math.sqrt(sum(span * span for span in spans)), 1.0)


def _match_score(saved, current, scale):
    if saved.get("kind") != current.get("kind"):
        return None
    try:
        area_delta = _relative_difference(saved["area"], current["area"])
        length_delta = _relative_difference(saved["length"], current["length"])
        size_deltas = [
            _relative_difference(first, second, floor=max(scale * 1e-5, 1e-6))
            for first, second in zip(saved["size"], current["size"])
        ]
        center_delta = _distance(saved["center"], current["center"]) / scale
    except (KeyError, TypeError, ValueError, ZeroDivisionError):
        return None

    # These gates intentionally prefer dropping a choice over applying it to
    # the wrong loop after a substantial topology change.
    if area_delta > 0.20 or length_delta > 0.15:
        return None
    if max(size_deltas) > 0.20 or center_delta > 0.05:
        return None
    return area_delta + length_delta + max(size_deltas) + center_delta * 4.0


def match_contour_state(contours, encoded):
    """Restore confident one-to-one matches.

    Returns ``(sides, snaps, report)``. Unmatched or ambiguous records are
    omitted, allowing the caller's current defaults to remain in force.
    """

    if not encoded:
        return {}, {}, {"matched": 0, "missing": 0, "ambiguous": 0}
    try:
        state = json.loads(str(encoded))
        if int(state.get("version", 0)) != CONTOUR_STATE_VERSION:
            raise ValueError("unsupported contour-state version")
        records = list(state["contours"])
        saved_fingerprints = [record["fingerprint"] for record in records]
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return {}, {}, {"matched": 0, "missing": 1, "ambiguous": 0}

    current = {contour.index: contour_fingerprint(contour) for contour in contours}
    scale = _model_scale(saved_fingerprints + list(current.values()))
    proposals = []
    missing = 0
    ambiguous = 0
    for record_index, fingerprint in enumerate(saved_fingerprints):
        candidates = sorted(
            (
                (score, index)
                for index, candidate in current.items()
                for score in [_match_score(fingerprint, candidate, scale)]
                if score is not None
            ),
            key=lambda item: item[0],
        )
        if not candidates:
            missing += 1
            continue
        if (
            len(candidates) > 1
            and candidates[1][0] - candidates[0][0] <= 0.04
            and candidates[1][0] <= candidates[0][0] * 1.5 + 1e-6
        ):
            ambiguous += 1
            continue
        proposals.append((candidates[0][0], record_index, candidates[0][1]))

    # Also reject a reverse ambiguity: two saved loops both claiming the same
    # current loop. Applying whichever record happens to sort first would be a
    # topology-dependent guess.
    claimed = {}
    for proposal in proposals:
        claimed.setdefault(proposal[2], []).append(proposal)

    safe_proposals = []
    for matches in claimed.values():
        matches.sort()
        if len(matches) > 1:
            ambiguous += len(matches)
            continue
        safe_proposals.extend(matches)

    sides = {}
    snaps = {}
    for _score, record_index, contour_index in sorted(safe_proposals):
        record = records[record_index]
        side = str(record.get("side", "none"))
        if side not in _VALID_SIDES:
            side = "none"
        sides[contour_index] = side
        snaps[contour_index] = bool(record.get("snap", False))
    return sides, snaps, {
        "matched": len(sides),
        "missing": missing,
        "ambiguous": ambiguous,
    }
