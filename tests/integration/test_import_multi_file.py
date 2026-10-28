"""
Integration test for importing several 3MF files at once (drag and drop).

Files from the same producer often reuse the same resource IDs, so state
cached while importing one file must not leak into the next.
"""

import os
import struct
import unittest
import zipfile
import zlib

import bpy

from test_base import Blender3mfTestCase, get_temp_test_dir


CONTENT_TYPES = """<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/>
  <Default Extension="png" ContentType="image/png"/>
</Types>
"""

RELS = """<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Target="/3D/3dmodel.model" Id="rel0"
    Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/>
</Relationships>
"""

# One mesh object wrapped in a single-component object, as some producers
# write every part. The {color} and {mesh} placeholders differ per file.
MODEL = """<?xml version="1.0" encoding="UTF-8"?>
<model unit="millimeter" xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02">
  <resources>
    <basematerials id="1">
      <base name="Part" displaycolor="{color}"/>
    </basematerials>
    <object id="2" type="model" pid="1" pindex="0">
      <mesh>
        <vertices>{mesh}
        </triangles>
      </mesh>
    </object>
    <object id="3" type="model">
      <components><component objectid="2"/></components>
    </object>
  </resources>
  <build>
    <item objectid="3"/>
  </build>
</model>
"""

TETRAHEDRON = """
          <vertex x="0" y="0" z="0"/><vertex x="1" y="0" z="0"/>
          <vertex x="0" y="1" z="0"/><vertex x="0" y="0" z="1"/>
        </vertices>
        <triangles>
          <triangle v1="0" v2="2" v3="1"/><triangle v1="0" v2="1" v3="3"/>
          <triangle v1="1" v2="2" v3="3"/><triangle v1="0" v2="3" v3="2"/>"""

PYRAMID = """
          <vertex x="0" y="0" z="0"/><vertex x="1" y="0" z="0"/>
          <vertex x="1" y="1" z="0"/><vertex x="0" y="1" z="0"/>
          <vertex x="0.5" y="0.5" z="1"/>
        </vertices>
        <triangles>
          <triangle v1="0" v2="2" v3="1"/><triangle v1="0" v2="3" v3="2"/>
          <triangle v1="0" v2="1" v3="4"/><triangle v1="1" v2="2" v3="4"/>
          <triangle v1="2" v2="3" v3="4"/><triangle v1="3" v2="0" v3="4"/>"""


# One textured triangle. Both files use the same texture ID and path but
# different image pixels.
TEXTURED_MODEL = """<?xml version="1.0" encoding="UTF-8"?>
<model unit="millimeter" xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02"
  xmlns:m="http://schemas.microsoft.com/3dmanufacturing/material/2015/02">
  <resources>
    <m:texture2d id="1" path="/3D/Textures/texture.png" contenttype="image/png"/>
    <m:texture2dgroup id="2" texid="1">
      <m:tex2coord u="0" v="0"/><m:tex2coord u="1" v="0"/><m:tex2coord u="0" v="1"/>
    </m:texture2dgroup>
    <object id="3" type="model">
      <mesh>
        <vertices>
          <vertex x="0" y="0" z="0"/><vertex x="1" y="0" z="0"/><vertex x="0" y="1" z="0"/>
        </vertices>
        <triangles>
          <triangle v1="0" v2="1" v3="2" pid="2" p1="0" p2="1" p3="2"/>
        </triangles>
      </mesh>
    </object>
  </resources>
  <build>
    <item objectid="3"/>
  </build>
</model>
"""


def _png(rgb):
    """Return the bytes of a 1x1 RGB PNG."""
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    header = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header)
            + chunk(b"IDAT", zlib.compress(b"\x00" + bytes(rgb))) + chunk(b"IEND", b""))


class TestMultiFileImport(Blender3mfTestCase):
    """Importing several files in one operator call keeps each file separate."""

    def setUp(self):
        super().setUp()
        self.directory = str(get_temp_test_dir())
        self.names = ["multi_a.3mf", "multi_b.3mf"]
        for name, color, mesh in zip(self.names, ("#FF0000", "#0000FF"), (TETRAHEDRON, PYRAMID)):
            with zipfile.ZipFile(os.path.join(self.directory, name), "w") as archive:
                archive.writestr("[Content_Types].xml", CONTENT_TYPES)
                archive.writestr("_rels/.rels", RELS)
                archive.writestr("3D/3dmodel.model", MODEL.format(color=color, mesh=mesh))

    def test_each_file_keeps_its_own_mesh(self):
        """Two files with the same component IDs import as two different meshes."""
        bpy.ops.import_mesh.threemf(
            directory=self.directory, files=[{"name": name} for name in self.names]
        )

        objects = [obj for obj in bpy.data.objects if obj.type == "MESH"]
        self.assertEqual(len(objects), 2)
        self.assertNotEqual(objects[0].data, objects[1].data, "Each file should get its own mesh")
        self.assertEqual(sorted(len(obj.data.vertices) for obj in objects), [4, 5])

    def test_each_file_keeps_its_own_texture(self):
        """Two files with the same texture IDs and paths import with their own images."""
        names = ["multi_red.3mf", "multi_blue.3mf"]
        for name, rgb in zip(names, ((255, 0, 0), (0, 0, 255))):
            with zipfile.ZipFile(os.path.join(self.directory, name), "w") as archive:
                archive.writestr("[Content_Types].xml", CONTENT_TYPES)
                archive.writestr("_rels/.rels", RELS)
                archive.writestr("3D/3dmodel.model", TEXTURED_MODEL)
                archive.writestr("3D/Textures/texture.png", _png(rgb))

        bpy.ops.import_mesh.threemf(directory=self.directory, files=[{"name": name} for name in names])

        objects = [obj for obj in bpy.data.objects if obj.type == "MESH"]
        self.assertEqual(len(objects), 2)
        colors = set()
        for obj in objects:
            images = [
                node.image
                for material in obj.data.materials
                for node in material.node_tree.nodes
                if node.type == "TEX_IMAGE" and node.image
            ]
            self.assertEqual(len(images), 1)
            colors.add(tuple(round(c) for c in images[0].pixels[:3]))
        self.assertEqual(colors, {(1, 0, 0), (0, 0, 1)}, "Each file should show its own texture")


if __name__ == "__main__":
    unittest.main()
