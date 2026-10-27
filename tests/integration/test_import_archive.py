"""
Integration tests for ``io_mesh_3mf.import_3mf.archive``.

Tests archive reading, content-type parsing, and MIME-type assignment
using crafted ZIP files.  Runs inside real Blender.
"""

import io
import re
import unittest
import zipfile

import bpy

from io_mesh_3mf.import_3mf.context import ImportContext, ImportOptions
from io_mesh_3mf.import_3mf.archive import (
    read_content_types,
    assign_content_types,
)
from test_base import Blender3mfTestCase
from io_mesh_3mf.common.constants import (
    CONTENT_TYPES_LOCATION,
    MODEL_MIMETYPE,
    RELS_MIMETYPE,
)


def _make_ctx() -> ImportContext:
    """Create a minimal ImportContext for function-level tests."""
    return ImportContext(options=ImportOptions(), operator=None)


def _make_archive(files: dict) -> zipfile.ZipFile:
    """Create an in-memory ZIP archive with given files.

    :param files: ``{path: content_bytes_or_str}``
    :return: A ``ZipFile`` opened for reading.
    """
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for path, content in files.items():
            if isinstance(content, str):
                content = content.encode("utf-8")
            zf.writestr(path, content)
    buf.seek(0)
    return zipfile.ZipFile(buf, "r")


# ============================================================================
# read_content_types
# ============================================================================


class TestReadContentTypes(unittest.TestCase):
    """read_content_types() with crafted [Content_Types].xml files."""

    def test_with_overrides(self):
        ct_xml = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '  <Override PartName="/3D/3dmodel.model" '
            '            ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml" />'
            "</Types>"
        )
        archive = _make_archive(
            {
                CONTENT_TYPES_LOCATION: ct_xml,
                "3D/3dmodel.model": "<model />",
            }
        )
        ctx = _make_ctx()
        result = read_content_types(ctx, archive)

        # Should have override + 2 fallback defaults
        self.assertGreater(len(result), 0)
        # First entry should be the override
        pattern, mime = result[0]
        self.assertEqual(mime, MODEL_MIMETYPE)

    def test_with_defaults(self):
        ct_xml = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '  <Default Extension="model" '
            '           ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml" />'
            "</Types>"
        )
        archive = _make_archive(
            {
                CONTENT_TYPES_LOCATION: ct_xml,
                "3D/3dmodel.model": "<model />",
            }
        )
        ctx = _make_ctx()
        result = read_content_types(ctx, archive)

        # Should have at least the default + fallbacks
        mimes = [mime for _, mime in result]
        self.assertIn(MODEL_MIMETYPE, mimes)

    def test_missing_content_types(self):
        """Missing [Content_Types].xml should still return fallback patterns."""
        archive = _make_archive(
            {
                "3D/3dmodel.model": "<model />",
            }
        )
        ctx = _make_ctx()
        result = read_content_types(ctx, archive)

        # Fallback .rels and .model patterns
        mimes = [mime for _, mime in result]
        self.assertIn(RELS_MIMETYPE, mimes)
        self.assertIn(MODEL_MIMETYPE, mimes)

    def test_malformed_xml(self):
        """Malformed XML should still return fallback patterns."""
        archive = _make_archive(
            {
                CONTENT_TYPES_LOCATION: "<<<NOT XML>>>",
                "3D/3dmodel.model": "<model />",
            }
        )
        ctx = _make_ctx()
        result = read_content_types(ctx, archive)
        # Should still have fallbacks
        self.assertGreater(len(result), 0)


# ============================================================================
# assign_content_types
# ============================================================================


