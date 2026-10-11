"""Heimdall's template variables (variables.py), its libraries (library.py), and how templates,
the command line and the window use them (issue #11). Standard library only, no browser.

Libraries are encrypted with Windows DPAPI. Off Windows these tests use a stand-in protector, or
HEIMDALL_UNPROTECTED_LIBRARIES=1 where they go through the command line; on Windows the command
line tests use real DPAPI, and DpapiTests check it directly.
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
for folder in (ROOT, ROOT / "apps" / "heimdall", Path(__file__).resolve().parent):
    if str(folder) not in sys.path:
        sys.path.insert(0, str(folder))

from asgard import dpapi  # noqa: E402
from heimdall import library as libs  # noqa: E402
from heimdall import templates as tpl  # noqa: E402
from heimdall import variables as v  # noqa: E402
from heimdall.form import FormError  # noqa: E402
from heimdall.ui_backend import Backend  # noqa: E402
from test_heimdall import Base  # noqa: E402

ISSUE = ("We need access to {SYSTEM_NAME:TEXTBOX('string'):\"System Name\"} from "
         "{SRC_ADDR:LIBRARY('ip_library_12'):'Source'} to {DEST_ADDR:LIBRARY('ip_library_12'):'Destination'}.")
PUBLISHED = {"library": "ip_library_12", "description": "Data centre address groups", "version": "2026-10-01",
             "entries": [{"name": "web-servers", "value": "10.20.0.0/24", "description": "DMZ web tier"},
                         {"name": "db-servers", "value": ["10.30.0.5", "10.30.0.6"], "description": "Payroll DB"},
                         {"name": "jump-hosts", "value": "10.40.0.9"}]}
# A stand-in for DPAPI off Windows: reversible, and nothing it writes is readable as the original.
XOR = libs.Protector(libs.DPAPI.kind, lambda d: bytes(b ^ 0x5A for b in d), lambda d: bytes(b ^ 0x5A for b in d))


def one(text):
    return v.collect([("field", text)])


class ParseTests(unittest.TestCase):
    def test_the_issues_own_examples(self):
        cases = [
            (ISSUE, [("SYSTEM_NAME", "TEXTBOX", ("string",), "System Name"),
                     ("SRC_ADDR", "LIBRARY", ("ip_library_12",), "Source"),
                     ("DEST_ADDR", "LIBRARY", ("ip_library_12",), "Destination")]),
            ('We need access from {SOURCE_ADDRESS:TEXTBOX} to {DESTINATION_ADDRESS:DROPDOWN("dest_1","dest_2")}.',
             [("SOURCE_ADDRESS", "TEXTBOX", (), "Source address"),
              ("DESTINATION_ADDRESS", "DROPDOWN", ("dest_1", "dest_2"), "Destination address")]),
            ('{URGENT:CHECKBOX("Emergency change"):"Urgent"}', [("URGENT", "CHECKBOX", ("Emergency change",), "Urgent")]),
            ("{ IP : textbox ( 'ip_v4' , 'ip_v6' ) : 'Server' }", [("IP", "TEXTBOX", ("ip_v4", "ip_v6"), "Server")]),
        ]
        for text, want in cases:
            with self.subTest(text=text):
                self.assertEqual([(x.name, x.kind, x.args, x.label) for x in one(text)], want)

    def test_a_bare_name_repeats_a_variable_and_identical_definitions_agree(self):
        found = v.collect([("a", "{HOST:TEXTBOX:'Host'} and {HOST}"), ("b", "{host} {HOST:TEXTBOX:'Host'}")])
        self.assertEqual([x.name for x in found], ["HOST"])

    def test_one_name_two_meanings_is_refused(self):
        with self.assertRaises(v.VariableError) as cm:
            v.collect([("Summary", "{HOST:TEXTBOX}"), ("Detail", "{HOST:DROPDOWN('a')}")])
        self.assertIn("defined twice", str(cm.exception))
        self.assertIn("Detail", str(cm.exception))

    def test_a_name_used_but_never_defined_is_refused_by_its_spelling(self):
        with self.assertRaises(v.VariableError) as cm:
            v.collect([("Summary", "from {Src_Host} to {DEST:TEXTBOX}")])
        self.assertIn("{Src_Host} is used but never defined", str(cm.exception))

    def test_braces_dollars_and_placeholders(self):
        cases = [
            ("{{literal}} braces", ["{literal} braces"]),
            ("on ${today}", ["on ${today}"]),
            ("cost $${X:TEXTBOX}", ["cost $$", "X"]),
            ("$$$today", ["$$$today"]),
        ]
        for text, want in cases:
            with self.subTest(text=text):
                got = [s.name if isinstance(s, v.Ref) else s for s in v.parse(text)]
                self.assertEqual(got, want)

    def test_escaped_text_renders_the_way_it_did_before_variables(self):
        from string import Template
        for text in ["plain", "{a}", "}{", "a {{b}} c", "${today} and {x}", "$${x}", "{X:TEXTBOX}", ""]:
            with self.subTest(text=text):
                self.assertFalse(v.has_variables(v.escape(text)))
                self.assertEqual(v.render(v.escape(text), {}, {"today": "T"}), Template(text).substitute(today="T"))

    def test_definitions_that_cant_be_read_say_what_to_change(self):
        cases = [
            ("{SRC:LIBRARY:('ip_library_12')}", "no colon before the bracket"),
            ("{X:TEXTBOX('ipv4')}", "'ipv4' isn't a check"),
            ("{X:DROPDOWN()}", "needs the values to offer"),
            ("{X:DROPDOWN('a','A')}", "same value twice"),
            ("{X:LIBRARY('a','b')}", "one library name"),
            ("{X:LIBRARY('has space')}", "one library name"),
            ('{X:CHECKBOX("a","b")}', "one description at most"),
            ("{X:SLIDER}", "must be one of"),
            ("{X:TEXTBOX('string}", "never closed"),
            ("{X:TEXTBOX('string' 'int')}", "expected , or )"),
            ("{X:TEXTBOX:Label}", "quoted value"),
            ("{9X:TEXTBOX}", "needs a NAME"),
            ("{see attached}", "expected : or }"),
            ("a } b", "write }} for a literal brace"),
            ("${today", "never closed"),
        ]
        for text, needle in cases:
            with self.subTest(text=text):
                with self.assertRaises(v.VariableError) as cm:
                    one(text)
                self.assertIn(needle, str(cm.exception))

    def test_a_definition_writes_back_the_way_it_reads(self):
        for text in [ISSUE, '{U:CHECKBOX("say \\"yes\\""):"Urgent"}', "{E:DROPDOWN('prod','test')}"]:
            with self.subTest(text=text):
                for x in one(text):
                    self.assertEqual(one(x.definition()), [x])


class ValueTests(unittest.TestCase):
    def test_textbox_checks(self):
        cases = [
            (("ip_v4",), "10.0.0.1", True), (("ip_v4",), "10.0.0", False), (("ip_v4",), "::1", False),
            (("ip_v4",), "10.0.0.0/24", False),
            (("ip_v6",), "fe80::1", True), (("ip_v6",), "10.0.0.1", False),
            (("ip_v4", "ip_v6"), "fe80::1", True), (("ip_v4", "ip_v6"), "host", False),
            (("int",), "-42", True), (("int",), "4.2", False), (("int",), "1_000", False),
            (("float",), "4.2", True), (("float",), "1e3", True), (("float",), "nan", False),
            (("bool",), "Yes", True), (("bool",), "maybe", False),
            (("string",), "anything at all", True), ((), "no checks", True),
        ]
        for rules, value, ok in cases:
            with self.subTest(rules=rules, value=value):
                var = v.Variable("X", "TEXTBOX", rules, "Thing")
                if ok:
                    self.assertEqual(v.check(var, value), value)
                else:
                    with self.assertRaises(v.VariableError) as cm:
                        v.check(var, value)
                    self.assertIn("Thing must be", str(cm.exception))

    def test_dropdowns_take_their_own_spelling_and_checkboxes_say_yes_or_no(self):
        drop = v.Variable("E", "DROPDOWN", ("Prod", "Test"), "Environment")
        self.assertEqual(v.check(drop, " prod "), "Prod")
        with self.assertRaises(v.VariableError) as cm:
            v.check(drop, "dev")
        self.assertIn("one of: Prod, Test", str(cm.exception))
        box = v.Variable("U", "CHECKBOX", ("Emergency",), "Urgent")
        self.assertEqual([v.check(box, x) for x in (True, False, "yes", "0", None, "")],
                         ["Yes", "No", "Yes", "No", "No", "No"])

    def test_every_missing_value_is_named_at_once_and_checkboxes_are_never_missing(self):
        found = one("{A:TEXTBOX:'Alpha'} {B:DROPDOWN('x'):'Beta'} {C:CHECKBOX:'Gamma'}")
        with self.assertRaises(v.VariableError) as cm:
            v.resolve(found, {"a": " "})
        self.assertEqual(str(cm.exception), "Give a value for Alpha (A), Beta (B).")

    def test_a_value_for_a_variable_the_template_doesnt_have_is_refused(self):
        with self.assertRaises(v.VariableError) as cm:
            v.resolve(one("{A:TEXTBOX}"), {"A": "1", "B": "2"})
        self.assertIn("B isn't a variable in this template", str(cm.exception))

    def test_typed_values_go_in_exactly_as_given(self):
        text = "Fix {A:TEXTBOX:'Alpha'} on $today, {{not a var}} and $$ {A}"
        filled = v.resolve(one(text), {"A": "${today} $5 {x} }}"})
        self.assertEqual(v.render(text, filled, {"today": "2026-10-11"}),
                         "Fix ${today} $5 {x} }} on 2026-10-11, {not a var} and $ ${today} $5 {x} }}")

    def test_var_assignments(self):
        self.assertEqual(v.parse_assignments(["A=x=y", "b_2="]), {"A": "x=y", "b_2": ""})
        for bad in ["A", "=x", "9A=x", "A B=x"]:
            with self.subTest(bad=bad):
                with self.assertRaises(v.VariableError):
                    v.parse_assignments([bad])


class LibraryStoreTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.store = libs.Store(self.dir / "libraries", protector=XOR)

    def publish(self, body, name="lib.json"):
        path = self.dir / name
        path.write_text(json.dumps(body), encoding="utf-8")
        return path

    def test_import_keeps_an_encrypted_copy_that_reads_back(self):
        lib, before = self.store.import_file(self.publish(PUBLISHED))
        self.assertIsNone(before)
        self.assertEqual(self.store.names(), ["ip_library_12"])
        on_disk = (self.dir / "libraries" / "ip_library_12.library").read_bytes()
        self.assertTrue(on_disk.startswith(libs.MAGIC + b"dpapi\n"))
        for secret in (b"10.20.0.0/24", b"web-servers", b"DMZ"):
            self.assertNotIn(secret, on_disk)
        fresh = libs.Store(self.dir / "libraries", protector=XOR).load("IP_LIBRARY_12")
        self.assertEqual(fresh.fill("DB-SERVERS"), "10.30.0.5, 10.30.0.6")
        self.assertEqual((fresh.version, fresh.source, len(fresh.entries)), ("2026-10-01", "lib.json", 3))
        self.assertTrue(fresh.imported_at.endswith("Z"))

    def test_importing_again_replaces_and_says_what_it_had(self):
        self.store.import_file(self.publish(PUBLISHED))
        lib, before = self.store.import_file(self.publish({"library": "ip_library_12", "entries": {"a": "1"}}))
        self.assertEqual((before, len(lib.entries)), (3, 1))
        self.assertEqual(libs.Store(self.dir / "libraries", protector=XOR).load("ip_library_12").fill("a"), "1")

    def test_a_name_library_never_keeps_the_values(self):
        body = dict(PUBLISHED, renders="name")
        lib, _ = self.store.import_file(self.publish(body))
        self.assertEqual({e.value for e in lib.entries}, {""})
        raw = XOR.unprotect((self.dir / "libraries" / "ip_library_12.library").read_bytes()[len(libs.MAGIC) + 6:])
        self.assertNotIn(b"10.20.0.0", raw)
        self.assertEqual(lib.fill("web-servers"), "web-servers")

    def test_search_looks_at_names_and_descriptions_never_values(self):
        lib, _ = self.store.import_file(self.publish(PUBLISHED))
        self.assertEqual([e.name for e in lib.search("servers")], ["db-servers", "web-servers"])
        self.assertEqual([e.name for e in lib.search("payroll")], ["db-servers"])
        self.assertEqual([e.name for e in lib.search("web tier")], ["web-servers"])
        self.assertEqual(lib.search("10.20"), [])
        self.assertEqual([e.name for e in lib.search("")], ["db-servers", "jump-hosts", "web-servers"])
        self.assertEqual(lib.entry_json(lib.entries[0]), {"name": "web-servers", "description": "DMZ web tier"})

    def test_an_entry_that_isnt_there_suggests_close_ones(self):
        lib, _ = self.store.import_file(self.publish(PUBLISHED))
        with self.assertRaises(libs.LibraryError) as cm:
            lib.fill("web")
        self.assertIn("Did you mean: web-servers?", str(cm.exception))
        with self.assertRaises(libs.LibraryError) as cm:
            lib.fill("zzz")
        self.assertIn("--search", str(cm.exception))

    def test_published_files_that_cant_be_used_say_why(self):
        cases = [
            ([], "JSON object"),
            ({"entries": {"a": "1"}}, '"library"'),
            ({"library": "a b", "entries": {"a": "1"}}, '"library"'),
            ({"library": "x", "renders": "both", "entries": {"a": "1"}}, '"renders"'),
            ({"library": "x", "entries": []}, '"entries"'),
            ({"library": "x", "entries": [{"value": "1"}]}, '"name"'),
            ({"library": "x", "entries": [{"name": "a", "value": "1"}, {"name": "A ", "value": "2"}]}, "both named"),
            ({"library": "x", "entries": [{"name": "a"}]}, '"value"'),
            ({"library": "x", "entries": [{"name": "a", "value": []}]}, '"value"'),
            ({"library": "x", "entries": [{"name": "a", "value": "1", "description": 5}]}, '"description"'),
            ({"library": "x", "version": 3, "entries": {"a": "1"}}, '"version"'),
        ]
        for body, needle in cases:
            with self.subTest(body=body):
                with self.assertRaises(libs.LibraryError) as cm:
                    self.store.import_file(self.publish(body))
                self.assertIn(needle, str(cm.exception))
        self.assertEqual(self.store.names(), [])
        (self.dir / "bad.json").write_text("{nope", encoding="utf-8")
        with self.assertRaises(libs.LibraryError) as cm:
            self.store.import_file(self.dir / "bad.json")
        self.assertIn("not valid JSON", str(cm.exception))
        with self.assertRaises(libs.LibraryError) as cm:
            self.store.import_file(self.dir / "missing.json")
        self.assertIn("No file at", str(cm.exception))

    def test_without_dpapi_or_the_switch_libraries_are_refused(self):
        store = libs.Store(self.dir / "libraries", protector=None)
        with self.assertRaises(libs.LibraryError) as cm:
            store.import_file(self.publish(PUBLISHED))
        self.assertIn(libs.UNPROTECTED_ENV, str(cm.exception))
        self.assertFalse(store.encrypted)

    @unittest.skipIf(os.name == "nt", "Windows never stores a library unencrypted")
    def test_an_unencrypted_copy_isnt_read_as_an_encrypted_one(self):
        libs.Store(self.dir / "libraries", protector=libs.UNPROTECTED).import_file(self.publish(PUBLISHED))
        with self.assertRaises(libs.LibraryError) as cm:
            self.store.load("ip_library_12")
        self.assertIn("was saved unencrypted", str(cm.exception))

    def test_damaged_or_foreign_files_say_import_again(self):
        self.store.import_file(self.publish(PUBLISHED))
        path = self.dir / "libraries" / "ip_library_12.library"
        path.write_bytes(path.read_bytes()[:-9])
        with self.assertRaises(libs.LibraryError) as cm:
            libs.Store(self.dir / "libraries", protector=XOR).load("ip_library_12")
        self.assertIn("damaged", str(cm.exception))
        path.write_bytes(b"hello")
        with self.assertRaises(libs.LibraryError) as cm:
            libs.Store(self.dir / "libraries", protector=XOR).load("ip_library_12")
        self.assertIn("isn't a library Heimdall saved", str(cm.exception))
        self.store.import_file(self.publish(PUBLISHED))        # importing again is the fix
        self.assertEqual(libs.Store(self.dir / "libraries", protector=XOR).load("ip_library_12").fill("jump-hosts"),
                         "10.40.0.9")

    def test_remove(self):
        self.store.import_file(self.publish(PUBLISHED))
        self.assertEqual(self.store.remove("IP_Library_12"), "ip_library_12")
        self.assertEqual(self.store.names(), [])
        with self.assertRaises(libs.LibraryError):
            self.store.remove("ip_library_12")
        with self.assertRaises(libs.LibraryError) as cm:
            self.store.load("ip_library_12")
        self.assertIn("heimdall library import", str(cm.exception))


@unittest.skipUnless(dpapi.AVAILABLE, "DPAPI is Windows-only")
class DpapiTests(unittest.TestCase):
    def test_round_trip_and_entropy(self):
        sealed = dpapi.protect(b"10.20.0.0/24", "test", b"pepper")
        self.assertNotIn(b"10.20.0.0/24", sealed)
        self.assertEqual(dpapi.unprotect(sealed, b"pepper"), b"10.20.0.0/24")
        with self.assertRaises(dpapi.DpapiError):
            dpapi.unprotect(sealed, b"other")

    def test_windows_refuses_unencrypted_libraries(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = libs.Store(Path(tmp), protector=libs.UNPROTECTED)
            with self.assertRaises(libs.LibraryError) as cm:
                store.import_file(Path(tmp) / "x.json")
            self.assertIn("unencrypted on Windows", str(cm.exception))
        self.assertIs(libs.default_protector(), libs.DPAPI)


class LibraryCase(Base):
    """Heimdall's Base (a temporary ASGARD_HOME and form), with libraries this computer can keep."""

    def setUp(self):
        super().setUp()
        self._switch = os.environ.get(libs.UNPROTECTED_ENV)
        os.environ[libs.UNPROTECTED_ENV] = "1"       # ignored on Windows, which uses DPAPI

    def tearDown(self):
        if self._switch is None:
            os.environ.pop(libs.UNPROTECTED_ENV, None)
        else:
            os.environ[libs.UNPROTECTED_ENV] = self._switch
        super().tearDown()

    def publish(self, body=PUBLISHED):
        path = self.dir / "published.json"
        path.write_text(json.dumps(body), encoding="utf-8")
        return path


