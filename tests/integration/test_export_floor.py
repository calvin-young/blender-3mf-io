"""
Integration tests for parts resting on the floor.

The floor (z = 0) is the only build-volume bound a part may touch, so a part
resting on it must never be written with a tiny negative z from round-off.
Runs inside real Blender (``--background --factory-startup``).
"""

import math
import random
import re
import unittest
import zipfile

import bpy
import numpy as np

from test_base import Blender3mfTestCase

from io_mesh_3mf.common.xml import format_transformation


def _matrix(text):
    """A 3MF transform attribute as a 4x3 array (rows: x, y, z axes, then translation)."""
    if not text:
        return np.vstack([np.eye(3), np.zeros(3)])
    return np.array(text.split(), dtype=float).reshape(4, 3)


def _lowest_z_per_item(model):
    """Each build item's lowest z, computed from the file as a reader would."""
    meshes = {}
    components = {}
    for object_id, body in re.findall(r'<object id="(\d+)"[^>]*>(.*?)</object>', model, re.S):
        vertices = re.findall(r'<vertex x="([^"]+)" y="([^"]+)" z="([^"]+)"', body)
        if vertices:
            meshes[object_id] = np.array(vertices, dtype=float)
        components[object_id] = re.findall(r'<component objectid="(\d+)"(?: transform="([^"]+)")?', body)

    def points(object_id, matrix):
        result = []
        if object_id in meshes:
            result.append(meshes[object_id] @ matrix[:3] + matrix[3])
        for child_id, transform in components.get(object_id, []):
            child = _matrix(transform)
            combined = np.vstack([child[:3] @ matrix[:3], child[3] @ matrix[:3] + matrix[3]])
            result.extend(points(child_id, combined))
        return result

    lowest = []
    for object_id, transform in re.findall(r'<item objectid="(\d+)"(?:[^>]*?transform="([^"]+)")?', model):
        lowest.append(min(p[:, 2].min() for p in points(object_id, _matrix(transform))))
    return lowest


class TestRestingOnFloor(Blender3mfTestCase):
    """Parts resting on the floor are written resting exactly on it."""

    def setUp(self):
        super().setUp()
        units = bpy.context.scene.unit_settings
        units.system = "METRIC"
        units.scale_length = 0.001
        units.length_unit = "MILLIMETERS"

    def _add_on_floor(self, count, seed=1):
        """Randomly rotated parts, each moved so its lowest vertex is at z = 0 in Blender."""
        rng = random.Random(seed)
        for i in range(count):
            bpy.ops.mesh.primitive_ico_sphere_add(
                radius=rng.uniform(3, 40), location=(i * 90, 0, 0),
                rotation=[rng.uniform(0, 2 * math.pi) for _ in range(3)])
            obj = bpy.context.object
            bpy.context.view_layer.update()
            obj.location.z -= min((obj.matrix_world @ v.co).z for v in obj.data.vertices)
        bpy.context.view_layer.update()

    def _export(self, path=None, **options):
        path = path or self.temp_file
        options.setdefault("use_orca_format", "STANDARD")
        options.setdefault("export_location", "KEEP")
        result = bpy.ops.export_mesh.threemf(filepath=str(path), **options)
        self.assertIn("FINISHED", result)
        with zipfile.ZipFile(path) as archive:
            return archive.read("3D/3dmodel.model").decode()

    def test_rotated_parts_rest_exactly_on_floor(self):
        """Round-off put parts up to ~1e-6 mm below the floor; now none is (some were already above it in Blender)."""
        self._add_on_floor(30)
        lowest = _lowest_z_per_item(self._export())
        self.assertEqual(len(lowest), 30)
        for z in lowest:
            self.assertGreaterEqual(z, 0.0)
            self.assertLess(z, 1e-5)

    def test_linked_copies_rest_on_floor(self):
        """Linked duplicates (one mesh, several copies) get the same treatment."""
        self._add_on_floor(1)
        original = bpy.context.object
        for i in range(1, 4):
            copy = original.copy()
            copy.location.x += i * 100
            bpy.context.collection.objects.link(copy)
        bpy.context.view_layer.update()
        model = self._export(use_components=True)
        self.assertIn("<component ", model)
        for z in _lowest_z_per_item(model):
            self.assertGreaterEqual(z, 0.0)
            self.assertLess(z, 1e-5)

    def test_parts_clearly_above_or_below_unchanged(self):
        """Only round-off is corrected: a part 1 mm below or above the floor stays where it is."""
        bpy.ops.mesh.primitive_cube_add(size=10, location=(0, 0, 4))
        bpy.ops.mesh.primitive_cube_add(size=10, location=(50, 0, 6))
        lowest = sorted(_lowest_z_per_item(self._export()))
        self.assertAlmostEqual(lowest[0], -1.0, places=9)
        self.assertAlmostEqual(lowest[1], 1.0, places=9)

    def test_no_negative_zero(self):
        """Values that round to zero are written as 0, never as -0."""
        bpy.ops.mesh.primitive_cube_add(size=10, location=(0, 0, 5), rotation=(0, 0, math.pi))
        model = self._export()
        self.assertNotRegex(model, r'="-0(\.0*)?"')
        self.assertNotRegex(model, r'[" ]-0\.0+[" ]')

    def test_round_trip_keeps_floor_at_zero(self):
        """Import, then export again: a part resting on the floor still rests exactly on it."""
        self._add_on_floor(5, seed=7)
        bpy.ops.mesh.primitive_cube_add(size=10, location=(0, 200, 5))
        self._export()
        bpy.ops.object.select_all(action="SELECT")
        bpy.ops.object.delete()
        bpy.ops.import_mesh.threemf(filepath=str(self.temp_file))
        second = self.temp_file.with_name("second.3mf")
        lowest = _lowest_z_per_item(self._export(path=second))
        self.assertEqual(len(lowest), 6)
        self.assertIn(0.0, lowest)  # The upright cube: exactly 0.
        for z in lowest:
            self.assertGreaterEqual(z, 0.0)
            self.assertLess(z, 1e-5)

    def test_prusa_parts_rest_on_floor(self):
        """PrusaSlicer exports put world positions in the vertices; the lowest one is exactly 0."""
        self._add_on_floor(10)
        model = self._export(use_orca_format="PAINT", mmu_slicer_format="PRUSA")
        zs = np.array(re.findall(r'<vertex x="[^"]+" y="[^"]+" z="([^"]+)"', model), dtype=float)
        self.assertEqual(zs.min(), 0.0)


class TestFormatTransformation(unittest.TestCase):
    """format_transformation() never writes -0."""

    def test_tiny_negative_cells_are_zero(self):
        import mathutils
        matrix = mathutils.Matrix.Translation((1e-12, -1e-11, -0.0))
        self.assertEqual(format_transformation(matrix).split()[9:], ["0.000000000"] * 3)
