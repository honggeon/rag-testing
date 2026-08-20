"""Case 执行器：单个 golden case 的执行 → CaseResult（架构 §5/§8）。"""

from __future__ import annotations

import json
import time
from pathlib import Path

from ragtest.adapters.base import (
    AdapterError,
    ErrorKind,
    KBHandle,
    RetrievalQuery,
    Session,
)
from ragtest.evaluators import get_evaluator
from ragtest.models import CaseStatus
from ragtest.models.result import (
    AgentToolCallRecord,
    AssertionRecord,
    CaseError,
    CaseResult,
    ChunkSnapshot,
    GenerationSnapshot,
    MetricResult,
    RetrievalSnapshot,
    Span,
    TraceInfo,
)
from ragtest.models.suite import GoldenCase


async def execute_case(
    case: GoldenCase,
    *,
    adapter,                       # RetrievalPort（runner 只依赖 adapters.base 协议）
    session: Session,
    kb: KBHandle,
    doc_id_to_logical: dict[str, str],
    raw_dir: Path | None = None,
    chat_adapter=None,             # M4：ChatPort + TracePort（target=xuanjian）
    target_config: dict | None = None,
) -> CaseResult:
    """执行单个 case。expect_error / A-B 对比 / expected 三条互斥路径；
    expected_fail（defect 证据）在出口统一做语义反转（P1-3）。"""
    base = CaseResult(
        case_id=case.id,
        name=case.name,
        status=CaseStatus.ERROR,
        identity=case.identity,
        severity=case.severity,
        tags=list(case.tags),
        expected_fail=bool(case.expected_fail),
    )

    if case.expect_error:
        result = await _execute_expect_error(case, base, adapter=adapter, session=session, kb=kb)
    elif chat_adapter is not None and (case.input.question or case.input.query):
        result = await _execute_generation(
            case, base, chat_adapter=chat_adapter, identity=session.identity, kb=kb,
            target_config=target_config or {}, doc_id_to_logical=doc_id_to_logical,
            raw_dir=raw_dir,
        )
    elif case.input_b is not None:
        result = await _execute_ab(
            case, base, adapter=adapter, session=session, kb=kb,
            doc_id_to_logical=doc_id_to_logical, raw_dir=raw_dir,
        )
    else:
        result = await _execute_retrieval(
            case, base, adapter=adapter, session=session, kb=kb,
            doc_id_to_logical=doc_id_to_logical, raw_dir=raw_dir,
        )

    # ── defect 语义反转：expected_fail && 实际失败 = 证据有效（转为 PASSED）──
    if case.expected_fail:
        reason = case.expected_fail.get("reason", "")
        if result.status is CaseStatus.FAILED:
            result.status = CaseStatus.PASSED
            result.assertions.append(AssertionRecord(
                kind="expected_fail", passed=True,
                detail=f"缺陷已复现（证据有效）: {reason}",
            ))
        elif result.status is CaseStatus.PASSED:
            result.assertions.append(AssertionRecord(
                kind="expected_fail", passed=True,
                detail=f"缺陷疑似已修复（意外通过），请更新套件: {reason}",
            ))
    return result


async def _execute_ab(
    case: GoldenCase, base: CaseResult, *, adapter, session, kb,
    doc_id_to_logical: dict[str, str], raw_dir: Path | None,
) -> CaseResult:
    """A/B 对比路径（缺陷探针）：同 query 两组参数各检索一次，
    结果一致性写入 trace.server_signals.ab，由 ab_results_differ evaluator 判定。"""
    assert case.input_b is not None

    async def probe(inp) -> list[tuple[str, float]]:
        result = await adapter.retrieve(
            session, kb, RetrievalQuery(
                query=inp.query or "",
                top_k=inp.top_k,
                score_threshold=inp.score_threshold,
                extra_body=dict(inp.extra_body),
            )
        )
        return [(c.chunk_id, round(c.score, 6)) for c in result.chunks]

    try:
        chunks_a = await probe(case.input)
        chunks_b = await probe(case.input_b)
    except AdapterError as e:
        base.status = CaseStatus.ERROR
        base.error = CaseError(kind=e.kind.value, message=e.message)
        return base

    identical = chunks_a == chunks_b
    base.trace = TraceInfo(
        client_spans=[Span(name="retrieve_ab", duration_ms=0)],
        server_signals={
            "ab": {
                "identical": identical,
                "a": [cid for cid, _ in chunks_a],
                "b": [cid for cid, _ in chunks_b],
                "a_params": dict(case.input.extra_body),
                "b_params": dict(case.input_b.extra_body),
            }
        },
        unavailable=["rerank_scores"],
    )
    base.retrieval = RetrievalSnapshot(
        query=case.input.query or "",
        top_k=case.input.top_k,
        chunks=[
            ChunkSnapshot(chunk_id=cid, document_id="", rank=i + 1, score=score)
            for i, (cid, score) in enumerate(chunks_a)
        ],
    )

    metrics: list[MetricResult] = []
    for spec in case.evaluators:
        try:
            metric = get_evaluator(spec.name)(case, base, spec.params)
        except Exception as e:  # noqa: BLE001
            metric = MetricResult(name=spec.name, passed=False, category="error",
                                  detail=f"evaluator 异常: {e}")
        metrics.append(metric)
    base.metrics = metrics
    effective = [m for m in metrics if not m.skipped]
    base.status = CaseStatus.PASSED if all(m.passed for m in effective) else CaseStatus.FAILED
    return base


