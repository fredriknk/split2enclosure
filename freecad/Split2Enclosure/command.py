"""FreeCAD GUI command for Split2Enclosure."""

import os

import FreeCAD as App
import FreeCADGui as Gui
import Part
from PySide import QtCore, QtGui, QtWidgets

from .config import load_defaults
from .geometry import (
    analyze_section_contours,
    analyze_sketch_contours,
    make_enclosure,
    make_enclosure_with_sketch,
    plane_from_axes,
    ruled_contour_positive_direction,
)
from .settings import (
    find_source_settings,
    latest_source_settings,
    load_source_defaults,
    save_source_defaults,
    saved_operation_state,
    saved_split_defaults,
    settings_owner,
)
from .selection_state import encode_contour_state, match_contour_state


ICON_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
    "Resources",
    "icons",
    "split2enclosure.svg",
)


def _shape_of(obj):
    shape = getattr(obj, "Shape", None)
    return shape if shape is not None and not shape.isNull() else None


def _selection_details():
    source = None
    selected_face = None
    selected_datum = None
    selected_sketch = None
    reference_object = None
    reference_subname = ""
    reference_kind = "global"

    for selection in Gui.Selection.getSelectionEx():
        obj = selection.Object
        shape = _shape_of(obj)
        if obj.TypeId == "Sketcher::SketchObject" and shape is not None and shape.Edges:
            selected_sketch = obj
        if source is None and shape is not None and shape.Solids:
            source = obj
        sub_names = list(getattr(selection, "SubElementNames", []))
        for sub_index, subobject in enumerate(selection.SubObjects):
            if subobject.ShapeType == "Face" and isinstance(subobject.Surface, Part.Plane):
                selected_face = subobject
                reference_object = obj
                reference_subname = (
                    sub_names[sub_index] if sub_index < len(sub_names) else ""
                )
                reference_kind = "face"
                break
        if selected_face is None and shape is not None and shape.Faces:
            face = shape.Faces[0]
            if isinstance(face.Surface, Part.Plane) and obj.TypeId == "PartDesign::Plane":
                selected_datum = face
                reference_object = obj
                reference_subname = ""
                reference_kind = "datum"

    reference = selected_face or selected_datum
    if selected_sketch is not None:
        reference_object = selected_sketch
        reference_subname = ""
        reference_kind = "sketch"
    return (
        source,
        reference,
        selected_sketch,
        reference_object,
        reference_subname,
        reference_kind,
    )


def _selection_context():
    """Backward-compatible compact form used by GUI smoke checks."""

    return _selection_details()[:3]


def _saved_reference(settings):
    state = saved_operation_state(settings)
    kind = state.get("reference_kind", "global")
    obj = state.get("reference_object")
    subname = state.get("reference_subname", "")
    if kind == "sketch":
        shape = _shape_of(obj)
        if shape is not None and shape.Edges:
            return None, obj, obj, "", "sketch"
    elif kind == "face" and obj is not None and subname:
        try:
            face = obj.getSubObject(subname)
        except (AttributeError, RuntimeError):
            face = None
        if (
            face is not None
            and getattr(face, "ShapeType", "") == "Face"
            and isinstance(face.Surface, Part.Plane)
        ):
            return face, None, obj, subname, "face"
    elif kind == "datum":
        shape = _shape_of(obj)
        if shape is not None and shape.Faces:
            face = shape.Faces[0]
            if isinstance(face.Surface, Part.Plane):
                return face, None, obj, "", "datum"
    return None, None, None, "", "global"


def _sketch_normal(sketch):
    placement = (
        sketch.getGlobalPlacement()
        if hasattr(sketch, "getGlobalPlacement")
        else sketch.Placement
    )
    normal = placement.Rotation.multVec(App.Vector(0, 0, 1))
    normal.normalize()
    return normal


def _plane_from_face(face, offset):
    u_min, u_max, v_min, v_max = face.ParameterRange
    u_mid = (u_min + u_max) * 0.5
    v_mid = (v_min + v_max) * 0.5
    origin = face.valueAt(u_mid, v_mid)
    normal = face.normalAt(u_mid, v_mid)
    normal.normalize()
    return origin + normal * offset, normal


