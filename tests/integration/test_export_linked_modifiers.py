"""
Integration tests for linked duplicates that don't look the same.

Objects sharing mesh data with different modifiers each export their own
result; objects that really are the same still share one component.
"""

import os
import unittest

import bpy

from test_base import Blender3mfTestCase, get_temp_test_dir


class TestLinkedModifiers(Blender3mfTestCase):

    def setUp(self):
        super().setUp()
        bpy.ops.mesh.primitive_cube_add()
        self.plain = bpy.context.active_object
        self.plain.name = "plain"

    def duplicate(self, name):
        obj = self.plain.copy()
        obj.name = name
        bpy.context.collection.objects.link(obj)
        return obj

    def round_trip(self, name, **options):
        path = os.path.join(str(get_temp_test_dir()), name)
        bpy.ops.export_mesh.threemf(filepath=path, use_orca_format="STANDARD", **options)
        self.clean_scene()
        bpy.ops.import_mesh.threemf(filepath=path)
        return {obj.name: obj for obj in bpy.data.objects if obj.type == "MESH"}

    def test_different_modifiers_export_different_meshes(self):
        modifier = self.duplicate("displaced").modifiers.new("Displace", "DISPLACE")
        modifier.strength = 5.0
        objects = self.round_trip("linked_modifier.3mf")
        self.assertEqual(sorted(objects), ["displaced", "plain"])

        def width(obj):
            return max(abs(v.co.x) for v in obj.data.vertices)

        self.assertNotEqual(objects["plain"].data, objects["displaced"].data)
        self.assertGreater(width(objects["displaced"]), width(objects["plain"]) * 1.5)

    def test_modifiers_not_applied_share_the_mesh(self):
        self.duplicate("displaced").modifiers.new("Displace", "DISPLACE").strength = 5.0
        objects = self.round_trip("linked_no_apply.3mf", use_mesh_modifiers=False)
        self.assertEqual(objects["plain"].data, objects["displaced"].data)

    def test_identical_objects_still_share_the_mesh(self):
        self.duplicate("copy")
        objects = self.round_trip("linked_same.3mf")
        self.assertEqual(objects["plain"].data, objects["copy"].data)

    def test_one_of_three_differs(self):
        """The two identical objects share; the one with a modifier gets its own mesh."""
        self.duplicate("copy")
        self.duplicate("displaced").modifiers.new("Displace", "DISPLACE").strength = 5.0
        objects = self.round_trip("linked_three.3mf")
        self.assertEqual(objects["plain"].data, objects["copy"].data)
        self.assertNotEqual(objects["plain"].data, objects["displaced"].data)

    def test_material_linked_to_object_gets_its_own_mesh(self):
        red = self.create_red_material()
        blue = self.create_blue_material()
        self.plain.data.materials.append(red)
        other = self.duplicate("blue")
        other.material_slots[0].link = "OBJECT"
        other.material_slots[0].material = blue
        objects = self.round_trip("linked_material.3mf")
        self.assertNotEqual(objects["plain"].data, objects["blue"].data)
        self.assertNotEqual(objects["plain"].data.materials[0].name, objects["blue"].data.materials[0].name)


if __name__ == "__main__":
    unittest.main()
