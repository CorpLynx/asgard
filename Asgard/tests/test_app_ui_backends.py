"""Baldur's and Ysildir's pages, as plain Python: the data each view asks for, with no Qt.

Both apps joined the shared window in Oct 2026, so the sidebar lists all four. The window itself is in
test_ui_qt.py; what these check is the layer underneath, which is where the behaviour lives:
- Baldur reads through desk.py and never estimates here; approving is offered, posting is Odin's.
- Ysildir's page works on a Python without the MCP SDK, which is exactly when it has something to say.
"""
import datetime as dt
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
for folder in (ROOT, ROOT / "apps" / "baldur", ROOT / "apps" / "ysildir"):
    if str(folder) not in sys.path:
        sys.path.insert(0, str(folder))

from asgard import muninn  # noqa: E402
from asgard.ui import registry  # noqa: E402


class Home(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name).resolve()
        self._saved = os.environ.get("ASGARD_HOME")
        os.environ["ASGARD_HOME"] = str(self.dir)
        self.addCleanup(self.restore)

    def restore(self):
        if self._saved is None:
            os.environ.pop("ASGARD_HOME", None)
        else:
            os.environ["ASGARD_HOME"] = self._saved


class ManifestTests(unittest.TestCase):
    def test_all_four_apps_offer_views_the_window_can_load(self):
        apps, warnings = registry.discover(ROOT)
        self.assertEqual(warnings, [])
        self.assertEqual([a.id for a in apps], ["baldur", "heimdall", "odin", "ysildir"])
        for app in apps:
            with self.subTest(app.id):
                self.assertTrue(app.views, app.id)
                self.assertTrue(app.backend, f"{app.id} needs a backend for its pages")
                for view in app.views:
                    self.assertTrue(view.qml.is_file(), view.qml)

    def test_each_manifest_names_a_backend_that_imports(self):
        for app in registry.discover(ROOT)[0]:
            with self.subTest(app.id):
                backend = app.load_backend()
                self.assertTrue(hasattr(backend, "dashboard"), f"{app.id} backend needs dashboard()")


