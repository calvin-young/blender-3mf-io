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
Archive management for 3MF export.

Functions for creating and managing the 3MF ZIP archive:
- create_archive: Create an empty 3MF archive with OPC structure
- must_preserve: Write must-preserve files from previous imports
- write_core_properties: Write Dublin Core metadata
"""

import base64
import datetime
import os
import posixpath
import xml.etree.ElementTree
import zipfile
from typing import Optional, Callable

import bpy

from ..common.annotations import Annotations
from ..common.logging import debug, error
from ..common.version import ADDON_NAME, application_name
from ..common.constants import (
    CORE_PROPERTIES_LOCATION,
    CORE_PROPERTIES_NAMESPACE,
    DC_NAMESPACE,
    DCTERMS_NAMESPACE,
    MATERIAL_NAMESPACE,
    conflicting_mustpreserve_contents,
)


class _ThreeMFArchive(zipfile.ZipFile):
    """
    ZIP archive that writes the OPC package parts when it is closed.

    ``_rels/.rels`` and ``[Content_Types].xml`` are written last so they
    only describe parts that actually ended up in the archive. For example,
    the thumbnail relationship is omitted when no thumbnail was written
    (thumbnail mode NONE, or the thumbnail render failed).
    """

    annotations: Optional[Annotations] = None
    # Strip Names & Metadata: every entry gets this date instead of the time of
    # export, and images get generic names, recorded here as {original: new}
    # for the model's references (see rename_texture_paths).
    fixed_date: Optional[tuple] = None
    texture_renames: Optional[dict] = None

    def _entry(self, name):
        """The ZipInfo (or name) to write *name* under."""
        if isinstance(name, zipfile.ZipInfo):
            return name
        name = self._renamed(name)
        if self.fixed_date is None:
            return name
        info = zipfile.ZipInfo(name, self.fixed_date)
        info.compress_type = self.compression
        info.compress_level = self.compresslevel
        info._compresslevel = self.compresslevel  # Python before 3.13
        info.external_attr = 0o600 << 16
        return info

    def _renamed(self, name: str) -> str:
        if self.texture_renames is None or name.startswith("Metadata/"):
            return name
        if not name.lower().endswith((".png", ".jpg", ".jpeg")):
            return name
        if name not in self.texture_renames:
            folder, extension = posixpath.dirname(name), posixpath.splitext(name)[1].lower()
            new = f"texture_{len(self.texture_renames) + 1}{extension}"
            self.texture_renames[name] = f"{folder}/{new}" if folder else new
        return self.texture_renames[name]

    def open(self, name, mode="r", pwd=None, *, force_zip64=False):
        if mode == "w":
            name = self._entry(name)
        return super().open(name, mode, pwd, force_zip64=force_zip64)

    def writestr(self, zinfo_or_arcname, data, compress_type=None, compresslevel=None):
        return super().writestr(self._entry(zinfo_or_arcname), data, compress_type, compresslevel)

    def write(self, filename, arcname=None, compress_type=None, compresslevel=None):
        if self.fixed_date is None and self.texture_renames is None:
            return super().write(filename, arcname, compress_type, compresslevel)
        with open(filename, "rb") as f:
            data = f.read()
        return self.writestr(arcname or os.path.basename(filename), data, compress_type, compresslevel)

    def getinfo(self, name):
        if isinstance(name, str) and self.texture_renames is not None and name in self.texture_renames:
            name = self.texture_renames[name]
        return super().getinfo(name)

    def close(self) -> None:
        annotations, self.annotations = self.annotations, None
        try:
            if annotations is not None and self.fp is not None:
                annotations.write_rels(self)
                annotations.write_content_types(self)
        finally:
            super().close()


def create_archive(
    filepath: str,
    safe_report: Callable,
    compression_level: int = 3,
    preserve_imported: bool = True,
    fixed_date: bool = False,
) -> Optional[zipfile.ZipFile]:
    """
    Creates an empty 3MF archive.

    The archive is complete according to the 3MF specs except that the actual
    3dmodel.model file is missing. The OPC relationships and content types are
    written when the archive is closed.

    :param filepath: The path to write the file to.
    :param safe_report: Callable for reporting errors/warnings.
    :param compression_level: ZIP deflate compression level (0=store, 9=max). Default 3.
    :param preserve_imported: Carry over the relationships, content types and
        must-preserve parts stored from previously imported 3MF files.
    :param fixed_date: Date every entry 1980-01-01 instead of the time of export.
    :return: A zip archive that other functions can add things to.
    """
    try:
        archive = _ThreeMFArchive(
            filepath, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=compression_level
        )
        if fixed_date:
            archive.fixed_date = (1980, 1, 1, 0, 0, 0)

        # Store the file annotations we got from imported 3MF files.
        archive.annotations = Annotations()
        if preserve_imported:
            archive.annotations.retrieve()
            must_preserve(archive)
    except EnvironmentError as e:
        error(f"Unable to write 3MF archive to {filepath}: {e}")
        safe_report({"ERROR"}, f"Unable to write 3MF archive to {filepath}: {e}")
        return None

    return archive


def must_preserve(archive: zipfile.ZipFile) -> None:
    """
    Write files that must be preserved to the archive.

    These files were stored in the Blender scene in a hidden location.

    :param archive: The archive to write files to.
    """
    for textfile in bpy.data.texts:
        # Cache filename to protect Unicode characters from garbage collection
        filename = str(textfile.name)
        if not filename.startswith(".3mf_preserved/"):
            continue  # Unrelated file. Not ours to read.
        contents = textfile.as_string()
        if contents == conflicting_mustpreserve_contents:
            continue  # This file was in conflict. Don't preserve any copy of it then.
        contents = base64.b85decode(contents.encode("UTF-8"))
        filename = filename[len(".3mf_preserved/"):]
        with archive.open(filename, "w") as f:
            f.write(contents)


def rename_texture_paths(archive: zipfile.ZipFile, root, texture_rels: dict) -> None:
    """Point the model's texture references at the generic names the archive gave the images."""
    renames = getattr(archive, "texture_renames", None)
    if not renames:
        return
    for texture in root.iter(f"{{{MATERIAL_NAMESPACE}}}texture2d"):
        path = texture.get("path", "")
        if path.lstrip("/") in renames:
            texture.set("path", "/" + renames[path.lstrip("/")])
    for key, path in list(texture_rels.items()):
        if path.lstrip("/") in renames:
            texture_rels[key] = "/" + renames[path.lstrip("/")]


