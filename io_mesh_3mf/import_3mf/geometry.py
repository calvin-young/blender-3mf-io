# Blender add-on to import and export 3MF files.
# Copyright (C) 2020 Ghostkeeper
# Copyright (C) 2025 Jack (modernization for Blender 4.2+)
# This add-on is free software; you can redistribute it and/or modify it under the terms of the GNU General Public
# License as published by the Free Software Foundation; either version 2 of the License, or (at your option) any later
# version.
# This add-on is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied
# warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.
# You should have received a copy of the GNU General Public License along with this program; if not, write to the Free
# Software Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA.

# <pep8 compliant>

"""
Geometry reading — vertices, triangles, components, and objects.

This module is the heart of the 3MF import pipeline.  It extracts mesh
data from the parsed XML and stores ``ResourceObject`` entries in the
import context.

Key improvement over the monolithic version: ``read_triangles`` and
``read_triangles_with_paint_color`` are merged into a single
:func:`read_triangles` with a ``paint_mode`` parameter, eliminating
~80% duplication.
"""

import xml.etree.ElementTree
from typing import Dict, List, Optional, Tuple, TYPE_CHECKING

import numpy as np

from ..common import (
    debug,
    warn,
    MODEL_NAMESPACES,
    SLIC3RPE_NAMESPACE,
    PRODUCTION_NAMESPACE,
)
from ..common.types import (
    TriangleMaterials,
    ResourceMaterial,
    ResourceObject,
    Component,
)
from ..common.metadata import Metadata, MetadataEntry
from ..common.xml import parse_transformation
from .fast_parse import fast_array
from .layers import LayerData, layer_kinds, resolve_layered_rows

if TYPE_CHECKING:
    from .context import ImportContext

__all__ = [
    "read_objects",
    "read_external_model_objects",
    "read_vertices",
    "read_triangles",
    "read_components",
]


# ---------------------------------------------------------------------------
# read_vertices
# ---------------------------------------------------------------------------

def read_vertices(
    ctx: "ImportContext",
    object_node: xml.etree.ElementTree.Element,
) -> List[Tuple[float, float, float]]:
    """Read vertices from an ``<object>`` element.

    If any vertex is corrupt (missing or non-float coordinate) the vertex
    is stored as ``(0, 0, 0)`` to keep the index list consistent.

    :param ctx: The import context (for ``safe_report``).
    :param object_node: An ``<object>`` element from the model file.
    :return: List of ``(x, y, z)`` tuples, or an array of shape (N, 3) if the
        vertices were converted by :mod:`.fast_parse`.
    """
    converted = fast_array(ctx, object_node.find("./3mf:mesh/3mf:vertices", MODEL_NAMESPACES))
    if converted is not None:
        return converted

    vertex_nodes = object_node.findall(
        "./3mf:mesh/3mf:vertices/3mf:vertex", MODEL_NAMESPACES
    )
    # Fast path for well-formed files.  Any missing or bad coordinate falls
    # through to the per-vertex loop below, which handles it.
    try:
        return [
            (float(a["x"]), float(a["y"]), float(a["z"]))
            for a in (vertex.attrib for vertex in vertex_nodes)
        ]
    except (KeyError, ValueError, TypeError):
        pass

    result: List[Tuple[float, float, float]] = []
    for vertex in vertex_nodes:
        attrib = vertex.attrib
        try:
            result.append((
                float(attrib.get("x", 0)),
                float(attrib.get("y", 0)),
                float(attrib.get("z", 0)),
            ))
        except (ValueError, TypeError):
            warn("Vertex has non-float coordinate — using 0.")
            ctx.safe_report({"WARNING"}, "Vertex has non-float coordinate")
            result.append((0.0, 0.0, 0.0))
    return result


# ---------------------------------------------------------------------------
# read_triangles  (unified — replaces both old read_triangles
#                  AND read_triangles_with_paint_color)
# ---------------------------------------------------------------------------

# Threshold to distinguish short Orca paint codes from PrusaSlicer segmentation strings
_PRUSA_SEGMENTATION_THRESHOLD = 10


