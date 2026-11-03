"""
Integration tests for exports with non-mesh children and exports that fail.
"""

import os
import unittest
from unittest import mock

import bpy

from io_mesh_3mf.export_3mf.standard import StandardExporter
from test_base import Blender3mfTestCase, get_temp_test_dir


class TestExportFailures(Blender3mfTestCase):

    def path(self, name):
        return os.path.join(str(get_temp_test_dir()), name)

    def test_mesh_with_lattice_child(self):
        """A mesh whose only child can't be exported (a lattice) still exports."""
        bpy.ops.mesh.primitive_cube_add()
        cube = bpy.context.active_object
        lattice = bpy.data.objects.new("Lattice", bpy.data.lattices.new("Lattice"))
        bpy.context.collection.objects.link(lattice)
        lattice.parent = cube

        path = self.path("lattice_child.3mf")
        result = bpy.ops.export_mesh.threemf(filepath=path, use_orca_format="STANDARD")
        self.assertIn("FINISHED", result)

        self.clean_scene()
        bpy.ops.import_mesh.threemf(filepath=path)
        meshes = [obj for obj in bpy.data.objects if obj.type == "MESH"]
        self.assertEqual([len(obj.data.vertices) for obj in meshes], [8])

    def test_failed_export_closes_and_removes_the_file(self):
        """An export that raises leaves no open or partial file behind."""
        bpy.ops.mesh.primitive_cube_add()
        path = self.path("failed.3mf")
        with mock.patch.object(StandardExporter, "write_objects", side_effect=TypeError("failure")):
            with self.assertRaises(RuntimeError):
                bpy.ops.export_mesh.threemf(filepath=path, use_orca_format="STANDARD")
        self.assertFalse(os.path.exists(path))

    def test_cancelled_export_removes_the_file(self):
        """Nothing selected with Selection Only: no empty file is left behind."""
        bpy.ops.mesh.primitive_cube_add()
        bpy.ops.object.select_all(action="DESELECT")
        path = self.path("cancelled.3mf")
        with self.assertRaises(RuntimeError):
            bpy.ops.export_mesh.threemf(filepath=path, use_selection=True)
        self.assertFalse(os.path.exists(path))


if __name__ == "__main__":
    unittest.main()
