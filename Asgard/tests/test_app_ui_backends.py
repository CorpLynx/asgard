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
import threading
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
for folder in (ROOT, ROOT / "apps" / "baldur", ROOT / "apps" / "ysildir"):
    if str(folder) not in sys.path:
        sys.path.insert(0, str(folder))

from asgard import muninn  # noqa: E402
from asgard.muninn import baldur as approvals  # noqa: E402
from asgard.ui import registry  # noqa: E402

sys.path.insert(0, str(ROOT / "tests"))
import test_baldur as tb  # noqa: E402  (its fixtures; a module attribute, so its tests aren't run twice)


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
        self.addCleanup(self.b.close)       # before the temporary folder goes: Windows won't delete an open file

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

    def test_reading_leaves_no_connection_open(self):
        """Windows can't delete or replace an open file, so a read must not keep muninn.db open.

        A held connection broke the temporary folder every test runs in (the Windows build saw
        PermissionError: WinError 32 on cleanup) and would block an Asgard upgrade the same way.
        """
        import gc
        import sqlite3 as sqlite
        self.set_up_baldur()
        self.b.week()
        self.b.day()
        self.b.reviews()
        self.b.dashboard()
        gc.collect()
        still_open = []
        for obj in gc.get_objects():
            if isinstance(obj, sqlite.Connection):
                try:
                    obj.execute("SELECT 1")
                except sqlite.ProgrammingError:
                    continue                    # already closed
                except sqlite.Error:
                    continue                    # someone else's, in a state we can't read
                still_open.append(obj)
        self.assertEqual(still_open, [], "the backend is holding a Muninn connection open")

    def test_reads_work_from_several_threads(self):
        """sqlite3 refuses a connection on another thread, so each read opens its own."""
        self.set_up_baldur()
        problems = []

        def work():
            try:
                self.b.week()
                self.b.day()
            except Exception as exc:            # noqa: BLE001 - the test reports it
                problems.append(exc)

        threads = [threading.Thread(target=work) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(problems, [])

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


class BaldurFigureTests(tb.MuninnCase):
    """Typing a figure on the day page: staged until you approve, or a change to one you approved.

    The worked example from test_baldur.py (PROJ-42 1h30m, PROJ-51 30m on 1 Oct 2026), read through
    the backend the page uses, with the settings in the file the backend reads.
    """

    def setUp(self):
        super().setUp()
        path = self.dir / "settings" / "baldur.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"project_keys": ["PROJ", "OPS"], "history_days": 3650}), encoding="utf-8")
        self.worked_example()
        from baldur import ui_backend
        self.b = ui_backend.Backend()
        self.addCleanup(self.b.close)
        self.date = tb.DAY.isoformat()

    def approved(self):
        return {r["work_item_key"]: r["minutes_final"] for r in self.rows("approved")}

    def ticket(self, key):
        return next(t for t in self.b.day(self.date)["tickets"] if t["key"] == key)

    def test_a_read_on_another_thread_gets_its_own_connection(self):
        """Regression: one cached read connection, opened by the shell's dashboard on a pool thread, was
        then used by the day page on the window's thread, and sqlite3 refused it (ProgrammingError)."""
        import threading
        errors = []

        def work():
            try:
                self.b.dashboard()
                self.b.close()
            except Exception as exc:  # noqa: BLE001 - recorded for the assertion
                errors.append(exc)

        worker = threading.Thread(target=work)
        worker.start()
        worker.join()
        self.assertEqual(errors, [])
        self.b.dashboard()                          # opens on the worker first, as in the window
        other = threading.Thread(target=lambda: errors.extend(
            [r for r in [self.b.dashboard()] if r[0].get("tone") == "error"]))
        other.start()
        other.join()
        self.assertEqual(errors, [], "a page on another thread reads fine")
        self.assertEqual([t["key"] for t in self.b.day(self.date)["tickets"]], ["PROJ-42", "PROJ-51"])

    def test_what_you_type_is_parsed_by_the_clis_own_rules(self):
        for text, minutes in (("1h15m", 75), ("75", 75), ("1:15", 75), (" 45m ", 45)):
            self.assertEqual(self.b.parse_minutes(text)["minutes"], minutes, text)
        self.assertEqual(self.b.parse_minutes("75")["text"], "1h15m")
        for bad, words in (("an hour", "isn't a length of time"), ("25h", "more than a day"), ("", "length of time")):
            with self.assertRaisesRegex(ValueError, words):
                self.b.parse_minutes(bad)

    def test_a_staged_figure_shows_on_the_day_and_is_what_gets_approved(self):
        self.assertEqual(self.ticket("PROJ-42")["edit"], "stage")
        self.assertTrue(self.b.day(self.date)["approvable"], "before Estimate has stored it, as the old window")
        reply = self.b.stage_figure(self.date, "proj-42", "1h45m")
        self.assertEqual((reply["key"], reply["minutes"]), ("PROJ-42", 105))
        self.assertEqual(self.b.staged(self.date), {"PROJ-42": 105})
        row = self.ticket("PROJ-42")
        self.assertEqual((row["staged"], row["staged_text"], row["figure"]), (105, "1h45m", 90),
                         "the estimate stays as it was; the typed figure is beside it")
        self.assertEqual(self.ticket("PROJ-51")["staged"], None)
        self.assertEqual(self.rows("approved"), [], "staging decides nothing")
        self.assertEqual(self.b.approve(self.date)["count"], 2)
        self.assertEqual(self.approved(), {"PROJ-42": 105, "PROJ-51": 30})
        self.assertEqual(self.b.staged(self.date), {}, "cleared once the day is approved")

    def test_staged_figures_survive_moving_between_pages(self):
        """The shell keeps one backend per app; the page is rebuilt, the backend isn't."""
        self.b.stage_figure(self.date, "PROJ-51", "20m")
        self.b.week(self.date)
        self.b.day(tb.NEXT.isoformat())
        self.assertEqual(self.b.staged(self.date), {"PROJ-51": 20})
        self.assertEqual(self.b.staged(tb.NEXT.isoformat()), {})

    def test_clearing_a_staged_figure_goes_back_to_the_estimate(self):
        self.b.stage_figure(self.date, "PROJ-42", "2h")
        self.assertTrue(self.b.clear_figure(self.date, "PROJ-42")["cleared"])
        self.assertFalse(self.b.clear_figure(self.date, "PROJ-42")["cleared"])
        self.b.approve(self.date)
        self.assertEqual(self.approved(), {"PROJ-42": 90, "PROJ-51": 30})

    def test_typing_the_estimate_again_stages_nothing(self):
        self.b.stage_figure(self.date, "PROJ-42", "2h")
        self.assertIsNone(self.b.stage_figure(self.date, "PROJ-42", "90")["minutes"])
        self.assertEqual(self.b.staged(self.date), {})

    def test_a_bad_figure_or_ticket_is_refused_and_nothing_is_staged(self):
        self.b.stage_figure(self.date, "PROJ-42", "1h")
        for key, text, words in (("PROJ-42", "soon", "isn't a length of time"),
                                 ("PROJ-42", "26h", "more than a day"),
                                 ("PROJ-99", "1h", "PROJ-99 has no time"),
                                 ("not a key", "1h", "isn't a Jira key")):
            with self.subTest(key=key, text=text), self.assertRaisesRegex(ValueError, words):
                self.b.stage_figure(self.date, key, text)
        self.assertEqual(self.b.staged(self.date), {"PROJ-42": 60}, "a refusal keeps what was there")

    def test_a_staged_figure_counts_with_the_ai_figures_too(self):
        """Approving the AI-assisted figures still takes what you typed for its ticket (desk.approve_day)."""
        self.b.stage_figure(self.date, "PROJ-51", "15m")
        with mock.patch("baldur.desk.assist.approve_day", return_value=[1, 2]) as approve:
            self.b.approve(self.date, "digest")
        self.assertEqual(approve.call_args[0][3], {"PROJ-51": 15})
        self.assertEqual(approve.call_args[1]["shown"], "digest")

    def test_a_staged_figure_for_a_ticket_decided_elsewhere_is_said_not_swallowed(self):
        """Regression: approve() matched "nothing to approve" anywhere in the message, so desk's refusal
        "PROJ-42 has nothing to approve" (a typed figure that can't be used) read as "Nothing on that day
        was waiting", while PROJ-51 was waiting."""
        self.b.stage_figure(self.date, "PROJ-42", "1h45m")
        self.run_store()                                       # the CLI stores the estimate...
        approvals.approve(self.con, self.open_ids()["PROJ-42"])  # ...and approves PROJ-42 alone
        with self.assertRaisesRegex(ValueError, "PROJ-42 has nothing to approve"):
            self.b.approve(self.date)
        self.assertEqual(self.approved(), {"PROJ-42": 90}, "nothing more approved when one figure can't be")
        self.assertEqual(self.b.staged(self.date), {"PROJ-42": 105}, "refused, so still there to see")
        self.assertEqual(self.ticket("PROJ-42")["edit"], "change")
        self.assertEqual(self.b.staged(self.date), {}, "reloading the day drops a figure that can't be approved")
        self.assertEqual(self.b.approve(self.date)["count"], 1)
        self.assertEqual(self.approved(), {"PROJ-42": 90, "PROJ-51": 30})

    def test_changing_an_approved_figure_supersedes_it(self):
        self.b.approve(self.date)
        row = self.ticket("PROJ-51")
        self.assertEqual((row["edit"], row["approved"]), ("change", 30))
        self.assertNotIn("approved_id", row, "the id stays in Python")
        with self.assertRaisesRegex(ValueError, "already approved at 30m. Change it instead"):
            self.b.stage_figure(self.date, "PROJ-51", "45m")
        reply = self.b.change_approved(self.date, "PROJ-51", "45m")
        self.assertEqual(reply["count"], 1)
        self.assertIn("Odin posts the difference", reply["message"])
        self.assertEqual(self.approved(), {"PROJ-42": 90, "PROJ-51": 45})
        self.assertEqual(len(self.rows("superseded")), 1, "the old decision is kept, not edited")
        self.assertEqual(self.b.change_approved(self.date, "PROJ-51", "45")["count"], 0, "same figure: no new row")
        self.assertEqual(len(self.rows("superseded")), 1)

    def test_a_figure_that_was_never_approved_cant_be_changed(self):
        with self.assertRaisesRegex(ValueError, "no approved figure"):
            self.b.change_approved(self.date, "PROJ-42", "1h")
        self.assertEqual(self.rows("approved"), [])

    def test_a_person_may_type_a_bigger_number(self):
        """Biasing down binds the code, not the person: their figure is their decision."""
        self.b.stage_figure(self.date, "PROJ-42", "3h")
        self.b.approve(self.date)
        self.assertEqual(self.approved()["PROJ-42"], 180)

    def test_rejecting_a_day_drops_its_typed_figures(self):
        self.b.stage_figure(self.date, "PROJ-42", "1h")
        self.assertEqual(self.b.reject(self.date)["count"], 2)
        self.assertEqual(self.b.staged(self.date), {})


class BaldurGitHubTests(tb.MuninnCase):
    """The week page's timer: one GitHub pass through callAsync, never loud."""

    def setUp(self):
        super().setUp()
        self.settings_file = self.dir / "settings" / "baldur.json"
        self.settings_file.parent.mkdir(parents=True, exist_ok=True)
        from baldur import ui_backend
        self.b = ui_backend.Backend()
        self.addCleanup(self.b.close)

    def write(self, **values):
        self.settings_file.write_text(json.dumps(dict({"project_keys": ["PROJ"]}, **values)), encoding="utf-8")

    def test_github_off_is_a_state_not_an_error(self):
        self.write(poll_minutes=7)
        reply = self.b.sync_github()
        self.assertEqual((reply["state"], reply["poll_minutes"]), ("off", 7))
        self.assertFalse(self.b.week()["github"])
        self.assertEqual(self.b.week()["poll_minutes"], 7)

    def test_no_token_says_how_to_save_one(self):
        self.write(github_api="github.agency.gov")
        from baldur import github
        with mock.patch.object(github, "load_token", return_value=None):
            reply = self.b.sync_github()
        self.assertEqual(reply["state"], "no token")
        self.assertIn("github token", reply["message"])

    def test_github_out_of_reach_is_quiet(self):
        """A failure is a line beside the list, never a raise: estimates and approvals carry on."""
        self.write(github_api="github.agency.gov")
        from baldur import github
        with mock.patch.object(github, "load_token", return_value="t"), \
                mock.patch.object(github, "sync", side_effect=github.GitHubError("Couldn't reach github.agency.gov")):
            reply = self.b.sync_github()
        self.assertEqual(reply["state"], "failed")
        self.assertIn("Couldn't reach github.agency.gov", reply["message"])

    def test_opening_the_page_again_soon_reuses_the_last_pass(self):
        self.write(github_api="github.agency.gov", poll_minutes=5)
        from baldur import github
        with mock.patch.object(github, "load_token", return_value=None) as token:
            first = self.b.sync_github()
            self.assertEqual(self.b.sync_github(), first)
            self.assertEqual(token.call_count, 1, "one pass within poll_minutes")
            self.b.sync_github(force=True)
            self.assertEqual(token.call_count, 2)

    def test_a_pass_against_the_fake_server_fills_the_list(self):
        sys.path.insert(0, str(ROOT / "tests"))
        import fake_github
        server = fake_github.FakeGitHub()
        self.addCleanup(server.close)
        self.con.execute("UPDATE repos SET github_repo = 'csb/asgard' WHERE id = ?", (self.repo,))
        server.pulls["csb/asgard"] = [fake_github.pr(8, "Session timeout", "sam", "feature/PROJ-51-timeout",
                                                     requested=("bdoe",))]
        server.requested = [("csb/asgard", 8)]
        self.write(github_api="github.agency.gov")
        from baldur import github
        real_client = github.Client
        with mock.patch.object(github, "load_token", return_value=fake_github.TOKEN), \
                mock.patch.object(github, "Client", side_effect=lambda api, token: real_client(server.api, token)):
            reply = self.b.sync_github()
        self.assertEqual((reply["state"], reply["problems"]), ("fresh", []))
        self.assertEqual([r["label"] for r in self.b.reviews()], ["csb/asgard#8"])


class SettingsContractTests(Home):
    """The Settings page builds each app's section from what its backend declares (registry.py)."""

    KINDS = {"text", "number", "bool", "choice", "list", "time", "secret"}

    def backends(self):
        out = []
        for app in registry.discover(ROOT)[0]:
            backend = app.load_backend()
            if hasattr(backend, "settings_schema"):
                out.append((app.id, backend))
        return out

    def test_odin_baldur_and_heimdall_all_offer_a_section(self):
        self.assertEqual([app for app, _ in self.backends()], ["baldur", "heimdall", "odin"])

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

    def test_no_app_ever_hands_a_secret_back_to_the_window(self):
        """A secret field is write-only: settings_values says "saved" or "", never the secret itself."""
        for app, backend in self.backends():
            secrets_declared = [f["key"] for g in backend.settings_schema() for f in g["fields"]
                                if f["kind"] == "secret"]
            values = backend.settings_values()
            for key in secrets_declared:
                with self.subTest(app=app, key=key):
                    self.assertIn(values[key], ("", "saved"))

    def test_heimdall_saves_the_instance_and_the_user_name_but_never_the_password(self):
        from heimdall import ui_backend
        b = ui_backend.Backend()
        b.init_form()
        path = Path(b.settings_files()[0]["path"])
        self.assertEqual(b.set_settings({"instance_url": "https://dev99.service-now.com", "username": "bdoe",
                                         "ui": "portal", "attempts": "4"}), {"message": "Saved."})
        values = b.settings_values()
        self.assertEqual(values["instance_url"], "https://dev99.service-now.com")
        self.assertEqual((values["username"], values["ui"], values["attempts"]), ("bdoe", "portal", 4))
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
        self.assertTrue([k for k in raw if k.startswith("_")], "the file's comments must survive a save")
        self.assertNotIn("password", raw)

        # The password goes to the secret store, which off Windows is an environment variable.
        from asgard import secrets as store
        name = store.env_name("heimdall", "dev99.service-now.com")
        self.assertEqual(b.settings_values()["password"], "")
        os.environ[name] = "sekrit"
        self.addCleanup(lambda: os.environ.pop(name, None))
        self.assertEqual(b.settings_values()["password"], "saved")
        self.assertNotIn("sekrit", path.read_text(encoding="utf-8-sig"))
        self.assertNotIn("sekrit", json.dumps(b.settings_values()))
        self.assertNotIn("sekrit", json.dumps(b.credentials()))

    def test_heimdall_refuses_a_bad_instance_address_and_writes_nothing(self):
        from heimdall import ui_backend
        b = ui_backend.Backend()
        b.init_form()
        path = Path(b.settings_files()[0]["path"])
        before = path.read_text(encoding="utf-8-sig")
        self.assertIn("https://", b.set_settings({"instance_url": "ftp://nope"})["error"])
        self.assertEqual(path.read_text(encoding="utf-8-sig"), before)
        self.assertIn("whole number", b.set_settings({"attempts": "many"})["error"])
        self.assertIn("isn't a setting", b.set_settings({"fields": []})["error"])

    def test_heimdall_needs_the_address_before_a_password_and_leaves_an_empty_box_alone(self):
        from heimdall import ui_backend
        b = ui_backend.Backend()
        b.init_form()
        b.set_settings({"instance_url": "https://dev99.service-now.com"})
        self.assertEqual(b.set_settings({"password": ""}), {"message": "Nothing to change."})
        reply = b.set_settings({"password": " spaced "})
        self.assertIn("starts or ends with a space", reply["error"])
        if os.name != "nt":            # no Credential Manager here, which is itself worth saying clearly
            self.assertIn("ASGARD_SECRET_HEIMDALL", b.set_settings({"password": "pw"})["error"])

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
        self.assertIn("asgard ysildir tools --on", reply["error"])


if __name__ == "__main__":
    unittest.main()
