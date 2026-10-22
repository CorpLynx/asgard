# Evals (draft)

Does a model's reply to Baldur's AI review prompt pass the rules Baldur applies to it? Dev only: nothing
here is shipped, imported by `asgard/` or `apps/`, or meant for the workstation.

**What is graded.** `apps/baldur/prompts/review.md` asks a model to adjust one day's minutes. Baldur already
refuses a bad reply in code (`muninn.baldur.check_review`: the day never rises, every adjustment cites
evidence, nothing goes below what Jira holds, no new tickets). `grading.grade()` runs that same check plus
what a case expects (a flag for the person, a ticket left alone). So **a pass means Baldur would have stored
the reply.** The five cases in `cases/review_cases.json` are synthetic (`DEMO-n` keys, invented SHAs).

**Getting replies.** Either paste `grading.model_input(case)` into the chat you are allowed to use and save the
answer as `replies/<case id>.txt` (the clipboard tier; no key, no network), or point `ASGARD_EVAL_ENDPOINT`,
`ASGARD_EVAL_KEY` and `ASGARD_EVAL_MODEL` at an OpenAI-compatible endpoint (`model.py`). Send only these
synthetic cases to a model, never a real pack (Asgard rule 6).

## DeepEval

```
python -m pip install -r evals/requirements.txt
DEEPEVAL_TELEMETRY_OPT_OUT=YES python -m pytest evals/test_review_deepeval.py
```

`ReviewRulesMetric` is the gate: deterministic, no judge model. `GEval` "injection resistance" is an
optional second opinion and runs only when a judge is configured.

## Promptfoo

```
cd Asgard && PROMPTFOO_DISABLE_TELEMETRY=1 npx promptfoo@0.124.1 eval -c evals/promptfooconfig.yaml
```

Needs Node. Same cases, same grader (a Python assertion). Its use is the matrix: uncomment more
providers in the config and compare the prompt across models side by side.

## Using both?

They overlap: both run the same cases through the same grader, and neither is needed to gate a reply (the
grader is plain Python, tested in the ordinary suite by `tests/test_evals_grading.py`). Keep both only if each
earns its place:

- **Promptfoo** for changing `review.md` or trying models: a table of cases x models, no Python test code.
- **DeepEval** for a pytest gate in CI, and later for what Promptfoo is weaker at: Ysildir's tool choice and
  an agent's multi-step behaviour.

If one has to go, keep DeepEval (Python, pinned in a requirements file, in the repo's test habit) and drop
Promptfoo (a Node toolchain this Python-only project doesn't otherwise have).

## Not done

- No live-model run yet; only recorded `replies/good` and `replies/bad` were run (all pass, all fail as expected).
- The GEval judge test has never run (needs a judge model).
- Not in CI. Running it there needs a key or recorded replies in the repo.
- Ysildir tool-choice and agent-guide evals.
