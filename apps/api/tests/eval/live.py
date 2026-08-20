"""Live eval — the real model answers, scored against a question set.

Run by hand against a running API that has an AI provider configured and a
published model. It is NOT a CI gate: it measures how often the model maps a
question to the expected query, and prints a table plus a score.

    # from apps/api, with `pnpm infra` + api running and a key in Settings:
    NOMADATA_EVAL_SOURCE=scp_mysql uv run python -m tests.eval.live

Optional env: NOMADATA_EVAL_URL (default http://localhost:8000),
NOMADATA_EVAL_FILE (default questions.json). The checked-in questions.json is
written for the offline fixture; for a real model, point NOMADATA_EVAL_FILE at a
set whose gold `measures`/`dimensions` use THAT model's published names.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

import httpx

_URL = os.environ.get("NOMADATA_EVAL_URL", "http://localhost:8000")
_SOURCE = os.environ.get("NOMADATA_EVAL_SOURCE", "")
_FILE = os.environ.get("NOMADATA_EVAL_FILE", str(Path(__file__).parent / "questions.json"))


def _matches(expect: dict[str, Any], query: dict[str, Any] | None) -> bool:
    if query is None:
        return False
    if set(query.get("measures", [])) != set(expect.get("measures", [])):
        return False
    if "dimensions" in expect and set(query.get("dimensions", [])) != set(expect["dimensions"]):
        return False
    return not ("range" in expect and (query.get("time") or {}).get("range") != expect["range"])


def main() -> int:
    if not _SOURCE:
        print("Set NOMADATA_EVAL_SOURCE to a published data source name.")
        return 2
    cases: list[dict[str, Any]] = json.loads(Path(_FILE).read_text(encoding="utf-8"))
    hits = 0
    scored = 0
    with httpx.Client(base_url=_URL, timeout=120) as client:
        for case in cases:
            question = case["question"]
            expect = case.get("expect", {})
            try:
                response = client.post(
                    f"/api/v1/datasources/{_SOURCE}/ask", json={"question": question}
                )
                response.raise_for_status()
                turn = response.json()
            except Exception as exc:  # noqa: BLE001 - a manual script; report and go on
                print(f"[error] {question}\n        {exc}")
                continue

            kind = turn.get("kind")
            mark = kind
            if expect.get("kind") == "answer":
                if kind == "answer":
                    scored += 1
                    if _matches(expect, turn.get("query")):
                        hits += 1
                        mark = "match"
                    else:
                        mark = "MISS (query)"
                else:
                    mark = f"MISS (got {kind})"
            elif expect.get("kind"):
                mark = "match" if kind == expect["kind"] else f"MISS (got {kind})"

            print(f"{mark:16} {question}")
            if kind == "answer":
                print(f"                 -> {turn.get('answer')}  |  {turn.get('explanation')}")

    if scored:
        print(f"\nquery match: {hits}/{scored}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
