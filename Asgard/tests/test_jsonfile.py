"""Writing a settings file safely (asgard/jsonfile.py), including the two ways it went wrong.

Both of these were real failures, found by the Windows build:
- two writers sharing one fixed temp filename, where the first rename takes the file the second is
  about to rename (seen on macOS as FileNotFoundError while the Settings page was drawn);
- os.replace refusing on Windows because something else has the target open (PermissionError), where
  POSIX simply does it.

The Windows one can't be reproduced on this machine, so it is tested by making os.replace fail the
way Windows does.
"""
import json
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from asgard import jsonfile  # noqa: E402


class WriteJsonTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.path = self.dir / "settings" / "app.json"

    def leftovers(self):
        return sorted(p.name for p in self.path.parent.glob("*.tmp"))

    def test_it_writes_json_and_makes_the_folder(self):
        written = jsonfile.write_json(self.path, {"a": 1, "b": ["x"]})
        self.assertEqual(written, self.path)
        self.assertEqual(json.loads(self.path.read_text(encoding="utf-8")), {"a": 1, "b": ["x"]})
        self.assertTrue(self.path.read_text(encoding="utf-8").endswith("\n"))
        self.assertEqual(self.leftovers(), [], "the temporary file must not be left behind")

    def test_each_write_uses_its_own_temporary_file(self):
        seen = []
        real = tempfile.mkstemp

        def watch(*args, **kwargs):
            handle, name = real(*args, **kwargs)
            seen.append(Path(name).name)
            return handle, name

        with mock.patch.object(jsonfile.tempfile, "mkstemp", watch):
            jsonfile.write_json(self.path, {"n": 1})
            jsonfile.write_json(self.path, {"n": 2})
        self.assertEqual(len(set(seen)), 2, f"two writes shared one temporary name: {seen}")

    def test_a_rename_windows_refuses_is_tried_again(self):
        """os.replace fails with PermissionError while another handle has the target open."""
        calls = []
        real = os.replace

        def refuse_twice(src, dst):
            calls.append(1)
            if len(calls) <= 2:
                raise PermissionError(32, "The process cannot access the file because it is being used "
                                          "by another process")
            return real(src, dst)

        with mock.patch.object(jsonfile.os, "replace", refuse_twice):
            jsonfile.write_json(self.path, {"after": "retry"})
        self.assertEqual(len(calls), 3)
        self.assertEqual(json.loads(self.path.read_text(encoding="utf-8")), {"after": "retry"})
        self.assertEqual(self.leftovers(), [])

    def test_a_rename_that_keeps_being_refused_raises_and_leaves_nothing(self):
        def always_refuse(src, dst):
            raise PermissionError(5, "Access is denied")

        with mock.patch.object(jsonfile.os, "replace", always_refuse):
            with self.assertRaises(PermissionError):
                jsonfile.write_json(self.path, {"never": "lands"}, )
        self.assertFalse(self.path.exists())
        self.assertEqual(self.leftovers(), [], "a failed write must clean up after itself")

    def test_retrying_gives_up_after_its_deadline(self):
        def always_refuse(src, dst):
            raise PermissionError(32, "in use")

        source = self.dir / "a.tmp"
        source.write_text("x", encoding="utf-8")
        with mock.patch.object(jsonfile.os, "replace", always_refuse):
            with self.assertRaises(PermissionError):
                jsonfile.replace_with_retry(source, self.dir / "b.json", total=0.05)

    def test_an_earlier_file_is_replaced_not_appended_to(self):
        jsonfile.write_json(self.path, {"first": True})
        jsonfile.write_json(self.path, {"second": True})
        self.assertEqual(json.loads(self.path.read_text(encoding="utf-8")), {"second": True})

    def test_many_threads_writing_at_once_all_finish(self):
        problems = []

        def work(n):
            try:
                jsonfile.write_json(self.path, {"n": n})
            except Exception as exc:                    # noqa: BLE001 - the test reports it
                problems.append(exc)

        threads = [threading.Thread(target=work, args=(n,)) for n in range(12)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(problems, [])
        self.assertIn(json.loads(self.path.read_text(encoding="utf-8"))["n"], range(12))
        self.assertEqual(self.leftovers(), [])

    def test_the_apps_all_write_through_it(self):
        """Odin, Baldur and Ysildir share this, so a fix here fixes all three."""
        for module, where in (("baldur.settings", ROOT / "apps" / "baldur"),
                              ("odin.config", ROOT / "apps" / "odin"),
                              ("ysildir.config", ROOT / "apps" / "ysildir")):
            with self.subTest(module):
                if str(where) not in sys.path:
                    sys.path.insert(0, str(where))
                try:
                    imported = __import__(module, fromlist=["x"])
                except ImportError as exc:              # ysildir needs the MCP SDK's pydantic
                    self.skipTest(f"{module} isn't importable here ({exc})")
                self.assertIs(getattr(imported, "jsonfile", None), jsonfile,
                              f"{module} must write through asgard.jsonfile")


if __name__ == "__main__":
    unittest.main()
