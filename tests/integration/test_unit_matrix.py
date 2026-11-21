"""
Integration tests: import and export sizes for every scene unit setup.

Each setup is a Unit System, length unit and Unit Scale, with the "Unitless
Scenes in Millimeters" preference on or off. Import reads a 20 mm cube from
files in millimetres, inches and metres; export writes a 20 unit cube. Every
case must keep the real size, and import must give scale 1 whenever one unit
is the file's unit.

Set ``THREEMF_UNIT_TABLE`` to a file path to also write the results as the
Markdown table in docs/UNITS.md.
"""

import os
import re
import zipfile
from unittest import mock

import bpy

from test_base import Blender3mfTestCase

# (label, system, length unit, Unit Scale, unitless preference)
SETUPS = [
    ("None, scale 1", "NONE", None, 1.0, True),
    ("None, scale 1, preference off", "NONE", None, 1.0, False),
    ("Metric, m, scale 1 (Blender's default)", "METRIC", "METERS", 1.0, True),
    ("Metric, mm, scale 1", "METRIC", "MILLIMETERS", 1.0, True),
    ("Metric, mm, scale 0.001", "METRIC", "MILLIMETERS", 0.001, True),
    ("Imperial, in, scale 1", "IMPERIAL", "INCHES", 1.0, True),
]
FILE_UNITS = {"millimeter": 1.0, "inch": 25.4, "meter": 1000.0}  # mm per file unit


def _cube_model(unit: str) -> str:
    """A 20 mm cube at x = 100 mm, written in *unit*."""
    per = FILE_UNITS[unit]
    half = 10 / per
    vertices = "".join(
        f'<vertex x="{x:.9g}" y="{y:.9g}" z="{z:.9g}"/>'
        for x in (-half, half) for y in (-half, half) for z in (0, 2 * half)
    )
    faces = [(0, 2, 1), (1, 2, 3), (4, 5, 6), (5, 7, 6), (0, 1, 4), (1, 5, 4),
             (2, 6, 3), (3, 6, 7), (0, 4, 2), (2, 4, 6), (1, 3, 5), (3, 7, 5)]
    triangles = "".join(f'<triangle v1="{a}" v2="{b}" v3="{c}"/>' for a, b, c in faces)
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<model xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02" unit="{unit}">'
        f'<resources><object id="1" type="model"><mesh><vertices>{vertices}</vertices>'
        f'<triangles>{triangles}</triangles></mesh></object></resources>'
        f'<build><item objectid="1" transform="1 0 0 0 1 0 0 0 1 {100 / per:.9g} 0 0"/></build></model>'
    )


def _write_3mf(path, model: str) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("[Content_Types].xml", (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/>'
            '</Types>'))
        archive.writestr("_rels/.rels", (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Target="/3D/3dmodel.model" Id="rel0" '
            'Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/></Relationships>'))
        archive.writestr("3D/3dmodel.model", model)


class TestUnitMatrix(Blender3mfTestCase):
    """Every scene unit setup keeps real sizes on import and export."""

    def _set_up_scene(self, system, length_unit, scale):
        units = bpy.context.scene.unit_settings
        units.system = system
        if length_unit:
            units.length_unit = length_unit
        units.scale_length = scale

    def _import(self, unit):
        path = self.temp_file.with_name(f"cube_{unit}.3mf")
        _write_3mf(path, _cube_model(unit))
        bpy.ops.import_mesh.threemf(filepath=str(path))
        obj = [o for o in bpy.context.scene.objects if o.type == "MESH"][0]
        return obj.dimensions.x, obj.scale.x, obj.location.x

    def _export(self):
        bpy.ops.mesh.primitive_cube_add(size=20, location=(100, 0, 10))
        result = bpy.ops.export_mesh.threemf(filepath=str(self.temp_file), use_orca_format="STANDARD",
                                             export_location="KEEP")
        self.assertIn("FINISHED", result)
        with zipfile.ZipFile(self.temp_file) as archive:
            model = archive.read("3D/3dmodel.model").decode()
        xs = [float(x) for x in re.findall(r'<vertex x="([^"]+)"', model)]
        transform = [float(v) for v in re.search(r'<item [^>]*transform="([^"]+)"', model).group(1).split()]
        return (max(xs) - min(xs)) * transform[0], transform[0]

    def test_matrix(self):
        rows = []
        for label, system, length_unit, scale, unitless_mm in SETUPS:
            with self.subTest(setup=label), mock.patch(
                    "io_mesh_3mf.common.units.unitless_is_millimetres", return_value=unitless_mm):
                if system == "NONE":
                    unit_mm = 1.0 if unitless_mm else 1000.0
                else:
                    unit_mm = 1000.0 * scale
                imports = []
                for file_unit, per in FILE_UNITS.items():
                    self.clean_scene()
                    self._set_up_scene(system, length_unit, scale)
                    width, obj_scale, x = self._import(file_unit)
                    self.assertAlmostEqual(width * unit_mm, 20.0, places=3)
                    self.assertAlmostEqual(x * unit_mm, 100.0, places=3)
                    if abs(per - unit_mm) < 1e-9:
                        self.assertAlmostEqual(obj_scale, 1.0, places=6)
                    imports.append(f"{width:.4g} u, scale {obj_scale:.4g}")
                self.clean_scene()
                self._set_up_scene(system, length_unit, scale)
                file_width, item_scale = self._export()
                self.assertAlmostEqual(file_width, 20.0 * unit_mm, places=3)
                self.assertEqual(item_scale, 1.0)
                rows.append(f"| {label} | 1 unit = {unit_mm:g} mm | {' | '.join(imports)} | {file_width:g} mm |")
        path = os.environ.get("THREEMF_UNIT_TABLE")
        if path:
            header = ("| Scene units | Means | Import 20 mm cube from a mm file | from an inch file "
                      "| from a metre file | Export 20 unit cube |\n|---|---|---|---|---|---|\n")
            with open(path, "w") as table:
                table.write(header + "\n".join(rows) + "\n")