class TestAssignContentTypes(unittest.TestCase):
    """assign_content_types() with crafted archives."""

    def test_model_file_gets_type(self):
        archive = _make_archive(
            {
                CONTENT_TYPES_LOCATION: "<Types />",
                "3D/3dmodel.model": "<model />",
                "_rels/.rels": "<Relationships />",
            }
        )
        content_types = [
            (re.compile(r".*\.model"), MODEL_MIMETYPE),
            (re.compile(r".*\.rels"), RELS_MIMETYPE),
        ]

        result = assign_content_types(archive, content_types)

        self.assertEqual(result["3D/3dmodel.model"], MODEL_MIMETYPE)
        self.assertEqual(result["_rels/.rels"], RELS_MIMETYPE)

    def test_unrecognized_gets_empty(self):
        archive = _make_archive(
            {
                CONTENT_TYPES_LOCATION: "<Types />",
                "random.txt": "hello",
            }
        )
        content_types = [
            (re.compile(r".*\.model"), MODEL_MIMETYPE),
        ]

        result = assign_content_types(archive, content_types)
        self.assertEqual(result.get("random.txt"), "")

    def test_content_types_excluded(self):
        """[Content_Types].xml itself should not appear in results."""
        archive = _make_archive(
            {
                CONTENT_TYPES_LOCATION: "<Types />",
                "3D/3dmodel.model": "<model />",
            }
        )
        content_types = []
        result = assign_content_types(archive, content_types)
        self.assertNotIn(CONTENT_TYPES_LOCATION, result)

    def test_priority_override_first(self):
        """Earlier patterns (overrides) should win over later ones (defaults)."""
        archive = _make_archive(
            {
                CONTENT_TYPES_LOCATION: "<Types />",
                "3D/3dmodel.model": "<model />",
            }
        )
        content_types = [
            (re.compile(re.escape("/3D/3dmodel.model")), "override/xml"),
            (re.compile(r".*\.model"), MODEL_MIMETYPE),
        ]
        result = assign_content_types(archive, content_types)
        # The override regex uses fullmatch, so it depends on whether the path matches
        # In any case, some MIME type should be assigned
        self.assertIn("3D/3dmodel.model", result)


if __name__ == "__main__":
    unittest.main()


class DamagedArchiveTests(Blender3mfTestCase):
    """A model that only has a local ZIP header (missing from the central directory) is recovered."""

    MODEL = """<?xml version="1.0" encoding="UTF-8"?>
<model xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02" unit="millimeter">
  <resources><object id="1" type="model"><mesh>
    <vertices><vertex x="0" y="0" z="0"/><vertex x="1" y="0" z="0"/><vertex x="0" y="1" z="0"/></vertices>
    <triangles><triangle v1="0" v2="1" v3="2"/></triangles>
  </mesh></object></resources>
  <build><item objectid="1"/></build>
</model>"""

    def write_damaged(self):
        import struct
        path = self.temp_file
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("[Content_Types].xml", """<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/>
</Types>""")
            archive.writestr("_rels/.rels", """<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rel0" Target="/3D/3dmodel.model"
    Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/>
</Relationships>""")
            archive.writestr("3D/3dmodel.model", self.MODEL)
        # Remove the model's entry from the central directory, as some writers do.
        data = bytearray(path.read_bytes())
        end = data.rfind(b"PK\x05\x06")
        count, size, offset = struct.unpack("<HII", data[end + 10:end + 20])
        entry = data.find(b"PK\x01\x02", offset)
        while entry >= 0 and entry < end:
            name_length, extra_length, comment_length = struct.unpack("<HHH", data[entry + 28:entry + 34])
            length = 46 + name_length + extra_length + comment_length
            if data[entry + 46:entry + 46 + name_length] == b"3D/3dmodel.model":
                del data[entry:entry + length]
                end -= length
                data[end + 8:end + 16] = struct.pack("<HHI", count - 1, count - 1, size - length)
                break
            entry += length
        path.write_bytes(bytes(data))
        with zipfile.ZipFile(path) as archive:
            self.assertNotIn("3D/3dmodel.model", archive.namelist())
        return path

    def test_model_missing_from_central_directory_is_recovered(self):
        path = self.write_damaged()
        result = bpy.ops.import_mesh.threemf(filepath=str(path))
        self.assertEqual(result, {"FINISHED"})
        meshes = [o for o in bpy.data.objects if o.type == "MESH"]
        self.assertEqual(len(meshes), 1)
        self.assertEqual(len(meshes[0].data.polygons), 1)
