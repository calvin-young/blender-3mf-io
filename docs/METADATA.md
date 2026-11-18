# Metadata in 3MF files: design

Status: design, 2026-11-18. The parts marked **built** are in the fork (2.7.7-fork.12 and later); the rest is proposed.

## The use case

A part generator (a script or add-on in Blender) makes one part from parameters: the input it was based on, wall
thickness, a template name, and so on. Today the parameters sit in a JSON file next to the part's 3MF. They should
travel inside it:

1. **Part file:** one part per 3MF, carrying its generation parameters, readable again in Blender.
2. **Build file (bucket):** several part files arranged into one build, for the printer and for the archive. It
   should carry each part's parameters (no lookup needed later), plus metadata about the build itself: the printer it
   was arranged for, the packing settings, the tool and version that packed it, and optionally the operator.
3. **Hybrid workflows:** parts made in Blender may be packed in another program (Magics, Netfabb), and builds made
   elsewhere may be opened in Blender. Losing metadata in another program is acceptable; losing it silently in
   Blender is not.
4. **Traceability:** every file says which software wrote it (**built**: the `Application` stamp). Orca Slicer and
   Bambu Studio exports must keep `Application` = `BambuStudio-...` to open as projects, so they carry the same
   stamp as `b3mf:Application`. Strip Names & Metadata removes both.

## What the 3MF core specification offers

| Mechanism | Where | Good for | Weak points |
|---|---|---|---|
| `<metadata name="ns:key">` on the model | top of the model document | facts about the whole file (a build's printer, packing settings) | one per file: merging part files loses all but one file's entries |
| `<metadatagroup>` on an `<object>` | inside the part's resource | facts about one part design | readers may drop it (to be tested, below) |
| `<metadatagroup>` on a build `<item>` | the placed copy | facts about one copy (serial number, label text) | packers create new items, so it's the first thing lost; this add-on reads it but writes per-copy data on each copy's object instead |
| A file in the package with a MustPreserve relationship | `/Metadata/x.json` | large data | belongs to the package, not to a part: merged builds can't tell whose it is, and two part files collide on the same path |

Names other than the core's well-known ones (Title, Designer, Description, Copyright, LicenseTerms, Rating,
CreationDate, ModificationDate, Application) must have a namespace prefix declared on the model root. Values are
strings, so JSON goes in as a string. `preserve="1"` asks programs that edit the file to keep the entry.

## Recommendation

**Part parameters go in the part object's `<metadatagroup>`, as one JSON entry in the generator's own namespace.**
For example, a generator that declares `xmlns:gen="urn:example:part-generator"` writes:

```xml
<object id="1" name="bracket_1" type="model">
  <metadatagroup>
    <metadata name="gen:parameters" preserve="1" type="xs:string">{"template": "bracket-a", "wall_thickness_mm": 2.5}</metadata>
  </metadatagroup>
  <mesh>...</mesh>
</object>
```

Why the object and not the model or an attachment: the object element *is* the part. When part files are combined
into a build, by Blender or by anything that keeps object metadata, each part's parameters stay with that part and
nothing collides. A part file and a build file then carry part parameters the same way, so a reader needs only one
rule.

Why one JSON entry and not one entry per parameter: the parameters belong to the generator and change with it.
One entry keeps them together, versioned by the generator (put a `"generator": "name version"` key inside), and
keeps the 3MF namespace stable while the parameter set evolves.

Why the generator's namespace: whoever defines the keys owns the namespace. This add-on carries any namespace
through import and export (**built**), so it doesn't need to know each generator's keys.

Attachments only for data too large for an XML attribute value (more than a few hundred kB, or binary): a scan, a
simulation result. If used, name them per part (`/Metadata/<object UUID>.json`) and point to them from the part's
metadatagroup, so they can be matched up again.

**Build metadata goes in model-level `<metadata>` entries in the packing tool's namespace.** For Bucket Builder:

