"""FreeCAD GUI command for Split2Enclosure."""

import os

import FreeCAD as App
import FreeCADGui as Gui
import Part
from PySide import QtCore, QtWidgets

from .geometry import (
    analyze_section_contours,
    analyze_sketch_contours,
    make_enclosure,
    make_enclosure_with_sketch,
    plane_from_axes,
)


ICON_PATH = os.path.join(
    os.path.dirname(os.path.dirname(__file__)),
    "Resources",
    "icons",
    "split2enclosure.svg",
)


def _shape_of(obj):
    shape = getattr(obj, "Shape", None)
    return shape if shape is not None and not shape.isNull() else None


def _selection_context():
    source = None
    selected_face = None
    selected_datum = None
    selected_sketch = None

    for selection in Gui.Selection.getSelectionEx():
        obj = selection.Object
        shape = _shape_of(obj)
        if obj.TypeId == "Sketcher::SketchObject" and shape is not None and shape.Edges:
            selected_sketch = obj
        if source is None and shape is not None and shape.Solids:
            source = obj
        for subobject in selection.SubObjects:
            if subobject.ShapeType == "Face" and isinstance(subobject.Surface, Part.Plane):
                selected_face = subobject
                break
        if selected_face is None and shape is not None and shape.Faces:
            face = shape.Faces[0]
            if isinstance(face.Surface, Part.Plane) and obj.TypeId == "PartDesign::Plane":
                selected_datum = face

    reference = selected_face or selected_datum
    return source, reference, selected_sketch


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
    def __init__(self, source, reference_face, parent=None, split_sketch=None):
        super().__init__(parent)
        self.source = source
        self.reference_face = reference_face
        self.split_sketch = split_sketch
        self._contours = []
        self._preview_group = None
        self._preview_objects = {}
        self._selection_observer_active = False
        self._updating_checks = False
        self._selected_contour_indices = None
        self._source_view_state = None
        self.setWindowTitle("Split to enclosure")
        self.setMinimumWidth(480)

        layout = QtWidgets.QVBoxLayout(self)
        source_label = QtWidgets.QLabel(
            "Source: <b>{}</b>".format(source.Label)
        )
        source_label.setTextFormat(QtCore.Qt.RichText)
        layout.addWidget(source_label)

        form = QtWidgets.QFormLayout()
        layout.addLayout(form)

        self.plane_mode = QtWidgets.QComboBox()
        self.plane_mode.addItems(["Global XY", "Global XZ", "Global YZ"])
        if reference_face is not None:
            self.plane_mode.addItem("Selected planar face / datum")
            self.plane_mode.setCurrentIndex(3)
        if split_sketch is not None:
            self.plane_mode.addItem("Selected open sketch")
            self.plane_mode.setCurrentIndex(self.plane_mode.count() - 1)
        form.addRow("Split plane", self.plane_mode)

        self.offset = self._length_box(-100000.0, 100000.0, 0.0)
        self.offset.setToolTip("Distance along the plane's positive normal")
        form.addRow("Plane offset", self.offset)

        self.lip_side = QtWidgets.QComboBox()
        self.lip_side.addItem("Negative half", "negative")
        self.lip_side.addItem("Positive half", "positive")
        form.addRow("Lip belongs to", self.lip_side)

        self.lip_width = self._length_box(0.01, 1000.0, 1.0)
        self.lip_height = self._length_box(0.01, 1000.0, 2.0)
        self.clearance = self._length_box(0.0, 100.0, 0.2)
        self.vertical_clearance = self._length_box(0.0, 100.0, 0.2)
        form.addRow("Lip width", self.lip_width)
        form.addRow("Lip height", self.lip_height)
        form.addRow("Side clearance", self.clearance)
        form.addRow("Depth clearance", self.vertical_clearance)

        contour_group = QtWidgets.QGroupBox("Joint contours")
        contour_layout = QtWidgets.QVBoxLayout(contour_group)
        contour_buttons = QtWidgets.QHBoxLayout()
        self.preview_button = QtWidgets.QPushButton("Preview / choose contours")
        self.select_all_button = QtWidgets.QPushButton("All")
        self.select_none_button = QtWidgets.QPushButton("None")
        contour_buttons.addWidget(self.preview_button)
        contour_buttons.addStretch(1)
        contour_buttons.addWidget(self.select_all_button)
        contour_buttons.addWidget(self.select_none_button)
        contour_layout.addLayout(contour_buttons)

        self.contour_list = QtWidgets.QListWidget()
        self.contour_list.setMinimumHeight(125)
        self.contour_list.setToolTip(
            "Check contours here, or click their green/red preview wires in the 3D view"
        )
        contour_layout.addWidget(self.contour_list)
        legend = QtWidgets.QLabel(
            "<span style='color:#20c050'>Green = included</span> &nbsp; "
            "<span style='color:#e04040'>Red = excluded</span>. "
            "Outermost contours are selected initially."
        )
        legend.setTextFormat(QtCore.Qt.RichText)
        legend.setWordWrap(True)
        contour_layout.addWidget(legend)
        layout.addWidget(contour_group)

        note = QtWidgets.QLabel(
            "Side clearance widens only the groove's material-side mating face; "
            "it does not move the lip away from the chosen perimeter. The lip "
            "is taken from the receiving half so holes and local details remain."
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
        self.select_all_button.clicked.connect(lambda: self._set_all_checked(True))
        self.select_none_button.clicked.connect(lambda: self._set_all_checked(False))
        self.contour_list.itemChanged.connect(self._item_changed)
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
        return {
            "plane_origin": origin,
            "plane_normal": normal,
            "lip_width": self.lip_width.value(),
            "lip_height": self.lip_height.value(),
            "clearance": self.clearance.value(),
            "vertical_clearance": self.vertical_clearance.value(),
            "lip_on": self.lip_side.currentData(),
            "contour_mode": "outer",
            "contour_indices": self._selected_contour_indices,
            "plane_mode": mode,
            "offset": offset,
            "split_kind": "sketch" if mode == "Selected open sketch" else "plane",
            "split_sketch": self.split_sketch,
        }

    def _build_preview(self):
        self._cleanup_preview(clear_list=True)
        self.preview_button.setText("Preview / choose contours")
        try:
            mode, _offset, origin, normal = self._plane_parameters()
            if mode == "Selected open sketch":
                _split, self._contours = analyze_sketch_contours(
                    self.source.Shape,
                    self.split_sketch.Shape,
                    normal,
                )
            else:
                _section, _plane, self._contours = analyze_section_contours(
                    self.source.Shape, origin, normal
                )
            if not self._contours:
                raise ValueError("No closed contours were found on this split plane.")

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
                included = contour.kind == "outer"
                item = QtWidgets.QListWidgetItem(
                    "#{:02d}  {:8s}   area {:9.2f} mm²   length {:9.2f} mm".format(
                        contour.index + 1,
                        contour.kind,
                        contour.area,
                        contour.length,
                    )
                )
                item.setFlags(
                    item.flags()
                    | QtCore.Qt.ItemIsUserCheckable
                    | QtCore.Qt.ItemIsEnabled
                    | QtCore.Qt.ItemIsSelectable
                )
                item.setData(QtCore.Qt.UserRole, contour.index)
                item.setCheckState(
                    QtCore.Qt.Checked if included else QtCore.Qt.Unchecked
                )
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
                self._set_preview_color(contour.index, included)
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

    def _set_preview_color(self, index, included):
        for name, mapped_index in self._preview_objects.items():
            if mapped_index != index:
                continue
            obj = self.source.Document.getObject(name)
            if obj is not None:
                color = (0.15, 0.90, 0.25) if included else (0.95, 0.20, 0.20)
                obj.ViewObject.LineColor = color
                obj.ViewObject.PointColor = color

    def _item_for_index(self, index):
        for row in range(self.contour_list.count()):
            item = self.contour_list.item(row)
            if int(item.data(QtCore.Qt.UserRole)) == index:
                return item
        return None

    def _item_changed(self, item):
        if self._updating_checks:
            return
        index = int(item.data(QtCore.Qt.UserRole))
        self._set_preview_color(index, item.checkState() == QtCore.Qt.Checked)

    def _set_all_checked(self, included):
        state = QtCore.Qt.Checked if included else QtCore.Qt.Unchecked
        for row in range(self.contour_list.count()):
            self.contour_list.item(row).setCheckState(state)

    def addSelection(self, document_name, object_name, _sub_name, _point):
        if document_name != self.source.Document.Name:
            return
        index = self._preview_objects.get(object_name)
        if index is None:
            return
        item = self._item_for_index(index)
        if item is not None:
            item.setCheckState(
                QtCore.Qt.Unchecked
                if item.checkState() == QtCore.Qt.Checked
                else QtCore.Qt.Checked
            )
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
        doc.recompute()

    def accept(self):
        if self._contours:
            selected = []
            for row in range(self.contour_list.count()):
                item = self.contour_list.item(row)
                if item.checkState() == QtCore.Qt.Checked:
                    selected.append(int(item.data(QtCore.Qt.UserRole)))
            if not selected:
                QtWidgets.QMessageBox.warning(
                    self, "Split to enclosure", "Select at least one joint contour."
                )
                return
            self._selected_contour_indices = selected
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
        container = doc.addObject("App::Part", "Split2EnclosureResult")
        container.Label = "Split enclosure: {}".format(source.Label)
        container.addProperty("App::PropertyLink", "Source", "Split parameters")
        container.addProperty("App::PropertyString", "PlaneMode", "Split parameters")
        container.addProperty("App::PropertyLink", "SplitSketch", "Split parameters")
        container.addProperty("App::PropertyVector", "PlaneOrigin", "Split parameters")
        container.addProperty("App::PropertyVector", "PlaneNormal", "Split parameters")
        container.addProperty("App::PropertyLength", "PlaneOffset", "Split parameters")
        container.addProperty("App::PropertyLength", "LipWidth", "Joint parameters")
        container.addProperty("App::PropertyLength", "LipHeight", "Joint parameters")
        container.addProperty("App::PropertyLength", "SideClearance", "Joint parameters")
        container.addProperty("App::PropertyLength", "DepthClearance", "Joint parameters")
        container.addProperty("App::PropertyString", "LipSide", "Joint parameters")
        container.addProperty("App::PropertyString", "ContourMode", "Joint parameters")
        container.addProperty("App::PropertyString", "ContourSelection", "Joint parameters")
        container.addProperty("App::PropertyInteger", "JointContours", "Diagnostics")

        container.Source = source
        container.SplitSketch = parameters.get("split_sketch")
        container.PlaneMode = parameters["plane_mode"]
        container.PlaneOrigin = parameters["plane_origin"]
        container.PlaneNormal = parameters["plane_normal"]
        container.PlaneOffset = parameters["offset"]
        container.LipWidth = parameters["lip_width"]
        container.LipHeight = parameters["lip_height"]
        container.SideClearance = parameters["clearance"]
        container.DepthClearance = parameters["vertical_clearance"]
        container.LipSide = parameters["lip_on"]
        container.ContourMode = (
            "selected"
            if parameters.get("contour_indices") is not None
            else parameters["contour_mode"]
        )
        container.ContourSelection = ",".join(
            str(index + 1) for index in (parameters.get("contour_indices") or [])
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
        negative.Joint = "Lip" if parameters["lip_on"] == "negative" else "Groove"
        positive.Joint = "Lip" if parameters["lip_on"] == "positive" else "Groove"
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
    source, reference_face, split_sketch = _selection_context()
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
