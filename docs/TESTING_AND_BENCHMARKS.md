# Testing against real files, and benchmarks

Fork-only notes (branch `test-all-prs`), not part of any upstream PR. Last run: 2026-10-27, Blender 5.2.3 LTS on
Ubuntu (dual-Xeon workstation, 104 threads, 750 GB RAM). The add-on's import uses up to 16 worker processes for
large files; laptop-class timings (4–8 cores) are noted where measured.

## The test corpus

65 real 3MF files from Multi Jet Fusion production use: 50 builds ("buckets") and 15 single parts. They are
private and never committed; this page names them only by corpus ID (`bucket-NN`, `part-NN`). Contents:

| | Count | Notes |
|---|---|---|
| Producer: Netfabb | 39 | `History/history.xml`, a component wrapper per placed part, texture files renamed to 32-hex IDs |
| Producer: Materialise Magics | 22 | One colorgroup and texture groups per object, layered textures (`multiproperties`), repeated build items |
| Producer: unknown | 2 | Color calibration parts |
| Inferred printer: 580 (color) | 37 | Fits 332 × 200 × 268 mm, carries color |
| Inferred printer: 4200/5200/5600 class | 16 | Larger than the 580 envelope, mostly TPU / PA builds without color |
| Layered textures | 11 | `colorgroup + texture`, `texture + texture`, one 3-layer stack; label textures with transparency |
| Size | 0.2 MB – 885 MB | Up to 26 M triangles in one build, up to 642 build items |
| Damaged | 2 | Model > 4 GB written without ZIP64: the model is missing from the ZIP central directory |

Plus the 3MF Consortium's public samples (`3mf-samples`: core, materials, production, slices, beam lattice,
and the validation MUSTPASS / MUSTFAIL sets) and the add-on's own test files.

## What is checked for every file

1. Import with the add-on (fork and upstream `master`), recording per object: vertex and triangle counts,
   world bounding box, coordinate and index checksums, material slots and their triangle counts, UV sums.
2. The fast parser against the element-by-element parser: identical geometry, UVs and materials.
3. Export (Standard, placement kept), then validate the export with **lib3mf 2.5** (the 3MF Consortium's
   reference library), normal and strict mode.
4. Re-import the export and compare with step 1: objects, triangle counts, bounding boxes (0.1 mm), UV sums
   and per-material triangle counts.

Result (fork, all 63 readable files): **identical after round trip**; every export reads in lib3mf without
errors; 27 pass strict mode, the rest only hit lib3mf's missing support for PBR display properties
(`pbspeculardisplayproperties`, valid per the Materials spec). Upstream `master` can't export the 12 files with
layered textures in under an hour, and its headless exports point at a thumbnail it never wrote, which lib3mf
and Netfabb reject.

Bugs this found (fixed on branches, see `git log` of `test-all-prs`): `<multi>` read from the wrong namespace;
layered textures flattened; quadratic UV lookup in passthrough export; textures and texture groups written twice;
model metadata names in object metadata groups; internal `3mf:` metadata written; damaged ZIPs unreadable; one
mesh copy per build item; Smooth by Angle silently skipped in Blender 4.2; test-runner flags for 4.2.

## Blind spots (to test later)

- **Netfabb and Magics** (Windows, Magics needs a license): open the fork's exports, especially layered
  textures, readable texture names, PBR display properties, components vs repeated build items, and the
  recovered (formerly damaged) builds. Then print a layered export, including label textures that are
  replaced at job submission.
- **Other producers not in the corpus:** HP SmartStream 3D Build Manager, Autodesk Fusion / Inventor,
  Microsoft 3D Builder, Materialise 3-matic, Siemens NX, SolidWorks, Rhino, 3D Systems 3D Sprint, Stratasys
  GrabCAD Print, Cura, Bambu Studio, nTopology.
- **Extensions with little or no real-file coverage:** beam lattice, slices, secure content, volumetric,
  displacement, Materialise boolean components (namespace seen in 2 files), production external models outside
  slicer projects, triangle sets from MJF tools.
- **Edge cases:** proper ZIP64 files over 4 GB, units other than mm, mirrored (negative-determinant)
  transforms, Dual Tone (5600) halftone textures at production resolution.
- **Laptop timings** on real hardware (ZBook Firefly class) instead of CPU-pinned runs.

## Benchmarks (partial: run still in progress at 03:42, remaining files to be added)

Blender rows: time inside Blender for import (+ export, Standard). Other tools: wall clock per process. PrusaSlicer memory isn't measurable through Flatpak. Assimp's 3MF writer drops geometry under components (empty files). pymeshlab loads only the first object of multi-object files. Upstream master needs over 15 minutes to export layered-texture builds (timeouts).

### Load (read into memory): seconds / peak memory