async def _execute_expect_error(case: GoldenCase, base: CaseResult, *, adapter, session, kb) -> CaseResult:
    """鲁棒性路径：断言被测系统以预期方式报错（如空 query → validation）。"""
    assert case.expect_error is not None
    want_kind = case.expect_error.kind
    try:
        await adapter.retrieve(
            session, kb, RetrievalQuery(
                query=case.input.query or "",
                top_k=case.input.top_k,
                score_threshold=case.input.score_threshold,
            )
        )
    except AdapterError as e:
        passed = e.kind.value == want_kind
        base.status = CaseStatus.PASSED if passed else CaseStatus.FAILED
        base.assertions.append(AssertionRecord(
            kind="expect_error", passed=passed,
            detail=f"期望错误 kind={want_kind}，实际 kind={e.kind.value}（{e.message[:80]}）",
        ))
        return base
    base.status = CaseStatus.FAILED
    base.assertions.append(AssertionRecord(
        kind="expect_error", passed=False,
        detail=f"期望错误 kind={want_kind}，但请求成功返回（未报错）",
    ))
    return base


async def _execute_retrieval(
    case: GoldenCase, base: CaseResult, *, adapter, session, kb,
    doc_id_to_logical: dict[str, str], raw_dir: Path | None,
) -> CaseResult:
    """正常检索路径：retrieve → 快照 → evaluators。"""
    started = time.perf_counter()
    try:
        result = await adapter.retrieve(
            session, kb, RetrievalQuery(
                query=case.input.query or "",
                top_k=case.input.top_k,
                score_threshold=case.input.score_threshold,
            )
        )
    except AdapterError as e:
        if e.kind is ErrorKind.CAPABILITY:
            base.status = CaseStatus.SKIPPED
        else:
            base.status = CaseStatus.ERROR
        base.error = CaseError(kind=e.kind.value, message=e.message)
        return base
    elapsed = int((time.perf_counter() - started) * 1000)

    # 原始响应落盘（raw/ 目录，run.json 只留路径）
    raw_path: str | None = None
    if raw_dir is not None:
        raw_dir.mkdir(parents=True, exist_ok=True)
        raw_file = raw_dir / f"{case.id}.search.json"
        raw_file.write_text(json.dumps(result.raw, ensure_ascii=False, indent=2), encoding="utf-8")
        raw_path = f"raw/{raw_file.name}"

    base.retrieval = RetrievalSnapshot(
        query=case.input.query or "",
        top_k=case.input.top_k,
        score_threshold=case.input.score_threshold,
        chunks=[
            ChunkSnapshot(
                chunk_id=c.chunk_id,
                document_id=c.document_id,
                logical_doc=doc_id_to_logical.get(c.document_id),
                rank=c.rank,
                score=c.score,
                content_preview=(c.content or "")[:120],
                breadcrumb=c.metadata.get("breadcrumb"),
            )
            for c in result.chunks
        ],
        degraded=result.degraded,
        degraded_reason=result.degraded_reason,
        latency_ms=result.latency_ms or elapsed,
        raw_response_path=raw_path,
    )
    base.trace = TraceInfo(
        client_spans=[Span(name="retrieve", duration_ms=elapsed)],
        server_signals={"degraded": result.degraded, "request_id": None},
        unavailable=["rerank_scores", "prompt", "token_usage"],
    )

    # evaluators
    metrics: list[MetricResult] = []
    for spec in case.evaluators:
        try:
            metric = get_evaluator(spec.name)(case, base, spec.params)
        except Exception as e:  # noqa: BLE001 —— evaluator 异常不拖垮 run
            metric = MetricResult(
                name=spec.name, passed=False, category="error",
                detail=f"evaluator 异常: {e}",
            )
        metrics.append(metric)
    base.metrics = metrics

    effective = [m for m in metrics if not m.skipped]
    base.status = CaseStatus.PASSED if all(m.passed for m in effective) else CaseStatus.FAILED
    return base


