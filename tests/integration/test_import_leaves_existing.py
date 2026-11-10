"""
Importing must not change anything that was in the .blend file before:
objects, meshes, materials, images, collections, the scene's name, units and
custom properties. New things are added next to them.
"""

import glob
import hashlib
import os
import re
import unittest
import uuid
import zipfile

import bpy

from test_base import Blender3mfTestCase, get_temp_test_dir


def _value(v):
    """A comparable copy of a property value."""
    if hasattr(v, "to_dict"):
        return v.to_dict()
    if hasattr(v, "to_list"):
        return v.to_list()
    try:
        return [round(x, 6) for x in v]
    except TypeError:
        return round(v, 6) if isinstance(v, float) else v


def _props(idblock):
    return {k: _value(idblock[k]) for k in idblock.keys()}


def snapshot():
    """What's in the file now, keyed by datablock, for comparing before and after."""
    state = {"scene": {}, "objects": {}, "meshes": {}, "materials": {}, "images": {}, "collections": {}}
    scene = bpy.context.scene
    state["scene"] = {
        "name": scene.name, "props": _props(scene),
        "units": (scene.unit_settings.system, scene.unit_settings.length_unit,
                  round(scene.unit_settings.scale_length, 6)),
    }
    for obj in bpy.data.objects:
        state["objects"][obj.as_pointer()] = (
            obj.name, [round(x, 6) for row in obj.matrix_world for x in row],
            obj.data.name if obj.data else None, obj.parent.name if obj.parent else None,
            [s.material.name if s.material else None for s in obj.material_slots], _props(obj),
            sorted(c.name for c in obj.users_collection),
        )
    for mesh in bpy.data.meshes:
        coords = [0.0] * (len(mesh.vertices) * 3)
        mesh.vertices.foreach_get("co", coords)
        state["meshes"][mesh.as_pointer()] = (
            mesh.name, hashlib.sha1(repr([round(c, 5) for c in coords]).encode()).hexdigest(),
            len(mesh.polygons), [m.name if m else None for m in mesh.materials], _props(mesh),
        )
    for material in bpy.data.materials:
        nodes = []
        if material.node_tree:
            for node in material.node_tree.nodes:
                inputs = [(i.identifier, _value(i.default_value)) for i in node.inputs
                          if hasattr(i, "default_value")]
                nodes.append((node.name, node.type, inputs,
                              getattr(getattr(node, "image", None), "name", None)))
            links = sorted((l.from_node.name, l.from_socket.identifier, l.to_node.name, l.to_socket.identifier)
                           for l in material.node_tree.links)
        else:
            links = []
        state["materials"][material.as_pointer()] = (
            material.name, sorted(nodes), links, _props(material), _value(material.diffuse_color),
        )
    for image in bpy.data.images:
        pixels = hashlib.sha1(bytes(int(p * 255) for p in image.pixels[:])).hexdigest() if image.has_data else None
        state["images"][image.as_pointer()] = (
            image.name, image.filepath, pixels, _props(image), image.packed_file is not None,
        )
    for collection in bpy.data.collections:
        state["collections"][collection.as_pointer()] = (
            collection.name, sorted(o.name for o in collection.objects),
            sorted(c.name for c in collection.children),
        )
    return state


