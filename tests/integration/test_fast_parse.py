"""
Integration tests for fast model parsing (import_3mf.fast_parse).

The fast path must give exactly the same result as parsing element by
element, for the usual syntax, for variants that fall back, and when the
work is split into pieces and done in worker processes.
"""

import unittest
import zipfile

import bpy
import numpy as np

from test_base import Blender3mfTestCase, get_temp_test_dir

NS_CORE = "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"
NS_MATERIAL = "http://schemas.microsoft.com/3dmanufacturing/material/2015/02"


def grid_model(size=12, magics_style=False, comment=False, attribute_order="v1 v2 v3"):
    """A colored, textured grid: triangles with and without properties, like Netfabb and Magics write."""
    end = "></vertex>" if magics_style else "/>"
    vertices = [f'<vertex x="{x * 1.5}" y="{y * 0.5}" z="{(x * y) % 7 * 0.25}"{end}'
                for y in range(size + 1) for x in range(size + 1)]
    triangles = []
    for y in range(size):
        for x in range(size):
            a = y * (size + 1) + x
            b, c, d = a + 1, a + size + 2, a + size + 1
            names = attribute_order.split()
            values = {"v1": a, "v2": b, "v3": c}
            first = " ".join(f'{n}="{values[n]}"' for n in names)
            if (x + y) % 3 == 0:
                props = ' pid="2" p1="1"'
            elif (x + y) % 3 == 1:
                props = f' pid="3" p1="{a % 4}" p2="{b % 4}" p3="{c % 4}"'
            else:
                props = ""
            close = "></triangle>" if magics_style else "/>"
            triangles.append(f"<triangle {first}{props}{close}")
            triangles.append(f'<triangle v1="{a}" v2="{c}" v3="{d}"{close}')
    if comment:
        triangles.insert(5, "<!-- 1 2 3 -->")
    coords = "".join(f'<m:tex2coord u="{u}" v="{v}" />' for u, v in ((0, 0), (1, 0), (1, 1), (0, 1)))
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<model xmlns="{NS_CORE}" xmlns:m="{NS_MATERIAL}" unit="millimeter">
  <resources>
    <m:colorgroup id="2"><m:color color="#FF0000" /><m:color color="#00FF00" /></m:colorgroup>
    <m:texture2d id="4" path="/3D/Textures/t.png" contenttype="image/png" />
    <m:texture2dgroup id="3" texid="4">{coords}</m:texture2dgroup>
    <object id="1" type="model"><mesh>
      <vertices>{"".join(vertices)}</vertices>
      <triangles>{"".join(triangles)}</triangles>
    </mesh></object>
  </resources>
  <build><item objectid="1" /></build>
</model>"""


class FastParseTestCase(Blender3mfTestCase):

    def write(self, model):
        path = get_temp_test_dir() / f"fast_{self._testMethodName}.3mf"
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("[Content_Types].xml", """<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml" />
  <Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml" />
</Types>""")
            archive.writestr("_rels/.rels", """<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rel0" Target="/3D/3dmodel.model"
    Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel" />
</Relationships>""")
            archive.writestr("3D/3dmodel.model", model)
        return path

    def import_signature(self, path):
        for obj in list(bpy.data.objects):
            bpy.data.objects.remove(obj)
        for material in list(bpy.data.materials):
            bpy.data.materials.remove(material)
        self.assertEqual(bpy.ops.import_mesh.threemf(filepath=str(path)), {"FINISHED"})
        obj = [o for o in bpy.data.objects if o.type == "MESH"][0]
        mesh = obj.data
        co = np.empty(len(mesh.vertices) * 3, np.float32)
        mesh.vertices.foreach_get("co", co)
        corners = np.empty(len(mesh.loops), np.int32)
        mesh.loops.foreach_get("vertex_index", corners)
        slots = np.empty(len(mesh.polygons), np.int32)
        mesh.polygons.foreach_get("material_index", slots)
        names = [s.material.name for s in obj.material_slots]
        uvs = None
        if mesh.uv_layers:
            uvs = np.empty(len(mesh.loops) * 2, np.float32)
            mesh.uv_layers[0].data.foreach_get("uv", uvs)
        return co, corners, [names[i] for i in slots], uvs

    def assert_same_as_element_by_element(self, path):
        from io_mesh_3mf.import_3mf import fast_parse
        fast = self.import_signature(path)
        original = fast_parse._parse_split
        fast_parse._parse_split = lambda ctx, data: None  # Parse everything with ElementTree
        try:
            slow = self.import_signature(path)
        finally:
            fast_parse._parse_split = original
        np.testing.assert_array_equal(fast[0], slow[0])
        np.testing.assert_array_equal(fast[1], slow[1])
        self.assertEqual(fast[2], slow[2])
        if slow[3] is None:
            self.assertIsNone(fast[3])
        else:
            np.testing.assert_array_equal(fast[3], slow[3])
        return fast


class TestFastParse(FastParseTestCase):

    def test_netfabb_style(self):
        self.assert_same_as_element_by_element(self.write(grid_model()))

    def test_magics_style_closing_tags(self):
        self.assert_same_as_element_by_element(self.write(grid_model(magics_style=True)))

    def test_comment_falls_back(self):
        self.assert_same_as_element_by_element(self.write(grid_model(comment=True)))

    def test_other_attribute_order_falls_back(self):
        self.assert_same_as_element_by_element(self.write(grid_model(attribute_order="v2 v1 v3")))

    def test_pieces_and_worker_processes(self):
        from io_mesh_3mf.import_3mf import fast_parse
        saved = fast_parse._PARALLEL_THRESHOLD, fast_parse._PIECE_SIZE, fast_parse._worker_count
        fast_parse._PARALLEL_THRESHOLD, fast_parse._PIECE_SIZE = 0, 1000
        fast_parse._worker_count = lambda: 3
        try:
            self.assert_same_as_element_by_element(self.write(grid_model(size=30, magics_style=True)))
        finally:
            fast_parse._PARALLEL_THRESHOLD, fast_parse._PIECE_SIZE, fast_parse._worker_count = saved

    def test_chunk_bounds_split_at_elements(self):
        from io_mesh_3mf.import_3mf import threemf_fast_convert as convert
        data = b"head" + b'<triangle v1="1" v2="2" v3="3"/>' * 100 + b"tail"
        bounds = convert.chunk_bounds(data, 4, len(data) - 4, b"<triangle", 100)
        self.assertGreater(len(bounds), 5)
        self.assertEqual(bounds[0][0], 4)
        self.assertEqual(bounds[-1][1], len(data) - 4)
        for start, end in bounds:
            self.assertTrue(data[start:end].startswith(b"<triangle"))
        self.assertEqual(convert.chunk_bounds(data, 7, 7, b"<triangle", 100), [(7, 7)])


if __name__ == "__main__":
    unittest.main()