class EnclosureDialog(QtWidgets.QDialog):
    def __init__(
        self,
        source,
        reference_face,
        parent=None,
        split_sketch=None,
        reference_object=None,
        reference_subname="",
        reference_kind="global",
    ):
        super().__init__(parent)
        if split_sketch is not None:
            reference_object = reference_object or split_sketch
            reference_kind = "sketch"
        elif reference_face is not None and reference_kind == "global":
            reference_kind = "face"
        self.source = source
        self.reference_face = reference_face
        self.split_sketch = split_sketch
        self.reference_object = reference_object
        self.reference_subname = reference_subname
        self.reference_kind = reference_kind
        self._contours = []
        self._preview_group = None
        self._preview_objects = {}
        self._selection_observer_active = False
        self._updating_checks = False
        self._selected_contour_indices = None
        self._contour_sides = None
        self._contour_snaps = None
        self._preview_split = None
        self._preview_positive_directions = {}
        self._source_view_state = None
        self.defaults, self.source_settings = load_source_defaults(
            source, load_defaults()
        )
        self.split_defaults = saved_split_defaults(self.source_settings)
        self.saved_operation = saved_operation_state(self.source_settings)
        if reference_face is None and split_sketch is None:
            (
                self.reference_face,
                self.split_sketch,
                self.reference_object,
                self.reference_subname,
                self.reference_kind,
            ) = _saved_reference(self.source_settings)
        self.setWindowTitle("Split to enclosure")
        self.setMinimumWidth(480)

        layout = QtWidgets.QVBoxLayout(self)
        source_label = QtWidgets.QLabel(
            "Source: <b>{}</b>".format(source.Label)
        )
        source_label.setTextFormat(QtCore.Qt.RichText)
        layout.addWidget(source_label)

        owner = settings_owner(source)
        self.remember_settings = QtWidgets.QCheckBox(
            "Remember settings for: {}".format(owner.Label)
        )
        self.remember_settings.setChecked(True)
        if self.source_settings is not None:
            self.remember_settings.setToolTip(
                "Loaded from '{}'; Create will update that VarSet.".format(
                    self.source_settings.Label
                )
            )
        else:
            self.remember_settings.setToolTip(
                "Create will add an editable Enclosure defaults VarSet to this document."
            )
        layout.addWidget(self.remember_settings)

        form = QtWidgets.QFormLayout()
        layout.addLayout(form)

        self.plane_mode = QtWidgets.QComboBox()
        self.plane_mode.addItems(["Global XY", "Global XZ", "Global YZ"])
        if self.reference_face is not None:
            self.plane_mode.addItem("Selected planar face / datum")
            self.plane_mode.setCurrentIndex(3)
        if self.split_sketch is not None:
            self.plane_mode.addItem("Selected open sketch")
            self.plane_mode.setCurrentIndex(self.plane_mode.count() - 1)
        elif self.reference_face is None:
            saved_mode = self.split_defaults.get("plane_mode")
            saved_index = self.plane_mode.findText(saved_mode) if saved_mode else -1
            if saved_index >= 0:
                self.plane_mode.setCurrentIndex(saved_index)
        form.addRow("Split plane", self.plane_mode)

        self.offset = self._length_box(
            -100000.0, 100000.0, self.split_defaults.get("offset", 0.0)
        )
        self.offset.setToolTip("Distance along the plane's positive normal")
        form.addRow("Plane offset", self.offset)

        self.lip_side = QtWidgets.QComboBox()
        self.lip_side.addItem("Negative half", "negative")
        self.lip_side.addItem("Positive half", "positive")
        default_side = self.lip_side.findData(self.defaults["default_lip_side"])
        self.lip_side.setCurrentIndex(max(default_side, 0))
        form.addRow("Default contour side", self.lip_side)

        self.lip_width = self._length_box(
            0.01, 1000.0, self.defaults["lip_width"]
        )
        self.lip_height = self._length_box(
            0.01, 1000.0, self.defaults["lip_height"]
        )
        self.clearance = self._length_box(
            0.0, 100.0, self.defaults["side_clearance"]
        )
        self.vertical_clearance = self._length_box(
            0.0, 100.0, self.defaults["depth_clearance"]
        )
        self.draft_angle = self._angle_box(
            0.0, 30.0, self.defaults["draft_angle"]
        )
        self.snap_radius = self._length_box(
            0.01, 100.0, self.defaults["snap_radius"]
        )
        self.snap_clearance = self._length_box(
            0.0, 100.0, self.defaults["snap_clearance"]
        )
        self.snap_position = self._number_box(
            0.1, 0.9, self.defaults["snap_position"], 2, 0.05
        )
        form.addRow("Lip width", self.lip_width)
        form.addRow("Lip height", self.lip_height)
        form.addRow("Side clearance", self.clearance)
        form.addRow("Depth clearance", self.vertical_clearance)
        form.addRow("Draft angle", self.draft_angle)
        self.snap_radius.setToolTip(
            "Half the wedge height and its lateral reach; produces 45-degree faces"
        )
        self.snap_clearance.setToolTip(
            "Extra width and height in the receiving snap channel"
        )
        self.vertical_clearance.setToolTip(
            "Axial gap beyond the lip tip and between opposing flat shoulders"
        )
        form.addRow("Snap seam half-size", self.snap_radius)
        form.addRow("Snap channel clearance", self.snap_clearance)
        form.addRow("Snap height fraction", self.snap_position)

        contour_group = QtWidgets.QGroupBox("Joint contours")
        contour_layout = QtWidgets.QVBoxLayout(contour_group)
        contour_buttons = QtWidgets.QHBoxLayout()
        self.preview_button = QtWidgets.QPushButton("Preview / choose contours")
        self.set_negative_button = QtWidgets.QPushButton("NEG")
        self.select_none_button = QtWidgets.QPushButton("OFF")
        self.set_positive_button = QtWidgets.QPushButton("POS")
        self.toggle_snap_button = QtWidgets.QPushButton("SNAP")
        contour_buttons.addWidget(self.preview_button)
        contour_buttons.addStretch(1)
        contour_buttons.addWidget(self.set_negative_button)
        contour_buttons.addWidget(self.select_none_button)
        contour_buttons.addWidget(self.set_positive_button)
        contour_buttons.addWidget(self.toggle_snap_button)
        contour_layout.addLayout(contour_buttons)

        self.contour_list = QtWidgets.QListWidget()
        self.contour_list.setMinimumHeight(125)
        self.contour_list.setSelectionMode(
            QtWidgets.QAbstractItemView.ExtendedSelection
        )
        self.contour_list.setToolTip(
            "Shift/Ctrl-select rows, then assign NEG, OFF, or POS. "
            "SNAP toggles retention for every selected row. Clicking a 3D "
            "contour cycles its side assignment."
        )
        contour_layout.addWidget(self.contour_list)
        self.persistence_status = QtWidgets.QLabel("")
        self.persistence_status.setWordWrap(True)
        contour_layout.addWidget(self.persistence_status)
        legend = QtWidgets.QLabel(
            "<span style='color:#3595ff'>Blue = NEG lip, arrow toward POS</span> &nbsp; "
            "<span style='color:#f0a03a'>Orange = POS lip, arrow toward NEG</span> &nbsp; "
            "<span style='color:#999'>Gray = OFF</span>."
        )
        legend.setTextFormat(QtCore.Qt.RichText)
        legend.setWordWrap(True)
        contour_layout.addWidget(legend)
        layout.addWidget(contour_group)

        note = QtWidgets.QLabel(
            "Side clearance widens only the groove's material-side mating face; "
            "it does not move the lip away from the chosen perimeter. Depth "
            "clearance gaps both the lip tip and the flat shoulders. The lip is "
            "taken from the receiving half so holes and local details remain."
        )
        note.setWordWrap(True)
        layout.addWidget(note)

        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel
        )
        buttons.button(QtWidgets.QDialogButtonBox.Ok).setText("Create")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self.preview_button.clicked.connect(self._build_preview)
        self.set_negative_button.clicked.connect(
            lambda: self._set_selected_side("negative")
        )
        self.select_none_button.clicked.connect(
            lambda: self._set_selected_side("none")
        )
        self.set_positive_button.clicked.connect(
            lambda: self._set_selected_side("positive")
        )
        self.toggle_snap_button.clicked.connect(self._toggle_selected_snap)
        self.plane_mode.currentIndexChanged.connect(self._invalidate_preview)
        self.plane_mode.currentIndexChanged.connect(self._update_split_controls)
        self.offset.valueChanged.connect(self._invalidate_preview)
        self._update_split_controls()

    @staticmethod
    def _length_box(minimum, maximum, value):
        box = QtWidgets.QDoubleSpinBox()
        box.setDecimals(3)
        box.setRange(minimum, maximum)
        box.setValue(value)
        box.setSuffix(" mm")
        box.setSingleStep(0.1)
        return box

    @staticmethod
    def _angle_box(minimum, maximum, value):
        box = QtWidgets.QDoubleSpinBox()
        box.setDecimals(2)
        box.setRange(minimum, maximum)
        box.setValue(value)
        box.setSuffix(" deg")
        box.setSingleStep(0.5)
        return box

    @staticmethod
    def _number_box(minimum, maximum, value, decimals, step):
        box = QtWidgets.QDoubleSpinBox()
        box.setDecimals(decimals)
        box.setRange(minimum, maximum)
        box.setValue(value)
        box.setSingleStep(step)
        return box

    def _plane_parameters(self):
        mode = self.plane_mode.currentText()
        offset = self.offset.value()
        if mode == "Selected open sketch":
            placement = (
                self.split_sketch.getGlobalPlacement()
                if hasattr(self.split_sketch, "getGlobalPlacement")
                else self.split_sketch.Placement
            )
            origin = placement.Base
            normal = _sketch_normal(self.split_sketch)
            offset = 0.0
        elif mode.startswith("Global"):
            origin, normal = plane_from_axes(mode.split()[-1], offset)
        else:
            origin, normal = _plane_from_face(self.reference_face, offset)
        return mode, offset, origin, normal

    def _update_split_controls(self, *_args):
        sketch_mode = self.plane_mode.currentText() == "Selected open sketch"
        self.offset.setEnabled(not sketch_mode)
        self.offset.setToolTip(
            "Sketch splits use the sketch path directly"
            if sketch_mode
            else "Distance along the plane's positive normal"
        )

    def parameters(self):
        mode, offset, origin, normal = self._plane_parameters()
        contour_state = None
        if self._contours and self._contour_sides is not None:
            contour_state = encode_contour_state(
                self._contours,
                self._contour_sides,
                self._contour_snaps or {},
            )
        reference_kind = self.reference_kind if not mode.startswith("Global") else "global"
        return {
            "plane_origin": origin,
            "plane_normal": normal,
            "lip_width": self.lip_width.value(),
            "lip_height": self.lip_height.value(),
            "clearance": self.clearance.value(),
            "vertical_clearance": self.vertical_clearance.value(),
            "draft_angle": self.draft_angle.value(),
            "snap_radius": self.snap_radius.value(),
            "snap_clearance": self.snap_clearance.value(),
            "snap_position": self.snap_position.value(),
            "lip_on": self.lip_side.currentData(),
            "contour_mode": "outer",
            "contour_indices": self._selected_contour_indices,
            "contour_sides": self._contour_sides,
            "contour_snaps": self._contour_snaps,
            "plane_mode": mode,
            "offset": offset,
            "split_kind": "sketch" if mode == "Selected open sketch" else "plane",
            "split_sketch": self.split_sketch,
            "reference_kind": reference_kind,
            "reference_object": (
                self.reference_object if reference_kind != "global" else None
            ),
            "reference_subname": (
                self.reference_subname if reference_kind == "face" else ""
            ),
            "contour_state": contour_state,
            "remember_settings": self.remember_settings.isChecked(),
        }

    def _saved_choices_apply(self, mode, offset):
        if not self.saved_operation.get("contour_state"):
            return False
        saved_kind = self.saved_operation.get("reference_kind", "global")
        if mode.startswith("Global"):
            return (
                saved_kind == "global"
                and self.saved_operation.get("plane_mode") == mode
                and abs(float(self.saved_operation.get("offset", 0.0)) - offset)
                <= 1e-6
            )
        if mode == "Selected open sketch":
            return (
                saved_kind == "sketch"
                and self.saved_operation.get("reference_object") == self.split_sketch
            )
        return (
            saved_kind in {"face", "datum"}
            and self.saved_operation.get("reference_object") == self.reference_object
            and self.saved_operation.get("reference_subname", "")
            == self.reference_subname
            and abs(float(self.saved_operation.get("offset", 0.0)) - offset) <= 1e-6
        )

    def _build_preview(self):
        self._cleanup_preview(clear_list=True)
        self.preview_button.setText("Preview / choose contours")
        try:
            mode, _offset, origin, normal = self._plane_parameters()
            if mode == "Selected open sketch":
                self._preview_split, self._contours = analyze_sketch_contours(
                    self.source.Shape,
                    self.split_sketch.Shape,
                    normal,
                )
            else:
                _section, _plane, self._contours = analyze_section_contours(
                    self.source.Shape, origin, normal
                )
                self._preview_split = None
            if not self._contours:
                raise ValueError("No closed contours were found on this split plane.")

            restored_sides = {}
            restored_snaps = {}
            report = None
            if self._saved_choices_apply(mode, _offset):
                restored_sides, restored_snaps, report = match_contour_state(
                    self._contours, self.saved_operation.get("contour_state")
                )

            doc = self.source.Document
            self._preview_group = doc.addObject(
                "App::DocumentObjectGroup", "Split2EnclosurePreview"
            )
            self._preview_group.Label = "Split2Enclosure contour preview (temporary)"
            self._source_view_state = (
                self.source.ViewObject.Visibility,
                self.source.ViewObject.Transparency,
            )
            self.source.ViewObject.Visibility = True
            self.source.ViewObject.Transparency = max(
                self.source.ViewObject.Transparency, 70
            )

            self._updating_checks = True
            for contour in self._contours:
                side = (
                    self.lip_side.currentData()
                    if contour.kind == "outer"
                    else "none"
                )
                side = restored_sides.get(contour.index, side)
                snap = restored_snaps.get(contour.index, False)
                base_label = (
                    "#{:02d}  {:8s}   area {:9.2f} mm2   length {:9.2f} mm".format(
                        contour.index + 1,
                        contour.kind,
                        contour.area,
                        contour.length,
                    )
                )
                item = QtWidgets.QListWidgetItem(base_label)
                item.setFlags(
                    item.flags()
                    | QtCore.Qt.ItemIsEnabled
                    | QtCore.Qt.ItemIsSelectable
                )
                item.setData(QtCore.Qt.UserRole, contour.index)
                item.setData(QtCore.Qt.UserRole + 1, side)
                item.setData(QtCore.Qt.UserRole + 2, snap)
                item.setData(QtCore.Qt.UserRole + 3, base_label)
                self.contour_list.addItem(item)

                preview = doc.addObject("Part::Feature", "Split2EnclosureContour")
                preview.Label = "Contour #{:02d} ({})".format(
                    contour.index + 1, contour.kind
                )
                preview.Shape = contour.wire
                preview.addProperty("App::PropertyInteger", "ContourIndex")
                preview.ContourIndex = contour.index
                preview.ViewObject.LineWidth = 6.0
                preview.ViewObject.PointSize = 8.0
                preview.ViewObject.Selectable = True
                self._preview_group.addObject(preview)
                self._preview_objects[preview.Name] = contour.index
                positive_direction = (
                    ruled_contour_positive_direction(
                        self._preview_split, contour.wire
                    )
                    if self._preview_split is not None
                    else App.Vector(normal)
                )
                self._preview_positive_directions[contour.index] = positive_direction

                arrow = doc.addObject("Part::Feature", "Split2EnclosureDirection")
                arrow.Label = "Contour #{:02d} lip direction".format(
                    contour.index + 1
                )
                arrow.addProperty("App::PropertyInteger", "ContourIndex")
                arrow.addProperty("App::PropertyString", "PreviewKind")
                arrow.ContourIndex = contour.index
                arrow.PreviewKind = "arrow"
                arrow.ViewObject.Selectable = True
                self._preview_group.addObject(arrow)
                self._preview_objects[arrow.Name] = contour.index
                self._set_contour_side(contour.index, side)
            if report is not None:
                skipped = report["missing"] + report["ambiguous"]
                message = "Restored {} contour choice(s).".format(report["matched"])
                if skipped:
                    message += (
                        " {} changed or ambiguous choice(s) were left at safe defaults."
                    ).format(skipped)
                    self.persistence_status.setStyleSheet("color: #d99020")
                else:
                    self.persistence_status.setStyleSheet("color: #55a868")
                self.persistence_status.setText(message)
            elif self.saved_operation.get("contour_state"):
                self.persistence_status.setStyleSheet("color: #d99020")
                self.persistence_status.setText(
                    "Saved contour choices belong to a different split reference; "
                    "using safe defaults."
                )
            else:
                self.persistence_status.clear()
            self._updating_checks = False
            doc.recompute()
            if hasattr(Gui, "Selection"):
                Gui.Selection.addObserver(self)
                self._selection_observer_active = True
        except Exception as exc:
            self._updating_checks = False
            self._cleanup_preview(clear_list=True)
            QtWidgets.QMessageBox.warning(
                self, "Contour preview", str(exc)
            )

    @staticmethod
    def _side_color(side):
        return {
            "negative": (0.20, 0.58, 1.00),
            "positive": (0.95, 0.55, 0.18),
            "none": (0.55, 0.55, 0.55),
        }[side]

    def _arrow_shape(self, index, side):
        if side == "none":
            return Part.Shape()
        contour = self._contours[index]
        direction = App.Vector(self._preview_positive_directions[index])
        if side == "positive":
            direction = -direction
        direction.normalize()
        length = max(self.lip_height.value() * 2.0, 3.0)
        radius = max(length * 0.045, 0.12)
        point = contour.wire.CenterOfMass
        shaft = Part.makeCylinder(radius, length * 0.68, point, direction)
        head = Part.makeCone(
            radius * 2.2,
            0.0,
            length * 0.32,
            point + direction * (length * 0.68),
            direction,
        )
        return Part.makeCompound([shaft, head])

    def _set_preview_style(self, index, side):
        color = self._side_color(side)
        for name, mapped_index in self._preview_objects.items():
            if mapped_index != index:
                continue
            obj = self.source.Document.getObject(name)
            if obj is not None:
                obj.ViewObject.LineColor = color
                obj.ViewObject.PointColor = color
                obj.ViewObject.ShapeColor = color
                if hasattr(obj, "PreviewKind") and obj.PreviewKind == "arrow":
                    obj.Shape = self._arrow_shape(index, side)
                    obj.ViewObject.Visibility = side != "none"

    def _item_for_index(self, index):
        for row in range(self.contour_list.count()):
            item = self.contour_list.item(row)
            if int(item.data(QtCore.Qt.UserRole)) == index:
                return item
        return None

    def _set_contour_side(self, index, side):
        item = self._item_for_index(index)
        if item is None:
            return
        item.setData(QtCore.Qt.UserRole + 1, side)
        self._refresh_item_text(item)
        item.setForeground(QtGui.QColor.fromRgbF(*self._side_color(side)))
        self._set_preview_style(index, side)

    @staticmethod
    def _refresh_item_text(item):
        labels = {"negative": "NEG", "positive": "POS", "none": "OFF"}
        side = str(item.data(QtCore.Qt.UserRole + 1))
        snap = "SNAP" if bool(item.data(QtCore.Qt.UserRole + 2)) else "----"
        base = str(item.data(QtCore.Qt.UserRole + 3))
        item.setText("[{}][{}] {}".format(labels[side], snap, base))

    def _set_selected_side(self, side):
        items = self.contour_list.selectedItems()
        if not items and self.contour_list.currentItem() is not None:
            items = [self.contour_list.currentItem()]
        for item in items:
            self._set_contour_side(int(item.data(QtCore.Qt.UserRole)), side)

    def _toggle_selected_snap(self):
        items = self.contour_list.selectedItems()
        if not items and self.contour_list.currentItem() is not None:
            items = [self.contour_list.currentItem()]
        for item in items:
            item.setData(
                QtCore.Qt.UserRole + 2,
                not bool(item.data(QtCore.Qt.UserRole + 2)),
            )
            self._refresh_item_text(item)

    def addSelection(self, document_name, object_name, _sub_name, _point):
        if document_name != self.source.Document.Name:
            return
        index = self._preview_objects.get(object_name)
        if index is None:
            return
        item = self._item_for_index(index)
        if item is not None:
            current = str(item.data(QtCore.Qt.UserRole + 1))
            next_side = {
                "none": "negative",
                "negative": "positive",
                "positive": "none",
            }[current]
            self.contour_list.setCurrentItem(item)
            self._set_contour_side(index, next_side)
        if hasattr(Gui, "Selection"):
            Gui.Selection.clearSelection()

    def _invalidate_preview(self, *_args):
        if self._contours or self._preview_objects:
            self._cleanup_preview(clear_list=True)
            self.preview_button.setText("Plane changed — preview again")

    def _cleanup_preview(self, clear_list=False):
        if self._selection_observer_active:
            Gui.Selection.removeObserver(self)
            self._selection_observer_active = False
        if hasattr(Gui, "Selection"):
            Gui.Selection.clearSelection()
        try:
            doc = self.source.Document
        except (ReferenceError, RuntimeError):
            doc = None
        if doc is None:
            self._preview_objects.clear()
            self._preview_group = None
            self._source_view_state = None
            self._preview_split = None
            self._preview_positive_directions.clear()
            return
        for name in list(self._preview_objects):
            if doc.getObject(name) is not None:
                doc.removeObject(name)
        self._preview_objects.clear()
        if self._preview_group is not None:
            if doc.getObject(self._preview_group.Name) is not None:
                doc.removeObject(self._preview_group.Name)
            self._preview_group = None
        if self._source_view_state is not None:
            visibility, transparency = self._source_view_state
            self.source.ViewObject.Visibility = visibility
            self.source.ViewObject.Transparency = transparency
            self._source_view_state = None
        if clear_list:
            self._updating_checks = True
            self.contour_list.clear()
            self._updating_checks = False
            self._contours = []
            self._selected_contour_indices = None
            self._contour_sides = None
            self._contour_snaps = None
            self._preview_split = None
            self._preview_positive_directions.clear()
        doc.recompute()

    def accept(self):
        if self._contours:
            contour_sides = {}
            contour_snaps = {}
            for row in range(self.contour_list.count()):
                item = self.contour_list.item(row)
                index = int(item.data(QtCore.Qt.UserRole))
                contour_sides[index] = str(
                    item.data(QtCore.Qt.UserRole + 1)
                )
                contour_snaps[index] = bool(item.data(QtCore.Qt.UserRole + 2))
            if not any(side != "none" for side in contour_sides.values()):
                QtWidgets.QMessageBox.warning(
                    self,
                    "Split to enclosure",
                    "Assign at least one contour to NEG or POS.",
                )
                return
            self._selected_contour_indices = None
            self._contour_sides = contour_sides
            self._contour_snaps = contour_snaps
        self._cleanup_preview(clear_list=False)
        super().accept()

    def reject(self):
        self._cleanup_preview(clear_list=True)
        super().reject()

    def closeEvent(self, event):
        self._cleanup_preview(clear_list=True)
        super().closeEvent(event)


