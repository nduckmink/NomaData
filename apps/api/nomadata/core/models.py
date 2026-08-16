"""Shared data contracts between layers.

These Pydantic models are the vocabulary NomaData layers speak to each other.
They live in ``core`` precisely because changing them is a deliberate,
cross-cutting act. Grouped by architectural boundary.
"""

from __future__ import annotations

import os
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

# ======================================================================
# AI boundary
# ======================================================================


class Role(StrEnum):
    system = "system"
    user = "user"
    assistant = "assistant"
    tool = "tool"


class Message(BaseModel):
    role: Role
    content: str


class ChatResponse(BaseModel):
    content: str
    model: str
    usage: dict[str, int] = Field(default_factory=dict)


class ToolSpec(BaseModel):
    name: str
    description: str
    parameters: dict[str, Any] = Field(default_factory=dict)  # JSON schema


class ToolCall(BaseModel):
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class ToolCallResponse(BaseModel):
    tool_calls: list[ToolCall] = Field(default_factory=list)
    content: str | None = None


class ProviderCapabilities(BaseModel):
    chat: bool = True
    structured_output: bool = False
    tool_calling: bool = False
    streaming: bool = False
    max_context_tokens: int | None = None


class AIProviderConfig(BaseModel):
    """AI provider configuration (persisted in the app DB, editable in the UI).

    A single active configuration. The key is stored plaintext for now (dev,
    like data source passwords); set ``api_key_env`` to read it from the
    environment instead. Encryption at rest lands in Phase 6.
    """

    provider: str = "openai_compatible"
    base_url: str = "https://api.openai.com/v1"
    api_key: str = ""
    api_key_env: str | None = None
    model: str = "gpt-4o-mini"

    def resolve_api_key(self) -> str:
        if self.api_key_env:
            return os.environ.get(self.api_key_env, "")
        return self.api_key

    def to_info(self) -> AIProviderInfo:
        key = self.resolve_api_key()
        return AIProviderInfo(
            provider=self.provider,
            base_url=self.base_url,
            model=self.model,
            configured=bool(key),
            uses_api_key_env=bool(self.api_key_env),
            key_hint=_mask_key(key),
        )


def _mask_key(key: str) -> str | None:
    """First 5 + 8 dots + last 3, e.g. ``sk-or••••••••abc``. A hint for the UI,
    never the secret. Short keys are fully masked so we never reveal most of one."""
    if not key:
        return None
    if len(key) < 12:
        return "•" * 8
    return f"{key[:5]}{'•' * 8}{key[-3:]}"


class AIProviderInfo(BaseModel):
    """Safe view of the AI config — never includes the API key, only a hint."""

    provider: str
    base_url: str
    model: str
    configured: bool  # a usable key is present
    uses_api_key_env: bool
    # Masked preview of the stored key (first 5 + dots + last 3), or None.
    key_hint: str | None = None


# ======================================================================
# Data boundary
# ======================================================================


class DataSourceConfig(BaseModel):
    """A data source connection definition (persisted in the app DB)."""

    name: str
    kind: str
    host: str = "localhost"
    port: int = 3306
    database: str
    user: str = ""
    password: str = ""
    # If set, read the password from this env var instead of `password`.
    password_env: str | None = None

    def resolve_password(self) -> str:
        if self.password_env:
            return os.environ.get(self.password_env, "")
        return self.password

    def to_info(self) -> DataSourceInfo:
        return DataSourceInfo(
            name=self.name,
            kind=self.kind,
            host=self.host,
            port=self.port,
            database=self.database,
            user=self.user,
            uses_password_env=bool(self.password_env),
        )


class DataSourceInfo(BaseModel):
    """Safe view of a data source — never includes the password."""

    name: str
    kind: str
    host: str
    port: int
    database: str
    user: str
    uses_password_env: bool


class ConnectionState(StrEnum):
    ok = "ok"
    error = "error"


class ConnectionStatus(BaseModel):
    state: ConnectionState
    latency_ms: float | None = None
    message: str | None = None


class ColumnInfo(BaseModel):
    name: str
    data_type: str
    nullable: bool = True
    is_primary_key: bool = False


class ForeignKey(BaseModel):
    column: str
    references_table: str
    references_column: str


class TableInfo(BaseModel):
    schema_name: str = "public"
    name: str
    columns: list[ColumnInfo] = Field(default_factory=list)
    primary_key: list[str] = Field(default_factory=list)
    foreign_keys: list[ForeignKey] = Field(default_factory=list)


class DatabaseCatalog(BaseModel):
    source_id: str
    tables: list[TableInfo] = Field(default_factory=list)


class TableSummary(BaseModel):
    """A row in the paginated table list — no columns, so listing many tables
    stays cheap. Fetch ``TableInfo`` for one table when it is selected."""

    schema_name: str = "public"
    name: str
    column_count: int = 0
    foreign_key_count: int = 0


class TablePage(BaseModel):
    items: list[TableSummary] = Field(default_factory=list)
    # Matches for the current search — drives "more to load?" for the list.
    total: int = 0
    # Whole-catalog counts, unaffected by the search filter — for header
    # stats that shouldn't jump around while the user types.
    total_tables: int = 0
    total_columns: int = 0
    total_relationships: int = 0


class ProfileTarget(BaseModel):
    table: str
    column: str
    schema_name: str = "public"


class ColumnProfile(BaseModel):
    table: str
    column: str
    null_fraction: float | None = None
    distinct_count: int | None = None
    min_value: Any | None = None
    max_value: Any | None = None
    sample_values: list[Any] = Field(default_factory=list)


