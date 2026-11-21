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
Part sizes for the import and export dialogs: readable lengths, and a check
that catches a wrong unit or scale before it's too late.
"""

from typing import Optional, Sequence

__all__ = [
    "format_length",
    "size_problem",
    "draw_size",
]

# A part smaller than this or larger than this (its largest dimension, in mm)
# almost certainly has the wrong unit or scale.
SMALLEST_PLAUSIBLE_MM = 1.0
LARGEST_PLAUSIBLE_MM = 1000.0


def format_length(mm: float) -> str:
    """A length in millimetres as a short, rounded string in a fitting unit: 850 µm, 12.5 mm, 34.2 cm, 2.55 m."""
    size = abs(mm)
    if size == 0:
        return "0 mm"
    if size < 1:
        value, unit = mm * 1000, "µm"
    elif size < 100:
        value, unit = mm, "mm"
    elif size < 1000:
        value, unit = mm / 10, "cm"
    elif size < 1e6:
        value, unit = mm / 1000, "m"
    else:
        value, unit = mm / 1e6, "km"
    text = f"{value:.3g}"
    if "e" in text:  # Beyond the range of its unit (over 999 km): plain digits anyway.
        text = f"{value:.0f}"
    return f"{text} {unit}"


def size_problem(size: Sequence[float]) -> Optional[str]:
    """Why a width, depth and height in mm look wrong, or None if they look like a printable part."""
    largest = max(size)
    if largest < SMALLEST_PLAUSIBLE_MM:
        return "Smaller than 1 mm"
    if largest > LARGEST_PLAUSIBLE_MM:
        return "Larger than 1 m"
    return None


def draw_size(layout, title: str, size: Sequence[float], hint: str) -> None:
    """Draw a size, one dimension per line so it never gets cut off, flagged when it looks wrong.

    :param layout: The layout to draw into.
    :param title: The first line, e.g. "Size in file".
    :param size: Width, depth and height in millimetres.
    :param hint: What to check when the size looks wrong, e.g. "Check Scale and Unit Scale".
    """
    problem = size_problem(size)
    col = layout.column(align=True)
    col.alert = problem is not None
    col.label(text=f"{title}:", icon="ERROR" if problem else "DRIVER_DISTANCE")
    for axis, length in zip("XYZ", size):
        col.label(text=f"{axis}  {format_length(length)}", icon="BLANK1")
    if problem:
        col.label(text=f"{problem}: {hint}", icon="BLANK1")