def _add_result_to_document(source, result, parameters):
    doc = source.Document
    doc.openTransaction("Split enclosure with lip and groove")
    try:
        settings = (
            save_source_defaults(source, parameters)
            if parameters.get("remember_settings", True)
            else find_source_settings(source)
        )
        container = doc.addObject("App::Part", "Split2EnclosureResult")
        container.Label = "Split enclosure: {}".format(source.Label)
        container.addProperty("App::PropertyLink", "Source", "Split parameters")
        container.addProperty("App::PropertyLink", "Settings", "Split parameters")
        container.addProperty("App::PropertyString", "PlaneMode", "Split parameters")
        container.addProperty("App::PropertyLink", "SplitSketch", "Split parameters")
        container.addProperty("App::PropertyLink", "SplitReference", "Split parameters")
        container.addProperty("App::PropertyString", "ReferenceSubelement", "Split parameters")
        container.addProperty("App::PropertyVector", "PlaneOrigin", "Split parameters")
        container.addProperty("App::PropertyVector", "PlaneNormal", "Split parameters")
        container.addProperty("App::PropertyLength", "PlaneOffset", "Split parameters")
        container.addProperty("App::PropertyLength", "LipWidth", "Joint parameters")
        container.addProperty("App::PropertyLength", "LipHeight", "Joint parameters")
        container.addProperty("App::PropertyLength", "SideClearance", "Joint parameters")
        container.addProperty("App::PropertyLength", "DepthClearance", "Joint parameters")
        container.addProperty("App::PropertyAngle", "DraftAngle", "Joint parameters")
        container.addProperty("App::PropertyLength", "SnapRadius", "Snap parameters")
        container.addProperty("App::PropertyLength", "SnapClearance", "Snap parameters")
        container.addProperty("App::PropertyFloat", "SnapPosition", "Snap parameters")
        container.addProperty("App::PropertyString", "SnapContours", "Snap parameters")
        container.addProperty("App::PropertyString", "LipSide", "Joint parameters")
        container.addProperty("App::PropertyString", "ContourMode", "Joint parameters")
        container.addProperty("App::PropertyString", "ContourSelection", "Joint parameters")
        container.addProperty("App::PropertyString", "ContourAssignments", "Joint parameters")
        container.addProperty("App::PropertyInteger", "JointContours", "Diagnostics")

        container.Source = source
        container.Settings = settings
        container.SplitSketch = parameters.get("split_sketch")
        container.SplitReference = parameters.get("reference_object")
        container.ReferenceSubelement = parameters.get("reference_subname", "")
        container.PlaneMode = parameters["plane_mode"]
        container.PlaneOrigin = parameters["plane_origin"]
        container.PlaneNormal = parameters["plane_normal"]
        container.PlaneOffset = parameters["offset"]
        container.LipWidth = parameters["lip_width"]
        container.LipHeight = parameters["lip_height"]
        container.SideClearance = parameters["clearance"]
        container.DepthClearance = parameters["vertical_clearance"]
        container.DraftAngle = parameters["draft_angle"]
        container.SnapRadius = parameters["snap_radius"]
        container.SnapClearance = parameters["snap_clearance"]
        container.SnapPosition = parameters["snap_position"]
        assignments = parameters.get("contour_sides") or {}
        snap_assignments = parameters.get("contour_snaps") or {}
        container.SnapContours = ",".join(
            str(index + 1)
            for index, enabled in sorted(snap_assignments.items())
            if enabled and assignments.get(index, "none") != "none"
        )
        active_sides = {side for side in assignments.values() if side != "none"}
        if not active_sides:
            active_sides = {parameters["lip_on"]}
        container.LipSide = (
            next(iter(active_sides)) if len(active_sides) == 1 else "mixed"
        )
        container.ContourMode = (
            "per-contour" if assignments else parameters["contour_mode"]
        )
        container.ContourSelection = ",".join(
            str(index + 1)
            for index, side in sorted(assignments.items())
            if side != "none"
        ) or ",".join(
            str(index + 1)
            for index in (parameters.get("contour_indices") or [])
        )
        container.ContourAssignments = ", ".join(
            "{}:{}".format(index + 1, side)
            for index, side in sorted(assignments.items())
        )
        container.JointContours = len(result.internal_wires)

        negative = doc.addObject("Part::Feature", "EnclosureNegative")
        positive = doc.addObject("Part::Feature", "EnclosurePositive")
        negative.Label = "Negative half"
        positive.Label = "Positive half"
        negative.Shape = result.negative
        positive.Shape = result.positive
        negative.addProperty("App::PropertyString", "Joint", "Split2Enclosure")
        positive.addProperty("App::PropertyString", "Joint", "Split2Enclosure")
        negative.Joint = (
            "Lip and groove"
            if len(active_sides) > 1
            else "Lip" if "negative" in active_sides else "Groove"
        )
        positive.Joint = (
            "Lip and groove"
            if len(active_sides) > 1
            else "Lip" if "positive" in active_sides else "Groove"
        )
        negative.ViewObject.ShapeColor = (0.30, 0.65, 0.95)
        positive.ViewObject.ShapeColor = (0.95, 0.65, 0.25)
        container.addObject(negative)
        container.addObject(positive)

        source.ViewObject.Visibility = False
        doc.recompute()
        doc.commitTransaction()
        Gui.activeDocument().activeView().fitAll()
        return container
    except Exception:
        doc.abortTransaction()
        raise


