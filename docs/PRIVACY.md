# What a 3MF file can reveal

A 3MF file carries more than geometry. This page lists what this add-on can put in an exported file that might be
sensitive, and what **Strip Names & Metadata** (export dialog, Archive panel) removes today. It's kept up to date as
the exporter changes. Last checked: 2.7.7-fork.15.

## The list

| What | Where in the file | Example | Removed by Strip? |
|---|---|---|---|
| Object and part names | `name` on objects and build items | a customer's or patient's name used as the part name | yes |
| Part numbers | `partnumber` on objects and items | an order number | yes |
| Model metadata | `<metadata>` at the top of the model | Title (the scene name), Designer, Description, Copyright | yes |
| Custom metadata | `<metadata>` in object metadatagroups | generator parameters, which often name their input, such as a scan file whose name identifies a person | yes |
| Writing software and version | `Application` metadata (`b3mf:Application` in Orca exports), `dc:creator` in `docProps/core.xml` | `Blender 5.2.3 LTS; Blender 3MF Format Add-on 2.7.7-fork.12` | yes (dc:creator keeps the add-on's plain name) |
| Export date and time | `dcterms:created` and `modified` in `docProps/core.xml` | the moment of export | yes |
| Export date and time | the date of entries in the ZIP archive (images got the export time; other entries already had 1980-01-01) | local time of export | yes, every entry dated 1980-01-01 |
| Material names | `<base name>`, `b3mf:name` on texture and layered groups | a material named after a client | yes, replaced with `Material 1`, ... or left out |
| Texture file names (single textures) | `<texture2d path>`, the image's name in the archive | `scan-photo-front.png` | yes, replaced with generic names |
| Texture file names (layered textures, textures carried over from an imported file) | same | same | yes, since 2.7.7-fork.15 |
| Colors and textures from a scan | texture images, color groups and vertex colors, materials derived from a scan | a body scan's skin color and surface texture | no |
| Other texture images | the PNG or JPEG itself | a photo, a logo, a signature | no, they're part of the print |
| Thumbnail | `Metadata/thumbnail.png` | a picture of the part | yes |
| Triangle set names | `<t:triangleset name>` | | yes, replaced with `Set 1`, ... |
| Files carried over from an imported file | MustPreserve parts and print tickets | another program's notes or settings | yes |
| Slicer settings (Orca, PrusaSlicer exports) | `Metadata/*.config` | printer and filament profile names, which can include user names | **no**, but the export warns when the settings come from an imported file or a saved profile |
| Slicer object names | `Metadata/model_settings.config`, `Slic3r_PE_model.config` | | yes |
| Geometry | the mesh | a part made from a body scan; a part can identify its customer | no |
| Placement | build item transforms | how a build was arranged | no |

Things the add-on never writes: file paths of the .blend or of source files (unless they're in a name or metadata
value you set), the user name of the computer, and Netfabb's `History/history.xml`, which isn't kept on import.

## What Strip doesn't cover yet

- **Slicer settings.** Preset names are how Orca Slicer, Bambu Studio and PrusaSlicer match their settings, so
  they're kept, and the export warns when they come from an imported project or a saved profile (the built-in
  template's names say nothing about the user). Possible fix, to test in the slicers first: when stripping, replace
  each preset name with the system preset it inherits from.
- **Colors and textures.** Strip keeps them, since they're usually the point of a color print. For parts made from
  scans they deserve separate treatment: in at least one healthcare setting, a scan's shape alone was judged not to
  identify a person while its color and texture were. So colors and textures need their own option, apart from names
  and metadata (below).
- **Geometry and placement** are the print itself, and are never changed.

## Proposed: finer options

Strip is all or nothing today. Real cases need parts of it: a print service needs part names but not the input
file names in generator parameters; an archive copy wants everything; a file shared publicly wants nothing. Proposed,
not built:

| Option | Default | Covers |
|---|---|---|
| **Names** (Keep / Generic) | Keep | object, part, material, texture and triangle-set names, part numbers |
| **Descriptive metadata** (Keep / Remove) | Keep | Title, Designer, Description, Copyright, custom metadata |
| **Custom metadata** per namespace (checkboxes, listed from the scene) | all kept | e.g. keep `bb:` (build settings), drop `gen:` (generator parameters) |
| **Software and dates** (Keep / Remove) | Keep | Application, dc:creator version, creation dates, ZIP entry dates |
| **Colors and textures** (Keep / Remove) | Keep | texture images, color groups, vertex colors and materials; the part exports in one plain color. For scans, where color can identify a person when shape doesn't |
| **Thumbnail** | already its own option | |
| **Carried-over data** (Keep / Remove) | Keep | MustPreserve parts and passthrough data from imported files |

Strip Names & Metadata would stay as the one-click "all of the above", so nobody needs to understand the list to be
safe. Traceability argues for keeping the software stamp in most cases: it says nothing about the part or its owner.
