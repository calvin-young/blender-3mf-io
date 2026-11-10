"""
Integration tests for the Export As option.
"""

import unittest
import zipfile
import xml.etree.ElementTree as ET

import bpy

from test_base import Blender3mfTestCase

NS = "{http://schemas.microsoft.com/3dmanufacturing/core/2015/02}"


class TestExportAs(Blender3mfTestCase):

    def add_cubes(self):
        bpy.ops.mesh.primitive_cube_add(location=(50, 0, 10))
        bpy.ops.mesh.primitive_cube_add(location=(80, 0, 10))

    def export(self, **options):
        result = bpy.ops.export_mesh.threemf(filepath=str(self.temp_file), **options)
        self.assertIn("FINISHED", result)
        with zipfile.ZipFile(str(self.temp_file)) as archive:
            return ET.fromstring(archive.read("3D/3dmodel.model"))

    def test_part_is_one_build_item(self):
        self.add_cubes()
        root = self.export(export_as="PART_FILE")
        self.assertEqual(len(root.findall(f"{NS}build/{NS}item")), 1)

    def test_powder_bed_keeps_parts_in_place(self):
        self.add_cubes()
        root = self.export(export_as="PRINT_FILE", printer_type="PBF")
        items = root.findall(f"{NS}build/{NS}item")
        self.assertEqual(len(items), 2)
        x = sorted(float(item.get("transform").split()[9]) for item in items)
        # Not centered: the parts keep their positions, whatever the unit scale.
        self.assertAlmostEqual(x[1] / x[0], 80 / 50, places=4)

    def add_parts(self, parent_type):
        """Five parts, each a parent with three or four meshes under it."""
        for p in range(5):
            if parent_type == "EMPTY":
                parent = bpy.data.objects.new(f"part{p}", None)
                bpy.context.collection.objects.link(parent)
            else:
                bpy.ops.mesh.primitive_cube_add(location=(p * 5, 0, 0))
                parent = bpy.context.object
            for k in range(4 if parent_type == "EMPTY" else 3):
                bpy.ops.mesh.primitive_cube_add(location=(p * 5, k * 3 + 3, 0))
                bpy.context.object.parent = parent

    def test_print_file_parts_per_object_or_per_mesh(self):
        for parent_type in ("EMPTY", "MESH"):
            self.clean_scene()
            self.add_parts(parent_type)
            for print_parts, count in (("OBJECTS", 5), ("MESHES", 20)):
                root = self.export(export_as="PRINT_FILE", printer_type="PBF", print_parts=print_parts)
                items = root.findall(f"{NS}build/{NS}item")
                self.assertEqual(len(items), count, f"{parent_type} parents, {print_parts}")
                if print_parts == "MESHES":
                    objects = root.findall(f"{NS}resources/{NS}object")
                    self.assertEqual(len(objects), 20, "each mesh written once")

    def test_components_refer_to_objects_defined_before_them(self):
        """lib3mf-based readers refuse a component that refers to a later object."""
        for parent_type in ("EMPTY", "MESH"):
            self.clean_scene()
            self.add_parts(parent_type)
            root = self.export(export_as="PRINT_FILE", printer_type="PBF", print_parts="OBJECTS")
            seen = set()
            for obj in root.findall(f"{NS}resources/{NS}object"):
                for component in obj.iter(f"{NS}component"):
                    self.assertIn(component.get("objectid"), seen,
                                  f"{parent_type}: object {obj.get('id')} refers to a later object")
                seen.add(obj.get("id"))

    def test_custom_uses_the_options_given(self):
        self.add_cubes()
        root = self.export(export_as="CUSTOM", export_mode="ONE_PART", use_orca_format="STANDARD")
        self.assertEqual(len(root.findall(f"{NS}build/{NS}item")), 1)
        root = self.export(export_as="CUSTOM", export_mode="PARTS", use_orca_format="STANDARD")
        self.assertEqual(len(root.findall(f"{NS}build/{NS}item")), 2)

    def test_scripts_default_to_custom(self):
        properties = bpy.ops.export_mesh.threemf.get_rna_type().properties
        self.assertEqual(properties["export_as"].default, "CUSTOM")


if __name__ == "__main__":
    unittest.main()