class BaldurBackendTests(Home):
    def setUp(self):
        super().setUp()
        muninn.prepare()
        from baldur import ui_backend
        self.b = ui_backend.Backend()

    def set_up_baldur(self):
        """Who you are and which project counts: Baldur refuses to estimate without both."""
        con = muninn.open_app("baldur", supported=(1, muninn.SCHEMA_VERSION))
        try:
            muninn.add_identity(con, "git_email", "dev@agency.gov")
        finally:
            con.close()
        path = self.dir / "settings" / "baldur.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"project_keys": ["PROJ"]}), encoding="utf-8")

    def test_a_week_with_nothing_in_it_still_lists_seven_days(self):
        week = self.b.week()
        self.assertEqual(len(week["days"]), 7)
        self.assertTrue(week["is_this_week"])
        self.assertEqual(week["total_text"], "0m")
        self.assertEqual([d["dev"] for d in week["days"]], [0] * 7)
        self.assertEqual(week["policy"], "ambient")
        self.assertTrue(any(d["today"] for d in week["days"]))

    def test_stepping_weeks_moves_by_seven_days(self):
        week = self.b.week()
        monday = dt.date.fromisoformat(week["monday"])
        self.assertEqual(monday.weekday(), 0)
        self.assertEqual(dt.date.fromisoformat(week["previous"]), monday - dt.timedelta(days=7))
        back = self.b.week(week["previous"])
        self.assertFalse(back["is_this_week"])
        self.assertEqual(back["monday"], week["previous"])

    def test_a_bad_date_says_what_a_date_looks_like(self):
        with self.assertRaisesRegex(ValueError, "isn't a date like"):
            self.b.week("last tuesday")

    def test_the_day_page_opens_at_the_day_the_week_page_chose(self):
        """The shell keeps one backend per app, so the choice survives moving between pages."""
        wanted = (dt.date.today() - dt.timedelta(days=2)).isoformat()
        self.assertEqual(self.b.choose_day(wanted)["date"], wanted)
        self.assertEqual(self.b.day()["date"], wanted)
        self.assertEqual(self.b.day("")["date"], wanted)                  # "" means "the chosen one"
        other = (dt.date.today() - dt.timedelta(days=3)).isoformat()
        self.assertEqual(self.b.day(other)["date"], other)                # and asking moves it
        self.assertEqual(self.b.day()["date"], other)

    def test_pages_can_be_read_from_a_worker_thread_after_the_ui_thread(self):
        """The shell calls one backend from the UI thread and from workers (Dashboard, callAsync); a
        connection kept from the first call is refused on the next thread."""
        import threading
        self.b.week()
        problems = []

        def work():
            try:
                self.b.week()
                self.b.day()
                self.b.reviews()
            except Exception as exc:                        # noqa: BLE001 - the test reports it
                problems.append(exc)

        t = threading.Thread(target=work)
        t.start()
        t.join()
        self.assertEqual(problems, [])

    def test_an_empty_day_offers_nothing_to_approve(self):
        self.set_up_baldur()
        day = self.b.day(dt.date.today().isoformat())
        self.assertEqual(day["tickets"], [])
        self.assertFalse(day["approvable"])
        self.assertFalse(day["has_approved"])
        self.assertIsNone(day["ai"])
        self.assertIn("development time", day["timesheet"])
        self.assertEqual(self.b.approve(day["date"])["message"],
                         "Nothing on that day was waiting to be approved.")
        self.assertEqual(self.b.reject(day["date"])["count"], 0)

    def test_approving_before_setup_says_what_to_set_up(self):
        """Baldur's own refusals reach the page as their own words, not as "unexpected problem"."""
        with self.assertRaisesRegex(ValueError, "which commits are yours"):
            self.b.approve(dt.date.today().isoformat())

    def test_collect_refuses_before_you_say_where_your_repositories_are(self):
        with self.assertRaisesRegex(ValueError, "where your repositories are"):
            self.b.collect()

    def test_estimating_before_setup_says_what_to_set_up(self):
        with self.assertRaisesRegex(ValueError, "which commits are yours"):
            self.b.estimate(dt.date.today().isoformat())

    def test_the_dashboard_counts_days_to_review(self):
        cards = self.b.dashboard()
        self.assertEqual(cards[0]["label"], "Days to review this week")
        self.assertEqual(cards[0]["value"], "0")
        self.assertEqual(cards[0]["view"], "week")

    def test_broken_settings_are_a_message_not_a_traceback(self):
        path = self.dir / "settings" / "baldur.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{ not json", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "has a mistake in it"):
            self.b.week()

    def test_without_muninn_the_page_gets_muninns_own_message(self):
        """Not a ValueError from Muninn, so the backend turns it into one: the page shows the real text."""
        from baldur import ui_backend
        fresh = Path(self.tmp.name) / "empty"
        with mock.patch.dict(os.environ, {"ASGARD_HOME": str(fresh)}):
            with self.assertRaisesRegex(ValueError, "Open Asgard once"):
                ui_backend.Backend().week()
            self.assertEqual(ui_backend.Backend().dashboard()[0]["tone"], "error")


