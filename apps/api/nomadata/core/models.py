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


class Measure(BaseModel):
    name: str
    expression: str  # e.g. SUM(payments.amount)
    description: str | None = None


class MetricDefinition(BaseModel):
    name: str
    definition: str  # business meaning, in human language
    formula: str  # e.g. SUM(payments.amount)
    filters: list[Filter] = Field(default_factory=list)
    time_dimension: str | None = None


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
    measures: list[Measure] = Field(default_factory=list)
    description: str | None = None


class SemanticGraph(BaseModel):
    """The persistent semantic artifact — the contract between AI and database."""

    source_id: str
    entities: list[Entity] = Field(default_factory=list)
    metrics: list[MetricDefinition] = Field(default_factory=list)
    relationships: list[Relationship] = Field(default_factory=list)
    version: int = 1
    published: bool = False


class PublishResult(BaseModel):
    source_id: str
    version: int
    published: bool


class SemanticModelVersion(BaseModel):
    version: int
    status: str  # "draft" | "published"
    created_at: str


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
