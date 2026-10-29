"""
Integration tests for part names on export and import, and batch export.

Export names the parts (build items) after the file, the object or the mesh;
batch export writes one file per part.  Import names objects after the part
or the file, and can join a part's components into one object.
"""

import os
import unittest
import xml.etree.ElementTree as ET
import zipfile

import bpy

from test_base import Blender3mfTestCase, get_temp_test_dir


CORE = "{http://schemas.microsoft.com/3dmanufacturing/core/2015/02}"


def part_names(path):
    """Names of the objects placed by build items in a 3MF file, in build order."""
    with zipfile.ZipFile(path) as archive:
        root = ET.fromstring(archive.read("3D/3dmodel.model"))
    names = {obj.get("id"): obj.get("name") for obj in root.iter(f"{CORE}object")}
    return [names[item.get("objectid")] for item in root.iter(f"{CORE}item")]


class PartNameTestCase(Blender3mfTestCase):
    """Helpers to build a scene and export it."""

    def setUp(self):
        super().setUp()
        self.directory = str(get_temp_test_dir())

    def add_cube(self, name, location=(0, 0, 0), mesh_name=None):
        bpy.ops.mesh.primitive_cube_add(location=location)
        obj = bpy.context.active_object
        obj.name = name
        if mesh_name:
            obj.data.name = mesh_name
        return obj

    def path(self, name):
        return os.path.join(self.directory, name)

    def export(self, name, **options):
        path = self.path(name)
        bpy.ops.export_mesh.threemf(filepath=path, **options)
        return path


class TestExportPartNames(PartNameTestCase):
    """The Part Names option."""

    def test_automatic_single_part_takes_file_name(self):
        self.add_cube("suzanne")
        path = self.export("suzanne_export.3mf")
        self.assertEqual(part_names(path), ["suzanne_export"])

    def test_automatic_several_parts_keep_object_names(self):
        self.add_cube("suzanne")
        self.add_cube("cube", location=(3, 0, 0))
        path = self.export("bucket_x.3mf")
        self.assertEqual(sorted(part_names(path)), ["cube", "suzanne"])

    def test_file_name_with_several_parts_is_a_prefix(self):
        self.add_cube("suzanne")
        self.add_cube("cube", location=(3, 0, 0))
        path = self.export("bucket_x.3mf", part_names="FILE")
        self.assertEqual(sorted(part_names(path)), ["bucket_x_cube", "bucket_x_suzanne"])

    def test_object_name(self):
        self.add_cube("suzanne")
        path = self.export("suzanne_export.3mf", part_names="OBJECT")
        self.assertEqual(part_names(path), ["suzanne"])

    def test_mesh_name(self):
        self.add_cube("suzanne", mesh_name="Suzanne.001")
        path = self.export("suzanne_export.3mf", part_names="MESH")
        self.assertEqual(part_names(path), ["Suzanne.001"])

    def test_round_trip_with_defaults_keeps_the_name(self):
        """Exported as <part>.3mf with the defaults, a part imports with its own name."""
        self.add_cube("suzanne")
        path = self.export("suzanne.3mf")
        self.clean_scene()
        bpy.ops.import_mesh.threemf(filepath=path)
        self.assertEqual([obj.name for obj in bpy.data.objects], ["suzanne"])


class TestBatchExport(PartNameTestCase):
    """The Batch option."""

    def setUp(self):
        super().setUp()
        bpy.ops.object.empty_add(location=(0, 0, 0))
        self.assembly = bpy.context.active_object
        self.assembly.name = "assembly"
        for index, name in enumerate(("wheel", "axle")):
            child = self.add_cube(name, location=(index * 3, 5, 0))
            child.parent = self.assembly
        self.add_cube("Handle - Cap", location=(0, -5, 0))

    def test_keeps_hierarchies_together(self):
        self.export("batch_.3mf", use_batch=True)
        self.assertTrue(os.path.exists(self.path("batch_assembly.3mf")))
        self.assertTrue(os.path.exists(self.path("batch_Handle_-_Cap.3mf")))
        self.assertFalse(os.path.exists(self.path("batch_.3mf")))
        self.assertEqual(part_names(self.path("batch_assembly.3mf")), ["batch_assembly"])
        with zipfile.ZipFile(self.path("batch_assembly.3mf")) as archive:
            model = archive.read("3D/3dmodel.model").decode()
        self.assertEqual(model.count("<mesh>") + model.count("<mesh "), 2, "Both children in the file")

    def test_one_file_per_mesh(self):
        self.export("each_.3mf", use_batch=True, batch_keep_hierarchy=False)
        for name in ("wheel", "axle", "Handle_-_Cap"):
            path = self.path(f"each_{name}.3mf")
            self.assertTrue(os.path.exists(path), path)
            self.assertEqual(part_names(path), [f"each_{name}"])
        self.assertFalse(os.path.exists(self.path("each_assembly.3mf")))

        # A child exported alone holds only its own mesh.
        self.clean_scene()
        bpy.ops.import_mesh.threemf(filepath=self.path("each_axle.3mf"))
        meshes = [obj for obj in bpy.data.objects if obj.type == "MESH"]
        self.assertEqual([len(obj.data.vertices) for obj in meshes], [8])


class TestImportNames(PartNameTestCase):
    """The import options Object Names and Join Components."""

    def test_file_name_for_a_single_part(self):
        self.add_cube("suzanne")
        path = self.export("part_file.3mf", part_names="OBJECT")
        self.clean_scene()
        bpy.ops.import_mesh.threemf(filepath=path, object_names="FILE")
        self.assertEqual([obj.name for obj in bpy.data.objects], ["part_file"])

    def test_file_name_with_several_parts_is_a_prefix(self):
        self.add_cube("suzanne")
        self.add_cube("cube", location=(3, 0, 0))
        path = self.export("bucket_y.3mf")
        self.clean_scene()
        bpy.ops.import_mesh.threemf(filepath=path, object_names="FILE")
        self.assertEqual(sorted(obj.name for obj in bpy.data.objects), ["bucket_y_cube", "bucket_y_suzanne"])

    def test_join_components(self):
        bpy.ops.object.empty_add()
        assembly = bpy.context.active_object
        assembly.name = "assembly"
        for index, name in enumerate(("wheel", "axle")):
            self.add_cube(name, location=(index * 3, 0, 0)).parent = assembly
        path = self.export("assembly.3mf")

        self.clean_scene()
        bpy.ops.import_mesh.threemf(filepath=path)
        self.assertEqual(sorted(obj.name for obj in bpy.data.objects), ["axle", "wheel"],
                         "Without joining, each component is an object of its own")

        self.clean_scene()
        bpy.ops.import_mesh.threemf(filepath=path, join_components=True)
        objects = list(bpy.data.objects)
        self.assertEqual(len(objects), 1)
        self.assertEqual(objects[0].name, "assembly")
        self.assertEqual(len(objects[0].data.vertices), 16)


if __name__ == "__main__":
    unittest.main()
