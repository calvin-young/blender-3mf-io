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
The name and version of the program writing a 3MF file.

Every export records which Blender and which build of this add-on wrote it,
in the core specification's ``Application`` metadata, so a file can be traced
back to the software that made it.  Both versions are read at run time.
"""

import functools
import pathlib

import bpy

__all__ = ["ADDON_NAME", "addon_version", "application_name"]

ADDON_NAME = "Blender 3MF Format Add-on"


@functools.lru_cache(maxsize=1)
def addon_version() -> str:
    """The add-on's version from its manifest, or ``"unknown"`` if it can't be read."""
    manifest = pathlib.Path(__file__).resolve().parent.parent / "blender_manifest.toml"
    try:
        import tomllib
        with open(manifest, "rb") as f:
            return str(tomllib.load(f)["version"])
    except Exception:
        return "unknown"


def application_name() -> str:
    """E.g. ``"Blender 5.2.3; Blender 3MF Format Add-on 2.7.7"``."""
    return f"Blender {bpy.app.version_string}; {ADDON_NAME} {addon_version()}"
