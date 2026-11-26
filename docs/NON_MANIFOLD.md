# Non-manifold checks: what each program reports

Status: first pass, 2026-11-25. Eleven synthetic defect parts (10 mm cubes), made in Blender with bmesh and
exported with Blender's STL and OBJ exporters and this add-on's 3MF exporter. "Blender" uses the checks the 3D
Print Toolbox makes (non-manifold edges and vertices, flipped edges, zero-area faces, intersecting faces).
PrusaSlicer 2.9.4 (`--info`) gave the same result as OrcaSlicer in every case and every format, so it has no
column of its own; both repair STL with admesh's algorithms, and admesh itself is the last column. Netfabb and
MiniMagics are queued (they need the Windows VM).

| Defect | Blender | Blender, after an STL round trip | trimesh 5.2 (3MF) | OrcaSlicer 2.4.2, 3MF | OrcaSlicer, STL | OrcaSlicer, OBJ | admesh 0.98.5, STL |
|---|---|---|---|---|---|---|---|
| clean | clean | clean | watertight | manifold | manifold | manifold | clean |
| hole | 4 edges | 4 edges | not watertight | not manifold (4 open edges) | not manifold (4 open edges) | not manifold (4 open edges) | 4 disconnected facets |
| two shells touching along an edge, separate vertices | **clean** | **1 edge, 2 vertices** | not watertight | manifold | manifold | manifold | clean, 2 parts |
| two shells touching along an edge, shared vertices | 1 edge, 2 vertices | 1 edge, 2 vertices | not watertight | manifold | manifold | manifold | clean, 2 parts |
| two shells touching at a corner, separate vertices | **clean** | **1 vertex** | watertight | manifold | manifold | manifold | clean, 2 parts |
| overlapping shells | 6 intersecting faces | 10 intersecting faces | watertight | manifold | manifold | manifold | clean, 2 parts |
| one flipped face | 4 flipped | 4 flipped | winding inconsistent | not manifold (8 open edges) | manifold (repaired on load) | not manifold (8 open edges) | clean (repaired) |
| unwelded seam (coincident vertices not merged) | 8 edges | **clean** | watertight | not manifold (8 open edges) | manifold | not manifold (8 open edges) | clean |
| internal face | 4 edges, intersecting | 4 edges | not watertight | not manifold (4 open edges) | not manifold (4 open edges) | not manifold (4 open edges) | 2 disconnected facets |
| T-junction | 3 edges | 3 edges | not watertight | not manifold (3 open edges) | manifold (repaired on load) | refused (face with more than 4 sides) | 3 disconnected facets |
| zero-area sliver | 1 zero-area face | 1 zero-area face | watertight | manifold | manifold | refused (face with more than 4 sides) | clean |

## For Blender users

- **Blender's 0 non-manifold isn't the whole story.** It doesn't count shells that touch along an edge or at a
  corner without sharing vertices, and it doesn't count overlapping shells. Run the Toolbox's Intersections check
  too, and Merge by Distance before checking: merging makes touching shells show up as the non-manifold edges and
  vertices that other programs will see.
- **Check after a round trip when you export STL.** Export, import the STL into an empty scene, and check again:
  that's what every STL reader sees. STL welds coincident vertices, so it can add problems (touching shells) and
  hide others (unwelded seams).
- **3MF and OBJ keep Blender's vertices as they are**, so a program reading them sees the same connectivity as
  Blender. Programs that weld coincident vertices themselves (trimesh does, in every format) still see touching
  shells as non-manifold, so Merge by Distance first is the safe habit.
- **Slicers repair STL silently.** OrcaSlicer and PrusaSlicer fix flipped faces and some slivers on load and then
  report "manifold". A part that's clean in a slicer can still be flagged by a stricter build preparation tool.

## What it shows so far

- **STL has no connectivity.** Readers weld coincident vertices, so shells that only touch (common after
  joining or packing) are clean in Blender and non-manifold in anything that reads STL. Unwelded seams go the
  other way: Blender flags them, and STL hides them. 3MF and OBJ keep Blender's vertex indices, so they show what
  Blender shows.
- **"0 non-manifold" in Blender doesn't cover touching shells or overlapping shells.** Blender's non-manifold
  count ignores both; the Toolbox's separate Intersections check finds overlaps, but nothing flags touching
  shells with separate vertices. That's the likely gap between Blender's 0 and other programs' reports.
- **OrcaSlicer repairs STL on load** (flips faces, removes slivers), so it's lenient for STL and strict for 3MF.
  It refuses OBJ files with faces of more than 4 sides, which Blender's OBJ exporter writes.
- **trimesh welds every format by position**, so it behaves like an STL reader on all three.
