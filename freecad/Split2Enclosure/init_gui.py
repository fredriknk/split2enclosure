import os

import FreeCADGui as Gui


ADDON_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))


class Split2EnclosureWorkbench(Gui.Workbench):
    MenuText = "Split2Enclosure"
    ToolTip = "Split hollow solids and create matched enclosure joints"

    def Initialize(self):
        from . import command  # noqa: F401

        commands = ["Split2Enclosure_Create"]
        self.appendToolbar("Split2Enclosure", commands)
        self.appendMenu("Split2Enclosure", commands)

    def GetClassName(self):
        return "Gui::PythonWorkbench"


Split2EnclosureWorkbench.Icon = os.path.join(
    ADDON_ROOT, "Resources", "icons", "split2enclosure.svg"
)
Gui.addWorkbench(Split2EnclosureWorkbench())
