"""
Integration tests for layered properties: <multiproperties> with texture layers.

A partly transparent texture layered on top of a base texture or color is
imported as one Blender material blending the layers with Mix nodes, and
exported back as <multiproperties>.
"""

import struct
import unittest
import xml.etree.ElementTree as ET
import zipfile
import zlib

import bpy

from test_base import Blender3mfTestCase, get_temp_test_dir

NS_CORE = "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"
NS_MATERIAL = "http://schemas.microsoft.com/3dmanufacturing/material/2015/02"


def make_png(pixels):
    """Encode rows of (r, g, b, a) byte tuples as an RGBA PNG."""
    height, width = len(pixels), len(pixels[0])

    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    raw = b"".join(b"\x00" + bytes(value for pixel in row for value in pixel) for row in pixels)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


BASE_PNG = make_png([[(200, 30, 30, 255)] * 4] * 4)  # Opaque red
# Label: opaque black in the left half, fully transparent in the right half.
LABEL_PNG = make_png([[(0, 0, 0, 255)] * 2 + [(255, 255, 255, 0)] * 2] * 4)

MESH = """
    <object id="10" type="model" name="Layered part">
      <mesh>
        <vertices>
          <vertex x="0" y="0" z="0" /><vertex x="10" y="0" z="0" />
          <vertex x="10" y="10" z="0" /><vertex x="0" y="10" z="0" />
        </vertices>
        <triangles>
          <triangle v1="0" v2="1" v3="2" pid="{pid_a}" p1="0" p2="1" p3="2" />
          <triangle v1="0" v2="2" v3="3" pid="{pid_b}" p1="{b1}" p2="{b2}" p3="{b3}" />
        </triangles>
      </mesh>
    </object>"""


class LayeredTexturesTestCase(Blender3mfTestCase):
    """Helpers to build, import and export layered test files."""

    def write_3mf(self, resources, mesh, textures):
        path = get_temp_test_dir() / f"layered_{self._testMethodName}.3mf"
        model = f"""<?xml version="1.0" encoding="UTF-8"?>
<model xmlns="{NS_CORE}" xmlns:m="{NS_MATERIAL}" unit="millimeter">
  <resources>{resources}{mesh}
  </resources>
  <build><item objectid="10" /></build>
</model>"""
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("[Content_Types].xml", f"""<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml" />
  <Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml" />
  <Default Extension="png" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodeltexture" />
</Types>""")
            archive.writestr("_rels/.rels", """<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rel0" Target="/3D/3dmodel.model"
    Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel" />
</Relationships>""")
            archive.writestr("3D/3dmodel.model", model)
            for name, data in textures.items():
                archive.writestr(name, data)
        return path

    def import_file(self, path):
        self.assertEqual(bpy.ops.import_mesh.threemf(filepath=str(path)), {"FINISHED"})
        return [o for o in bpy.data.objects if o.type == "MESH"][0]

    def export_model(self):
        bpy.ops.object.select_all(action="SELECT")
        self.assertEqual(
            bpy.ops.export_mesh.threemf(filepath=str(self.temp_file), thumbnail_mode="NONE"), {"FINISHED"}
        )
        with zipfile.ZipFile(self.temp_file) as archive:
            return ET.fromstring(archive.read("3D/3dmodel.model")), archive.namelist()

    @staticmethod
    def layered_materials(obj):
        return [s.material for s in obj.material_slots if s.material and s.material.get("3mf_layered")]

    @staticmethod
    def resources(root, tag, namespace=NS_MATERIAL):
        return root.findall(f"./{{{NS_CORE}}}resources/{{{namespace}}}{tag}")


