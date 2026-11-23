"""
Integration tests for Link Identical Meshes on import.

Copies of one part stored as separate meshes, each with its placement baked
into the vertices, share one mesh after import, and every copy stays exactly
where it was. Mirror images, scaled copies and different parts don't link.
"""

import math
import zipfile

import bpy
import numpy as np

from test_base import Blender3mfTestCase

# An irregular part (no symmetry), so a fit can't be confused by turning it.
PART = np.array([[0, 0, 0], [12, 0, 0], [0, 7, 0], [0, 0, 4], [9, 5, 3], [2, 6, 5]], dtype=float)
FACES = [(0, 2, 1), (0, 1, 3), (0, 3, 2), (1, 2, 4), (1, 4, 3), (2, 3, 5), (2, 5, 4), (3, 4, 5)]


def _rotation(axis, angle):
    axis = np.asarray(axis, dtype=float) / np.linalg.norm(axis)
    x, y, z = axis
    c, s = math.cos(angle), math.sin(angle)
    return np.array([[c + x * x * (1 - c), x * y * (1 - c) - z * s, x * z * (1 - c) + y * s],
                     [y * x * (1 - c) + z * s, c + y * y * (1 - c), y * z * (1 - c) - x * s],
                     [z * x * (1 - c) - y * s, z * y * (1 - c) + x * s, c + z * z * (1 - c)]])


def _object(oid, vertices):
    verts = "".join(f'<vertex x="{x:.9g}" y="{y:.9g}" z="{z:.9g}"/>' for x, y, z in vertices)
    tris = "".join(f'<triangle v1="{a}" v2="{b}" v3="{c}"/>' for a, b, c in FACES)
    return (f'<object id="{oid}" type="model"><mesh><vertices>{verts}</vertices>'
            f'<triangles>{tris}</triangles></mesh></object>')


def _write(path, parts):
    objects = "".join(_object(i + 1, v) for i, v in enumerate(parts))
    items = "".join(f'<item objectid="{i + 1}"/>' for i in range(len(parts)))
    model = ('<?xml version="1.0" encoding="UTF-8"?>'
             '<model xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02" unit="millimeter">'
             f'<resources>{objects}</resources><build>{items}</build></model>')
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("[Content_Types].xml", (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/>'
            '</Types>'))
        archive.writestr("_rels/.rels", (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Target="/3D/3dmodel.model" Id="rel0" '
            'Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/></Relationships>'))
        archive.writestr("3D/3dmodel.model", model)


class TestLinkIdenticalMeshes(Blender3mfTestCase):

    def _parts(self):
        placed = [
            PART + [100, 0, 0],
            PART @ _rotation((0, 0, 1), 0.7).T + [0, 80, 0],
            PART @ _rotation((1, 2, 3), 2.1).T + [-50, -40, 30],
        ]
        mirrored = PART * [-1, 1, 1] + [0, -90, 0]
        scaled = PART * 1.5 + [60, 60, 0]
        return placed, mirrored, scaled

    def _import(self, **options):
        placed, mirrored, scaled = self._parts()
        _write(self.temp_file, placed + [mirrored, scaled])
        result = bpy.ops.import_mesh.threemf(filepath=str(self.temp_file), import_location="KEEP", **options)
        self.assertEqual(result, {"FINISHED"})
        objects = [o for o in bpy.context.scene.objects if o.type == "MESH"]
        self.assertEqual(len(objects), 5)
        return objects, placed + [mirrored, scaled]

    def _world(self, obj):
        matrix = np.array(obj.matrix_world)
        coords = np.array([v.co for v in obj.data.vertices])
        return coords @ matrix[:3, :3].T + matrix[:3, 3]

    def test_copies_share_one_mesh_in_place(self):
        objects, expected = self._import()
        meshes = {o.data.name for o in objects}
        self.assertEqual(len(meshes), 3)  # The three copies share one; mirror and scaled keep their own.
        for obj in objects:
            world = self._world(obj)
            matches = [np.abs(world - e).max() for e in expected if e.shape == world.shape]
            self.assertLess(min(matches), 1e-3, f"{obj.name} moved")
        self.assertEqual(sum(1 for m in bpy.data.meshes if m.users == 0), 0)  # No leftover meshes.

    def test_off_keeps_every_mesh(self):
        objects, _ = self._import(link_identical_meshes=False)
        self.assertEqual(len({o.data.name for o in objects}), 5)
