"""A pretend Jira Data Center on your own machine, so Odin can be tried end to end with no real Jira.

    python tools/fake_jira_server.py                  # start it, print the config to paste
    python tools/fake_jira_server.py --state jira.json  # keep what it creates across restarts

It serves the endpoints Odin uses (see apps/odin/odin/jira.py): server info, myself, fields, statuses,
a project, issues, JQL search, worklogs, transitions, and the personal-access-token list. Odin can
then create sub-tasks, log time, transition them, and read issues and worklogs back, exactly as it
would against Jira Data Center.

Development tool. Never shipped, never part of the packaged build, and deliberately limited:
- It binds 127.0.0.1 only, so nothing off this machine can reach it. Any other address is refused.
- It needs the bearer token it prints; anything else gets a 401, like Jira.
- It is not Jira: the JQL it understands is only what Odin asks (see _matches), and it keeps
  everything in memory unless you pass --state.

Odin allows http:// for 127.0.0.1 and localhost, and nowhere else (odin/config.py), so pointing Odin
here needs no change to its TLS rules.

Standard library only; run it with any Python 3.8+.
"""
from __future__ import annotations

import argparse
import json
import re
import secrets
import signal
import sys
import threading
import datetime as dt
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, unquote, urlparse

JIRA_TIME = "%Y-%m-%dT%H:%M:%S.000+0000"
CATEGORIES = {"To Do": ("new", "To Do"), "In Progress": ("indeterminate", "In Progress"),
              "Done": ("done", "Done"), "Won't Do": ("done", "Done")}


def now_text() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime(JIRA_TIME)


