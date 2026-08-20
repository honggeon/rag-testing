"""生成质量指标（M4）：golden_facts / forbidden_fact / citation_format /
retrieval_attribution（归因诊断）。"""

from __future__ import annotations

import re

from ragtest.evaluators.base import register
from ragtest.models.result import CaseResult, MetricResult
from ragtest.models.suite import GoldenCase

_CITATION_RE = re.compile(r"\[\^\d+\]|参考来源|📚")


def _answer(result: CaseResult) -> str:
    return result.generation.answer if result.generation else ""


@register("golden_facts")
def golden_facts(case: GoldenCase, result: CaseResult, params: dict) -> MetricResult:
    """答案必须包含的事实点（contains 匹配）。"""
    facts = case.expected.golden_facts if case.expected else []
    if not facts:
        return MetricResult(name="golden_facts", skipped=True, category="generation",
                            detail="未声明 golden_facts")
    answer = _answer(result)
    missing = [f for f in facts if f not in answer]
    value = (len(facts) - len(missing)) / len(facts)
    threshold = float(params.get("threshold", 1.0))
    return MetricResult(
        name="golden_facts", value=value, threshold=threshold, passed=value >= threshold,
        category="generation",
        detail=f"事实覆盖 {len(facts) - len(missing)}/{len(facts)}"
               + (f"，缺失: {missing}" if missing else ""),
    )


@register("forbidden_fact")
def forbidden_fact(case: GoldenCase, result: CaseResult, params: dict) -> MetricResult:
    """答案中禁止出现的事实（幻觉/过时/错误事实检测）。"""
    facts = case.expected.forbidden_facts if case.expected else []
    if not facts:
        return MetricResult(name="forbidden_fact", skipped=True, category="generation",
                            detail="未声明 forbidden_facts")
    answer = _answer(result)
    hits = [f for f in facts if f in answer]
    value = float(len(hits))
    threshold = float(params.get("threshold", 0))
    return MetricResult(
        name="forbidden_fact", value=value, threshold=threshold, passed=value <= threshold,
        category="generation",
        detail=f"命中禁止事实 {len(hits)} 个" + (f": {hits}" if hits else ""),
    )


@register("citation_format")
def citation_format(case: GoldenCase, result: CaseResult, params: dict) -> MetricResult:
    """引用格式检查：agent 无结构化 citation 输出（P1-6），从文本抽取 [^n] / 参考来源标记。"""
    answer = _answer(result)
    if not answer:
        return MetricResult(name="citation_format", value=0.0,
                            threshold=float(params.get("threshold", 1.0)),
                            passed=False, category="generation", detail="无回答")
    found = _CITATION_RE.findall(answer)
    value = 1.0 if found else 0.0
    threshold = float(params.get("threshold", 1.0))
    return MetricResult(
        name="citation_format", value=value, threshold=threshold, passed=value >= threshold,
        category="generation",
        detail=f"找到 {len(found)} 处引用标记" if found else "未找到 [^n]/参考来源 引用标记",
    )


@register("retrieval_attribution")
def retrieval_attribution(case: GoldenCase, result: CaseResult, params: dict) -> MetricResult:
    """归因诊断（skipped：只记录不计入 pass/fail）。

    读取 executor 预计算的 trace.attribution：
    routing_failure / retrieval_miss / generation_failure / ok
    """
    attribution = result.trace.attribution
    if not attribution:
        return MetricResult(name="retrieval_attribution", skipped=True, category="generation",
                            detail="无归因数据（非 E2E case）")
    return MetricResult(
        name="retrieval_attribution", skipped=True, category="generation",
        detail=f"归因: {attribution}",
    )