class SettingsContractTests(Home):
    """The Settings page builds each app's section from what its backend declares (registry.py)."""

    KINDS = {"text", "number", "bool", "choice", "list", "time"}

    def backends(self):
        out = []
        for app in registry.discover(ROOT)[0]:
            backend = app.load_backend()
            if hasattr(backend, "settings_schema"):
                out.append((app.id, backend))
        return out

    def test_odin_and_baldur_both_offer_a_section(self):
        self.assertEqual([app for app, _ in self.backends()], ["baldur", "odin"])

    def test_every_declared_field_is_one_the_page_can_draw(self):
        for app, backend in self.backends():
            for group in backend.settings_schema():
                self.assertTrue(group["title"], app)
                for field in group["fields"]:
                    with self.subTest(app=app, key=field.get("key")):
                        self.assertIn(field["kind"], self.KINDS)
                        self.assertTrue(field["label"])
                        self.assertTrue(field["key"])
                        if field["kind"] == "number":
                            self.assertIn("min", field)
                            self.assertIn("max", field)
                            self.assertLess(field["min"], field["max"])
                        if field["kind"] == "choice":
                            self.assertTrue(field["options"])
                            for option in field["options"]:
                                self.assertIn("value", option)
                                self.assertIn("label", option)

    def test_every_field_has_a_value_to_show_before_anything_is_set_up(self):
        """With no settings file yet, the page shows the app's defaults instead of blanks."""
        for app, backend in self.backends():
            values = backend.settings_values()
            for group in backend.settings_schema():
                for field in group["fields"]:
                    with self.subTest(app=app, key=field["key"]):
                        self.assertIn(field["key"], values)

    def test_a_setting_the_window_does_not_edit_is_refused(self):
        for app, backend in self.backends():
            with self.subTest(app):
                reply = backend.set_settings({"something.invented": 1})
                self.assertIn("isn't a setting", reply["error"])

    def test_baldur_saves_numbers_choices_and_lists(self):
        from baldur import ui_backend
        b = ui_backend.Backend()
        self.assertEqual(b.set_settings({"idle_gap_minutes": "90", "policy": "overlap",
                                         "project_keys": ["proj", " ", "ops"]}), {"message": "Saved."})
        values = b.settings_values()
        self.assertEqual(values["idle_gap_minutes"], 90)
        self.assertEqual(values["policy"], "overlap")
        self.assertEqual(values["project_keys"], ["OPS", "PROJ"])          # Baldur normalises keys

    def test_baldur_keeps_the_neighbours_of_a_nested_setting(self):
        from baldur import ui_backend
        b = ui_backend.Backend()
        before = b.settings_values()["tour_of_duty.unpaid_minutes"]
        self.assertEqual(b.set_settings({"tour_of_duty.start": "08:15"}), {"message": "Saved."})
        self.assertEqual(b.settings_values()["tour_of_duty.start"], "08:15")
        self.assertEqual(b.settings_values()["tour_of_duty.unpaid_minutes"], before)

    def test_baldur_refuses_a_bad_value_with_its_own_words(self):
        from baldur import ui_backend
        b = ui_backend.Backend()
        self.assertIn("policy must be one of", b.set_settings({"policy": "nonsense"})["error"])
        self.assertEqual(b.settings_values()["policy"], "ambient")

    def test_baldur_never_offers_the_review_mode_it_refuses_to_use(self):
        """content mode would mean sending code off the machine, so it isn't in the window at all."""
        from baldur import ui_backend
        options = [o["value"] for g in ui_backend.Backend().settings_schema()
                   for f in g["fields"] if f["key"] == "review_mode" for o in f["options"]]
        self.assertEqual(options, ["off", "metadata"])

    def test_odin_writes_the_file_and_keeps_its_comments(self):
        from odin import ui_backend
        b = ui_backend.Backend()
        b.init_config()
        path = Path(b.settings_files()[0]["path"])
        self.assertEqual(b.set_settings({"jira.base_url": "https://jira.example.gov",
                                         "muninn.post_approved": False,
                                         "filters.min_minutes": "10"}), {"message": "Saved."})
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
        self.assertTrue([k for k in raw if k.startswith("_")], "the file's comments must survive a save")
        self.assertIs(raw["muninn"]["post_approved"], False)
        values = b.settings_values()
        self.assertEqual(values["jira.base_url"], "https://jira.example.gov")
        self.assertEqual(values["filters.min_minutes"], 10)

    def test_odin_refuses_a_bad_value_and_writes_nothing(self):
        from odin import ui_backend
        b = ui_backend.Backend()
        b.init_config()
        path = Path(b.settings_files()[0]["path"])
        before = path.read_text(encoding="utf-8-sig")
        reply = b.set_settings({"jira.base_url": "ftp://nope"})
        self.assertIn("https://", reply["error"])
        self.assertEqual(path.read_text(encoding="utf-8-sig"), before)

    def test_odin_can_write_the_first_settings_before_a_config_exists(self):
        from odin import ui_backend
        b = ui_backend.Backend()
        self.assertEqual(b.set_settings({"jira.default_parent": "PROJ-100",
                                         "jira.base_url": "https://jira.example.gov"}),
                         {"message": "Saved."})
        self.assertEqual(b.settings_values()["jira.default_parent"], "PROJ-100")

    def test_an_empty_box_unsets_the_keys_that_mean_nothing_when_empty(self):
        from odin import ui_backend
        b = ui_backend.Backend()
        b.init_config()
        b.set_settings({"jira.base_url": "https://jira.example.gov", "jira.transition_to": "Done"})
        self.assertEqual(b.settings_values()["jira.transition_to"], "Done")
        self.assertEqual(b.set_settings({"jira.transition_to": ""}), {"message": "Saved."})
        self.assertEqual(b.settings_values()["jira.transition_to"], "")

    def test_settings_survive_being_written_from_several_threads(self):
        """The Dashboard reads on a worker thread while the Settings page writes; one fixed temp
        filename would have one rename a file the other already took."""
        import threading
        from baldur import ui_backend
        problems = []

        def work(value):
            try:
                ui_backend.Backend().set_settings({"history_days": value})
            except Exception as exc:                        # noqa: BLE001 - the test reports it
                problems.append(exc)

        threads = [threading.Thread(target=work, args=(100 + n,)) for n in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(problems, [])
        self.assertIn(ui_backend.Backend().settings_values()["history_days"], range(100, 106))


class YsildirBackendTests(Home):
    def setUp(self):
        super().setUp()
        from ysildir import ui_backend
        self.b = ui_backend.Backend()

    def test_it_lists_every_tool_with_what_it_sends(self):
        from ysildir import switches
        reply = self.b.tools()
        rows = reply["value"]
        self.assertEqual([r["name"] for r in rows], [s.name for s in switches.SWITCHES])
        self.assertEqual(reply["total"], len(switches.SWITCHES))
        for row in rows:
            with self.subTest(row["name"]):
                self.assertTrue(row["sends"], "every tool says what it sends: that's what the ISSO approves")
        guide = next(r for r in rows if r["name"] == switches.ALWAYS_ON)
        self.assertTrue(guide["always_on"])

    def test_the_tools_that_send_muninns_data_start_off(self):
        rows = {r["name"]: r for r in self.b.tools()["value"]}
        self.assertTrue(rows["asgard_guide"]["on"])
        for name in ("baldur_day", "baldur_review_pack", "muninn_search", "muninn_issue"):
            with self.subTest(name):
                self.assertFalse(rows[name]["on"], f"{name} sends Muninn's data, so it starts off")

    def test_a_saved_switch_file_is_read_back(self):
        path = self.dir / "settings" / "ysildir.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"version": 1, "tools": {"muninn_search": True}}), encoding="utf-8")
        rows = {r["name"]: r for r in self.b.tools()["value"]}
        self.assertTrue(rows["muninn_search"]["on"])
        self.assertEqual(self.b.tools()["path"], str(path))

    def test_a_broken_switch_file_is_reported_not_overwritten(self):
        path = self.dir / "settings" / "ysildir.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{ not json", encoding="utf-8")
        reply = self.b.tools()
        self.assertTrue(reply["problem"])
        self.assertEqual(path.read_text(encoding="utf-8"), "{ not json")

    def test_it_says_whether_the_sdk_is_there(self):
        sdk = self.b.sdk()
        self.assertEqual(sorted(sdk), ["missing", "needs", "python", "ready", "version"])
        self.assertEqual(sdk["ready"], not sdk["missing"])

    def test_the_setup_command_for_each_client_names_ysildirs_cli(self):
        rows = self.b.clients()
        self.assertEqual([r["id"] for r in rows], ["kiro", "vscode", "claude"])
        for row in rows:
            with self.subTest(row["id"]):
                self.assertIn("setup --" + row["id"], row["command"])
                self.assertIn("cli.py", row["command"])

    def test_the_dashboard_says_how_many_tools_are_on_or_what_is_missing(self):
        cards = self.b.dashboard()
        self.assertEqual(len(cards), 1)
        self.assertEqual(cards[0]["view"], "tools")
        if self.b.sdk()["ready"]:
            self.assertIn(" of ", cards[0]["value"])
        else:
            self.assertEqual(cards[0]["tone"], "warning")

    def test_switching_a_tool_without_the_sdk_says_which_command_to_use(self):
        if self.b.sdk()["ready"]:
            self.skipTest("the SDK is installed here, so the page can write the file")
        reply = self.b.set_tool("muninn_search", True)
        self.assertIn("ysildir.cmd tools --on", reply["error"])


if __name__ == "__main__":
    unittest.main()