class TemplateTests(LibraryCase):
    def test_saving_refuses_a_template_whose_variables_disagree(self):
        err = self.fails("template", "save", "T", "--set", "Short description={A:TEXTBOX}",
                         "--set", "Requested for={A:DROPDOWN('x')}")
        self.assertIn("defined twice", err)
        err = self.fails("template", "save", "T", "--set", "Short description={A}")
        self.assertIn("never defined", err)
        err = self.fails("template", "save", "T", "--set", "Short description={A:TEXTBOX('ipv4')}")
        self.assertIn("'ipv4'", err)
        self.assertEqual(tpl.Store().names(), [])

    def test_a_fill_asks_for_every_variable_then_fills_them_in(self):
        self.ok("library", "import", str(self.publish()))
        self.ok("template", "save", "Access", "--set", "Short description=" + ISSUE + " ($today)",
                "--set", "Requested for={DEST_ADDR}")
        err = self.fails("fill", "-t", "Access", "--dry-run")
        self.assertIn('--var "SYSTEM_NAME=..." --var "SRC_ADDR=..." --var "DEST_ADDR=..."', err)
        err = self.fails("fill", "-t", "Access", "--dry-run", "--var", "SYSTEM_NAME=Payroll")
        self.assertIn("Give a value for Source (SRC_ADDR), Destination (DEST_ADDR).", err)
        out = json.loads(self.ok("fill", "-t", "Access", "--dry-run", "--json", "--var", "system_name=Payroll $5",
                                 "--var", "SRC_ADDR=WEB-SERVERS", "--var", "DEST_ADDR=jump-hosts"))
        fields = {f["label"]: f["value"] for f in out["fields"]}
        self.assertRegex(fields["Short description"], r"^We need access to Payroll \$5 from 10\.20\.0\.0/24 to "
                                                       r"10\.40\.0\.9\. \(\d{4}-\d\d-\d\d\)$")
        self.assertEqual(fields["Requested for"], "10.40.0.9")
        self.assertEqual([(x["name"], x["value"]) for x in out["variables"]],
                         [("SYSTEM_NAME", "Payroll $5"), ("SRC_ADDR", "10.20.0.0/24"), ("DEST_ADDR", "10.40.0.9")])

    def test_a_template_whose_library_isnt_here_needs_fixing(self):
        self.ok("template", "save", "Access", "--set", "Short description=" + ISSUE)
        code, out, _ = self.run_cli("template", "show", "Access")
        self.assertEqual(code, 1)
        self.assertIn("library ip_library_12 isn't on this computer", out)
        self.assertIn("isn't on this computer", self.fails("fill", "-t", "Access", "--dry-run", "--var", "SYSTEM_NAME=x",
                                                           "--var", "SRC_ADDR=a", "--var", "DEST_ADDR=b"))

    def test_a_version_1_file_keeps_its_braces_as_text(self):
        tpl.templates_path().parent.mkdir(parents=True, exist_ok=True)
        tpl.templates_path().write_text(json.dumps({"version": 1, "templates": {"Old": {"values": {
            "Short description": "Patch {see ticket} on ${today}"}}}}), encoding="utf-8")
        out = json.loads(self.ok("fill", "-t", "Old", "--dry-run", "--json"))
        self.assertRegex(out["fields"][0]["value"], r"^Patch \{see ticket\} on \d{4}-\d\d-\d\d$")
        self.assertEqual(out["variables"], [])
        self.ok("template", "edit", "Old", "--description", "kept")       # writes version 2
        raw = json.loads(tpl.templates_path().read_text(encoding="utf-8"))
        self.assertEqual(raw["version"], 2)
        self.assertEqual(raw["templates"]["Old"]["values"]["Short description"], "Patch {{see ticket}} on ${today}")
        out = json.loads(self.ok("fill", "-t", "Old", "--dry-run", "--json"))
        self.assertRegex(out["fields"][0]["value"], r"^Patch \{see ticket\} on ")

    def test_a_newer_file_is_refused_not_rewritten(self):
        tpl.templates_path().parent.mkdir(parents=True, exist_ok=True)
        tpl.templates_path().write_text(json.dumps({"version": 3, "templates": {}}), encoding="utf-8")
        self.assertIn("reads versions 1, 2", self.fails("template", "list"))

    def test_form_defaults_take_variables_too_and_a_template_can_replace_them(self):
        form = json.loads(forms_path().read_text(encoding="utf-8"))
        form["fields"][2]["default"] = "{WHO:TEXTBOX:'Requester'}"
        forms_path().write_text(json.dumps(form), encoding="utf-8")
        self.ok("template", "save", "T", "--set", "Short description=x")
        self.assertIn("Requester", self.fails("fill", "-t", "T", "--dry-run"))
        self.ok("template", "edit", "T", "--set", "Requested for=Jane")
        self.ok("fill", "-t", "T", "--dry-run")


