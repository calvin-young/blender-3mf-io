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
Export layered materials as ``<multiproperties>``.

A material whose Base Color is a stack of layers blended with Mix nodes is
written as a ``<multiproperties>`` group, one layer per property group:

- Image Texture (with a UV Map node, or the active UV map) -> ``<texture2dgroup>``
- RGB node or an unconnected socket -> ``<colorgroup>`` with one color
- Color Attribute -> ``<colorgroup>`` with the attribute's colors

Layers are blended like this (the same pattern the importer creates):

- Mix (Mix) with the factor connected to the upper layer's Alpha -> ``mix``
- Mix (Multiply) with factor 1 -> ``multiply``

This is how a label or placeholder texture with transparent areas is put on
top of a full color texture.
"""

import os
import tempfile
import xml.etree.ElementTree
import zipfile
from typing import Dict, List, Optional

import bpy
import numpy as np

from ..common import debug, warn
from ..common.constants import MATERIAL_NAMESPACE, MODEL_NAMESPACE, TEXTURE_MIMETYPE_PNG, TEXTURE_MIMETYPE_JPEG

__all__ = [
    "find_layers",
    "detect_layered_materials",
    "LayeredExport",
]

# Image Texture extension -> 3MF tile style
_TILE_STYLE = {"REPEAT": "wrap", "MIRROR": "mirror", "EXTEND": "clamp", "CLIP": "none"}


def _linked_from(socket):
    """Return (node, output socket) linked into *socket*, or (None, None)."""
    if not socket.is_linked or not socket.links:
        return None, None
    link = socket.links[0]
    return link.from_node, link.from_socket


def _mix_sockets(node):
    """Return (factor, a, b, blend_type) of a Mix (color) node, or None."""
    if node.type == "MIX" and getattr(node, "data_type", "") == "RGBA":
        return node.inputs[0], node.inputs[6], node.inputs[7], node.blend_type
    if node.type == "MIX_RGB":
        return node.inputs["Fac"], node.inputs["Color1"], node.inputs["Color2"], node.blend_type
    return None


def _layer_from(node, output) -> Optional[Dict]:
    """Describe the layer a node provides, or None if it isn't a layer."""
    if node is None:
        return None
    if node.type == "TEX_IMAGE" and node.image is not None and output.name == "Color":
        uv_map = None
        vector_node, _ = _linked_from(node.inputs["Vector"])
        if vector_node is not None:
            if vector_node.type == "UVMAP":
                uv_map = vector_node.uv_map or None
            elif vector_node.type != "TEX_COORD":
                return None  # Mapping, projections, ...: not a plain UV lookup.
        return {
            "kind": "texture", "image": node.image, "uv_map": uv_map, "node": node,
            "tilestyle": _TILE_STYLE.get(node.extension, "wrap"),
            "filter": "nearest" if node.interpolation == "Closest" else "auto",
        }
    if node.type == "VERTEX_COLOR" and output.name == "Color":
        return {"kind": "attribute", "name": node.layer_name, "node": node}
    if node.type == "RGB":
        return {"kind": "color", "color": tuple(node.outputs[0].default_value)}
    return None


def _alpha_matches(factor, layer: Dict) -> bool:
    """Whether a Mix factor is the alpha of *layer* (so the Mix is a 'mix' blend)."""
    node, output = _linked_from(factor)
    if node is None:
        return False
    return node == layer.get("node") and output.name == "Alpha"


