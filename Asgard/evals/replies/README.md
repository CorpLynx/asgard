Recorded model replies, one `<case id>.txt` per case in `cases/review_cases.json`: paste
`grading.model_input(case)` into a chat you are allowed to use and save its answer here. These are
the outputs of synthetic cases, so they are safe to commit. `good/` holds replies the grader must
accept and `bad/` ones it must refuse (tests/test_evals_grading.py); neither is read by the evals.
