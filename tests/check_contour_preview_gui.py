"""Run under FreeCAD.exe to exercise the interactive contour preview."""

import os
import sys
import traceback

import FreeCAD as App
import FreeCADGui as Gui
import Part
import Sketcher
from PySide import QtCore, QtWidgets


PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def run_check():
    try:
        for name in list(sys.modules):
            if name == "split2enclosure" or name.startswith("split2enclosure."):
                del sys.modules[name]
        sys.path.insert(0, PROJECT_ROOT)
        from split2enclosure.command import EnclosureDialog, _selection_context

        doc = App.newDocument("Split2EnclosurePreviewCheck")
        outer = Part.makeBox(40, 30, 20)
        cavity = Part.makeBox(36, 26, 16, App.Vector(2, 2, 2))
        hole = Part.makeCylinder(0.55, 20, App.Vector(1, 8, 0))
        source = doc.addObject("Part::Feature", "Source")
        source.Shape = outer.cut(cavity).cut(hole)
        doc.recompute()

        dialog = EnclosureDialog(source, None, Gui.getMainWindow())
        dialog.offset.setValue(10.0)
        original_warning = QtWidgets.QMessageBox.warning

        def fail_warning(_parent, _title, message):
            raise RuntimeError(message)

        QtWidgets.QMessageBox.warning = fail_warning
        try:
            dialog._build_preview()
        finally:
            QtWidgets.QMessageBox.warning = original_warning

        assert dialog.contour_list.count() == 3
        negative = sum(
            str(dialog.contour_list.item(row).data(QtCore.Qt.UserRole + 1))
            == "negative"
            for row in range(dialog.contour_list.count())
        )
        assert negative == 1
        assert len(dialog._preview_objects) == 6

        preview_name = next(iter(dialog._preview_objects))
        preview_index = dialog._preview_objects[preview_name]
        item = dialog._item_for_index(preview_index)
        previous_side = str(item.data(QtCore.Qt.UserRole + 1))
        Gui.Selection.addSelection(doc.getObject(preview_name))
        assert str(item.data(QtCore.Qt.UserRole + 1)) != previous_side

        dialog.contour_list.clearSelection()
        dialog.contour_list.item(0).setSelected(True)
        dialog.contour_list.item(1).setSelected(True)
        dialog._set_selected_side("positive")
        assert all(
            str(dialog.contour_list.item(row).data(QtCore.Qt.UserRole + 1))
            == "positive"
            for row in (0, 1)
        )

        dialog.accept()
        assignments = dialog.parameters()["contour_sides"]
        assert assignments[0] == "positive"
        assert assignments[1] == "positive"
        assert doc.getObject("Split2EnclosurePreview") is None

        sketch = doc.addObject("Sketcher::SketchObject", "SplitPath")
        sketch.addGeometry(
            [
                Part.LineSegment(App.Vector(-5, 15), App.Vector(12, 15)),
                Part.LineSegment(App.Vector(12, 15), App.Vector(18, 20)),
                Part.LineSegment(App.Vector(18, 20), App.Vector(27, 20)),
                Part.LineSegment(App.Vector(27, 20), App.Vector(45, 13)),
            ],
            False,
        )
        doc.recompute()
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(source)
        Gui.Selection.addSelection(sketch)
        selected_source, selected_reference, selected_sketch = _selection_context()
        assert selected_source == source
        assert selected_reference is None
        assert selected_sketch == sketch
        Gui.Selection.clearSelection()
        sketch_dialog = EnclosureDialog(
            source,
            None,
            Gui.getMainWindow(),
            split_sketch=sketch,
        )
        assert sketch_dialog.plane_mode.currentText() == "Selected open sketch"
        assert not sketch_dialog.offset.isEnabled()
        QtWidgets.QMessageBox.warning = fail_warning
        try:
            sketch_dialog._build_preview()
        finally:
            QtWidgets.QMessageBox.warning = original_warning
        assert sketch_dialog.contour_list.count() >= 2
        assert sketch_dialog.parameters()["split_kind"] == "sketch"
        assert len(sketch_dialog._preview_objects) == 2 * sketch_dialog.contour_list.count()
        sketch_dialog.reject()
        assert doc.getObject("Split2EnclosurePreview") is None

        print("SPLIT2ENCLOSURE_CONTOUR_PREVIEW_OK")
        dialog.deleteLater()
        sketch_dialog.deleteLater()
        App.closeDocument(doc.Name)
    except Exception:
        traceback.print_exc()
        print("SPLIT2ENCLOSURE_CONTOUR_PREVIEW_FAILED")
    finally:
        Gui.getMainWindow().close()


QtCore.QTimer.singleShot(1000, run_check)
