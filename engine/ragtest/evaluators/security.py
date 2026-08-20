"""安全指标（MVP）：forbidden_document / permission_leak（架构 §9）。

forbidden_documents 中的 logical_id 出现在检索结果（任意 rank）即为违规。
permission_leak 语义同上，用于越权场景（identity 无授权时检索结果必须为空泄漏）。
"""

from __future__ import annotations

from ragtest.evaluators.base import forbidden_docs, register, retrieved_logical_docs
from ragtest.models.result import CaseResult, MetricResult
from ragtest.models.suite import GoldenCase


def _check_forbidden(case: GoldenCase, result: CaseResult, params: dict, name: str) -> MetricResult:
    forbidden = set(forbidden_docs(case))
    retrieved = retrieved_logical_docs(result)  # 全部 rank
    leaked = sorted(forbidden & set(retrieved))
    value = float(len(leaked))
    threshold = float(params.get("threshold", 0))
    return MetricResult(
        name=name, value=value, threshold=threshold, passed=value <= threshold,
        category="security",
        detail=f"违规命中 {len(leaked)} 个 forbidden 文档" + (f": {leaked}" if leaked else ""),
    )


@register("forbidden_document")
def forbidden_document(case: GoldenCase, result: CaseResult, params: dict) -> MetricResult:
    return _check_forbidden(case, result, params, "forbidden_document")


@register("permission_leak")
def permission_leak(case: GoldenCase, result: CaseResult, params: dict) -> MetricResult:
    return _check_forbidden(case, result, params, "permission_leak")
