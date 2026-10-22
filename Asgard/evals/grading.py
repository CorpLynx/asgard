"""Grading Baldur's AI review replies, shared by the DeepEval tests and the Promptfoo config.

The judge is not a model. Baldur already refuses a bad reply in code (muninn.baldur.check_review),
and that is the pass/fail an eval should use: the same rules production applies, so an eval that
passes means Baldur would have stored the reply. Per-case `expect` adds what check_review can't
know (a flag the person should see, a ticket that must stay untouched).

Cases are synthetic: invented ticket keys, SHAs and subjects. Never put a real commit subject,
ticket or repository name here (Asgard rule 6: metadata only, nothing off the machine).

Standard library plus Asgard's own code; no eval package is imported here, so
tests/test_evals_grading.py runs in the ordinary suite.
"""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parent.parent
for folder in (ROOT, ROOT / "apps" / "baldur"):
    if str(folder) not in sys.path:
        sys.path.insert(0, str(folder))

from asgard.muninn import baldur as rules  # noqa: E402
from baldur import assist  # noqa: E402

CASES_FILE = Path(__file__).resolve().parent / "cases" / "review_cases.json"


@dataclass
class Grade:
    ok: bool
    reason: str

    @property
    def score(self) -> float:
        return 1.0 if self.ok else 0.0


def load_cases(path: Path = CASES_FILE) -> List[Dict[str, Any]]:
    return json.loads(path.read_text(encoding="utf-8"))["cases"]


def model_input(case: Dict[str, Any]) -> str:
    """Exactly what the clipboard tier pastes into a chat: the review prompt, then the pack."""
    return assist.clipboard_text(case["pack"])


def grade(case: Dict[str, Any], reply: Any) -> Grade:
    pack = case["pack"]
    data = _parse(reply)
    if isinstance(data, Grade):
        return data
    if data.get("pack") != pack["pack_hash"]:
        return Grade(False, f"the reply's pack is {data.get('pack')!r}, not {pack['pack_hash']!r}")
    baseline = {b["ticket"]: b["minutes"] for b in pack["baseline"]}
    floors = {b["ticket"]: b["in_jira"] for b in pack["baseline"] if b.get("in_jira")}
    try:
        checked = rules.check_review(pack["day"], baseline, assist.evidence_ids(pack), data,
                                     step=pack["round_to_minutes"], floors=floors)
    except rules.ReviewRejected as exc:
        return Grade(False, str(exc))
    expect = case.get("expect", {})
    touched = {a.ticket for a in checked.adjustments}
    for ticket in expect.get("untouched", []):
        if ticket in touched:
            return Grade(False, f"{ticket} must not be adjusted in this case")
    if expect.get("flags") and not checked.flags:
        return Grade(False, "this case needs a flag for the person to check, and the reply has none")
    return Grade(True, "check_review accepted it")


def _parse(reply: Any):
    try:
        return rules.parse_reply(reply)
    except rules.ReviewRejected as exc:
        return Grade(False, str(exc))
