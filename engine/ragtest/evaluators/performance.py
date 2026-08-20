"""性能指标（MVP）：search_latency（架构 §9，客户端计时）。"""

from __future__ import annotations

from ragtest.evaluators.base import register
from ragtest.models.result import CaseResult, MetricResult
from ragtest.models.suite import GoldenCase


@register("search_latency")
def search_latency(case: GoldenCase, result: CaseResult, params: dict) -> MetricResult:
    if not result.retrieval:
        return MetricResult(name="search_latency", skipped=True, category="performance",
                            detail="无检索结果")
    value = float(result.retrieval.latency_ms)
    threshold = float(params.get("threshold_ms", params.get("threshold", 2000)))
    return MetricResult(
        name="search_latency", value=value, threshold=threshold, passed=value <= threshold,
        category="performance", detail=f"检索耗时 {value:.0f}ms（阈值 {threshold:.0f}ms）",
    )