class TestImportLeavesExisting(Blender3mfTestCase):

    def new_path(self):
        return str(get_temp_test_dir() / f"existing_{uuid.uuid4().hex[:8]}.3mf")

    def image(self, name, color):
        image = bpy.data.images.new(name, 4, 4)
        image.pixels = list(color) * 16
        image.filepath_raw = str(get_temp_test_dir() / f"{uuid.uuid4().hex[:8]}_{name}")
        image.file_format = "PNG"
        image.save()
        return image

    def material(self, name, color=None, image=None, metallic=0.0):
        material = bpy.data.materials.new(name)
        material.use_nodes = True
        principled = material.node_tree.nodes["Principled BSDF"]
        principled.inputs["Metallic"].default_value = metallic
        if color:
            principled.inputs["Base Color"].default_value = color
        if image:
            node = material.node_tree.nodes.new("ShaderNodeTexImage")
            node.image = image
            material.node_tree.links.new(node.outputs["Color"], principled.inputs["Base Color"])
        return material

    def cube(self, name, material, location):
        bpy.ops.mesh.primitive_cube_add(location=location)
        obj = bpy.context.object
        obj.name = name
        obj.data.name = name
        obj.data.materials.append(material)
        return obj

    def export_source_files(self):
        """Files whose names collide with what the user has: Wood, grain.png, Red, Cube."""
        paths = []
        self.cube("Cube", self.material("Wood", image=self.image("grain.png", (0.5, 0.3, 0.1, 1))), (0, 0, 0))
        self.cube("Cube.001", self.material("Red", color=(1, 0, 0, 1)), (3, 0, 0))
        path = self.new_path()
        bpy.ops.export_mesh.threemf(filepath=path, use_orca_format="STANDARD", export_mode="PARTS")
        paths.append(path)
        # The same with model metadata the user's scene already has, and a title.
        titled = self.new_path()
        with zipfile.ZipFile(path) as source, zipfile.ZipFile(titled, "w") as target:
            for item in source.namelist():
                data = source.read(item)
                if item == "3D/3dmodel.model":
                    data = re.sub(rb'<metadata name="Title"[^<]*</metadata>',
                                  b'<metadata name="Title">Imported title</metadata>'
                                  b'<metadata name="Designer">Someone else</metadata>', data)
                target.writestr(item, data)
        paths.append(titled)
        self.clean_scene()
        return paths

    def build_users_file(self, texture_source):
        """A .blend with things named like the imported ones, but different, and one
        image identical to an imported one that the user has since changed."""
        with zipfile.ZipFile(texture_source) as archive:
            texture = next(n for n in archive.namelist() if n.lower().endswith(".png"))
            data = archive.read(texture)
        copy = get_temp_test_dir() / f"{uuid.uuid4().hex[:8]}_texture.png"
        copy.write_bytes(data)
        same = bpy.data.images.load(str(copy))
        same.name = os.path.basename(texture)
        same.pack()
        same["3mf_tilestyleu"] = "mirror"
        scene = bpy.context.scene
        scene.name = "My scene"
        scene["Designer"] = "Me"
        scene["my_setting"] = 42
        scene.unit_settings.length_unit = "MILLIMETERS"
        scene.unit_settings.scale_length = 0.001
        collection = bpy.data.collections.new("Parts")
        scene.collection.children.link(collection)
        wood = self.material("Wood", image=self.image("grain.png", (0.1, 0.6, 0.2, 1)), metallic=0.7)
        red = self.material("Red", color=(0, 1, 0, 1))  # A "Red" that's actually green.
        same_red = self.material("My red", color=(1, 0, 0, 1))  # Same color, other name.
        for name, material, x in (("Cube", wood, 0), ("Cube.001", red, 5), ("Other", same_red, 10)):
            obj = self.cube(name, material, (x, 5, 0))
            for c in obj.users_collection:
                c.objects.unlink(obj)
            collection.objects.link(obj)
            obj["note"] = name

    def test_imports_change_nothing_that_was_there(self):
        paths = self.export_source_files()
        paths += sorted(glob.glob(os.path.join(str(self.test_resources_dir), "3mf_consortium", "*.3mf")))
        self.build_users_file(paths[0])
        before = snapshot()
        for path in paths:
            for options in ({}, {"reuse_materials": False, "reuse_images": False},
                            {"join_components": True, "object_names": "FILE"}):
                self.assertIn("FINISHED", bpy.ops.import_mesh.threemf(filepath=path, **options))
        after = snapshot()
        self.assertEqual(after["scene"]["name"], before["scene"]["name"])
        self.assertEqual(after["scene"]["units"], before["scene"]["units"])
        for key, value in before["scene"]["props"].items():
            self.assertEqual(after["scene"]["props"].get(key), value, f"scene property {key}")
        for kind in ("objects", "meshes", "materials", "images", "collections"):
            for pointer, value in before[kind].items():
                self.assertIn(pointer, after[kind], f"{kind}: {value[0]} was removed")
                self.assertEqual(after[kind][pointer], value, f"{kind}: {value[0]} was changed")
        self.assertGreater(len(after["objects"]), len(before["objects"]))


if __name__ == "__main__":
    unittest.main()
