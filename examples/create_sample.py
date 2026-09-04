"""Create a closed hollow box and split it into a demonstrator enclosure."""

import os
import sys

import FreeCAD as App
import Part

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from freecad.Split2Enclosure.geometry import make_enclosure, plane_from_axes


doc = App.newDocument("Split2EnclosureSample")

outer = Part.makeBox(60, 40, 24)
cavity = Part.makeBox(56, 36, 20, App.Vector(2, 2, 2))
source_shape = outer.cut(cavity)

source = doc.addObject("Part::Feature", "SourceEnclosure")
source.Label = "Source enclosure (hidden)"
source.Shape = source_shape

origin, normal = plane_from_axes("XY", 12)
result = make_enclosure(
    source_shape,
    origin,
    normal,
    lip_width=0.8,
    lip_height=2.0,
    clearance=0.2,
    vertical_clearance=0.2,
)

negative = doc.addObject("Part::Feature", "NegativeHalfLip")
negative.Label = "Lower half with lip"
negative.Shape = result.negative
if negative.ViewObject is not None:
    negative.ViewObject.ShapeColor = (0.30, 0.65, 0.95)

positive = doc.addObject("Part::Feature", "PositiveHalfGroove")
positive.Label = "Upper half with groove"
positive.Shape = result.positive
if positive.ViewObject is not None:
    positive.ViewObject.ShapeColor = (0.95, 0.65, 0.25)

if source.ViewObject is not None:
    source.ViewObject.Visibility = False
doc.recompute()

output_path = os.path.join(PROJECT_ROOT, "examples", "Split2EnclosureSample.FCStd")
doc.saveAs(output_path)
print("Created", output_path)
