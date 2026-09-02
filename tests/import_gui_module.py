"""Smoke-import the GUI module; FreeCADCmd has no active view or dialog."""

import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import FreeCAD as App
import Part
from PySide import QtWidgets

from split2enclosure.command import EnclosureDialog
from split2enclosure.settings import save_source_defaults

application = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
document = App.newDocument("Split2EnclosureGuiSmoke")
source = document.addObject("Part::Feature", "Source")
source.Shape = Part.makeBox(10, 10, 10)
dialog = EnclosureDialog(source, None)
parameters = dialog.parameters()
assert parameters["plane_mode"] == "Global XY"
assert parameters["contour_mode"] == "outer"
assert parameters["snap_radius"] > 0
assert parameters["snap_clearance"] >= 0
assert 0.1 <= parameters["snap_position"] <= 0.9
assert parameters["contour_snaps"] is None
dialog.close()

save_source_defaults(
    source,
    {
        "lip_width": 1.65,
        "lip_height": 2.75,
        "clearance": 0.23,
        "vertical_clearance": 0.34,
        "draft_angle": 1.0,
        "snap_radius": 0.17,
        "snap_clearance": 0.04,
        "snap_position": 0.61,
        "lip_on": "positive",
        "plane_mode": "Global YZ",
        "offset": 4.5,
    },
)
remembered_dialog = EnclosureDialog(source, None)
remembered_parameters = remembered_dialog.parameters()
assert remembered_parameters["plane_mode"] == "Global YZ"
assert remembered_parameters["offset"] == 4.5
assert remembered_parameters["lip_width"] == 1.65
assert remembered_parameters["lip_height"] == 2.75
assert remembered_parameters["lip_on"] == "positive"
assert remembered_parameters["remember_settings"]
assert remembered_dialog.source_settings is not None
remembered_dialog.close()

sketch = document.addObject("Part::Feature", "SplitSketch")
sketch.Shape = Part.makePolygon(
    [App.Vector(-2, 5, 0), App.Vector(5, 7, 0), App.Vector(12, 5, 0)]
)
sketch_dialog = EnclosureDialog(source, None, split_sketch=sketch)
sketch_parameters = sketch_dialog.parameters()
assert sketch_parameters["split_kind"] == "sketch"
assert sketch_parameters["plane_mode"] == "Selected open sketch"
assert not sketch_dialog.offset.isEnabled()
sketch_dialog.close()
App.closeDocument(document.Name)

print("GUI module imported")
