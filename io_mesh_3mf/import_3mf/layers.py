# Blender add-on to import and export 3MF files.
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
Layered properties: ``<multiproperties>`` whose layers are colors and textures.

A ``<multiproperties>`` group stacks property groups on top of each other,
for example a full color texture with a partly transparent label texture on
top.  Each ``<multi>`` picks one entry from every layer, and each triangle
corner picks a ``<multi>``.

Such a group is imported as one Blender material that blends the layers with
Mix nodes, as the Materials Extension specifies:

- ``mix``: Mix (Mix) with the layer's alpha as the factor.
- ``multiply``: Mix (Multiply) with factor 1.

Every layer gets its own per-corner data on the mesh: a UV map for a texture
layer, or a color attribute for a color layer whose color varies.  Layer 0
uses the regular ``UVMap``, so single textures and layered textures share it.
"""

from typing import Callable, Dict, List, Optional, Tuple, TYPE_CHECKING

import numpy as np

from ..common import warn
from ..common.types import ResourceMaterial

if TYPE_CHECKING:
    from .context import ImportContext

__all__ = [
    "layer_uv_name",
    "layer_color_name",
    "layer_kinds",
    "LayerData",
    "resolve_layered_rows",
]

# Color layers with more distinct colors than this get a color attribute
# instead of one material per color combination.
_MAX_COLOR_MATERIALS = 16


def layer_uv_name(position: int) -> str:
    """Name of the UV map holding the texture coordinates of layer *position*."""
    return "UVMap" if position == 0 else f"UVMap Layer {position}"


def layer_color_name(position: int) -> str:
    """Name of the color attribute holding the colors of layer *position*."""
    return f"Color Layer {position}"


def layer_kinds(ctx: "ImportContext", multiprop_id: str) -> Optional[List[str]]:
    """Return the kind of each layer, or None if Blender can't represent the group.

    Kinds: ``"material"`` (basematerials, first layer only), ``"color"``
    (colorgroup) and ``"texture"`` (texture2dgroup).
    """
    multiprop = ctx.resource_multiproperties.get(multiprop_id)
    if multiprop is None:
        return None
    kinds = []
    for position, pid in enumerate((multiprop.pids or "").split()):
        if pid in ctx.resource_texture_groups:
            texture_group = ctx.resource_texture_groups[pid]
            texture = ctx.resource_textures.get(texture_group.texid)
            if texture is None or texture.blender_image is None or not texture.blender_image.size[0]:
                return None  # Texture missing or unreadable: keep the data as passthrough.
            kinds.append("texture")
        elif pid in ctx.resource_colorgroups:
            kinds.append("color")
        elif pid in ctx.resource_materials and position == 0:
            kinds.append("material")
        else:
            return None  # Composite materials, unknown groups, ...
    if "texture" not in kinds:
        return None  # Material and color stacks stay passthrough data.
    return kinds


class LayerData:
    """Per-corner layer data of one object, filled while resolving its triangles."""

    def __init__(self, count: int):
        self.count = count
        self.uvs: Dict[int, np.ndarray] = {}  # Layer position -> (N, 3, 2)
        self.colors: Dict[int, np.ndarray] = {}  # Layer position -> (N, 3, 4) linear RGBA
        self.uv_rows: Dict[int, np.ndarray] = {}  # Layer position -> which triangles have UVs

    def set_uvs(self, position: int, rows: np.ndarray, uvs: np.ndarray) -> None:
        if position not in self.uvs:
            self.uvs[position] = np.zeros((self.count, 3, 2), dtype=np.float64)
            self.uv_rows[position] = np.zeros(self.count, dtype=bool)
        self.uvs[position][rows] = uvs
        self.uv_rows[position][rows] = True

    def set_colors(self, position: int, rows: np.ndarray, colors: np.ndarray) -> None:
        if position not in self.colors:
            self.colors[position] = np.ones((self.count, 3, 4), dtype=np.float64)
        self.colors[position][rows] = colors

    def merge_base_uvs(self, triangle_uvs: Optional[np.ndarray]) -> Optional[np.ndarray]:
        """Move layer 0 texture coordinates into the regular per-triangle UVs (both use ``UVMap``)."""
        if 0 not in self.uvs:
            return triangle_uvs
        if triangle_uvs is None:
            triangle_uvs = np.zeros((self.count, 3, 2), dtype=np.float64)
        rows = self.uv_rows.pop(0)
        triangle_uvs[rows] = self.uvs.pop(0)[rows]
        return triangle_uvs


def _multi_indices(ctx: "ImportContext", multiprop_id: str, layer_count: int) -> np.ndarray:
    """``pindices`` of every ``<multi>`` as an int array with one column per layer.

    Missing indices default to 0, as the spec requires; extra ones are ignored.
    """
    cache = ctx.fast_arrays.setdefault("multiproperties", {})
    array = cache.get(multiprop_id)
    if array is None:
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
    result = np.zeros((len(array), layer_count), dtype=np.int64)
    width = min(array.shape[1], layer_count)
    result[:, :width] = np.maximum(array[:, :width], 0)
    return result


def _colorgroup_rgba(ctx: "ImportContext", colorgroup_id: str) -> np.ndarray:
    """Colors of a colorgroup as linear RGBA, alpha as written (it matters in upper layers)."""
    from .materials.base import parse_hex_color
    colors = ctx.resource_colorgroups[colorgroup_id].colors
    return np.array([parse_hex_color(color) for color in colors] or [(1.0, 1.0, 1.0, 1.0)], dtype=np.float64)


def resolve_layered_rows(
    ctx: "ImportContext",
    multiprop_id: str,
    kinds: List[str],
    properties: np.ndarray,
    rows: np.ndarray,
    layer_data: LayerData,
    assign: Callable[[np.ndarray, Optional[ResourceMaterial]], None],
    default_material: Optional[ResourceMaterial],
) -> None:
    """Resolve triangles that reference a layered ``<multiproperties>`` group.

    :param kinds: Result of :func:`layer_kinds` for the group.
    :param properties: Array of shape (N, 3): p1 p2 p3 of each triangle, -1 if absent.
    :param rows: Index of each of those triangles in the object (for *layer_data*).
    :param assign: Called with triangle indices (into *rows*' object) and their material.
    """
    multiprop = ctx.resource_multiproperties[multiprop_id]
    pids = (multiprop.pids or "").split()
    ctx.used_property_ids.update(pids)  # The layers are represented by the Blender material.
    blends = (multiprop.blendmethods or "").split()
    blends = [(blends[i - 1] if i - 1 < len(blends) else "mix").lower() for i in range(1, len(pids))]
    multis = _multi_indices(ctx, multiprop_id, len(pids))

    first = properties[:, 0]
    indices = np.stack((
        first,
        np.where(properties[:, 1] < 0, first, properties[:, 1]),
        np.where(properties[:, 2] < 0, first, properties[:, 2]),
    ), axis=1)
    valid = (first >= 0) & (indices >= 0).all(axis=1) & (indices < len(multis)).all(axis=1)
    if not valid.all():
        warn(f"Multi index out of range for multiproperties {multiprop_id} "
             f"on {int((~valid).sum())} triangles")
        assign(rows[~valid], default_material)
    rows = rows[valid]
    if len(rows) == 0:
        return
    pindices = multis[indices[valid]]  # (n triangles, 3 corners, layers)

    # Layers that select a material per triangle: the base material, and color
    # layers with few colors that are the same on all corners of each triangle.
    keyed_layers = []
    for position, kind in enumerate(kinds):
        if kind == "material":
            keyed_layers.append(position)
        elif kind == "color":
            column = pindices[:, :, position]
            uniform = (column == column[:, :1]).all()
            if uniform and len(np.unique(column[:, 0])) <= _MAX_COLOR_MATERIALS:
                keyed_layers.append(position)

    for position, kind in enumerate(kinds):
        pid = pids[position]
        if kind == "texture":
            texture_group = ctx.resource_texture_groups[pid]
            tex2coords = np.asarray(texture_group.tex2coords, dtype=np.float64).reshape(-1, 2)
            uv_indices = pindices[:, :, position]
            in_range = uv_indices < len(tex2coords)
            uvs = np.zeros(uv_indices.shape + (2,), dtype=np.float64)
            uvs[in_range] = tex2coords[uv_indices[in_range]]
            layer_data.set_uvs(position, rows, uvs)
        elif kind == "color" and position not in keyed_layers:
            rgba = _colorgroup_rgba(ctx, pid)
            color_indices = np.minimum(pindices[:, :, position], len(rgba) - 1)
            layer_data.set_colors(position, rows, rgba[color_indices])

    keys = pindices[:, 0, keyed_layers]
    distinct, inverse = np.unique(keys, axis=0, return_inverse=True)
    inverse = inverse.reshape(-1)
    for which, key in enumerate(distinct.tolist()):
        material = _layered_material(ctx, multiprop_id, kinds, pids, blends, dict(zip(keyed_layers, key)))
        assign(rows[inverse == which], material)


def _layered_material(
    ctx: "ImportContext",
    multiprop_id: str,
    kinds: List[str],
    pids: List[str],
    blends: List[str],
    keyed: Dict[int, int],
) -> ResourceMaterial:
    """Create (or reuse) the ResourceMaterial describing one layer stack."""
    cache = ctx.fast_arrays.setdefault("layered_materials", {})
    cache_key = (multiprop_id, tuple(sorted(keyed.items())))
    if cache_key in cache:
        return cache[cache_key]

    layers: List[Tuple] = []
    names = []
    base_material = None
    for position, (kind, pid) in enumerate(zip(kinds, pids)):
        blend = blends[position - 1] if position else None
        if kind == "material":
            group = ctx.resource_materials[pid]
            base_material = group.get(keyed[position]) or next(iter(group.values()), None)
            color = base_material.color if base_material and base_material.color else (0.8, 0.8, 0.8, 1.0)
            layers.append(("material", tuple(color), base_material.name if base_material else "Material", blend))
            names.append(base_material.name if base_material else "Material")
        elif kind == "color" and position in keyed:
            rgba = _colorgroup_rgba(ctx, pid)
            color = tuple(rgba[min(keyed[position], len(rgba) - 1)].tolist())
            layers.append(("color", color, None, blend))
            names.append("#%02X%02X%02X" % tuple(round(_linear_to_srgb(c) * 255) for c in color[:3]))
        elif kind == "color":
            layers.append(("attribute", None, layer_color_name(position), blend))
            names.append("Colors")
        else:
            texture_group = ctx.resource_texture_groups[pid]
            texture = ctx.resource_textures[texture_group.texid]
            layers.append(("texture", texture_group.texid, layer_uv_name(position), blend))
            names.append(texture.blender_image.name)

    name = " + ".join(names)
    template = base_material or ResourceMaterial()
    material = ResourceMaterial(
        name=name,
        color=(1.0, 1.0, 1.0, 1.0),
        metallic=template.metallic,
        roughness=template.roughness,
        specular_color=template.specular_color,
        glossiness=template.glossiness,
        ior=template.ior,
        attenuation=template.attenuation,
        transmission=template.transmission,
        layers=tuple(layers),
    )
    cache[cache_key] = material
    return material


def _linear_to_srgb(value: float) -> float:
    if value <= 0.0031308:
        return max(0.0, value * 12.92)
    return min(1.0, 1.055 * value ** (1 / 2.4) - 0.055)