TEXTURE_RESOURCES = """
    <m:texture2d id="1" path="/3D/Textures/base.png" contenttype="image/png" />
    <m:texture2d id="2" path="/3D/Textures/DefaultPartIDTexture_B.png" contenttype="image/png"
      tilestyleu="none" tilestylev="none" />
    <m:texture2dgroup id="3" texid="1">
      <m:tex2coord u="0" v="0" /><m:tex2coord u="1" v="0" />
      <m:tex2coord u="1" v="1" /><m:tex2coord u="0" v="1" />
    </m:texture2dgroup>
    <m:texture2dgroup id="4" texid="2">
      <m:tex2coord u="0.25" v="0.25" /><m:tex2coord u="0.75" v="0.75" /><m:tex2coord u="0.25" v="0.75" />
    </m:texture2dgroup>
    <m:multiproperties id="5" pids="3 4">
      <m:multi pindices="0 0" /><m:multi pindices="2 1" /><m:multi pindices="3 2" />
    </m:multiproperties>"""

TEXTURES = {"3D/Textures/base.png": BASE_PNG, "3D/Textures/DefaultPartIDTexture_B.png": LABEL_PNG}


class TestLayeredTextureImport(LayeredTexturesTestCase):
    """Texture over texture, as used for placeholder labels on full color parts."""

    def setUp(self):
        super().setUp()
        mesh = MESH.format(pid_a=3, pid_b=5, b1=0, b2=1, b3=2)
        self.obj = self.import_file(self.write_3mf(TEXTURE_RESOURCES, mesh, TEXTURES))

    def test_layered_material_blends_by_alpha(self):
        materials = self.layered_materials(self.obj)
        self.assertEqual(len(materials), 1)
        nodes = materials[0].node_tree.nodes
        mix = [n for n in nodes if n.type == "MIX"]
        self.assertEqual(len(mix), 1)
        self.assertEqual(mix[0].blend_type, "MIX")
        factor = mix[0].inputs[0].links[0]
        self.assertEqual(factor.from_node.type, "TEX_IMAGE")
        self.assertEqual(factor.from_socket.name, "Alpha")
        self.assertEqual(factor.from_node.image.name, "DefaultPartIDTexture_B.png")
        # tilestyle "none" on an upper layer: transparent outside the texture
        self.assertEqual(factor.from_node.extension, "CLIP")

    def test_each_layer_has_its_own_uv_map(self):
        mesh = self.obj.data
        self.assertEqual([layer.name for layer in mesh.uv_layers], ["UVMap", "UVMap Layer 1"])
        # Second triangle uses multis 0, 1, 2: base coords 0, 2, 3 and label coords 0, 1, 2.
        polygon = mesh.polygons[1]
        base = [tuple(mesh.uv_layers["UVMap"].data[i].uv) for i in polygon.loop_indices]
        label = [tuple(mesh.uv_layers["UVMap Layer 1"].data[i].uv) for i in polygon.loop_indices]
        self.assertEqual(base, [(0.0, 0.0), (1.0, 1.0), (0.0, 1.0)])
        self.assertEqual(label, [(0.25, 0.25), (0.75, 0.75), (0.25, 0.75)])

    def test_plain_textured_triangle_unchanged(self):
        polygon = self.obj.data.polygons[0]
        material = self.obj.material_slots[polygon.material_index].material
        self.assertFalse(material.get("3mf_layered"))
        uvs = [tuple(self.obj.data.uv_layers["UVMap"].data[i].uv) for i in polygon.loop_indices]
        self.assertEqual(uvs, [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0)])

    def test_not_stored_as_passthrough(self):
        self.assertNotIn("3mf_multiproperties", bpy.context.scene.keys())
        self.assertNotIn("3mf_passthrough_pid", self.obj.data.keys())

    def test_export_writes_multiproperties(self):
        root, names = self.export_model()
        groups = self.resources(root, "multiproperties")
        self.assertEqual(len(groups), 1)
        pids = groups[0].get("pids").split()
        self.assertEqual(len(pids), 2)
        texture_groups = {g.get("id"): g for g in self.resources(root, "texture2dgroup")}
        self.assertTrue(all(pid in texture_groups for pid in pids))
        self.assertIsNone(groups[0].get("blendmethods"))  # "mix" is the default
        multis = groups[0].findall(f"{{{NS_MATERIAL}}}multi")
        self.assertEqual(len(multis), 3)
        # Texture names stay readable and keep the placeholder name.
        textures = {t.get("id"): t for t in self.resources(root, "texture2d")}
        label = textures[texture_groups[pids[1]].get("texid")]
        self.assertEqual(label.get("path"), "/3D/Textures/DefaultPartIDTexture_B.png")
        self.assertEqual(label.get("tilestyleu"), "none")
        self.assertIn("3D/Textures/DefaultPartIDTexture_B.png", names)
        # The layered triangle references the group with a multi per corner.
        triangles = root.findall(f".//{{{NS_CORE}}}triangle")
        layered = [t for t in triangles if t.get("pid") == groups[0].get("id")]
        self.assertEqual(len(layered), 1)
        self.assertEqual(sorted(layered[0].get(p) for p in ("p1", "p2", "p3")), ["0", "1", "2"])

    def test_roundtrip_keeps_layers(self):
        self.export_model()
        for obj in list(bpy.data.objects):
            bpy.data.objects.remove(obj)
        obj = self.import_file(self.temp_file)
        materials = self.layered_materials(obj)
        self.assertEqual(len(materials), 1)
        polygon = next(p for p in obj.data.polygons if obj.material_slots[p.material_index].material == materials[0])
        label = sorted(tuple(round(c, 4) for c in obj.data.uv_layers["UVMap Layer 1"].data[i].uv)
                       for i in polygon.loop_indices)
        self.assertEqual(label, [(0.25, 0.25), (0.25, 0.75), (0.75, 0.75)])


