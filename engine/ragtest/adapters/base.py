"""Adapter 契约层：四个可组合 Port + 归一化类型（架构 v0.3 §6.1，评审 P0-4）。

依赖规则：本模块只依赖 ragtest.models；runner 只允许 import 本模块，
不得 import 具体 adapter。
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from ragtest.models import NormalizedDocStatus


# ── 能力集 ────────────────────────────────────────────────────────────────


class Capability(enum.Enum):
    KB_PROVISIONING = "kb_provisioning"  # 建库/上传/删除
    RETRIEVAL = "retrieval"
    CHAT = "chat"
    AGENT_TOOL_TRACE = "agent_tool_trace"
    PERMISSION = "permission"
    RESUMABLE_UPLOAD = "resumable_upload"
    METADATA_FILTER = "metadata_filter"


# ── 错误模型 ──────────────────────────────────────────────────────────────


class ErrorKind(enum.Enum):
    AUTH = "auth"
    NOT_FOUND = "not_found"
    CONFLICT = "conflict"
    VALIDATION = "validation"
    RATE_LIMIT = "rate_limit"
    SERVER = "server"
    TIMEOUT = "timeout"
    CAPABILITY = "capability"


class AdapterError(Exception):
    """归一化 Adapter 错误。kind 供 expect_error 断言与 runner 分支使用。"""

    def __init__(self, kind: ErrorKind, message: str, *, raw: Any = None):
        super().__init__(f"[{kind.value}] {message}")
        self.kind = kind
        self.message = message
        self.raw = raw


class WaitTimeout(AdapterError):
    def __init__(self, message: str, *, raw: Any = None):
        super().__init__(ErrorKind.TIMEOUT, message, raw=raw)


class IngestFailed(AdapterError):
    """文档摄入进入终态失败（携带被测系统 error_message）。"""

    def __init__(self, message: str, *, raw: Any = None):
        super().__init__(ErrorKind.SERVER, message, raw=raw)


class CapabilityNotSupported(AdapterError):
    def __init__(self, what: str):
        super().__init__(ErrorKind.CAPABILITY, f"capability not supported: {what}")


# ── 轮询策略（分级超时的一部分；禁止固定 sleep）────────────────────────────


@dataclass(frozen=True)
class PollPolicy:
    initial_s: float = 1.0
    factor: float = 2.0
    max_interval_s: float = 10.0
    timeout_s: float = 300.0


# ── 身份绑定（评审 P0-1：跨 adapter 共享）──────────────────────────────────


@dataclass
class Identity:
    """跨 adapter 共享身份。provisioning 建/查用户后回写 arag_user_id；
    target adapter 必须用同一 UUID 注入身份（agent X-USER-ID = arag user_id）。"""

    logical_name: str
    role: str = "user"
    email: str | None = None
    password: str | None = None
    arag_user_id: str | None = None  # provisioning 回写
    agent_uid: str | None = None     # = arag_user_id（target.login 使用）


@dataclass
class Session:
    token: str
    user_id: str
    identity: Identity


# ── 资源句柄 ──────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class KBSpec:
    name: str
    description: str = ""
    kb_type: str = "personal"


@dataclass(frozen=True)
class KBHandle:
    kb_id: str
    name: str


@dataclass(frozen=True)
class DocumentAsset:
    """数据集文档资产（logical_id 供 golden set 引用，与运行时 doc_id 解耦）。"""

    path: Path
    logical_id: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class DocumentHandle:
    doc_id: str
    filename: str
    logical_id: str | None = None
    sha256: str | None = None


@dataclass(frozen=True)
class DocumentStatusInfo:
    status: NormalizedDocStatus
    progress: int | None = None        # 0-100
    error_message: str | None = None
    chunk_count: int | None = None
    raw_status: str = ""               # 被测系统原始状态串（如 "就绪"）
    raw: dict[str, Any] = field(default_factory=dict)


# ── 检索 ──────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class RetrievalQuery:
    query: str
    top_k: int = 5
    score_threshold: float = 0.0
    extra_body: dict[str, Any] = field(default_factory=dict)  # 透传额外字段（缺陷探针）


@dataclass(frozen=True)
class RetrievedChunk:
    chunk_id: str
    document_id: str
    score: float
    rank: int                        # 响应数组序（1 起）
    content: str | None = None
    kb_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RetrievalResult:
    chunks: list[RetrievedChunk]
    degraded: bool = False
    degraded_reason: str | None = None
    latency_ms: int = 0
    raw: dict[str, Any] = field(default_factory=dict)


# ── Chat / Trace（M4 起用；M0 仅定义契约）──────────────────────────────────


@dataclass(frozen=True)
class ChatRequest:
    question: str
    skill: str | None = "xj-kbase"
    kb_id_inject: str | None = None  # 注入确定性检索指令（评审 P0-3）
    model: str | None = None
    timeout_s: int = 300             # chat 分级超时（评审 P0-2）
    stream: bool = False


@dataclass(frozen=True)
class ChatResult:
    """评审 P1-6：不含 citations 结构（citation 由 evaluator 从文本抽取）。"""

    answer: str
    usage: dict[str, Any] | None = None
    latency_ms: int = 0
    ttft_ms: int | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ChatSession:
    session_id: str
    identity: Identity


@dataclass(frozen=True)
class AgentToolCall:
    tool: str
    args: dict[str, Any]
    chunk_count: int | None = None
    hit_doc_ids: list[str] = field(default_factory=list)
    duration_ms: int | None = None
    is_error: bool = False
    source: str = "chat_response"    # chat_response | sse | pg


# ── RunLease（评审 P1-2：资源清单，CLEANUP 逆序执行）───────────────────────


@dataclass
class RunLease:
    """一次 run 创建的全部资源，cleanup 按逆序释放。"""

    run_id: str
    documents: list[tuple[KBHandle, DocumentHandle]] = field(default_factory=list)
    knowledge_bases: list[KBHandle] = field(default_factory=list)
    users: list[Identity] = field(default_factory=list)
    agent_sessions: list[ChatSession] = field(default_factory=list)


# ── 四个 Port ─────────────────────────────────────────────────────────────


class ProvisioningPort(Protocol):
    """建库 / 上传 / 授权 / 删除 / 清理（arag ✅ / xuanjian ❌）。"""

    def capabilities(self) -> set[Capability]: ...
    async def login(self, identity: Identity) -> Session: ...
    async def ensure_user(self, session: Session, identity: Identity) -> Identity: ...
    async def create_knowledge_base(self, session: Session, spec: KBSpec) -> KBHandle: ...
    async def upload_document(
        self, session: Session, kb: KBHandle, doc: DocumentAsset
    ) -> DocumentHandle: ...
    async def get_document_status(
        self, session: Session, kb: KBHandle, doc: DocumentHandle
    ) -> DocumentStatusInfo: ...
    async def wait_until_ready(
        self,
        session: Session,
        kb: KBHandle,
        doc: DocumentHandle,
        *,
        poll: PollPolicy = PollPolicy(),
    ) -> DocumentStatusInfo: ...
    async def grant_permission(
        self, session: Session, kb: KBHandle, target: Identity, level: str
    ) -> None: ...
    async def delete_document(self, session: Session, kb: KBHandle, doc: DocumentHandle) -> None: ...
    async def delete_knowledge_base(self, session: Session, kb: KBHandle) -> None: ...
    async def cleanup(self, session: Session, lease: RunLease) -> None: ...


class RetrievalPort(Protocol):
    async def retrieve(
        self, session: Session, kb: KBHandle, query: RetrievalQuery
    ) -> RetrievalResult: ...


class ChatPort(Protocol):
    async def create_session(self, identity: Identity) -> ChatSession: ...
    async def chat(self, session: ChatSession, request: ChatRequest) -> ChatResult: ...
    async def delete_session(self, session: ChatSession) -> None: ...


class TracePort(Protocol):
    async def collect_agent_trace(
        self, session: ChatSession, result: ChatResult
    ) -> list[AgentToolCall]: ...
