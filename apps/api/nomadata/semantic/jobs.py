"""Background semantic-model builds — the client submits a job and polls it.

A generate/enhance run does schema introspection plus batched AI enrichment,
which is too slow for a single request. The work runs as an in-process asyncio
task; the client polls ``GET .../semantic/jobs/{id}`` for progress and, when the
job is ``done``, reloads the (already saved) draft. Jobs are in-memory and
ephemeral — fine for a single-process dev/app server.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Callable, Coroutine
from typing import Any

from nomadata.core.interfaces.semantic_model import SemanticModel
from nomadata.core.models import GenerationJob, JobStatus
from nomadata.core.registry import Registry
from nomadata.logging import get_logger
from nomadata.semantic.suggester import SemanticSuggester

log = get_logger()

_Coro = Coroutine[Any, Any, None]


class SemanticJobRunner:
    def __init__(self, registry: Registry, semantic: SemanticModel) -> None:
        self._registry = registry
        self._semantic = semantic
        self._jobs: dict[str, GenerationJob] = {}
        self._tasks: set[asyncio.Task[None]] = set()

    def get(self, job_id: str) -> GenerationJob | None:
        return self._jobs.get(job_id)

    def start_generate(self, source_id: str, use_ai: bool) -> GenerationJob:
        job = self._new(source_id, "generate")
        self._spawn(job, self._run_generate(job, source_id, use_ai))
        return job

    def start_enhance(self, source_id: str) -> GenerationJob:
        job = self._new(source_id, "enhance")
        self._spawn(job, self._run_enhance(job, source_id))
        return job

    def _new(self, source_id: str, kind: str) -> GenerationJob:
        job = GenerationJob(id=uuid.uuid4().hex, source_id=source_id, kind=kind)
        self._jobs[job.id] = job
        return job

    def _spawn(self, job: GenerationJob, coro: _Coro) -> None:
        task = asyncio.create_task(self._guard(job, coro))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _guard(self, job: GenerationJob, coro: _Coro) -> None:
        try:
            await coro
            job.status = JobStatus.done
        except Exception as exc:  # noqa: BLE001 - surface any failure to the poller
            job.status = JobStatus.error
            job.error = str(exc)
            log.warning("semantic.job.failed", job=job.id, kind=job.kind, error=str(exc))

    @staticmethod
    def _progress(job: GenerationJob) -> Callable[[int, int], None]:
        def report(done: int, total: int) -> None:
            job.done = done
            job.total = total

        return report

    async def _run_generate(self, job: GenerationJob, source_id: str, use_ai: bool) -> None:
        source = self._registry.get_data_source(source_id)
        catalog = await source.inspect_schema()
        provider = self._registry.active_provider()
        suggester = SemanticSuggester(provider)
        graph = suggester.heuristic(catalog)
        if use_ai and provider is not None:
            graph = await suggester.enrich_batched(graph, on_progress=self._progress(job))
        await self._semantic.save_draft(graph.model_copy(update={"source_id": source_id}))

    async def _run_enhance(self, job: GenerationJob, source_id: str) -> None:
        provider = self._registry.active_provider()
        if provider is None:
            raise RuntimeError("AI provider not configured.")
        graph = await self._semantic.get_draft(source_id)
        if graph is None:
            raise RuntimeError("No semantic model to enhance.")
        suggester = SemanticSuggester(provider)
        enriched = await suggester.enrich_batched(graph, on_progress=self._progress(job))
        await self._semantic.save_draft(enriched.model_copy(update={"source_id": source_id}))
