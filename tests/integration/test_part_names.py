"""
Integration tests for part names on export and import, and batch export.

Export names the parts (build items) after the file, the object or the mesh;
batch export writes one file per part.  Import names objects after the part
or the file, and can join a part's components into one object.
"""

import os
import unittest
from unittest import mock
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
        self.add_cube("Part - One", location=(0, -5, 0))

    def test_keeps_hierarchies_together(self):
        self.export("batch_.3mf", export_mode="BATCH")
        self.assertTrue(os.path.exists(self.path("batch_assembly.3mf")))
        self.assertTrue(os.path.exists(self.path("batch_Part_-_One.3mf")))
        self.assertFalse(os.path.exists(self.path("batch_.3mf")))
        self.assertEqual(part_names(self.path("batch_assembly.3mf")), ["batch_assembly"])
        with zipfile.ZipFile(self.path("batch_assembly.3mf")) as archive:
            model = archive.read("3D/3dmodel.model").decode()
        self.assertEqual(model.count("<mesh>") + model.count("<mesh "), 2, "Both children in the file")

    def test_one_file_per_mesh(self):
        self.export("each_.3mf", export_mode="BATCH", batch_keep_hierarchy=False)
        for name in ("wheel", "axle", "Part_-_One"):
            path = self.path(f"each_{name}.3mf")
            self.assertTrue(os.path.exists(path), path)
            self.assertEqual(part_names(path), [f"each_{name}"])
        self.assertFalse(os.path.exists(self.path("each_assembly.3mf")))

        # A child exported alone holds only its own mesh.
        self.clean_scene()
        bpy.ops.import_mesh.threemf(filepath=self.path("each_axle.3mf"))
        meshes = [obj for obj in bpy.data.objects if obj.type == "MESH"]
        self.assertEqual([len(obj.data.vertices) for obj in meshes], [8])


class TestOnePart(PartNameTestCase):
    """Export As: One Part in One File."""

    def setUp(self):
        super().setUp()
        self.add_cube("frame")
        self.add_cube("bracket", location=(3, 0, 0))

    def export_one(self, name, **options):
        return self.export(name, export_mode="ONE_PART", use_orca_format="STANDARD", **options)

    def test_one_build_item_named_after_the_file(self):
        path = self.export_one("composite.3mf")
        self.assertEqual(part_names(path), ["composite"])

    def test_object_name_option(self):
        path = self.export_one("composite_object.3mf", part_names="OBJECT")
        self.assertIn(part_names(path)[0], ("frame", "bracket"))

    def test_components_keep_their_names_and_places(self):
        path = self.export_one("composite_names.3mf", export_location="KEEP", global_scale=0.001)
        with zipfile.ZipFile(path) as archive:
            root = ET.fromstring(archive.read("3D/3dmodel.model"))
        names = [obj.get("name") for obj in root.iter(f"{CORE}object")]
        self.assertEqual(sorted(names), ["bracket", "composite_names", "frame"])
        self.assertEqual(len(list(root.iter(f"{CORE}component"))), 2)

        self.clean_scene()
        bpy.ops.import_mesh.threemf(filepath=path, join_components=True)
        objects = list(bpy.data.objects)
        self.assertEqual([obj.name for obj in objects], ["composite_names"])
        self.assertEqual(len(objects[0].data.vertices), 16)

    def test_single_object_has_no_grouping_around_it(self):
        self.clean_scene()
        self.add_cube("only")
        path = self.export_one("single_one_part.3mf")
        self.assertEqual(part_names(path), ["single_one_part"])
        with zipfile.ZipFile(path) as archive:
            root = ET.fromstring(archive.read("3D/3dmodel.model"))
        self.assertEqual(len(list(root.iter(f"{CORE}component"))), 0)

    def test_auto_format_uses_standard_so_parts_are_grouped(self):
        material = self.create_red_material()
        for obj in bpy.data.objects:
            obj.data.materials.append(material)
        path = self.path("composite_auto.3mf")
        bpy.ops.export_mesh.threemf(filepath=path, export_mode="ONE_PART")
        self.assertEqual(part_names(path), ["composite_auto"])


