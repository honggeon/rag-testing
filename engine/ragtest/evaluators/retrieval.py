"""检索质量指标（MVP）：hit_rate_at_k / recall_at_k / mrr（架构 §9）。

语义（文档粒度，logical_id 维度）：
- recall_at_k = |expected ∩ retrieved@k| / |expected|；expected 为空 → skipped
- hit_rate_at_k = top-k 内任一 expected 命中 → 1.0 否则 0.0；expected 为空 → skipped
- mrr = 首个 expected 命中文档的最佳 rank 的倒数；无命中 → 0.0
"""

from __future__ import annotations

from ragtest.evaluators.base import expected_docs, register, retrieved_logical_docs
from ragtest.models.result import CaseResult, MetricResult
from ragtest.models.suite import GoldenCase


def _k(params: dict) -> int:
    return int(params.get("k", 5))


def _threshold(params: dict) -> float:
    return float(params.get("threshold", 1.0))


@register("recall_at_k")
def recall_at_k(case: GoldenCase, result: CaseResult, params: dict) -> MetricResult:
    expected = expected_docs(case)
    k = _k(params)
    name = f"recall_at_{k}"
    if not expected:
        return MetricResult(name=name, skipped=True, category="retrieval",
                            detail="expected.documents 为空，recall 不适用")
    retrieved = set(retrieved_logical_docs(result, k))
    hits = set(expected) & retrieved
    value = len(hits) / len(expected)
    threshold = _threshold(params)
    missing = sorted(set(expected) - retrieved)
    return MetricResult(
        name=name, value=value, threshold=threshold, passed=value >= threshold,
        category="retrieval",
        detail=f"命中 {len(hits)}/{len(expected)}" + (f"，缺失: {missing}" if missing else ""),
    )


@register("hit_rate_at_k")
def hit_rate_at_k(case: GoldenCase, result: CaseResult, params: dict) -> MetricResult:
    expected = expected_docs(case)
    k = _k(params)
    name = f"hit_rate_at_{k}"
    if not expected:
        return MetricResult(name=name, skipped=True, category="retrieval",
                            detail="expected.documents 为空，hit_rate 不适用")
    retrieved = set(retrieved_logical_docs(result, k))
    value = 1.0 if set(expected) & retrieved else 0.0
    threshold = _threshold(params)
    return MetricResult(
        name=name, value=value, threshold=threshold, passed=value >= threshold,
        category="retrieval",
        detail="top-%d 内%s命中 expected" % (k, "" if value else "未"),
    )


@register("max_score_below")
def max_score_below(case: GoldenCase, result: CaseResult, params: dict) -> MetricResult:
    """无关问题鲁棒性：top-k 最高分不得超过阈值（防止系统对无关问题"自信作答"）。

    背景：arag 在 score_threshold=0 时即使无关问题也会返回低分 chunks，
    且有意义的断言是"最高分足够低"，而不是"无结果"。"""
    if not result.retrieval or not result.retrieval.chunks:
        return MetricResult(name="max_score_below", value=0.0,
                            threshold=float(params.get("threshold", 0.5)),
                            passed=True, category="retrieval",
                            detail="无检索结果（最高分视为 0）")
    top = max(c.score for c in result.retrieval.chunks)
    threshold = float(params.get("threshold", 0.5))
    return MetricResult(
        name="max_score_below", value=top, threshold=threshold,
        passed=top <= threshold, category="retrieval",
        detail=f"top-1 score={top:.3f}（阈值 {threshold}）",
    )


@register("mrr")
def mrr(case: GoldenCase, result: CaseResult, params: dict) -> MetricResult:
    expected = set(expected_docs(case))
    if not expected or not result.retrieval:
        return MetricResult(name="mrr", skipped=not expected, value=None if expected else 0.0,
                            category="retrieval",
                            detail="expected.documents 为空" if not expected else "无检索结果")
    best_rank: int | None = None
    for chunk in result.retrieval.chunks:
        if chunk.logical_doc in expected:
            best_rank = chunk.rank if best_rank is None else min(best_rank, chunk.rank)
    value = 1.0 / best_rank if best_rank else 0.0
    threshold = _threshold(params) if "threshold" in params else 0.5
    return MetricResult(
        name="mrr", value=value, threshold=threshold, passed=value >= threshold,
        category="retrieval",
        detail=f"首个命中 rank={best_rank}" if best_rank else "无命中",
    )
