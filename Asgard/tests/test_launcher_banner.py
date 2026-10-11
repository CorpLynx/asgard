"""The tkinter launcher's warning banners (the fallback when PySide6 won't load). Skips without a display."""
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from asgard import launcher  # noqa: E402
from asgard.ui import prefs as ui_prefs  # noqa: E402


@unittest.skipIf(launcher.tk is None, "no tkinter")
class TkBannerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self._home = os.environ.get("ASGARD_HOME")
        os.environ["ASGARD_HOME"] = self.tmp.name
        try:
            self.root = launcher.tk.Tk()
        except launcher.tk.TclError as exc:
            self.tmp.cleanup()
            self.skipTest(f"no display ({exc})")
        self.win = launcher.LauncherWindow(self.root, dark=False)

    def tearDown(self):
        self.win.close()
        if self._home is None:
            os.environ.pop("ASGARD_HOME", None)
        else:
            os.environ["ASGARD_HOME"] = self._home
        self.tmp.cleanup()

    def pump(self, seconds):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            self.root.update()
            time.sleep(0.01)

    def test_banners_scroll_pulse_pause_and_follow_settings(self):
        top, bottom = self.win.banners
        self.pump(0.6)
        self.assertTrue(top.items and bottom.items)
        moved = top.offset
        self.assertGreater(moved, 0, "the text scrolls")
        inks = {top.canvas.itemcget(top.items[0], "fill") for _ in range(8) if not self.pump(0.1)}
        self.assertGreater(len(inks), 1, "the colour pulses")
        top.toggle_pause()
        held = top.offset
        self.pump(0.3)
        self.assertEqual(top.offset, held, "click pauses")

        self.win._save_banner(text="CHANGE FREEZE")
        self.assertEqual(ui_prefs.load().banner.text, "CHANGE FREEZE")
        self.assertEqual(bottom.canvas.itemcget(bottom.items[0], "text"), "CHANGE FREEZE")
        self.win._save_banner(animate=False)
        self.assertEqual(len(top.items), 1)
        self.assertEqual(top.canvas.itemcget(top.items[0], "fill").upper(), ui_prefs.BANNER_COLORS["still"])
        self.win._save_banner(enabled=False)
        self.root.update()
        self.assertEqual(top.items, [])
        self.assertLessEqual(top.canvas.winfo_height(), 1)


if __name__ == "__main__":
    unittest.main()
