"""
Integration tests for Reuse Existing Materials.
"""

import unittest

import bpy

from test_base import Blender3mfTestCase


class TestReuseColorMatch(Blender3mfTestCase):

    def test_textured_material_is_not_reused_for_its_default_color(self):
        bpy.ops.mesh.primitive_cube_add()
        plain = bpy.data.materials.new("Grey")
        plain.use_nodes = True
        color = plain.node_tree.nodes["Principled BSDF"].inputs["Base Color"].default_value[:]
        bpy.context.object.data.materials.append(plain)
        bpy.ops.export_mesh.threemf(filepath=str(self.temp_file), use_orca_format="STANDARD")
        self.clean_scene()

        # A textured material whose unlinked Base Color happens to be the same.
        textured = bpy.data.materials.new("Textured")
        textured.use_nodes = True
        principled = textured.node_tree.nodes["Principled BSDF"]
        principled.inputs["Base Color"].default_value = color
        node = textured.node_tree.nodes.new("ShaderNodeTexImage")
        node.image = bpy.data.images.new("tex", 4, 4)
        textured.node_tree.links.new(node.outputs["Color"], principled.inputs["Base Color"])

        bpy.ops.import_mesh.threemf(filepath=str(self.temp_file), reuse_materials=True)
        imported = [o for o in bpy.data.objects if o.type == "MESH"][0]
        self.assertNotEqual(imported.material_slots[0].material, textured)


if __name__ == "__main__":
    unittest.main()