class CliLibraryTests(LibraryCase):
    def test_import_list_show_remove(self):
        out = self.ok("library", "import", str(self.publish()))
        self.assertIn("Imported the library ip_library_12: 3 entries, version 2026-10-01.", out)
        self.assertIn("Updated the library ip_library_12: 3 entries, was 3", self.ok("library", "import",
                                                                                    str(self.publish())))
        listed = json.loads(self.ok("library", "list", "--json"))["libraries"]
        self.assertEqual([(x["name"], x["entries"], x["renders"]) for x in listed], [("ip_library_12", 3, "value")])
        shown = self.ok("library", "show", "ip_library_12", "--search", "servers", "--limit", "1")
        self.assertIn("db-servers", shown)
        self.assertIn("and 1 more", shown)
        body = json.loads(self.ok("library", "show", "ip_library_12", "--json"))
        self.assertNotIn("10.", json.dumps(body["shown"]))
        self.assertIn("--limit", self.fails("library", "show", "ip_library_12", "--limit", "0"))
        self.ok("library", "remove", "ip_library_12")
        self.assertIn("No libraries yet", self.ok("library", "list"))
        self.assertIn("No library named", self.fails("library", "show", "ip_library_12"))

    def test_libraries_folder_option(self):
        folder = self.dir / "elsewhere"
        self.ok("--libraries", str(folder), "library", "import", str(self.publish()))
        self.assertTrue((folder / "ip_library_12.library").is_file())
        self.assertIn("No libraries yet", self.ok("library", "list"))

    @unittest.skipIf(os.name == "nt", "Windows has DPAPI")
    def test_without_the_switch_import_says_how_to_try_it(self):
        os.environ.pop(libs.UNPROTECTED_ENV, None)
        self.assertIn(libs.UNPROTECTED_ENV, self.fails("library", "import", str(self.publish())))