# ======================================================================
# Query boundary
# ======================================================================


class TimeGrain(StrEnum):
    day = "day"
    week = "week"
    month = "month"
    quarter = "quarter"
    year = "year"


class TimeSpec(BaseModel):
    dimension: str
    range: str | None = None  # e.g. "this_month", "2026", "last_7_days"
    grain: TimeGrain | None = None


class Filter(BaseModel):
    field: str
    operator: str  # eq, neq, gt, gte, lt, lte, in, contains
    value: Any


class AnalyticalQuery(BaseModel):
    """Intermediate query representation the agent produces — never raw SQL."""

    measures: list[str] = Field(default_factory=list)
    dimensions: list[str] = Field(default_factory=list)
    filters: list[Filter] = Field(default_factory=list)
    time: TimeSpec | None = None
    limit: int | None = None
    order_by: list[str] = Field(default_factory=list)


class QueryIntent(BaseModel):
    question: str
    kind: str = "aggregation"  # aggregation | timeseries | comparison | detail


class ExecutionPlan(BaseModel):
    """Engine-specific plan derived from an AnalyticalQuery. Opaque to the agent."""

    source_id: str
    representation: dict[str, Any] = Field(default_factory=dict)


class ResultColumn(BaseModel):
    name: str
    data_type: str


class QueryResult(BaseModel):
    columns: list[ResultColumn] = Field(default_factory=list)
    rows: list[dict[str, Any]] = Field(default_factory=list)
    row_count: int = 0
    truncated: bool = False


# ======================================================================
# Semantic boundary
# ======================================================================


class Dimension(BaseModel):
    name: str
    column: str
    description: str | None = None


class Aggregation(StrEnum):
    # `count` shadows str.count on a StrEnum — harmless; the ignore quiets mypy.
    count = "count"  # type: ignore[assignment]
    count_distinct = "count_distinct"
    sum = "sum"
    avg = "avg"
    min = "min"
    max = "max"


class MetricKind(StrEnum):
    base = "base"  # aggregation over one entity's column
    derived = "derived"  # arithmetic over other metrics


class MetricDefinition(BaseModel):
    """A named business number. Structured, not a free SQL string, so the query
    engine can execute it and the UI can edit it with pickers.

    - ``base``: an ``aggregation`` over ``column`` on ``entity``, with optional
      ``filters`` (business rules) and a ``time_dimension`` (the date column this
      metric is measured over — grain is chosen at query time, not here).
    - ``derived``: an ``expression`` combining other metrics by name, e.g.
      ``"Revenue / Order Count"``.
    """

    name: str
    description: str | None = None
    kind: MetricKind = MetricKind.base

    # base metric
    entity: str | None = None  # the entity (its table) this metric aggregates
    aggregation: Aggregation | None = None
    column: str | None = None  # column to aggregate; None for count
    filters: list[Filter] = Field(default_factory=list)
    time_dimension: str | None = None

    # derived metric
    expression: str | None = None  # references other metric names

    # display
    format: str | None = None  # "currency" | "percent" | "number"


class Relationship(BaseModel):
    from_entity: str
    to_entity: str
    from_column: str
    to_column: str
    kind: str = "many_to_one"


class Entity(BaseModel):
    name: str
    table: str
    primary_key: str = "id"
    dimensions: list[Dimension] = Field(default_factory=list)
    description: str | None = None


class SemanticGraph(BaseModel):
    """The persistent semantic artifact — the contract between AI and database."""

    source_id: str
    entities: list[Entity] = Field(default_factory=list)
    metrics: list[MetricDefinition] = Field(default_factory=list)
    relationships: list[Relationship] = Field(default_factory=list)
    version: int = 1
    published: bool = False
    # How this draft was produced: "ai" (LLM-enriched) or "heuristic" (rule-based).
    # A suggestion — never a guarantee; a human still reviews and publishes.
    provenance: str = "heuristic"


class PublishResult(BaseModel):
    source_id: str
    version: int
    published: bool


class SemanticModelVersion(BaseModel):
    version: int
    status: str  # "draft" | "published"
    created_at: str


class EntityHint(BaseModel):
    """Compact AI enrichment for one entity — business text only, matched back
    to the model by ``table``. Keeps the enrichment response small and fast."""

    table: str
    name: str = ""
    description: str = ""


class MetricHint(BaseModel):
    """Compact AI enrichment for one metric, matched back by ``key`` (a stable
    identifier the caller computes, e.g. ``sum(Payment.amount)``)."""

    key: str
    name: str = ""
    definition: str = ""


class EnrichmentHints(BaseModel):
    entities: list[EntityHint] = Field(default_factory=list)
    metrics: list[MetricHint] = Field(default_factory=list)


class SemanticModelSummary(BaseModel):
    """One row of the cross-source semantic overview — status + shape per source,
    including sources that have no model yet (so the UI can offer 'generate')."""

    source_id: str
    kind: str | None = None  # data source engine, for the UI logo
    has_model: bool = False
    status: str = "none"  # none | draft | published
    latest_version: int | None = None
    published_version: int | None = None
    provenance: str | None = None  # ai | heuristic
    entity_count: int = 0
    metric_count: int = 0
    relationship_count: int = 0


# ======================================================================
# Visualization boundary
# ======================================================================


class VisualizationType(StrEnum):
    number = "number"
    table = "table"
    line = "line"
    bar = "bar"
    pie = "pie"


class VisualizationSpec(BaseModel):
    """A visualization *specification*, not frontend code. The client renders it."""

    type: VisualizationType
    x: str | None = None
    y: str | None = None
    series: str | None = None
    title: str | None = None
