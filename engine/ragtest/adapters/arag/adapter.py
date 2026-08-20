"""AragAdapter：arag 知识库系统的 Provisioning + Retrieval Port 实现。

API 事实依据（架构 v0.3 §2）：
- 统一信封 `ApiResponse{code, message, data}`，code >= 400 为错误
- auth:  `POST {auth}/auth/v1/login|register|token/refresh`
- app:   `POST {app}/api/v1/knowledge-bases`、`.../{id}/documents`（multipart 字段名 file）、
         `.../documents/{doc_id}`、`.../{id}/search`
- 状态 JSON 中文序列化（见 status_map.py）
- 分级超时（评审 P0-2）：本 adapter 单请求默认 30s；就绪等待由 PollPolicy 控制
"""

from __future__ import annotations

import asyncio
import hashlib
import mimetypes
import time
from pathlib import Path
from typing import Any

import httpx

from ragtest.adapters.arag.status_map import normalize_document_status
from ragtest.adapters.base import (
    AdapterError,
    Capability,
    DocumentAsset,
    DocumentHandle,
    DocumentStatusInfo,
    ErrorKind,
    Identity,
    IngestFailed,
    KBHandle,
    KBSpec,
    PollPolicy,
    RetrievalQuery,
    RetrievalResult,
    RetrievedChunk,
    RunLease,
    Session,
)
from ragtest.models import NormalizedDocStatus
from ragtest.runner.polling import poll_until

# 单请求默认超时（search/auth/KB CRUD 档，评审 P0-2 分级表）
_REQUEST_TIMEOUT_S = 30.0
# GET 幂等重试（429/5xx/传输错误）
_GET_MAX_RETRIES = 3


