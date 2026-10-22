"""asgard/notify.py: what is declared, what the person chose, what is shown, and what is held back."""
import contextlib
import io
import json
import os
import tempfile
import time
import unittest
from pathlib import Path

from asgard import notify, notifycli

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = {"app": "demo", "name": "Demo", "events": [
    {"id": "news", "label": "News", "level": "info", "repeat_minutes": 60},
    {"id": "alarm", "label": "Alarm", "level": "error"},
    {"id": "quiet_one", "label": "Off by default", "default": False},
]}
NOON = time.mktime((2026, 10, 22, 12, 0, 0, 0, 0, -1))
NIGHT = time.mktime((2026, 10, 22, 23, 30, 0, 0, 0, -1))


class NotifyCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name).resolve()
        saved = os.environ.get("ASGARD_HOME")
        os.environ["ASGARD_HOME"] = str(self.dir)
        self.addCleanup(lambda: os.environ.pop("ASGARD_HOME", None) if saved is None
                        else os.environ.__setitem__("ASGARD_HOME", saved))
        self.shown = []
        notify.set_backend(lambda title, text, level: self.shown.append((title, text, level)))
        self.addCleanup(notify.set_backend, None)
        self.root = self.dir / "code"
        self.write_manifest(MANIFEST)

    def write_manifest(self, body, app="demo"):
        folder = self.root / "apps" / app
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "notify.json").write_text(json.dumps(body), encoding="utf-8")

    def prefs(self, body):
        path = notify.prefs_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(body), encoding="utf-8")

    def send(self, event="news", text="hello", **kw):
        kw.setdefault("now", NOON)
        return notify.send("demo", event, text, root=self.root, **kw)


class ManifestTests(NotifyCase):
    def test_every_shipped_manifest_is_valid(self):
        found, warnings = notify.discover(ROOT)
        self.assertEqual(warnings, [])
        self.assertTrue({"baldur", "odin", "heimdall"} <= set(found))
        for app in found.values():
            for ev in app.events.values():
                self.assertTrue(ev.label, ev.key)

    def test_a_broken_manifest_skips_only_that_app(self):
        self.write_manifest({"app": "broken", "events": []}, app="broken")
        found, warnings = notify.discover(self.root)
        self.assertEqual(list(found), ["demo"])
        self.assertIn("broken/notify.json", warnings[0])

    def test_the_app_must_match_its_folder_and_levels_are_checked(self):
        for bad in ({"app": "other", "events": MANIFEST["events"]},
                    {"app": "demo", "events": [{"id": "x", "label": "X", "level": "loud"}]},
                    {"app": "demo", "events": [{"id": "x", "label": "X"}, {"id": "x", "label": "Y"}]},
                    {"app": "demo", "events": [{"id": "x", "label": "X", "repeat_minutes": -1}]}):
            with self.subTest(bad):
                self.write_manifest(bad)
                self.assertEqual(notify.discover(self.root)[0], {})


class SendTests(NotifyCase):
    def test_a_declared_event_is_shown_with_the_apps_name(self):
        out = self.send("alarm", "It broke")
        self.assertTrue(out)
        self.assertEqual(self.shown, [("Demo", "It broke", "error")])

    def test_an_undeclared_event_or_app_is_refused(self):
        self.assertFalse(self.send("typo"))
        self.assertFalse(notify.send("nobody", "news", "x", root=self.root))
        self.assertEqual(self.shown, [])

    def test_text_is_one_line_and_cut(self):
        self.send("alarm", "a\n  b\t" + "x" * 500)
        text = self.shown[0][1]
        self.assertTrue(text.startswith("a b x") and "\n" not in text)
        self.assertLessEqual(len(text), notify.MAX_TEXT)

    def test_empty_text_shows_nothing(self):
        self.assertEqual(self.send("alarm", "  ").reason, "no text to show")

    def test_a_backend_that_fails_is_reported_not_raised(self):
        def broken(*_):
            raise notify.NotifyError("no taskbar")
        notify.set_backend(broken)
        out = self.send("alarm")
        self.assertFalse(out)
        self.assertIn("no taskbar", out.reason)

    def test_a_backend_that_crashes_is_reported_not_raised(self):
        notify.set_backend(lambda *_: 1 / 0)
        self.assertFalse(self.send("alarm"))

    def test_no_backend_says_so(self):
        notify.set_backend(None)
        os.environ["ASGARD_NOTIFY"] = "off"
        self.addCleanup(os.environ.pop, "ASGARD_NOTIFY", None)
        self.assertIn("no way", self.send("alarm").reason)

    def test_async_returns_and_the_backend_still_runs(self):
        self.assertTrue(self.send("alarm", wait=False))
        for _ in range(100):
            if self.shown:
                break
            time.sleep(0.01)
        self.assertEqual(len(self.shown), 1)