class TestLayeredColorAndMaterialBase(LayeredTexturesTestCase):
    """A texture over a colorgroup color, over a base material, and multiply blending."""

    def test_color_base(self):
        resources = TEXTURE_RESOURCES.replace(
            '<m:multiproperties id="5" pids="3 4">', '<m:colorgroup id="6"><m:color color="#336699" /></m:colorgroup>'
            '<m:multiproperties id="5" pids="6 4">').replace('pindices="2 1"', 'pindices="0 1"').replace(
            'pindices="3 2"', 'pindices="0 2"')
        obj = self.import_file(self.write_3mf(resources, MESH.format(pid_a=3, pid_b=5, b1=0, b2=1, b3=2), TEXTURES))
        material = self.layered_materials(obj)[0]
        rgb = [n for n in material.node_tree.nodes if n.type == "RGB"]
        self.assertEqual(len(rgb), 1)
        root, _ = self.export_model()
        group = self.resources(root, "multiproperties")[0]
        colors = {g.get("id"): g for g in self.resources(root, "colorgroup")}
        first = group.get("pids").split()[0]
        self.assertIn(first, colors)
        self.assertEqual(colors[first].find(f"{{{NS_MATERIAL}}}color").get("color"), "#336699")

    def test_material_base_stays_a_material(self):
        resources = '<basematerials id="7"><base name="PA12" displaycolor="#808080" /></basematerials>' + \
            TEXTURE_RESOURCES.replace('pids="3 4"', 'pids="7 4"').replace(
                'pindices="2 1"', 'pindices="0 1"').replace('pindices="3 2"', 'pindices="0 2"')
        obj = self.import_file(self.write_3mf(resources, MESH.format(pid_a=3, pid_b=5, b1=0, b2=1, b3=2), TEXTURES))
        self.assertEqual(self.layered_materials(obj)[0].get("3mf_base_material"), "PA12")
        root, _ = self.export_model()
        group = self.resources(root, "multiproperties")[0]
        first = group.get("pids").split()[0]
        bases = {g.get("id"): g for g in self.resources(root, "basematerials", NS_CORE)}
        self.assertIn(first, bases)
        self.assertEqual(bases[first].find(f"{{{NS_CORE}}}base").get("name"), "PA12")

    def test_multiply_blend(self):
        resources = TEXTURE_RESOURCES.replace('pids="3 4"', 'pids="3 4" blendmethods="multiply"')
        obj = self.import_file(self.write_3mf(resources, MESH.format(pid_a=3, pid_b=5, b1=0, b2=1, b3=2), TEXTURES))
        mix = [n for n in self.layered_materials(obj)[0].node_tree.nodes if n.type == "MIX"][0]
        self.assertEqual(mix.blend_type, "MULTIPLY")
        root, _ = self.export_model()
        self.assertEqual(self.resources(root, "multiproperties")[0].get("blendmethods"), "multiply")


