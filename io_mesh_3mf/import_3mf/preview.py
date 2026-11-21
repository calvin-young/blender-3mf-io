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
A quick look at a 3MF file before importing it: the size of its build, so the
import dialog can show it and flag a wrong unit.
"""

import os
import re
import zipfile
from typing import Dict, Optional, Tuple

import numpy as np

from ..common.constants import MODEL_DEFAULT_UNIT
from ..common.units import threemf_to_metre

__all__ = ["build_size"]

# Larger model documents take too long to scan while the dialog draws; their
# size is reported after the import instead.
MAX_PREVIEW_BYTES = 50 * 1024 * 1024

_OBJECT = re.compile(rb"<(?:\w+:)?object\b([^>]*)>(.*?)</(?:\w+:)?object>", re.S)
_ID = re.compile(rb'\bid="(\d+)"')
_VERTEX = re.compile(rb'<(?:\w+:)?vertex\s+x="([^"]+)"\s+y="([^"]+)"\s+z="([^"]+)"')
_COMPONENT = re.compile(rb'<(?:\w+:)?component\b([^>]*)/?>')
_ITEM = re.compile(rb'<(?:\w+:)?item\b([^>]*)/?>')
_OBJECTID = re.compile(rb'\bobjectid="(\d+)"')
_TRANSFORM = re.compile(rb'\btransform="([^"]+)"')
_UNIT = re.compile(rb'<(?:\w+:)?model\b[^>]*\bunit="([^"]+)"')

_cache: Dict[Tuple[str, float], Optional[Tuple[Tuple[float, float, float], str]]] = {}


def _matrix(attributes: bytes) -> np.ndarray:
    """A transform attribute as a 4x3 array (rows: x, y, z axes, then translation)."""
    match = _TRANSFORM.search(attributes)
    if not match:
        return np.vstack([np.eye(3), np.zeros(3)])
    return np.array(match.group(1).split(), dtype=np.float64).reshape(4, 3)


def _corners(low: np.ndarray, high: np.ndarray) -> np.ndarray:
    return np.array([[x, y, z] for x in (low[0], high[0]) for y in (low[1], high[1]) for z in (low[2], high[2])])


def _read_size(path: str) -> Optional[Tuple[Tuple[float, float, float], str]]:
    with zipfile.ZipFile(path) as archive:
        names = [n for n in archive.namelist() if n.lower().endswith(".model")]
        name = "3D/3dmodel.model" if "3D/3dmodel.model" in names else (names[0] if names else None)
        if name is None or archive.getinfo(name).file_size > MAX_PREVIEW_BYTES:
            return None
        data = archive.read(name)

    boxes = {}  # Object ID: (low, high) in the object's own coordinates.
    composed = {}  # Object ID: [(child ID, transform)].
    for attributes, body in _OBJECT.findall(data):
        object_id = _ID.search(attributes)
        if not object_id:
            continue
        vertices = _VERTEX.findall(body)
        if vertices:
            coords = np.array(vertices, dtype=np.float64)
            boxes[object_id.group(1)] = (coords.min(axis=0), coords.max(axis=0))
        children = [(_OBJECTID.search(c).group(1), _matrix(c)) for c in _COMPONENT.findall(body)
                    if _OBJECTID.search(c)]
        if children:
            composed[object_id.group(1)] = children

    def points(object_id, matrix, depth=0):
        result = []
        if object_id in boxes:
            result.append(_corners(*boxes[object_id]) @ matrix[:3] + matrix[3])
        if depth < 16:
            for child_id, child in composed.get(object_id, []):
                combined = np.vstack([child[:3] @ matrix[:3], child[3] @ matrix[:3] + matrix[3]])
                result.extend(points(child_id, combined, depth + 1))
        return result

    placed = []
    for attributes in _ITEM.findall(data):
        object_id = _OBJECTID.search(attributes)
        if object_id:
            placed.extend(points(object_id.group(1), _matrix(attributes)))
    if not placed:
        return None
    every = np.vstack(placed)
    unit = _UNIT.search(data)
    unit = unit.group(1).decode() if unit else MODEL_DEFAULT_UNIT
    if unit not in threemf_to_metre:
        return None
    to_mm = threemf_to_metre[unit] / 0.001
    size = tuple(float(v) * to_mm for v in every.max(axis=0) - every.min(axis=0))
    return size, unit


def build_size(path: str) -> Optional[Tuple[Tuple[float, float, float], str]]:
    """The build's width, depth and height in mm and the file's unit, or None if unknown or too large to scan.

    Cached per file and modification time, since the dialog redraws often.
    """
    try:
        key = (path, os.path.getmtime(path))
    except OSError:
        return None
    if key not in _cache:
        try:
            _cache[key] = _read_size(path)
        except (OSError, zipfile.BadZipFile, ValueError, KeyError):
            _cache[key] = None
    return _cache[key]
