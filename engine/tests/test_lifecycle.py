"""生命周期状态机测试（FakeAdapter 内存实现，验证状态迁移 / 清理 / 取消）。"""

from pathlib import Path

import pytest
import yaml

from ragtest.adapters.base import (
    AdapterError,
    DocumentHandle,
    DocumentStatusInfo,
    ErrorKind,
    Identity,
    KBHandle,
    RetrievalResult,
    RetrievedChunk,
    Session,
)
from ragtest.artifacts import RunStatusWriter
from ragtest.assets import load_suite
from ragtest.evaluators import known_evaluators
from ragtest.models import NormalizedDocStatus, RunState
from ragtest.runner.lifecycle import SuiteRunner


class FakeAdapter:
    """内存 adapter：记录调用序列，可注入故障。"""

    name = "fake"

    def __init__(self):
        self.calls: list[str] = []
        self.fail_at: str | None = None
        self._uid = 0

    def _maybe_fail(self, step: str):
        self.calls.append(step)
        if self.fail_at == step:
            raise AdapterError(ErrorKind.SERVER, f"注入故障: {step}")

    async def login(self, identity: Identity) -> Session:
        self.calls.append(f"login:{identity.logical_name}")
        identity.arag_user_id = identity.arag_user_id or f"u-{identity.logical_name}"
        identity.agent_uid = identity.arag_user_id
        return Session(token="t", user_id=identity.arag_user_id, identity=identity)

    async def ensure_user(self, session: Session, identity: Identity) -> Identity:
        self._uid += 1
        self.calls.append(f"ensure_user:{identity.logical_name}")
        identity.arag_user_id = f"u-new-{self._uid}"
        identity.agent_uid = identity.arag_user_id
        return identity

    async def create_knowledge_base(self, session, spec) -> KBHandle:
        self._maybe_fail("create_kb")
        return KBHandle(kb_id="kb-1", name=spec.name)

    async def grant_permission(self, session, kb, target, level) -> None:
        self.calls.append(f"grant:{target.logical_name}:{level}")

    async def upload_document(self, session, kb, doc) -> DocumentHandle:
        self._maybe_fail("upload")
        h = DocumentHandle(doc_id=f"doc-{doc.logical_id}", filename=doc.path.name,
                           logical_id=doc.logical_id, sha256="x")
        return h

    async def get_document_status(self, session, kb, doc) -> DocumentStatusInfo:
        return DocumentStatusInfo(status=NormalizedDocStatus.READY, progress=100, chunk_count=2)

    async def wait_until_ready(self, session, kb, doc, *, poll) -> DocumentStatusInfo:
        self._maybe_fail("wait_ready")
        return DocumentStatusInfo(status=NormalizedDocStatus.READY, progress=100, chunk_count=2)

    async def retrieve(self, session, kb, query) -> RetrievalResult:
        self.calls.append(f"retrieve:{session.identity.logical_name}:{query.query[:10]}")
        if not query.query:
            raise AdapterError(ErrorKind.VALIDATION, "query 不能为空")
        # 越权模拟：outsider 无权限 → 空结果；其他身份 → 命中同名文档
        if session.identity.logical_name == "outsider":
            return RetrievalResult(chunks=[], latency_ms=5)
        hits = [
            RetrievedChunk(
                chunk_id="c1",
                document_id="doc-doc_copolymer",
                score=0.9,
                rank=1,
                content="共聚物内容",
            )
        ] if "共聚物" in query.query else []
        return RetrievalResult(chunks=hits, latency_ms=42)

    async def delete_document(self, session, kb, doc) -> None:
        self.calls.append(f"del_doc:{doc.doc_id}")

    async def delete_knowledge_base(self, session, kb) -> None:
        self.calls.append(f"del_kb:{kb.kb_id}")

    async def delete_user(self, session, identity) -> None:
        self.calls.append(f"del_user:{identity.logical_name}")

    async def cleanup_resources(self, session, lease) -> list:
        self.calls.append("cleanup:start")
        for kb, doc in reversed(lease.documents):
            await self.delete_document(session, kb, doc)
        for kb in reversed(lease.knowledge_bases):
            await self.delete_knowledge_base(session, kb)
        return []

    async def cleanup_users(self, session, lease) -> list:
        for identity in reversed(lease.users):
            await self.delete_user(session, identity)
        self.calls.append("cleanup:end")
        return []

    async def cleanup(self, session, lease) -> list:
        await self.cleanup_resources(session, lease)
        await self.cleanup_users(session, lease)
        return []


