"""报告写出：run.json 原子写 + summary.md + junit.xml（架构 §10/§12.1）。"""

from __future__ import annotations

import json
import os
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

from ragtest.baseline import DiffReport
from ragtest.gate import GateVerdict
from ragtest.models.result import TestRun


def _atomic_write(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.stem}-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    return path


def write_run_json(run: TestRun, run_dir: Path) -> Path:
    return _atomic_write(Path(run_dir) / "run.json", run.model_dump_json(indent=2))


def load_run_json(path: Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


# ── summary.md ──────────────────────────────────────────────────────────────

_CLASS_ICON = {"improvement": "🟢", "stable": "⚪", "regression": "🔴"}


def write_summary_md(
    run: TestRun,
    run_dir: Path,
    *,
    diff: DiffReport | None = None,
    gate: GateVerdict | None = None,
) -> Path:
    s = run.summary
    lines = [
        f"# RAG 测试报告 — {run.suite.get('id', '')} / {run.run_id}",
        "",
        f"- 终态: **{run.lifecycle[-1].state if run.lifecycle else '?'}**",
        f"- 通过率: **{s.pass_rate:.1%}**（{s.passed}/{s.passed + s.failed}，"
        f"error {s.error}，skipped {s.skipped}）",
        f"- 耗时: {run.environment.get('duration_ms', 0) / 1000:.1f}s"
        f"｜ git: {run.environment.get('git_commit') or '-'}",
        "",
    ]

    # Quality Gate
    if gate and not gate.skipped:
        lines.append(f"## Quality Gate：{'✅ PASS' if gate.passed else '❌ FAIL'}")
        lines.append("")
        if gate.violations:
            lines.append("| 违规 | 阈值 | 实际 | 说明 |")
            lines.append("|---|---|---|---|")
            for v in gate.violations:
                lines.append(
                    f"| {v.section}.{v.metric} | {v.expr} | {v.actual} | {v.detail} |"
                )
            lines.append("")

    # 指标
    if s.metrics_avg:
        lines.append("## 指标（均值）")
        lines.append("")
        lines.append("| 指标 | 值 |")
        lines.append("|---|---|")
        for name, value in sorted(s.metrics_avg.items()):
            lines.append(f"| {name} | {value:.3f} |")
        for name, value in sorted(s.latency.items()):
            lines.append(f"| {name} | {value:.0f}ms |")
        lines.append("")

    # Baseline diff
    if diff:
        lines.append("## Baseline 对比")
        lines.append("")
        if not diff.comparable:
            lines.append("⚠️ **不可比（incomparable）**：")
            for r in diff.incomparable_reasons:
                lines.append(f"- {r}")
            lines.append("")
        else:
            lines.append("| 指标 | Baseline | Current | Δ | 分类 |")
            lines.append("|---|---|---|---|---|")
            rows = ([diff.pass_rate] if diff.pass_rate else []) + diff.metrics
            for m in rows:
                if m is None or m.current is None or m.baseline is None:
                    continue
                delta = f"{m.delta:+.3f}" if not m.is_latency else f"{m.delta_pct:+.1%}"
                lines.append(
                    f"| {m.name} | {m.baseline:.3f} | {m.current:.3f} | {delta} "
                    f"| {_CLASS_ICON.get(m.classification, '')} {m.classification} |"
                )
            lines.append("")

    # 失败 case
    failed = [c for c in run.cases if c.status.value in ("failed", "error")]
    if failed:
        lines.append(f"## 失败 Case（{len(failed)}）")
        lines.append("")
        for c in failed:
            lines.append(f"### {c.case_id} {c.name}（{c.status.value}）")
            lines.append("")
            if c.error:
                lines.append(f"- 错误: [{c.error.kind}] {c.error.message}")
            for m in c.metrics:
                if not m.passed and not m.skipped:
                    lines.append(
                        f"- ✗ {m.name}: {m.value}（阈值 {m.threshold}）{m.detail}"
                    )
            for a in c.assertions:
                if not a.passed:
                    lines.append(f"- ✗ {a.kind}: {a.detail}")
            if c.retrieval:
                lines.append(
                    f"- 检索 top-{len(c.retrieval.chunks)}: "
                    + ", ".join(
                        f"#{ch.rank} {ch.logical_doc or ch.document_id}({ch.score:.2f})"
                        for ch in c.retrieval.chunks[:5]
                    )
                )
            lines.append("")
    return _atomic_write(Path(run_dir) / "summary.md", "\n".join(lines))


# ── junit.xml ────────────────────────────────────────────────────────────────


def write_junit_xml(run: TestRun, run_dir: Path) -> Path:
    suite = ET.Element(
        "testsuite",
        {
            "name": run.suite.get("id", "ragtest"),
            "tests": str(run.summary.total),
            "failures": str(run.summary.failed),
            "errors": str(run.summary.error),
            "skipped": str(run.summary.skipped),
            "time": f"{run.environment.get('duration_ms', 0) / 1000:.3f}",
        },
    )
    for c in run.cases:
        case_el = ET.SubElement(
            suite, "testcase",
            {"classname": run.suite.get("id", ""), "name": f"{c.case_id} {c.name}"},
        )
        if c.status.value == "failed":
            fail = ET.SubElement(case_el, "failure")
            parts = [f"{m.name}={m.value}（阈值 {m.threshold}）{m.detail}"
                     for m in c.metrics if not m.passed and not m.skipped]
            parts += [f"{a.kind}: {a.detail}" for a in c.assertions if not a.passed]
            fail.text = "\n".join(parts) or "断言失败"
        elif c.status.value == "error":
            err = ET.SubElement(case_el, "error")
            err.text = f"[{c.error.kind}] {c.error.message}" if c.error else "执行错误"
        elif c.status.value == "skipped":
            ET.SubElement(case_el, "skipped")
    tree = ET.ElementTree(suite)
    ET.indent(tree, space="  ")
    path = Path(run_dir) / "junit.xml"
    tree.write(path, encoding="utf-8", xml_declaration=True)
    return path
