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

## Which question set

| File | For | Size |
| ---- | --- | ---- |
| `questions.json` | the offline fixture (`Advance Amount`, `Transaction Count`, `Status`) | 7 |
| `questions.scp_mysql.json` | the real `scp_mysql` model — Vietnamese questions, published metric names | 20 |

The offline test runs the fixture set; it checks the harness, not a model.
Scoring the *model* needs the real set:

```bash
NOMADATA_EVAL_SOURCE=scp_mysql NOMADATA_EVAL_FILE=tests/eval/questions.scp_mysql.json uv run python -m tests.eval.live
```

Record the score in the M3 plan §F table each time it is run.

For another model, copy `questions.scp_mysql.json` and rewrite the gold
`measures`/`dimensions` with *that* model's published names — a gold query that
does not resolve makes the score meaningless.

### What the real set covers

17 query cases + 3 non-answers, deliberately mixed:

- **the metric is named plainly** — "tổng số tiền ứng lương tháng này"
- **the metric is named loosely** — "doanh thu phí dịch vụ", "phí giao dịch
  trung bình đang là bao nhiêu"
- **a slice is asked for** — "chia theo trạng thái", "chia theo ngân hàng"
- **every relative range shape** — this_month, last_month, this_quarter,
  this_year, last_30_days, last_12_months, and no period at all
- **ordering and a limit** — "top doanh nghiệp theo số tiền ứng lương"
- **ambiguity that must clarify** — "cho tôi xem doanh thu" (two plausible
  metrics)
- **out of scope, and a write request** — both must refuse

Two cases are there specifically because `scp_mysql` publishes 138 metrics
while the model card shows 60: "doanh thu phí dịch vụ" and "phí giao dịch
trung bình" only score if the right metric survived the trim.
