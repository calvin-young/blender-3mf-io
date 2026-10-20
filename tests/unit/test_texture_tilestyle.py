"""
Unit tests for mapping 3MF texture tile styles to and from Blender.

Per the 3MF Materials Extension: ``wrap`` repeats, ``mirror`` reflects,
``clamp`` uses the nearest edge pixel, and ``none`` behaves like ``clamp``
for a single property (transparent outside the image in multiproperty
layers).
"""

import unittest

from io_mesh_3mf.import_3mf.materials.textures import blender_extension_for_tilestyle
from io_mesh_3mf.export_3mf.materials.textures import tilestyle_for_blender_extension


class TestImportTileStyle(unittest.TestCase):
    """blender_extension_for_tilestyle()"""

    def test_wrap_is_repeat(self):
        self.assertEqual(blender_extension_for_tilestyle("wrap", "wrap"), "REPEAT")

    def test_missing_defaults_to_repeat(self):
        self.assertEqual(blender_extension_for_tilestyle(None, None), "REPEAT")

    def test_mirror_is_mirror(self):
        self.assertEqual(blender_extension_for_tilestyle("mirror", "mirror"), "MIRROR")

    def test_clamp_is_extend(self):
        self.assertEqual(blender_extension_for_tilestyle("clamp", "clamp"), "EXTEND")

    def test_none_is_extend(self):
        self.assertEqual(blender_extension_for_tilestyle("none", "none"), "EXTEND")

    def test_mixed_axes_prefer_non_repeating(self):
        self.assertEqual(blender_extension_for_tilestyle("wrap", "clamp"), "EXTEND")
        self.assertEqual(blender_extension_for_tilestyle("mirror", "wrap"), "MIRROR")


class TestExportTileStyle(unittest.TestCase):
    """tilestyle_for_blender_extension()"""

    def test_mapping(self):
        self.assertEqual(tilestyle_for_blender_extension("REPEAT"), "wrap")
        self.assertEqual(tilestyle_for_blender_extension("MIRROR"), "mirror")
        self.assertEqual(tilestyle_for_blender_extension("EXTEND"), "clamp")
        self.assertEqual(tilestyle_for_blender_extension("CLIP"), "none")

    def test_round_trip(self):
        for style in ("wrap", "mirror", "clamp"):
            extension = blender_extension_for_tilestyle(style, style)
            self.assertEqual(tilestyle_for_blender_extension(extension), style)


if __name__ == "__main__":
    unittest.main()
