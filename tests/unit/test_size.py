"""
Unit tests for ``io_mesh_3mf.common.size``: readable lengths and the size check.
"""

import unittest

from io_mesh_3mf.common.size import format_length, size_problem


class TestFormatLength(unittest.TestCase):
    """Lengths are rounded to three figures in a fitting unit, never in scientific notation."""

    def test_units(self):
        self.assertEqual(format_length(0.85), "850 µm")
        self.assertEqual(format_length(12.5), "12.5 mm")
        self.assertEqual(format_length(342.123), "34.2 cm")
        self.assertEqual(format_length(2546.0), "2.55 m")
        self.assertEqual(format_length(254600.0), "255 m")
        self.assertEqual(format_length(2.5e6), "2.5 km")
        self.assertEqual(format_length(0), "0 mm")

    def test_no_scientific_notation(self):
        for mm in (1e-6, 0.0123, 99.95, 999.9, 1.416e5, 9.99e11):
            self.assertNotIn("e", format_length(mm).split()[0])


class TestSizeProblem(unittest.TestCase):
    """A part whose largest dimension is under 1 mm or over 1 m is flagged."""

    def test_plausible(self):
        self.assertIsNone(size_problem((0.5, 20, 300)))
        self.assertIsNone(size_problem((1, 1, 1)))
        self.assertIsNone(size_problem((380, 284, 380)))

    def test_too_small(self):
        self.assertEqual(size_problem((0.02, 0.02, 0.01)), "Smaller than 1 mm")

    def test_too_large(self):
        self.assertEqual(size_problem((20000, 5, 5)), "Larger than 1 m")


if __name__ == "__main__":
    unittest.main()
