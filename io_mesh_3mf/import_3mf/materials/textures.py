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
Texture import functionality for 3MF Materials Extension.

This module handles:
- Reading texture2d elements (image metadata)
- Reading texture2dgroup elements (UV coordinate sets)
- Extracting texture images from archives
- Setting up Blender materials with texture nodes
"""

import os
import tempfile
import zipfile
from typing import Dict, Optional

import bpy

from ...common import debug, warn, error
from ..fast_parse import fast_array

from ...common.types import (
    ResourceTexture,
    ResourceTextureGroup,
    ResourceMaterial,
)


def read_textures(op, root, material_ns: Dict[str, str]) -> None:
    """
    Parse <m:texture2d> elements from the 3MF document.

    Texture2D elements define image resources within the archive.
    Per 3MF Materials Extension spec:
    - path: Required. Path to image file in archive
    - contenttype: Required. "image/png" or "image/jpeg"
    - tilestyleu, tilestylev: Optional. "wrap" (default), "mirror", "clamp", "none"
    - filter: Optional. "auto" (default), "linear", "nearest"

    :param op: The Import3MF operator instance.
    :param root: XML root element.
    :param material_ns: Namespace dict for materials extension.
    """
    from ...common.constants import MODEL_NAMESPACES

    for texture_item in root.iterfind(
        "./3mf:resources/m:texture2d", {**MODEL_NAMESPACES, **material_ns}
    ):
        try:
            texture_id = texture_item.attrib["id"]
        except KeyError:
            warn("Encountered a texture2d without resource ID.")
            op.safe_report({"WARNING"}, "Encountered a texture2d without resource ID")
            continue

        if texture_id in op.resource_textures:
            warn(f"Duplicate texture ID: {texture_id}")
            continue

        # Required attributes
        try:
            path = texture_item.attrib["path"]
            contenttype = texture_item.attrib["contenttype"]
        except KeyError as e:
            warn(f"Texture {texture_id} missing required attribute: {e}")
            continue

        # Validate content type
        if contenttype not in ("image/png", "image/jpeg"):
            warn(f"Texture {texture_id} has unsupported contenttype: {contenttype}")
            continue

        # Optional attributes with defaults
        tilestyleu = texture_item.attrib.get("tilestyleu", "wrap")
        tilestylev = texture_item.attrib.get("tilestylev", "wrap")
        filter_mode = texture_item.attrib.get("filter", "auto")

        op.resource_textures[texture_id] = ResourceTexture(
            path=path,
            contenttype=contenttype,
            tilestyleu=tilestyleu,
            tilestylev=tilestylev,
            filter=filter_mode,
            blender_image=None,
        )
        debug(f"Parsed texture2d {texture_id}: {path} ({contenttype})")

    if op.resource_textures:
        debug(f"Found {len(op.resource_textures)} texture2d resources")


def read_texture_groups(
    op, root, material_ns: Dict[str, str], display_properties: Dict
) -> None:
    """
    Parse <m:texture2dgroup> elements from the 3MF document.

    Texture2DGroup elements contain UV coordinate sets and reference a texture2d.

    :param op: The Import3MF operator instance.
    :param root: XML root element.
    :param material_ns: Namespace dict for materials extension.
    :param display_properties: Parsed PBR display properties lookup.
    """
    from ...common.constants import MODEL_NAMESPACES

    for group_item in root.iterfind(
        "./3mf:resources/m:texture2dgroup", {**MODEL_NAMESPACES, **material_ns}
    ):
        try:
            group_id = group_item.attrib["id"]
        except KeyError:
            warn("Encountered a texture2dgroup without resource ID.")
            op.safe_report(
                {"WARNING"}, "Encountered a texture2dgroup without resource ID"
            )
            continue

        if group_id in op.resource_texture_groups:
            warn(f"Duplicate texture2dgroup ID: {group_id}")
            continue

        try:
            texid = group_item.attrib["texid"]
        except KeyError:
            warn(f"Texture2dgroup {group_id} missing required texid attribute")
            continue

        if texid not in op.resource_textures:
            warn(f"Texture2dgroup {group_id} references unknown texture: {texid}")
            continue

        display_props_id = group_item.attrib.get("displaypropertiesid")

        tex2coords = fast_array(op, group_item)
        if tex2coords is not None:
            tex2coords = [tuple(coord) for coord in tex2coords.tolist()]
        else:
            tex2coords = []
        for coord_item in group_item.iterfind("./m:tex2coord", material_ns):
            try:
                u = float(coord_item.attrib.get("u", "0"))
                v = float(coord_item.attrib.get("v", "0"))
                tex2coords.append((u, v))
            except (ValueError, KeyError) as e:
                warn(f"Invalid tex2coord in group {group_id}: {e}")
                tex2coords.append((0.0, 0.0))

        if not tex2coords:
            warn(f"Texture2dgroup {group_id} has no tex2coords")
            continue

        op.resource_texture_groups[group_id] = ResourceTextureGroup(
            texid=texid, tex2coords=tex2coords, displaypropertiesid=display_props_id
        )
        debug(
            f"Parsed texture2dgroup {group_id}: {len(tex2coords)} UVs referencing texture {texid}"
        )

    if op.resource_texture_groups:
        debug(f"Found {len(op.resource_texture_groups)} texture2dgroup resources")


def extract_textures_from_archive(op, archive_path: str) -> None:
    """
    Extract texture images from the 3MF archive and create Blender images.

    Textures are extracted from paths defined in texture2d elements and loaded
    as Blender images. The images are packed into the blend file for portability.

    :param op: The Import3MF operator instance.
    :param archive_path: Path to the 3MF archive file.
    """
    if not op.resource_textures:
        return

    # Support both operator (op.import_materials) and ImportContext (op.options.import_materials)
    import_materials = getattr(op, "import_materials", None)
    if import_materials is None:
        import_materials = getattr(getattr(op, "options", None), "import_materials", "MATERIALS")
    if import_materials == "NONE":
        return

    try:
        with zipfile.ZipFile(archive_path, "r") as archive:
            archive_files = archive.namelist()

            for texture_id, texture in list(op.resource_textures.items()):
                tex_path = texture.path.lstrip("/")

                if tex_path not in archive_files:
                    warn(f"Texture file not found in archive: {tex_path}")
                    continue

                try:
                    texture_data = archive.read(tex_path)

                    image_name = os.path.basename(tex_path)
                    base_name, ext = os.path.splitext(image_name)
                    counter = 1
                    while image_name in bpy.data.images:
                        image_name = f"{base_name}_{counter}{ext}"
                        counter += 1

                    with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp:
                        tmp.write(texture_data)
                        tmp_path = tmp.name

                    try:
                        blender_image = bpy.data.images.load(tmp_path)
                        blender_image.name = image_name
                        blender_image.pack()

                        blender_image["3mf_path"] = texture.path
                        blender_image["3mf_tilestyleu"] = texture.tilestyleu
                        blender_image["3mf_tilestylev"] = texture.tilestylev
                        blender_image["3mf_filter"] = texture.filter

                        op.resource_textures[texture_id] = ResourceTexture(
                            path=texture.path,
                            contenttype=texture.contenttype,
                            tilestyleu=texture.tilestyleu,
                            tilestylev=texture.tilestylev,
                            filter=texture.filter,
                            blender_image=blender_image,
                        )

                        debug(f"Loaded texture {texture_id}: {image_name}")

                    finally:
                        try:
                            os.unlink(tmp_path)
                        except OSError:
                            pass

                except Exception as e:
                    warn(f"Failed to extract texture {texture_id} ({tex_path}): {e}")
                    continue

    except (zipfile.BadZipFile, IOError) as e:
        error(f"Failed to read textures from archive: {e}")


def get_or_create_textured_material(
    op, texture_group_id: str, texture_group: ResourceTextureGroup
) -> Optional[ResourceMaterial]:
    """
    Get or create a ResourceMaterial for a texture group.

    :param op: The Import3MF operator instance.
    :param texture_group_id: The ID of the texture2dgroup.
    :param texture_group: The ResourceTextureGroup data.
    :return: ResourceMaterial for this texture, or None if texture not available.
    """
    # Get the texture this group references
    texture = op.resource_textures.get(texture_group.texid)
    if not texture:
        return None

    # Generate a material name based on the texture
    material_name = f"Texture_{texture_group_id}"

    return ResourceMaterial(
        name=material_name,
        color=(1.0, 1.0, 1.0, 1.0),
        metallic=None,
        roughness=None,
        specular_color=None,
        glossiness=None,
        ior=None,
        attenuation=None,
        transmission=None,
        texture_id=texture_group_id,
        metallic_texid=None,
        roughness_texid=None,
        specular_texid=None,
        glossiness_texid=None,
        basecolor_texid=None,
    )


def blender_extension_for_tilestyle(tilestyleu: str, tilestylev: str) -> str:
    """Map 3MF ``tilestyleu``/``tilestylev`` to a Blender Image Texture extension.

    Per the Materials Extension, ``clamp`` uses the nearest edge pixel and
    ``none`` behaves like ``clamp`` for a single property, which is Blender's
    ``EXTEND``; ``mirror`` is ``MIRROR`` and ``wrap`` (the default) is
    ``REPEAT``.  Blender uses one mode for both axes, so a non-repeating
    axis takes precedence.
    """
    styles = {tilestyleu or "wrap", tilestylev or "wrap"}
    if styles & {"clamp", "none"}:
        return "EXTEND"
    if "mirror" in styles:
        return "MIRROR"
    return "REPEAT"


def setup_textured_material(
    op, material: bpy.types.Material, texture: ResourceTexture
) -> None:
    """
    Set up a Blender material with an Image Texture node for 3MF texture support.

    Creates a node tree with Image Texture -> Principled BSDF connection.

    :param op: The Import3MF operator instance.
    :param material: The Blender material to configure.
    :param texture: The ResourceTexture containing the Blender image.
    """
    material.use_nodes = True
    nodes = material.node_tree.nodes
    links = material.node_tree.links

    nodes.clear()

    principled = nodes.new("ShaderNodeBsdfPrincipled")
    principled.location = (0, 0)

    output = nodes.new("ShaderNodeOutputMaterial")
    output.location = (300, 0)

    # Texture Coordinate node — use UV output because this function is only
    # called when a texture2dgroup exists, which means apply_uv_coordinates
    # will have created a UV layer on the mesh from the tex2coord data.
    tex_coord = nodes.new("ShaderNodeTexCoord")
    tex_coord.location = (-700, 0)

    tex_node = nodes.new("ShaderNodeTexImage")
    tex_node.location = (-300, 0)
    tex_node.image = texture.blender_image

    # Wire UV → Image Texture vector input (UV layer exists from tex2coords)
    links.new(tex_coord.outputs["UV"], tex_node.inputs["Vector"])

    # Set texture extension mode based on tilestyle
    tex_node.extension = blender_extension_for_tilestyle(texture.tilestyleu, texture.tilestylev)

    # Set interpolation based on filter
    if texture.filter == "nearest":
        tex_node.interpolation = "Closest"
    else:
        tex_node.interpolation = "Linear"

    links.new(tex_node.outputs["Color"], principled.inputs["Base Color"])
    links.new(principled.outputs["BSDF"], output.inputs["Surface"])

    material["3mf_texture_tilestyleu"] = texture.tilestyleu or "wrap"
    material["3mf_texture_tilestylev"] = texture.tilestylev or "wrap"
    material["3mf_texture_filter"] = texture.filter or "auto"
    material["3mf_texture_path"] = texture.path

    debug(f"Created textured material with image '{texture.blender_image.name}'")


def setup_multi_textured_material(
    op,
    material: bpy.types.Material,
    textures: list,
    blendmethods: Optional[str] = None,
) -> None:
    """
    Set up a Blender material with multiple Image Texture nodes mixed together.

    Used for multiproperties that reference multiple texture2dgroups.
    Creates: Texture Coordinate -> Mapping -> Image Textures -> Mix chain -> Principled BSDF

    Per 3MF Materials Extension spec, multiproperties blendmethods:
    - "mix" (default): Alpha blend / additive
    - "multiply": Multiplicative blend

    :param op: The Import3MF operator instance.
    :param material: The Blender material to configure.
    :param textures: List of ResourceTexture objects to layer.
    :param blendmethods: Space-separated blend methods from multiproperties (e.g., "mix multiply").
    """
    if not textures:
        return

    material.use_nodes = True
    nodes = material.node_tree.nodes
    links = material.node_tree.links
    nodes.clear()

    # Parse blend methods - one per texture layer transition
    # First layer has no blend method (it's the base), subsequent layers each have one
    blend_list = blendmethods.split() if blendmethods else []

    # Create output and principled BSDF
    output = nodes.new("ShaderNodeOutputMaterial")
    output.location = (300, 100)

    principled = nodes.new("ShaderNodeBsdfPrincipled")
    principled.location = (0, 100)
    links.new(principled.outputs["BSDF"], output.inputs["Surface"])

    # Create shared Texture Coordinate and Mapping nodes
    tex_coord = nodes.new("ShaderNodeTexCoord")
    tex_coord.location = (-1200, 100)

    mapping = nodes.new("ShaderNodeMapping")
    mapping.location = (-1000, 100)
    links.new(tex_coord.outputs["UV"], mapping.inputs["Vector"])

    # Create Image Texture nodes for each texture
    tex_nodes = []
    for i, texture in enumerate(textures):
        tex_node = nodes.new("ShaderNodeTexImage")
        tex_node.location = (-800, 150 - (i * 300))
        tex_node.image = texture.blender_image
        tex_node.interpolation = "Smart"

        # Set texture extension mode
        tex_node.extension = blender_extension_for_tilestyle(texture.tilestyleu, texture.tilestylev)

        # Set interpolation
        if texture.filter == "nearest":
            tex_node.interpolation = "Closest"

        # Connect Mapping to texture Vector input
        links.new(mapping.outputs["Vector"], tex_node.inputs["Vector"])
        tex_nodes.append(tex_node)

    if len(tex_nodes) == 1:
        # Single texture - connect directly
        links.new(tex_nodes[0].outputs["Color"], principled.inputs["Base Color"])
    else:
        # Multiple textures - chain Mix nodes
        # First pair: tex[0] and tex[1]
        previous_output = tex_nodes[0].outputs["Color"]

        for i in range(1, len(tex_nodes)):
            mix = nodes.new("ShaderNodeMix")
            mix.location = (-500 + (i - 1) * 200, 100)
            mix.data_type = 'RGBA'
            mix.clamp_factor = True
            mix.clamp_result = False
            mix.factor_mode = 'UNIFORM'
            mix.inputs[0].default_value = 1.0  # Factor

            # Determine blend type from blendmethods
            # blendmethods has one entry per layer after the first
            # So blend_list[0] applies between layer 0 and 1, etc.
            blend_idx = i - 1
            if blend_idx < len(blend_list):
                method = blend_list[blend_idx].lower()
                if method == "multiply":
                    mix.blend_type = 'MULTIPLY'
                else:
                    mix.blend_type = 'ADD'  # "mix" maps to ADD
            else:
                mix.blend_type = 'ADD'  # Default

            # Connect inputs: A = previous result, B = current texture
            links.new(previous_output, mix.inputs[6])       # A_Color
            links.new(tex_nodes[i].outputs["Color"], mix.inputs[7])  # B_Color

            previous_output = mix.outputs[2]  # Result_Color

        # Connect final Mix output to Principled BSDF Base Color
        links.new(previous_output, principled.inputs["Base Color"])

    # Store texture metadata for round-trip
    for i, texture in enumerate(textures):
        suffix = f"_{i}" if i > 0 else ""
        material[f"3mf_texture_path{suffix}"] = texture.path
        material[f"3mf_texture_tilestyleu{suffix}"] = texture.tilestyleu or "wrap"
        material[f"3mf_texture_tilestylev{suffix}"] = texture.tilestylev or "wrap"
        material[f"3mf_texture_filter{suffix}"] = texture.filter or "auto"

    tex_names = ", ".join(t.blender_image.name for t in textures if t.blender_image)
    debug(f"Created multi-textured material with {len(textures)} textures: {tex_names}")


# 3MF tile style -> Image Texture extension.  "none" shows nothing outside the
# texture (transparent), which only differs from "clamp" in upper layers.
_LAYER_EXTENSION = {"wrap": "REPEAT", "mirror": "MIRROR", "clamp": "EXTEND", "none": "CLIP"}


def setup_layered_material(op, material: bpy.types.Material, resource_material) -> None:
    """Build the node tree of a layered material (see :mod:`..layers`).

    Each layer becomes an RGB, Color Attribute or Image Texture node, and the
    layers are blended bottom to top with Mix nodes into the Principled BSDF's
    Base Color:

    - ``mix``: Mix (Mix), factor = the layer's alpha.
    - ``multiply``: Mix (Multiply), factor 1.

    The exporter recognises this pattern and writes it back as multiproperties.

    :param op: The import context.
    :param material: The Blender material to configure.
    :param resource_material: ResourceMaterial with ``layers`` set.
    """
    material.use_nodes = True
    nodes = material.node_tree.nodes
    links = material.node_tree.links
    nodes.clear()

    output = nodes.new("ShaderNodeOutputMaterial")
    principled = nodes.new("ShaderNodeBsdfPrincipled")
    layer_count = len(resource_material.layers)
    principled.location = (300 * layer_count, 0)
    output.location = (300 * layer_count + 300, 0)
    links.new(principled.outputs["BSDF"], output.inputs["Surface"])

    accumulated = None
    for position, (kind, value, data_name, blend) in enumerate(resource_material.layers):
        x = 300 * position - 600
        y = -320 * position
        alpha = None  # Socket or constant for the layer's opacity
        if kind in ("color", "material"):
            node = nodes.new("ShaderNodeRGB")
            node.outputs["Color"].default_value = tuple(value[:3]) + (1.0,)
            color = node.outputs["Color"]
            alpha = value[3] if len(value) > 3 else 1.0
            if kind == "material":
                # The base layer is a 3MF base material, not just a color: keep its name
                # so the exporter writes it back as a material.
                material["3mf_base_material"] = data_name
        elif kind == "attribute":
            node = nodes.new("ShaderNodeVertexColor")
            node.layer_name = data_name
            color, alpha = node.outputs["Color"], node.outputs["Alpha"]
        else:
            texture = op.resource_textures[value]
            uv_node = nodes.new("ShaderNodeUVMap")
            uv_node.uv_map = data_name
            uv_node.location = (x - 250, y - 120)
            node = nodes.new("ShaderNodeTexImage")
            node.image = texture.blender_image
            tile_style = texture.tilestyleu or "wrap"
            node.extension = _LAYER_EXTENSION.get(tile_style, "REPEAT")
            if position == 0 and node.extension == "CLIP":
                node.extension = "EXTEND"  # "none" acts like "clamp" in the base layer.
            node.interpolation = "Closest" if texture.filter == "nearest" else "Linear"
            links.new(uv_node.outputs["UV"], node.inputs["Vector"])
            color, alpha = node.outputs["Color"], node.outputs["Alpha"]
            if texture.path:
                texture.blender_image["3mf_texture_path"] = texture.path
        node.location = (x, y)
        node.label = f"3MF layer {position}"

        if accumulated is None:
            accumulated = color
            continue
        mix = nodes.new("ShaderNodeMix")
        mix.data_type = "RGBA"
        mix.location = (x + 250, y + 160)
        mix.label = f"Blend layer {position} ({blend or 'mix'})"
        if blend == "multiply":
            mix.blend_type = "MULTIPLY"
            mix.inputs[0].default_value = 1.0
        else:
            mix.blend_type = "MIX"
            if isinstance(alpha, float):
                mix.inputs[0].default_value = alpha
            else:
                links.new(alpha, mix.inputs[0])
        links.new(accumulated, mix.inputs[6])  # A
        links.new(color, mix.inputs[7])  # B
        accumulated = mix.outputs[2]

    links.new(accumulated, principled.inputs["Base Color"])
    material["3mf_layered"] = True
    debug(f"Created layered material '{material.name}' with {layer_count} layers")
