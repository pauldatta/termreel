import unittest
from termreel.emulator.colors import (
    parse_hex_color,
    rgb_to_hex,
    ColorPalette,
    truecolor_rgb,
    blend_colors,
    adjust_brightness,
    DEFAULT_PALETTE,
)


class TestColors(unittest.TestCase):

    def test_parse_hex_color(self):
        self.assertEqual(parse_hex_color("#000000"), (0.0, 0.0, 0.0))
        self.assertEqual(parse_hex_color("#ffffff"), (1.0, 1.0, 1.0))
        self.assertEqual(parse_hex_color("#fff"), (1.0, 1.0, 1.0))
        r, g, b = parse_hex_color("#ff8000")
        self.assertAlmostEqual(r, 1.0)
        self.assertAlmostEqual(g, 128 / 255.0, places=2)
        self.assertAlmostEqual(b, 0.0)

    def test_rgb_to_hex(self):
        self.assertEqual(rgb_to_hex((0.0, 0.0, 0.0)), "#000000")
        self.assertEqual(rgb_to_hex((1.0, 1.0, 1.0)), "#ffffff")
        self.assertEqual(rgb_to_hex((1.0, 0.0, 0.0)), "#ff0000")

    def test_256_palette_cube(self):
        palette = ColorPalette()
        # 0-15 standard
        c0 = palette.get_256_color(0)
        self.assertIsInstance(c0, tuple)
        self.assertEqual(len(c0), 3)

        # 16: Black in RGB cube
        c16 = palette.get_256_color(16)
        self.assertEqual(c16, (0.0, 0.0, 0.0))

        # 231: White in RGB cube
        c231 = palette.get_256_color(231)
        self.assertEqual(c231, (1.0, 1.0, 1.0))

        # 232-255: Grayscale ramp
        c232 = palette.get_256_color(232)
        c255 = palette.get_256_color(255)
        self.assertTrue(c232[0] < c255[0])

    def test_truecolor_rgb(self):
        tc = truecolor_rgb(255, 128, 0)
        self.assertEqual(tc[0], 1.0)
        self.assertAlmostEqual(tc[1], 128 / 255.0, places=2)
        self.assertEqual(tc[2], 0.0)

    def test_blend_and_brightness(self):
        c1 = (0.0, 0.0, 0.0)
        c2 = (1.0, 1.0, 1.0)
        blended = blend_colors(c1, c2, 0.5)
        self.assertEqual(blended, (0.5, 0.5, 0.5))

        brightened = adjust_brightness((0.2, 0.3, 0.4), 2.0)
        self.assertAlmostEqual(brightened[0], 0.4)
        self.assertAlmostEqual(brightened[1], 0.6)
        self.assertAlmostEqual(brightened[2], 0.8)


if __name__ == "__main__":
    unittest.main()
