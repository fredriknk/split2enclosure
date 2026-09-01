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
- lets you interactively include or exclude individual contours;
- generates a lip on either half;
- cuts a matching groove with configurable side and depth clearance;
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
6. Click contours in the 3D view or use the checkboxes to choose where the
   joint should be generated.
7. Set lip dimensions and clearances.
8. Press **Create**.

The original solid is left untouched. Split2Enclosure creates two new
`Part::Feature` solids inside an App Part.

## Joint parameters

| Parameter | Description |
|---|---|
| **Lip belongs to** | Chooses which resulting half receives the lip |
| **Lip width** | Width of the tongue measured into the wall |
| **Lip height** | Height of the tongue across the split |
| **Side clearance** | Additional lateral clearance in the mating groove |
| **Depth clearance** | Additional clearance beyond the end of the lip |

Green preview contours are **included** in the joint.  
Red preview contours are **excluded**.

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
 
## TODO
- [ ] Fix lip clearance on both top and bottom half ceiling and roof, now it only adds clearance to the receiving half.
- [ ] Add optional draft angle to lip and groove.
- [ ] Add better support for sketch splits, it works now but its very flakym, often failing with error "The receiving half contains no material for the sketch-seam lip", or only producing a single lip on one side. 
- [ ] Add support for up and down lips! So each profile gives me the option to be negative or positive, it could have two boxes, one for up and one for down, and the user can select which one to use. But we must implement it in a way we can only select one. maby a radio button? pos/neg/none
- [ ] It should be possible to shift select multiple profiles and set them all to up or down, instead of having to select each one individually.
- [ ] Add visualization for profiles which way they will extrude lips
- [ ] Add option to add parametric retention features to the lip and groove so that the two halves will snap together. This could be a checkbox on each profile.
- [ ] Add a config file in the file location if possible, so that the user can set default values for lip width, height, clearances, etc. This way the user doesn't have to set them every time they use the workbench.

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
This work is made with openai codex, and i am a hobbyist who needed this functionality. I am not a professional software developer. Use at your own risk. 