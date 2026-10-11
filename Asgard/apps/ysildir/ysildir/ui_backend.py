"""What Ysildir's page in Asgard's window shows, with no Qt: the tools, their switches, and how to
connect an AI client.

Ysildir is an MCP server, so there is nothing to "open": the page exists to answer three questions.
Is the SDK installed? Which tools may an AI client use, and what does each send? How do I connect a
client? Switching a tool on or off is the person's decision (and the ISSO's), so the page does it and
an agent can't: Kiro's guard hook blocks `asgard ysildir tools --on`.

It stays useful on a Python without the MCP SDK, which is when it matters most: the switch list comes
from ysildir.switches (standard library), and only reading or writing the file needs pydantic. Without
it the page is read-only and says what to install.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List

from asgard import paths

from . import switches

CLIENTS = (("kiro", "Kiro (this workspace)"), ("vscode", "VS Code (Copilot agent mode)"),
           ("claude", "Claude Code"))


def _cli() -> Path:
    return Path(__file__).resolve().parent.parent / "cli.py"


class Backend:
    """Called from QML through the shell's bridge. Every method returns plain data or {"error": ...}."""

    # ---- the SDK ----------------------------------------------------------
    def sdk(self) -> Dict[str, Any]:
        """Whether the MCP SDK and pydantic are importable, and the Python that would run the server."""
        missing = []
        for name in ("mcp", "pydantic"):
            try:
                __import__(name)
            except ImportError:
                missing.append(name)
        return {"ready": not missing, "missing": missing, "python": sys.executable,
                "version": "%d.%d.%d" % sys.version_info[:3],
                "needs": "Python 3.10 or newer" if sys.version_info < (3, 10) else ""}

    # ---- the switches -----------------------------------------------------
    def _state(self) -> Dict[str, Any]:
        """The switch file as it stands: through config when pydantic is there, else read by hand."""
        path = paths.data_dir() / "settings" / "ysildir.json"
        try:
            from . import config
        except ImportError:
            pass
        else:
            loaded = config.load()
            return {"path": str(loaded.path), "tools": dict(loaded.tools), "problem": loaded.problem or "",
                    "unknown": list(loaded.unknown), "writable": True}
        tools = {s.name: s.default for s in switches.SWITCHES}
        problem = ""
        if path.is_file():
            try:
                raw = json.loads(path.read_text(encoding="utf-8-sig"))
                saved = raw.get("tools") if isinstance(raw, dict) else None
                if not isinstance(saved, dict):
                    raise ValueError('it has no "tools" object')
                tools.update({k: bool(v) for k, v in saved.items() if k in tools})
            except (OSError, ValueError) as exc:
                problem = f"{path} couldn't be read ({exc}); showing the defaults"
        return {"path": str(path), "tools": tools, "problem": problem, "unknown": [], "writable": False}

    def tools(self) -> Dict[str, Any]:
        state = self._state()
        rows = [{"name": s.name, "on": bool(state["tools"].get(s.name, s.default)), "default": s.default,
                 "sends": s.sends, "fallback": s.fallback, "always_on": s.name == switches.ALWAYS_ON}
                for s in switches.SWITCHES]
        return {"value": rows, "path": state["path"], "problem": state["problem"], "unknown": state["unknown"],
                "writable": state["writable"], "on": sum(1 for r in rows if r["on"]), "total": len(rows)}

    def set_tool(self, name: str, on: bool) -> Dict[str, Any]:
        """Turn one tool on or off. The MCP server reads the file at start, so say to restart it."""
        try:
            from . import config
        except ImportError:
            return {"error": "Switching tools needs the MCP SDK's pydantic on this Python. Until then use: "
                             "asgard ysildir tools --on NAME"}
        from . import Refused
        try:
            config.switch([name], bool(on))
        except (Refused, OSError) as exc:
            return {"error": str(exc)}
        return {"message": f"{name} is {'on' if on else 'off'}. Restart the MCP server in your AI client."}

    # ---- connecting a client ----------------------------------------------
    def clients(self) -> List[Dict[str, str]]:
        """The `asgard ysildir setup` command for each client, to copy and run in a workspace folder."""
        return [{"id": cid, "name": name,
                 "command": f'python "{_cli()}" setup --{cid} <workspace folder>'} for cid, name in CLIENTS]

    def check(self) -> Dict[str, Any]:
        """Run `ysildir check`: what an agent would see. Slow enough to call from a button, not on open."""
        try:
            done = subprocess.run([sys.executable, str(_cli()), "check"], capture_output=True, text=True,
                                  timeout=60)
        except (OSError, subprocess.SubprocessError) as exc:
            return {"error": f"Couldn't run ysildir check ({exc})."}
        text = (done.stdout or "") + (done.stderr or "")
        return {"value": text.strip() or "(no output)", "ok": done.returncode == 0}

    # ---- what the shell asks for ------------------------------------------
    def dashboard(self) -> List[Dict[str, Any]]:
        sdk = self.sdk()
        if not sdk["ready"]:
            return [{"label": "MCP SDK", "value": "Missing", "tone": "warning", "view": "tools",
                     "detail": "install " + ", ".join(sdk["missing"])}]
        counted = self.tools()
        cards: List[Dict[str, Any]] = [
            {"label": "Tools your AI client may use", "value": f"{counted['on']} of {counted['total']}",
             "view": "tools", "detail": "each one's data flow is the ISSO's decision"}]
        if counted["problem"]:
            cards.append({"label": "Switch file", "value": "!", "tone": "error", "view": "tools",
                          "detail": counted["problem"]})
        return cards

    def settings_files(self) -> List[Dict[str, str]]:
        return [{"label": "Tool switches", "path": self._state()["path"]}]