def read_triangles(
    ctx: "ImportContext",
    object_node: xml.etree.ElementTree.Element,
    default_material: Optional[ResourceMaterial],
    material_pid: Optional[str],
    vertex_coords: Optional[List[Tuple[float, float, float]]] = None,
    object_id: Optional[str] = None,
    *,
    paint_only: bool = False,
) -> Tuple[
    List[Tuple[int, int, int]],
    List[Optional[ResourceMaterial]],
    List[Optional[Tuple]],
    List[Tuple[float, float, float]],
    Dict[int, str],
    Dict[int, str],
    Dict[int, str],
    int,
]:
    """Read triangles from an ``<object>`` element.

    This single function replaces the two old methods ``read_triangles``
    (full material/UV handling) and ``read_triangles_with_paint_color``
    (Orca external model shortcut).  Set *paint_only* to ``True`` for
    the simpler paint-code-only path used by external model objects.

    :param ctx: The import context.
    :param object_node: An ``<object>`` XML element.
    :param default_material: Fallback material when a triangle has none.
    :param material_pid: Default property group ID from the ``<object>`` element.
    :param vertex_coords: Vertex coordinate list (may be extended by subdivision).
    :param object_id: Object ID for default-extruder lookup.
    :param paint_only: If ``True``, skip texture-group / multiproperties handling
        (used for external model objects that only carry ``paint_color``).
    :return: ``(triangles, materials, triangle_uvs, vertex_list,
        segmentation_strings, seam_strings, support_strings, default_extruder)``
    """
    vertices: List[Tuple[int, int, int]] = []
    materials: List[Optional[ResourceMaterial]] = []
    triangle_uvs: List[Optional[Tuple]] = []
    segmentation_strings: Dict[int, str] = {}
    seam_strings: Dict[int, str] = {}
    support_strings: Dict[int, str] = {}

    import_materials = ctx.options.import_materials
    ctx.last_layer_data = None

    # Default extruder
    default_extruder = 1
    if object_id:
        default_extruder = ctx.object_default_extruders.get(object_id, 1)

    converted = fast_array(ctx, object_node.find("./3mf:mesh/3mf:triangles", MODEL_NAMESPACES))
    if converted is not None:
        return _read_converted_triangles(
            ctx, converted, default_material, material_pid, vertex_coords, default_extruder, paint_only,
        )

    if isinstance(vertex_coords, np.ndarray):
        # Converted vertices, but triangles that need the element-by-element path
        # (e.g. paint attributes), which may add vertices to the list.
        vertex_list = [tuple(vertex) for vertex in vertex_coords.tolist()]
    else:
        vertex_list = list(vertex_coords) if vertex_coords else []

    # Per-object state for segmentation subdivision
    state_materials: Dict[int, ResourceMaterial] = {}
    paint_color_materials: Dict[str, ResourceMaterial] = {}

    # --- Fast path for geometry-only imports (no material / UV data needed) ---
    if import_materials == "NONE":
        _tri_path = "./3mf:mesh/3mf:triangles/3mf:triangle"
        for triangle in object_node.iterfind(_tri_path, MODEL_NAMESPACES):
            attrib = triangle.attrib
            try:
                v1 = int(attrib["v1"])
                v2 = int(attrib["v2"])
                v3 = int(attrib["v3"])
            except (KeyError, ValueError) as e:
                warn(f"Vertex reference error: {e}")
                ctx.safe_report({"WARNING"}, f"Vertex reference error: {e}")
                continue
            if v1 < 0 or v2 < 0 or v3 < 0:
                warn("Triangle containing negative index to vertex list.")
                ctx.safe_report({"WARNING"}, "Triangle containing negative index to vertex list")
                continue
            vertices.append((v1, v2, v3))
        return (
            vertices, [], None, vertex_list, {}, {}, {}, default_extruder
        )

    # Triangles of layered multiproperties, resolved together after the loop:
    # (triangle index, pid, p1, p2, p3)
    pending_layered: List[Tuple[int, str, int, int, int]] = []

    for tri_index, triangle in enumerate(
        object_node.iterfind(
            "./3mf:mesh/3mf:triangles/3mf:triangle", MODEL_NAMESPACES
        )
    ):
        attrib = triangle.attrib
        try:
            v1 = int(attrib["v1"])
            v2 = int(attrib["v2"])
            v3 = int(attrib["v3"])
            if v1 < 0 or v2 < 0 or v3 < 0:
                warn("Triangle containing negative index to vertex list.")
                ctx.safe_report({"WARNING"}, "Triangle containing negative index to vertex list")
                continue

            pid = attrib.get("pid", material_pid)
            p1 = attrib.get("p1")
            p2 = attrib.get("p2")
            p3 = attrib.get("p3")
            material: Optional[ResourceMaterial] = None
            uvs = None

            if import_materials != "NONE":
                # --- Paint / segmentation codes ---
                paint_code = attrib.get("paint_color")
                if not paint_code:
                    paint_code = attrib.get(
                        f"{{{SLIC3RPE_NAMESPACE}}}mmu_segmentation"
                    )
                if not paint_code:
                    paint_code = attrib.get("slic3rpe:mmu_segmentation")

                # --- Seam / support codes (independent layers) ---
                seam_code = attrib.get("paint_seam")
                support_code = attrib.get("paint_supports")

                if paint_code:
                    handled = _handle_paint_code(
                        ctx,
                        paint_code,
                        v1, v2, v3,
                        tri_index,
                        vertex_list,
                        vertices,
                        materials,
                        triangle_uvs,
                        segmentation_strings,
                        state_materials,
                        paint_color_materials,
                        default_extruder,
                        default_material,
                    )
                    if handled:
                        # Store seam/support at the face index just created
                        if import_materials == "PAINT":
                            face_idx = len(vertices) - 1
                            if seam_code:
                                seam_strings[face_idx] = seam_code
                            if support_code:
                                support_strings[face_idx] = support_code
                        continue

                # --- Texture groups / multiproperties / standard materials ---
                if not paint_only:
                    if pid is not None and pid in ctx.resource_texture_groups:
                        material, uvs = _resolve_texture_group(ctx, pid, p1, p2, p3)
                    elif pid is not None and layer_kinds(ctx, pid):
                        material = default_material  # Replaced after the loop.
                        pending_layered.append((
                            len(vertices), pid,
                            -1 if p1 is None else int(p1),
                            -1 if p2 is None else int(p2),
                            -1 if p3 is None else int(p3),
                        ))
                    elif pid is not None and pid in ctx.resource_multiproperties:
                        material, uvs = _resolve_multiproperties_material(
                            ctx, pid, p1, p2, p3, default_material
                        )
                    elif p1 is not None:
                        material = _resolve_standard_material(
                            ctx, pid, p1, default_material
                        )
                    else:
                        material = default_material
                else:
                    material = default_material
            else:
                material = default_material

            vertices.append((v1, v2, v3))
            materials.append(material)
            triangle_uvs.append(uvs)

            # Store seam/support for triangles without paint_code
            if import_materials == "PAINT":
                face_idx = len(vertices) - 1
                seam_code_late = attrib.get("paint_seam")
                support_code_late = attrib.get("paint_supports")
                if seam_code_late:
                    seam_strings[face_idx] = seam_code_late
                if support_code_late:
                    support_strings[face_idx] = support_code_late

        except KeyError as e:
            warn(f"Vertex {e} is missing.")
            ctx.safe_report({"WARNING"}, f"Vertex {e} is missing")
            continue
        except ValueError as e:
            warn(f"Vertex reference is not an integer: {e}")
            ctx.safe_report({"WARNING"}, f"Vertex reference is not an integer: {e}")
            continue

    layer_data = ctx.last_layer_data = None
    if pending_layered:
        layer_data = ctx.last_layer_data = LayerData(len(vertices))

        def assign(rows, material):
            for row in rows.tolist():
                materials[row] = material

        for pid in sorted({entry[1] for entry in pending_layered}):
            entries = np.array([entry[2:] for entry in pending_layered if entry[1] == pid], dtype=np.int64)
            rows = np.array([entry[0] for entry in pending_layered if entry[1] == pid], dtype=np.int64)
            resolve_layered_rows(
                ctx, pid, layer_kinds(ctx, pid), entries, rows, layer_data, assign, default_material,
            )
        if 0 in layer_data.uvs:
            for row in np.flatnonzero(layer_data.uv_rows.pop(0)).tolist():
                triangle_uvs[row] = tuple(map(tuple, layer_data.uvs[0][row].tolist()))
            del layer_data.uvs[0]

    return (
        vertices,
        materials,
        triangle_uvs,
        vertex_list,
        segmentation_strings,
        seam_strings,
        support_strings,
        default_extruder,
    )


