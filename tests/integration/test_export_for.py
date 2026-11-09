"""
Integration tests for the Export For option.
"""

import unittest
import zipfile
import xml.etree.ElementTree as ET

import bpy

from test_base import Blender3mfTestCase

NS = "{http://schemas.microsoft.com/3dmanufacturing/core/2015/02}"


class TestExportFor(Blender3mfTestCase):

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
        root = self.export(export_for="PART")
        self.assertEqual(len(root.findall(f"{NS}build/{NS}item")), 1)

    def test_powder_bed_keeps_parts_in_place(self):
        self.add_cubes()
        root = self.export(export_for="PRINTER", printer_type="PBF")
        items = root.findall(f"{NS}build/{NS}item")
        self.assertEqual(len(items), 2)
        x = sorted(float(item.get("transform").split()[9]) for item in items)
        # Not centered: the parts keep their positions, whatever the unit scale.
        self.assertAlmostEqual(x[1] / x[0], 80 / 50, places=4)

    def test_custom_uses_the_options_given(self):
        self.add_cubes()
        root = self.export(export_for="CUSTOM", export_mode="ONE_PART", use_orca_format="STANDARD")
        self.assertEqual(len(root.findall(f"{NS}build/{NS}item")), 1)
        root = self.export(export_for="CUSTOM", export_mode="PARTS", use_orca_format="STANDARD")
        self.assertEqual(len(root.findall(f"{NS}build/{NS}item")), 2)

    def test_scripts_default_to_custom(self):
        properties = bpy.ops.export_mesh.threemf.get_rna_type().properties
        self.assertEqual(properties["export_for"].default, "CUSTOM")


if __name__ == "__main__":
    unittest.main()