class AragAdapter:
    """arag 适配器。用法：`async with AragAdapter(base_url, auth_url) as ad: ...`"""

    name = "arag"

    def __init__(self, base_url: str, auth_url: str):
        self._app = base_url.rstrip("/")
        self._auth = auth_url.rstrip("/")
        # trust_env=False：忽略环境代理（测试工具要求确定性网络，被测系统通常在 localhost/内网）
        self._client = httpx.AsyncClient(timeout=_REQUEST_TIMEOUT_S, trust_env=False)

    async def __aenter__(self) -> AragAdapter:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    def capabilities(self) -> set[Capability]:
        return {
            Capability.KB_PROVISIONING,
            Capability.RETRIEVAL,
            Capability.PERMISSION,
            Capability.RESUMABLE_UPLOAD,
        }

    # ── HTTP 基础 ────────────────────────────────────────────────────────

    async def _api(
        self,
        method: str,
        path: str,
        *,
        auth: bool = False,
        session: Session | None = None,
        json: Any = None,
        timeout: float | None = None,
        **kw: Any,
    ) -> Any:
        """统一请求 + 信封解包。返回信封 data 字段（可能为 None）。
        timeout=None 用客户端默认（30s 档）；上传等大请求显式传更久（分级超时 P0-2）。"""
        url = (self._auth if auth else self._app) + path
        headers: dict[str, str] = dict(kw.pop("headers", {}) or {})
        if session is not None:
            headers["Authorization"] = f"Bearer {session.token}"
        if timeout is not None:
            kw["timeout"] = timeout

        attempts = _GET_MAX_RETRIES if method == "GET" else 1
        last_exc: Exception | None = None
        for attempt in range(attempts):
            try:
                resp = await self._client.request(
                    method, url, headers=headers, json=json, **kw
                )
            except httpx.TimeoutException as e:
                raise AdapterError(ErrorKind.TIMEOUT, f"请求超时 {method} {path}") from e
            except httpx.TransportError as e:
                last_exc = e
                if attempt + 1 < attempts:
                    await asyncio.sleep(0.5 * (2**attempt))
                    continue
                raise AdapterError(ErrorKind.SERVER, f"连接失败 {method} {path}: {e}") from e

            body: dict[str, Any]
            try:
                body = resp.json()
            except ValueError:
                raise AdapterError(
                    ErrorKind.SERVER,
                    f"非 JSON 响应 {method} {path} (HTTP {resp.status_code})",
                    raw=resp.text[:500],
                )

            code = body.get("code", resp.status_code)
            if isinstance(code, int) and code < 400:
                return body.get("data")

            kind = _map_error_kind(code if isinstance(code, int) else resp.status_code)
            message = str(body.get("message") or f"HTTP {resp.status_code}")
            # 幂等 GET 对限流/服务端错误重试
            if method == "GET" and kind in (ErrorKind.RATE_LIMIT, ErrorKind.SERVER) and attempt + 1 < attempts:
                await asyncio.sleep(0.5 * (2**attempt))
                continue
            raise AdapterError(kind, f"{method} {path}: {message}", raw=body)

        raise AdapterError(ErrorKind.SERVER, f"请求失败 {method} {path}: {last_exc}")

    # ── 认证与身份（ProvisioningPort）─────────────────────────────────────

    async def login(self, identity: Identity) -> Session:
        data = await self._api(
            "POST",
            "/auth/v1/login",
            auth=True,
            json={"email": identity.email, "password": identity.password},
        )
        user = data.get("user") or {}
        session = Session(token=data["token"], user_id=user.get("id", ""), identity=identity)
        # 身份绑定回写（评审 P0-1）
        identity.arag_user_id = session.user_id
        identity.agent_uid = session.user_id
        return session

    async def ensure_user(self, session: Session, identity: Identity) -> Identity:
        """注册测试用户；已存在（400 + 已注册）则改为登录。回写 arag_user_id。"""
        assert identity.email and identity.password, "测试用户必须提供 email/password"
        try:
            await self._api(
                "POST",
                "/auth/v1/register",
                auth=True,
                json={
                    "email": identity.email,
                    "password": identity.password,
                    "nickname": identity.logical_name,
                },
            )
        except AdapterError as e:
            if e.kind is ErrorKind.VALIDATION and "已注册" in e.message:
                pass  # 幂等：已存在则直接登录
            else:
                raise
        await self.login(identity)  # login 内部回写 arag_user_id / agent_uid
        return identity

    # ── 知识库 ───────────────────────────────────────────────────────────

    async def create_knowledge_base(self, session: Session, spec: KBSpec) -> KBHandle:
        data = await self._api(
            "POST",
            "/api/v1/knowledge-bases",
            session=session,
            json={"name": spec.name, "description": spec.description, "kb_type": spec.kb_type},
        )
        return KBHandle(kb_id=data["id"], name=spec.name)

    async def delete_knowledge_base(self, session: Session, kb: KBHandle) -> None:
        try:
            await self._api("DELETE", f"/api/v1/knowledge-bases/{kb.kb_id}", session=session)
        except AdapterError as e:
            if e.kind is not ErrorKind.NOT_FOUND:
                raise

    # ── 文档 ─────────────────────────────────────────────────────────────

    async def upload_document(
        self, session: Session, kb: KBHandle, doc: DocumentAsset
    ) -> DocumentHandle:
        path = Path(doc.path)
        content = path.read_bytes()
        mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        # 上传档超时（P0-2 分级）：随文件大小伸缩，下限 120s、上限 600s
        upload_timeout = min(600.0, max(120.0, len(content) / 1024 / 1024 * 10))
        data = await self._api(
            "POST",
            f"/api/v1/knowledge-bases/{kb.kb_id}/documents",
            session=session,
            files={"file": (path.name, content, mime)},
            data={"path": "/"},
            timeout=upload_timeout,
        )
        return DocumentHandle(
            doc_id=data["id"],
            filename=data.get("filename", path.name),
            logical_id=doc.logical_id,
            sha256=hashlib.sha256(content).hexdigest(),
        )

    async def get_document_status(
        self, session: Session, kb: KBHandle, doc: DocumentHandle
    ) -> DocumentStatusInfo:
        data = await self._api(
            "GET",
            f"/api/v1/knowledge-bases/{kb.kb_id}/documents/{doc.doc_id}",
            session=session,
        )
        raw_status = str(data.get("status") or "")
        return DocumentStatusInfo(
            status=normalize_document_status(raw_status),
            progress=data.get("ingest_progress"),
            error_message=data.get("error_message"),
            chunk_count=data.get("chunk_count"),
            raw_status=raw_status,
            raw=data,
        )

    async def wait_until_ready(
        self,
        session: Session,
        kb: KBHandle,
        doc: DocumentHandle,
        *,
        poll: PollPolicy = PollPolicy(),
    ) -> DocumentStatusInfo:
        """backoff 轮询直到 READY；FAILED 抛 IngestFailed（带 error_message）；超时抛 WaitTimeout。"""

        async def probe() -> DocumentStatusInfo | None:
            info = await self.get_document_status(session, kb, doc)
            if info.status is NormalizedDocStatus.READY:
                return info
            if info.status is NormalizedDocStatus.FAILED:
                raise IngestFailed(
                    f"文档摄入失败 {doc.filename}: {info.error_message or '未知错误'}",
                    raw=info.raw,
                )
            return None

        return await poll_until(probe, policy=poll, describe=f"文档 {doc.filename}")

    async def delete_document(
        self, session: Session, kb: KBHandle, doc: DocumentHandle
    ) -> None:
        try:
            await self._api(
                "DELETE",
                f"/api/v1/knowledge-bases/{kb.kb_id}/documents/{doc.doc_id}",
                session=session,
            )
        except AdapterError as e:
            if e.kind is not ErrorKind.NOT_FOUND:
                raise

    # ── 权限 ─────────────────────────────────────────────────────────────

    async def grant_permission(
        self, session: Session, kb: KBHandle, target: Identity, level: str
    ) -> None:
        assert target.arag_user_id, "授权目标缺少 arag_user_id（需先 ensure_user/login）"
        await self._api(
            "POST",
            f"/api/v1/knowledge-bases/{kb.kb_id}/permissions",
            session=session,
            json={"user_id": target.arag_user_id, "level": level},
        )

    # ── 检索（RetrievalPort）──────────────────────────────────────────────

    async def retrieve(
        self, session: Session, kb: KBHandle, query: RetrievalQuery
    ) -> RetrievalResult:
        started = time.perf_counter()
        body = {
            "query": query.query,
            "top_k": query.top_k,
            "score_threshold": query.score_threshold,
            **query.extra_body,  # 缺陷探针：透传 schema 外字段（如 vector_similarity_weight）
        }
        data = await self._api(
            "POST",
            f"/api/v1/knowledge-bases/{kb.kb_id}/search",
            session=session,
            json=body,
        )
        latency_ms = int((time.perf_counter() - started) * 1000)
        items = data.get("items") or []
        chunks = [
            RetrievedChunk(
                chunk_id=str(item.get("chunk_id") or ""),
                document_id=str(item.get("document_id") or ""),
                score=float(item.get("score") or 0.0),
                rank=idx + 1,  # 响应数组序即 rank（无独立 rank 字段）
                content=item.get("text_content"),
                kb_id=item.get("kb_id"),
                metadata={
                    "document_name": item.get("document_name"),
                    "breadcrumb": item.get("text_breadcrumb"),
                    "chunk_index": item.get("text_chunk_index"),
                    "chunk_source": item.get("text_chunk_source"),
                    "first_page": item.get("text_first_page"),
                    "last_page": item.get("text_last_page"),
                    "embedding_model": item.get("embedding_model"),
                },
            )
            for idx, item in enumerate(items)
        ]
        return RetrievalResult(
            chunks=chunks,
            degraded=bool(data.get("degraded")),
            degraded_reason=data.get("degraded_reason"),
            latency_ms=latency_ms,
            raw=data,
        )

    # ── 清理（RunLease 逆序，评审 P1-2）────────────────────────────────────

    async def delete_user(self, session: Session, identity: Identity) -> None:
        """删除测试用户账号（auth admin API：`DELETE /auth/v1/admin/users/{id}`）。
        注意区别于 app 侧 `DELETE /api/v1/admin/users/{id}`——后者删的是用户 KB 数据而非账号
        （arag-app/src/kb/handlers.rs:437 delete_kb_user_impl）。404 幂等忽略。"""
        if not identity.arag_user_id:
            return
        try:
            await self._api(
                "DELETE", f"/auth/v1/admin/users/{identity.arag_user_id}",
                auth=True, session=session,
            )
        except AdapterError as e:
            if e.kind is not ErrorKind.NOT_FOUND:
                raise

    async def cleanup(self, session: Session, lease: RunLease) -> None:
        """按逆序释放资源：documents → knowledge_bases → users。
        单资源失败不阻断后续清理（残留由调用方记录）。
        文档删除是异步的（Deleting→Deleted），KB 在文档未删净时拒绝删除
        （400 存在处理中的文档）→ KB 删除做短 backoff 重试。"""
        for kb, doc in reversed(lease.documents):
            try:
                await self.delete_document(session, kb, doc)
            except AdapterError:
                pass
        for kb in reversed(lease.knowledge_bases):
            await self._delete_kb_with_retry(session, kb)
        for identity in reversed(lease.users):
            try:
                await self.delete_user(session, identity)
            except AdapterError:
                pass

    async def _delete_kb_with_retry(self, session: Session, kb: KBHandle) -> None:
        interval = 1.0
        for _ in range(6):  # ~31s 总窗口（1+2+4+8+8+8）
            try:
                await self.delete_knowledge_base(session, kb)
                return
            except AdapterError as e:
                if e.kind is ErrorKind.NOT_FOUND:
                    return
                # 文档删除未完成的暂态拒绝 → backoff 重试；其余错误上抛
                if not (e.kind is ErrorKind.VALIDATION and "处理中" in e.message):
                    raise
                await asyncio.sleep(interval)
                interval = min(interval * 2, 8.0)


def _map_error_kind(code: int) -> ErrorKind:
    if code == 400:
        return ErrorKind.VALIDATION
    if code in (401, 403):
        return ErrorKind.AUTH
    if code == 404:
        return ErrorKind.NOT_FOUND
    if code == 409:
        return ErrorKind.CONFLICT
    if code == 429:
        return ErrorKind.RATE_LIMIT
    if code >= 500:
        return ErrorKind.SERVER
    return ErrorKind.SERVER
