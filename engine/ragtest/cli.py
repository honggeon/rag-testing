"""ragtest CLI（架构 §12.1 exit code 语义）。

用法：
    uv run python -m ragtest.cli run --suite <path> [--baseline main] [--save-baseline main]
    uv run python -m ragtest.cli validate --suite <path>
    uv run python -m ragtest.cli baseline-update --suite <path> --name main [--run <run_dir>]

exit code：0=通过（含 gate）；1=gate 失败 / case 未达标；2=运行错误；3=配置/资产错误
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import signal
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ragtest.adapters.arag import AragAdapter
from ragtest.adapters.base import Identity
from ragtest.adapters.xuanjian import XuanjianAgentAdapter
from ragtest.artifacts import RunStatusWriter
from ragtest.assets import AssetError, load_suite
from ragtest.baseline import compare, load_baseline, save_baseline
from ragtest.config import load_settings
from ragtest.evaluators import known_evaluators
from ragtest.gate import evaluate_gate
from ragtest.models import RunState
from ragtest.models.result import TestRun
from ragtest.report import write_junit_xml, write_run_json, write_summary_md
from ragtest.runner.lifecycle import SuiteRunner

REPO_ROOT = Path(__file__).resolve().parents[2]


def _suite_checksum(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def decide_exit_code(final_state: str, gate_passed: bool | None, summary) -> int:
    """exit code 矩阵（§12.1）。"""
    if final_state in (RunState.ERROR.value, RunState.TIMEOUT.value, RunState.CANCELLED.value):
        return 2
    if gate_passed is not None:
        return 0 if gate_passed else 1
    # 无 gate 配置：以 case 失败/error 作为质量未达标信号
    return 1 if (summary.failed or summary.error) else 0


async def _run(args: argparse.Namespace) -> int:
    settings = load_settings()
    if not settings.arag_admin_password:
        print("[CONFIG] 缺少 RAGTEST_ARAG_ADMIN_PASSWORD", file=sys.stderr)
        return 3

    try:
        suite, dataset, dataset_base = load_suite(
            args.suite, known_evaluators=known_evaluators()
        )
    except AssetError as e:
        print(f"[CONFIG] {e}", file=sys.stderr)
        return 3

    run_id = args.run_id or (
        time.strftime("%Y%m%d-%H%M%S")
        + f"-{hashlib.sha256(str(time.time_ns()).encode()).hexdigest()[:6]}"
    )
    out_dir = (args.artifacts_dir or settings.artifacts_dir) / "runs" / run_id
    writer = RunStatusWriter(out_dir, run_id, total=len(suite.cases))
    print(f"[RUN] suite={suite.id} cases={len(suite.cases)} run_id={run_id}")
    print(f"[RUN] artifacts: {out_dir}")

    admin_identity = Identity(
        logical_name="owner", role="admin",
        email=settings.arag_admin_email, password=settings.arag_admin_password,
    )

    # target adapter（M4）：suite.adapters.target == xuanjian 时启用 E2E 生成
    target_adapter = None
    if suite.adapters.target == "xuanjian":
        target_adapter = XuanjianAgentAdapter(settings.agent_base_url)

    async with AragAdapter(settings.arag_base_url, settings.arag_auth_url) as adapter:
        runner = SuiteRunner(
            suite=suite, dataset=dataset, dataset_base=dataset_base, adapter=adapter,
            admin_identity=admin_identity, run_id=run_id, writer=writer,
            repo_root=REPO_ROOT, ingest_timeout_s=args.ingest_timeout,
            target_adapter=target_adapter,
        )
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, setattr, runner, "_cancelled", True)
            except NotImplementedError:
                pass
        run = await runner.run_suite()

    run.suite["golden_checksum"] = _suite_checksum(args.suite)
    final_state = writer.status.state
    baselines_dir = (args.artifacts_dir or settings.artifacts_dir) / "baselines"

    # ── Baseline diff（COMPARABLE_BASELINE）──
    diff = None
    if args.baseline:
        baseline = load_baseline(baselines_dir, suite.id, args.baseline)
        if baseline is None:
            print(f"[BASELINE] 未找到 {suite.id}/{args.baseline}.json，跳过 diff")
        else:
            diff = compare(run, baseline)
            run.baseline_diff = diff.model_dump()
            if diff.comparable:
                regressions = [m for m in diff.metrics if m.classification == "regression"]
                print(f"[BASELINE] vs {args.baseline}: 可比，regression {len(regressions)} 项")
            else:
                print(f"[BASELINE] ⚠️ 不可比: {'; '.join(diff.incomparable_reasons[:2])}")

    # ── Quality Gate（QUALITY_GATE）──
    verdict = None
    if suite.quality_gate:
        gate_cfg = dict(suite.quality_gate)
        # 未指定 --baseline 时回归项不参与评估（无对比对象不等于违规）；
        # 指定了但缺失/不可比 → gate.py fail closed
        if not args.baseline and "regression" in gate_cfg:
            gate_cfg.pop("regression")
            print("[GATE] 未指定 --baseline，regression 项本次不评估")
        verdict = evaluate_gate(run, gate_cfg, diff, is_defect_suite=suite.is_defect_suite)
        run.gate = verdict.model_dump()
        if verdict.skipped:
            print(f"[GATE] 跳过: {verdict.skip_reason}")
        else:
            print(f"[GATE] {'✅ PASS' if verdict.passed else '❌ FAIL'}"
                  + ("" if verdict.passed else f"（{len(verdict.violations)} 项违规）"))
            for v in verdict.violations:
                print(f"  ✗ {v.section}.{v.metric} {v.expr}，实际 {v.actual}：{v.detail}")

    # ── 报告（GENERATE_REPORT）──
    run_path = write_run_json(run, out_dir)
    write_summary_md(run, out_dir, diff=diff, gate=verdict)
    write_junit_xml(run, out_dir)

    # ── 保存 baseline（显式动作）──
    if args.save_baseline:
        bp = save_baseline(run, baselines_dir, args.save_baseline)
        print(f"[BASELINE] 已保存: {bp}")

    s = run.summary
    print(
        f"[{final_state}] total={s.total} passed={s.passed} failed={s.failed} "
        f"error={s.error} pass_rate={s.pass_rate:.1%}"
    )
    print(f"[REPORT] {run_path}")

    return decide_exit_code(final_state, None if verdict is None or verdict.skipped else verdict.passed, s)


def _validate(suite_path: Path) -> int:
    try:
        suite, dataset, _ = load_suite(suite_path, known_evaluators=known_evaluators())
    except AssetError as e:
        print(f"[CONFIG] {e}", file=sys.stderr)
        return 3
    print(
        f"[OK] suite={suite.id} cases={len(suite.cases)} "
        f"dataset={dataset.id}@{dataset.version} docs={len(dataset.documents)}"
    )
    return 0


def _baseline_update(args: argparse.Namespace) -> int:
    """从既有 run.json 保存 baseline（CI 主分支动作）。"""
    try:
        suite, _, _ = load_suite(args.suite, known_evaluators=known_evaluators())
    except AssetError as e:
        print(f"[CONFIG] {e}", file=sys.stderr)
        return 3
    run_path = Path(args.run) / "run.json"
    if not run_path.exists():
        print(f"[CONFIG] run.json 不存在: {run_path}", file=sys.stderr)
        return 3
    run = TestRun.model_validate(json.loads(run_path.read_text(encoding="utf-8")))
    if run.suite.get("id") != suite.id:
        print(f"[CONFIG] run 的 suite={run.suite.get('id')} 与 --suite {suite.id} 不一致", file=sys.stderr)
        return 3
    baselines_dir = (args.artifacts_dir or load_settings().artifacts_dir) / "baselines"
    bp = save_baseline(run, baselines_dir, args.name)
    print(f"[BASELINE] 已保存: {bp}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="ragtest", description="RAG 自动化测试引擎")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_run = sub.add_parser("run", help="运行 golden suite")
    p_run.add_argument("--suite", required=True, type=Path)
    p_run.add_argument("--run-id", default=None, help="指定 run_id（默认时间戳生成；DSH host 调用时传入）")
    p_run.add_argument("--artifacts-dir", type=Path, default=None)
    p_run.add_argument("--ingest-timeout", type=float, default=600.0)
    p_run.add_argument("--baseline", default=None, help="对比的 baseline 名（如 main）")
    p_run.add_argument("--save-baseline", default=None, help="运行后保存为 baseline（如 main）")

    p_val = sub.add_parser("validate", help="校验 suite/dataset 资产")
    p_val.add_argument("--suite", required=True, type=Path)

    p_bu = sub.add_parser("baseline-update", help="从既有 run 保存 baseline")
    p_bu.add_argument("--suite", required=True, type=Path)
    p_bu.add_argument("--name", required=True)
    p_bu.add_argument("--run", required=True, type=Path, help="run 目录（含 run.json）")
    p_bu.add_argument("--artifacts-dir", type=Path, default=None)

    args = parser.parse_args()
    match args.cmd:
        case "run":
            return asyncio.run(_run(args))
        case "validate":
            return _validate(args.suite)
        case "baseline-update":
            return _baseline_update(args)
    return 3


if __name__ == "__main__":
    sys.exit(main())
