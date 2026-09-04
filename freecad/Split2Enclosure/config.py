"""User-editable defaults for the Split2Enclosure dialog."""

import json
import os
import warnings


DEFAULTS = {
    "lip_width": 1.0,
    "lip_height": 2.0,
    "side_clearance": 0.2,
    "depth_clearance": 0.2,
    "draft_angle": 0.0,
    "default_lip_side": "negative",
    "snap_radius": 0.2,
    "snap_clearance": 0.05,
    "snap_position": 0.7,
}

DEFAULT_CONFIG_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "split2enclosure_defaults.json",
)


def _number(name, value, minimum, maximum, inclusive_minimum=True):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("{} must be a number".format(name))
    value = float(value)
    lower_ok = value >= minimum if inclusive_minimum else value > minimum
    if not lower_ok or value > maximum:
        operator = ">=" if inclusive_minimum else ">"
        raise ValueError(
            "{} must be {} {} and <= {}".format(name, operator, minimum, maximum)
        )
    return value


def validate_defaults(values):
    """Return a validated, normalized complete defaults mapping."""

    result = dict(DEFAULTS)
    result["lip_width"] = _number("lip_width", values["lip_width"], 0, 1000, False)
    result["lip_height"] = _number("lip_height", values["lip_height"], 0, 1000, False)
    result["side_clearance"] = _number(
        "side_clearance", values["side_clearance"], 0, 100
    )
    result["depth_clearance"] = _number(
        "depth_clearance", values["depth_clearance"], 0, 100
    )
    result["draft_angle"] = _number("draft_angle", values["draft_angle"], 0, 30)
    if values["default_lip_side"] not in ("negative", "positive"):
        raise ValueError("default_lip_side must be 'negative' or 'positive'")
    result["default_lip_side"] = values["default_lip_side"]
    result["snap_radius"] = _number("snap_radius", values["snap_radius"], 0, 100, False)
    result["snap_clearance"] = _number(
        "snap_clearance", values["snap_clearance"], 0, 100
    )
    result["snap_position"] = _number(
        "snap_position", values["snap_position"], 0.1, 0.9
    )
    return result


def load_defaults(path=None):
    """Load and validate dialog defaults, falling back safely on any error."""
    path = path or DEFAULT_CONFIG_PATH
    try:
        with open(path, "r", encoding="utf-8") as stream:
            loaded = json.load(stream)
        if not isinstance(loaded, dict):
            raise ValueError("the root value must be a JSON object")
        unknown = sorted(set(loaded) - set(DEFAULTS))
        if unknown:
            raise ValueError("unknown setting(s): {}".format(", ".join(unknown)))
        values = dict(DEFAULTS)
        values.update(loaded)
        return validate_defaults(values)
    except FileNotFoundError:
        return dict(DEFAULTS)
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        warnings.warn(
            "Could not load Split2Enclosure defaults from {}: {}. "
            "Using built-in defaults.".format(path, exc),
            RuntimeWarning,
        )
        return dict(DEFAULTS)
