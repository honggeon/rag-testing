"""TestResult Schema（架构 v0.3 §8，Run Artifact v1）。"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from ragtest.models import CaseStatus

RESULT_SCHEMA_VERSION = "1"


# ── 指标与断言 ─────────────────────────────────────────────────────────────


class MetricResult(BaseModel):
    name: str
    value: float | None = None
    threshold: float | None = None
    passed: bool = True
    skipped: bool = False                  # 空 expected 等场景，不计入 pass/fail
    category: str = ""
    detail: str = ""


class AssertionRecord(BaseModel):
    kind: str
    passed: bool
    detail: str = ""


# ── 检索 / 生成快照 ─────────────────────────────────────────────────────────


class ChunkSnapshot(BaseModel):
    chunk_id: str
    document_id: str
    logical_doc: str | None = None         # 映射回数据集 logical_id
    rank: int
    score: float
    content_preview: str = ""
    breadcrumb: str | None = None


class RetrievalSnapshot(BaseModel):
    query: str
    top_k: int
    score_threshold: float = 0.0
    chunks: list[ChunkSnapshot] = Field(default_factory=list)
    degraded: bool = False
    degraded_reason: str | None = None
    latency_ms: int = 0
    raw_response_path: str | None = None


class GenerationSnapshot(BaseModel):       # M4 使用
    answer: str
    usage: dict[str, Any] | None = None
    latency_ms: int = 0
    ttft_ms: int | None = None
    context: str = ""          # 检索上下文（faithfulness judge 用）


# ── Trace（诚实分层）────────────────────────────────────────────────────────


class Span(BaseModel):
    name: str
    duration_ms: int


class AgentToolCallRecord(BaseModel):      # M4 使用
    tool: str
    args: dict[str, Any] = Field(default_factory=dict)
    chunk_count: int | None = None
    hit_doc_ids: list[str] = Field(default_factory=list)
    duration_ms: int | None = None
    is_error: bool = False
    source: str = "chat_response"


class TraceInfo(BaseModel):
    client_spans: list[Span] = Field(default_factory=list)
    server_signals: dict[str, Any] = Field(default_factory=dict)
    agent_tool_calls: list[AgentToolCallRecord] = Field(default_factory=list)
    attribution: str | None = None
    unavailable: list[str] = Field(default_factory=list)


# ── Case / 文档 / 运行 ──────────────────────────────────────────────────────


class CaseError(BaseModel):
    kind: str
    message: str


class CaseResult(BaseModel):
    case_id: str
    name: str = ""
    status: CaseStatus
    identity: str = ""
    severity: str = "major"
    tags: list[str] = Field(default_factory=list)
    expected_fail: bool = False
    retrieval: RetrievalSnapshot | None = None
    generation: GenerationSnapshot | None = None
    metrics: list[MetricResult] = Field(default_factory=list)
    assertions: list[AssertionRecord] = Field(default_factory=list)
    trace: TraceInfo = Field(default_factory=TraceInfo)
    error: CaseError | None = None


class DocumentRunInfo(BaseModel):
    logical_id: str
    doc_id: str
    filename: str = ""
    indexing_latency_ms: int = 0
    final_status: str = "unknown"
    chunk_count: int | None = None


class LifecycleEntry(BaseModel):
    state: str
    at: str
    ok: bool = True
    detail: str = ""


class RunSummary(BaseModel):
    total: int = 0
    passed: int = 0
    failed: int = 0
    error: int = 0
    skipped: int = 0
    pass_rate: float = 0.0
    metrics_avg: dict[str, float] = Field(default_factory=dict)
    latency: dict[str, float] = Field(default_factory=dict)


class TestRun(BaseModel):
    schema_version: str = RESULT_SCHEMA_VERSION
    run_id: str
    suite: dict[str, Any] = Field(default_factory=dict)
    environment: dict[str, Any] = Field(default_factory=dict)
    lifecycle: list[LifecycleEntry] = Field(default_factory=list)
    kb: dict[str, Any] = Field(default_factory=dict)
    cases: list[CaseResult] = Field(default_factory=list)
    summary: RunSummary = Field(default_factory=RunSummary)
    baseline_diff: dict[str, Any] | None = None
    gate: dict[str, Any] | None = None
