# Blender add-on to import and export 3MF files.
# Copyright (C) 2020 Ghostkeeper
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
Metadata storage for 3MF objects and scenes.

Tracks metadata entries with conflict resolution — if the same key appears
with different values across multiple 3MF files, that entry is marked
conflicting and excluded from output.
"""

import collections
import json
import re
from typing import Any, Dict, Iterator, Optional, Union

import bpy.types
import idprop.types

MetadataEntry = collections.namedtuple(
    "MetadataEntry", ["name", "preserve", "datatype", "value"]
)

__all__ = [
    "Metadata",
    "MetadataEntry",
    "stamp_application",
    "NAMESPACES_PROPERTY",
    "declare_metadata_namespace",
    "metadata_namespace_uri",
    "set_metadata",
    "get_metadata",
]

# Scene property holding ``{prefix: namespace URI}`` for metadata names such as
# ``"ns:key"``: declared by set_metadata(), and read from imported files, so a
# re-export declares each prefix with its real URI.
NAMESPACES_PROPERTY = "3mf_metadata_namespaces"

_PREFIX = re.compile(r"^([A-Za-z_][\w.-]*):")


def declare_metadata_namespace(prefix: str, uri: str, scene=None, replace: bool = True) -> None:
    """Record the namespace URI that a metadata name prefix stands for.

    :param replace: Overwrite an existing different URI for this prefix.
    """
    import bpy
    scene = scene or bpy.context.scene
    table = dict(scene.get(NAMESPACES_PROPERTY, {}))
    if replace or prefix not in table:
        table[prefix] = uri
    scene[NAMESPACES_PROPERTY] = table


def metadata_namespace_uri(prefix: str, scene=None) -> Optional[str]:
    """The URI recorded for *prefix* in the scene, or None."""
    import bpy
    scene = scene or bpy.context.scene
    uri = scene.get(NAMESPACES_PROPERTY, {}).get(prefix) if scene is not None else None
    return str(uri) if uri else None


def set_metadata(
    target: Union[bpy.types.Object, bpy.types.Scene],
    name: str,
    value: Any,
    namespace: Optional[str] = None,
    preserve: bool = True,
) -> None:
    """Attach a 3MF metadata entry to an object (its part) or the scene (the model).

    Exported as ``<metadata>`` in the object's ``<metadatagroup>``, or at the
    top of the model for the scene, and read back the same way on import.

    :param name: ``"prefix:key"``. Names without a prefix are reserved for the
        core specification's own names (Title, Designer, ...).
    :param value: A string, or anything ``json.dumps`` accepts (stored as JSON).
    :param namespace: The URI the prefix stands for. Needed once per prefix,
        unless the prefix came in with an imported file.
    :param preserve: Ask programs that edit the file to keep this entry.
    """
    match = _PREFIX.match(name)
    if match is None and not isinstance(target, bpy.types.Scene):
        raise ValueError(f"Metadata name {name!r} needs a namespace prefix, such as 'myapp:{name}'")
    if namespace:
        if match is None:
            raise ValueError(f"Metadata name {name!r} has no prefix for namespace {namespace!r}")
        declare_metadata_namespace(match.group(1), namespace)
    if not isinstance(value, str):
        value = json.dumps(value, sort_keys=True)
    target[name] = {"datatype": "xs:string", "preserve": bool(preserve), "value": value}


def get_metadata(target: Union[bpy.types.Object, bpy.types.Scene], name: str, parse_json: bool = False) -> Any:
    """The value of a metadata entry set by :func:`set_metadata` or imported, or None.

    :param parse_json: Decode the value as JSON (returns the string if it isn't JSON).
    """
    entry = target.get(name)
    if entry is None or not hasattr(entry, "keys") or "value" not in entry.keys():
        return None
    value = entry["value"]
    if parse_json and isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return value
    return value


def stamp_application(metadata: "Metadata") -> None:
    """Record this Blender and add-on as the file's ``Application``.

    Replaces an ``Application`` entry carried over from an imported file:
    the program writing the file is this one.
    """
    from .version import application_name
    del metadata["Application"]
    metadata["Application"] = MetadataEntry(
        name="Application", preserve=False, datatype="xs:string", value=application_name()
    )


class Metadata:
    """
    Tracks metadata of a Blender object for 3MF round-trip.

    Behaves like a dictionary keyed by metadata name.  Storing the same key
    with a different value marks that entry as conflicting (``None``), so only
    the intersection of consistent metadata survives a multi-file import.
    """

    def __init__(self):
        self.metadata = {}

    def __setitem__(self, key: str, value: MetadataEntry) -> None:
        if key not in self.metadata:
            self.metadata[key] = value
            return

        if self.metadata[key] is None:
            return

        competing = self.metadata[key]
        if value.value != competing.value or value.datatype != competing.datatype:
            self.metadata[key] = None
            return

        if not competing.preserve and value.preserve:
            self.metadata[key] = MetadataEntry(
                name=key,
                preserve=True,
                datatype=competing.datatype,
                value=competing.value,
            )

    def __getitem__(self, key: str) -> MetadataEntry:
        if key not in self.metadata or self.metadata[key] is None:
            raise KeyError(key)
        return self.metadata[key]

    def __contains__(self, item: str) -> bool:
        return item in self.metadata and self.metadata[item] is not None

    def __bool__(self) -> bool:
        return any(self.values())

    def __len__(self) -> int:
        return sum(1 for _ in self.values())

    def __delitem__(self, key: str) -> None:
        if key in self.metadata:
            del self.metadata[key]

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Metadata):
            return NotImplemented
        return self.metadata == other.metadata

    def store(self, blender_object: Union[bpy.types.Object, bpy.types.Scene], keep_existing: bool = False) -> None:
        """Store this metadata in a Blender object as custom properties.

        :param keep_existing: Leave the name and any property that's already there
            alone, for something the user had before the import (the scene).
        """
        for metadata_entry in self.values():
            name = str(metadata_entry.name)
            if keep_existing and (name == "Title" or name in blender_object.keys()):
                continue
            value = (
                str(metadata_entry.value) if metadata_entry.value is not None else ""
            )
            if name == "Title":
                blender_object.name = value
            elif name == "3mf:partnumber":
                blender_object[name] = value
            else:
                datatype = (
                    str(metadata_entry.datatype)
                    if metadata_entry.datatype is not None
                    else ""
                )
                blender_object[name] = {
                    "datatype": datatype,
                    "preserve": metadata_entry.preserve,
                    "value": value,
                }

    def retrieve(
        self, blender_object: Union[bpy.types.Object, bpy.types.Scene]
    ) -> None:
        """Retrieve metadata from a Blender object's custom properties."""
        for key in blender_object.keys():
            cached_key = str(key)
            entry = blender_object[key]
            if cached_key == "3mf:partnumber":
                cached_entry = str(entry)
                self[cached_key] = MetadataEntry(
                    name=cached_key,
                    preserve=True,
                    datatype="xs:string",
                    value=cached_entry,
                )
                continue
            if (
                isinstance(entry, idprop.types.IDPropertyGroup)
                and "datatype" in entry.keys()
                and "preserve" in entry.keys()
                and "value" in entry.keys()
            ):
                self[key] = MetadataEntry(
                    name=key,
                    preserve=entry.get("preserve"),
                    datatype=entry.get("datatype"),
                    value=entry.get("value"),
                )

        self["Title"] = MetadataEntry(
            name="Title",
            preserve=True,
            datatype="xs:string",
            value=blender_object.name,
        )

    def values(self) -> Iterator[MetadataEntry]:
        """Yield all non-conflicting metadata entries."""
        yield from filter(lambda entry: entry is not None, self.metadata.values())
