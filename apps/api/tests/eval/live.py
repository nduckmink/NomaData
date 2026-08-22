"""Live eval — the real model answers, scored against a question set.

Run by hand against a running API that has an AI provider configured and a
published model. It is NOT a CI gate: it measures how often the model maps a
question to the expected query, and prints a table plus a score.

    # from apps/api, with `pnpm infra` + api running and a key in Settings:
    uv run python -m tests.eval.live scp_mysql

The question set defaults to ``questions.<source>.json`` beside this file,
falling back to ``questions.json``, so a new source needs a file rather than a
remembered environment variable. A gold answer names metrics and dimensions in
THAT model's published names — the file and the model belong together.

    --url   the API (default http://localhost:8000)
    --file  a question set somewhere else
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import httpx


def _questions_for(source: str, given: str | None) -> Path:
    """The question set to score against.

    Named after the source, because gold answers quote one model's metric names
    and are wrong against any other. A shared default was how a run scored a new
    model against last month's names and reported everything as a miss.
    """
    if given:
        return Path(given)
    here = Path(__file__).parent
    named = here / f"questions.{source}.json"
    return named if named.exists() else here / "questions.json"


def _bare(name: str) -> str:
    """A name without its entity prefix — "Doanh nghiep.Name" and "Name" are the
    same dimension, and which form the model writes is not what is being scored."""
    return name.rsplit(".", 1)[-1].split(": ")[-1].strip().casefold()


def _matches(expect: dict[str, Any], query: dict[str, Any] | None) -> bool:
    if query is None:
        return False
    if {_bare(m) for m in query.get("measures", [])} != {
        _bare(m) for m in expect.get("measures", [])
    }:
        return False
    if "dimensions" in expect and {_bare(d) for d in query.get("dimensions", [])} != {
        _bare(d) for d in expect["dimensions"]
    }:
        return False
    return not ("range" in expect and (query.get("time") or {}).get("range") != expect["range"])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", help="a published data source name")
    parser.add_argument("--url", default="http://localhost:8000")
    parser.add_argument("--file", default=None, help="a question set to score against")
    args = parser.parse_args()

    source: str = args.source
    url: str = args.url
    questions = _questions_for(source, args.file)
    if not questions.exists():
        print(f"No question set at {questions}.")
        return 2

    print(f"{source} · {questions.name} · {url}\n")
    cases: list[dict[str, Any]] = json.loads(questions.read_text(encoding="utf-8"))
    hits = 0
    scored = 0
    non_answer_hits = 0
    declined: list[tuple[str, str]] = []
    failed: list[str] = []
    with httpx.Client(base_url=url, timeout=120) as client:
        for case in cases:
            question = case["question"]
            expect = case.get("expect", {})
            try:
                response = client.post(
                    f"/api/v1/datasources/{source}/chat", json={"question": question}
                )
                response.raise_for_status()
                turn = response.json()
            except Exception as exc:  # noqa: BLE001 - a manual script; report and go on
                print(f"[error] {question}\n        {exc}")
                failed.append(question)
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
                    declined.append((question, kind))
            elif expect.get("kind"):
                if kind == expect["kind"]:
                    mark = "match"
                    non_answer_hits += 1
                else:
                    mark = f"MISS (got {kind})"

            print(f"{mark:16} {question}")
            if kind == "answer":
                print(f"                 -> {turn.get('answer')}  |  {turn.get('explanation')}")

    # Report every denominator. "13/13 of the ones it answered" hides the
    # questions the agent would not answer at all -- and a question it declines
    # is a question the user cannot ask, which is the failure that matters most.
    answerable = sum(1 for c in cases if c.get("expect", {}).get("kind") == "answer")
    non_answer = len(cases) - answerable
    print("")
    print(f"answered correctly : {hits}/{answerable} questions with an expected query")
    if scored:
        print(f"  of those answered: {hits}/{scored} produced exactly the gold query")
    print(f"non-answers correct: {non_answer_hits}/{non_answer} (clarify / refuse)")
    if declined:
        print("")
        print(f"declined to answer ({len(declined)}):")
        for asked, got in declined:
            print(f"  [{got}] {asked}")
    if failed:
        print("")
        print(f"request failed ({len(failed)}):")
        for asked in failed:
            print(f"  {asked}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