def _read_converted_triangles(
    ctx: "ImportContext",
    converted: np.ndarray,
    default_material: Optional[ResourceMaterial],
    material_pid: Optional[str],
    vertex_coords,
    default_extruder: int,
    paint_only: bool,
):
    """:func:`read_triangles` for triangles converted by :mod:`.fast_parse`.

    Gives the same result as the element-by-element path, but resolves each
    distinct property reference once instead of once per triangle.  Converted
    triangles never carry paint attributes, so there are no segmentation strings.

    :param converted: Array of shape (N, 7): v1 v2 v3 pid p1 p2 p3, -1 if absent.
    """
    triangles = converted[:, :3]
    if ctx.options.import_materials == "NONE" or len(converted) == 0:
        return triangles, [], None, vertex_coords, {}, {}, {}, default_extruder

    count = len(converted)
    materials = _MaterialNumbering(default_material, count)
    triangle_uvs = None
    layer_data = ctx.last_layer_data = LayerData(count)

    if not paint_only:
        pids = converted[:, 3]
        for pid_value in np.unique(pids):
            rows = np.flatnonzero(pids == pid_value)
            pid = material_pid if pid_value < 0 else str(int(pid_value))
            properties = converted[rows, 4:7]

            if pid is not None and pid in ctx.resource_texture_groups:
                from .materials import get_or_create_textured_material
                texture_group = ctx.resource_texture_groups[pid]
                materials.assign(rows, get_or_create_textured_material(ctx, pid, texture_group))
                tex2coords = np.asarray(texture_group.tex2coords, dtype=np.float64).reshape(-1, 2)
                # p1 defaults to 0; p2 and p3 default to p1.
                first = np.where(properties[:, 0] < 0, 0, properties[:, 0])
                indices = np.stack((
                    first,
                    np.where(properties[:, 1] < 0, first, properties[:, 1]),
                    np.where(properties[:, 2] < 0, first, properties[:, 2]),
                ), axis=1)
                in_range = indices < len(tex2coords)
                uvs = np.zeros((len(rows), 3, 2), dtype=np.float64)
                uvs[in_range] = tex2coords[indices[in_range]]
                if triangle_uvs is None:
                    triangle_uvs = np.zeros((count, 3, 2), dtype=np.float64)
                triangle_uvs[rows] = uvs

            elif pid is not None and layer_kinds(ctx, pid):
                resolve_layered_rows(
                    ctx, pid, layer_kinds(ctx, pid), properties, rows, layer_data,
                    materials.assign, default_material,
                )

            elif pid is not None and pid in ctx.resource_multiproperties:
                uvs, has_uvs = _resolve_multiproperties_rows(
                    ctx, pid, properties, default_material, materials, rows,
                )
                if has_uvs.any():
                    if triangle_uvs is None:
                        triangle_uvs = np.zeros((count, 3, 2), dtype=np.float64)
                    triangle_uvs[rows[has_uvs]] = uvs[has_uvs]

            else:
                first = properties[:, 0]
                for p1 in np.unique(first[first >= 0]):
                    material = _resolve_standard_material(ctx, pid, str(int(p1)), default_material)
                    materials.assign(rows[first == p1], material)

    triangle_uvs = layer_data.merge_base_uvs(triangle_uvs)
    return (
        triangles, materials.result(), triangle_uvs, vertex_coords, {}, {}, {}, default_extruder,
    )


