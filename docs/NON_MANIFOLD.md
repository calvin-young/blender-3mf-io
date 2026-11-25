# Non-manifold checks: what each program reports

Status: first pass, 2026-11-25. Eleven synthetic defect parts (10 mm cubes), made in Blender with bmesh and
exported with Blender's STL and OBJ exporters and this add-on's 3MF exporter. "Blender" uses the checks the 3D
Print Toolbox makes (non-manifold edges and vertices, flipped edges, zero-area faces, intersecting faces).
Netfabb and MiniMagics are queued (they need the Windows VM).

| Defect | Blender | Blender, after an STL round trip | trimesh 5.2 (3MF) | OrcaSlicer 2.4.2, 3MF | OrcaSlicer, STL | OrcaSlicer, OBJ |
|---|---|---|---|---|---|---|
| clean | clean | clean | watertight | manifold | manifold | manifold |
| hole | 4 edges | 4 edges | not watertight | not manifold (4 open edges) | not manifold (4 open edges) | not manifold (4 open edges) |
| two shells touching along an edge, separate vertices | **clean** | **1 edge, 2 vertices** | not watertight | manifold | manifold | manifold |
| two shells touching along an edge, shared vertices | 1 edge, 2 vertices | 1 edge, 2 vertices | not watertight | manifold | manifold | manifold |
| two shells touching at a corner, separate vertices | **clean** | **1 vertex** | watertight | manifold | manifold | manifold |
| overlapping shells | 6 intersecting faces | 10 intersecting faces | watertight | manifold | manifold | manifold |
| one flipped face | 4 flipped | 4 flipped | winding inconsistent | not manifold (8 open edges) | manifold (repaired on load) | not manifold (8 open edges) |
| unwelded seam (coincident vertices not merged) | 8 edges | **clean** | watertight | not manifold (8 open edges) | manifold | not manifold (8 open edges) |
| internal face | 4 edges, intersecting | 4 edges | not watertight | not manifold (4 open edges) | not manifold (4 open edges) | not manifold (4 open edges) |
| T-junction | 3 edges | 3 edges | not watertight | not manifold (3 open edges) | manifold (repaired on load) | refused (face with more than 4 sides) |
| zero-area sliver | 1 zero-area face | 1 zero-area face | watertight | manifold | manifold | refused (face with more than 4 sides) |

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