class TestSelectionOnly(PartNameTestCase):
    """Selection Only exports what's selected, whatever the Include options say."""

    def test_selected_render_disabled_object_is_exported(self):
        cube = self.add_cube("disabled")
        cube.hide_render = True
        self.add_cube("other", location=(3, 0, 0))
        bpy.ops.object.select_all(action="DESELECT")
        cube.select_set(True)
        path = self.export("selected_disabled.3mf", use_selection=True, part_names="OBJECT")
        self.assertEqual(part_names(path), ["disabled"])


class TestBatchProgress(PartNameTestCase):

    def test_one_progress_card_for_the_batch(self):
        self.add_cube("a")
        self.add_cube("b", location=(3, 0, 0))
        from io_mesh_3mf.export_3mf import operator as export_operator
        with mock.patch.object(export_operator, "get_progress_mode", return_value="VIEWPORT"), \
                mock.patch.object(export_operator, "ProgressReporter") as reporter:
            self.export("card_.3mf", export_mode="BATCH")
        self.assertEqual(reporter.call_count, 1)
        reporter.return_value.show_done.assert_called_once()
        reporter.return_value.finish.assert_called_once()
        percents = [call.args[0] for call in reporter.return_value.update.call_args_list]
        self.assertTrue(percents and max(percents) <= 1.0)
        self.assertGreater(max(percents), 0.5, "The card should cover both files")


class TestFinishedExportNote(PartNameTestCase):
    """A non-manifold mesh doesn't make a finished export look like a failure."""

    def open_mesh(self):
        mesh = bpy.data.meshes.new("open")
        mesh.from_pydata([(0, 0, 0), (1, 0, 0), (0, 1, 0)], [], [(0, 1, 2)])
        obj = bpy.data.objects.new("open", mesh)
        bpy.context.collection.objects.link(obj)

    def test_note_on_the_full_bar(self):
        self.open_mesh()
        from io_mesh_3mf.export_3mf import operator as export_operator
        with mock.patch.object(export_operator, "get_progress_mode", return_value="VIEWPORT"), \
                mock.patch.object(export_operator, "ProgressReporter") as reporter:
            self.export("note.3mf")
        args = reporter.return_value.show_done.call_args.args
        self.assertIn("watertight", args[1])
        self.assertEqual(args[2], export_operator.DONE_NOTE_SECONDS)


class TestPreferencesMirrorOptions(unittest.TestCase):
    """Every export and import option has a default in the add-on preferences."""

    # Chosen for each export, not settings to default: a picture or a slicer profile file.
    NOT_MIRRORED = {
        "filter_glob", "files", "directory", "filepath", "show_advanced",
        "slicer_profile", "thumbnail_image", "thumbnail_image_path",
    }
    RENAMED = {
        "use_mesh_modifiers": "default_apply_modifiers",
        "use_orca_format": "default_multi_material_export",
    }

    def check(self, operator_class, path, renamed):
        import io_mesh_3mf
        with open(path, encoding="utf-8") as f:
            source = f.read()
        preferences = io_mesh_3mf.ThreeMFPreferences.__annotations__
        options = operator_class.__annotations__
        for option in options:
            if option in self.NOT_MIRRORED:
                continue
            preference = renamed.get(option, f"default_{option}")
            self.assertIn(preference, preferences, f"{option} has no preference {preference}")
            self.assertIn(f"self.{option} = prefs.preferences.{preference}", source,
                          f"{option} isn't set from the preferences when the dialog opens")

    def test_export_options(self):
        from io_mesh_3mf.export_3mf import operator as module
        self.check(module.Export3MF, module.__file__, self.RENAMED)

    def test_import_options(self):
        from io_mesh_3mf.import_3mf import operator as module
        self.check(module.Import3MF, module.__file__, {"global_scale": "default_import_global_scale"})


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