class Jira:
    """Issues, worklogs and transitions, in memory. Guarded by a lock: the server is threaded."""

    ISSUE_TYPES = ({"name": "Task", "subtask": False}, {"name": "Story", "subtask": False},
                   {"name": "Bug", "subtask": False}, {"name": "Sub-task", "subtask": True})

    def __init__(self, user: str = "jdoe", display: str = "J Doe", project: str = "PROJ",
                 base_url: str = "") -> None:
        self.lock = threading.Lock()
        self.user, self.display, self.project = user, display, project
        self.projects = [project]
        self.base_url = base_url
        self.issues: Dict[str, Dict[str, Any]] = {}
        self.worklogs: Dict[str, List[Dict[str, Any]]] = {}
        self.deleted: List[Dict[str, Any]] = []
        self.next_id = 10000
        self.next_key = 100
        self.next_worklog = 90000
        self.created: List[str] = []          # keys this run made, for the summary on the way out

    # ---- data ------------------------------------------------------------
    def add_issue(self, summary: str, kind: str = "Task", status: str = "To Do", key: Optional[str] = None,
                  parent: Optional[str] = None, labels: Optional[List[str]] = None, mine: bool = True,
                  created: Optional[str] = None) -> str:
        if key is None:
            self.next_key += 1
            key = f"{self.project}-{self.next_key}"
        self.next_id += 1
        stamp = created or now_text()
        self.issues[key] = {
            "id": str(self.next_id), "key": key, "summary": summary, "issuetype": kind, "status": status,
            "parent": parent, "labels": list(labels or []), "assignee": self.user if mine else "someone",
            "creator": self.user, "created": stamp, "updated": stamp,
            "resolution": "Done" if status in ("Done", "Won't Do") else None,
        }
        self.worklogs.setdefault(key, [])
        return key

    def _fields(self, key: str, wanted: Optional[List[str]] = None) -> Dict[str, Any]:
        i = self.issues[key]
        cat, cat_name = CATEGORIES.get(i["status"], ("indeterminate", i["status"]))
        full = {
            "summary": i["summary"],
            "issuetype": {"name": i["issuetype"], "subtask": i["issuetype"] == "Sub-task"},
            "project": {"key": self.project, "name": "Example project"},
            "status": {"name": i["status"], "statusCategory": {"key": cat, "name": cat_name}},
            "resolution": {"name": i["resolution"]} if i["resolution"] else None,
            "resolutiondate": i["updated"] if i["resolution"] else None,
            "priority": {"name": "Medium"},
            "labels": list(i["labels"]),
            "components": [],
            "assignee": {"name": i["assignee"], "displayName": self.display if i["assignee"] == self.user
                         else "Someone Else"},
            "reporter": {"name": i["creator"], "displayName": self.display},
            "creator": {"name": i["creator"], "displayName": self.display},
            "created": i["created"],
            "updated": i["updated"],
            "duedate": None,
            "parent": {"key": i["parent"]} if i["parent"] else None,
            "timespent": sum(w["timeSpentSeconds"] for w in self.worklogs.get(key, [])) or None,
        }
        if not wanted:
            return full
        return {name: full.get(name) for name in wanted if name in full}

    def issue_json(self, key: str, wanted: Optional[List[str]] = None, changelog: bool = False) -> Dict[str, Any]:
        i = self.issues[key]
        out = {"id": i["id"], "key": key, "self": f"{self.base_url}/rest/api/2/issue/{i['id']}",
               "fields": self._fields(key, wanted)}
        if changelog:
            out["changelog"] = {"histories": []}
        return out

    def touch(self, key: str) -> None:
        self.issues[key]["updated"] = now_text()

    # ---- the little bit of JQL Odin asks for ------------------------------
    def _matches(self, jql: str) -> List[str]:
        """Only the shapes Odin sends (collect.py and sync.py). Anything else matches nothing."""
        text = jql.strip()
        keys = list(self.issues)
        label = re.search(r'labels\s*=\s*"([^"]+)"', text)
        if label:
            keys = [k for k in keys if label.group(1) in self.issues[k]["labels"]]
        if re.search(r"creator\s*=\s*currentUser\(\)", text):
            keys = [k for k in keys if self.issues[k]["creator"] == self.user]
        if re.search(r"assignee\s+was\s+currentUser\(\)|assignee\s*=\s*currentUser\(\)", text):
            keys = [k for k in keys if self.issues[k]["assignee"] == self.user]
        if re.search(r"worklogAuthor\s*=\s*currentUser\(\)", text):
            keys = [k for k in keys if any(w["author"]["name"] == self.user for w in self.worklogs.get(k, []))]
        parent = re.search(r"parent\s*=\s*([A-Z][A-Z0-9_]*-[1-9][0-9]*)", text)
        if parent:
            keys = [k for k in keys if self.issues[k]["parent"] == parent.group(1)]
        project = re.search(r"project\s*=\s*([A-Z][A-Z0-9_]*)", text)
        if project:
            keys = [k for k in keys if k.startswith(project.group(1) + "-")]
        if "ORDER BY created DESC" in text:
            return sorted(keys, key=lambda k: (self.issues[k]["created"], k), reverse=True)
        return sorted(keys, key=lambda k: (self.issues[k]["updated"], k))

    # ---- what the handler calls -------------------------------------------
    def search(self, params: Dict[str, List[str]]) -> Dict[str, Any]:
        jql = (params.get("jql") or [""])[0]
        wanted = [f for f in (params.get("fields") or [""])[0].split(",") if f]
        changelog = "changelog" in (params.get("expand") or [""])[0]
        start = int((params.get("startAt") or ["0"])[0])
        size = min(int((params.get("maxResults") or ["50"])[0]), 100)
        keys = self._matches(jql)
        page = keys[start:start + size]
        return {"startAt": start, "maxResults": size, "total": len(keys),
                "issues": [self.issue_json(k, wanted, changelog) for k in page]}

    def create(self, fields: Dict[str, Any]) -> Dict[str, Any]:
        parent = (fields.get("parent") or {}).get("key")
        kind = (fields.get("issuetype") or {}).get("name") or ("Sub-task" if parent else "Task")
        key = self.add_issue(str(fields.get("summary") or "(no summary)"), kind=kind, parent=parent,
                             labels=list(fields.get("labels") or []))
        self.created.append(key)
        return {"id": self.issues[key]["id"], "key": key}

    def add_worklog(self, key: str, body: Dict[str, Any]) -> Dict[str, Any]:
        self.next_worklog += 1
        wid = str(self.next_worklog)
        entry = {"id": wid, "issueId": self.issues[key]["id"],
                 "author": {"name": self.user, "displayName": self.display},
                 "updateAuthor": {"name": self.user, "displayName": self.display},
                 "started": str(body.get("started") or now_text()),
                 "timeSpentSeconds": int(body.get("timeSpentSeconds") or 60),
                 "comment": body.get("comment") or "",
                 "created": now_text(), "updated": now_text()}
        self.worklogs.setdefault(key, []).append(entry)
        self.touch(key)
        return entry

    def transitions(self, key: str) -> Dict[str, Any]:
        here = self.issues[key]["status"]
        return {"transitions": [{"id": str(10 + n), "name": name, "to": {"name": name}}
                                for n, name in enumerate(CATEGORIES) if name != here]}

    def transition(self, key: str, transition_id: str) -> bool:
        for t in self.transitions(key)["transitions"]:
            if t["id"] == str(transition_id):
                self.issues[key]["status"] = t["name"]
                self.issues[key]["resolution"] = "Done" if t["name"] in ("Done", "Won't Do") else None
                self.touch(key)
                return True
        return False

    # ---- keeping it across restarts ---------------------------------------
    def dump(self) -> Dict[str, Any]:
        return {"user": self.user, "display": self.display, "project": self.project,
                "projects": self.projects, "issues": self.issues,
                "worklogs": self.worklogs, "deleted": self.deleted, "next_id": self.next_id,
                "next_key": self.next_key, "next_worklog": self.next_worklog}

    def load(self, raw: Dict[str, Any]) -> None:
        self.user = raw.get("user", self.user)
        self.display = raw.get("display", self.display)
        self.project = raw.get("project", self.project)
        self.projects = list(raw.get("projects") or [self.project])
        self.issues = dict(raw.get("issues") or {})
        self.worklogs = {k: list(v) for k, v in (raw.get("worklogs") or {}).items()}
        self.deleted = list(raw.get("deleted") or [])
        self.next_id = int(raw.get("next_id") or 10000)
        self.next_key = int(raw.get("next_key") or 100)
        self.next_worklog = int(raw.get("next_worklog") or 90000)


