"""FreeCAD-document persistence for per-source enclosure defaults."""

import warnings

from .config import DEFAULTS, validate_defaults


SETTINGS_MARKER = "Split2Enclosure.SourceDefaults"
SETTINGS_SCHEMA_VERSION = 2

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
        settings.SettingsType = SETTINGS_MARKER
        settings.SchemaVersion = SETTINGS_SCHEMA_VERSION
        settings.Source = owner
        settings.DefaultLipSide = ["negative", "positive"]
        settings.setEditorMode("SettingsType", 1)
        settings.setEditorMode("SchemaVersion", 1)
        settings.setEditorMode("Source", 1)

    _ensure_operation_properties(settings)
    previous_operation = saved_operation_state(settings)

    for key, property_name, _property_type, _group in _VALUE_PROPERTIES:
        setattr(settings, property_name, values[key])
    settings.DefaultLipSide = values["default_lip_side"]
    plane_mode = str(parameters.get("plane_mode", "Global XY"))
    plane_offset = float(parameters.get("offset", 0.0))
    reference_kind = str(parameters.get("reference_kind", "global"))
    reference_object = parameters.get("reference_object")
    reference_subname = str(parameters.get("reference_subname", ""))
    settings.PlaneMode = plane_mode
    settings.PlaneOffset = plane_offset
    settings.ReferenceKind = reference_kind
    settings.SplitReference = reference_object
    settings.ReferenceSubelement = reference_subname
    contour_state = parameters.get("contour_state")
    if contour_state is not None:
        settings.ContourState = str(contour_state)
    elif not _same_reference(
        previous_operation,
        plane_mode,
        plane_offset,
        reference_kind,
        reference_object,
        reference_subname,
    ):
        settings.ContourState = ""
    settings.LastUsedSequence = _next_sequence(owner.Document)
    settings.SchemaVersion = SETTINGS_SCHEMA_VERSION
    owner.Document.recompute()
    return settings


def _add_property(settings, property_type, name, group):
    if name not in getattr(settings, "PropertiesList", []):
        settings.addProperty(property_type, name, group)


def _ensure_operation_properties(settings):
    """Upgrade older source VarSets in place without invalidating them."""

    _add_property(settings, "App::PropertyString", "PlaneMode", "Split defaults")
    _add_property(settings, "App::PropertyLength", "PlaneOffset", "Split defaults")
    _add_property(settings, "App::PropertyString", "ReferenceKind", "Split reference")
    _add_property(settings, "App::PropertyLink", "SplitReference", "Split reference")
    _add_property(
        settings, "App::PropertyString", "ReferenceSubelement", "Split reference"
    )
    _add_property(settings, "App::PropertyString", "ContourState", "Contour choices")
    _add_property(settings, "App::PropertyInteger", "LastUsedSequence", "Split2Enclosure")


def _next_sequence(document):
    sequence = 0
    for obj in document.Objects:
        if getattr(obj, "TypeId", "") != "App::VarSet":
            continue
        if getattr(obj, "SettingsType", "") != SETTINGS_MARKER:
            continue
        try:
            sequence = max(sequence, int(obj.LastUsedSequence))
        except (AttributeError, TypeError, ValueError):
            pass
    return sequence + 1


def _same_reference(
    previous, plane_mode, plane_offset, kind, reference_object, reference_subname
):
    if previous.get("reference_kind", "global") != kind:
        return False
    if kind == "global":
        return (
            previous.get("plane_mode") == plane_mode
            and abs(float(previous.get("offset", 0.0)) - plane_offset) <= 1e-6
        )
    if previous.get("reference_object") != reference_object:
        return False
    if kind == "face" and previous.get("reference_subname", "") != reference_subname:
        return False
    if kind in {"face", "datum"}:
        return abs(float(previous.get("offset", 0.0)) - plane_offset) <= 1e-6
    return True


def latest_source_settings(document):
    """Return the most recently used valid source and its settings VarSet."""

    candidates = []
    for position, obj in enumerate(document.Objects):
        if getattr(obj, "TypeId", "") != "App::VarSet":
            continue
        if getattr(obj, "SettingsType", "") != SETTINGS_MARKER:
            continue
        source = getattr(obj, "Source", None)
        shape = getattr(source, "Shape", None)
        if source is None or shape is None or shape.isNull() or not shape.Solids:
            continue
        try:
            sequence = int(obj.LastUsedSequence)
        except (AttributeError, TypeError, ValueError):
            sequence = 0
        candidates.append((sequence, position, source, obj))
    if not candidates:
        return None, None
    _sequence, _position, source, settings = max(candidates)
    return source, settings


def saved_operation_state(settings):
    """Return persisted reference links and contour data when still available."""

    if settings is None:
        return {}
    result = saved_split_defaults(settings)
    result.update(
        {
            "reference_kind": str(getattr(settings, "ReferenceKind", "global")),
            "reference_object": getattr(settings, "SplitReference", None),
            "reference_subname": str(
                getattr(settings, "ReferenceSubelement", "")
            ),
            "contour_state": str(getattr(settings, "ContourState", "")),
        }
    )
    return result


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
