"""缺陷证据 evaluator：ab_results_differ（A/B 对比探针）。

用途（defect suite）：两个仅某项参数不同的检索请求，若被测系统正确响应该参数，
结果应不同；若结果完全一致，说明参数被静默忽略（缺陷复现）。

配合 case.expected_fail 使用：结果一致 → 本 evaluator fail → expected_fail 翻转为
「缺陷已复现（证据有效）」。
"""

from __future__ import annotations

from ragtest.evaluators.base import register
from ragtest.models.result import CaseResult, MetricResult
from ragtest.models.suite import GoldenCase


@register("ab_results_differ")
def ab_results_differ(case: GoldenCase, result: CaseResult, params: dict) -> MetricResult:
    ab = (result.trace.server_signals or {}).get("ab") or {}
    identical = ab.get("identical")
    if identical is None:
        return MetricResult(name="ab_results_differ", skipped=True, category="defect",
                            detail="非 A/B case，无对比数据")
    passed = not identical
    return MetricResult(
        name="ab_results_differ",
        value=0.0 if identical else 1.0,
        threshold=1.0,
        passed=passed,
        category="defect",
        detail=(
            f"A/B 结果完全一致（参数 {ab.get('a_params')} vs {ab.get('b_params')} 未生效）"
            if identical else
            "A/B 结果不同（参数已生效）"
        ),
    )
