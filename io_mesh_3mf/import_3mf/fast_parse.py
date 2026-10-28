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

import os
import re
import sys
import types
import xml.etree.ElementTree
from typing import IO, Callable, Dict, List, Optional, Tuple, TYPE_CHECKING

import numpy as np

from ..common import debug, warn
from . import threemf_fast_convert as convert
from ..common.constants import MODEL_NAMESPACE, MATERIAL_NAMESPACE

if TYPE_CHECKING:
    from .context import ImportContext

__all__ = [
    "parse_model",
    "fast_array",
]

# Models with less than this much mesh data are converted in Blender's own process.
_PARALLEL_THRESHOLD = 64 << 20
_PIECE_SIZE = 16 << 20
_MAX_WORKERS = 16

# Attribute put on the opening tag of a cut-out block so the element can be
# found again after parsing.  It's removed before anyone else sees the tree.
_MARKER = "threemf-fast-block"

# Progress reporting: the model document is read in pieces of this size, and
# the share of parse_model's progress each step gets (roughly its share of the
# time for a large model).
_READ_SIZE = 8 << 20
_READ_SHARE = 0.3
_CUT_SHARE = 0.1
# Pieces per worker process, so the pieces finish one after another and the
# conversion can report progress.
_PIECES_PER_WORKER = 4

_ROOT_TAG = re.compile(rb"<(?:[\w.-]+:)?model\b[^>]*>")
_NS_DECLARATION = re.compile(rb"""\sxmlns(?::([\w.-]+))?\s*=\s*(["'])(.*?)\2""")


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


def parse_model(
    ctx: "ImportContext",
    model_file: IO[bytes],
    progress: Optional[Callable[[float], None]] = None,
) -> xml.etree.ElementTree.Element:
    """Parse a 3MF model document, converting bulky blocks with numpy.

    The converted arrays are stored in ``ctx.fast_arrays``, keyed by their
    (now empty) container element; see :func:`fast_array`.

    :param ctx: The import context.
    :param model_file: A readable binary stream of the model document.
    :param progress: Optional callable, given the fraction done (0 to 1) as parsing goes on.
    :return: The root ``<model>`` element.
    :raises xml.etree.ElementTree.ParseError: If the document is malformed.
    """
    report = progress or (lambda fraction: None)
    data = _read(model_file, report)
    try:
        root = _parse_split(ctx, data, report)
    except Exception as e:  # Anything unexpected: parse the whole document normally.
        debug(f"Fast model parsing not used: {e}")
        root = None
    if root is None:
        root = _parse_whole(data)
    report(1.0)
    return root


def _read(model_file: IO[bytes], report: Callable[[float], None]) -> bytes:
    """Read the whole stream, reporting progress if its size is known."""
    # Streams from zipfile know their uncompressed size.
    size = getattr(model_file, "_orig_file_size", None)
    if not isinstance(size, int) or size <= _READ_SIZE:
        return model_file.read()
    chunks = []
    done = 0
    while True:
        chunk = model_file.read(_READ_SIZE)
        if not chunk:
            break
        chunks.append(chunk)
        done += len(chunk)
        report(_READ_SHARE * min(done / size, 1.0))
    return b"".join(chunks)


def _parse_whole(data: bytes) -> xml.etree.ElementTree.Element:
    """Parse a document with ElementTree, in pieces (expat takes less than 2 GB at a time)."""
    parser = xml.etree.ElementTree.XMLParser()
    view = memoryview(data)
    for start in range(0, len(data), 1 << 26):
        parser.feed(view[start:start + (1 << 26)])
    return parser.close()


def _parse_split(
    ctx: "ImportContext", data: bytes, report: Callable[[float], None]
) -> Optional[xml.etree.ElementTree.Element]:
    """Parse *data* with the bulky blocks cut out.  Returns None if not applicable."""
    prefixes, declarations = _namespace_prefixes(data)
    allowed = {}  # Container element prefix + name -> (container name, prefix)
    core = prefixes.get(MODEL_NAMESPACE.encode())
    if core == b"":  # Core elements in the default namespace, as everyone writes them.
        for name in (b"vertices", b"triangles"):
            allowed[name] = (name, b"")
    material = prefixes.get(MATERIAL_NAMESPACE.encode())
    if material is not None:
        for name in (b"texture2dgroup", b"multiproperties"):
            allowed[material + name] = (name, material)
    if not allowed:
        return None

    container = re.compile(
        rb"<(" + b"|".join(re.escape(name) for name in allowed) + rb")(\s[^>]*)?>"
    )
    pieces: List[bytes] = []
    blocks: List[Tuple[Tuple[bytes, bytes], int, int]] = []
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

    root = _parse_whole(b"".join(pieces))
    del pieces
    report(_READ_SHARE + _CUT_SHARE)

    elements = {}
    for element in root.iter():
        index = element.attrib.pop(_MARKER, None)
        if index is not None:
            elements[int(index)] = element
    if len(elements) != len(blocks):
        return None

    converted = rehydrated = 0
    for index, array in enumerate(_convert_blocks(data, blocks, report)):
        start, end = blocks[index][1:]
        if array is not None:
            ctx.fast_arrays[elements[index]] = array
            converted += 1
        else:
            # Not in canonical form: give the element its children back.
            wrapper = xml.etree.ElementTree.fromstring(
                b"<threemf-wrapper" + declarations + b">" + data[start:end] + b"</threemf-wrapper>"
            )
            elements[index].extend(list(wrapper))
            rehydrated += 1
    debug(f"Fast model parsing: {converted} blocks converted, {rehydrated} parsed element by element")
    return root


