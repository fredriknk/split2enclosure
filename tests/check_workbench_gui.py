"""Launch under FreeCAD.exe to report whether the workbench was registered."""

import FreeCAD as App
import FreeCADGui as Gui
from PySide import QtCore


def report_and_close():
    workbenches = Gui.listWorkbenches()
    print("SPLIT2ENCLOSURE_WORKBENCHES", sorted(workbenches))
    print(
        "SPLIT2ENCLOSURE_REGISTERED",
        "Split2EnclosureWorkbench" in workbenches,
    )
    App.Console.PrintMessage(
        "Split2Enclosure registered: {}\n".format(
            "Split2EnclosureWorkbench" in workbenches
        )
    )
    if "Split2EnclosureWorkbench" in workbenches:
        Gui.activateWorkbench("Split2EnclosureWorkbench")
    commands = Gui.listCommands()
    print(
        "SPLIT2ENCLOSURE_COMMAND_REGISTERED",
        "Split2Enclosure_Create" in commands,
    )
    App.Console.PrintMessage(
        "Split2Enclosure command registered: {}\n".format(
            "Split2Enclosure_Create" in commands
        )
    )
    Gui.getMainWindow().close()


QtCore.QTimer.singleShot(1000, report_and_close)