class TestHandBuiltLayeredMaterial(LayeredTexturesTestCase):
    """A layered material built in Blender (not imported) exports as multiproperties."""

    def test_decal_over_texture(self):
        bpy.ops.mesh.primitive_plane_add()
        obj = bpy.context.object
        mesh = obj.data
        mesh.uv_layers.new(name="Decal")
        base_image = bpy.data.images.new("Body", 4, 4)
        decal_image = bpy.data.images.new("DefaultBuildIDTexture_W", 4, 4, alpha=True)
        material = bpy.data.materials.new("Body with date stamp")
        material.use_nodes = True
        nodes, links = material.node_tree.nodes, material.node_tree.links
        principled = nodes["Principled BSDF"]
        base = nodes.new("ShaderNodeTexImage")
        base.image = base_image
        decal = nodes.new("ShaderNodeTexImage")
        decal.image = decal_image
        uv = nodes.new("ShaderNodeUVMap")
        uv.uv_map = "Decal"
        links.new(uv.outputs["UV"], decal.inputs["Vector"])
        mix = nodes.new("ShaderNodeMix")
        mix.data_type = "RGBA"
        links.new(base.outputs["Color"], mix.inputs[6])
        links.new(decal.outputs["Color"], mix.inputs[7])
        links.new(decal.outputs["Alpha"], mix.inputs[0])
        links.new(mix.outputs[2], principled.inputs["Base Color"])
        mesh.materials.append(material)

        root, names = self.export_model()
        group = self.resources(root, "multiproperties")
        self.assertEqual(len(group), 1)
        paths = sorted(t.get("path") for t in self.resources(root, "texture2d"))
        self.assertEqual(paths, ["/3D/Textures/Body.png", "/3D/Textures/DefaultBuildIDTexture_W.png"])
        self.assertIn("3D/Textures/DefaultBuildIDTexture_W.png", names)


if __name__ == "__main__":
    unittest.main()


class TestNoDuplicateTextureGroups(LayeredTexturesTestCase):
    """Texture groups used by triangles are written once (from the material), unused ones are kept."""

    def test_used_group_written_once(self):
        resources = """
    <m:texture2d id="1" path="/3D/Textures/base.png" contenttype="image/png" />
    <m:texture2dgroup id="3" texid="1">
      <m:tex2coord u="0" v="0" /><m:tex2coord u="1" v="0" />
      <m:tex2coord u="1" v="1" /><m:tex2coord u="0" v="1" />
    </m:texture2dgroup>
    <m:texture2dgroup id="4" texid="1">
      <m:tex2coord u="0.5" v="0.5" />
    </m:texture2dgroup>"""
        mesh = MESH.format(pid_a=3, pid_b=3, b1=0, b2=2, b3=3)
        self.import_file(self.write_3mf(resources, mesh, {"3D/Textures/base.png": BASE_PNG}))
        root, _ = self.export_model()
        groups = self.resources(root, "texture2dgroup")
        sizes = sorted(len(g) for g in groups)
        self.assertEqual(sizes, [1, 4], "used group once (4 coords), unused group kept (1 coord)")


class TestTextureRelationships(LayeredTexturesTestCase):
    """Every texture the model references has a relationship, also after importing twice."""

    def test_all_textures_have_relationships(self):
        mesh = MESH.format(pid_a=3, pid_b=5, b1=0, b2=1, b3=2)
        path = self.write_3mf(TEXTURE_RESOURCES, mesh, TEXTURES)
        self.import_file(path)
        self.import_file(path)  # A second copy, with its own images
        root, names = self.export_model()
        with zipfile.ZipFile(self.temp_file) as archive:
            rels = archive.read("3D/_rels/3dmodel.model.rels").decode()
        for texture in self.resources(root, "texture2d"):
            self.assertIn(f'Target="{texture.get("path")}"', rels)
            self.assertIn(texture.get("path").lstrip("/"), names)
