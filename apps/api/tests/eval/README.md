# Agent eval set

Two modes, per docs/plans M3 §C4 / §H5.

## Offline (CI, no tokens)

`test_eval_offline.py` feeds each question's **gold plan** through the real
runtime + resolver against a fixed model. It checks the *harness*, not the
model's judgement: every gold query in `questions.json` must resolve against the
model, and clarify/refuse pass through. Runs with the normal suite:

```bash
uv run pytest tests/eval
```

## Live (manual, by hand)

`live.py` asks a **running** API the same questions and scores the real model's
query against the gold one:

```bash
# api running (pnpm api:dev), AI key set in /settings, a published model:
NOMADATA_EVAL_SOURCE=scp_mysql uv run python -m tests.eval.live
```

Env: `NOMADATA_EVAL_URL` (default `http://localhost:8000`), `NOMADATA_EVAL_FILE`.

`questions.json` is written for the offline fixture (metrics `Advance Amount`,
`Transaction Count`; dimension `Status`). For a real model, point
`NOMADATA_EVAL_FILE` at a set whose gold `measures`/`dimensions` use **that
model's** published names, and record the score in the M3 plan §F table.