class _MaterialNumbering:
    """Builds a :class:`TriangleMaterials` by assigning materials to groups of triangles."""

    def __init__(self, default_material: Optional[ResourceMaterial], count: int):
        self.distinct = [default_material]
        self.number_by_id = {id(default_material): 0}
        self.numbers = np.zeros(count, dtype=np.int64)

    def number(self, material: Optional[ResourceMaterial]) -> int:
        key = id(material)
        if key not in self.number_by_id:
            self.number_by_id[key] = len(self.distinct)
            self.distinct.append(material)
        return self.number_by_id[key]

    def assign(self, rows: np.ndarray, material: Optional[ResourceMaterial]) -> None:
        self.numbers[rows] = self.number(material)

    def result(self) -> TriangleMaterials:
        return TriangleMaterials(self.distinct, self.numbers)


def _multi_array(ctx: "ImportContext", multiprop_id: str) -> np.ndarray:
    """The ``pindices`` of each ``<multi>`` as an int array padded with -1 (cached)."""
    cache = ctx.fast_arrays.setdefault("multiproperties", {})
    if multiprop_id not in cache:
        rows = []
        for multi in ctx.resource_multiproperties[multiprop_id].multis:
            try:
                rows.append([int(index) for index in multi.get("pindices", "").split()])
            except ValueError:
                rows.append([])
        width = max((len(row) for row in rows), default=0)
        array = np.full((len(rows), width), -1, dtype=np.int64)
        for number, row in enumerate(rows):
            array[number, :len(row)] = row
        cache[multiprop_id] = array
    return cache[multiprop_id]


def _resolve_multiproperties_rows(
    ctx: "ImportContext",
    multiprop_id: str,
    properties: np.ndarray,
    default_material: Optional[ResourceMaterial],
    materials: "_MaterialNumbering",
    triangle_rows: np.ndarray,
) -> Tuple[np.ndarray, np.ndarray]:
    """Vectorized :func:`_resolve_multiproperties_material` for many triangles.

    Assigns the materials of *triangle_rows* in *materials*.

    :param properties: Array of shape (N, 3): p1 p2 p3 of each triangle, -1 if absent.
    :return: ``(uvs, has_uvs)``: UVs of shape (N, 3, 2), and which triangles have UVs.
    """
    count = len(properties)
    materials.assign(triangle_rows, default_material)
    uvs = np.zeros((count, 3, 2), dtype=np.float64)
    has_uvs = np.zeros(count, dtype=bool)

    multis = _multi_array(ctx, multiprop_id)
    pids = (ctx.resource_multiproperties[multiprop_id].pids or "").split()
    first = properties[:, 0]
    indices = np.stack((
        first,
        np.where(properties[:, 1] < 0, first, properties[:, 1]),
        np.where(properties[:, 2] < 0, first, properties[:, 2]),
    ), axis=1)
    valid = (first >= 0) & (indices >= 0).all(axis=1) & (indices < len(multis)).all(axis=1)
    if not valid.all():
        warn(f"Multi index out of range for multiproperties {multiprop_id} "
             f"on {int(count - valid.sum())} triangles")
    # Multis with fewer pindices than layers: use the per-triangle code, which handles them.
    lengths = (multis >= 0).sum(axis=1) if multis.size else np.zeros(len(multis), dtype=np.int64)
    full = np.zeros(count, dtype=bool)
    full[valid] = (lengths[indices[valid]] >= len(pids)).all(axis=1)
    for row in np.flatnonzero(valid & ~full):
        p1, p2, p3 = (None if value < 0 else str(int(value)) for value in properties[row])
        material, row_uvs = _resolve_multiproperties_material(ctx, multiprop_id, p1, p2, p3, default_material)
        materials.assign(triangle_rows[row], material)
        if row_uvs is not None:
            uvs[row] = row_uvs
            has_uvs[row] = True

    rows = np.flatnonzero(full)
    if len(rows) == 0:
        return uvs, has_uvs
    pindices = multis[indices[rows]]  # (n, 3 vertices, layers)

    material_layers = [i for i, pid in enumerate(pids) if pid in ctx.resource_materials]
    texture_layers = [i for i, pid in enumerate(pids) if pid in ctx.resource_texture_groups]

    # The material comes from the first basematerial/colorgroup layer of p1's multi.
    keys = pindices[:, 0, material_layers] if material_layers else np.zeros((len(rows), 0), dtype=np.int64)
    distinct, inverse = np.unique(keys, axis=0, return_inverse=True)
    inverse = inverse.reshape(-1)
    for which, key in enumerate(distinct.tolist()):
        material = None
        for layer, pindex in zip(material_layers, key):
            group = ctx.resource_materials[pids[layer]]
            if pindex in group:
                material = group[pindex]
                break
        if material is None:
            material = default_material
        elif texture_layers:
            texture_group_ids = [pids[layer] for layer in texture_layers]
            original_basematerial = material
            material = ResourceMaterial(
                name=original_basematerial.name,
                color=original_basematerial.color,
                metallic=original_basematerial.metallic,
                roughness=original_basematerial.roughness,
                specular_color=original_basematerial.specular_color,
                glossiness=original_basematerial.glossiness,
                ior=original_basematerial.ior,
                attenuation=original_basematerial.attenuation,
                transmission=original_basematerial.transmission,
                texture_id=texture_group_ids[0],
                extra_texture_ids=tuple(texture_group_ids[1:]) if len(texture_group_ids) > 1 else None,
            )
            ctx.textured_to_basematerial_map[material] = original_basematerial
        materials.assign(triangle_rows[rows[inverse == which]], material)

    # UVs come from the last texture layer, per vertex.
    if texture_layers:
        layer = texture_layers[-1]
        tex2coords = np.asarray(
            ctx.resource_texture_groups[pids[layer]].tex2coords, dtype=np.float64
        ).reshape(-1, 2)
        uv_indices = pindices[:, :, layer]
        in_range = uv_indices < len(tex2coords)
        layer_uvs = np.zeros((len(rows), 3, 2), dtype=np.float64)
        layer_uvs[in_range] = tex2coords[uv_indices[in_range]]
        uvs[rows] = layer_uvs
        has_uvs[rows] = True
    return uvs, has_uvs


