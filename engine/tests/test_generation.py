"""M4：生成 evaluator + 归因四分支 + agent fixture 解析测试。"""

import json
from pathlib import Path

import pytest

from ragtest.adapters.base import ChatResult, ChatSession, Identity
from ragtest.adapters.xuanjian import XuanjianAgentAdapter
from ragtest.evaluators import get_evaluator
from ragtest.models import CaseStatus
from ragtest.models.result import (
    AgentToolCallRecord,
    CaseResult,
    GenerationSnapshot,
    MetricResult,
    TraceInfo,
)
from ragtest.models.suite import EvaluatorSpec, ExpectedSpec, GoldenCase, InputSpec
from ragtest.runner.executor import compute_attribution

FIXTURE = Path(__file__).parent / "fixtures" / "agent_chat_response.json"


def make_gen_case(facts=None, forbidden=None, expected_docs=None) -> GoldenCase:
    return GoldenCase(
        id="g1",
        input=InputSpec(question="q"),
        expected=ExpectedSpec(
            documents=expected_docs or [],
            golden_facts=facts or [],
            forbidden_facts=forbidden or [],
        ),
    )


def make_gen_result(answer: str, tool_calls=None) -> CaseResult:
    return CaseResult(
        case_id="g1",
        status=CaseStatus.ERROR,
        generation=GenerationSnapshot(answer=answer, usage={}, latency_ms=1000),
        trace=TraceInfo(agent_tool_calls=tool_calls or []),
    )


# ── golden_facts / forbidden_fact / citation_format ──


def test_golden_facts_full_and_missing():
    case = make_gen_case(facts=["两种或两种以上", "共聚反应"])
    ok = get_evaluator("golden_facts")(case, make_gen_result("共聚物由两种或两种以上单体经共聚反应形成"), {})
    assert ok.value == 1.0 and ok.passed

    miss = get_evaluator("golden_facts")(case, make_gen_result("共聚物经共聚反应形成"), {})
    assert miss.value == 0.5 and not miss.passed and "两种或两种以上" in miss.detail


def test_forbidden_fact():
    case = make_gen_case(forbidden=["共聚物是天然产物"])
    bad = get_evaluator("forbidden_fact")(case, make_gen_result("共聚物是天然产物的一种"), {})
    assert bad.value == 1.0 and not bad.passed
    good = get_evaluator("forbidden_fact")(case, make_gen_result("共聚物由合成单体形成"), {})
    assert good.value == 0.0 and good.passed


def test_citation_format():
    with_cite = get_evaluator("citation_format")(
        make_gen_case(), make_gen_result("答案是……[^1]\n\n[^1]: 共聚物文档"), {})
    assert with_cite.passed
    without = get_evaluator("citation_format")(make_gen_case(), make_gen_result("答案是……"), {})
    assert not without.passed and "未找到" in without.detail


# ── 归因四分支 ──


def test_attribution_routing_failure():
    case = make_gen_case(expected_docs=["doc_a"])
    result = make_gen_result("答案")
    result.metrics = [MetricResult(name="golden_facts", value=0.0, passed=False, category="generation")]
    assert compute_attribution(case, result) == "routing_failure"


def test_attribution_retrieval_miss_zero_chunks():
    case = make_gen_case(expected_docs=["doc_a"])
    calls = [AgentToolCallRecord(tool="knowledge_retrieve", args={"query": "x"}, chunk_count=0)]
    result = make_gen_result("答案", tool_calls=calls)
    assert compute_attribution(case, result) == "retrieval_miss"


def test_attribution_retrieval_miss_expected_not_hit():
    case = make_gen_case(expected_docs=["doc_a"])
    calls = [AgentToolCallRecord(tool="knowledge_retrieve", args={}, chunk_count=5,
                                 hit_doc_ids=["doc_other"])]
    result = make_gen_result("答案", tool_calls=calls)
    assert compute_attribution(case, result) == "retrieval_miss"


def test_attribution_generation_failure_vs_ok():
    case = make_gen_case(expected_docs=["doc_a"])
    calls = [AgentToolCallRecord(tool="knowledge_retrieve", args={}, chunk_count=5,
                                 hit_doc_ids=["doc_a"])]
    result = make_gen_result("答案", tool_calls=calls)
    # 检索命中但生成指标失败 → generation_failure
    result.metrics = [MetricResult(name="golden_facts", value=0.0, passed=False, category="generation")]
    assert compute_attribution(case, result) == "generation_failure"
    # 生成指标通过 → ok
    result.metrics = [MetricResult(name="golden_facts", value=1.0, passed=True, category="generation")]
    assert compute_attribution(case, result) == "ok"


# ── fixture 解析（M0 spike 契约验证，P0-6）──


async def test_collect_agent_trace_from_real_fixture():
    """用真实 agent 响应 fixture 验证 TracePort 解析（tool_calls 扁平结构）。
    注意：fixture 来自真实 LLM 运行，调用次数会变——断言结构而非具体次数。"""
    raw = json.loads(FIXTURE.read_text(encoding="utf-8"))
    chat_result = ChatResult(answer="x", usage=raw.get("usage"), raw=raw)
    adapter = XuanjianAgentAdapter("http://unused")
    identity = Identity(logical_name="t", agent_uid="u-1")
    session = ChatSession(session_id="s-1", identity=identity)

    calls = await adapter.collect_agent_trace(session, chat_result)
    await adapter.aclose()

    assert len(calls) >= 5
    retrieves = [c for c in calls if c.tool == "knowledge_retrieve"]
    assert len(retrieves) >= 3
    # 至少一次命中（count>0 且带 document_id 列表）
    hits = [r for r in retrieves if r.chunk_count and r.chunk_count > 0]
    assert hits and all(r.hit_doc_ids for r in hits)
    # 至少一次未命中（count=0）——retrieval_miss 样本
    assert any(r.chunk_count == 0 for r in retrieves)
    # 错误形态 {error: ...} 被正确识别（如 知识库ID无效）
    errors = [r for r in retrieves if r.is_error]
    if errors:
        assert all(r.chunk_count is None for r in errors)


def test_flat_tool_calls_shape_in_fixture():
    """契约钉死：tool_calls 是扁平 {id, name, arguments}（非 OpenAI 嵌套 function.*）。"""
    raw = json.loads(FIXTURE.read_text(encoding="utf-8"))
    for m in raw["session"]["messages"]:
        for tc in m.get("tool_calls") or []:
            assert set(tc.keys()) >= {"id", "name", "arguments"}
            assert "function" not in tc  # 钉死：不是 OpenAI 嵌套结构
