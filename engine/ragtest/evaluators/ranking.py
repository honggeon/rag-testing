"""排名质量指标（M6）：precision_at_k / ndcg_at_k / map（二进制相关性）。

说明：binary relevance（expected 文档 rel=1，其余 0）是分级相关性的最小形态，
NDCG 采用 binary gain；map 为单 case 的平均精确率（run 级 MAP 由 metrics_avg 均值给出）。
"""

from __future__ import annotations

import math

from ragtest.evaluators.base import expected_docs, register, retrieved_logical_docs
from ragtest.models.result import CaseResult, MetricResult
from ragtest.models.suite import GoldenCase


def _expected_set(case: GoldenCase) -> set[str]:
    return set(expected_docs(case))


@register("precision_at_k")
def precision_at_k(case: GoldenCase, result: CaseResult, params: dict) -> MetricResult:
    expected = _expected_set(case)
    k = int(params.get("k", 5))
    name = f"precision_at_{k}"
    if not expected:
        return MetricResult(name=name, skipped=True, category="retrieval",
                            detail="expected 为空，precision 不适用")
    retrieved = retrieved_logical_docs(result, k)
    hits = sum(1 for d in retrieved if d in expected)
    value = hits / k
    threshold = float(params.get("threshold", 0.8))
    return MetricResult(
        name=name, value=value, threshold=threshold, passed=value >= threshold,
        category="retrieval", detail=f"top-{k} 命中 {hits}/{k}",
    )


@register("ndcg_at_k")
def ndcg_at_k(case: GoldenCase, result: CaseResult, params: dict) -> MetricResult:
    expected = _expected_set(case)
    k = int(params.get("k", 5))
    name = f"ndcg_at_{k}"
    if not expected:
        return MetricResult(name=name, skipped=True, category="retrieval",
                            detail="expected 为空，NDCG 不适用")
    retrieved = retrieved_logical_docs(result, k)
    dcg = 0.0
    for i, doc in enumerate(retrieved):
        if doc in expected:
            dcg += 1.0 / math.log2(i + 2)
    idcg = sum(1.0 / math.log2(i + 2) for i in range(min(k, len(expected))))
    value = dcg / idcg if idcg else 0.0
    threshold = float(params.get("threshold", 0.8))
    return MetricResult(
        name=name, value=value, threshold=threshold, passed=value >= threshold,
        category="retrieval", detail=f"DCG={dcg:.3f}/IDCG={idcg:.3f}（top-{k}）",
    )


@register("map")
def map_(case: GoldenCase, result: CaseResult, params: dict) -> MetricResult:
    """Average Precision（单 case；run 级 MAP = metrics_avg['map']）。"""
    expected = _expected_set(case)
    if not expected:
        return MetricResult(name="map", skipped=True, category="retrieval",
                            detail="expected 为空，AP 不适用")
    retrieved = retrieved_logical_docs(result)
    hits = 0
    ap = 0.0
    for i, doc in enumerate(retrieved):
        if doc in expected:
            hits += 1
            ap += hits / (i + 1)
    value = ap / len(expected)
    threshold = float(params.get("threshold", 0.6))
    return MetricResult(
        name="map", value=value, threshold=threshold, passed=value >= threshold,
        category="retrieval", detail=f"AP={value:.3f}（命中 {hits}/{len(expected)}）",
    )
