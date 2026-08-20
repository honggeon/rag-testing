"""Suite 生命周期运行器（架构 §12.2 状态机 + RunLease 清理，评审 P0-5/P1-2）。

INIT → LOAD_SUITE → LOGIN → CREATE_KB → UPLOAD_DOCUMENTS → WAIT_INDEX_READY
→ RUN_TEST_CASES → EVALUATE → GENERATE_REPORT → CLEANUP → DONE
异常路径：ERROR / TIMEOUT / CANCELLED / PARTIAL，CLEANUP finally 语义必达。
"""

from __future__ import annotations

import asyncio
import secrets
import subprocess
import time
from pathlib import Path

from ragtest.adapters.base import (
    AdapterError,
    DocumentAsset,
    Identity,
    KBSpec,
    PollPolicy,
    RunLease,
    Session,
)
from ragtest.artifacts import RunStatusWriter
from ragtest.models import CaseStatus, RunState
from ragtest.models.result import (
    DocumentRunInfo,
    LifecycleEntry,
    RunSummary,
    TestRun,
)
from ragtest.models.suite import Dataset, GoldenSuite
from ragtest.runner.executor import execute_case


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def _git_commit(cwd: Path) -> str | None:
    try:
        return subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=cwd, capture_output=True, text=True, timeout=5,
        ).stdout.strip() or None
    except Exception:  # noqa: BLE001
        return None