| Name | Value |
|---|---|
| `bb:printer` | printer model and build volume, e.g. `{"model": "HP Jet Fusion 5600", "volume_mm": [380, 284, 380]}` |
| `bb:packing` | the packer settings that produced the layout, as JSON (method, clearance, rotations, seed) |
| `bb:tool` | `Bucket Builder 1.x.y` (the packing tool; `Application` names the 3MF writer) |
| `bb:checks` | result of the last collision and bounds check, with its clearance |
| `bb:operator` | only if the user opts in, off by default (personal data) |

with `xmlns:bb="urn:bucket-builder"` (proposed; Bucket Builder's to decide). Bucket Builder sets them before
calling the exporter, through `io_mesh_3mf.api.metadata.set_metadata(scene, "bb:printer", {...},
namespace="urn:bucket-builder")`. Noted for Bucket Builder's export wiring (its issue #9).

**Per-copy data (serial numbers, label text)** goes on each copy's Blender object, like any other metadata: the
exporter gives every copy its own object in the file. Bucket Builder's labeled copies are the case for it.

## How metadata travels through Blender (built, 2.7.7-fork.12)

- **Import:** model metadata becomes custom properties on the scene; object and build-item metadata become custom
  properties on the Blender object, each stored as `{"value", "datatype", "preserve"}` and shown in the 3MF sidebar
  panel. The namespace declarations on the model root are recorded in the scene (`3mf_metadata_namespaces`).
- **Export:** scene properties are written as model metadata, object properties as the object's metadata. Each
  prefix is declared with the URI recorded at import or given to `set_metadata()`, or a stand-in URI
  (`urn:3mf-metadata:<prefix>`) if neither exists. Until 2.7.7-fork.12 every prefix got the stand-in.
- **Scripts:** `io_mesh_3mf.api.metadata.set_metadata(target, "ns:key", value, namespace=URI)` and
  `get_metadata(target, "ns:key", parse_json=True)`. A non-string value is stored as JSON.
- **Packing in Blender:** moving and duplicating objects keeps their custom properties, so part parameters survive
  import, pack and export. Joining meshes keeps only the active object's properties.

- **Where it's written (2.7.7-fork.15 and later):** each Blender object's custom metadata goes in its own
  `<object>`'s metadatagroup, once. Linked duplicates (one mesh, several objects) are written as one shared mesh
  plus a small container object per copy, and each container carries that copy's metadata, part number and type.
  Build items carry no metadata groups. Before, plain objects had their metadata on both the object and the build
  item, and linked duplicates only on the build items, the level packers drop first.
- **Per-copy data from other programs:** a file with metadata on its build items (the 3MF Zoo file
  `core-23-meta-object-group`) imports with each copy's item metadata on that copy's Blender object, and exports
  it on the copy's object. Nothing is lost; it moves from the item to the object.

## Hybrid workflows

- **Built in Blender, packed elsewhere:** works if the packer keeps object metadata. Which programs do is the
  reader test below. If a program drops it, the fallback is a lookup: keep each part's parameters in its part file
  (always), and match parts in the returned build to their part files by mesh fingerprint (triangle count, volume,
  bounding box). That matching belongs in Bucket Builder's import, not in the exporter.
- **Built elsewhere, opened in Blender:** whatever the program wrote comes in as custom properties, with its
  namespace, and goes back out unchanged.

## Privacy

Generator parameters often name input files (a scan file name can identify a person), and an operator field is
personal data. Strip Names & Metadata removes every custom entry today. The finer options are in
[PRIVACY.md](PRIVACY.md).

## Open questions

- Namespace for this add-on's own entries (if it gets any, e.g. the export settings): the `b3mf` prefix exists
  for material names with the URI `urn:blender-3mf:material`, which is wrong for metadata. Proposed: a general
  `urn:blender-3mf` URI, reading the old one too. To raise in the upstream discussion.
- Should the export settings be recorded in the file (one `b3mf:export` JSON entry)? Useful for tracing a file back
  to how it was exported; costs nothing to strip.
- Which readers keep which mechanism: queued, see [TESTING_AND_BENCHMARKS.md](TESTING_AND_BENCHMARKS.md), "Which
  readers keep metadata".
- A gold-standard part file and build file in the 3MF Zoo, once the reader test is in.
