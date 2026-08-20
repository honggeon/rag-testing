"""ragtest CLI（架构 §12.1 exit code 语义）。

用法：
    uv run python -m ragtest.cli run --suite suites/golden/basic_retrieval.v1.yaml
    uv run python -m ragtest.cli validate --suite <path>

exit code：0=通过；1=质量未达标（M2 起为 gate 失败）；2=运行错误；3=配置/资产错误
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import signal
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ragtest.adapters.arag import AragAdapter
from ragtest.adapters.base import Identity
from ragtest.artifacts import RunStatusWriter
from ragtest.assets import AssetError, load_suite
from ragtest.config import load_settings
from ragtest.evaluators import known_evaluators
from ragtest.models import CaseStatus, RunState
from ragtest.report import write_run_json
from ragtest.runner.lifecycle import SuiteRunner

REPO_ROOT = Path(__file__).resolve().parents[2]


def _suite_checksum(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()[:16]


async def _run(suite_path: Path, artifacts_dir: Path | None, ingest_timeout_s: float) -> int:
    settings = load_settings()
    if not settings.arag_admin_password:
        print("[CONFIG] 缺少 RAGTEST_ARAG_ADMIN_PASSWORD", file=sys.stderr)
        return 3

    # LOAD_SUITE（含 schema/checksum/evaluator 名校验，fail fast）
    try:
        suite, dataset, dataset_base = load_suite(
            suite_path, known_evaluators=known_evaluators()
        )
    except AssetError as e:
        print(f"[CONFIG] {e}", file=sys.stderr)
        return 3

    run_id = time.strftime("%Y%m%d-%H%M%S") + f"-{hashlib.sha256(str(time.time_ns()).encode()).hexdigest()[:6]}"
    out_dir = (artifacts_dir or settings.artifacts_dir) / "runs" / run_id
    writer = RunStatusWriter(out_dir, run_id, total=len(suite.cases))
    print(f"[RUN] suite={suite.id} cases={len(suite.cases)} run_id={run_id}")
    print(f"[RUN] artifacts: {out_dir}")

    admin_identity = Identity(
        logical_name="owner",
        role="admin",
        email=settings.arag_admin_email,
        password=settings.arag_admin_password,
    )

    async with AragAdapter(settings.arag_base_url, settings.arag_auth_url) as adapter:
        runner = SuiteRunner(
            suite=suite,
            dataset=dataset,
            dataset_base=dataset_base,
            adapter=adapter,
            admin_identity=admin_identity,
            run_id=run_id,
            writer=writer,
            repo_root=REPO_ROOT,
            ingest_timeout_s=ingest_timeout_s,
        )
        # 取消协议：SIGINT/SIGTERM → CANCELLED → CLEANUP（契约 §5.1）
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, setattr, runner, "_cancelled", True)
            except NotImplementedError:  # Windows
                pass
        run = await runner.run_suite()

    run.suite["golden_checksum"] = _suite_checksum(suite_path)
    run_path = write_run_json(run, out_dir)

    summary = run.summary
    final_state = writer.status.state
    print(
        f"[{final_state}] total={summary.total} passed={summary.passed} "
        f"failed={summary.failed} error={summary.error} pass_rate={summary.pass_rate:.1%}"
    )
    for name, value in sorted(summary.metrics_avg.items()):
        print(f"  {name}: {value:.3f}")
    print(f"[REPORT] {run_path}")

    if final_state in (RunState.ERROR.value, RunState.TIMEOUT.value):
        return 2
    if final_state == RunState.CANCELLED.value:
        return 2
    # M1：无 gate，以 case 失败作为质量未达标信号（M2 切换为 gate 判定）
    return 1 if summary.failed or summary.error else 0


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


def main() -> int:
    parser = argparse.ArgumentParser(prog="ragtest", description="RAG 自动化测试引擎")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_run = sub.add_parser("run", help="运行 golden suite")
    p_run.add_argument("--suite", required=True, type=Path)
    p_run.add_argument("--artifacts-dir", type=Path, default=None)
    p_run.add_argument("--ingest-timeout", type=float, default=600.0,
                       help="文档就绪等待超时秒数（默认 600）")

    p_val = sub.add_parser("validate", help="校验 suite/dataset 资产")
    p_val.add_argument("--suite", required=True, type=Path)

    args = parser.parse_args()
    if args.cmd == "run":
        return asyncio.run(_run(args.suite, args.artifacts_dir, args.ingest_timeout))
    return _validate(args.suite)


if __name__ == "__main__":
    sys.exit(main())
