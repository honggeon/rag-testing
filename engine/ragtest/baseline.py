"""Baseline 存取与 diff（架构 v0.3 §11，评审 P1-1 可比性校验）。

存储：artifacts/baselines/<suite_id>/<name>.json（随 git 提交，Q8）
可比性：retrieval fingerprint 或 dataset_version 不一致 → incomparable，不静默对比。
回归分类：比率类 |Δ|≤0.01 为 stable；时延类 |Δ%|≤10% 为 stable（均可配）。
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from ragtest.models.result import TestRun

BASELINE_SCHEMA_VERSION = "1"

# 分类阈值（默认）
RATE_STABLE_DELTA = 0.01      # 比率类指标（0-1）
LATENCY_STABLE_PCT = 0.10     # 时延类指标


class Baseline(BaseModel):
    schema_version: str = BASELINE_SCHEMA_VERSION
    suite_id: str
    name: str
    created_at: str = ""
    run_id: str = ""
    fingerprint: dict[str, Any] = Field(default_factory=dict)
    dataset_version: str = ""
    golden_checksum: str = ""
    summary: dict[str, Any] = Field(default_factory=dict)   # pass_rate/metrics_avg/latency
    cases: list[dict[str, Any]] = Field(default_factory=list)


class MetricDiff(BaseModel):
    name: str
    current: float | None = None
    baseline: float | None = None
    delta: float | None = None
    delta_pct: float | None = None
    classification: str = "stable"          # improvement | stable | regression
    is_latency: bool = False


class DiffReport(BaseModel):
    comparable: bool = True
    incomparable_reasons: list[str] = Field(default_factory=list)
    metrics: list[MetricDiff] = Field(default_factory=list)
    pass_rate: MetricDiff | None = None


def save_baseline(run: TestRun, baselines_dir: Path, name: str) -> Path:
    """从 TestRun 提取 baseline 并原子写。"""
    baseline = Baseline(
        suite_id=run.suite.get("id", ""),
        name=name,
        created_at=time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        run_id=run.run_id,
        fingerprint=run.environment.get("fingerprint", {}),
        dataset_version=run.suite.get("dataset_version", ""),
        golden_checksum=run.suite.get("golden_checksum", ""),
        summary={
            "pass_rate": run.summary.pass_rate,
            "metrics_avg": dict(run.summary.metrics_avg),
            "latency": dict(run.summary.latency),
        },
        cases=[
            {
                "case_id": c.case_id,
                "status": c.status.value,
                "metrics": [
                    {"name": m.name, "value": m.value} for m in c.metrics if not m.skipped
                ],
            }
            for c in run.cases
        ],
    )
    target_dir = Path(baselines_dir) / baseline.suite_id
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"{name}.json"
    tmp = target.with_suffix(".tmp")
    tmp.write_text(baseline.model_dump_json(indent=2), encoding="utf-8")
    tmp.replace(target)
    return target


def load_baseline(baselines_dir: Path, suite_id: str, name: str) -> Baseline | None:
    path = Path(baselines_dir) / suite_id / f"{name}.json"
    if not path.exists():
        return None
    return Baseline.model_validate(json.loads(path.read_text(encoding="utf-8")))


def compare(
    run: TestRun,
    baseline: Baseline,
    *,
    rate_stable_delta: float = RATE_STABLE_DELTA,
    latency_stable_pct: float = LATENCY_STABLE_PCT,
) -> DiffReport:
    """Current vs Baseline。先可比性校验，再逐指标分类。"""
    report = DiffReport()

    # ── 可比性（评审 P1-1）：指纹或数据集版本不一致 → incomparable ──
    run_fp = (run.environment.get("fingerprint") or {}).get("retrieval") or {}
    base_fp = (baseline.fingerprint or {}).get("retrieval") or {}
    fp_keys = set(run_fp) | set(base_fp)
    for key in sorted(fp_keys):
        rv, bv = run_fp.get(key), base_fp.get(key)
        if rv != bv:
            report.comparable = False
            report.incomparable_reasons.append(f"fingerprint.retrieval.{key}: {bv!r} → {rv!r}")
    if run.suite.get("dataset_version") != baseline.dataset_version:
        report.comparable = False
        report.incomparable_reasons.append(
            f"dataset_version: {baseline.dataset_version!r} → {run.suite.get('dataset_version')!r}"
        )
    if not report.comparable:
        return report

    # ── pass_rate ──
    report.pass_rate = _diff_one(
        "pass_rate", run.summary.pass_rate, baseline.summary.get("pass_rate"),
        rate_stable_delta, latency_stable_pct,
    )

    # ── metrics_avg 并集 ──
    base_avg: dict[str, float] = baseline.summary.get("metrics_avg") or {}
    for name in sorted(set(run.summary.metrics_avg) | set(base_avg)):
        report.metrics.append(_diff_one(
            name, run.summary.metrics_avg.get(name), base_avg.get(name),
            rate_stable_delta, latency_stable_pct,
        ))

    # ── latency ──
    base_lat: dict[str, float] = baseline.summary.get("latency") or {}
    for name in sorted(set(run.summary.latency) | set(base_lat)):
        report.metrics.append(_diff_one(
            name, run.summary.latency.get(name), base_lat.get(name),
            rate_stable_delta, latency_stable_pct, is_latency=True,
        ))
    return report


def _diff_one(
    name: str,
    current: float | None,
    base: float | None,
    rate_stable_delta: float,
    latency_stable_pct: float,
    *,
    is_latency: bool = False,
) -> MetricDiff:
    d = MetricDiff(name=name, current=current, baseline=base, is_latency=is_latency)
    if current is None or base is None:
        d.classification = "stable"
        return d
    d.delta = current - base
    d.delta_pct = (d.delta / base) if base else None
    if is_latency:
        # 时延：上升为回归
        if d.delta_pct is not None and d.delta_pct > latency_stable_pct:
            d.classification = "regression"
        elif d.delta_pct is not None and d.delta_pct < -latency_stable_pct:
            d.classification = "improvement"
    else:
        # 比率/质量：下降为回归
        if d.delta < -rate_stable_delta:
            d.classification = "regression"
        elif d.delta > rate_stable_delta:
            d.classification = "improvement"
    return d
