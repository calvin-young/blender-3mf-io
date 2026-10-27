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
Fast parsing of the bulky parts of a 3MF model document.

ElementTree creates a Python object for every ``<vertex>``, ``<triangle>``,
``<tex2coord>`` and ``<multi>`` element, and the importer then reads their
attributes one element at a time.  For meshes with millions of triangles
that dominates the import time.

:func:`parse_model` cuts the contents of ``<vertices>``, ``<triangles>``,
``<texture2dgroup>`` and ``<multiproperties>`` out of the raw document before
ElementTree sees it, and converts each block to a numpy array with a handful
of C-level calls.  The rest of the document (resources, objects, components,
build items, metadata) is parsed by ElementTree as before.

Every block is first checked against a strict pattern of the usual syntax.
A block that doesn't match (other attribute order, namespace prefixes,
comments, paint attributes, entities, ...) is parsed with ElementTree and put
back into the element tree, so the element-by-element code handles it exactly
as it did before.  If anything else goes wrong, the whole document is parsed
with ElementTree.
"""

import re
import warnings
import xml.etree.ElementTree
from typing import IO, Dict, List, Optional, Tuple, TYPE_CHECKING

import numpy as np

from ..common import debug
from ..common.constants import MODEL_NAMESPACE, MATERIAL_NAMESPACE

if TYPE_CHECKING:
    from .context import ImportContext

__all__ = [
    "parse_model",
    "fast_array",
]

# Attribute put on the opening tag of a cut-out block so the element can be
# found again after parsing.  It's removed before anyone else sees the tree.
_MARKER = "threemf-fast-block"

_QUOTE = rb"[\"']"
_NUMBER = rb"\s*[-+.0-9eE]+\s*"
_INDEX = rb"\s*\d+\s*"

# Characters that make up the numbers.  Everything else becomes a space.
_KEEP = b"0123456789.-+eE"
_TO_SPACES = bytes(c if c in _KEEP else 0x20 for c in range(256))

_ROOT_TAG = re.compile(rb"<(?:[\w.-]+:)?model\b[^>]*>")
_NS_DECLARATION = re.compile(rb"""\sxmlns(?::([\w.-]+))?\s*=\s*(["'])(.*?)\2""")


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


def _namespace_prefixes(data: bytes) -> Tuple[Dict[bytes, bytes], bytes]:
    """Return ``{namespace: prefix}`` declared on the root element, and its raw declarations."""
    match = _ROOT_TAG.search(data, 0, 1 << 20)
    if match is None:
        return {}, b""
    prefixes = {}
    declarations = []
    for declaration in _NS_DECLARATION.finditer(match.group(0)):
        prefix = declaration.group(1)
        prefixes.setdefault(declaration.group(3), prefix + b":" if prefix else b"")
        declarations.append(declaration.group(0))
    return prefixes, b"".join(declarations)


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


def parse_model(ctx: "ImportContext", model_file: IO[bytes]) -> xml.etree.ElementTree.Element:
    """Parse a 3MF model document, converting bulky blocks with numpy.

    The converted arrays are stored in ``ctx.fast_arrays``, keyed by their
    (now empty) container element; see :func:`fast_array`.

    :param ctx: The import context.
    :param model_file: A readable binary stream of the model document.
    :return: The root ``<model>`` element.
    :raises xml.etree.ElementTree.ParseError: If the document is malformed.
    """
    data = model_file.read()
    try:
        root = _parse_split(ctx, data)
    except Exception as e:  # Anything unexpected: parse the whole document normally.
        debug(f"Fast model parsing not used: {e}")
        root = None
    if root is None:
        root = xml.etree.ElementTree.fromstring(data)
    return root


def _parse_split(ctx: "ImportContext", data: bytes) -> Optional[xml.etree.ElementTree.Element]:
    """Parse *data* with the bulky blocks cut out.  Returns None if not applicable."""
    prefixes, declarations = _namespace_prefixes(data)
    kinds = _block_kinds()
    allowed = {}  # Container element prefix + name -> kind
    core = prefixes.get(MODEL_NAMESPACE.encode())
    if core == b"":  # Core elements in the default namespace, as everyone writes them.
        for name in (b"vertices", b"triangles"):
            kinds[name].compile(b"")
            allowed[name] = kinds[name]
    material = prefixes.get(MATERIAL_NAMESPACE.encode())
    if material is not None:
        for name in (b"texture2dgroup", b"multiproperties"):
            kinds[name].compile(material)
            allowed[material + name] = kinds[name]
    if not allowed:
        return None

    container = re.compile(
        rb"<(" + b"|".join(re.escape(name) for name in allowed) + rb")(\s[^>]*)?>"
    )
    pieces: List[bytes] = []
    blocks: List[Tuple[_BlockKind, int, int]] = []
    position = 0
    for match in container.finditer(data):
        if match.start() < position:
            continue
        opening = match.group(0)
        if opening.endswith(b"/>"):
            continue  # Empty element, nothing to cut.
        name = match.group(1)
        end = data.find(b"</" + name + b">", match.end())
        if end < 0:
            return None
        pieces.append(data[position:match.start()])
        pieces.append(opening[:-1] + b' %s="%d">' % (_MARKER.encode(), len(blocks)))
        blocks.append((allowed[name], match.end(), end))
        position = end
    if not blocks:
        return None
    pieces.append(data[position:])

    root = xml.etree.ElementTree.fromstring(b"".join(pieces))
    del pieces

    elements = {}
    for element in root.iter():
        index = element.attrib.pop(_MARKER, None)
        if index is not None:
            elements[int(index)] = element
    if len(elements) != len(blocks):
        return None

    converted = rehydrated = 0
    for index, (kind, start, end) in enumerate(blocks):
        block = data[start:end]
        array = _convert(block, kind)
        if array is not None:
            ctx.fast_arrays[elements[index]] = array
            converted += 1
        else:
            # Not in canonical form: give the element its children back.
            wrapper = xml.etree.ElementTree.fromstring(
                b"<threemf-wrapper" + declarations + b">" + block + b"</threemf-wrapper>"
            )
            elements[index].extend(list(wrapper))
            rehydrated += 1
    debug(f"Fast model parsing: {converted} blocks converted, {rehydrated} parsed element by element")
    return root


def fast_array(ctx: "ImportContext", element: Optional[xml.etree.ElementTree.Element]) -> Optional[np.ndarray]:
    """Return the converted children of *element*, or None if it has real child elements.

    - ``<vertices>``: float array of shape (N, 3).
    - ``<triangles>``: int array of shape (N, 7) with columns v1 v2 v3 pid p1 p2 p3,
      -1 where an attribute is absent.
    - ``<texture2dgroup>``: float array of shape (N, 2), u and v.
    - ``<multiproperties>``: int array of shape (N, K), the ``pindices`` of each
      ``<multi>``, padded with -1.
    """
    if element is None:
        return None
    return ctx.fast_arrays.get(element)
