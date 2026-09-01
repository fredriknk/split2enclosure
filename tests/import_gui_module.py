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
dialog.close()
App.closeDocument(document.Name)

print("GUI module imported")
