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

- splits it using a global plane, a selected planar face or datum plane, or
  one connected open line-segment sketch;
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
2. Optionally Ctrl-select a planar face, datum plane, or connected open
   line-segment sketch as the split reference. For a sketch split, extend both
   endpoints beyond the model.
3. Open the **Split2Enclosure** workbench.
4. Click **Split to enclosure**.
5. Choose the split plane and press **Preview / choose contours**.
6. Click contours in the 3D view, or Shift/Ctrl-select list rows and use
   **NEG**, **OFF**, or **POS**, to choose the lip owner.
7. Optionally select rows and press **SNAP** to add continuous, printable
   45-degree wedge seams and matching channels around those perimeters.
8. Set lip dimensions, clearances, and optional draft.
9. Press **Create**.

The original solid is left untouched. Split2Enclosure creates two new
`Part::Feature` solids inside an App Part.

## Joint parameters

The dialog uses the FreeCAD document's length units. The included defaults are
shown in millimetres.

| Parameter and defaults key | Default | Dialog range | Description |
|---|---:|---:|---|
| **Default contour side** (`default_lip_side`) | Negative | Negative or Positive | Initial lip owner for outer contours |
| **Lip width** (`lip_width`) | 1.0 mm | 0.01 to 1000 mm | Width of the tongue measured into the wall |
| **Lip height** (`lip_height`) | 2.0 mm | 0.01 to 1000 mm | Tongue length along the assembly axis |
| **Side clearance** (`side_clearance`) | 0.2 mm | 0 to 100 mm | Additional lateral clearance in the mating groove |
| **Depth clearance** (`depth_clearance`) | 0.2 mm | 0 to 100 mm | Axial gap beyond the lip tip and between opposing flat shoulders |
| **Draft angle** (`draft_angle`) | 0 degrees | 0 to 30 degrees | Optional taper on the lip and groove limiting volumes |
| **Snap seam half-size** (`snap_radius`) | 0.2 mm | 0.01 to 100 mm | Half the wedge height and its lateral reach; the ramps are 45 degrees |
| **Snap channel clearance** (`snap_clearance`) | 0.05 mm | 0 to 100 mm | Extra lateral and axial room around the matching channel |
| **Snap height fraction** (`snap_position`) | 0.70 | 0.10 to 0.90 | Seam centre measured from the lip root as a fraction of lip height |

Plane offset defaults to 0 mm and accepts -100000 to +100000 mm. It applies
only to global-plane, selected-face, and datum-plane splits.

### Contours and preview

Blue contours/arrows are lips owned by the **negative** half, orange by the
**positive** half, and gray contours are **off**. `[SNAP]` in a list row means
that an enabled **NEG** or **POS** contour also receives retention geometry.

An **outer** contour is the outermost boundary of each disconnected material
region in the split section. An **internal** contour is a nested boundary such
as a cavity or through-hole. Outer contours initially use **Default contour
side**; internal contours initially start **OFF**. The preview lists outer
contours first, then internal contours, with larger enclosed areas first.

The preview shows contour ownership and the lip's straight assembly direction;
it does not run or display the final lip, groove, clearance, draft, or snap
Booleans. Changing the plane mode or offset clears the assignments, so press
**Preview / choose contours** again. Preview is optional: if it is skipped,
all outer contours use the default side, all internal contours stay off, and
no snap seams are generated.

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

For a selected planar face or datum plane, positive offset follows that
reference's oriented normal. The preview arrows are the authoritative sign
indicator.

### Sketch splits and assembly direction

For a sketch split, the sketch defines **where the enclosure is cut**, not the
direction in which the finished halves mate. The sketch is extruded along its
support-plane normal to make a cutting curtain. Split2Enclosure then chooses
one signed global principal axis (`+/-X`, `+/-Y`, or `+/-Z`) that crosses every
panel of that curtain. The complete lip, groove, flat-shoulder relief, snap
seam/channel, and preview arrows all use this one axis. A diagonal sketch can
therefore make a diagonal cut path without creating a slanted insertion motion
or mating extrusion.