def run():
    (
        source,
        reference_face,
        split_sketch,
        reference_object,
        reference_subname,
        reference_kind,
    ) = _selection_details()
    if source is None and App.ActiveDocument is not None:
        source, _settings = latest_source_settings(App.ActiveDocument)
    if source is None:
        QtWidgets.QMessageBox.warning(
            Gui.getMainWindow(),
            "Split to enclosure",
            "Select one solid object first. Optionally also select an open sketch, "
            "planar face, or datum plane as the split reference.",
        )
        return

    dialog = EnclosureDialog(
        source,
        reference_face,
        Gui.getMainWindow(),
        split_sketch=split_sketch,
        reference_object=reference_object,
        reference_subname=reference_subname,
        reference_kind=reference_kind,
    )
    if dialog.exec() != QtWidgets.QDialog.Accepted:
        return
    try:
        parameters = dialog.parameters()
        engine_parameters = dict(parameters)
        engine_parameters.pop("plane_mode")
        engine_parameters.pop("offset")
        split_kind = engine_parameters.pop("split_kind")
        selected_sketch = engine_parameters.pop("split_sketch")
        engine_parameters.pop("remember_settings")
        engine_parameters.pop("reference_kind")
        engine_parameters.pop("reference_object")
        engine_parameters.pop("reference_subname")
        engine_parameters.pop("contour_state")
        if split_kind == "sketch":
            plane_origin = engine_parameters.pop("plane_origin")
            sketch_normal = engine_parameters.pop("plane_normal")
            del plane_origin
            result = make_enclosure_with_sketch(
                source.Shape,
                selected_sketch.Shape,
                sketch_normal,
                **engine_parameters,
            )
        else:
            result = make_enclosure(source.Shape, **engine_parameters)
        _add_result_to_document(source, result, parameters)
    except Exception as exc:
        App.Console.PrintError("Split2Enclosure: {}\n".format(exc))
        QtWidgets.QMessageBox.critical(
            Gui.getMainWindow(), "Split to enclosure", str(exc)
        )


class Split2EnclosureCommand:
    def GetResources(self):
        return {
            "Pixmap": ICON_PATH,
            "MenuText": "Split to enclosure",
            "ToolTip": "Split a hollow solid and add a mating lip and groove",
        }

    def Activated(self):
        run()

    def IsActive(self):
        return App.ActiveDocument is not None


if hasattr(Gui, "addCommand"):
    Gui.addCommand("Split2Enclosure_Create", Split2EnclosureCommand())
