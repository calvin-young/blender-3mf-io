"""
Custom metadata: namespaced entries on parts and on the model survive export and import.
"""

import json
import zipfile

import bpy

from test_base import Blender3mfTestCase

from io_mesh_3mf.common.metadata import NAMESPACES_PROPERTY, get_metadata, set_metadata

NS = "urn:example:generator"


class TestCustomMetadata(Blender3mfTestCase):

    def _model_xml(self):
        with zipfile.ZipFile(self.temp_file) as archive:
            return archive.read("3D/3dmodel.model").decode("utf-8")

    def _export(self):
        result = bpy.ops.export_mesh.threemf(filepath=str(self.temp_file), use_orca_format="STANDARD")
        self.assertIn("FINISHED", result)

    def _reimport(self):
        self.clean_scene()
        result = bpy.ops.import_mesh.threemf(filepath=str(self.temp_file))
        self.assertIn("FINISHED", result)

    def test_part_parameters_round_trip(self):
        bpy.ops.mesh.primitive_cube_add()
        part = bpy.context.object
        params = {"wall_thickness_mm": 2.5, "suspension": "pin lock"}
        set_metadata(part, "gen:parameters", params, namespace=NS)
        set_metadata(bpy.context.scene, "gen:job", "job-001", namespace=NS)
        self._export()
        xml = self._model_xml()
        self.assertIn(f'xmlns:gen="{NS}"', xml)
        self.assertIn('name="gen:parameters" preserve="1"', xml)
        self.assertIn("<metadatagroup>", xml)

        self._reimport()
        part = next(obj for obj in bpy.context.scene.objects if obj.type == "MESH")
        self.assertEqual(get_metadata(part, "gen:parameters", parse_json=True), params)
        self.assertEqual(get_metadata(bpy.context.scene, "gen:job"), "job-001")
        self.assertEqual(bpy.context.scene[NAMESPACES_PROPERTY]["gen"], NS)

        # Re-export keeps the real namespace URI, not a stand-in.
        self._export()
        self.assertIn(f'xmlns:gen="{NS}"', self._model_xml())

    def test_undeclared_prefix_gets_stand_in(self):
        bpy.ops.mesh.primitive_cube_add()
        bpy.context.object["other:key"] = {"datatype": "xs:string", "preserve": False, "value": "x"}
        self._export()
        self.assertIn('xmlns:other="urn:3mf-metadata:other"', self._model_xml())

    def test_name_without_prefix_refused_on_objects(self):
        bpy.ops.mesh.primitive_cube_add()
        with self.assertRaises(ValueError):
            set_metadata(bpy.context.object, "parameters", "{}")

    def test_strip_removes_custom_metadata(self):
        bpy.ops.mesh.primitive_cube_add()
        set_metadata(bpy.context.object, "gen:parameters", {"a": 1}, namespace=NS)
        result = bpy.ops.export_mesh.threemf(filepath=str(self.temp_file), use_orca_format="STANDARD",
                                             strip_identifying_data=True)
        self.assertIn("FINISHED", result)
        self.assertNotIn("gen:parameters", self._model_xml())


class TestMetadataOnParts(Blender3mfTestCase):
    """Each part's metadata goes on its <object> once, also for linked duplicates."""

    def test_objects_carry_metadata_items_do_not(self):
        import re
        bpy.ops.mesh.primitive_cube_add()
        first = bpy.context.object
        set_metadata(first, "gen:parameters", {"t": 1}, namespace=NS)
        set_metadata(first, "gen:serial", "SN-1")
        bpy.ops.object.duplicate_move_linked()
        second = bpy.context.object
        second.location.x += 5
        set_metadata(second, "gen:serial", "SN-2")
        bpy.ops.mesh.primitive_cube_add(location=(0, 9, 0))
        set_metadata(bpy.context.object, "gen:parameters", {"t": 2})
        result = bpy.ops.export_mesh.threemf(filepath=str(self.temp_file), use_orca_format="STANDARD",
                                             export_location="KEEP")
        self.assertIn("FINISHED", result)
        with zipfile.ZipFile(self.temp_file) as archive:
            xml = archive.read("3D/3dmodel.model").decode("utf-8")
        build = xml[xml.index("<build"):]
        self.assertNotIn("metadatagroup", build)
        self.assertEqual(xml.count('name="gen:parameters"'), 3)
        self.assertEqual(len(re.findall(r'name="gen:serial"[^>]*>SN-[12]<', xml)), 2)

        self.clean_scene()
        bpy.ops.import_mesh.threemf(filepath=str(self.temp_file))
        serials = sorted(filter(None, (get_metadata(obj, "gen:serial") for obj in bpy.context.scene.objects)))
        self.assertEqual(serials, ["SN-1", "SN-2"])