def _convert_blocks(data: bytes, blocks, report: Callable[[float], None]) -> List[Optional[np.ndarray]]:
    """Convert every block, in worker processes for large models.

    Large blocks are split into pieces at child elements, so one huge mesh is
    spread over the workers too.
    """
    total = sum(end - start for _, start, end in blocks)
    parallel = total >= _PARALLEL_THRESHOLD and _worker_count() > 1
    piece_size = _PIECE_SIZE
    if parallel:
        piece_size = max(1 << 20, min(_PIECE_SIZE, total // (_worker_count() * _PIECES_PER_WORKER)))
    tasks = []  # (block index, container, prefix, start, end)
    for index, ((container, prefix), start, end) in enumerate(blocks):
        open_tag = convert.block_kind(container, prefix).open_tag
        for piece_start, piece_end in convert.chunk_bounds(data, start, end, open_tag, piece_size):
            tasks.append((index, container, prefix, piece_start, piece_end))

    start_share = _READ_SHARE + _CUT_SHARE
    done = 0

    def piece_done(size: int) -> None:
        nonlocal done
        done += size
        if total:
            report(start_share + (1.0 - start_share) * done / total)

    results = None
    workers = min(len(tasks), _worker_count())
    if parallel and workers > 1:
        try:
            results = _convert_in_workers(data, tasks, workers, piece_done)
        except Exception as e:  # Anything going wrong with processes: do it here instead.
            warn(f"Converting the model in worker processes failed ({e}); continuing in Blender")
            results = None
    if results is None:
        done = 0
        results = []
        for _, container, prefix, start, end in tasks:
            results.append(convert.convert_piece(container, prefix, data[start:end]))
            piece_done(end - start)

    pieces: List[List] = [[] for _ in blocks]
    for (index, *_), result in zip(tasks, results):
        pieces[index].append(result)
    return [convert.join_pieces(arrays, blocks[index][0][0]) for index, arrays in enumerate(pieces)]


def _worker_count() -> int:
    try:
        cpus = len(os.sched_getaffinity(0))  # CPUs this process may use (Linux)
    except AttributeError:
        cpus = os.cpu_count() or 1
    return max(1, min(cpus - 1, _MAX_WORKERS))


def _convert_in_workers(
    data: bytes, tasks, workers: int, piece_done: Callable[[int], None]
) -> List[Optional[np.ndarray]]:
    """Convert pieces in separate processes (Python runs one thread at a time per process)."""
    import concurrent.futures
    import importlib.util
    import multiprocessing

    # Workers are fresh Python processes ("spawn" works the same on every
    # platform and is safe in a multithreaded program like Blender).  They
    # import the converter as a top-level module, without Blender or the add-on.
    directory = os.path.dirname(os.path.abspath(__file__))
    name = "threemf_fast_convert"
    module = sys.modules.get(name)
    if module is None:
        spec = importlib.util.spec_from_file_location(name, os.path.join(directory, name + ".py"))
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    # Workers get the parent's sys.path (environment variables may be ignored).
    sys.path.insert(0, directory)
    # Spawned workers re-import the parent's __main__ module, which in Blender
    # may be a script that imports bpy.  They don't need it: hide it meanwhile.
    main_module = sys.modules.get("__main__")
    sys.modules["__main__"] = types.ModuleType("__main__")
    try:
        context = multiprocessing.get_context("spawn")
        with concurrent.futures.ProcessPoolExecutor(workers, mp_context=context) as pool:
            # Largest first, so the workers finish at about the same time.
            order = sorted(range(len(tasks)), key=lambda i: tasks[i][4] - tasks[i][3], reverse=True)
            futures = {
                pool.submit(module.convert_piece, tasks[i][1], tasks[i][2], data[tasks[i][3]:tasks[i][4]]): i
                for i in order
            }
            results = [None] * len(tasks)
            for future in concurrent.futures.as_completed(futures):
                i = futures[future]
                results[i] = future.result()
                piece_done(tasks[i][4] - tasks[i][3])
            return results
    finally:
        sys.modules["__main__"] = main_module
        sys.path.remove(directory)


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
