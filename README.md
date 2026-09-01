# Split2Enclosure

**Turn an existing FreeCAD solid into two printable enclosure halves with an
automatically generated lip-and-groove joint.**

Split2Enclosure is a FreeCAD 1.1 workbench for splitting complex solids into
enclosure halves while preserving the geometry around the joint — including
holes, bosses, curved walls, slopes and other local features. This allows you
to work on a single model and then split it into two parts for 3D printing.

![Split2Enclosure interface](media/Interface.png)

## What it does

Split2Enclosure takes a solid like this and:

- splits it using a global plane, planar face, datum plane;
- detects the resulting joint contours automatically;
- lets you assign every contour to the negative half, positive half, or off;
- supports Shift/Ctrl multi-selection and shows each lip's extrusion direction;
- cuts a matching groove with configurable side and depth clearance;
- optionally drafts the joint and adds per-contour snap retention;
- preserves existing geometry around the split;
- produces two ready-to-export solid parts.

### Result

<table>
<tr>
<td width="50%">

**Top half**

<img src="media/Split_part_top.png" width="100%">

</td>
<td width="50%">

**Bottom half**

<img src="media/Split_part_bottom.png" width="100%">

</td>
</tr>
</table>

## Quick start

1. Select the solid you want to split.
2. Optionally Ctrl-select a planar face, datum plane, to use as
   the split reference.
3. Open the **Split2Enclosure** workbench.
4. Click **Split to enclosure**.
5. Choose the split plane and press **Preview / choose contours**.
6. Click contours in the 3D view, or Shift/Ctrl-select list rows and use
   **NEG**, **OFF**, or **POS**, to choose the lip owner.
7. Optionally select rows and press **SNAP** to add continuous retention ribs
   and matching channels around those perimeters.
8. Set lip dimensions, clearances, and optional draft.
9. Press **Create**.

The original solid is left untouched. Split2Enclosure creates two new
`Part::Feature` solids inside an App Part.

## Joint parameters

| Parameter | Description |
|---|---|
| **Default contour side** | Initial lip owner for newly previewed outer contours |
| **Lip width** | Width of the tongue measured into the wall |
| **Lip height** | Height of the tongue across the split |
| **Side clearance** | Additional lateral clearance in the mating groove |
| **Depth clearance** | Axial gap beyond the lip tip and between opposing flat shoulders |
| **Draft angle** | Optional taper on the generated lip and groove limiting volumes |
| **Snap seam half-size** | Half the wedge height and its lateral reach (45-degree faces) |
| **Snap channel clearance** | Extra width and height around the matching channel |
| **Snap height fraction** | Rib position from lip root (`0.1` to `0.9`) |

Blue contours/arrows are lips owned by the **negative** half, orange by the
**positive** half, and gray contours are **off**. `[SNAP]` in a list row means
that contour also receives retention geometry.

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
5. Assign contours with **NEG**, **OFF**, and **POS**. Shift/Ctrl-selection
   applies an assignment to several rows; clicking a contour or its arrow in
   the 3D view cycles its assignment.
6. Optionally use **SNAP** on selected rows, then choose joint dimensions,
   clearances, and draft angle.
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
wall. `Depth clearance` leaves the requested axial room both beyond the lip tip
and across the non-lip shoulder surfaces. The lip-owning shoulder is relieved
by that amount while the lip root remains attached at the original split.

## Configurable defaults

Edit `split2enclosure_defaults.json` in the add-on's root directory to change
the values used whenever the dialog opens. The included file documents all
supported keys by example: lip width/height, both clearances, draft angle,
default lip side, and the three snap settings. Values use millimetres, degrees,
or the unitless snap-height fraction as appropriate. Restarting FreeCAD is not
required; defaults are read when a new dialog is opened. Invalid files produce
a warning and safely fall back to built-in defaults.

## Geometry model

The engine intersects the source BRep with the split plane and classifies every
closed contour for display. It builds a two-sided planar offset band around the
user-selected contours and clips that band to the actual wall material.

For an open-sketch split, the engine extrudes the sketch along its support-plane
normal beyond the model bounds. OpenCASCADE partitions the body with the
resulting ruled surface. Connected boundary edges are unfolded into
sketch-path-distance coordinates for outer/internal contour classification.
The engine fits one signed joint direction that crosses every ruled panel and
uses it for the complete lip, groove, and preview arrows. A straight sketch
uses its exact surface normal; a polyline uses the bisector of its limiting
panel normals. This avoids the distortion caused by forcing a diagonal seam
onto a global axis while still keeping polyline corners continuous. Coplanar
section fragments created by OpenCASCADE are buffered as one region so their
topological boundaries cannot introduce small breaks in the joint.

The groove is cut with a widened/deepened band, and the lip-owning flat
shoulder is set back by the same depth-clearance value. The nominal lip prism
is first intersected with the unmodified receiving half, and only that existing
material is transferred to the lip half. Consequently, holes, slopes, curved
walls, and features beginning immediately after the split plane remain in the
lip instead of being covered by a uniform extrusion.

An optional draft angle lofts between the root footprint and a smaller tip
footprint. On unusually complex clipped sketch panels where OpenCASCADE cannot
construct that loft, only the affected panel falls back to a straight prism.
Per-contour snap retention narrows the tongue and lofts out to a continuous
diamond/wedge seam at the requested height. Its outward face rises one unit for
each unit of lateral reach, limiting the unsupported surface to 45 degrees. A
similarly lofted channel adds the configured lateral and axial clearance. The
resulting printable undercut follows the actual perimeter without placing
dimples or holes in the enclosure wall.

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
- A sketch split must admit one consistent direction through every ruled panel;
  paths that reverse or fold back on themselves cannot form a continuous joint.
- Very thin walls, tight concave radii, or a lip wider than the available wall
  can make offsets fail. Reduce width/clearance or move the plane.
- Results are static features. Change parameters by deleting the result group
  and running the command again.
- Snap retention creates one continuous 45-degree wedge/channel seam on each
  enabled contour. Segmented snaps or multiple independently positioned ribs
  are not yet supported.

## v0.4.2 corrections

- Snap seams use printable 45-degree wedge faces with clearance-matched channels.
- Depth clearance now applies equally at the lip tip and flat mating shoulders.
- Sketch joints use a fitted common direction, including the exact normal for
  straight diagonal sketches, and merge coplanar section fragments before
  offsetting.
- `examples/Splitbox_test.FCStd` is covered by the geometry regression suite.

## v0.4.1 corrections

- Sketch seams use one global extrusion axis instead of changing direction at
  every polyline panel.
- Lip-root clearance recesses were removed.
- Point dimples were replaced by continuous perimeter snap ribs and channels.

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

## Disclaimer

This work is made with OpenAI Codex, and I am a hobbyist who needed this
functionality. I am not a professional software developer. Use at your own
risk.
