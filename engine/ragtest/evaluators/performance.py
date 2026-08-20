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


@register("e2e_latency")
def e2e_latency(case: GoldenCase, result: CaseResult, params: dict) -> MetricResult:
    """端到端问答耗时（M4，target=xuanjian）。"""
    if not result.generation:
        return MetricResult(name="e2e_latency", skipped=True, category="performance",
                            detail="无生成结果")
    value = float(result.generation.latency_ms)
    threshold = float(params.get("threshold_ms", params.get("threshold", 30000)))
    return MetricResult(
        name="e2e_latency", value=value, threshold=threshold, passed=value <= threshold,
        category="performance", detail=f"E2E 耗时 {value / 1000:.1f}s（阈值 {threshold / 1000:.0f}s）",
    )
