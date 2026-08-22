"""Shared data contracts between layers.

These Pydantic models are the vocabulary NomaData layers speak to each other.
They live in ``core`` precisely because changing them is a deliberate,
cross-cutting act. Grouped by architectural boundary.
"""

from __future__ import annotations

import os
from datetime import date, datetime
from enum import StrEnum
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, Field, field_validator, model_validator

# ======================================================================
# AI boundary
# ======================================================================


class Role(StrEnum):
    system = "system"
    user = "user"
    assistant = "assistant"
    tool = "tool"


class ToolCall(BaseModel):
    """A tool the model asked to run.

    ``id`` is assigned by the provider and must travel back with the result, or
    the model cannot tell which call is being answered. Empty means the provider
    does not use call ids.
    """

    id: str = ""
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class Message(BaseModel):
    """One turn in a conversation with the model.

    An agent loop needs more than ``{role, content}``: the assistant's turn has
    to carry the tool calls it asked for, and the reply has to say which call it
    answers. Without both, a tool result cannot be handed back and the loop
    stops after one call.

    These fields describe the *conversation*, not any provider's wire format —
    turning them into a request body is the provider's job.
    """

    role: Role
    content: str = ""
    #: Tool calls the assistant asked for (``role=assistant``).
    tool_calls: list[ToolCall] = Field(default_factory=list)
    #: Which call this message answers (``role=tool``).
    tool_call_id: str | None = None
    #: Name of the tool that produced this content (``role=tool``).
    name: str | None = None


class ChatResponse(BaseModel):
    content: str
    model: str
    # Whatever the provider reports, verbatim. Not dict[str, int]: gateways
    # add fractional costs and nested breakdowns, and a usage figure is
    # never worth failing a good answer over.
    usage: dict[str, Any] = Field(default_factory=dict)


class ToolSpec(BaseModel):
    name: str
    description: str
    parameters: dict[str, Any] = Field(default_factory=dict)  # JSON schema


class ToolCallResponse(BaseModel):
    tool_calls: list[ToolCall] = Field(default_factory=list)
    content: str | None = None
    # An agent turn costs several of these. Reporting usage here — as `chat`
    # already does — is what makes the price of a question measurable at all.
    model: str = ""
    # Whatever the provider reports, verbatim. Not dict[str, int]: gateways
    # add fractional costs and nested breakdowns, and a usage figure is
    # never worth failing a good answer over.
    usage: dict[str, Any] = Field(default_factory=dict)


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
    # Low-cardinality relative to row count → a good dimension / enum candidate.
    is_categorical: bool | None = None


# ======================================================================
# Query boundary
# ======================================================================


class TimeGrain(StrEnum):
    day = "day"
    week = "week"
    month = "month"
    quarter = "quarter"
    year = "year"


#: Relative periods the whole stack agrees on. A caller — especially a model
#: writing a query — will otherwise invent phrasings ("thang_nay", "last_3_months")
#: that only fail deep inside the query engine, where the message means nothing
#: to whoever asked. Anything outside this set is rejected at the edge.
RELATIVE_RANGES = frozenset(
    {
        "today",
        "yesterday",
        "this_week",
        "last_week",
        "this_month",
        "last_month",
        "this_quarter",
        "last_quarter",
        "this_year",
        "last_year",
        "last_7_days",
        "last_30_days",
        "last_90_days",
        "last_12_months",
    }
)


def validate_timezone(value: str) -> str:
    """An IANA zone name, or an error naming it.

    A relative period only means something in a timezone: "this month" asked at
    05:00 in Vietnam is still July in UTC, so the first seven hours of every day
    land in the wrong month. Letting a mistyped zone fall back to UTC would
    reintroduce exactly that, silently.
    """
    name = value.strip()
    if not name:
        raise ValueError("A timezone is required; use 'UTC' if the data is UTC.")
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError(
            f"Unknown timezone {value!r} — use an IANA name like 'Asia/Ho_Chi_Minh'."
        ) from exc
    return name