def make_suite(tmp_path: Path, cases: list[dict]) -> tuple:
    doc_dir = tmp_path / "datasets" / "basic" / "documents"
    doc_dir.mkdir(parents=True)
    (doc_dir / "a.md").write_text("# 共聚物", encoding="utf-8")
    (tmp_path / "datasets" / "basic" / "dataset.yaml").write_text(yaml.safe_dump({
        "schema_version": "1", "id": "ds", "version": "v1",
        "documents": [{"logical_id": "doc_copolymer", "path": "documents/a.md"}],
    }), encoding="utf-8")
    suite_path = tmp_path / "suite.yaml"
    suite_path.write_text(yaml.safe_dump({
        "schema_version": "1", "kind": "GoldenSuite", "id": "s1",
        "dataset": {"ref": "datasets/basic/dataset.yaml"},
        "identities": {
            "owner": {"role": "admin"},
            "reader": {"role": "user", "create": True, "grant_level": "read"},
            "outsider": {"role": "user", "create": True, "no_grant": True},
        },
        "cases": cases,
    }, allow_unicode=True), encoding="utf-8")
    return load_suite(suite_path, known_evaluators=known_evaluators())


CASES = [
    {"id": "c1", "identity": "reader", "input": {"query": "共聚物是什么"},
     "expected": {"documents": ["doc_copolymer"]},
     "evaluators": [{"name": "recall_at_k", "k": 5, "threshold": 1.0}]},
    {"id": "c2", "identity": "outsider", "input": {"query": "共聚物", "top_k": 10},
     "expected": {"documents": [], "forbidden_documents": ["doc_copolymer"]},
     "evaluators": [{"name": "permission_leak", "threshold": 0}]},
    {"id": "c3", "identity": "reader", "input": {"query": ""},
     "expect_error": {"kind": "validation"}},
]


def make_runner(tmp_path, adapter, cases=CASES):
    suite, dataset, base = make_suite(tmp_path, cases)
    writer = RunStatusWriter(tmp_path / "run", "test-run", total=len(cases))
    admin = Identity(logical_name="owner", role="admin", email="ops", password="pw")
    runner = SuiteRunner(
        suite=suite, dataset=dataset, dataset_base=base, adapter=adapter,
        admin_identity=admin, run_id="test-run", writer=writer, repo_root=tmp_path,
    )
    return runner


def states_of(run) -> list[str]:
    return [e.state for e in run.lifecycle]


async def test_happy_path_done(tmp_path):
    adapter = FakeAdapter()
    runner = make_runner(tmp_path, adapter)
    run = await runner.run_suite()

    assert writer_state(runner) == RunState.DONE.value
    assert run.summary.total == 3
    assert run.summary.passed == 3, [c.model_dump() for c in run.cases if c.status.value != "passed"]
    assert run.summary.pass_rate == 1.0

    # 生命周期顺序：SETUP → RUN → EVALUATE → REPORT → CLEANUP → DONE
    states = states_of(run)
    assert states[0] == RunState.LOGIN.value
    assert states.index(RunState.CREATE_KB.value) < states.index(RunState.UPLOAD_DOCUMENTS.value)
    assert states.index(RunState.WAIT_INDEX_READY.value) < states.index(RunState.RUN_TEST_CASES.value)
    assert states[-2] == RunState.CLEANUP.value

    # 授权：reader 被授予 read，outsider（no_grant）无授权调用
    assert "grant:reader:read" in adapter.calls
    assert not any(c.startswith("grant:outsider") for c in adapter.calls)

    # 清理逆序：文档 → KB → 用户
    cleanup_seq = adapter.calls[adapter.calls.index("cleanup:start"):]
    assert cleanup_seq.index("del_doc:doc-doc_copolymer") < cleanup_seq.index("del_kb:kb-1")
    assert cleanup_seq.index("del_kb:kb-1") < cleanup_seq.index("del_user:reader")
    assert "del_user:outsider" in cleanup_seq


async def test_error_at_upload_still_cleans_up(tmp_path):
    adapter = FakeAdapter()
    adapter.fail_at = "upload"
    runner = make_runner(tmp_path, adapter)
    run = await runner.run_suite()

    assert writer_state(runner) == RunState.ERROR.value
    states = states_of(run)
    assert RunState.CLEANUP.value in states  # finally 语义：ERROR 路径必达 CLEANUP
    assert "del_kb:kb-1" in adapter.calls   # 已建 KB 被回收


async def test_ingest_failure_maps_to_timeout(tmp_path):
    adapter = FakeAdapter()
    adapter.fail_at = "wait_ready"
    runner = make_runner(tmp_path, adapter)
    run = await runner.run_suite()
    assert writer_state(runner) == RunState.ERROR.value  # AdapterError(SERVER) → ERROR
    assert RunState.CLEANUP.value in states_of(run)


async def test_case_error_becomes_partial(tmp_path):
    bad_cases = CASES + [
        {"id": "c4", "identity": "ghost", "input": {"query": "x"},
         "expected": {"documents": []}, "evaluators": []},
    ]
    adapter = FakeAdapter()
    runner = make_runner(tmp_path, adapter, cases=bad_cases)
    run = await runner.run_suite()
    # identity 未定义 → case error → PARTIAL
    assert writer_state(runner) == RunState.PARTIAL.value
    assert run.summary.error == 1


def writer_state(runner) -> str:
    return runner.writer.status.state
