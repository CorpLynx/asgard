"""The local pretend Jira (tools/fake_jira_server.py), driven by Odin's own JiraClient over real HTTP.

It exists so Odin can be tried end to end with no Jira. That is only worth anything if it answers the
way Odin expects, so these tests use Odin's client, not hand-written requests: create a sub-task, find
it again by its dedupe label, log time, transition it, read the worklogs back, and refuse a bad token.

The server binds 127.0.0.1 and needs its bearer token; both are checked here.
"""
import sys
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for folder in (ROOT, ROOT / "apps" / "odin", ROOT / "tools"):
    if str(folder) not in sys.path:
        sys.path.insert(0, str(folder))

import fake_jira_server as fjs  # noqa: E402
from odin.jira import JiraClient, JiraError  # noqa: E402

TOKEN = "test-token"


class ServerTests(unittest.TestCase):
    def setUp(self):
        self.jira = fjs.Jira(project="PROJ")
        fjs.seed(self.jira, "PROJ-100")
        fjs.Handler.jira, fjs.Handler.token, fjs.Handler.quiet = self.jira, TOKEN, True
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), fjs.Handler)
        self.jira.base_url = "http://127.0.0.1:%d" % self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop)
        self.client = JiraClient(self.jira.base_url, TOKEN)

    def stop(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=5)

    # ---- what `odin check` asks for
    def test_it_answers_the_setup_checks(self):
        self.assertEqual(self.client.server_info()["deploymentType"], "Server")
        self.assertEqual(self.client.myself()["name"], "jdoe")
        self.assertEqual(self.client.get_project("PROJ")["key"], "PROJ")
        self.assertTrue(self.client.personal_access_tokens())
        self.assertIn("Epic Link", [f["name"] for f in self.client.fields()])
        self.assertIn("Done", [s["name"] for s in self.client.statuses()])

    def test_the_project_offers_a_sub_task_type(self):
        """odin check looks for an issue type flagged subtask; without it, Odin can't create one."""
        types = [t["name"] for t in self.client.get_project("PROJ")["issueTypes"] if t.get("subtask")]
        self.assertEqual(types, ["Sub-task"])

    def test_it_makes_every_key_the_example_config_names(self):
        """A fresh `odin init` names ADMIN-7 and PROJ-200 in its example rules, so they exist here."""
        for key in ("PROJ-100", "PROJ-200", "ADMIN-7"):
            with self.subTest(key):
                self.assertTrue(self.client.get_issue(key)["fields"]["summary"])
        self.assertEqual(self.client.get_project("ADMIN")["key"], "ADMIN")

    # ---- what a push does
    def test_a_sub_task_is_created_found_by_its_label_logged_and_transitioned(self):
        key = self.client.create_issue({
            "project": {"key": "PROJ"}, "summary": "Meeting: Standup (2026-10-10 09:00)",
            "issuetype": {"name": "Sub-task"}, "parent": {"key": "PROJ-100"}, "labels": ["m2j-abc123"]})
        self.assertRegex(key, r"^PROJ-\d+$")
        fields = self.client.get_issue(key, fields="summary,issuetype,project,status,parent")["fields"]
        self.assertTrue(fields["issuetype"]["subtask"])
        self.assertEqual(fields["parent"]["key"], "PROJ-100")

        found = self.client.search_issue_keys('labels = "m2j-abc123" AND creator = currentUser() '
                                              "ORDER BY created DESC")
        self.assertEqual(found, [key], "the dedupe label must find exactly the sub-task it was put on")

        worklog_id = self.client.add_worklog(key, 1800, "2026-10-10T09:00:00.000+0000", "Meeting: Standup")
        self.assertTrue(worklog_id)
        logged = self.client.issue_worklogs(key)
        self.assertEqual([w["timeSpentSeconds"] for w in logged], [1800])
        self.assertEqual(logged[0]["author"]["name"], "jdoe")

        self.assertTrue(self.client.transition(key, "Done"))
        self.assertEqual(self.client.get_issue(key)["fields"]["status"]["name"], "Done")
        self.assertEqual(self.client.get_issue(key)["fields"]["status"]["statusCategory"]["key"], "done")

    def test_a_sub_task_under_a_parent_that_does_not_exist_is_refused(self):
        with self.assertRaises(JiraError) as caught:
            self.client.create_issue({"project": {"key": "PROJ"}, "summary": "No parent",
                                      "issuetype": {"name": "Sub-task"}, "parent": {"key": "PROJ-9999"}})
        self.assertIn("doesn't exist", str(caught.exception))

    # ---- what a sync reads
    def test_the_jql_odin_sends_comes_back_with_the_right_issues(self):
        pages = list(self.client.search("assignee was currentUser() ORDER BY updated ASC", changelog=True))
        mine = [i["key"] for page in pages for i in page]
        self.assertIn("PROJ-101", mine)
        self.assertTrue(all("changelog" in i for page in pages for i in page))

        self.client.add_worklog("PROJ-101", 600, "2026-10-10T10:00:00.000+0000", "work")
        pages = list(self.client.search("worklogAuthor = currentUser() ORDER BY updated ASC"))
        self.assertIn("PROJ-101", [i["key"] for page in pages for i in page])

        pages = list(self.client.search("parent = PROJ-100 ORDER BY updated ASC"))
        self.assertEqual([i["key"] for page in pages for i in page], [])

    def test_an_unknown_issue_is_a_404_so_odin_can_tell_it_is_gone(self):
        with self.assertRaises(JiraError) as caught:
            self.client.get_issue("PROJ-4242")
        self.assertEqual(caught.exception.status, 404)

    # ---- the guards
    def test_the_wrong_token_is_refused(self):
        with self.assertRaises(JiraError) as caught:
            JiraClient(self.jira.base_url, "not-the-token").myself()
        self.assertEqual(caught.exception.status, 401)

    def test_it_listens_on_loopback_only(self):
        self.assertEqual(self.httpd.server_address[0], "127.0.0.1")

    def test_what_it_keeps_survives_a_restart(self):
        key = self.client.create_issue({"project": {"key": "PROJ"}, "summary": "Keep me",
                                        "issuetype": {"name": "Task"}})
        again = fjs.Jira()
        again.load(self.jira.dump())
        self.assertIn(key, again.issues)
        self.assertEqual(again.projects, self.jira.projects)
        self.assertEqual(again.next_worklog, self.jira.next_worklog)


if __name__ == "__main__":
    unittest.main()