| File | Add-on (upstream master) | Add-on (this fork) | lib3mf 2.5 (Python) | trimesh 4.x | pymeshlab | Assimp 5.x CLI | OrcaSlicer 2.4.2 CLI | PrusaSlicer 2.9.6 CLI |
|---|---|---|---|---|---|---|---|---|
| **part-07**: Netfabb, 0.41M unique / 0.41M placed triangles, 7.0 MB, components | 2.9 / 871 MB | 1.1 / 429 MB | 0.6 / 100 MB | 1.9 / 712 MB | 1.0 / 192 MB | 0.8 / 251 MB | 1.5 / 195 MB | 1.0 |
| **bucket-04**: Netfabb, 1.21M unique / 1.21M placed triangles, 17.2 MB, colors, components | 11.4 / 1.8 GB | 1.7 / 554 MB | 1.8 / 108 MB | 5.4 / 1.9 GB | 3.4 / 259 MB | 2.4 / 680 MB | 11.0 / 379 MB | 2.5 |
| **bucket-01**: Netfabb, 2.59M unique / 3.89M placed triangles, 118.3 MB, textures, components, repeated items | 47.6 / 5.4 GB | 5.6 / 1.1 GB | 6.1 / 295 MB | 16.0 / 6.6 GB | 12.7 / 922 MB | 8.1 / 2.3 GB | 14.0 / 764 MB | 7.9 |
| **bucket-35**: Magics, 0.06M unique / 0.06M placed triangles, 0.6 MB, colors | 0.6 / 386 MB | 0.2 / 349 MB | 0.1 / 25 MB | 0.2 / 149 MB | 0.2 / 89 MB | 0.1 / 41 MB | 0.8 / 108 MB | 0.3 |
| **bucket-28**: Magics, 0.47M unique / 24.77M placed triangles, 12.9 MB, layered textures, repeated items | 96.2 / 2.9 GB | 2.7 / 717 MB | 0.8 / 78 MB | 2.2 / 858 MB | 14.9 / 2.8 GB | 1.1 / 331 MB | 471.1 / 323 MB | 10.2 |
| **bucket-34**: Magics, 2.72M unique / 16.19M placed triangles, 65.3 MB, layered textures, repeated items | 64.0 / 5.7 GB | 11.4 / 2.3 GB | 7.7 / 662 MB | 17.5 / 7.6 GB | 23.5 / 2.7 GB | 13.6 / 3.0 GB | 21.1 / 744 MB | 15.8 |
| **bucket-40**: Netfabb, 8.43M unique / 9.49M placed triangles, 144.0 MB, components, repeated items | 71.3 / 10.3 GB | 8.1 / 2.1 GB | 13.4 / 341 MB | 38.2 / 12.7 GB | 29.1 / 1.2 GB | 22.1 / 4.6 GB | 36.2 / 2.0 GB | 15.8 |
| **bucket-33**: Magics, 9.95M unique / 19.90M placed triangles, 320.3 MB, layered textures, repeated items | 206.5 / 19.8 GB | 24.0 / 4.4 GB | – | – | – | – | – | – |

### Load + write 3MF: seconds / peak memory

| File | Add-on (upstream master) | Add-on (this fork) | lib3mf 2.5 (Python) | trimesh 4.x | pymeshlab | Assimp 5.x CLI | OrcaSlicer 2.4.2 CLI | PrusaSlicer 2.9.6 CLI |
|---|---|---|---|---|---|---|---|---|
| **part-07**: Netfabb, 0.41M unique / 0.41M placed triangles, 7.0 MB, components | 5.2 / 865 MB | 2.1 / 500 MB | 1.0 / 100 MB | 3.1 / 713 MB | – | 0.5 (empty) / 251 MB | 3.4 / 195 MB | 2.6 |
| **bucket-04**: Netfabb, 1.21M unique / 1.21M placed triangles, 17.2 MB, colors, components | 16.9 / 1.8 GB | 5.0 / 711 MB | 3.2 / 108 MB | 9.0 / 1.9 GB | – | 1.5 (empty) / 681 MB | 12.2 / 427 MB | 6.8 |
| **bucket-01**: Netfabb, 2.59M unique / 3.89M placed triangles, 118.3 MB, textures, components, repeated items | 94.4 / 5.4 GB | 25.6 / 2.1 GB | 13.5 / 294 MB | 25.1 / 6.6 GB | – | 5.2 (empty) / 2.3 GB | 17.4 / 761 MB | 23.0 |
| **bucket-35**: Magics, 0.06M unique / 0.06M placed triangles, 0.6 MB, colors | 0.9 / 391 MB | 0.4 / 359 MB | 0.1 / 26 MB | 0.4 / 149 MB | – | 0.3 / 41 MB | 0.9 / 118 MB | 0.5 |
| **bucket-28**: Magics, 0.47M unique / 24.77M placed triangles, 12.9 MB, layered textures, repeated items | timeout | 5.8 / 996 MB | 1.8 / 78 MB | 3.5 / 859 MB | – | 104.1 / 4.0 GB | 454.3 / 407 MB | 97.9 |
| **bucket-34**: Magics, 2.72M unique / 16.19M placed triangles, 65.3 MB, layered textures, repeated items | 149.9 / 5.8 GB | 62.7 / 4.0 GB | 18.7 / 661 MB | 25.9 / 7.6 GB | – | 68.6 / 4.3 GB | 25.9 / 740 MB | 81.5 |
| **bucket-40**: Netfabb, 8.43M unique / 9.49M placed triangles, 144.0 MB, components, repeated items | 99.6 / 10.3 GB | 27.4 / 2.1 GB | 22.9 / 340 MB | 70.4 / 12.7 GB | – | 12.0 (empty) / 4.6 GB | 39.0 / 2.0 GB | 56.0 |
| **bucket-33**: Magics, 9.95M unique / 19.90M placed triangles, 320.3 MB, layered textures, repeated items | – | 129.8 / 9.3 GB | – | – | – | – | – | – |

