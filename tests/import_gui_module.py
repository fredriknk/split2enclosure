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