class ChoiceTests(NotifyCase):
    def test_the_manifest_default_applies_until_the_person_says_otherwise(self):
        self.assertFalse(self.send("quiet_one"))
        self.prefs({"events": {"demo.quiet_one": True}})
        self.assertTrue(self.send("quiet_one"))

    def test_the_master_switch_an_app_and_an_event_can_each_mute(self):
        for prefs in ({"enabled": False}, {"apps": {"demo": False}}, {"events": {"demo.alarm": False}}):
            with self.subTest(prefs):
                self.prefs(prefs)
                self.assertFalse(self.send("alarm"))
        self.assertEqual(self.shown, [])

    def test_quiet_hours_may_cross_midnight(self):
        self.prefs({"quiet_hours": {"start": "22:00", "end": "07:00"}})
        self.assertTrue(self.send("alarm", now=NOON))
        self.assertEqual(self.send("alarm", now=NIGHT).reason, "quiet hours")

    def test_quiet_hours_within_a_day(self):
        prefs = notify.Prefs(quiet_start="09:00", quiet_end="17:00")
        self.assertTrue(prefs.quiet_at(12 * 60))
        self.assertFalse(prefs.quiet_at(17 * 60))
        self.assertFalse(notify.Prefs(quiet_start="08:00", quiet_end="08:00").quiet_at(8 * 60))

    def test_a_damaged_prefs_file_falls_back_and_is_not_overwritten(self):
        notify.prefs_path().parent.mkdir(parents=True)
        notify.prefs_path().write_text("{nope", encoding="utf-8")
        self.assertTrue(self.send("alarm"))
        self.assertTrue(notify.load_prefs().warnings)
        with self.assertRaises(notify.NotifyError):
            notify.save_prefs(notify.Prefs())
        self.assertEqual(notify.prefs_path().read_text(encoding="utf-8"), "{nope")

    def test_bad_quiet_hours_are_ignored_with_a_warning(self):
        self.prefs({"quiet_hours": {"start": "25:00", "end": "08:00"}})
        prefs = notify.load_prefs()
        self.assertEqual(prefs.quiet_start, "")
        self.assertTrue(prefs.warnings)

    def test_prefs_round_trip(self):
        prefs = notify.Prefs(enabled=False, quiet_start="18:00", quiet_end="08:00",
                             apps={"odin": False}, events={"baldur.days_waiting": True})
        notify.save_prefs(prefs)
        again = notify.load_prefs()
        self.assertEqual((again.enabled, again.quiet_start, again.apps, again.events),
                         (False, "18:00", {"odin": False}, {"baldur.days_waiting": True}))


class RepeatTests(NotifyCase):
    def test_the_same_news_inside_the_window_is_shown_once(self):
        self.assertTrue(self.send(key="3", now=NOON))
        self.assertEqual(self.send(key="3", now=NOON + 600).reason, "already shown recently")
        self.assertEqual(len(self.shown), 1)

    def test_changed_news_or_a_later_time_is_shown_again(self):
        self.send(key="3", now=NOON)
        self.assertTrue(self.send(key="4", now=NOON + 600))
        self.assertTrue(self.send(key="3", now=NOON + 61 * 60))

    def test_events_without_a_repeat_window_always_show(self):
        self.send("alarm")
        self.assertTrue(self.send("alarm"))

    def test_a_failed_show_is_not_remembered(self):
        notify.set_backend(lambda *_: (_ for _ in ()).throw(notify.NotifyError("busy")))
        self.assertFalse(self.send(key="3"))
        notify.set_backend(lambda title, text, level: self.shown.append(text))
        self.assertTrue(self.send(key="3", now=NOON + 5))

    def test_a_damaged_state_file_does_not_stop_a_notification(self):
        notify.state_path().parent.mkdir(parents=True)
        notify.state_path().write_text("[[", encoding="utf-8")
        self.assertTrue(self.send(key="3"))


class BackendTests(unittest.TestCase):
    def test_the_environment_can_turn_it_off_or_to_the_log(self):
        saved = os.environ.get("ASGARD_NOTIFY")
        self.addCleanup(lambda: os.environ.pop("ASGARD_NOTIFY", None) if saved is None
                        else os.environ.__setitem__("ASGARD_NOTIFY", saved))
        os.environ["ASGARD_NOTIFY"] = "off"
        self.assertIsNone(notify.default_backend())
        os.environ["ASGARD_NOTIFY"] = "log"
        self.assertIs(notify.default_backend(), notify._log_only)

    def test_the_applescript_is_quoted(self):
        self.assertEqual(notify._applescript('a "b" \\c'), '"a \\"b\\" \\\\c"')


class CliTests(NotifyCase):
    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                code = notifycli.main(list(argv))
            except SystemExit as exc:
                code = exc.code
        return code, out.getvalue(), err.getvalue()

    def test_list_names_the_shipped_events(self):
        code, out, _ = self.run_cli("list")
        self.assertEqual(code, 0)
        self.assertIn("baldur.days_waiting", out)

    def test_off_and_on_by_app_and_event(self):
        self.assertEqual(self.run_cli("off", "odin")[0], 0)
        self.assertEqual(self.run_cli("off", "baldur.days_waiting")[0], 0)
        prefs = notify.load_prefs()
        self.assertEqual((prefs.apps, prefs.events), ({"odin": False}, {"baldur.days_waiting": False}))
        self.run_cli("on", "odin")
        self.assertEqual(notify.load_prefs().apps, {"odin": True})

    def test_an_unknown_target_says_what_exists(self):
        code, _, err = self.run_cli("off", "baldur.nope")
        self.assertEqual(code, 1)
        self.assertIn("days_waiting", err)

    def test_quiet_hours(self):
        self.assertEqual(self.run_cli("quiet", "18:00", "08:00")[0], 0)
        self.assertEqual(notify.load_prefs().quiet_start, "18:00")
        self.assertEqual(self.run_cli("quiet", "off")[0], 0)
        self.assertEqual(notify.load_prefs().quiet_start, "")
        self.assertEqual(self.run_cli("quiet", "6pm", "8am")[0], 1)

    def test_test_uses_the_backend_and_ignores_the_switches(self):
        notify.save_prefs(notify.Prefs(enabled=False))
        code, _, _ = self.run_cli("test")
        self.assertEqual(code, 0)
        self.assertEqual(len(self.shown), 1)


if __name__ == "__main__":
    unittest.main()