# ---------------------------------------------------------------------------
# Private helpers for read_triangles
# ---------------------------------------------------------------------------

def _handle_paint_code(
    ctx: "ImportContext",
    paint_code: str,
    v1: int, v2: int, v3: int,
    tri_index: int,
    vertex_list: list,
    vertices: list,
    materials: list,
    triangle_uvs: list,
    segmentation_strings: dict,
    state_materials: dict,
    paint_color_materials: dict,
    default_extruder: int,
    default_material,
) -> bool:
    """Handle a paint_code / mmu_segmentation attribute on a triangle.

    Returns ``True`` if the triangle was fully consumed (caller should
    ``continue``), ``False`` if it should fall through to normal handling.
    """
    from .slicer.paint import (
        parse_paint_color_to_index,
        subdivide_prusa_segmentation,
        get_or_create_paint_material,
    )

    import_materials = ctx.options.import_materials

    if import_materials == "PAINT" and vertex_list:
        # PAINT mode: all codes become segmentation strings for UV texture
        current_face_index = len(vertices)
        segmentation_strings[current_face_index] = paint_code
        vertices.append((v1, v2, v3))
        triangle_uvs.append(None)
        materials.append(default_material)
        return True

    if import_materials == "MATERIALS" and vertex_list:
        if len(paint_code) >= _PRUSA_SEGMENTATION_THRESHOLD:
            # Long segmentation string — subdivide geometry
            try:
                sub_tris, sub_mats = subdivide_prusa_segmentation(
                    ctx, v1, v2, v3, paint_code, vertex_list,
                    state_materials, tri_index, default_extruder,
                )
                for tri in sub_tris:
                    vertices.append(tri)
                    triangle_uvs.append(None)
                materials.extend(sub_mats)
                return True
            except Exception as e:
                warn(f"Failed to subdivide long segmentation: {e}")
                return False  # Fall through to default
        else:
            # Short code — try Orca lookup, then short segmentation
            if paint_code not in paint_color_materials:
                filament_index = parse_paint_color_to_index(paint_code)
                if filament_index > 0:
                    mat = get_or_create_paint_material(ctx, filament_index, paint_code)
                    paint_color_materials[paint_code] = mat
                    debug(f"Multi-material code '{paint_code}' -> filament {filament_index}")
                else:
                    try:
                        sub_tris, sub_mats = subdivide_prusa_segmentation(
                            ctx, v1, v2, v3, paint_code, vertex_list,
                            state_materials, tri_index, default_extruder,
                        )
                        for tri in sub_tris:
                            vertices.append(tri)
                            triangle_uvs.append(None)
                        materials.extend(sub_mats)
                        return True
                    except Exception:
                        debug(
                            f"String '{paint_code}' not valid Orca code or segmentation, using default"
                        )
                        return False

            # Known paint code — use cached material
            material = paint_color_materials.get(paint_code)
            if material is not None:
                vertices.append((v1, v2, v3))
                materials.append(material)
                triangle_uvs.append(None)
                return True

    return False


def _resolve_texture_group(
    ctx: "ImportContext",
    pid: str,
    p1: Optional[str],
    p2: Optional[str],
    p3: Optional[str],
) -> Tuple[Optional[ResourceMaterial], Optional[Tuple]]:
    """Resolve a texture-group material reference and extract UVs."""
    from .materials import get_or_create_textured_material

    texture_group = ctx.resource_texture_groups[pid]
    tex2coords = texture_group.tex2coords
    uvs = None
    material = None

    try:
        idx1 = int(p1) if p1 is not None else 0
        idx2 = int(p2) if p2 is not None else idx1
        idx3 = int(p3) if p3 is not None else idx1

        uv1 = tex2coords[idx1] if idx1 < len(tex2coords) else (0.0, 0.0)
        uv2 = tex2coords[idx2] if idx2 < len(tex2coords) else (0.0, 0.0)
        uv3 = tex2coords[idx3] if idx3 < len(tex2coords) else (0.0, 0.0)
        uvs = (uv1, uv2, uv3)

        material = get_or_create_textured_material(ctx, pid, texture_group)

    except (ValueError, IndexError) as e:
        warn(f"Invalid texture coordinate index: {e}")
        uvs = None

    return material, uvs


