"""arag DocumentStatus 中英双语 → NormalizedDocStatus 映射（评审 P1-7）。

事实依据（`arag-app/src/entities/document.rs`）：
- API JSON 序列化为中文（serde rename）：就绪 / 失败 / 索引中 …
- FromStr 兼容英文，Display 为英文
契约测试锁定：线上 JSON 状态值必须是中文。
"""

from __future__ import annotations

from ragtest.models import NormalizedDocStatus

_STATUS_MAP: dict[str, NormalizedDocStatus] = {
    # 中文（线上 JSON 形态）
    "待上传": NormalizedDocStatus.PENDING,
    "已上传": NormalizedDocStatus.PENDING,
    "索引中": NormalizedDocStatus.PROCESSING,
    "就绪": NormalizedDocStatus.READY,
    "失败": NormalizedDocStatus.FAILED,
    "删除中": NormalizedDocStatus.PROCESSING,
    "已删除": NormalizedDocStatus.DELETED,
    # 英文（FromStr/Display 兼容形态）
    "awaiting_upload": NormalizedDocStatus.PENDING,
    "awaitingupload": NormalizedDocStatus.PENDING,
    "uploaded": NormalizedDocStatus.PENDING,
    "indexing": NormalizedDocStatus.PROCESSING,
    "ready": NormalizedDocStatus.READY,
    "failed": NormalizedDocStatus.FAILED,
    "deleting": NormalizedDocStatus.PROCESSING,
    "deleted": NormalizedDocStatus.DELETED,
}


def normalize_document_status(raw: str | None) -> NormalizedDocStatus:
    """中英双收；空值与未识别值 → UNKNOWN（不猜）。"""
    if not raw:
        return NormalizedDocStatus.UNKNOWN
    return _STATUS_MAP.get(raw.strip().lower() if raw.isascii() else raw.strip(),
                           _STATUS_MAP.get(raw.strip(), NormalizedDocStatus.UNKNOWN))
