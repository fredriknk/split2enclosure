"""FreeCAD GUI command for Split2Enclosure."""

import os

import FreeCAD as App
import FreeCADGui as Gui
import Part
from PySide import QtCore, QtWidgets

from .geometry import make_enclosure, plane_from_axes


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

    for selection in Gui.Selection.getSelectionEx():
        obj = selection.Object
        shape = _shape_of(obj)
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
    return source, reference


def _plane_from_face(face, offset):
    u_min, u_max, v_min, v_max = face.ParameterRange
    u_mid = (u_min + u_max) * 0.5
    v_mid = (v_min + v_max) * 0.5
    origin = face.valueAt(u_mid, v_mid)
    normal = face.normalAt(u_mid, v_mid)
    normal.normalize()
    return origin + normal * offset, normal


class EnclosureDialog(QtWidgets.QDialog):
    def __init__(self, source, reference_face, parent=None):
        super().__init__(parent)
        self.source = source
        self.reference_face = reference_face
        self.setWindowTitle("Split to enclosure")
        self.setMinimumWidth(390)

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
        form.addRow("Split plane", self.plane_mode)

        self.offset = self._length_box(-100000.0, 100000.0, 0.0)
        self.offset.setToolTip("Distance along the plane's positive normal")
        form.addRow("Plane offset", self.offset)

        self.lip_side = QtWidgets.QComboBox()
        self.lip_side.addItem("Negative half", "negative")
        self.lip_side.addItem("Positive half", "positive")
        form.addRow("Lip belongs to", self.lip_side)

        self.contour_mode = QtWidgets.QComboBox()
        self.contour_mode.addItem("Outermost perimeter(s)", "outer")
        self.contour_mode.addItem("Internal contours", "internal")
        self.contour_mode.setToolTip(
            "Outermost mode ignores holes and nested section details"
        )
        form.addRow("Joint follows", self.contour_mode)

        self.lip_width = self._length_box(0.01, 1000.0, 1.0)
        self.lip_height = self._length_box(0.01, 1000.0, 2.0)
        self.clearance = self._length_box(0.0, 100.0, 0.2)
        self.vertical_clearance = self._length_box(0.0, 100.0, 0.2)
        form.addRow("Lip width", self.lip_width)
        form.addRow("Lip height", self.lip_height)
        form.addRow("Side clearance", self.clearance)
        form.addRow("Depth clearance", self.vertical_clearance)

        note = QtWidgets.QLabel(
            "Outermost mode ignores holes and keeps the band on the material "
            "side of each exterior section perimeter. Clearance is centered "
            "around the lip."
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

    @staticmethod
    def _length_box(minimum, maximum, value):
        box = QtWidgets.QDoubleSpinBox()
        box.setDecimals(3)
        box.setRange(minimum, maximum)
        box.setValue(value)
        box.setSuffix(" mm")
        box.setSingleStep(0.1)
        return box

    def parameters(self):
        mode = self.plane_mode.currentText()
        offset = self.offset.value()
        if mode.startswith("Global"):
            origin, normal = plane_from_axes(mode.split()[-1], offset)
        else:
            origin, normal = _plane_from_face(self.reference_face, offset)
        return {
            "plane_origin": origin,
            "plane_normal": normal,
            "lip_width": self.lip_width.value(),
            "lip_height": self.lip_height.value(),
            "clearance": self.clearance.value(),
            "vertical_clearance": self.vertical_clearance.value(),
            "lip_on": self.lip_side.currentData(),
            "contour_mode": self.contour_mode.currentData(),
            "plane_mode": mode,
            "offset": offset,
        }


def _add_result_to_document(source, result, parameters):
    doc = source.Document
    doc.openTransaction("Split enclosure with lip and groove")
    try:
        container = doc.addObject("App::Part", "Split2EnclosureResult")
        container.Label = "Split enclosure: {}".format(source.Label)
        container.addProperty("App::PropertyLink", "Source", "Split parameters")
        container.addProperty("App::PropertyString", "PlaneMode", "Split parameters")
        container.addProperty("App::PropertyVector", "PlaneOrigin", "Split parameters")
        container.addProperty("App::PropertyVector", "PlaneNormal", "Split parameters")
        container.addProperty("App::PropertyLength", "PlaneOffset", "Split parameters")
        container.addProperty("App::PropertyLength", "LipWidth", "Joint parameters")
        container.addProperty("App::PropertyLength", "LipHeight", "Joint parameters")
        container.addProperty("App::PropertyLength", "SideClearance", "Joint parameters")
        container.addProperty("App::PropertyLength", "DepthClearance", "Joint parameters")
        container.addProperty("App::PropertyString", "LipSide", "Joint parameters")
        container.addProperty("App::PropertyString", "ContourMode", "Joint parameters")
        container.addProperty("App::PropertyInteger", "JointContours", "Diagnostics")

        container.Source = source
        container.PlaneMode = parameters["plane_mode"]
        container.PlaneOrigin = parameters["plane_origin"]
        container.PlaneNormal = parameters["plane_normal"]
        container.PlaneOffset = parameters["offset"]
        container.LipWidth = parameters["lip_width"]
        container.LipHeight = parameters["lip_height"]
        container.SideClearance = parameters["clearance"]
        container.DepthClearance = parameters["vertical_clearance"]
        container.LipSide = parameters["lip_on"]
        container.ContourMode = parameters["contour_mode"]
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
    source, reference_face = _selection_context()
    if source is None:
        QtWidgets.QMessageBox.warning(
            Gui.getMainWindow(),
            "Split to enclosure",
            "Select one solid object first. Optionally also select a planar face "
            "or datum plane as the split reference.",
        )
        return

    dialog = EnclosureDialog(source, reference_face, Gui.getMainWindow())
    if dialog.exec() != QtWidgets.QDialog.Accepted:
        return
    try:
        parameters = dialog.parameters()
        engine_parameters = dict(parameters)
        engine_parameters.pop("plane_mode")
        engine_parameters.pop("offset")
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
