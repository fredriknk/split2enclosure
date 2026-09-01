# Split2Enclosure

Split2Enclosure is a FreeCAD 1.1 workbench that turns a hollow solid into two
printable enclosure halves with a matched lip and groove.

The current MVP:

- splits a solid on global XY, XZ, or YZ, with a signed offset;
- accepts a selected planar face or Part Design datum plane as the reference;
- can follow the **outermost perimeter(s)** or all nested internal contours in
  the planar wall cross-section;
- adds a lip to either half and cuts a wider/deeper groove into the other;
- supports multiple internal contours, such as cavities separated by a divider;
- leaves the source untouched and creates two static `Part::Feature` results.

## Install for development

Copy or junction the complete repository folder into FreeCAD's user `Mod`
directory. In FreeCAD, that location is available from **Edit → Preferences →
Python → Macro** / the user application-data directory; on a typical Windows
FreeCAD 1.1 installation it is:

```text
%APPDATA%\FreeCAD\v1-1\Mod\Split2Enclosure
```

Restart FreeCAD and select the **Split2Enclosure** workbench. Keep the whole
folder together; `Split2Enclosure.FCMacro` is only a launcher for the installed
Python package.

## Use

1. Select one object whose `Shape` contains a valid solid.
2. Optionally Ctrl-select a planar face or a Part Design datum plane.
3. Run **Split2Enclosure → Split to enclosure**.
4. Choose the plane, signed plane offset, outer/internal contour mode, lip
   side, lip width/height, and the side/depth clearances.
5. Press **Create**. The original is hidden and an App Part containing the two
   resulting halves is added to the document.

Plane normals and positive offset directions are:

| Plane | Positive normal |
|---|---|
| XY | +Z |
| XZ | +Y |
| YZ | +X |

`Side clearance` is the total groove-minus-lip width. The lip is centered in
that groove, giving half the value on each side. `Depth clearance` is added to
the groove depth beyond the lip height.

## Geometry model

The engine intersects the source BRep with the split plane. It classifies the
outermost contour of each disconnected section as exterior and every nested
contour as internal. It builds a two-sided planar offset band around the chosen
contours, clips that band to the actual wall material, then extrudes the lip
and groove normal to the split plane. Outermost mode therefore ignores screw
holes and other nested openings.

Full-circle contours use an exact analytic offset because OpenCASCADE's 2D
offset builder can return a null result for a wire made from one circular edge.
If OpenCASCADE rejects another complex closed contour, the engine falls back to
a finely discretized GEOS/Shapely offset for that contour only.

This is more reliable than collecting model edges by name: FreeCAD's generated
edge numbers can change after Boolean operations, while the cross-section is
derived directly from the final solid.

## Current limitations

- The source must be a BRep solid, not a mesh. Convert meshes before running.
- Internal joint paths must be closed. A vertical cut through an open-top shell
  produces an open U-shaped cavity boundary and is deliberately rejected.
- Very thin walls, tight concave radii, or a lip wider than the available wall
  can make OpenCASCADE offsets fail. Reduce width/clearance or move the plane.
- Results are static features in this MVP. Change parameters by deleting the
  result group and running the command again.
- Draft angle is not yet applied; lip walls are normal to the split plane.

## Tests and sample

Run the headless regression suite with the matching FreeCAD executable:

```powershell
& 'C:\Program Files\FreeCAD 1.1\bin\FreeCADCmd.exe' 'tests\run_geometry_tests.py'
```

Generate a visual `.FCStd` example:

```powershell
& 'C:\Program Files\FreeCAD 1.1\bin\FreeCADCmd.exe' 'examples\create_sample.py'
```
