"""FreeCAD-document persistence for per-source enclosure defaults."""

import warnings

import FreeCAD as App

from .config import DEFAULTS, validate_defaults


SETTINGS_MARKER = "Split2Enclosure.SourceDefaults"
SETTINGS_SCHEMA_VERSION = 1

_VALUE_PROPERTIES = (
    ("lip_width", "LipWidth", "App::PropertyLength", "Joint defaults"),
    ("lip_height", "LipHeight", "App::PropertyLength", "Joint defaults"),
    ("side_clearance", "SideClearance", "App::PropertyLength", "Joint defaults"),
    ("depth_clearance", "DepthClearance", "App::PropertyLength", "Joint defaults"),
    ("draft_angle", "DraftAngle", "App::PropertyAngle", "Joint defaults"),
    ("snap_radius", "SnapRadius", "App::PropertyLength", "Snap defaults"),
    ("snap_clearance", "SnapClearance", "App::PropertyLength", "Snap defaults"),
    ("snap_position", "SnapPosition", "App::PropertyFloat", "Snap defaults"),
)


def settings_owner(source):
    """Use a containing Part Design Body when possible, else the source."""

    if source is None:
        return None
    if getattr(source, "TypeId", "") == "PartDesign::Body":
        return source
    for parent in getattr(source, "InList", []):
        if getattr(parent, "TypeId", "") == "PartDesign::Body":
            return parent
    return source


def find_source_settings(source):
    """Find the VarSet associated with ``source`` or its containing Body."""

    owner = settings_owner(source)
    if owner is None or getattr(owner, "Document", None) is None:
        return None
    for obj in owner.Document.Objects:
        if getattr(obj, "TypeId", "") != "App::VarSet":
            continue
        if "SettingsType" not in getattr(obj, "PropertiesList", []):
            continue
        if obj.SettingsType != SETTINGS_MARKER:
            continue
        if getattr(obj, "Source", None) == owner:
            return obj
    return None


def _quantity_value(value):
    return float(getattr(value, "Value", value))


def load_source_defaults(source, base_defaults=None):
    """Merge a source VarSet over JSON/built-in defaults.

    Returns ``(defaults, settings_object)``. A damaged or outdated VarSet is
    ignored as a whole so it cannot prevent the enclosure dialog from opening.
    """

    base = dict(DEFAULTS if base_defaults is None else base_defaults)
    settings = find_source_settings(source)
    if settings is None:
        return validate_defaults(base), None
    try:
        loaded = dict(base)
        for key, property_name, _property_type, _group in _VALUE_PROPERTIES:
            loaded[key] = _quantity_value(getattr(settings, property_name))
        side = str(settings.DefaultLipSide).lower()
        loaded["default_lip_side"] = side
        return validate_defaults(loaded), settings
    except (AttributeError, TypeError, ValueError) as exc:
        warnings.warn(
            "Could not load {}: {}. Using global defaults.".format(
                getattr(settings, "Label", "Split2Enclosure source defaults"),
                exc,
            ),
            RuntimeWarning,
        )
        return validate_defaults(base), settings


def _parameter_defaults(parameters):
    values = {
        "lip_width": parameters["lip_width"],
        "lip_height": parameters["lip_height"],
        "side_clearance": parameters["clearance"],
        "depth_clearance": parameters["vertical_clearance"],
        "draft_angle": parameters["draft_angle"],
        "snap_radius": parameters["snap_radius"],
        "snap_clearance": parameters["snap_clearance"],
        "snap_position": parameters["snap_position"],
        "default_lip_side": parameters["lip_on"],
    }
    return validate_defaults(values)


def save_source_defaults(source, parameters):
    """Create or update the document VarSet linked to ``source``/its Body."""

    owner = settings_owner(source)
    if owner is None or getattr(owner, "Document", None) is None:
        raise ValueError("The enclosure source must belong to a FreeCAD document.")
    values = _parameter_defaults(parameters)
    settings = find_source_settings(owner)
    if settings is None:
        name = "Split2EnclosureSettings_{}".format(owner.Name)
        settings = owner.Document.addObject("App::VarSet", name)
        settings.Label = "Enclosure defaults — {}".format(owner.Label)
        settings.addProperty(
            "App::PropertyString", "SettingsType", "Split2Enclosure"
        )
        settings.addProperty(
            "App::PropertyInteger", "SchemaVersion", "Split2Enclosure"
        )
        settings.addProperty(
            "App::PropertyLink", "Source", "Split2Enclosure"
        )
        for _key, property_name, property_type, group in _VALUE_PROPERTIES:
            settings.addProperty(property_type, property_name, group)
        settings.addProperty(
            "App::PropertyEnumeration",
            "DefaultLipSide",
            "Joint defaults",
        )
        settings.addProperty(
            "App::PropertyString", "PlaneMode", "Split defaults"
        )
        settings.addProperty(
            "App::PropertyLength", "PlaneOffset", "Split defaults"
        )
        settings.SettingsType = SETTINGS_MARKER
        settings.SchemaVersion = SETTINGS_SCHEMA_VERSION
        settings.Source = owner
        settings.DefaultLipSide = ["negative", "positive"]
        settings.setEditorMode("SettingsType", 1)
        settings.setEditorMode("SchemaVersion", 1)
        settings.setEditorMode("Source", 1)

    for key, property_name, _property_type, _group in _VALUE_PROPERTIES:
        setattr(settings, property_name, values[key])
    settings.DefaultLipSide = values["default_lip_side"]
    settings.PlaneMode = str(parameters.get("plane_mode", "Global XY"))
    settings.PlaneOffset = float(parameters.get("offset", 0.0))
    settings.SchemaVersion = SETTINGS_SCHEMA_VERSION
    owner.Document.recompute()
    return settings


def saved_split_defaults(settings):
    """Return persisted plane-mode defaults separately from joint defaults."""

    if settings is None:
        return {}
    result = {}
    try:
        result["plane_mode"] = str(settings.PlaneMode)
        result["offset"] = _quantity_value(settings.PlaneOffset)
    except (AttributeError, TypeError, ValueError):
        return {}
    return result