class BackendTests(LibraryCase):
    def setUp(self):
        super().setUp()
        self.b = Backend()
        self.b.import_library(str(self.publish()))
        self.b.save_template("", {"name": "Access", "values": {
            "Short description": ISSUE + " {URGENT:CHECKBOX(\"Emergency change\"):'Urgent'}",
            "Category": "{ENV:DROPDOWN('prod','test'):'Environment'}"}})

    def test_the_page_gets_each_variable_to_draw(self):
        t = self.b.template("Access")
        self.assertEqual(t["problems"], [])
        self.assertEqual([(x["name"], x["kind"], x["label"]) for x in t["variables"]],
                         [("SYSTEM_NAME", "textbox", "System Name"), ("SRC_ADDR", "library", "Source"),
                          ("DEST_ADDR", "library", "Destination"), ("URGENT", "checkbox", "Urgent"),
                          ("ENV", "dropdown", "Environment")])
        self.assertEqual(t["variables"][3]["description"], "Emergency change")
        self.assertEqual(t["variables"][4]["options"], ["prod", "test"])
        self.assertEqual([x["hint"] for x in t["variables"]],
                         ["Text", "An entry from ip_library_12", "An entry from ip_library_12", "Checked or not",
                          "One of prod, test"])
        self.assertEqual(v.Variable("I", "TEXTBOX", ("ip_v4", "ip_v6")).hint(), "An IPv4 address or an IPv6 address")
        st = self.b.state()
        self.assertEqual([(x["name"], x["variables"]) for x in st["templates"]], [("Access", 5)])
        self.assertEqual([x["name"] for x in st["libraries"]], ["ip_library_12"])

    def test_a_picker_search_shows_names_and_descriptions_only(self):
        found = self.b.library_search("ip_library_12", "serv", 1)
        self.assertEqual(found, {"library": "ip_library_12", "total": 2,
                                 "entries": [{"name": "db-servers", "description": "Payroll DB"}]})

    def test_fill_hands_the_values_to_the_command_line_and_library_picks_by_name(self):
        values = {"SYSTEM_NAME": "Payroll", "SRC_ADDR": "web-servers", "DEST_ADDR": "db-servers",
                  "URGENT": True, "ENV": "test"}
        lines = self.b.dry_run("Access", values)["lines"]
        self.assertIn("  {SRC_ADDR} Source: 10.20.0.0/24", lines)
        self.assertIn("  {URGENT} Urgent: Yes", lines)
        argv = self.b.fill_command("Access", dict(values, URGENT=False))
        self.assertEqual(argv[argv.index("fill"):], [
            "fill", "--template", "Access", "--var", "SYSTEM_NAME=Payroll", "--var", "SRC_ADDR=web-servers",
            "--var", "DEST_ADDR=db-servers", "--var", "URGENT=false", "--var", "ENV=test"])
        self.assertFalse(any("10." in a for a in argv), argv)
        with self.assertRaises(tpl.TemplateError) as cm:
            self.b.fill_command("Access", {"SYSTEM_NAME": "Payroll"})
        self.assertIn("Give a value for Source (SRC_ADDR)", str(cm.exception))

    def test_removing_the_library_flags_the_template(self):
        self.b.remove_library("ip_library_12")
        row = self.b.state()["templates"][0]
        self.assertEqual(row["variables"], 0)
        self.assertIn("isn't on this computer", row["problems"][0])
        self.assertEqual(self.b.dashboard()[1]["label"], "Need fixing")

    def test_a_computer_that_cant_keep_libraries_says_so(self):
        with mock.patch("heimdall.library.default_protector", return_value=None):
            st = self.b.libraries()
            self.assertIn(libs.UNPROTECTED_ENV, st["libraries_unavailable"])
            with self.assertRaises(libs.LibraryError):
                self.b.import_library(str(self.publish()))
        with self.assertRaises(libs.LibraryError):
            self.b.import_library("  ")


def forms_path():
    from heimdall import form
    return form.form_path()


class FormDefaultTests(unittest.TestCase):
    def test_a_broken_variable_in_the_form_file_is_a_form_error(self):
        from heimdall import form
        from test_heimdall import FORM
        body = dict(FORM, fields=[{"label": "A", "default": "{X:NOPE}"}])
        with self.assertRaises(FormError) as cm:
            form.parse(body, Path("heimdall.json"))
        self.assertIn('default for "A"', str(cm.exception))


if __name__ == "__main__":
    unittest.main()