def seed(jira: Jira, parent_key: str) -> None:
    """A project to work in: the parent your meeting sub-tasks go under, and a little history.

    It also makes every key config.example.json names (ADMIN-7, PROJ-200, the ADMIN project), so a
    fresh `odin init` passes `odin check` here without editing the example rules first.
    """
    yesterday = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=1)).strftime(JIRA_TIME)
    jira.add_issue("Meetings and admin time", key=parent_key, kind="Task", created=yesterday)
    for extra in ("ADMIN-7", f"{jira.project}-200"):
        project = extra.split("-")[0]
        if project not in jira.projects:
            jira.projects.append(project)
        jira.add_issue(f"Parent for {extra}", key=extra, kind="Task", created=yesterday)
    jira.add_issue("Retry the export on a 503", kind="Story", status="In Progress", created=yesterday)
    jira.add_issue("Fix the form validation", kind="Bug", status="To Do", created=yesterday)
    done = jira.add_issue("Tidy the retry logging", kind="Task", status="Done", created=yesterday)
    jira.add_worklog(done, {"timeSpentSeconds": 1800, "started": yesterday, "comment": "Meeting: standup"})


class Handler(BaseHTTPRequestHandler):
    server_version = "FakeJira/1.0"
    jira: Jira = None          # set by serve()
    token: str = ""
    quiet: bool = False

    # ---- plumbing
    def log_message(self, fmt: str, *args: Any) -> None:
        if not self.quiet:
            sys.stderr.write("  %s %s\n" % (self.command, self.path.split("?")[0]))

    def _send(self, status: int, payload: Any) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json;charset=UTF-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _error(self, status: int, *messages: str) -> None:
        self._send(status, {"errorMessages": list(messages), "errors": {}})

    def _authorised(self) -> bool:
        if self.headers.get("Authorization") == f"Bearer {self.token}":
            return True
        self._error(401, "Client must be authenticated to access this resource.")
        return False

    def _body(self) -> Dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        try:
            data = json.loads(self.rfile.read(length).decode("utf-8"))
        except ValueError:
            return {}
        return data if isinstance(data, dict) else {}

    # ---- routing
    def do_GET(self) -> None:       # noqa: N802 - http.server's name
        if not self._authorised():
            return
        path, params = self._split()
        jira = self.jira
        with jira.lock:
            if path == "/rest/api/2/serverInfo":
                return self._send(200, {"baseUrl": jira.base_url, "version": "9.12.0",
                                        "versionNumbers": [9, 12, 0], "deploymentType": "Server",
                                        "serverTitle": "Fake Jira (local)"})
            if path == "/rest/api/2/myself":
                return self._send(200, {"name": jira.user, "key": jira.user, "displayName": jira.display,
                                        "emailAddress": f"{jira.user}@example.test", "active": True})
            if path == "/rest/api/2/field":
                return self._send(200, [
                    {"id": "summary", "name": "Summary", "custom": False},
                    {"id": "customfield_10008", "name": "Epic Link", "custom": True},
                    {"id": "customfield_10004", "name": "Story Points", "custom": True},
                    {"id": "customfield_10007", "name": "Sprint", "custom": True}])
            if path == "/rest/api/2/status":
                return self._send(200, [{"name": name, "statusCategory": {"key": cat, "name": cat_name}}
                                        for name, (cat, cat_name) in CATEGORIES.items()])
            if path == "/rest/pat/latest/tokens":
                return self._send(200, [{"id": 1, "name": "odin", "createdAt": now_text(), "expiringAt": None}])
            if path == "/rest/api/2/search":
                return self._send(200, jira.search(params))
            if path == "/rest/api/2/worklog/deleted":
                return self._send(200, {"values": jira.deleted, "lastPage": True,
                                        "until": int(dt.datetime.now().timestamp() * 1000)})
            if path.startswith("/rest/api/2/project/"):
                key = unquote(path.rsplit("/", 1)[1])
                if key not in jira.projects:
                    return self._error(404, f"No project could be found with key '{key}'.")
                return self._send(200, {"key": key, "name": f"{key} project",
                                        "issueTypes": [dict(t) for t in jira.ISSUE_TYPES]})
            match = re.fullmatch(r"/rest/api/2/issue/([^/]+)(/worklog|/transitions)?", path)
            if match:
                key = unquote(match.group(1))
                if key not in jira.issues:
                    return self._error(404, f"Issue does not exist or you do not have permission to see it: {key}")
                if match.group(2) == "/worklog":
                    items = jira.worklogs.get(key, [])
                    return self._send(200, {"startAt": 0, "maxResults": len(items), "total": len(items),
                                            "worklogs": items})
                if match.group(2) == "/transitions":
                    return self._send(200, jira.transitions(key))
                wanted = [f for f in (params.get("fields") or [""])[0].split(",") if f]
                changelog = "changelog" in (params.get("expand") or [""])[0]
                return self._send(200, jira.issue_json(key, wanted, changelog))
        self._error(404, f"Fake Jira doesn't serve {path}. It serves what Odin asks for; see its docstring.")

    def do_POST(self) -> None:      # noqa: N802
        if not self._authorised():
            return
        path, _ = self._split()
        body = self._body()
        jira = self.jira
        with jira.lock:
            if path == "/rest/api/2/issue":
                fields = body.get("fields") or {}
                parent = (fields.get("parent") or {}).get("key")
                if parent and parent not in jira.issues:
                    return self._error(400, f"The parent issue {parent} doesn't exist.")
                if not fields.get("summary"):
                    return self._error(400, "You must specify a summary of the issue.")
                created = jira.create(fields)
                return self._send(201, created)
            match = re.fullmatch(r"/rest/api/2/issue/([^/]+)(/worklog|/transitions)", path)
            if match:
                key = unquote(match.group(1))
                if key not in jira.issues:
                    return self._error(404, f"Issue does not exist or you do not have permission to see it: {key}")
                if match.group(2) == "/worklog":
                    return self._send(201, jira.add_worklog(key, body))
                wanted = str((body.get("transition") or {}).get("id") or "")
                if not jira.transition(key, wanted):
                    return self._error(400, f"Transition {wanted} isn't available on {key}.")
                return self._send(204, {})
        self._error(404, f"Fake Jira doesn't serve POST {path}.")

    def _split(self) -> Tuple[str, Dict[str, List[str]]]:
        parts = urlparse(self.path)
        return parts.path.rstrip("/") or "/", parse_qs(parts.query)


