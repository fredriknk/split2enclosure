# Split2Enclosure

Split2Enclosure is a FreeCAD 1.1 workbench that turns a solid into two
printable enclosure halves with a matched lip and groove.

Features:

- splits a solid on global XY, XZ, or YZ, with a signed offset;
- accepts a selected planar face or Part Design datum plane as the reference;
- accepts a connected open line-segment sketch and extrudes it into a ruled
  cutting surface through the enclosure;
- previews every closed seam contour directly in the 3D view;
- lets contours be included or excluded using checkboxes or by clicking their
  green/red preview wires;
- adds a lip to either half and cuts a wider/deeper groove into the other;
- takes the lip geometry from the receiving half, preserving holes, slopes,
  and details that change immediately after the split plane;
- leaves the source untouched and creates two static `Part::Feature` results.

## Install

Clone or copy the complete repository folder into FreeCAD's user `Mod`
directory. On a typical Windows FreeCAD 1.1 installation it is:

```text
%APPDATA%\FreeCAD\v1-1\Mod\Split2Enclosure
```

Restart FreeCAD and select the **Split2Enclosure** workbench. Keep the whole
folder together; `Split2Enclosure.FCMacro` is only a launcher for the installed
Python package.

## Use

1. Select one object whose `Shape` contains a valid solid.
2. Optionally Ctrl-select a planar face, Part Design datum plane, or connected
   open sketch. For a sketch split, draw the path on a side of the model and
   extend both endpoints beyond the enclosure.
3. Run **Split2Enclosure > Split to enclosure**.
4. Choose the plane/sketch split mode and signed offset (plane modes only),
   then press **Preview / choose contours**.
5. Included contours are green and excluded contours are red. Toggle them in
   the list or click their wires in the 3D view.
6. Choose the lip side, lip width/height, and side/depth clearances.
7. Press **Create**. The original is hidden and an App Part containing the two
   resulting halves is added to the document.

Plane normals and positive offset directions are:

| Plane | Positive normal |
|---|---|
| XY | +Z |
| XZ | +Y |
| YZ | +X |

`Side clearance` is added only at the material-side mating face: the lip stays
anchored to the selected perimeter and the groove extends farther into the
wall. `Depth clearance` is added beyond the lip height.

## Geometry model

The engine intersects the source BRep with the split plane and classifies every
closed contour for display. It builds a two-sided planar offset band around the
user-selected contours and clips that band to the actual wall material.

For an open-sketch split, the engine extrudes the sketch along its support-plane
normal beyond the model bounds. OpenCASCADE partitions the body with the
resulting ruled surface. Each planar surface panel receives its own local joint
normal, while connected boundary edges are unfolded into sketch-path-distance
coordinates for outer/internal contour classification. Lip material is then
transferred face-by-face and the exact transferred shape is included in the
groove cut to prevent overlap at angled mitres.

The groove is cut with a widened/deepened band. The nominal lip prism is first
intersected with the unmodified receiving half, and only that existing material
is transferred to the lip half. Consequently, holes, slopes, curved walls, and
features beginning immediately after the split plane remain in the lip instead
of being covered by a uniform extrusion.

Full-circle contours use an exact analytic offset because OpenCASCADE's 2D
offset builder can return a null result for a wire made from one circular edge.
If OpenCASCADE rejects another complex closed contour, the engine falls back to
a finely discretized GEOS/Shapely offset for that contour only.

This is more reliable than collecting model edges by name: FreeCAD's generated
edge numbers can change after Boolean operations, while the cross-section is
derived directly from the final solid.

## Current limitations

- The source must be a BRep solid, not a mesh. Convert meshes before running.
- Joint paths must be closed. A vertical cut through an open-top shell can
  produce an open U-shaped cavity boundary, which cannot yet be selected.
- Sketch splits currently accept exactly one connected, open chain made from
  line segments. Branches, self-intersections, closed profiles, arcs, and
  B-splines are not yet supported.
- A sketch split must divide the source into exactly two solids. Its endpoints
  should extend beyond the projected model boundary.
- Very thin walls, tight concave radii, or a lip wider than the available wall
  can make offsets fail. Reduce width/clearance or move the plane.
- Results are static features. Change parameters by deleting the result group
  and running the command again.
- Draft angle is not yet applied; lip walls follow the receiving half's local
  geometry but the limiting prism is normal to the plane or each ruled-surface
  panel.

## Tests and sample

Run the headless regression suite with the matching FreeCAD executable:

```powershell
& 'C:\Program Files\FreeCAD 1.1\bin\FreeCADCmd.exe' 'tests\run_geometry_tests.py'
```

Generate a visual `.FCStd` example:

```powershell
& 'C:\Program Files\FreeCAD 1.1\bin\FreeCADCmd.exe' 'examples\create_sample.py'
```

## License

Split2Enclosure is released under the [MIT License](LICENSE).
