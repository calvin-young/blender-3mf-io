"""
Object IDs only need to be unique within their own model part.

A root object and an object in another ``.model`` may share an ID (Production Extension ``p:path``).
"""

import os
import tempfile
import unittest
import zipfile

try:
    import bpy
except ImportError:
    bpy = None

CORE = "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"
PROD = "http://schemas.microsoft.com/3dmanufacturing/production/2015/06"
TRIANGLE = (
    '<object id="1" type="model" name="{name}"><mesh><vertices>'
    '<vertex x="0" y="0" z="0"/><vertex x="{size}" y="0" z="0"/><vertex x="0" y="{size}" z="0"/>'
    '</vertices><triangles><triangle v1="0" v2="1" v3="2"/></triangles></mesh></object>'
)
SUB_MODEL = '<model unit="millimeter" xmlns="' + CORE + '"><resources>{object}</resources><build/></model>'
ROOT_MODEL = (
    '<model unit="millimeter" xmlns="' + CORE + '" xmlns:p="' + PROD + '" requiredextensions="p"><resources>'
    '<object id="1" type="model" name="wrapper"><components>'
    '<component objectid="1" p:path="/3D/Objects/a.model"/>'
    '<component objectid="1" p:path="/3D/Objects/b.model" transform="1 0 0 0 1 0 0 0 1 50 0 0"/>'
    '</components></object></resources><build><item objectid="1"/></build></model>'
)
CONTENT_TYPES = (
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
    '<Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/></Types>'
)
RELS = (
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Target="/3D/3dmodel.model" Id="rel0" '
    'Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/></Relationships>'
)


@unittest.skipIf(bpy is None, "Blender API not available")
class TestExternalModelIds(unittest.TestCase):
    def test_same_id_in_three_model_parts(self):
        from io_mesh_3mf import api

        bpy.ops.wm.read_factory_settings(use_empty=True)
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "ids.3mf")
            with zipfile.ZipFile(path, "w") as z:
                z.writestr("[Content_Types].xml", CONTENT_TYPES)
                z.writestr("_rels/.rels", RELS)
                z.writestr("3D/3dmodel.model", ROOT_MODEL)
                z.writestr("3D/Objects/a.model", SUB_MODEL.format(object=TRIANGLE.format(name="a", size=10)))
                z.writestr("3D/Objects/b.model", SUB_MODEL.format(object=TRIANGLE.format(name="b", size=20)))
            result = api.import_3mf(path)
            self.assertEqual(result.status, "FINISHED")

        meshes = [o for o in bpy.data.objects if o.type == "MESH"]
        self.assertEqual(sum(len(o.data.polygons) for o in meshes), 2)
        # The second part keeps its own size, not a copy of the first part's mesh, and its own placement.
        xs = sorted(round((o.matrix_world @ v.co).x * 1000, 3) for o in meshes for v in o.data.vertices)
        self.assertAlmostEqual(xs[-1], 70.0, places=2)
        self.assertAlmostEqual(xs[0], 0.0, places=2)


if __name__ == "__main__":
    unittest.main()