def serve(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0], allow_abbrev=False)
    p.add_argument("--port", type=int, default=7990, help="port on 127.0.0.1 (default 7990; 0 picks a free one)")
    p.add_argument("--token", default="", help="the bearer token to require (default: a fresh random one)")
    p.add_argument("--project", default="PROJ", help="the project key (default PROJ)")
    p.add_argument("--parent", default="", help="the parent issue for meeting sub-tasks (default <project>-100)")
    p.add_argument("--user", default="jdoe", help="the Jira username it answers as (default jdoe)")
    p.add_argument("--state", help="a JSON file to keep issues and worklogs in across restarts")
    p.add_argument("--quiet", action="store_true", help="don't print each request")
    args = p.parse_args(argv)

    token = args.token or secrets.token_urlsafe(24)
    parent_key = args.parent or f"{args.project}-100"
    jira = Jira(user=args.user, project=args.project)
    state = Path(args.state) if args.state else None
    if state and state.is_file():
        try:
            jira.load(json.loads(state.read_text(encoding="utf-8")))
        except (OSError, ValueError) as exc:
            print(f"Couldn't read {state} ({exc}); starting empty.", file=sys.stderr)
    if not jira.issues:
        seed(jira, parent_key)

    Handler.jira, Handler.token, Handler.quiet = jira, token, args.quiet
    # Loopback only. This is an unauthenticated-by-default protocol with a token bolted on; it must
    # never be reachable from another machine.
    httpd = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    host, port = httpd.server_address[0], httpd.server_address[1]
    jira.base_url = f"http://{host}:{port}"

    print(f"Fake Jira on {jira.base_url}  (loopback only)")
    print(f"  project {jira.project}, parent for meetings {parent_key}, user {jira.user}")
    print(f"  issues: {', '.join(sorted(jira.issues)) or 'none'}")
    print()
    print("Point Odin at it:")
    print(f"  setx JIRA_PAT {token}        (new shell afterwards; or odin set-token and paste it)")
    print("  then in %LOCALAPPDATA%\\Asgard\\odin\\config.json:")
    print(json.dumps({"jira": {"base_url": jira.base_url, "project": jira.project,
                               "default_parent": parent_key}}, indent=2))
    print()
    print("Try:  odin check  ->  odin preview  ->  odin push --input export.json  ->  odin sync")
    print("Stop it with Ctrl+C." + (f" State: {state}" if state else " Nothing is saved on exit."))
    print()

    def stop(*_: Any) -> None:
        raise KeyboardInterrupt

    signal.signal(signal.SIGINT, stop)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.shutdown()
        httpd.server_close()
        if jira.created:
            print(f"\nOdin created {len(jira.created)} issue(s): {', '.join(jira.created)}")
        if state:
            try:
                state.write_text(json.dumps(jira.dump(), indent=2), encoding="utf-8")
                print(f"Saved to {state}")
            except OSError as exc:
                print(f"Couldn't save {state} ({exc})", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(serve())