def _resolve_standard_material(
    ctx: "ImportContext",
    pid: Optional[str],
    p1: str,
    default_material: Optional[ResourceMaterial],
) -> Optional[ResourceMaterial]:
    """Resolve a standard basematerials reference."""
    try:
        return ctx.resource_materials[pid][int(p1)]
    except KeyError as e:
        warn(f"Material {e} is missing.")
        ctx.safe_report({"WARNING"}, f"Material {e} is missing")
        return default_material
    except ValueError as e:
        warn(f"Material index is not an integer: {e}")
        ctx.safe_report({"WARNING"}, f"Material index is not an integer: {e}")
        return default_material


# ---------------------------------------------------------------------------
# _resolve_multiproperties_material
# ---------------------------------------------------------------------------

def _resolve_multiproperties_material(
    ctx: "ImportContext",
    multiprop_id: str,
    p1: Optional[str],
    p2: Optional[str],
    p3: Optional[str],
    default_material: Optional[ResourceMaterial],
) -> Tuple[Optional[ResourceMaterial], Optional[Tuple]]:
    """Resolve a multiproperties reference to its underlying material and UVs.

    Multiproperties combine multiple property groups (basematerials,
    texture groups, etc.) with optional blend modes.  For rendering we
    extract the primary basematerial and any texture UVs.

    Per the 3MF Materials Extension spec, ``p1``/``p2``/``p3`` on the
    triangle each index into the ``<multi>`` list independently — giving
    per-vertex property resolution.  Each ``<multi>`` entry's ``pindices``
    selects an index within each referenced property group (basematerials,
    texture2dgroup, etc.).  We use ``p1`` for the basematerial (which must
    be the same across all three vertices per spec) and resolve per-vertex
    UVs from the texture groups using ``p1``/``p2``/``p3`` respectively.

    :param ctx: Import context.
    :param multiprop_id: ID of the multiproperties resource.
    :param p1: Property index for vertex 1 (into multi list).
    :param p2: Property index for vertex 2.
    :param p3: Property index for vertex 3.
    :param default_material: Fallback material if resolution fails.
    :return: ``(material, uvs)`` where *uvs* may be ``None``.
    """
    multiprop = ctx.resource_multiproperties.get(multiprop_id)
    if not multiprop:
        warn(f"Multiproperties {multiprop_id} not found")
        return default_material, None

    if p1 is None:
        warn(f"Multiproperties {multiprop_id} requires p1 index")
        return default_material, None

    pids_str = multiprop.pids if multiprop.pids else ""
    pids = pids_str.split() if pids_str else []

    # Resolve per-vertex multi indices (p2/p3 default to p1 per spec)
    vertex_indices = []
    for p in (p1, p2, p3):
        try:
            idx = int(p) if p is not None else int(p1)
        except ValueError:
            warn(f"Invalid multi index: {p}")
            return default_material, None
        if idx < 0 or idx >= len(multiprop.multis):
            warn(f"Multi index {idx} out of range for multiproperties {multiprop_id}")
            return default_material, None
        vertex_indices.append(idx)

    # Parse pindices for each vertex's multi entry
    vertex_pindices = []
    for idx in vertex_indices:
        multi = multiprop.multis[idx]
        pindices_str = multi.get("pindices", "")
        pindices = pindices_str.split() if pindices_str else []
        vertex_pindices.append(pindices)

    # Walk property groups: resolve basematerial from p1, per-vertex UVs from all three
    material = None
    uvs = None
    texture_group_ids: List[str] = []

    for i, pid in enumerate(pids):
        # Get the pindex for vertex 0 (p1) — used for basematerial
        if i >= len(vertex_pindices[0]):
            break
        pindex_v1 = int(vertex_pindices[0][i]) if vertex_pindices[0][i] else 0

        if pid in ctx.resource_materials:
            if material is None:
                material_group = ctx.resource_materials[pid]
                if pindex_v1 in material_group:
                    material = material_group[pindex_v1]
                    debug(
                        f"Multiproperties {multiprop_id}: resolved to material "
                        f"'{material.name}' from basematerials {pid}[{pindex_v1}]"
                    )

        elif pid in ctx.resource_texture_groups:
            texture_group = ctx.resource_texture_groups[pid]
            tex2coords = texture_group.tex2coords
            texture_group_ids.append(pid)

            # Resolve per-vertex UVs from each vertex's pindices
            try:
                vertex_uvs = []
                for v in range(3):
                    if i < len(vertex_pindices[v]):
                        uv_idx = int(vertex_pindices[v][i]) if vertex_pindices[v][i] else 0
                    else:
                        uv_idx = pindex_v1
                    if uv_idx < len(tex2coords):
                        vertex_uvs.append(tex2coords[uv_idx])
                    else:
                        vertex_uvs.append((0.0, 0.0))
                uvs = tuple(vertex_uvs)
            except (ValueError, IndexError):
                pass

    if material is None:
        debug(f"Multiproperties {multiprop_id}: no basematerial found, using default")
        material = default_material
    elif texture_group_ids:
        debug(f"Multiproperties {multiprop_id}: found {len(texture_group_ids)} texture groups")
        original_basematerial = material
        material = ResourceMaterial(
            name=original_basematerial.name,
            color=original_basematerial.color,
            metallic=original_basematerial.metallic,
            roughness=original_basematerial.roughness,
            specular_color=original_basematerial.specular_color,
            glossiness=original_basematerial.glossiness,
            ior=original_basematerial.ior,
            attenuation=original_basematerial.attenuation,
            transmission=original_basematerial.transmission,
            texture_id=texture_group_ids[0],
            metallic_texid=None,
            roughness_texid=None,
            specular_texid=None,
            glossiness_texid=None,
            basecolor_texid=None,
            extra_texture_ids=tuple(texture_group_ids[1:]) if len(texture_group_ids) > 1 else None,
        )
        ctx.textured_to_basematerial_map[material] = original_basematerial

    return material, uvs