def write_core_properties(archive: zipfile.ZipFile, include_timestamps: bool = True) -> None:
    """
    Write OPC Core Properties (Dublin Core metadata) to the archive.

    This adds standard document metadata like creator, creation date, and modification
    date as defined by the Open Packaging Conventions specification.

    :param archive: The 3MF archive to write Core Properties into.
    :param include_timestamps: Write the created / modified dates, and the
        Blender and add-on versions in ``dc:creator`` (off when stripping
        identifying data).
    """
    # Register namespaces for cleaner output
    xml.etree.ElementTree.register_namespace("cp", CORE_PROPERTIES_NAMESPACE)
    xml.etree.ElementTree.register_namespace("dc", DC_NAMESPACE)
    xml.etree.ElementTree.register_namespace("dcterms", DCTERMS_NAMESPACE)

    # Create root element with proper namespaces
    root = xml.etree.ElementTree.Element(
        f"{{{CORE_PROPERTIES_NAMESPACE}}}coreProperties"
    )

    # dc:creator - who created this file
    creator = xml.etree.ElementTree.SubElement(root, f"{{{DC_NAMESPACE}}}creator")
    creator.text = application_name() if include_timestamps else ADDON_NAME

    if include_timestamps:
        # dcterms:created - when the file was created (W3CDTF format)
        now = datetime.datetime.now(datetime.timezone.utc)
        created = xml.etree.ElementTree.SubElement(root, f"{{{DCTERMS_NAMESPACE}}}created")
        created.text = now.strftime("%Y-%m-%dT%H:%M:%SZ")

        # dcterms:modified - when the file was last modified
        modified = xml.etree.ElementTree.SubElement(
            root, f"{{{DCTERMS_NAMESPACE}}}modified"
        )
        modified.text = now.strftime("%Y-%m-%dT%H:%M:%SZ")

    # Write the Core Properties file
    document = xml.etree.ElementTree.ElementTree(root)
    xml.etree.ElementTree.indent(document)
    try:
        with archive.open(CORE_PROPERTIES_LOCATION, "w") as f:
            document.write(f, xml_declaration=True, encoding="UTF-8")
        debug("Wrote OPC Core Properties to docProps/core.xml")
    except Exception as e:
        error(f"Failed to write Core Properties: {e}")
