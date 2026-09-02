import os
import sys
import tempfile
import unittest

import FreeCAD as App
import Part


PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from split2enclosure.config import DEFAULTS
from split2enclosure.settings import (
    find_source_settings,
    load_source_defaults,
    save_source_defaults,
    saved_split_defaults,
    settings_owner,
)


def parameters(**overrides):
    values = {
        "lip_width": 1.4,
        "lip_height": 2.8,
        "clearance": 0.24,
        "vertical_clearance": 0.31,
        "draft_angle": 1.5,
        "snap_radius": 0.18,
        "snap_clearance": 0.06,
        "snap_position": 0.62,
        "lip_on": "positive",
        "plane_mode": "Global XZ",
        "offset": 7.5,
    }
    values.update(overrides)
    return values


class SourceSettingsTests(unittest.TestCase):
    def tearDown(self):
        for name in list(App.listDocuments()):
            if name.startswith("Split2EnclosureSettings"):
                App.closeDocument(name)

    def test_settings_follow_containing_body_and_update_in_place(self):
        document = App.newDocument("Split2EnclosureSettingsBody")
        body = document.addObject("PartDesign::Body", "Body")
        feature = body.newObject("PartDesign::Feature", "Enclosure")
        feature.Shape = Part.makeBox(10, 10, 10)
        document.recompute()

        self.assertIs(settings_owner(feature), body)
        settings = save_source_defaults(feature, parameters())
        self.assertEqual(settings.TypeId, "App::VarSet")
        self.assertIs(settings.Source, body)
        self.assertIs(find_source_settings(body), settings)
        loaded, found = load_source_defaults(feature, DEFAULTS)
        self.assertIs(found, settings)
        self.assertAlmostEqual(loaded["lip_width"], 1.4)
        self.assertAlmostEqual(loaded["snap_position"], 0.62)
        self.assertEqual(loaded["default_lip_side"], "positive")
        self.assertEqual(saved_split_defaults(settings)["plane_mode"], "Global XZ")
        self.assertAlmostEqual(saved_split_defaults(settings)["offset"], 7.5)

        updated = save_source_defaults(feature, parameters(lip_width=1.9))
        self.assertIs(updated, settings)
        self.assertAlmostEqual(updated.LipWidth.Value, 1.9)
        self.assertEqual(
            len(
                [
                    obj
                    for obj in document.Objects
                    if obj.TypeId == "App::VarSet"
                ]
            ),
            1,
        )

    def test_settings_survive_fcstd_save_and_reopen(self):
        document = App.newDocument("Split2EnclosureSettingsPersistence")
        source = document.addObject("Part::Feature", "Source")
        source.Shape = Part.makeBox(10, 10, 10)
        settings = save_source_defaults(source, parameters(lip_height=3.25))
        settings_name = settings.Name

        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "settings.FCStd")
            document.recompute()
            document.saveAs(path)
            App.closeDocument(document.Name)
            reopened = App.openDocument(path)
            reopened_source = reopened.getObject("Source")
            reopened_settings = find_source_settings(reopened_source)
            self.assertIsNotNone(reopened_settings)
            self.assertEqual(reopened_settings.Name, settings_name)
            loaded, _found = load_source_defaults(reopened_source, DEFAULTS)
            self.assertAlmostEqual(loaded["lip_height"], 3.25)
            self.assertEqual(loaded["default_lip_side"], "positive")


suite = unittest.defaultTestLoader.loadTestsFromTestCase(SourceSettingsTests)
result = unittest.TextTestRunner(verbosity=2).run(suite)
if not result.wasSuccessful():
    raise RuntimeError("Source settings test suite failed")