def find_layers(material: bpy.types.Material) -> Optional[List[Dict]]:
    """Return the layers of a layered material, bottom first, or None.

    Each layer is a dict with ``kind`` ("texture", "color" or "attribute"),
    its data, and ``blend`` ("mix" or "multiply"; None for the first layer).
    Only materials with at least two layers count as layered.
    """
    if material is None or not material.use_nodes or material.node_tree is None:
        return None
    principled = next((n for n in material.node_tree.nodes if n.type == "BSDF_PRINCIPLED"), None)
    if principled is None:
        return None
    base_color = principled.inputs.get("Base Color")
    layers: List[Dict] = []

    def walk(socket) -> bool:
        node, output = _linked_from(socket)
        if node is None:
            layers.append({"kind": "color", "color": tuple(socket.default_value), "blend": None})
            return True
        mix = _mix_sockets(node)
        if mix is not None:
            factor, a, b, blend_type = mix
            if not walk(a):
                return False
            upper_node, upper_output = _linked_from(b)
            upper = _layer_from(upper_node, upper_output) if upper_node else {
                "kind": "color", "color": tuple(b.default_value)}
            if upper is None:
                return False
            if blend_type == "MIX" and _alpha_matches(factor, upper):
                upper["blend"] = "mix"
            elif blend_type == "MIX" and not factor.is_linked and upper["kind"] == "color":
                upper["color"] = tuple(upper["color"][:3]) + (float(factor.default_value),)
                upper["blend"] = "mix"
            elif blend_type == "MULTIPLY" and not factor.is_linked and factor.default_value == 1.0:
                upper["blend"] = "multiply"
            else:
                return False
            layers.append(upper)
            return True
        layer = _layer_from(node, output)
        if layer is None:
            return False
        layer["blend"] = None
        layers.append(layer)
        return True

    if not walk(base_color) or len(layers) < 2:
        return None
    return layers


def detect_layered_materials(blender_objects) -> Dict[str, List[Dict]]:
    """Find the layered materials used by *blender_objects*: name -> layers."""
    result = {}
    for blender_object in blender_objects:
        for slot in getattr(blender_object, "material_slots", []):
            material = slot.material
            if material is None or material.name in result:
                continue
            layers = find_layers(material)
            if layers:
                result[material.name] = layers
                debug(f"Detected layered material '{material.name}' with {len(layers)} layers")
    return result


def _hex(color) -> str:
    """Linear RGBA (0-1) to an sRGB #RRGGBB(AA) string."""
    def to_srgb(value):
        value = min(max(float(value), 0.0), 1.0)
        return value * 12.92 if value <= 0.0031308 else 1.055 * value ** (1 / 2.4) - 0.055
    r, g, b = (round(to_srgb(c) * 255) for c in color[:3])
    a = round(min(max(float(color[3]) if len(color) > 3 else 1.0, 0.0), 1.0) * 255)
    return f"#{r:02X}{g:02X}{b:02X}" + (f"{a:02X}" if a != 255 else "")


def texture_archive_path(image: bpy.types.Image) -> str:
    """Archive path for an image: its original 3MF path if it has one and wasn't renamed.

    Otherwise the path comes from the image name, so names stay readable.
    """
    original = image.get("3mf_texture_path", "")
    if original:
        stem = os.path.splitext(os.path.basename(original))[0]
        if image.name in (os.path.basename(original), stem):
            return original.lstrip("/")
    name = "".join(c if c.isalnum() or c in "._- ()" else "_" for c in image.name)
    if not name.lower().endswith((".png", ".jpg", ".jpeg")):
        name += ".png"
    return f"3D/Textures/{name}"


def write_image(archive: zipfile.ZipFile, image: bpy.types.Image, archive_path: str) -> bool:
    """Write an image into the archive, using its packed bytes when they're current."""
    try:
        archive.getinfo(archive_path)
        return True  # Already written.
    except KeyError:
        pass
    if image.packed_file is not None and not image.is_dirty:
        archive.writestr(archive_path, image.packed_file.data)
        return True
    file_format = "JPEG" if archive_path.lower().endswith((".jpg", ".jpeg")) else "PNG"
    with tempfile.TemporaryDirectory() as directory:
        temporary = os.path.join(directory, "texture" + os.path.splitext(archive_path)[1])
        try:
            _save_copy(image, temporary, file_format)
        except Exception as e:
            warn(f"Failed to write texture '{image.name}': {e}")
            return False
        archive.write(temporary, archive_path)
    return True


def _save_copy(image, path, file_format):
    original_path, original_format = image.filepath_raw, image.file_format
    try:
        image.filepath_raw = path
        image.file_format = file_format
        image.save()
    finally:
        image.filepath_raw, image.file_format = original_path, original_format


class _Group:
    """One property group of a layer while exporting: its element and its entries."""

    def __init__(self, element, kind):
        self.element = element
        self.kind = kind
        self.index: Dict = {}  # entry -> index

    def indices(self, keys: np.ndarray) -> np.ndarray:
        """Map rows of *keys* to entry indices, adding new entries."""
        distinct, inverse = np.unique(keys, axis=0, return_inverse=True)
        mapping = np.empty(len(distinct), dtype=np.int64)
        index = self.index
        for number, row in enumerate(map(tuple, distinct.tolist())):
            if row not in index:
                index[row] = len(index)
            mapping[number] = index[row]
        return mapping[inverse.reshape(-1)]


