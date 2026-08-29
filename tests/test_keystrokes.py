import unittest
from termreel.utils.keystrokes import KeystrokeGenerator, KeyMap


class TestKeystrokes(unittest.TestCase):

    def test_keystroke_generation_cadence(self):
        kg = KeystrokeGenerator(base_speed=0.02, jitter=0.005, typo_rate=0.0)
        events = list(kg.generate_keystroke_events("git status"))
        self.assertEqual(len(events), 10)
        for act, val, delay in events:
            self.assertEqual(act, "char")
            self.assertGreater(delay, 0.0)

    def test_typo_generation(self):
        kg = KeystrokeGenerator(base_speed=0.01, jitter=0.0, typo_rate=1.0)
        events = list(kg.generate_keystroke_events("a"))
        # With 100% typo rate, should generate typo, pause, backspace, correct char
        actions = [e[0] for e in events]
        self.assertIn("key", actions)
        self.assertEqual(events[-1], ("char", "a", kg.get_delay("a")))


if __name__ == "__main__":
    unittest.main()
