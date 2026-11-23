# Blender add-on to import and export 3MF files.
# Copyright (C) 2026 Calvin Young
# This add-on is free software: you can redistribute it and/or modify it under the terms of the GNU General
# Public License as published by the Free Software Foundation, either version 2 of the License, or (at your
# option) any later version.
# This add-on is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the
# implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License
# for more details.
# You should have received a copy of the GNU General Public License along with this program. If not, see
# <https://www.gnu.org/licenses/>.

"""
Link Identical Meshes: imported objects whose meshes are the same part, only
moved or turned, share one mesh afterwards, like Blender's linked duplicates.

Files often store each copy of a part as its own mesh, with the copy's
placement baked into the vertices. Two meshes count as the same part when they
have the same triangles (the same vertex indices in the same order), the same
attributes (materials, UVs, colors) and vertices that one rigid motion
(rotation and translation, no mirroring or scaling) maps onto each other
within a tiny tolerance. The copy then gets the first mesh and that motion
on its object, so it stays exactly where it was.
"""

from typing import Dict, List, Tuple

import bpy
import mathutils
import numpy as np

__all__ = ["link_identical_meshes"]

# Largest allowed vertex mismatch, relative to the part's size: files store
# about 9 significant digits, so real copies differ by far less than this.
RELATIVE_TOLERANCE = 1e-5


def _vertices(mesh: bpy.types.Mesh) -> np.ndarray:
    coords = np.empty(len(mesh.vertices) * 3, dtype=np.float64)
    mesh.vertices.foreach_get("co", coords)
    return coords.reshape(-1, 3)


def _corners(mesh: bpy.types.Mesh) -> np.ndarray:
    indices = np.empty(len(mesh.loops), dtype=np.int64)
    mesh.loops.foreach_get("vertex_index", indices)
    return indices


def _attributes_equal(a: bpy.types.Mesh, b: bpy.types.Mesh) -> bool:
    """Same materials and the same values in every attribute but the positions."""
    if [slot for slot in a.materials] != [slot for slot in b.materials]:
        return False
    # Names starting with "." are Blender's own state, such as the selection.
    names_a = {attr.name for attr in a.attributes if attr.name != "position" and not attr.name.startswith(".")}
    names_b = {attr.name for attr in b.attributes if attr.name != "position" and not attr.name.startswith(".")}
    if names_a != names_b:
        return False
    for name in names_a:
        attr_a, attr_b = a.attributes[name], b.attributes[name]
        if attr_a.data_type != attr_b.data_type or attr_a.domain != attr_b.domain:
            return False
        if len(attr_a.data) != len(attr_b.data):
            return False
        if not len(attr_a.data):
            continue
        key = next((k for k in ("value", "color", "vector") if hasattr(attr_a.data[0], k)), None)
        if key is None:
            return False
        width = len(getattr(attr_a.data[0], key)) if hasattr(getattr(attr_a.data[0], key), "__len__") else 1
        if attr_a.data_type == "BOOLEAN":
            dtype = np.bool_
        elif attr_a.data_type in {"INT", "INT8", "INT32_2D"}:
            dtype = np.int32
        else:
            dtype = np.float32
        values_a = np.empty(len(attr_a.data) * width, dtype=dtype)
        values_b = np.empty(len(attr_b.data) * width, dtype=dtype)
        attr_a.data.foreach_get(key, values_a)
        attr_b.data.foreach_get(key, values_b)
        if not np.array_equal(values_a, values_b):
            return False
    return True


def _rigid_fit(source: np.ndarray, target: np.ndarray):
    """The rotation and translation mapping *source* onto *target* (Kabsch), and the largest leftover distance."""
    centre_s, centre_t = source.mean(axis=0), target.mean(axis=0)
    covariance = (source - centre_s).T @ (target - centre_t)
    u, _, vt = np.linalg.svd(covariance)
    if np.linalg.det(vt.T @ u.T) < 0:  # A mirror image is a different part.
        return None, None, np.inf
    rotation = vt.T @ u.T
    translation = centre_t - rotation @ centre_s
    error = np.abs(source @ rotation.T + translation - target).max()
    return rotation, translation, error


def link_identical_meshes(objects: List[bpy.types.Object]) -> Tuple[int, int]:
    """Make copies of the same part among *objects* share one mesh, keeping every copy in place.

    Only meshes created for these objects are changed or removed.

    :return: The number of objects that now share another object's mesh, and the number of shared meshes.
    """
    candidates: Dict[Tuple[int, int, int], List[bpy.types.Object]] = {}
    for obj in objects:
        if obj.type != "MESH" or obj.data.users != 1 or obj.data.shape_keys is not None:
            continue
        mesh = obj.data
        candidates.setdefault((len(mesh.vertices), len(mesh.polygons), len(mesh.loops)), []).append(obj)

    linked = 0
    shared = set()
    for group in candidates.values():
        if len(group) < 2:
            continue
        # Each entry: (object whose mesh others share, its vertices, its corner indices, its size).
        originals = []
        for obj in group:
            mesh = obj.data
            vertices, corners = _vertices(mesh), _corners(mesh)
            # The distance from the centre to the farthest vertex: the same however the copy is turned.
            size = float(np.linalg.norm(vertices - vertices.mean(axis=0), axis=1).max()) if len(vertices) else 0.0
            match = None
            for original, original_vertices, original_corners, original_size in originals:
                if not np.array_equal(corners, original_corners) or abs(size - original_size) > 1e-3 * size:
                    continue
                if not _attributes_equal(original.data, mesh):
                    continue
                rotation, translation, error = _rigid_fit(original_vertices, vertices)
                if error <= RELATIVE_TOLERANCE * max(size, 1e-9):
                    match = (original, rotation, translation)
                    break
            if match is None:
                originals.append((obj, vertices, corners, size))
                continue
            original, rotation, translation = match
            motion = mathutils.Matrix.Identity(4)
            for row in range(3):
                for column in range(3):
                    motion[row][column] = rotation[row][column]
                motion[row][3] = translation[row]
            old_mesh = obj.data
            world = obj.matrix_world.copy()
            obj.data = original.data
            obj.matrix_world = world @ motion
            if old_mesh.users == 0:
                bpy.data.meshes.remove(old_mesh)
            linked += 1
            shared.add(original.data.name)
    return linked, len(shared)