class TimeSpec(BaseModel):
    """Which date column a query is measured over, and across what period.

    Two ways to say the period: a keyword from ``RELATIVE_RANGES``, or an exact
    ``since``/``until`` window. Both may be absent — that means the whole
    history, which is a legitimate thing to ask for.
    """

    dimension: str
    #: A keyword from RELATIVE_RANGES.
    range: str | None = None
    #: An exact window, inclusive at both ends. Wins over `range` if both given.
    since: date | None = None
    until: date | None = None
    grain: TimeGrain | None = None
    #: The zone a relative period is measured in. Filled from the source's
    #: business context when the caller does not say — see `validate_timezone`.
    timezone: str | None = None

    @field_validator("timezone")
    @classmethod
    def _known_timezone(cls, value: str | None) -> str | None:
        return None if value is None else validate_timezone(value)

    @field_validator("range")
    @classmethod
    def _known_range(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalised = value.strip().lower().replace(" ", "_").replace("-", "_")
        if normalised not in RELATIVE_RANGES:
            raise ValueError(
                f"Unknown time range {value!r}; expected one of "
                f"{', '.join(sorted(RELATIVE_RANGES))}, or an exact since/until window."
            )
        return normalised

    @model_validator(mode="after")
    def _window_makes_sense(self) -> TimeSpec:
        if (self.since is None) != (self.until is None):
            raise ValueError("An exact window needs both `since` and `until`.")
        if self.since and self.until and self.since > self.until:
            raise ValueError(f"`since` ({self.since}) is after `until` ({self.until}).")
        return self

    @property
    def is_absolute(self) -> bool:
        return self.since is not None and self.until is not None


#: Every operator the whole stack knows how to execute. An operator outside this
#: set must be rejected at the edge — silently falling back to ``eq`` produces
#: wrong numbers with no error, the worst failure mode an analytics tool has.
FILTER_OPERATORS = frozenset(
    {"eq", "neq", "gt", "gte", "lt", "lte", "in", "not_in", "contains", "set", "not_set"}
)

#: Operators that carry no value (``... IS NULL``).
VALUELESS_OPERATORS = frozenset({"set", "not_set"})


class Filter(BaseModel):
    field: str
    operator: str  # see FILTER_OPERATORS
    value: Any = None

    @field_validator("operator")
    @classmethod
    def _known_operator(cls, value: str) -> str:
        if value not in FILTER_OPERATORS:
            raise ValueError(
                f"Unknown filter operator {value!r}; expected one of "
                f"{', '.join(sorted(FILTER_OPERATORS))}"
            )
        return value


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


class ChatRequest(BaseModel):
    """A natural-language question against one source's published model."""

    question: str
    #: Continue an existing thread. Omit to start one — the id comes back on the
    #: turn, so a caller never has to create a conversation before asking.
    conversation_id: str | None = None


class QueryPlan(BaseModel):
    """The agent's decision for one question — the model's structured output.

    ``kind`` gates the rest: only ``query`` carries a runnable ``AnalyticalQuery``
    (business names); ``clarify`` asks the user back; ``refuse`` declines a
    non-data question. Forcing this choice is what lets the model say "I don't
    know" instead of inventing a query that runs and answers wrongly.
    """

    kind: str = "query"  # "query" | "clarify" | "refuse"
    query: AnalyticalQuery | None = None
    clarification: str = ""
    reason: str = ""

    @model_validator(mode="after")
    def _payload_matches_kind(self) -> QueryPlan:
        """Every kind has to carry the thing that kind is for.

        A model that replies ``{"kind": "query"}`` and nothing else has decided
        nothing. Accepting it turned a broken reply into a fabricated "could you
        rephrase that?" — the user was asked to fix a question that was fine,
        and the eval scored it as the agent sensibly clarifying. Rejecting it
        here instead makes the provider retry with the error, and lets a real
        failure be reported as one.
        """
        if self.kind not in ("query", "clarify", "refuse"):
            raise ValueError('kind must be one of "query", "clarify", "refuse"')
        if self.kind == "query" and self.query is None:
            raise ValueError('kind is "query" but no `query` was given')
        if self.kind == "query" and not self.query.measures:  # type: ignore[union-attr]
            raise ValueError("a query has to name at least one metric in `measures`")
        if self.kind == "clarify" and not self.clarification.strip():
            raise ValueError('kind is "clarify" but no `clarification` question was given')
        if self.kind == "refuse" and not self.reason.strip():
            raise ValueError('kind is "refuse" but no `reason` was given')
        return self


class AgentTurn(BaseModel):
    """One answered (or declined) question — the /chat response and UI turn."""

    # "answer" runs a query; "clarify" and "refuse" are the model ending its
    # turn deliberately; "reply" is ordinary conversation — a greeting, "what
    # can you do" — which is neither, and used to be mislabelled as one;
    # "error" is the system failing, not the assistant speaking.
    kind: str  # "answer" | "clarify" | "refuse" | "reply" | "error"
    question: str
    #: The business-name query behind the answer (for the "view query" panel).
    query: AnalyticalQuery | None = None
    result: QueryResult | None = None
    #: Short deterministic headline (e.g. the single value or "N rows").
    answer: str = ""
    #: The "read from" trust line, built without the LLM.
    explanation: str = ""
    #: Business-language remarks (e.g. measured by a non-default date).
    notes: list[str] = Field(default_factory=list)
    clarification: str = ""
    reason: str = ""
    #: The thread this turn belongs to, and its place in it.
    conversation_id: str = ""
    ordinal: int = 0
    #: Which published model version answered — an answer from v3 cannot be
    #: reproduced once v4 is live, and the reader has to be told that.
    model_version: int | None = None
    #: What the turn cost: tokens in/out, wall-clock, how many tool calls. An
    #: agent turn costs several LLM calls; unmeasured, nobody knows the price of
    #: a question until the bill arrives.
    usage: TurnUsage = Field(default_factory=lambda: TurnUsage())
    #: What the agent did on the way to this answer, in order.
    steps: list[AgentStep] = Field(default_factory=list)


class AgentStep(BaseModel):
    """One thing the agent did, in the order it did it.

    The visible half of a turn that otherwise takes ten seconds behind a
    spinner. It is also the honest account of how an answer was reached: which
    metric was looked up, which query ran, what a tool rejected.
    """

    #: Position in the turn. A step is sent when it starts and again when it
    #: finishes carrying what came back, so the reader watches it happen and
    #: can still open it afterwards; the id is what joins the two.
    ordinal: int = 0
    kind: str  # "plan" | "tool" | "result" | "repair"
    label: str
    detail: str = ""


class TurnUsage(BaseModel):
    tokens_in: int = 0
    tokens_out: int = 0
    latency_ms: int = 0
    llm_calls: int = 0
    tool_calls: int = 0


class ConversationTurn(BaseModel):
    """A stored turn, read back for history and for the conversation view."""

    ordinal: int
    kind: str
    question: str
    query: AnalyticalQuery | None = None
    result: QueryResult | None = None
    answer: str = ""
    explanation: str = ""
    notes: list[str] = Field(default_factory=list)
    model_version: int | None = None
    usage: TurnUsage = Field(default_factory=lambda: TurnUsage())
    steps: list[AgentStep] = Field(default_factory=list)
    error: str = ""
    created_at: datetime | None = None


class Conversation(BaseModel):
    id: str
    source_id: str
    title: str = ""
    turns: list[ConversationTurn] = Field(default_factory=list)
    turn_count: int = 0
    created_at: datetime | None = None
    updated_at: datetime | None = None


# ======================================================================
# Semantic boundary
# ======================================================================


class Origin(StrEnum):
    heuristic = "heuristic"  # produced by rules over the schema
    ai = "ai"  # produced or improved by the model
    user = "user"  # typed by a human — AI must not overwrite


class Provenance(BaseModel):
    """Where an object's business text came from, and whether AI may touch it.

    This replaces guessing "has the user edited this?" from the string itself
    (a name ending in " Count" used to mean "still a default"), which silently
    overwrote hand-written values. Enrichment now only rewrites objects whose
    ``origin`` is not ``user`` and that are not ``locked``.
    """

    origin: Origin = Origin.heuristic
    locked: bool = False  # user pinned it: never rewrite, whatever the origin


class DimensionKind(StrEnum):
    time = "time"
    number = "number"
    string = "string"
    boolean = "boolean"


class Dimension(BaseModel):
    """An attribute to slice by. ``kind`` is read from the catalog rather than
    guessed from the column name — the query layer needs the real type."""

    name: str
    column: str
    kind: DimensionKind = DimensionKind.string
    data_type: str = ""  # native DB type, for display
    hidden: bool = False  # kept in the model but not published to the query layer
    description: str | None = None
    # Profiling hints — power the filter value picker and give the AI real
    # examples to name the column from. Only kept for low-cardinality columns.
    distinct_count: int | None = None
    sample_values: list[Any] = Field(default_factory=list)
    provenance: Provenance = Field(default_factory=Provenance)


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

    - ``base``: an ``aggregation`` over ``column`` on the entity identified by
      ``entity_key``, with optional ``filters`` (business rules) and a
      ``time_dimension`` (the date column this metric is measured over — grain
      is chosen at query time, not here).
    - ``derived``: an ``expression`` combining other metrics by name, e.g.
      ``"Revenue / Order Count"``.

    ``id`` is the stable handle. ``name`` is a label a human retitles freely, so
    nothing may reference a metric by name except a derived expression (which
    the validator checks).
    """

    id: str = Field(default_factory=lambda: uuid4().hex)
    name: str
    description: str | None = None
    kind: MetricKind = MetricKind.base

    # base metric
    entity_key: str | None = None  # Entity.key — NOT the display name
    aggregation: Aggregation | None = None
    column: str | None = None  # column to aggregate; None for count
    filters: list[Filter] = Field(default_factory=list)
    time_dimension: str | None = None

    # derived metric
    expression: str | None = None  # references other metric names

    # display
    format: str | None = None  # "currency" | "percent" | "number"

    provenance: Provenance = Field(default_factory=Provenance)


class Relationship(BaseModel):
    """A join between two entities, addressed by stable key."""

    from_entity_key: str
    to_entity_key: str
    from_column: str
    to_column: str
    kind: str = "many_to_one"


class Entity(BaseModel):
    """A business object backed by one table.

    ``key`` is the immutable identity every other part of the graph points at;
    ``name`` is the human label and may be rewritten at will. Keeping these
    separate is what stops an AI rename from orphaning metrics and joins.
    """

    key: str
    name: str
    table: str
    schema_name: str = "public"
    primary_key: str = "id"
    dimensions: list[Dimension] = Field(default_factory=list)
    description: str | None = None
    hidden: bool = False
    provenance: Provenance = Field(default_factory=Provenance)


def _humanize_identifier(identifier: str) -> str:
    """``order_items`` → ``Order Items``. Duplicated (small) from the suggester
    so ``core`` stays dependency-free; used only to repair legacy graphs."""
    spaced = identifier.replace("_", " ").replace("-", " ").strip()
    if not spaced:
        return identifier
    return " ".join(word.capitalize() for word in spaced.split())


class SemanticGraph(BaseModel):
    """The persistent semantic artifact — the contract between AI and database."""

    source_id: str
    entities: list[Entity] = Field(default_factory=list)
    metrics: list[MetricDefinition] = Field(default_factory=list)
    relationships: list[Relationship] = Field(default_factory=list)
    version: int = 1
    published: bool = False
    # Bumped on every draft save. The client sends back what it loaded, so two
    # tabs editing the same model collide loudly instead of overwriting silently.
    revision: int = 0
    # How this draft was produced: "ai" (LLM-enriched) or "heuristic" (rule-based).
    # A suggestion — never a guarantee; a human still reviews and publishes.
    provenance: str = "heuristic"
    # Tables left out of the model and why (e.g. no primary key). A dropped
    # table must be visible to the user, never a silent disappearance.
    skipped_tables: list[dict[str, str]] = Field(default_factory=list)
    # Tables the model was built from. Empty = the whole catalog.
    scope_tables: list[str] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _upgrade_legacy(cls, data: Any) -> Any:
        """Migrate graphs stored before entities had a stable ``key``.

        Old rows keyed metrics by the entity's *display name* and relationships
        by its *table name*. Both are repaired here on load, so a model built by
        an earlier version keeps working instead of silently losing every
        measure. References that cannot be resolved are dropped rather than
        guessed — the validator then reports them.
        """
        if not isinstance(data, dict):
            return data
        entities = data.get("entities")
        if not isinstance(entities, list):
            return data

        by_name: dict[str, str] = {}
        by_table: dict[str, str] = {}
        upgraded = False
        for entity in entities:
            if not isinstance(entity, dict):
                continue
            table = str(entity.get("table") or "")
            if not entity.get("key"):
                schema = str(entity.get("schema_name") or "public")
                entity["key"] = f"{schema}.{table}" if table else schema
                upgraded = True
            key = str(entity["key"])
            if entity.get("name"):
                by_name.setdefault(str(entity["name"]), key)
            if table:
                by_table.setdefault(table, key)
                # Metrics written before an AI rename still hold the original
                # humanized table name — recover those too.
                by_name.setdefault(_humanize_identifier(table), key)
        if not upgraded:
            return data

        for metric in data.get("metrics") or []:
            if not isinstance(metric, dict) or metric.get("entity_key"):
                continue
            legacy = metric.pop("entity", None)
            if legacy:
                resolved = by_name.get(str(legacy)) or by_table.get(str(legacy))
                if resolved:
                    metric["entity_key"] = resolved

        relationships = []
        for rel in data.get("relationships") or []:
            if not isinstance(rel, dict):
                continue
            if not rel.get("from_entity_key"):
                legacy_from = rel.pop("from_entity", None)
                legacy_to = rel.pop("to_entity", None)
                source = by_table.get(str(legacy_from)) or by_name.get(str(legacy_from))
                target = by_table.get(str(legacy_to)) or by_name.get(str(legacy_to))
                if not source or not target:
                    continue  # unresolvable join — drop rather than invent one
                rel["from_entity_key"] = source
                rel["to_entity_key"] = target
            relationships.append(rel)
        data["relationships"] = relationships
        return data


class BusinessContext(BaseModel):
    """What the AI needs to know about *this* business before it can name
    anything usefully. Written once per data source, injected into every
    semantic prompt. Free text from the user — never trusted to relax the hard
    rules in the system prompt, only to inform naming."""

    source_id: str = ""
    domain: str = ""  # "SaaS quản lý trường học, khách hàng là trường tư"
    glossary: str = ""  # "dot = đợt thu; hs = học sinh"
    conventions: str = ""  # "bảng scp_* là nghiệp vụ chính; *_tmp bỏ qua"
    # Language for generated names and descriptions. The application chrome is
    # always English; this only controls the content the model writes.
    language: str = "en"
    instructions: str = ""  # free-form preferences
    #: The zone this database's timestamps are read in. It decides what "this
    #: month" means: in UTC+7 the first seven hours of every day fall in the
    #: previous UTC day, and at a month boundary in the previous month.
    timezone: str = "UTC"

    @field_validator("timezone")
    @classmethod
    def _known_timezone(cls, value: str) -> str:
        return validate_timezone(value)

    def is_empty(self) -> bool:
        return not any(
            (
                self.domain.strip(),
                self.glossary.strip(),
                self.conventions.strip(),
                self.instructions.strip(),
            )
        )


class IssueLevel(StrEnum):
    error = "error"  # blocks publish
    warning = "warning"  # shown, does not block


class ValidationIssue(BaseModel):
    level: IssueLevel
    code: str
    message: str
    target: str | None = None  # Entity.key or MetricDefinition.id
    target_kind: str | None = None  # "entity" | "metric" | "relationship"


class ValidationReport(BaseModel):
    """Why a model can or cannot be published. Publishing a graph that Cube
    cannot execute used to succeed silently; this is the gate."""

    ok: bool = True
    issues: list[ValidationIssue] = Field(default_factory=list)

    @property
    def errors(self) -> list[ValidationIssue]:
        return [i for i in self.issues if i.level == IssueLevel.error]

    @property
    def warnings(self) -> list[ValidationIssue]:
        return [i for i in self.issues if i.level == IssueLevel.warning]


class MetricDraftRequest(BaseModel):
    """Describe one metric in words; get a filled-in definition back.

    ``base`` set = edit that metric ("tính theo ngày tạo thay vì ngày thu");
    ``base`` null = create a new one. Nothing is saved — the client fills its
    form with the result and the user still presses Save."""

    prompt: str
    base: MetricDefinition | None = None
    entity_key: str | None = None  # None: let the model pick the entity


class MetricDraftResponse(BaseModel):
    metric: MetricDefinition
    changed_fields: list[str] = Field(default_factory=list)  # for highlighting
    reasoning: str = ""
    warnings: list[str] = Field(default_factory=list)


class ProposedFilter(BaseModel):
    field: str = ""
    operator: str = "eq"
    value: Any = None


class MetricProposal(BaseModel):
    """The raw shape asked of the model — deliberately all-strings and separate
    from ``MetricDefinition`` so a sloppy reply fails one field instead of the
    whole call, and so the model can never set ``id`` or ``provenance``."""

    name: str = ""
    entity_key: str = ""
    kind: str = "base"
    aggregation: str = ""
    column: str = ""
    filters: list[ProposedFilter] = Field(default_factory=list)
    time_dimension: str = ""
    expression: str = ""
    format: str = ""
    description: str = ""
    reasoning: str = ""


class EntityDraftRequest(BaseModel):
    """Describe one entity in words and get its business text back.

    The same "ask right where you are editing" move as ``MetricDraftRequest``,
    which replaced a single global re-enrichment pass: the user is looking at
    one entity, so the request is about that one entity.
    """

    prompt: str
    entity_key: str


class EntityProposal(BaseModel):
    """What the model is asked for — text only. Structure (table, columns, keys)
    is never up for negotiation, so it is not in this shape at all."""

    name: str = ""
    description: str = ""
    reasoning: str = ""


class EntityDraftResponse(BaseModel):
    name: str
    description: str
    changed_fields: list[str] = Field(default_factory=list)
    reasoning: str = ""
    warnings: list[str] = Field(default_factory=list)


class MetricSuggestRequest(BaseModel):
    """Ask for the metrics worth having on one entity.

    Scoped to one entity on purpose: a 122-table model has a handful of tables
    anyone measures and a long tail of lookup tables nobody does, and proposing
    for all of them buries the useful ones.
    """

    entity_key: str
    limit: int = Field(default=5, ge=1, le=10)


class MetricProposalList(BaseModel):
    """What the model returns for a suggestion request."""

    metrics: list[MetricProposal] = Field(default_factory=list)


class MetricSuggestResponse(BaseModel):
    """Proposals, already checked against the catalog. Nothing is saved — the
    user picks which ones to keep."""

    metrics: list[MetricDefinition] = Field(default_factory=list)
    #: Per proposal, in the same order: why the model suggested it.
    reasons: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class MetricPreview(BaseModel):
    """Result of running one metric against the real database ("Chạy thử").

    The number is what tells a business user whether the definition is right —
    they know their own figures. ``sql`` makes the answer traceable."""

    metric_id: str
    value: Any | None = None
    row_count: int | None = None
    #: The span of the metric's time column across the matched rows. Without it
    #: a number is undated, and the commonest modelling mistake — measuring by
    #: the record-created date instead of the event date — stays invisible.
    period_start: Any | None = None
    period_end: Any | None = None
    time_column: str | None = None
    sql: str = ""
    error: str | None = None


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
    by ``key``. Keeps the enrichment response small and fast."""

    key: str
    name: str = ""
    description: str = ""


class MetricHint(BaseModel):
    """Compact AI enrichment for one metric, matched back by its stable ``id``."""

    id: str
    name: str = ""
    definition: str = ""


class EnrichmentHints(BaseModel):
    entities: list[EntityHint] = Field(default_factory=list)
    metrics: list[MetricHint] = Field(default_factory=list)


class JobStatus(StrEnum):
    running = "running"
    done = "done"
    error = "error"


class GenerationJob(BaseModel):
    """A background build of a semantic model — the client polls it. When it
    reaches ``done`` the draft is already saved; the client reloads it."""

    id: str
    source_id: str
    kind: str = "generate"
    status: JobStatus = JobStatus.running
    done: int = 0  # batches finished
    total: int = 0  # total batches (0 = no AI step)
    error: str | None = None
    # Naming batches that failed. The build still succeeds — those entities keep
    # their heuristic names — but "partly named" must not read as "named".
    failed_batches: int = 0
    last_batch_error: str | None = None
    # Columns whose values were sampled, and columns the time budget did not
    # reach. A model built without them still works; it just knows less about
    # what its own columns contain, and the reader has to be told which.
    profiled_columns: int = 0
    unprofiled_columns: int = 0


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
    # Structural health, from the same validator the Publish button uses (no
    # database hit): errors block a publish, warnings are advisory.
    error_count: int = 0
    warning_count: int = 0
    # A draft exists that is newer than the published model — unpublished work.
    has_unpublished_changes: bool = False


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
