import unittest
import tempfile
import os
from termreel.utils.asciicast import AsciicastRecorder, AsciicastPlayer


class TestAsciicast(unittest.TestCase):

    def test_record_and_replay(self):
        with tempfile.NamedTemporaryFile(suffix=".cast", delete=False) as f:
            cast_path = f.name

        rec = AsciicastRecorder(cast_path, width=100, height=30, title="Test Session")
        rec.start()
        rec.record_output("Line 1\r\n")
        rec.record_output("Line 2\r\n")
        rec.close()

        player = AsciicastPlayer(cast_path)
        self.assertEqual(player.width, 100)
        self.assertEqual(player.height, 30)
        self.assertEqual(len(player.events), 2)
        self.assertEqual(player.events[0][1], "o")
        self.assertEqual(player.events[0][2], "Line 1\r\n")

        os.unlink(cast_path)


if __name__ == "__main__":
    unittest.main()