# ---------------------------------------------------------------------------
# read_components
# ---------------------------------------------------------------------------

def read_components(
    ctx: "ImportContext",
    object_node: xml.etree.ElementTree.Element,
) -> List[Component]:
    """Read ``<component>`` elements from an ``<object>`` node.

    Supports Production Extension ``p:path`` for external model references.

    :param ctx: The import context (currently unused, reserved).
    :param object_node: An ``<object>`` element.
    :return: List of :class:`Component` instances.
    """
    result: List[Component] = []
    for component_node in object_node.iterfind(
        "./3mf:components/3mf:component", MODEL_NAMESPACES
    ):
        try:
            objectid = component_node.attrib["objectid"]
        except KeyError:
            continue

        transform = parse_transformation(
            component_node.attrib.get("transform", "")
        )

        path = component_node.attrib.get(f"{{{PRODUCTION_NAMESPACE}}}path")
        if path:
            debug(f"Component references external model: {path}")

        result.append(Component(resource_object=objectid, transformation=transform, path=path))
    return result


# ---------------------------------------------------------------------------
# read_objects
# ---------------------------------------------------------------------------

def read_objects(
    ctx: "ImportContext",
    root: xml.etree.ElementTree.Element,
) -> None:
    """Read all ``<object>`` resources from an XML root node.

    Populates ``ctx.resource_objects``.

    :param ctx: The import context.
    :param root: The root node of a ``3dmodel.model`` XML file.
    """
    from ..common.xml import read_metadata as _read_metadata
    from .archive import load_external_model
    from .triangle_sets import read_triangle_sets

    import time as _time
    _t0_all = _time.perf_counter()
    _total_verts = 0
    _total_tris = 0
    _ext_load_time = 0.0

    for object_node in root.iterfind(
        "./3mf:resources/3mf:object", MODEL_NAMESPACES
    ):
        try:
            objectid = object_node.attrib["id"]
        except KeyError:
            warn("Object resource without ID!")
            ctx.safe_report({"WARNING"}, "Object resource without ID")
            continue

        pid = object_node.attrib.get("pid")
        pindex = object_node.attrib.get("pindex")
        material = None

        if pid is not None and pindex is not None:
            if pid in ctx.resource_multiproperties:
                if not layer_kinds(ctx, pid):  # Layered groups become Blender materials.
                    ctx.object_passthrough_pids[objectid] = pid
                debug(f"Object {objectid} references multiproperties pid={pid}")
            else:
                try:
                    index = int(pindex)
                    material = ctx.resource_materials[pid][index]
                except KeyError:
                    if ctx.options.import_materials != "NONE":
                        warn(
                            f"Object with ID {objectid} refers to material collection "
                            f"{pid} with index {pindex} which doesn't exist."
                        )
                        ctx.safe_report(
                            {"WARNING"},
                            f"Object with ID {objectid} refers to material collection "
                            f"{pid} with index {pindex} which doesn't exist",
                        )
                    else:
                        debug(
                            f"Object with ID {objectid} refers to material {pid}:{pindex} "
                            f"(skipped due to import_materials=NONE)"
                        )
                except ValueError:
                    warn(
                        f"Object with ID {objectid} specifies material index "
                        f"{pindex}, which is not integer."
                    )
                    ctx.safe_report(
                        {"WARNING"},
                        f"Object with ID {objectid} specifies material index "
                        f"{pindex}, which is not integer",
                    )

        verts = read_vertices(ctx, object_node)
        _t_tri = _time.perf_counter()
        (
            triangles,
            mats,
            triangle_uvs,
            verts,
            segmentation_strings,
            seam_strings,
            support_strings,
            default_extruder,
        ) = read_triangles(ctx, object_node, material, pid, verts, objectid)
        layer_data, ctx.last_layer_data = ctx.last_layer_data, None
        _tri_elapsed = _time.perf_counter() - _t_tri
        _total_verts += len(verts)
        _total_tris += len(triangles)
        debug(
            f"[TIMING]     obj {objectid}: {len(verts)}v {len(triangles)}t  "
            f"parse={_tri_elapsed:.3f}s"
        )

        # Detect multiproperties at triangle level for passthrough
        converted = fast_array(ctx, object_node.find("./3mf:mesh/3mf:triangles", MODEL_NAMESPACES))
        if objectid not in ctx.object_passthrough_pids and ctx.resource_multiproperties and converted is not None:
            for tri_pid in np.unique(converted[:, 3]).tolist():
                if (
                    tri_pid >= 0 and str(tri_pid) in ctx.resource_multiproperties
                    and not layer_kinds(ctx, str(tri_pid))
                ):
                    ctx.object_passthrough_pids[objectid] = str(tri_pid)
                    debug(f"Object {objectid} has triangle-level multiproperties pid={tri_pid}")
                    break
        elif objectid not in ctx.object_passthrough_pids and ctx.resource_multiproperties:
            for tri_node in object_node.iterfind(
                "./3mf:mesh/3mf:triangles/3mf:triangle", MODEL_NAMESPACES
            ):
                tri_pid = tri_node.attrib.get("pid")
                if tri_pid and tri_pid in ctx.resource_multiproperties and not layer_kinds(ctx, tri_pid):
                    ctx.object_passthrough_pids[objectid] = tri_pid
                    debug(f"Object {objectid} has triangle-level multiproperties pid={tri_pid}")
                    break

        components = read_components(ctx, object_node)

        _t_ext = _time.perf_counter()
        for component in components:
            if component.path:
                load_external_model(ctx, component.path)
        _ext_load_time += _time.perf_counter() - _t_ext

        metadata = Metadata()
        for metadata_node in object_node.iterfind(
            "./3mf:metadatagroup", MODEL_NAMESPACES
        ):
            metadata = _read_metadata(metadata_node, metadata, ctx.operator)
        if "partnumber" in object_node.attrib:
            metadata["3mf:partnumber"] = MetadataEntry(
                name="3mf:partnumber",
                preserve=True,
                datatype="xs:string",
                value=object_node.attrib["partnumber"],
            )
        if "name" in object_node.attrib and "Title" not in metadata:
            object_name = str(object_node.attrib.get("name"))
            metadata["Title"] = MetadataEntry(
                name="Title",
                preserve=True,
                datatype="xs:string",
                value=object_name,
            )

        metadata["3mf:object_type"] = MetadataEntry(
            name="3mf:object_type",
            preserve=True,
            datatype="xs:string",
            value=object_node.attrib.get("type", "model"),
        )

        triangle_sets = read_triangle_sets(ctx, object_node)

        if isinstance(triangle_uvs, np.ndarray):
            has_uvs = True
        else:
            has_uvs = any(uv is not None for uv in triangle_uvs) if triangle_uvs else False

        ctx.resource_objects[objectid] = ResourceObject(
            vertices=verts,
            triangles=triangles,
            materials=mats,
            components=components,
            metadata=metadata,
            triangle_sets=triangle_sets,
            triangle_uvs=triangle_uvs if has_uvs else None,
            layer_uvs=layer_data.uvs if layer_data and layer_data.uvs else None,
            layer_colors=layer_data.colors if layer_data and layer_data.colors else None,
            segmentation_strings=segmentation_strings if segmentation_strings else None,
            seam_strings=seam_strings if seam_strings else None,
            support_strings=support_strings if support_strings else None,
            default_extruder=default_extruder,
        )

    debug(
        f"[TIMING]   read_objects total: {_time.perf_counter() - _t0_all:.3f}s  "
        f"{len(ctx.resource_objects)} objects  {_total_verts}v {_total_tris}t  "
        f"ext_load={_ext_load_time:.3f}s"
    )


