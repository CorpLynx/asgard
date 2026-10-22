"""The Python hooks promptfooconfig.yaml calls. Same cases and the same grader as the DeepEval tests."""

import grading
import model


def build_prompt(context):
    """Promptfoo prompt function: the review prompt and the case's pack, as the clipboard tier pastes them."""
    return grading.model_input(_case(context["vars"]["case_id"]))


def generate_tests(config=None):
    return [{"description": c["id"], "vars": {"case_id": c["id"]}} for c in grading.load_cases()]


def grade_reply(output, context):
    """Promptfoo assertion: pass when Baldur would store the reply."""
    verdict = grading.grade(_case(context["vars"]["case_id"]), output)
    return {"pass": verdict.ok, "score": verdict.score, "reason": verdict.reason}


def recorded_provider(prompt, options, context):
    """Provider that replays evals/replies/<case>.txt, so the config runs with no key and no network."""
    case = _case(context["vars"]["case_id"])
    reply = model.reply_for(case, prompt)
    return {"output": reply} if reply is not None else {"error": "no recorded reply for " + case["id"]}


def _case(case_id):
    return next(c for c in grading.load_cases() if c["id"] == case_id)