The automatically selected axis is the best-aligned usable global axis, not
necessarily the model's longest bounding-box dimension. It cannot currently be
chosen manually. If no global axis crosses every panel, reshape or reorient the
sketch so its path is monotonic along at least one global axis. For a lip owned
by **NEG**, the arrow points toward the positive side; a **POS** lip reverses
that direction.

`Side clearance` is added only at the material-side mating face: the lip stays
anchored to the selected perimeter and the groove extends farther into the
wall. `Depth clearance` leaves the requested axial room both beyond the lip tip
and across the non-lip shoulder surfaces. The lip-owning shoulder is relieved
by that amount while the lip root remains attached at the original split.

The depth-clearance value is not divided between the two locations. The full
entered value is used as extra room beyond the lip tip, and the same full value
is used between opposing flat shoulders. No clearance recess is cut underneath
the attached lip root.

### Snap and draft fit

A snap seam with half-size `r` spans `2r` along the assembly axis and reaches
`r` laterally from the narrowed tongue. The matching channel adds **Snap
channel clearance** in both directions. For an enabled snap contour, these
conditions must hold:

```text
snap half-size < lip width
snap half-size + snap channel clearance
    < lip height * min(snap height fraction, 1 - snap height fraction)
```

If the second check fails, reduce the snap size or channel clearance, or move
the height fraction toward `0.5`. Each wedge ramp is limited to 45 degrees
relative to the assembly axis. Orient each part with that axis vertical and the
joint facing upward in the slicer to obtain the intended 45-degree printing
overhang. Snap retention is continuous around each enabled contour; localized
or segmented snaps are not yet available.

Draft must also leave positive-width tip footprints. In practical terms,
`tan(draft) * lip height` must remain below the lip width, and
`tan(draft) * (lip height + depth clearance)` must remain below
`lip width + side clearance`. Reduce the angle on narrow or tall joints.

## Configurable defaults

Edit [`split2enclosure_defaults.json`](split2enclosure_defaults.json) in the
add-on's root directory to change the values used whenever the dialog opens.
The table above maps every UI parameter to its JSON key. A partial JSON object
is allowed; omitted keys retain their built-in values. Values must be numbers
except `default_lip_side`, which must be exactly `"negative"` or `"positive"`.

Restarting FreeCAD is not required; defaults are read when a new dialog opens.
An unknown key, invalid value, or malformed file invalidates that file as a
whole. Split2Enclosure emits a warning and safely uses all built-in defaults.

## Geometry model

The engine intersects the source BRep with the split plane and classifies every
closed contour for display. It builds a two-sided planar offset band around the
user-selected contours and clips that band to the actual wall material.

For an open-sketch split, the engine extrudes the sketch along its support-plane
normal beyond the model bounds. OpenCASCADE partitions the body with the
resulting ruled surface. Connected boundary edges are unfolded into
sketch-path-distance coordinates for outer/internal contour classification.
The engine selects one signed global X, Y, or Z assembly axis that crosses
every ruled panel and uses it for the complete lip, groove, shoulder relief,
snap channel, and preview arrows. The sketch controls where the case is cut,
but does not tilt the direction in which the finished halves slide together.
The best-aligned usable global principal axis is chosen automatically.
Coplanar section fragments created by OpenCASCADE are buffered as one region
so their topological boundaries cannot introduce small breaks in the joint.

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
- A sketch split must be monotonic across at least one global X, Y, or Z axis
  so the completed case can slide together along one principal direction.
- Very thin walls, tight concave radii, or a lip wider than the available wall
  can make offsets fail. Reduce width/clearance or move the plane.
- Existing holes, openings, and missing wall material are deliberately not
  bridged. If such a feature interrupts a selected joint, choose another
  contour or reduce the lip width, clearance, snap size, or draft.
- Results are static features. Change parameters by deleting the result group
  and running the command again.
