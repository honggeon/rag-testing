"""M6：LLM Judge / 排名指标 / repeat 采样 / cross_user_leak 测试。"""

import pytest

from ragtest.evaluators import get_evaluator, judge, known_evaluators
from ragtest.models import CaseStatus
from ragtest.models.result import (
    AgentToolCallRecord,
    CaseResult,
    ChunkSnapshot,
    GenerationSnapshot,
    MetricResult,
    RetrievalSnapshot,
    TraceInfo,
)
from ragtest.models.suite import (
    EvaluatorSpec,
    ExpectedSpec,
    GoldenCase,
    InputSpec,
)
from ragtest.runner.executor import _merge_samples


# ── 注册完整性 ──


def test_m6_evaluators_registered():
    assert {
        "faithfulness", "answer_relevancy", "semantic_similarity",
        "precision_at_k", "ndcg_at_k", "map", "cross_user_leak",
    } <= known_evaluators()


# ── LLM Judge（fake client）──


class FakeJudge:
    def __init__(self, value: float):
        self.value = value

    async def score(self, prompt: str) -> float:
        return self.value


def make_gen_case(facts=None, forbidden=None, reference=None) -> GoldenCase:
    return GoldenCase(
        id="g1",
        input=InputSpec(question="q"),
        expected=ExpectedSpec(
            golden_facts=facts or [],
            forbidden_facts=forbidden or [],
            answer={"mode": "semantic", "reference": reference} if reference else None,
        ),
        evaluators=[],
    )


def make_gen_result(answer: str, context: str = "") -> CaseResult:
    return CaseResult(
        case_id="g1", status=CaseStatus.ERROR,
        generation=GenerationSnapshot(answer=answer, context=context, latency_ms=1),
    )


async def test_judge_skipped_without_client():
    judge.set_judge(None)
    m = await get_evaluator("faithfulness")(make_gen_case(), make_gen_result("a"), {})
    assert m.skipped and "未配置" in m.detail


async def test_faithfulness_scores():
    judge.set_judge(FakeJudge(0.92))
    try:
        case = make_gen_case()
        m = await get_evaluator("faithfulness")(
            case, make_gen_result("答", context="上下文"), {"threshold": 0.85})
        assert m.value == 0.92 and m.passed
        m2 = await get_evaluator("faithfulness")(
            case, make_gen_result("答", context="上下文"), {"threshold": 0.95})
        assert not m2.passed
    finally:
        judge.set_judge(None)


async def test_answer_relevancy_and_semantic():
    judge.set_judge(FakeJudge(0.8))
    try:
        m = await get_evaluator("answer_relevancy")(make_gen_case(), make_gen_result("答"), {})
        assert m.passed
        ms = await get_evaluator("semantic_similarity")(
            make_gen_case(reference="参考答案"), make_gen_result("答"), {"threshold": 0.7})
        assert ms.passed
        mskip = await get_evaluator("semantic_similarity")(
            make_gen_case(), make_gen_result("答"), {})
        assert mskip.skipped  # 无参考答案
    finally:
        judge.set_judge(None)


# ── 排名指标（二进制相关性）──


def make_retr_case(expected: list[str]) -> GoldenCase:
    return GoldenCase(
        id="r1", input=InputSpec(query="q"),
        expected=ExpectedSpec(documents=expected),
    )


def make_retr_result(hits: list[tuple[str, float]]) -> CaseResult:
    return CaseResult(
        case_id="r1", status=CaseStatus.ERROR,
        retrieval=RetrievalSnapshot(
            query="q", top_k=5,
            chunks=[
                ChunkSnapshot(chunk_id=f"c{i}", document_id=f"d{i}", logical_doc=doc,
                              rank=i + 1, score=score)
                for i, (doc, score) in enumerate(hits)
            ],
        ),
    )


def test_precision_ndcg_map():
    case = make_retr_case(["a", "b"])
    result = make_retr_result([("a", 0.9), ("x", 0.8), ("b", 0.7), ("y", 0.6)])
    p5 = get_evaluator("precision_at_k")(case, result, {"k": 5})
    assert p5.value == pytest.approx(0.4) and p5.name == "precision_at_5"

    ndcg = get_evaluator("ndcg_at_k")(case, result, {"k": 5})
    # DCG = 1/log2(2) + 1/log2(4) = 1.5；IDCG = 1/log2(2) + 1/log2(3) ≈ 1.6309
    assert ndcg.value == pytest.approx(1.5 / (1 + 1 / 1.58496))

    ap = get_evaluator("map")(case, result, {})
    # AP = (1/1 + 2/3)/2 = (1 + 0.6667)/2 ≈ 0.8333
    assert ap.value == pytest.approx(0.83333, abs=1e-4)

    # 全未命中
    miss = get_evaluator("map")(case, make_retr_result([("x", 0.5)]), {})
    assert miss.value == 0.0


# ── cross_user_leak ──


async def test_cross_user_leak():
    case = make_gen_case(forbidden=["绝密数据"])
    result = make_gen_result("回答包含绝密数据内容")
    result.trace = TraceInfo(agent_tool_calls=[
        AgentToolCallRecord(tool="knowledge_retrieve", args={}, chunk_count=2,
                            hit_doc_ids=["secret_doc"]),
    ])
    case.expected.forbidden_documents = ["secret_doc"]
    m = get_evaluator("cross_user_leak")(case, result, {"threshold": 0})
    assert m.value == 2.0 and not m.passed

    clean = get_evaluator("cross_user_leak")(
        case, make_gen_result("正常回答"), {"threshold": 0})
    assert clean.value == 0.0 and clean.passed


# ── repeat 采样合并 ──


def test_merge_samples_average_and_strict_pass():
    case = make_gen_case()
    s1 = make_gen_result("a")
    s1.status = CaseStatus.PASSED
    s1.metrics = [MetricResult(name="m", value=0.8, threshold=0.7, passed=True)]
    s2 = make_gen_result("a")
    s2.status = CaseStatus.FAILED
    s2.metrics = [MetricResult(name="m", value=0.6, threshold=0.7, passed=False)]
    merged = _merge_samples(case, [s1, s2])
    assert merged.status is CaseStatus.FAILED          # 严格：任一采样失败
    assert merged.metrics[0].value == pytest.approx(0.7)
    assert "采样N=2" in merged.metrics[0].detail

    s4 = make_gen_result("a")
    s4.status = CaseStatus.PASSED
    s4.metrics = [MetricResult(name="m", value=0.9, threshold=0.7, passed=True)]
    s5 = make_gen_result("a")
    s5.status = CaseStatus.PASSED
    s5.metrics = [MetricResult(name="m", value=0.8, threshold=0.7, passed=True)]
    merged2 = _merge_samples(case, [s4, s5])
    assert merged2.status is CaseStatus.PASSED
    assert merged2.metrics[0].value == pytest.approx(0.85)
