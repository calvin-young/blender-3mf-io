# Blender add-on to import and export 3MF files.
# Copyright (C) 2025 Jack (modernization for Blender 4.2+)
# This add-on is free software; you can redistribute it and/or modify it under the terms of the GNU General Public
# License as published by the Free Software Foundation; either version 2 of the License, or (at your option) any later
# version.
# This add-on is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied
# warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.
# You should have received a copy of the GNU General Public License along with this program; if not, write to the Free
# Software Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA.

# <pep8 compliant>

"""
Conversion of the bulky blocks of a 3MF model to numpy arrays (see :mod:`.fast_parse`).

This module only uses the standard library and numpy, so worker processes can
import it on its own, without Blender.
"""

import re
import warnings
from typing import Dict, List, Optional, Tuple

import numpy as np

_QUOTE = rb"[\"']"
_NUMBER = rb"\s*[-+.0-9eE]+\s*"
_INDEX = rb"\s*\d+\s*"

# Characters that make up the numbers.  Everything else becomes a space.
_KEEP = b"0123456789.-+eE"
_TO_SPACES = bytes(c if c in _KEEP else 0x20 for c in range(256))


def _attr(name: bytes, value: bytes) -> bytes:
    return rb"\s+" + name + rb"=" + _QUOTE + value + _QUOTE


class _BlockKind:
    """How to recognise and convert the children of one kind of container element."""

    def __init__(self, container: bytes, child: bytes, attributes: bytes, names: Tuple[bytes, ...],
                 dtype, columns: Optional[int]):
        self.container = container
        self.child = child
        self.attributes = attributes  # Regex for the attributes in canonical order.
        self.names = names  # Attribute names, replaced by spaces before converting.
        self.dtype = dtype
        self.columns = columns  # Fixed number of values per child, or None if variable.

    def compile(self, prefix: bytes) -> None:
        """Compile the patterns for children written with the given namespace prefix."""
        child = re.escape(prefix + self.child)
        self.open_tag = b"<" + prefix + self.child
        self.close_tag = b"</" + prefix + self.child + b">"
        # Finds the first child that isn't in canonical form, if any.
        self.bad = re.compile(
            rb"<" + child + rb"\b(?!" + self.attributes + rb"\s*(?:/>|>\s*</" + child + rb"\s*>))"
        )


def _block_kinds() -> Dict[bytes, _BlockKind]:
    index = _INDEX
    triangle_attributes = (
        _attr(b"v1", index) + _attr(b"v2", index) + _attr(b"v3", index)
        + rb"(?:" + _attr(b"pid", index)
        + rb"(?:" + _attr(b"p1", index)
        + rb"(?:" + _attr(b"p2", index) + _attr(b"p3", index) + rb")?)?)?"
    )
    return {
        b"vertices": _BlockKind(
            b"vertices", b"vertex",
            _attr(b"x", _NUMBER) + _attr(b"y", _NUMBER) + _attr(b"z", _NUMBER),
            (b"x=", b"y=", b"z="), np.float64, 3,
        ),
        b"triangles": _BlockKind(
            b"triangles", b"triangle", triangle_attributes,
            (b"v1=", b"v2=", b"v3=", b"pid=", b"p1=", b"p2=", b"p3="), np.int64, None,
        ),
        b"texture2dgroup": _BlockKind(
            b"texture2dgroup", b"tex2coord",
            _attr(b"u", _NUMBER) + _attr(b"v", _NUMBER),
            (b"u=", b"v="), np.float64, 2,
        ),
        b"multiproperties": _BlockKind(
            b"multiproperties", b"multi",
            _attr(b"pindices", rb"[\d\s]*"),
            (b"pindices=",), np.int64, None,
        ),
    }


def _to_numbers(block: bytes, kind: _BlockKind, sentinel: bool) -> Optional[np.ndarray]:
    """Convert the children of a canonical block to a flat array of numbers."""
    text = block.replace(kind.open_tag, b" -1 " if sentinel else b" ")
    text = text.replace(kind.close_tag, b" ")
    for name in kind.names:
        text = text.replace(name, b" ")
    text = text.translate(_TO_SPACES)
    with warnings.catch_warnings():
        # numpy warns (instead of raising) when it can't read to the end.
        warnings.simplefilter("error")
        try:
            return np.fromstring(text, dtype=kind.dtype, sep=" ")
        except (ValueError, DeprecationWarning):
            return None


def _convert(block: bytes, kind: _BlockKind) -> Optional[np.ndarray]:
    """Convert one block, or return None if it isn't in canonical form."""
    if kind.bad.search(block) is not None:
        return None
    count = block.count(kind.open_tag)

    if kind.columns is not None:
        values = _to_numbers(block, kind, sentinel=False)
        if values is None or values.size != count * kind.columns:
            return None
        return values.reshape(count, kind.columns)

    # Variable number of values per child: a -1 sentinel marks each child.
    # Indices can't be negative in canonical form, so the sentinel is unambiguous.
    values = _to_numbers(block, kind, sentinel=True)
    if values is None:
        return None
    starts = np.flatnonzero(values == -1)
    if starts.size != count or (values.size and (count == 0 or starts[0] != 0)):
        return None  # Numbers outside the recognised children.
    lengths = np.diff(np.append(starts, values.size)) - 1
    width = int(lengths.max()) if count else 0
    if kind.child == b"triangle":
        # v1 v2 v3 [pid [p1 [p2 p3]]] -> columns v1 v2 v3 pid p1 p2 p3, -1 if absent.
        if count and not np.isin(lengths, (3, 4, 5, 7)).all():
            return None
        width = 7
    result = np.full((count, width), -1, dtype=np.int64)
    for length in np.unique(lengths):
        rows = lengths == length
        first = starts[rows] + 1
        for column in range(int(length)):
            result[rows, column] = values[first + column]
    return result


_KINDS: Dict[Tuple[bytes, bytes], _BlockKind] = {}


def block_kind(container: bytes, prefix: bytes) -> _BlockKind:
    """The compiled block kind for a container element written with *prefix* (cached)."""
    key = (container, prefix)
    if key not in _KINDS:
        kind = _block_kinds()[container]
        kind.compile(prefix)
        _KINDS[key] = kind
    return _KINDS[key]


def chunk_bounds(data: bytes, start: int, end: int, open_tag: bytes, size: int) -> List[Tuple[int, int]]:
    """Split ``data[start:end]`` into pieces of about *size* bytes, each starting at a child element."""
    if start == end:
        return [(start, end)]
    bounds = []
    while start < end:
        cut = data.find(open_tag, start + size, end) if start + size < end else -1
        if cut < 0:
            cut = end
        bounds.append((start, cut))
        start = cut
    return bounds


def convert_piece(container: bytes, prefix: bytes, piece: bytes) -> Optional[np.ndarray]:
    """Convert one piece of a block; the unit of work for worker processes."""
    return _convert(piece, block_kind(container, prefix))


def join_pieces(arrays: List[Optional[np.ndarray]], container: bytes) -> Optional[np.ndarray]:
    """Join converted pieces of one block, or None if any piece wasn't canonical."""
    if any(array is None for array in arrays):
        return None
    if len(arrays) == 1:
        return arrays[0]
    width = max(array.shape[1] for array in arrays)
    if container == b"multiproperties":  # Rows of different pieces may differ in width.
        arrays = [np.pad(a, ((0, 0), (0, width - a.shape[1])), constant_values=-1) for a in arrays]
    return np.concatenate(arrays)
