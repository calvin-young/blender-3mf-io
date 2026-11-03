"""
Integration tests for exporting objects whose parent isn't part of the export.
"""

import os
import unittest
import xml.etree.ElementTree as ET
import zipfile

import bpy

from test_base import Blender3mfTestCase, get_temp_test_dir


CORE = "{http://schemas.microsoft.com/3dmanufacturing/core/2015/02}"


def build_item_count(path):
    with zipfile.ZipFile(path) as archive:
        root = ET.fromstring(archive.read("3D/3dmodel.model"))
    return len(list(root.iter(f"{CORE}item")))


class TestExportParents(Blender3mfTestCase):

    def path(self, name):
        return os.path.join(str(get_temp_test_dir()), name)

    def add_cube(self, parent=None):
        bpy.ops.mesh.primitive_cube_add()
        cube = bpy.context.active_object
        cube.parent = parent
        return cube

    def add_parent(self, kind):
        data = bpy.data.lattices.new(kind) if kind == "LATTICE" else None
        parent = bpy.data.objects.new(kind, data)
        bpy.context.collection.objects.link(parent)
        return parent

    def export(self, name, **options):
        path = self.path(name)
        bpy.ops.export_mesh.threemf(filepath=path, use_orca_format="STANDARD", **options)
        return path

    def test_selected_child_of_unselected_parent(self):
        """A selected mesh is exported whatever its unselected parent is."""
        for kind in ("LATTICE", "EMPTY"):
            cube = self.add_cube(self.add_parent(kind))
            bpy.ops.object.select_all(action="DESELECT")
            cube.select_set(True)
            path = self.export(f"child_of_{kind}.3mf", use_selection=True)
            self.assertEqual(build_item_count(path), 1, kind)
            self.clean_scene()

    def test_mesh_parented_to_lattice_in_a_scene_export(self):
        """Without a selection, a mesh parented to a lattice isn't lost either."""
        self.add_cube(self.add_parent("LATTICE"))
        self.assertEqual(build_item_count(self.export("scene_lattice.3mf")), 1)

    def test_visible_child_of_hidden_parent(self):
        parent = self.add_cube()
        self.add_cube(parent)
        parent.hide_set(True)
        self.assertEqual(build_item_count(self.export("hidden_parent.3mf")), 1)

    def test_exported_parent_writes_its_children(self):
        """A child isn't written twice when its parent, or a grandparent, is exported."""
        parent = self.add_parent("EMPTY")
        middle = self.add_parent("EMPTY")
        middle.parent = parent
        self.add_cube(middle)
        path = self.export("hierarchy.3mf")
        self.assertEqual(build_item_count(path), 1)

        grandchild = next(obj for obj in bpy.data.objects if obj.type == "MESH")
        bpy.ops.object.select_all(action="DESELECT")
        parent.select_set(True)
        grandchild.select_set(True)
        path = self.export("hierarchy_selection.3mf", use_selection=True)
        self.assertEqual(build_item_count(path), 1)


if __name__ == "__main__":
    unittest.main()
