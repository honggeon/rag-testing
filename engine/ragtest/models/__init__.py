"""共享枚举与基础类型（架构 v0.3 §6/§12）。"""

from __future__ import annotations

import enum


class NormalizedDocStatus(enum.Enum):
    """归一化文档状态（adapter 把被测系统的原生状态映射到本枚举）。

    arag 业务层状态（JSON 中文序列化，adapter 中英双收）：
    待上传/已上传 → PENDING；索引中 → PROCESSING；就绪 → READY；
    失败 → FAILED；删除中/已删除 → DELETED 相关。
    """

    PENDING = "pending"
    PROCESSING = "processing"
    READY = "ready"
    FAILED = "failed"
    DELETED = "deleted"
    UNKNOWN = "unknown"


class RunState(enum.Enum):
    """运行生命周期状态（架构 §12.2 状态表）。"""

    INIT = "INIT"
    LOAD_SUITE = "LOAD_SUITE"
    LOGIN = "LOGIN"
    CREATE_KB = "CREATE_KB"
    UPLOAD_DOCUMENTS = "UPLOAD_DOCUMENTS"
    WAIT_INDEX_READY = "WAIT_INDEX_READY"
    RUN_TEST_CASES = "RUN_TEST_CASES"
    EVALUATE = "EVALUATE"
    COMPARE_BASELINE = "COMPARE_BASELINE"
    QUALITY_GATE = "QUALITY_GATE"
    GENERATE_REPORT = "GENERATE_REPORT"
    CLEANUP = "CLEANUP"
    DONE = "DONE"
    # 异常终态
    PARTIAL = "PARTIAL"
    ERROR = "ERROR"
    TIMEOUT = "TIMEOUT"
    CANCELLED = "CANCELLED"


TERMINAL_STATES: frozenset[RunState] = frozenset(
    {RunState.DONE, RunState.PARTIAL, RunState.ERROR, RunState.TIMEOUT, RunState.CANCELLED}
)
