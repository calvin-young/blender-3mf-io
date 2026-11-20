# Units: what a Blender unit becomes in a 3MF file

3MF files written by this add-on are always in millimetres. What one Blender unit (BU) means depends on the
scene's unit settings (Scene Properties, Units). Measured 2026-11-20 with a 20 BU cube exported, and a 20 mm cube
imported, in each setup:

| Scene units (System / Length / Unit Scale) | Upstream 2.8 and fork up to 2.7.7-fork.15 | Fork 2.7.7-fork.16 and later |
|---|---|---|
| **None / - / 1.0** (Calvin's convention) | 1 BU = 1 m: exports ×1000, imports a 20 mm part as 0.02 BU (object scale 0.001) | **1 BU = 1 mm** both ways, no scale on objects or in the file |
| Metric / Millimeters / 1.0 | 1 BU = 1 m (the length unit only changes what's displayed): ×1000, 0.02 BU | same as before |
| Metric / Millimeters / 0.001 | 1 BU = 1 mm both ways | same as before |
| Metric / Meters / 1.0 (Blender's default scene) | 1 BU = 1 m: ×1000, 0.02 BU | same as before |

- With Unit System None, Blender has no unit, and 3D printing programs read its numbers as millimetres, so the
  fork does too. The preference **Unitless Scenes in Millimeters** (on by default; Export and Import tabs) turns
  this off for 1 BU = 1 m, which is upstream's behaviour.
- Scale goes into the vertices on export (since 2.7.7-fork.14, and upstream's #39): build-item transforms are only
  rotation and translation. On import the unit factor goes on each object's scale, so importing a millimetre file
  into a metre scene gives objects scaled 0.001. Exporting those objects again keeps the right size, but the file
  then has a 0.001 scale in every build item and vertices ×1000. That's where such files come from; with the fork's
  unitless default the import factor is 1 and this no longer happens.
- The export dialog shows the exported size and turns red when it's implausible (under 0.1 mm or over 10 m).