# ── 生成路径（M4，target=xuanjian）──────────────────────────────────────────


def compute_attribution(case: GoldenCase, result: CaseResult) -> str:
    """E2E 归因（架构 §6.3）：routing_failure / retrieval_miss / generation_failure / ok。"""
    calls = [t for t in result.trace.agent_tool_calls if t.tool == "knowledge_retrieve"]
    if not calls:
        return "routing_failure"
    total_chunks = sum(t.chunk_count or 0 for t in calls)
    expected = set(case.expected.documents) if case.expected else set()
    hit_docs = set().union(*(set(t.hit_doc_ids) for t in calls)) if calls else set()
    # hit_doc_ids 已由 executor 映射为 logical_id
    if total_chunks == 0 or (expected and not (expected & hit_docs)):
        return "retrieval_miss"
    gen_failed = any(
        m.category == "generation" and not m.passed and not m.skipped
        for m in result.metrics
    )
    return "generation_failure" if gen_failed else "ok"


async def _execute_generation(
    case: GoldenCase, base: CaseResult, *, chat_adapter, identity, kb,
    target_config: dict, doc_id_to_logical: dict[str, str], raw_dir: Path | None,
) -> CaseResult:
    """E2E 问答路径：create_session → chat → collect_agent_trace → evaluators → 归因。
    会话在 finally 中删除（每 case 独立会话，防历史污染）。"""
    from ragtest.adapters.base import ChatRequest

    chat_session = None
    started = time.perf_counter()
    try:
        chat_session = await chat_adapter.create_session(identity)
        request = ChatRequest(
            question=case.input.question or case.input.query or "",
            skill=target_config.get("skill", "xj-kbase"),
            kb_id_inject=kb.kb_id if target_config.get("kb_id_inject", True) else None,
            model=target_config.get("model"),
            timeout_s=case.timeout_s or target_config.get("timeout_s", 300),
        )
        chat_result = await chat_adapter.chat(chat_session, request)

        base.generation = GenerationSnapshot(
            answer=chat_result.answer,
            usage=chat_result.usage,
            latency_ms=chat_result.latency_ms,
        )

        # TracePort：归因证据（session.messages 主路径）
        tool_calls = await chat_adapter.collect_agent_trace(chat_session, chat_result)
        # document_id → logical_id 映射（归因用逻辑 id 与 expected 对比）
        records = [
            AgentToolCallRecord(
                tool=t.tool,
                args=t.args,
                chunk_count=t.chunk_count,
                hit_doc_ids=[doc_id_to_logical.get(d, d) for d in t.hit_doc_ids],
                duration_ms=t.duration_ms,
                is_error=t.is_error,
                source=t.source,
            )
            for t in tool_calls
        ]
        base.trace = TraceInfo(
            client_spans=[Span(name="chat", duration_ms=int((time.perf_counter() - started) * 1000))],
            server_signals={"usage": chat_result.usage or {}},
            agent_tool_calls=records,
            unavailable=["prompt", "rerank_scores"],
        )

        if raw_dir is not None:
            raw_dir.mkdir(parents=True, exist_ok=True)
            raw_file = raw_dir / f"{case.id}.chat.json"
            raw_file.write_text(json.dumps(chat_result.raw, ensure_ascii=False, indent=2),
                                encoding="utf-8")

        metrics: list[MetricResult] = []
        for spec in case.evaluators:
            try:
                metric = get_evaluator(spec.name)(case, base, spec.params)
            except Exception as e:  # noqa: BLE001
                metric = MetricResult(name=spec.name, passed=False, category="error",
                                      detail=f"evaluator 异常: {e}")
            metrics.append(metric)
        base.metrics = metrics

        # 归因（在 metrics 之后，需要 generation 指标结果）
        base.trace.attribution = compute_attribution(case, base)
        # 回填 retrieval_attribution 指标详情（该指标在归因计算前执行）
        for m in base.metrics:
            if m.name == "retrieval_attribution":
                m.detail = f"归因: {base.trace.attribution}"

        effective = [m for m in metrics if not m.skipped]
        base.status = CaseStatus.PASSED if all(m.passed for m in effective) else CaseStatus.FAILED
        return base
    except AdapterError as e:
        base.status = CaseStatus.ERROR
        base.error = CaseError(kind=e.kind.value, message=e.message)
        return base
    finally:
        if chat_session is not None:
            await chat_adapter.delete_session(chat_session)
