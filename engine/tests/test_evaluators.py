"""检索/安全/性能 evaluator 单测（手工构造检索结果，不依赖 adapter）。"""

import pytest

from ragtest.evaluators import get_evaluator, known_evaluators
from ragtest.models.result import CaseResult, ChunkSnapshot, RetrievalSnapshot
from ragtest.models.suite import EvaluatorSpec, ExpectedSpec, GoldenCase, InputSpec


def make_case(expected: list[str], forbidden: list[str] | None = None) -> GoldenCase:
    return GoldenCase(
        id="t1",
        input=InputSpec(query="q", top_k=5),
        expected=ExpectedSpec(documents=expected, forbidden_documents=forbidden or []),
    )


def make_result(hits: list[tuple[str, float]]) -> CaseResult:
    """hits: [(logical_doc, score), ...] 按 rank 序。"""
    return CaseResult(
        case_id="t1",
        status="passed",
        retrieval=RetrievalSnapshot(
            query="q",
            top_k=5,
            chunks=[
                ChunkSnapshot(
                    chunk_id=f"c{i}", document_id=f"d{i}", logical_doc=doc,
                    rank=i + 1, score=score,
                )
                for i, (doc, score) in enumerate(hits)
            ],
            latency_ms=123,
        ),
    )


def test_registry_complete():
    assert {
        "recall_at_k", "hit_rate_at_k", "mrr",
        "forbidden_document", "permission_leak", "search_latency",
    } <= known_evaluators()


def test_recall_full_and_partial():
    case = make_case(["a", "b"])
    full = get_evaluator("recall_at_k")(case, make_result([("a", 0.9), ("b", 0.8)]), {"k": 5, "threshold": 1.0})
    assert full.value == 1.0 and full.passed and full.name == "recall_at_5"

    half = get_evaluator("recall_at_k")(case, make_result([("a", 0.9)]), {"k": 5, "threshold": 1.0})
    assert half.value == 0.5 and not half.passed and "b" in half.detail


def test_recall_respects_k_window():
    case = make_case(["a"])
    # expected 在第 6 名，k=5 窗口外 → 未命中
    result = make_result([(f"x{i}", 0.9) for i in range(5)] + [("a", 0.1)])
    m = get_evaluator("recall_at_k")(case, result, {"k": 5, "threshold": 1.0})
    assert m.value == 0.0 and not m.passed


def test_recall_empty_expected_skipped():
    m = get_evaluator("recall_at_k")(make_case([]), make_result([]), {"k": 5})
    assert m.skipped and m.passed  # skipped 不计入失败


def test_hit_rate():
    case = make_case(["a", "b"])
    hit = get_evaluator("hit_rate_at_k")(case, make_result([("x", 0.9), ("b", 0.5)]), {"k": 5})
    assert hit.value == 1.0 and hit.passed
    miss = get_evaluator("hit_rate_at_k")(case, make_result([("x", 0.9)]), {"k": 5})
    assert miss.value == 0.0 and not miss.passed


def test_mrr_first_hit_rank():
    case = make_case(["a"])
    m = get_evaluator("mrr")(case, make_result([("x", 0.9), ("y", 0.8), ("a", 0.7)]), {})
    assert m.value == pytest.approx(1 / 3)
    m0 = get_evaluator("mrr")(case, make_result([("x", 0.9)]), {})
    assert m0.value == 0.0 and not m0.passed


def test_forbidden_and_permission_leak():
    case = make_case([], forbidden=["secret", "other"])
    leak = get_evaluator("permission_leak")(
        case, make_result([("public", 0.9), ("secret", 0.5)]), {"threshold": 0})
    assert leak.value == 1.0 and not leak.passed and "secret" in leak.detail

    clean = get_evaluator("forbidden_document")(case, make_result([("public", 0.9)]), {})
    assert clean.value == 0.0 and clean.passed


def test_search_latency():
    m = get_evaluator("search_latency")(make_case([]), make_result([]), {"threshold_ms": 200})
    assert m.value == 123.0 and m.passed
    m2 = get_evaluator("search_latency")(make_case([]), make_result([]), {"threshold_ms": 100})
    assert not m2.passed
