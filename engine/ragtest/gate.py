"""Quality Gate（架构 v0.3 §12.1）。

配置（suite yaml quality_gate 段）：
    overall:     {pass_rate: ">=0.95"}
    retrieval:   {recall_at_5: ">=0.90", mrr: ">=0.60"}
    security:    {permission_leak: "==0"}
    performance: {search_p95_ms: "<=2000"}
    regression:  {recall_at_5_drop: "<=0.03"}   # 需 baseline 可比

语义：
- 先绝对阈值（overall/retrieval/security/performance），后回归项（regression）
- 指标缺失 → 违规（fail closed，CI 门禁保守设计）
- defect suite（tags 含 defect）默认不做 gate（P1-3：known-issue 不挡门）
- violations 全量列出；gate 失败 → CLI exit 1
"""

from __future__ import annotations

import re

from pydantic import BaseModel, Field

from ragtest.baseline import DiffReport
from ragtest.models.result import TestRun

_EXPR = re.compile(r"^\s*(>=|<=|==|>|<)?\s*(-?\d+(?:\.\d+)?)\s*$")


class GateViolation(BaseModel):
    section: str
    metric: str
    expr: str
    actual: float | None
    detail: str = ""


class GateVerdict(BaseModel):
    passed: bool = True
    skipped: bool = False
    skip_reason: str = ""
    violations: list[GateViolation] = Field(default_factory=list)
    evaluated: list[str] = Field(default_factory=list)


def _parse_expr(expr) -> tuple[str, float]:
    m = _EXPR.match(str(expr))
    if not m:
        raise ValueError(f"非法阈值表达式: {expr!r}（支持 >=, <=, ==, >, <）")
    op = m.group(1) or ">="
    return op, float(m.group(2))


def _satisfy(actual: float, op: str, want: float) -> bool:
    match op:
        case ">=":
            return actual >= want
        case "<=":
            return actual <= want
        case "==":
            return actual == want
        case ">":
            return actual > want
        case "<":
            return actual < want
    return False


def evaluate_gate(
    run: TestRun,
    gate_config: dict,
    diff: DiffReport | None = None,
    *,
    is_defect_suite: bool = False,
) -> GateVerdict:
    verdict = GateVerdict()
    if not gate_config:
        verdict.skipped = True
        verdict.skip_reason = "suite 未配置 quality_gate"
        return verdict
    if is_defect_suite:
        verdict.skipped = True
        verdict.skip_reason = "defect suite（expected_fail 证据套件）默认不做质量门"
        return verdict

    def check(section: str, metric: str, expr, actual: float | None) -> None:
        op, want = _parse_expr(expr)
        verdict.evaluated.append(f"{section}.{metric}")
        if actual is None:
            verdict.violations.append(GateViolation(
                section=section, metric=metric, expr=str(expr), actual=None,
                detail="指标缺失（fail closed）",
            ))
            return
        if not _satisfy(actual, op, want):
            verdict.violations.append(GateViolation(
                section=section, metric=metric, expr=str(expr), actual=actual,
                detail=f"实际 {actual} 不满足 {op} {want}",
            ))

    # ── 绝对阈值 ──
    for metric, expr in (gate_config.get("overall") or {}).items():
        actual = run.summary.pass_rate if metric == "pass_rate" else None
        check("overall", metric, expr, actual)

    for metric, expr in (gate_config.get("retrieval") or {}).items():
        check("retrieval", metric, expr, run.summary.metrics_avg.get(metric))

    for metric, expr in (gate_config.get("security") or {}).items():
        check("security", metric, expr, run.summary.metrics_avg.get(metric))

    for metric, expr in (gate_config.get("performance") or {}).items():
        check("performance", metric, expr, run.summary.latency.get(metric))

    # ── 回归项（需可比 baseline；不可比 → 违规标注但不重复扣分）──
    regression_cfg = gate_config.get("regression") or {}
    if regression_cfg:
        if diff is None or not diff.comparable:
            for metric, expr in regression_cfg.items():
                verdict.violations.append(GateViolation(
                    section="regression", metric=metric, expr=str(expr), actual=None,
                    detail="无可比 baseline，回归项无法评估（fail closed）"
                    if diff is None else f"baseline 不可比: {'; '.join(diff.incomparable_reasons[:2])}",
                ))
        else:
            diff_by_name = {m.name: m for m in diff.metrics}
            if diff.pass_rate:
                diff_by_name["pass_rate"] = diff.pass_rate
            for metric, expr in regression_cfg.items():
                # 约定：<metric>_drop = baseline - current（下降幅度，越小越好）
                name = metric[:-5] if metric.endswith("_drop") else metric
                d = diff_by_name.get(name)
                drop = (-d.delta) if d and d.delta is not None else None
                check("regression", metric, expr, drop)

    verdict.passed = not verdict.violations
    return verdict