- Snap retention creates one continuous 45-degree wedge/channel seam on each
  enabled contour. Segmented snaps or multiple independently positioned ribs
  are not yet supported.

## Troubleshooting

| Symptom or message | What to try |
|---|---|
| No closed contours are found | Move the plane so it crosses closed wall sections; an open U-shaped cavity boundary is not currently a joint contour |
| The sketch surface does not cross the solid | Extend both sketch endpoints beyond the model as viewed normal to the sketch support plane |
| The sketch produces more or fewer than two solids | Simplify or move the path so it divides the source exactly once |
| No principal-axis assembly direction is available | Make the sketch path monotonic along global X, Y, or Z, or reorient the model/sketch |
| A lip footprint or offset cannot be built | Reduce lip width/clearance/draft, avoid a tight concavity, or move the split slightly |
| A snap transition is too large or changes topology | Use the fit rules above; reduce snap size/clearance or move its fraction toward `0.5` |
| A joint is interrupted near an existing feature | Remember that source holes and missing material are preserved; try a smaller joint or another contour |

## Release history

### v0.4.3

- Sketch paths once again control only the split location. Lips, grooves,
  shoulder reliefs, and snap channels use one global principal assembly axis,
  including on straight diagonal sketches.
- The fitted/slanted mating direction introduced in v0.4.2 was removed because
  it could clip and break otherwise simple outer lips on complex sketch paths.
- Regression coverage verifies the complete selected outer contour from lip
  root to tip and collision-free straight separation of the supplied Splitbox
  halves along their selected assembly axis.

### v0.4.2

- Snap seams use printable 45-degree wedge faces with clearance-matched channels.
- Depth clearance now applies equally at the lip tip and flat mating shoulders.
- Coplanar sketch-section fragments are merged before offsetting to prevent
  topology-only breaks. The fitted-direction experiment in this version was
  superseded by the global principal-axis behavior in v0.4.3.
- `examples/Splitbox_test.FCStd` is covered by the geometry regression suite.

### v0.4.1

- Sketch seams use one global extrusion axis instead of changing direction at
  every polyline panel.
- The former recess beneath the tongue root was removed. From v0.4.2 onward,
  depth clearance instead relieves the non-lip shoulder while the lip root
  remains attached at the original split.
- Point dimples were replaced by continuous perimeter snap ribs and channels.

## Tests and sample

The repository includes two ready-to-open examples:

- `examples/Split2EnclosureSample.FCStd` is the simple plane-split
  demonstration generated by `create_sample.py`.
- `examples/Splitbox_test.FCStd` is the complex sketch-split regression model.
  Open it, select `Body`, Ctrl-select `Sketch005`, run the command, and choose
  **Selected open sketch**. The expected global assembly direction is `+Y`.

Generate or refresh the simple visual example with:

```powershell
& 'C:\Program Files\FreeCAD 1.1\bin\FreeCADCmd.exe' 'examples\create_sample.py'
```

Run the headless regression suite with the matching FreeCAD executable:

```powershell
& 'C:\Program Files\FreeCAD 1.1\bin\FreeCADCmd.exe' 'tests\run_geometry_tests.py'
```

Run the headless GUI-module/dialog smoke test with:

```powershell
& 'C:\Program Files\FreeCAD 1.1\bin\FreeCADCmd.exe' 'tests\import_gui_module.py'
```

The two interactive GUI checks launch FreeCAD and close it automatically:

```powershell
& 'C:\Program Files\FreeCAD 1.1\bin\FreeCAD.exe' 'tests\check_workbench_gui.py'
& 'C:\Program Files\FreeCAD 1.1\bin\FreeCAD.exe' 'tests\check_contour_preview_gui.py'
```

## License

Split2Enclosure is released under the [MIT License](LICENSE).

## Disclaimer

This work is made with OpenAI Codex, and I am a hobbyist who needed this
functionality. I am not a professional software developer. Use at your own
risk.
