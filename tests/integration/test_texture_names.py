"""
Integration tests for material and image names and reuse across imports.
"""

import re
import unittest
import uuid
import zipfile

import bpy

from test_base import Blender3mfTestCase, get_temp_test_dir


class TestTextureNames(Blender3mfTestCase):

    def new_path(self):
        return str(get_temp_test_dir() / f"tex_{uuid.uuid4().hex[:8]}.3mf")

    def make_image(self, name, color):
        image = bpy.data.images.new(name, 4, 4)
        image.pixels = list(color) * 16
        image.filepath_raw = str(get_temp_test_dir() / f"{uuid.uuid4().hex[:8]}_{name}")
        image.file_format = "PNG"
        image.save()
        return image

    def textured_cube(self, material_name, image):
        bpy.ops.mesh.primitive_cube_add()
        cube = bpy.context.object
        material = bpy.data.materials.new(material_name)
        material.use_nodes = True
        node = material.node_tree.nodes.new("ShaderNodeTexImage")
        node.image = image
        material.node_tree.links.new(
            node.outputs["Color"], material.node_tree.nodes["Principled BSDF"].inputs["Base Color"]
        )
        cube.data.materials.append(material)
        return cube

    def export_textured(self, material_name, image_name, color):
        """A file with one cube textured with a new image; leaves an empty scene."""
        path = self.new_path()
        self.textured_cube(material_name, self.make_image(image_name, color))
        result = bpy.ops.export_mesh.threemf(filepath=path, use_orca_format="STANDARD")
        self.assertIn("FINISHED", result)
        self.clean_scene()
        return path

    def import_file(self, path, **options):
        result = bpy.ops.import_mesh.threemf(filepath=path, **options)
        self.assertIn("FINISHED", result)

    def textured_materials(self):
        return sorted(
            m.name for m in bpy.data.materials
            if m.use_nodes and any(n.type == "TEX_IMAGE" for n in m.node_tree.nodes)
        )

    def test_material_name_survives_round_trip(self):
        path = self.export_textured("Wood", "grain.png", (0.5, 0.3, 0.1, 1.0))
        with zipfile.ZipFile(path) as archive:
            self.assertIn(b'b3mf:name="Wood"', archive.read("3D/3dmodel.model"))
        self.import_file(path)
        self.assertEqual(self.textured_materials(), ["Wood"])

    def test_without_the_name_the_image_names_the_material(self):
        path = self.export_textured("Wood", "grain.png", (0.5, 0.3, 0.1, 1.0))
        stripped = self.new_path()
        with zipfile.ZipFile(path) as source, zipfile.ZipFile(stripped, "w") as target:
            for item in source.namelist():
                data = source.read(item)
                if item == "3D/3dmodel.model":
                    data = re.sub(rb' b3mf:name="[^"]*"', b"", data)
                target.writestr(item, data)
        self.import_file(stripped)
        self.assertEqual(self.textured_materials(), ["grain"])

    def test_same_name_same_image_is_shared(self):
        first = self.export_textured("Wood", "grain.png", (0.5, 0.3, 0.1, 1.0))
        second = self.export_textured("Wood", "grain.png", (0.5, 0.3, 0.1, 1.0))
        self.import_file(first)
        self.import_file(second)
        self.assertEqual(self.textured_materials(), ["Wood"])
        self.assertEqual(len([i for i in bpy.data.images if i.name.startswith("grain")]), 1)

    def test_same_name_different_image_is_kept_apart(self):
        first = self.export_textured("Wood", "grain.png", (0.5, 0.3, 0.1, 1.0))
        second = self.export_textured("Wood", "grain.png", (0.1, 0.3, 0.5, 1.0))
        self.import_file(first)
        self.import_file(second)
        self.assertEqual(len(self.textured_materials()), 2)
        self.assertEqual(sorted(i.name for i in bpy.data.images if i.name.startswith("grain")),
                         ["grain.png", "grain_1.png"])

    def test_existing_material_with_extra_settings_wins(self):
        path = self.export_textured("Wood", "grain.png", (0.5, 0.3, 0.1, 1.0))
        self.import_file(path)
        existing = bpy.data.materials["Wood"]
        principled = existing.node_tree.nodes["Principled BSDF"]
        principled.inputs["Metallic"].default_value = 0.8
        self.import_file(path)
        self.assertEqual(self.textured_materials(), ["Wood"])
        for obj in bpy.data.objects:
            self.assertEqual(obj.material_slots[0].material, existing)

    def test_repeated_round_trips_add_no_copies(self):
        path = self.export_textured("Wood", "grain.png", (0.5, 0.3, 0.1, 1.0))
        for _ in range(3):
            self.import_file(path)
            for obj in list(bpy.data.objects):
                bpy.data.objects.remove(obj)
            path = self.new_path()
            bpy.ops.mesh.primitive_cube_add()
            bpy.context.object.data.materials.append(bpy.data.materials["Wood"])
            bpy.ops.export_mesh.threemf(filepath=path, use_orca_format="STANDARD")
            for obj in list(bpy.data.objects):
                bpy.data.objects.remove(obj)
        self.import_file(path)
        self.assertEqual(self.textured_materials(), ["Wood"])
        self.assertEqual(len([i for i in bpy.data.images if i.name.startswith("grain")]), 1)

    def test_reuse_images_off_adds_a_copy(self):
        path = self.export_textured("Wood", "grain.png", (0.5, 0.3, 0.1, 1.0))
        self.import_file(path, reuse_images=False, reuse_materials=False)
        self.import_file(path, reuse_images=False, reuse_materials=False)
        self.assertEqual(len([i for i in bpy.data.images if i.name.startswith("grain")]), 2)

    def test_same_plain_colors_share_one_material(self):
        path = self.new_path()
        bpy.ops.mesh.primitive_cube_add()
        material = bpy.data.materials.new("Red")
        material.use_nodes = True
        material.node_tree.nodes["Principled BSDF"].inputs["Base Color"].default_value = (1, 0, 0, 1)
        bpy.context.object.data.materials.append(material)
        bpy.ops.export_mesh.threemf(filepath=path, use_orca_format="STANDARD")
        self.clean_scene()
        for _ in range(3):
            self.import_file(path)
        self.assertEqual([m.name for m in bpy.data.materials if m.name.startswith("Red")], ["Red"])


if __name__ == "__main__":
    unittest.main()
