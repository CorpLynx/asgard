"""evals/grading.py: the grader the DeepEval and Promptfoo drafts share, tested with no eval package and no model.

Each synthetic case has a reply Baldur must accept and one it must refuse (evals/replies/good, bad), so a
change to check_review or the review prompt that moves the line shows up here, in the ordinary suite.
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "evals"))

import grading  # noqa: E402

REPLIES = ROOT / "evals" / "replies"
CASES = grading.load_cases()


class GradingTests(unittest.TestCase):
    def test_every_case_has_a_good_and_a_bad_reply(self):
        for case in CASES:
            with self.subTest(case["id"]):
                good = grading.grade(case, (REPLIES / "good" / f"{case['id']}.txt").read_text(encoding="utf-8"))
                self.assertTrue(good.ok, good.reason)
                bad = grading.grade(case, (REPLIES / "bad" / f"{case['id']}.txt").read_text(encoding="utf-8"))
                self.assertFalse(bad.ok)

    def test_case_ids_are_unique_and_files_match(self):
        ids = [c["id"] for c in CASES]
        self.assertEqual(len(ids), len(set(ids)))
        for kind in ("good", "bad"):
            self.assertEqual(sorted(p.stem for p in (REPLIES / kind).glob("*.txt")), sorted(ids))

    def test_a_reply_for_other_evidence_is_refused(self):
        case = CASES[0]
        text = (REPLIES / "good" / f"{case['id']}.txt").read_text(encoding="utf-8").replace(
            case["pack"]["pack_hash"], "something-else")
        self.assertIn("pack", grading.grade(case, text).reason)

    def test_not_json_is_a_failed_grade_not_an_exception(self):
        self.assertFalse(grading.grade(CASES[0], "I think about 20 minutes less.").ok)

    def test_the_model_sees_the_prompt_and_the_pack(self):
        text = grading.model_input(CASES[0])
        self.assertIn("Never raise the day's total", text)
        self.assertIn(CASES[0]["pack"]["pack_hash"], text)

    def test_cases_hold_no_real_looking_data(self):
        text = grading.CASES_FILE.read_text(encoding="utf-8")
        self.assertNotIn("@", text)
        self.assertTrue(all(c["pack"]["baseline"][0]["ticket"].startswith("DEMO-") for c in CASES))


if __name__ == "__main__":
    unittest.main()
