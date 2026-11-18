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