class SuiteRunner:
    """单 suite 运行器。M1：target=provisioning=arag（双 adapter 在 M4 引入）。"""

    def __init__(
        self,
        *,
        suite: GoldenSuite,
        dataset: Dataset,
        dataset_base: Path,
        adapter,                          # ProvisioningPort + RetrievalPort
        admin_identity: Identity,         # settings 提供的管理账号（ops/admin）
        run_id: str,
        writer: RunStatusWriter,
        repo_root: Path,
        ingest_timeout_s: float = 600.0,
        target_adapter=None,              # M4：ChatPort/TracePort（xuanjian）；None=纯检索
    ):
        self.suite = suite
        self.dataset = dataset
        self.dataset_base = dataset_base
        self.adapter = adapter
        self.admin_identity = admin_identity
        self.run_id = run_id
        self.writer = writer
        self.repo_root = repo_root
        self.ingest_timeout_s = ingest_timeout_s

        self.lease = RunLease(run_id=run_id)
        self.run = TestRun(run_id=run_id)
        self.doc_id_to_logical: dict[str, str] = {}
        self._cancelled = False
        self._heartbeat_task: asyncio.Task | None = None
        self._target_adapter = target_adapter
        self._sessions: dict[str, Session] = {}

    # ── 生命周期记录 ──────────────────────────────────────────────────────

    def _enter(self, state: RunState, *, done: int | None = None, detail: str = "") -> None:
        self.writer.update(state, done=done, message=detail or None)
        self.run.lifecycle.append(LifecycleEntry(state=state.value, at=_now(), detail=detail))

    # ── 主流程 ────────────────────────────────────────────────────────────

    async def run_suite(self) -> TestRun:
        started = time.perf_counter()
        self._heartbeat_task = asyncio.create_task(self.writer.heartbeat())
        final = RunState.ERROR
        try:
            await self._setup_and_run()
            error_cases = sum(1 for c in self.run.cases if c.status is CaseStatus.ERROR)
            final = RunState.PARTIAL if error_cases else RunState.DONE
        except asyncio.CancelledError:
            final = RunState.CANCELLED
            self.run.lifecycle.append(
                LifecycleEntry(state=RunState.CANCELLED.value, at=_now(), detail="收到取消信号"))
        except AdapterError as e:
            final = RunState.TIMEOUT if e.kind.value == "timeout" else RunState.ERROR
            self.run.lifecycle.append(
                LifecycleEntry(state=final.value, at=_now(), ok=False, detail=str(e)[:200]))
        except Exception as e:  # noqa: BLE001
            final = RunState.ERROR
            self.run.lifecycle.append(
                LifecycleEntry(state=RunState.ERROR.value, at=_now(), ok=False, detail=str(e)[:200]))
        finally:
            await self._cleanup()
            self._finalize(final, started)
            if self._heartbeat_task:
                self._heartbeat_task.cancel()
        return self.run

    async def _setup_and_run(self) -> None:
        suite = self.suite

        # LOGIN：admin + 各 identity（含动态建用户与 IdentityBinding 回写）
        # 注意：owner 也可以是 create: true 的测试用户（配额隔离场景，如 a100 的 ops 配额已满）
        self._enter(RunState.LOGIN)
        admin_session = await self.adapter.login(self.admin_identity)
        sessions: dict[str, Session] = {}
        for name, spec in suite.identities.items():
            if spec.role == "admin":
                sessions[name] = admin_session
                continue
            identity = Identity(
                logical_name=name,
                role=spec.role,
                email=f"ragtest-{self.run_id[-6:]}-{name}@test.local",
                password=f"Ragtest-{secrets.token_hex(4)}!",
            )
            await self.adapter.ensure_user(admin_session, identity)
            self.lease.users.append(identity)
            sessions[name] = await self.adapter.login(identity)
        self._sessions = sessions

        owner = sessions.get("owner", admin_session)

        # CREATE_KB
        self._enter(RunState.CREATE_KB)
        kb = await self.adapter.create_knowledge_base(
            owner, KBSpec(
                name=f"{suite.knowledge_base.name_prefix}-{self.run_id[-6:]}",
                description=f"ragtest suite={suite.id} run={self.run_id}",
                kb_type=suite.knowledge_base.kb_type,
            ))
        self.lease.knowledge_bases.append(kb)
        self.run.kb = {"kb_id": kb.kb_id, "name": kb.name, "documents": []}

        # 授权（no_grant 的 identity 跳过）
        for name, spec in suite.identities.items():
            if spec.no_grant or name == "owner" or spec.role == "admin":
                continue
            identity = next(u for u in self.lease.users if u.logical_name == name)
            await self.adapter.grant_permission(owner, kb, identity, spec.grant_level or "read")

        # UPLOAD_DOCUMENTS（metadata.upload_twice → 幂等探针：同文件上传两次）
        self._enter(RunState.UPLOAD_DOCUMENTS)
        handles = []
        for doc in self.dataset.documents:
            handle = await self.adapter.upload_document(
                owner, kb, DocumentAsset(
                    path=self.dataset_base / doc.path, logical_id=doc.logical_id,
                    metadata=doc.metadata))
            if doc.metadata.get("upload_twice"):
                dup = await self.adapter.upload_document(
                    owner, kb, DocumentAsset(
                        path=self.dataset_base / doc.path, logical_id=doc.logical_id,
                        metadata=doc.metadata))
                # 重复上传会产生新文档记录（服务端 resolve_duplicate_filename 改名），
                # 必须纳入 lease，否则 cleanup 漏删（真实泄漏事故 2026-08-20）
                if dup.doc_id != handle.doc_id:
                    self.lease.documents.append((kb, dup))
                    self.doc_id_to_logical[dup.doc_id] = doc.logical_id
                self.run.lifecycle.append(LifecycleEntry(
                    state=RunState.UPLOAD_DOCUMENTS.value, at=_now(),
                    detail=f"重复上传幂等探针 {doc.logical_id}: 第二次上传成功"
                           f"（{'同 doc_id 幂等' if dup.doc_id == handle.doc_id else '产生了新 doc_id'}）",
                ))
            handles.append((doc.logical_id, handle, doc))
            self.lease.documents.append((kb, handle))
            self.doc_id_to_logical[handle.doc_id] = doc.logical_id

        # WAIT_INDEX_READY（metadata.expect_ingest_failure → 预期摄入失败，如空文件）
        self._enter(RunState.WAIT_INDEX_READY)
        for logical_id, handle, doc in handles:
            t0 = time.perf_counter()
            expect_fail = bool(doc.metadata.get("expect_ingest_failure"))
            try:
                info = await self.adapter.wait_until_ready(
                    owner, kb, handle,
                    poll=PollPolicy(timeout_s=self.ingest_timeout_s))
                final_status = "ready_unexpected" if expect_fail else info.status.value
                chunk_count = info.chunk_count
                error_message = None
            except Exception as e:  # IngestFailed
                if not expect_fail:
                    raise
                final_status = "failed_expected"
                chunk_count = None
                error_message = str(e)[:120]
            self.run.kb["documents"].append(DocumentRunInfo(
                logical_id=logical_id,
                doc_id=handle.doc_id,
                filename=handle.filename,
                indexing_latency_ms=int((time.perf_counter() - t0) * 1000),
                final_status=final_status,
                chunk_count=chunk_count,
            ).model_dump())
            if error_message:
                self.run.lifecycle.append(LifecycleEntry(
                    state=RunState.WAIT_INDEX_READY.value, at=_now(),
                    detail=f"{logical_id} 按预期摄入失败（{final_status}）: {error_message}",
                ))

        # RUN_TEST_CASES
        self._enter(RunState.RUN_TEST_CASES, done=0)
        raw_dir = self.writer.run_dir / "raw"
        for idx, case in enumerate(suite.cases, start=1):
            if self._cancelled:
                raise asyncio.CancelledError
            self.writer.update(RunState.RUN_TEST_CASES, done=idx - 1, current_case=case.id)
            session = sessions.get(case.identity)
            if session is None:
                from ragtest.models.result import CaseError, CaseResult
                self.run.cases.append(CaseResult(
                    case_id=case.id, name=case.name, status=CaseStatus.ERROR,
                    identity=case.identity,
                    error=CaseError(kind="config", message=f"identity '{case.identity}' 未定义"),
                ))
                continue
            result = await execute_case(
                case, adapter=self.adapter, session=session, kb=kb,
                doc_id_to_logical=self.doc_id_to_logical, raw_dir=raw_dir,
                chat_adapter=self._target_adapter,
                target_config=self.suite.target_config)
            self.run.cases.append(result)

        # EVALUATE（指标已在 case 内计算，这里做汇总）
        self._enter(RunState.EVALUATE, done=len(suite.cases))
        self._summarize()

        # GENERATE_REPORT（run.json 由 cli 原子写；M2 补 summary.md/junit）
        self._enter(RunState.GENERATE_REPORT)

    # ── 汇总 / 收尾 ─────────────────────────────────────────────────────────

    def _summarize(self) -> None:
        cases = self.run.cases
        summary = RunSummary(total=len(cases))
        for c in cases:
            match c.status:
                case CaseStatus.PASSED:
                    summary.passed += 1
                case CaseStatus.FAILED:
                    summary.failed += 1
                case CaseStatus.ERROR:
                    summary.error += 1
                case CaseStatus.SKIPPED:
                    summary.skipped += 1
        counted = summary.passed + summary.failed
        summary.pass_rate = summary.passed / counted if counted else 0.0

        # 指标均值（跳过 skipped）
        buckets: dict[str, list[float]] = {}
        for c in cases:
            for m in c.metrics:
                if not m.skipped and m.value is not None:
                    buckets.setdefault(m.name, []).append(m.value)
        summary.metrics_avg = {k: sum(v) / len(v) for k, v in buckets.items()}

        latencies = [
            c.retrieval.latency_ms for c in cases if c.retrieval is not None
        ]
        if latencies:
            latencies.sort()
            summary.latency = {
                "search_p50_ms": _pct(latencies, 50),
                "search_p95_ms": _pct(latencies, 95),
            }
        doc_lat = [d["indexing_latency_ms"] for d in self.run.kb.get("documents", [])]
        if doc_lat:
            doc_lat.sort()
            summary.latency["indexing_p95_ms"] = _pct(doc_lat, 95)
        # E2E 生成耗时（M6：e2e_latency 进 summary.latency 供 gate 使用）
        e2e_lat = [c.generation.latency_ms for c in cases if c.generation is not None]
        if e2e_lat:
            e2e_lat.sort()
            summary.latency["e2e_latency"] = _pct(e2e_lat, 95)
        self.run.summary = summary

    async def _cleanup(self) -> None:
        """RunLease 逆序清理（评审 P1-2）：agent sessions → documents → KB → users。必达。
        会话选择：docs/KB 用属主会话（不能删他人个人 KB），users 用 admin 会话。"""
        self.writer.update(RunState.CLEANUP, message="清理测试资源")
        self.run.lifecycle.append(LifecycleEntry(state=RunState.CLEANUP.value, at=_now()))
        try:
            # agent 会话清理（M4 target adapter；失败不阻断）
            if self._target_adapter is not None:
                for chat_session in reversed(self.lease.agent_sessions):
                    try:
                        await self._target_adapter.delete_session(chat_session)
                    except Exception:  # noqa: BLE001
                        pass
            admin_session = await self.adapter.login(self.admin_identity)
            owner_session = getattr(self, "_sessions", {}).get("owner", admin_session)
            failures = await self.adapter.cleanup_resources(owner_session, self.lease)
            failures += await self.adapter.cleanup_users(admin_session, self.lease)
            if failures:
                self.run.lifecycle.append(LifecycleEntry(
                    state=RunState.CLEANUP.value, at=_now(), ok=False,
                    detail=f"清理不完整（残留 {len(failures)} 项）: " + "; ".join(failures)[:400]))
        except AdapterError as e:
            self.run.lifecycle.append(LifecycleEntry(
                state=RunState.CLEANUP.value, at=_now(), ok=False,
                detail=f"清理异常（残留见 lease）: {e}"[:200]))

    def _finalize(self, final: RunState, started: float) -> None:
        self.run.lifecycle.append(LifecycleEntry(state=final.value, at=_now()))
        self.run.suite = {
            "id": self.suite.id,
            "name": self.suite.name,
            "dataset_id": self.dataset.id,
            "dataset_version": self.dataset.version,
            "golden_checksum": "",  # M2 由 cli 回填
        }
        self.run.environment = {
            "adapter": {"name": getattr(self.adapter, "name", "unknown")},
            "fingerprint": {
                "retrieval": dict(self.suite.environment_fingerprint.retrieval),
                "generation": dict(self.suite.environment_fingerprint.generation),
            },
            "git_commit": _git_commit(self.repo_root),
            "started_at": self.run.lifecycle[0].at if self.run.lifecycle else _now(),
            "duration_ms": int((time.perf_counter() - started) * 1000),
        }
        self.writer.update(final, message=f"run 终态 {final.value}")


def _pct(sorted_values: list[float], pct: int) -> float:
    """最近秩百分位（输入已排序）。"""
    if not sorted_values:
        return 0.0
    import math

    rank = math.ceil(pct / 100 * len(sorted_values))
    return float(sorted_values[max(0, min(rank, len(sorted_values)) - 1)])
