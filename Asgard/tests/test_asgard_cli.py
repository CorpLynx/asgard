"""The asgard command (issue #10): asgard <app> <command> [args] and asgard <command> [args].

- asgard/cli.py reads only the first word and hands the rest to the app's own main(), unchanged:
  every app and every global command, help, an unknown word (exit 2), exit codes passed through,
  and --json output byte-identical to the old entry points;
- an app is imported only once it's chosen (asgard muninn status loads no PySide6, Playwright or MCP);
- Odin's own odin.cmd commands go to odin.cmd, quoted for cmd.exe; its Python commands run in-process;
- the packaged build's entry point sends the right command lines to it;
- asgard.cmd on the per-user Path (asgard/shim.py) against a stand-in winreg, never the real
  registry: no duplicate entry, the value's type kept, only HKCU, and Valhalla takes it out again.
"""
import contextlib
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from asgard import cli, install, paths, shim, valhalla  # noqa: E402

DISPATCHER = ROOT / "asgard" / "cli.py"


def load_frozen_main():
    spec = importlib.util.spec_from_file_location("packaging_frozen_main_cli", ROOT / "packaging" / "frozen_main.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Home(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.addCleanup(os.chdir, os.getcwd())      # Valhalla moves to the temp folder before deleting
        saved = os.environ.get("ASGARD_HOME")
        os.environ["ASGARD_HOME"] = str(self.dir / "home")
        self.addCleanup(lambda: os.environ.__setitem__("ASGARD_HOME", saved) if saved is not None
                        else os.environ.pop("ASGARD_HOME", None))

    def run_main(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def env(self, **extra):
        env = dict(os.environ, ASGARD_HOME=str(self.dir / "home"), PYTHONIOENCODING="utf-8")
        env.update(extra)
        return env

    def run_process(self, *argv, cwd=None):
        return subprocess.run([sys.executable, *map(str, argv)], capture_output=True, env=self.env(),
                              cwd=str(cwd or self.dir), timeout=120)


class FakeMain:
    """An app's main(): records what it was given and answers with a fixed result."""

    def __init__(self, result=0, raise_exit=None):
        self.calls = []
        self.result, self.raise_exit = result, raise_exit

    def __call__(self, argv):
        self.calls.append((list(argv), list(sys.argv)))
        if self.raise_exit is not None:
            raise SystemExit(self.raise_exit)
        return self.result


# --------------------------------------------------------------------------
# Dispatch
# --------------------------------------------------------------------------

class DispatchTests(Home):
    def test_every_app_gets_the_rest_of_the_line_unchanged(self):
        for app in ("baldur", "heimdall", "ysildir"):
            with self.subTest(app):
                fake = FakeMain()
                with mock.patch.object(cli, "load_app", return_value=fake) as load:
                    code, _, _ = self.run_main(app, "fill", "-t", "Firewall access", "--var", "SRC=web servers",
                                               "--json")
                self.assertEqual(code, 0)
                load.assert_called_once_with(app)
                argv, seen = fake.calls[0]
                self.assertEqual(argv, ["fill", "-t", "Firewall access", "--var", "SRC=web servers", "--json"])
                self.assertEqual(seen, [str(cli.app_script(app))] + argv, "sys.argv as python cli.py ARGS sets it")

    def test_the_first_word_is_any_case_and_the_rest_keeps_its_case(self):
        fake = FakeMain()
        with mock.patch.object(cli, "load_app", return_value=fake):
            self.run_main("Baldur", "Report", "Today")
        self.assertEqual(fake.calls[0][0], ["Report", "Today"])

    def test_app_help_is_the_apps_own_and_help_means_dash_dash_help(self):
        for words in (["baldur", "--help"], ["baldur", "help"], ["heimdall", "-h"], ["ysildir", "help"]):
            with self.subTest(words):
                fake = FakeMain(raise_exit=0)
                with mock.patch.object(cli, "load_app", return_value=fake):
                    code, _, _ = self.run_main(*words)
                self.assertEqual(code, 0)
                self.assertEqual(fake.calls[0][0], ["--help"] if words[1] == "help" else words[1:])

    def test_exit_codes_pass_through_unchanged(self):
        for result, raised, want in ((0, None, 0), (1, None, 1), (2, None, 2), (130, None, 130), (None, None, 0),
                                     (0, 2, 2), (0, 0, 0), (0, 3, 3)):
            with self.subTest(result=result, raised=raised):
                fake = FakeMain(result, raise_exit=raised)
                with mock.patch.object(cli, "load_app", return_value=fake):
                    self.assertEqual(self.run_main("baldur", "x")[0], want)
        with mock.patch.object(cli, "load_app", return_value=mock.Mock(side_effect=KeyboardInterrupt)):
            self.assertEqual(self.run_main("heimdall", "fill")[0], 130)
        with mock.patch.object(cli, "load_app", return_value=FakeMain(raise_exit="a message")):
            code, _, err = self.run_main("baldur", "x")
        self.assertEqual((code, err.strip()), (1, "a message"), "SystemExit with text: printed, exit 1, as python")

    def test_sys_argv_is_put_back(self):
        before = list(sys.argv)
        with mock.patch.object(cli, "load_app", return_value=FakeMain(raise_exit=2)):
            self.run_main("baldur", "nope")
        self.assertEqual(sys.argv, before)

    def test_every_global_command_is_dispatched(self):
        for word, target in (("muninn", "run_muninn"), ("install", "run_install"), ("uninstall", "run_uninstall"),
                             ("version", "run_version"), ("ui", "run_ui")):
            with self.subTest(word):
                fake = mock.Mock(return_value=7)
                with mock.patch.dict(cli.GLOBALS, {word: fake}):
                    self.assertEqual(self.run_main(word, "a", "--b")[0], 7)
                fake.assert_called_once_with(["a", "--b"])
                self.assertIs(cli.GLOBALS[word], getattr(cli, target))
        with mock.patch.object(cli, "run_selftest", return_value=0) as selftest:
            self.assertEqual(self.run_main("selftest", "--skip", "Qt")[0], 0)
        selftest.assert_called_once_with(["--skip", "Qt"], None)

    def test_globals_reach_the_code_they_name(self):
        from asgard import install as install_mod
        from asgard import ui
        from asgard.muninn import cli as muninn_cli
        with mock.patch.object(muninn_cli, "main", return_value=0) as m:
            self.assertEqual(self.run_main("muninn", "status")[0], 0)
        m.assert_called_once_with(["status"], prog="asgard muninn")
        with mock.patch.object(install_mod, "main", return_value=0) as m:
            self.run_main("install", "--desktop", "--no-launch")
        m.assert_called_once_with(["--desktop", "--no-launch"])
        with mock.patch.object(valhalla, "main", return_value=0) as m:
            self.run_main("uninstall", "--yes", "--purge")
            self.run_main("uninstall")
        self.assertEqual([c.args[0] for c in m.call_args_list],
                         [["--uninstall", "--yes", "--purge"], ["--uninstall"]])
        with mock.patch.object(ui, "run", return_value=0) as m:
            self.run_main("ui", "--app", "heimdall")
        m.assert_called_once_with(app="heimdall", argv=["asgard"])

    def test_the_launchers_old_forms_still_work_here(self):
        """asgard-cli.exe --uninstall --yes is the packaged build's QuietUninstallString."""
        from asgard.muninn import cli as muninn_cli
        with mock.patch.object(muninn_cli, "main", return_value=0) as m:
            self.run_main("--muninn", "check")
        m.assert_called_once_with(["check"], prog="asgard muninn")
        with mock.patch.object(valhalla, "main", return_value=0) as m:
            self.run_main("--uninstall", "--yes")
        m.assert_called_once_with(["--uninstall", "--yes"])

    def test_version_says_which_and_where(self):
        code, out, _ = self.run_main("version")
        self.assertEqual(code, 0)
        from asgard import __version__
        self.assertTrue(out.startswith(f"Asgard {__version__}\n"), out)
        self.assertIn(f"Code: {paths.CODE_ROOT}", out)
        self.assertEqual(self.run_main("version", "extra")[0], 2)


class HelpTests(Home):
    def test_help_lists_every_app_and_command_and_exits_0(self):
        for word in ("--help", "-h", "help", "/?"):
            with self.subTest(word):
                code, out, err = self.run_main(word)
                self.assertEqual((code, err), (0, ""))
                for name in list(cli.APPS) + list(cli.COMMANDS):
                    self.assertRegex(out, rf"(?m)^  {name} +\S")
                self.assertIn("usage: asgard <app> <command> [args]", out)

    def test_no_app_or_an_unknown_one_lists_them_and_exits_2(self):
        code, out, err = self.run_main()
        self.assertEqual((code, out), (2, ""))
        self.assertIn("Apps:", err)
        code, out, err = self.run_main("baldr", "collect")
        self.assertEqual((code, out), (2, ""))
        self.assertIn("no app or command called 'baldr'. Did you mean baldur?", err)
        self.assertIn("Asgard's commands:", err)
        self.assertEqual(self.run_main("freya")[0], 2, "a planned app isn't a command yet")

    def test_help_for_the_global_commands(self):
        for words in (["uninstall", "--help"], ["selftest", "--help"], ["ui", "--help"], ["version", "--help"],
                      ["muninn", "--help"]):
            with self.subTest(words):
                code, out, _ = self.run_main(*words)
                self.assertEqual(code, 0)
                self.assertIn(f"asgard {words[0]}", out)

    def test_the_lists_match_the_packaged_builds_and_the_guards(self):
        frozen = load_frozen_main()
        known = set(cli.APPS) | set(cli.COMMANDS)
        self.assertEqual(set(frozen.DISPATCH_WORDS), known | set(cli.HELP_WORDS) | {"--version", "-v"})
        spec = importlib.util.spec_from_file_location("guard_for_cli", ROOT / "apps" / "baldur" / "agents" / "hooks"
                                                      / "guard_baldur.py")
        guard = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(guard)
        self.assertEqual(guard.DISPATCH_WORDS, known)
        self.assertEqual(set(cli.APPS), set(frozen.APP_FOLDERS))


# --------------------------------------------------------------------------
# Odin
# --------------------------------------------------------------------------

class OdinTests(Home):
    def test_odin_cmds_own_commands_go_to_odin_cmd_on_windows(self):
        for args in ([], ["preview"], ["setup"], ["doctor"], ["schedule"], ["unschedule"], ["selftest"],
                     ["csv", r"C:\exports\cal.csv", "-DryRun"], ["run", "-DaysBack", "7"], ["help"], ["--help"]):
            with self.subTest(args):
                with mock.patch.object(cli, "IS_WINDOWS", True), \
                        mock.patch.object(cli, "run_odin_cmd", return_value=5) as run:
                    self.assertEqual(self.run_main("odin", *args)[0], 5, "odin.cmd's exit code, unchanged")
                run.assert_called_once_with(args)

    def test_odins_python_commands_run_here(self):
        for args, given in ((["status"], ["status"]), (["settle", "12", "--posted", "50001"],
                                                       ["settle", "12", "--posted", "50001"]),
                            (["daily", "--export", "x.json"], ["daily", "--export", "x.json"]),
                            (["cli", "forget", "PROJ-501"], ["forget", "PROJ-501"]),
                            (["report", "--no-subjects"], ["report", "--no-subjects"])):
            with self.subTest(args):
                fake = FakeMain(1)
                with mock.patch.object(cli, "IS_WINDOWS", True), mock.patch.object(cli, "load_app",
                                                                                  return_value=fake) as load, \
                        mock.patch.object(cli, "run_odin_cmd") as run:
                    self.assertEqual(self.run_main("odin", *args)[0], 1)
                load.assert_called_once_with("odin")
                run.assert_not_called()
                self.assertEqual(fake.calls[0][0], given)

    def test_off_windows_the_daily_run_says_why_and_help_still_works(self):
        with mock.patch.object(cli, "IS_WINDOWS", False), mock.patch.object(cli, "run_odin_cmd") as run:
            code, _, err = self.run_main("odin")
            self.assertEqual(code, 2)
            self.assertIn("daily run uses Outlook and Windows PowerShell", err)
            fake = FakeMain(raise_exit=0)
            with mock.patch.object(cli, "load_app", return_value=fake):
                self.assertEqual(self.run_main("odin", "--help")[0], 0)
            self.assertEqual(fake.calls[0][0], ["--help"])
        run.assert_not_called()

    def test_the_cmd_line_quotes_every_argument_for_cmd(self):
        script = Path(r"C:\Users\Dev User\AppData\Local\Asgard\app\apps\odin\odin.cmd")
        self.assertEqual(cli.cmd_line(script, []), f'""{script}""')
        self.assertEqual(cli.cmd_line(script, ["csv", r"C:\My Files (x86)\a&b.csv", "-DryRun"]),
                         f'""{script}" "csv" "C:\\My Files (x86)\\a&b.csv" "-DryRun""')
        self.assertEqual(cli.cmd_line(script, ["run", "C:\\out\\"]), f'""{script}" "run" "C:\\out\\\\""',
                         "a trailing backslash is doubled so it doesn't escape the closing quote")
        for bad in ('say "hi"', "%PATH%", "a\nb"):
            with self.subTest(bad), self.assertRaisesRegex(cli.Refused, "Run odin.cmd with it directly"):
                cli.cmd_line(script, ["csv", bad])

    def test_odin_cmd_runs_through_cmd_exe_with_no_autorun(self):
        with mock.patch.object(cli, "odin_cmd", return_value=self.dir / "odin.cmd"), \
                mock.patch.dict(os.environ, {"COMSPEC": r"C:\Windows\System32\cmd.exe"}), \
                mock.patch.object(cli.subprocess, "call", return_value=3) as call:
            (self.dir / "odin.cmd").write_text("")
            self.assertEqual(cli.run_odin_cmd(["preview"]), 3)
        line = call.call_args.args[0]
        self.assertTrue(line.startswith(r'"C:\Windows\System32\cmd.exe" /d /s /c ""'), line)
        self.assertTrue(line.endswith(' "preview""'), line)


# --------------------------------------------------------------------------
# Real processes: lazy imports, byte-identical output, any folder
# --------------------------------------------------------------------------

PROBE = """
import json, sys
sys.path.insert(0, {root!r})
from asgard import cli
code = cli.main({argv!r})
heavy = sorted(m for m in sys.modules if m.split(".")[0] in ("PySide6", "playwright", "mcp", "pydantic", "tkinter",
                                                                "baldur", "heimdall", "odin", "ysildir"))
print("\\nPROBE " + json.dumps([code, heavy]))
"""


class ProcessTests(Home):
    def probe(self, *argv):
        done = self.run_process("-c", PROBE.format(root=str(ROOT), argv=list(argv)))
        line = [x for x in done.stdout.decode("utf-8").splitlines() if x.startswith("PROBE ")]
        self.assertTrue(line, done.stderr.decode("utf-8", "replace"))
        return json.loads(line[-1][6:])

    def test_asgard_muninn_status_imports_no_app_or_package(self):
        code, heavy = self.probe("muninn", "status")
        self.assertEqual(code, 2, "no Muninn yet: its own exit code")
        self.assertEqual(heavy, [], "no PySide6, Playwright, MCP SDK, tkinter or app before an app is chosen")
        for argv in (["--help"], ["version"], ["nope"]):
            with self.subTest(argv):
                self.assertEqual(self.probe(*argv)[1], [])

    def test_choosing_an_app_loads_only_that_app(self):
        code, heavy = self.probe("baldur", "--help")
        self.assertEqual(code, 0)
        self.assertIn("baldur", heavy)
        self.assertFalse({m for m in heavy if m.split(".")[0] in ("PySide6", "playwright", "mcp", "heimdall")})

    def test_json_output_is_byte_identical_to_the_old_entry_points(self):
        prepared = self.run_process(DISPATCHER, "muninn", "prepare")
        self.assertEqual(prepared.returncode, 0, prepared.stderr)
        self.run_process(ROOT / "apps" / "heimdall" / "cli.py", "init")
        pairs = [
            ([ROOT / "apps" / "baldur" / "cli.py", "ai", "list", "--json"], ["baldur", "ai", "list", "--json"]),
            ([ROOT / "apps" / "baldur" / "cli.py", "nonsense"], ["baldur", "nonsense"]),
            ([ROOT / "apps" / "heimdall" / "cli.py", "template", "list", "--json"],
             ["heimdall", "template", "list", "--json"]),
            ([ROOT / "apps" / "heimdall" / "cli.py", "fields", "--json"], ["heimdall", "fields", "--json"]),
            ([ROOT / "apps" / "odin" / "cli.py", "status"], ["odin", "status"]),
            ([ROOT / "Asgard.pyw", "--muninn", "check"], ["muninn", "check"]),
        ]
        for old, new in pairs:
            with self.subTest(new):
                before = self.run_process(*old)
                after = self.run_process(DISPATCHER, *new)
                self.assertEqual(after.returncode, before.returncode)
                self.assertEqual(after.stdout, before.stdout)
                if new[0] != "baldur" or new[1] != "nonsense":       # argparse names its program in errors
                    self.assertEqual(after.stderr, before.stderr)

    def test_it_runs_from_any_folder(self):
        elsewhere = self.dir / "somewhere else"
        elsewhere.mkdir()
        (elsewhere / "paths.py").write_text("raise SystemExit('the current folder was imported')\n")
        done = self.run_process(DISPATCHER, "version", cwd=elsewhere)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn(f"Code: {ROOT}", done.stdout.decode("utf-8"))


# --------------------------------------------------------------------------
# The packaged build
# --------------------------------------------------------------------------

class FrozenEntryTests(Home):
    frozen = load_frozen_main()

    def test_which_command_lines_go_to_the_asgard_command(self):
        d = self.frozen.dispatches
        self.assertTrue(d([], console=True), "asgard-cli.exe alone: the asgard command's help")
        self.assertFalse(d([], console=False), "Asgard.exe alone: the launcher window")
        for args in (["baldur", "collect"], ["muninn", "status"], ["--help"], ["Odin"], ["ysildir", "serve"]):
            with self.subTest(args):
                self.assertTrue(d(args, console=True))
                self.assertTrue(d(args, console=False), "Asgard.exe baldur collect: the weekly task, no console")
        for args in ([r"apps\baldur\cli.py", "report"], ["Asgard.pyw", "--muninn", "check"], ["--uninstall"],
                     ["--uninstall", "--yes"], ["--muninn", "check"], ["x.pyw"]):
            with self.subTest(args):
                self.assertFalse(d(args, console=True))
                self.assertFalse(d(args, console=False))
        self.assertTrue(d(["nonsense"], console=True), "the console program: the asgard command says it's unknown")
        self.assertFalse(d(["nonsense"], console=False))

    def test_it_knows_its_own_program(self):
        self.assertTrue(self.frozen.console_program(r"C:\Asgard\app\asgard-cli.exe"))
        self.assertTrue(self.frozen.console_program("/opt/asgard/asgard-cli"))
        self.assertFalse(self.frozen.console_program(r"C:\Asgard\app\Asgard.exe"))

    def test_main_hands_the_line_to_the_asgard_command_with_the_self_test(self):
        with mock.patch.object(self.frozen, "console_program", return_value=True), \
                mock.patch.object(cli, "main", return_value=4) as m:
            self.assertEqual(self.frozen.main(["baldur", "report"]), 4)
        m.assert_called_once_with(["baldur", "report"], selftest=self.frozen.self_test)
        with mock.patch.object(self.frozen, "console_program", return_value=True), \
                mock.patch.object(self.frozen, "run_script") as run:
            self.assertEqual(self.frozen.main(["--uninstall", "--yes"]), 0)
        self.assertEqual(run.call_args.args[1], ["--uninstall", "--yes"], "the launcher, as before")

    def test_selftest_uses_the_one_it_is_given(self):
        given = mock.Mock(return_value=0)
        self.assertEqual(cli.main(["selftest", "--skip", "Qt,Tk"], selftest=given), 0)
        given.assert_called_once_with(skip=["Qt", "Tk"])


# --------------------------------------------------------------------------
# asgard.cmd and the per-user Path
# --------------------------------------------------------------------------

class FakeKey:
    def __init__(self, reg, hive, name):
        self.reg, self.hive, self.name = reg, hive, name

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeWinreg:
    """Just enough of winreg for HKCU\\Environment, recording every hive it was asked for."""
    HKEY_CURRENT_USER, HKEY_LOCAL_MACHINE = "HKCU", "HKLM"
    KEY_READ, KEY_WRITE = 1, 2
    REG_SZ, REG_EXPAND_SZ = 1, 2

    def __init__(self, path=None, kind=2):
        self.values = {} if path is None else {("HKCU", "Environment", "Path"): (path, kind)}
        self.hives = []
        self.writes = 0

    def OpenKey(self, hive, name, reserved=0, access=0):
        self.hives.append(hive)
        return FakeKey(self, hive, name)

    def CreateKeyEx(self, hive, name, reserved=0, access=0):
        self.hives.append(hive)
        return FakeKey(self, hive, name)

    def QueryValueEx(self, key, value):
        try:
            return self.values[(key.hive, key.name, value)]
        except KeyError:
            raise FileNotFoundError(value) from None

    def SetValueEx(self, key, value, reserved, kind, data):
        self.writes += 1
        self.values[(key.hive, key.name, value)] = (data, kind)

    def path(self):
        return self.values.get(("HKCU", "Environment", "Path"))


class PathTests(Home):
    def setUp(self) -> None:
        super().setUp()
        patcher = mock.patch.object(shim, "broadcast_environment_change", return_value=True)
        self.broadcast = patcher.start()
        self.addCleanup(patcher.stop)
        self.bin = Path(r"C:\Users\Dev User\AppData\Local\Asgard\bin")

    def test_it_adds_the_folder_once_at_the_end_and_keeps_the_type(self):
        reg = FakeWinreg(r"C:\tools;%USERPROFILE%\go\bin", FakeWinreg.REG_EXPAND_SZ)
        self.assertTrue(shim.add_to_user_path(self.bin, reg))
        self.assertEqual(reg.path(), (rf"C:\tools;%USERPROFILE%\go\bin;{self.bin}", FakeWinreg.REG_EXPAND_SZ))
        self.broadcast.assert_called_once_with()
        for again in (self.bin, Path(str(self.bin).upper()), Path(str(self.bin) + "\\")):
            with self.subTest(again):
                self.assertFalse(shim.add_to_user_path(again, reg), "never a second entry")
        self.assertEqual(reg.writes, 1)
        self.assertEqual(set(reg.hives), {"HKCU"}, "never the machine Path")
        plain = FakeWinreg(r"C:\tools", FakeWinreg.REG_SZ)
        shim.add_to_user_path(self.bin, plain)
        self.assertEqual(plain.path()[1], FakeWinreg.REG_SZ, "a REG_SZ Path stays REG_SZ")

    def test_an_entry_written_with_a_variable_counts_as_there(self):
        with mock.patch.dict(os.environ, {"LOCALAPPDATA": r"C:\Users\Dev User\AppData\Local"}):
            reg = FakeWinreg(r"%LOCALAPPDATA%\Asgard\bin;C:\tools")
            self.assertFalse(shim.add_to_user_path(self.bin, reg))
        self.assertEqual(reg.writes, 0)

    def test_no_path_yet_makes_one_as_windows_would(self):
        reg = FakeWinreg()
        self.assertTrue(shim.add_to_user_path(self.bin, reg))
        self.assertEqual(reg.path(), (str(self.bin), FakeWinreg.REG_EXPAND_SZ))

    def test_removing_takes_out_every_entry_for_the_folder_and_nothing_else(self):
        reg = FakeWinreg(rf"C:\tools;{self.bin};C:\other;{str(self.bin).lower()}\;", FakeWinreg.REG_EXPAND_SZ)
        self.assertTrue(shim.remove_from_user_path(self.bin, reg))
        self.assertEqual(reg.path(), (r"C:\tools;C:\other", FakeWinreg.REG_EXPAND_SZ))
        self.assertFalse(shim.remove_from_user_path(self.bin, reg))
        self.assertEqual(set(reg.hives), {"HKCU"})

    def test_a_path_too_long_for_windows_is_refused_and_unchanged(self):
        long = ";".join(rf"C:\dir{n:05d}" for n in range(3000))
        reg = FakeWinreg(long)
        with self.assertRaisesRegex(shim.PathError, "longer than Windows allows"):
            shim.add_to_user_path(self.bin, reg)
        self.assertEqual(reg.path()[0], long)

    def test_the_shim_runs_the_installed_copy_and_keeps_its_exit_code(self):
        text = shim.shim_text(r"C:\Program Files\Python312\python.exe", r"C:\Users\Dev\AppData\Local\Asgard\app"
                              r"\asgard\cli.py")
        text.encode("ascii")
        self.assertTrue(text.endswith("\r\n"))
        self.assertNotIn("\n", text.replace("\r\n", ""), "CRLF only")
        self.assertEqual(text.splitlines()[0], "@echo off")
        self.assertEqual(text.splitlines()[-1], r'"C:\Program Files\Python312\python.exe" '
                         r'"C:\Users\Dev\AppData\Local\Asgard\app\asgard\cli.py" %* & exit /b')
        app = Path(r"C:\Asgard\app")
        program, script = shim.shim_command(app, True, "unused")
        self.assertEqual((Path(program).name.split(".")[0], script), ("asgard-cli", None))
        self.assertEqual(shim.shim_command(app, False, r"C:\py\python.exe"),
                         (r"C:\py\python.exe", str(app / "asgard" / "cli.py")))
        with self.assertRaises(ValueError):
            shim.shim_text('C:\\a"b\\python.exe')

    def test_setup_writes_the_shim_and_records_both_for_valhalla(self):
        reg = FakeWinreg(r"C:\tools")
        app = paths.app_dir()
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            items = install.install_command(app, sys.executable, reg)
        folder = paths.bin_dir()
        self.assertEqual(items, [{"kind": "dir", "path": str(folder)}, {"kind": "path", "path": str(folder)}])
        written = (folder / "asgard.cmd").read_bytes()
        self.assertIn(f'"{sys.executable}" "{app / "asgard" / "cli.py"}" %* & exit /b'.encode("ascii"), written)
        self.assertEqual(reg.path()[0], rf"C:\tools;{folder}")
        self.assertIn("asgard --help", out.getvalue())
        with contextlib.redirect_stdout(io.StringIO()):
            install.install_command(app, sys.executable, reg)
        self.assertEqual(reg.path()[0], rf"C:\tools;{folder}", "setup again adds nothing")

    def test_setup_carries_on_when_the_path_cant_change(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), \
                mock.patch.object(shim, "add_to_user_path", side_effect=shim.PathError("Couldn't change your Path.")):
            items = install.install_command(paths.app_dir(), sys.executable)
        self.assertEqual(items, [{"kind": "dir", "path": str(paths.bin_dir())}])
        self.assertIn("add", out.getvalue())
        self.assertIn("to your Path yourself", out.getvalue())
        self.assertTrue((paths.bin_dir() / "asgard.cmd").exists())

    def test_setup_on_windows_puts_the_command_in_the_ledger(self):
        app = paths.app_dir()
        source = self.dir / "src"
        for rel in ("Asgard.pyw", "VERSION", "asgard/launcher.py", "asgard/asgard.ico", "apps/baldur/cli.py"):
            (source / rel).parent.mkdir(parents=True, exist_ok=True)
            (source / rel).write_text(rel)
        reg = FakeWinreg(r"C:\tools")
        with mock.patch.object(install, "SOURCE_ROOT", source), mock.patch.object(install.winutil, "IS_WINDOWS", True), \
                mock.patch.object(install.winutil, "create_shortcut"), \
                mock.patch.object(install.winutil, "write_uninstall_entry"), \
                mock.patch.object(install.paths, "start_menu_dir", return_value=self.dir / "Start"), \
                mock.patch.object(shim, "_winreg", return_value=reg), contextlib.redirect_stdout(io.StringIO()):
            install.install()
        ledger = json.loads(paths.ledger_path().read_text(encoding="utf-8"))
        self.assertIn({"kind": "path", "path": str(paths.bin_dir())}, ledger["items"])
        self.assertIn({"kind": "dir", "path": str(paths.bin_dir())}, ledger["items"])
        self.assertTrue((app / "Asgard.pyw").exists())

        with mock.patch.object(valhalla.winutil, "IS_WINDOWS", False), \
                mock.patch.object(valhalla.winutil, "delete_uninstall_entry", return_value=False):
            res = valhalla.uninstall(reg=reg)
        self.assertEqual(reg.path()[0], r"C:\tools", "Valhalla took Asgard's entry out of the Path")
        self.assertIn(f"{paths.bin_dir()} from your Path", res.removed)
        self.assertFalse(paths.bin_dir().exists())

    def test_valhalla_leaves_any_other_path_entry_alone(self):
        paths.data_dir().mkdir(parents=True)
        ledger = {"items": [{"kind": "path", "path": r"C:\Windows\System32"},
                            {"kind": "path", "path": str(paths.data_dir())}]}
        paths.ledger_path().write_text(json.dumps(ledger), encoding="utf-8")
        reg = FakeWinreg(rf"C:\Windows\System32;{paths.data_dir()}")
        res = valhalla.uninstall(reg=reg)
        self.assertEqual(reg.writes, 0)
        self.assertEqual([why for _, why in res.skipped], ["not Asgard's folder, so left in your Path"] * 2)


@unittest.skipUnless(os.name == "nt", "asgard.cmd runs under cmd.exe")
class ShimOnWindowsTests(Home):
    """The shim as cmd.exe runs it: the exit code comes back, and deleting it mid-run (asgard uninstall) is clean."""

    def shim(self, program, script):
        return shim.write_shim(self.dir / "bin", program, str(script))

    def test_it_runs_the_asgard_command_and_returns_its_exit_code(self):
        target = self.shim(sys.executable, DISPATCHER)
        done = subprocess.run([str(target), "version"], capture_output=True, text=True, env=self.env(), timeout=120)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("Asgard ", done.stdout)
        done = subprocess.run([str(target), "no-such-app"], capture_output=True, text=True, env=self.env(),
                              timeout=120)
        self.assertEqual(done.returncode, 2)

    def test_deleting_it_while_it_runs_ends_cleanly(self):
        script = self.dir / "uninstall_like.py"
        script.write_text("import os, sys\nos.remove(sys.argv[1])\nsys.exit(3)\n")
        target = self.shim(sys.executable, script)
        done = subprocess.run([str(target), str(target)], capture_output=True, text=True, env=self.env(), timeout=120)
        self.assertFalse(target.exists())
        self.assertEqual(done.returncode, 3)
        self.assertNotIn("cannot be found", done.stdout + done.stderr)


if __name__ == "__main__":
    unittest.main()
