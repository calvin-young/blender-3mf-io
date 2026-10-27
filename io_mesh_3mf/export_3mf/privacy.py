# Blender add-on to import and export 3MF files.
# Copyright (C) 2026 Calvin Young
# This add-on is free software; you can redistribute it and/or modify it under the terms of the GNU General Public
# License as published by the Free Software Foundation; either version 2 of the License, or (at your option) any later
# version.
# This add-on is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied
# warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.
# You should have received a copy of the GNU General Public License along with this program; if not, write to the Free
# Software Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA.

# <pep8 compliant>

"""
Leave identifying information out of exported 3MF files.

Used by the ``strip_identifying_data`` export option, for sharing parts
whose names or origin shouldn't travel with the file.  The geometry, colors,
textures and placement are unchanged; what gets removed is anything that
names the parts or describes where they came from:

- object names and part numbers (generic names are used where a slicer
  config needs one),
- ``<metadata>`` and ``<metadatagroup>`` (title, designer, description,
  dates, ...), except the few entries slicers need to recognize the format,
- material and triangle set names (replaced by generic names, since the
  spec requires them),
- texture file names (handled by the texture writers),
- the thumbnail, the creation/modification timestamps, and parts carried
  over from an imported file (handled by the exporters / archive).
"""

import xml.etree.ElementTree

__all__ = [
    "strip_model_xml",
    "strip_config_names",
]

# Metadata that slicers need to read the file correctly, not describing the parts.
_TECHNICAL_METADATA = frozenset({
    "BambuStudio:3mfVersion",
    "slic3rpe:Version3mf",
    "slic3rpe:MmPaintingVersion",
})


# Materials-extension elements whose required ``name`` attribute is replaced.
_DISPLAY_PROPERTY_TAGS = frozenset({
    "pbspecular",
    "pbmetallic",
    "translucent",
    "pbspeculartexturedisplayproperties",
    "pbmetallictexturedisplayproperties",
})


def _local(name: str) -> str:
    """Return the local part of a Clark-notation tag or attribute name."""
    return name.rsplit("}", 1)[-1]


def _attribute_key(element: xml.etree.ElementTree.Element, local_name: str):
    """Return the actual key of attribute *local_name* (plain or namespaced), or None."""
    for key in element.attrib:
        if _local(key) == local_name:
            return key
    return None


def strip_model_xml(root: xml.etree.ElementTree.Element, keep_metadata=()) -> None:
    """Remove names, part numbers and metadata from a ``<model>`` tree in place.

    Works on the trees built by every exporter, whether attributes are
    namespace-qualified (Standard) or plain (Orca / Prusa).

    :param root: The ``<model>`` root element.
    :param keep_metadata: Extra metadata names to keep, e.g. ``"Application"``,
        which Orca Slicer / BambuStudio use to recognize their project format.
    """
    keep = _TECHNICAL_METADATA.union(keep_metadata)
    material_count = 0
    display_count = 0
    triangle_set_count = 0
    for parent in root.iter():
        for child in list(parent):
            tag = _local(child.tag)
            if tag == "metadatagroup":
                parent.remove(child)
            elif tag == "metadata":
                name_key = _attribute_key(child, "name")
                if name_key is None or child.attrib[name_key] not in keep:
                    parent.remove(child)

        tag = _local(parent.tag)
        if tag in ("object", "item"):
            for local_name in ("name", "partnumber"):
                key = _attribute_key(parent, local_name)
                if key is not None:
                    del parent.attrib[key]
        elif tag == "base":
            # <basematerials> requires a name.
            key = _attribute_key(parent, "name")
            if key is not None:
                material_count += 1
                parent.attrib[key] = f"Material {material_count}"
        elif tag in _DISPLAY_PROPERTY_TAGS:
            # Display properties (Materials extension) require a name too.
            key = _attribute_key(parent, "name")
            if key is not None:
                display_count += 1
                parent.attrib[key] = f"Material {display_count}"
        elif tag == "triangleset":
            # The Triangle Sets extension requires a name; the identifier is optional.
            triangle_set_count += 1
            key = _attribute_key(parent, "name")
            if key is not None:
                parent.attrib[key] = f"Set {triangle_set_count}"
            key = _attribute_key(parent, "identifier")
            if key is not None:
                del parent.attrib[key]


def strip_config_names(root: xml.etree.ElementTree.Element) -> None:
    """Replace object and part names in a slicer model config with generic ones.

    Covers ``<metadata key="name" value="..."/>`` entries as written to Orca's
    ``model_settings.config`` and PrusaSlicer's ``Slic3r_PE_model.config``.

    :param root: The config root element.
    """
    counts = {}
    for element in root.iter():
        for child in element:
            if _local(child.tag) == "metadata" and child.get("key") == "name" and "value" in child.attrib:
                kind = _local(element.tag)
                counts[kind] = counts.get(kind, 0) + 1
                child.set("value", f"{kind.capitalize()} {counts[kind]}")
