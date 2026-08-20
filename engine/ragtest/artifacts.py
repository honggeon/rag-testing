"""artifacts 目录契约（架构 v0.3 §5.1，评审 P1-5）：DSH ↔ 引擎唯一接口。

M0 实现 status.json（原子写 + 心跳）；run.json/junit 等由 M1/M2 补齐。
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from ragtest.models import RunState


@dataclass
class RunStatus:
    """status.json 的内容（高频小文件，供 DSH 插件 2s 轮询）。"""

    run_id: str
    state: str
    progress: dict[str, int] = field(default_factory=lambda: {"done": 0, "total": 0})
    current_case: str | None = None
    started_at: str = ""
    heartbeat_at: float = 0.0
    pid: int = 0
    message: str | None = None


class RunStatusWriter:
    """status.json 写入器：tmp-write + rename 原子替换，读者永不看到半写文件。"""

    def __init__(self, run_dir: Path, run_id: str, *, total: int = 0):
        self.run_dir = Path(run_dir)
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.status = RunStatus(
            run_id=run_id,
            state=RunState.INIT.value,
            progress={"done": 0, "total": total},
            started_at=time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            heartbeat_at=time.time(),
            pid=os.getpid(),
        )
        self._heartbeat_task: asyncio.Task[None] | None = None
        self.write()

    @property
    def path(self) -> Path:
        return self.run_dir / "status.json"

    def write(self) -> None:
        payload = json.dumps(asdict(self.status), ensure_ascii=False, indent=2)
        fd, tmp = tempfile.mkstemp(dir=self.run_dir, prefix=".status-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(payload)
            os.replace(tmp, self.path)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)

    def update(
        self,
        state: RunState,
        *,
        done: int | None = None,
        current_case: str | None = None,
        message: str | None = None,
    ) -> None:
        self.status.state = state.value
        if done is not None:
            self.status.progress["done"] = done
        if current_case is not None:
            self.status.current_case = current_case
        if message is not None:
            self.status.message = message
        self.status.heartbeat_at = time.time()
        self.write()

    async def heartbeat(self, interval_s: float = 2.0) -> None:
        """后台心跳任务：供长时间无状态迁移的阶段（如轮询就绪）保持 heartbeat_at 新鲜。
        用法：`task = asyncio.create_task(writer.heartbeat())`，结束时 cancel。"""
        while True:
            await asyncio.sleep(interval_s)
            self.status.heartbeat_at = time.time()
            self.write()