# ---------------------------------------------------------------------------
# read_external_model_objects  (Production Extension)
# ---------------------------------------------------------------------------

def read_external_model_objects(
    ctx: "ImportContext",
    root: xml.etree.ElementTree.Element,
    source_path: str,
) -> None:
    """Read objects from an external model file (Production Extension).

    Uses the ``paint_only=True`` path since Orca external models only
    carry ``paint_color`` attributes (no texture groups or multiproperties).

    :param ctx: The import context.
    :param root: Root element of the external model XML.
    :param source_path: Archive path of the source file (for logging).
    """
    from ..common.xml import read_metadata as _read_metadata
    from .triangle_sets import read_triangle_sets

    for object_node in root.iterfind(
        "./3mf:resources/3mf:object", MODEL_NAMESPACES
    ):
        try:
            objectid = object_node.attrib["id"]
        except KeyError:
            warn(f"Object in {source_path} without ID!")
            continue

        if objectid in ctx.resource_objects:
            debug(f"Object {objectid} already loaded, skipping duplicate from {source_path}")
            continue

        verts = read_vertices(ctx, object_node)
        (
            triangles,
            mats,
            _triangle_uvs,
            verts,
            segmentation_strings,
            seam_strings,
            support_strings,
            default_extruder,
        ) = read_triangles(
            ctx, object_node, None, None, verts, objectid, paint_only=True
        )

        components = read_components(ctx, object_node)

        metadata = Metadata()
        for metadata_node in object_node.iterfind(
            "./3mf:metadatagroup", MODEL_NAMESPACES
        ):
            metadata = _read_metadata(metadata_node, metadata, ctx.operator)

        if "name" in object_node.attrib and "Title" not in metadata:
            object_name = str(object_node.attrib.get("name"))
            metadata["Title"] = MetadataEntry(
                name="Title", preserve=True, datatype="xs:string", value=object_name
            )

        metadata["3mf:object_type"] = MetadataEntry(
            name="3mf:object_type",
            preserve=True,
            datatype="xs:string",
            value=object_node.attrib.get("type", "model"),
        )

        triangle_sets = read_triangle_sets(ctx, object_node)

        ctx.resource_objects[objectid] = ResourceObject(
            vertices=verts,
            triangles=triangles,
            materials=mats,
            components=components,
            metadata=metadata,
            triangle_sets=triangle_sets,
            triangle_uvs=None,
            segmentation_strings=segmentation_strings if segmentation_strings else None,
            seam_strings=seam_strings if seam_strings else None,
            support_strings=support_strings if support_strings else None,
            default_extruder=default_extruder,
        )
        debug(
            f"Loaded object {objectid} from {source_path} with "
            f"{len(verts)} vertices, {len(triangles)} triangles"
        )