class LayeredExport:
    """Writes the resources of layered materials and the property references of their triangles."""

    def __init__(self, layered_materials: Dict[str, List[Dict]], next_resource_id: int):
        self.layered_materials = layered_materials
        self.next_resource_id = next_resource_id
        self.image_paths: Dict[str, str] = {}  # image name -> "/3D/Textures/..."
        self.materials: Dict[str, Dict] = {}  # material name -> {"id", "groups", "multis", "element"}

    def _new_id(self) -> int:
        value = self.next_resource_id
        self.next_resource_id += 1
        return value

    def write_resources(self, archive: zipfile.ZipFile, resources_element) -> None:
        """Write images, ``<texture2d>``, groups and ``<multiproperties>`` (entries come later)."""
        sub = xml.etree.ElementTree.SubElement
        texture_ids: Dict[tuple, int] = {}
        for material_name, layers in self.layered_materials.items():
            groups = []
            base_material = bpy.data.materials[material_name].get("3mf_base_material")
            for position, layer in enumerate(layers):
                if position == 0 and base_material and layer["kind"] == "color":
                    element = sub(resources_element, f"{{{MODEL_NAMESPACE}}}basematerials", {"id": str(self._new_id())})
                    sub(element, f"{{{MODEL_NAMESPACE}}}base",
                        {"name": str(base_material), "displaycolor": _hex(layer["color"])})
                    group = _Group(element, "material")
                    group.index[("base",)] = 0
                    groups.append(group)
                elif layer["kind"] == "texture":
                    image = layer["image"]
                    path = self.image_paths.get(image.name)
                    if path is None:
                        archive_path = texture_archive_path(image)
                        if not write_image(archive, image, archive_path):
                            break
                        path = self.image_paths[image.name] = "/" + archive_path
                    key = (path, layer["tilestyle"], layer["filter"])
                    if key not in texture_ids:
                        texture_ids[key] = self._new_id()
                        attrib = {
                            "id": str(texture_ids[key]),
                            "path": path,
                            "contenttype": TEXTURE_MIMETYPE_JPEG if path.lower().endswith((".jpg", ".jpeg"))
                            else TEXTURE_MIMETYPE_PNG,
                        }
                        if layer["tilestyle"] != "wrap":
                            attrib["tilestyleu"] = attrib["tilestylev"] = layer["tilestyle"]
                        if layer["filter"] != "auto":
                            attrib["filter"] = layer["filter"]
                        sub(resources_element, f"{{{MATERIAL_NAMESPACE}}}texture2d", attrib)
                    element = sub(resources_element, f"{{{MATERIAL_NAMESPACE}}}texture2dgroup",
                                  {"id": str(self._new_id()), "texid": str(texture_ids[key])})
                    groups.append(_Group(element, "texture"))
                else:
                    element = sub(resources_element, f"{{{MATERIAL_NAMESPACE}}}colorgroup",
                                  {"id": str(self._new_id())})
                    group = _Group(element, "color")
                    if layer["kind"] == "color":
                        group.index[(_hex(layer["color"]),)] = 0
                    groups.append(group)
            else:
                blends = [layer["blend"] for layer in layers[1:]]
                attrib = {"id": str(self._new_id()), "pids": " ".join(g.element.get("id") for g in groups)}
                if "multiply" in blends:
                    attrib["blendmethods"] = " ".join(blends)
                element = sub(resources_element, f"{{{MATERIAL_NAMESPACE}}}multiproperties", attrib)
                self.materials[material_name] = {
                    "id": attrib["id"], "groups": groups, "multis": _Group(element, "multi"),
                    "element": element, "layers": layers,
                }

    def triangle_fragments(self, mesh, loops: np.ndarray, material_indices: np.ndarray,
                           material_slots) -> Optional[List[Optional[str]]]:
        """Return the pid/p1/p2/p3 attributes of each triangle with a layered material.

        :param loops: Corner (loop) indices of the triangles, shape (N, 3).
        :param material_indices: Material slot of each triangle.
        :return: One string (or None) per triangle, or None if no slot is layered.
        """
        result = None
        for slot_index, slot in enumerate(material_slots):
            material = slot.material
            if material is None or material.name not in self.materials:
                continue
            triangles = np.flatnonzero(material_indices == slot_index)
            if len(triangles) == 0:
                continue
            state = self.materials[material.name]
            corners = loops[triangles]  # (n, 3)
            per_layer = []
            for layer, group in zip(state["layers"], state["groups"]):
                if layer["kind"] == "texture":
                    uv_layer = mesh.uv_layers.get(layer["uv_map"]) if layer["uv_map"] else mesh.uv_layers.active
                    if uv_layer is None:
                        uv_layer = mesh.uv_layers.active
                    uvs = np.zeros((len(mesh.loops), 2), dtype=np.float32)
                    if uv_layer is not None:
                        uv_layer.data.foreach_get("uv", uvs.ravel())
                    keys = uvs[corners.ravel()]
                    per_layer.append(group.indices(keys))
                elif layer["kind"] == "attribute":
                    attribute = mesh.color_attributes.get(layer["name"])
                    if attribute is None:
                        keys = np.ones((corners.size, 4), dtype=np.float32)
                    else:
                        values = np.empty(len(attribute.data) * 4, dtype=np.float32)
                        attribute.data.foreach_get("color", values)
                        values = values.reshape(-1, 4)
                        if attribute.domain == "POINT":
                            vertex_of_loop = np.empty(len(mesh.loops), dtype=np.int32)
                            mesh.loops.foreach_get("vertex_index", vertex_of_loop)
                            values = values[vertex_of_loop]
                        keys = values[corners.ravel()]
                    # Key by the written hex string so equal colors share an entry.
                    distinct, inverse = np.unique(np.round(keys * 255), axis=0, return_inverse=True)
                    hexes = np.array([[_hex(row / 255.0)] for row in distinct], dtype=object)
                    numbers = np.array([group.index.setdefault((h[0],), len(group.index)) for h in hexes],
                                       dtype=np.int64)
                    per_layer.append(numbers[inverse.reshape(-1)])
                else:
                    per_layer.append(np.zeros(corners.size, dtype=np.int64))
            multi = state["multis"].indices(np.stack(per_layer, axis=1)).reshape(-1, 3)
            if result is None:
                result = [None] * len(material_indices)
            pid = state["id"]
            for triangle, (a, b, c) in zip(triangles.tolist(), multi.tolist()):
                result[triangle] = f' pid="{pid}" p1="{a}" p2="{b}" p3="{c}"'
        return result

    def finish(self, resources_element) -> None:
        """Write the collected entries; remove the groups of materials no triangle used."""
        sub = xml.etree.ElementTree.SubElement
        for state in self.materials.values():
            if not state["multis"].index:
                for group in state["groups"]:
                    resources_element.remove(group.element)
                resources_element.remove(state["element"])
                continue
            for group in state["groups"]:
                ordered = sorted(group.index.items(), key=lambda item: item[1])
                if group.kind == "material":
                    continue  # Written with its one <base> already.
                if group.kind == "texture":
                    for (u, v), _ in ordered:
                        sub(group.element, f"{{{MATERIAL_NAMESPACE}}}tex2coord",
                            {"u": f"{u:.7g}", "v": f"{v:.7g}"})
                else:
                    for (color,), _ in ordered:
                        sub(group.element, f"{{{MATERIAL_NAMESPACE}}}color", {"color": color})
            for indices, _ in sorted(state["multis"].index.items(), key=lambda item: item[1]):
                sub(state["element"], f"{{{MATERIAL_NAMESPACE}}}multi",
                    {"pindices": " ".join(str(i) for i in indices)})
        # Unused <texture2d> elements (all their groups removed) are harmless but untidy.
        used = {g.get("texid") for g in resources_element.iter(f"{{{MATERIAL_NAMESPACE}}}texture2dgroup")}
        for texture in list(resources_element.iter(f"{{{MATERIAL_NAMESPACE}}}texture2d")):
            if texture.get("id") not in used and texture.get("path") in self.image_paths.values():
                resources_element.remove(texture)
