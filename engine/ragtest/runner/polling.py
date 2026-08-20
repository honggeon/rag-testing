"""指数 backoff 轮询工具（架构 §6.1：禁止固定 sleep）。

独立模块、无 runner 包内依赖，adapter 与 runner 均可使用。
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import TypeVar

from ragtest.adapters.base import PollPolicy, WaitTimeout

T = TypeVar("T")


async def poll_until(
    probe: Callable[[], Awaitable[T | None]],
    *,
    policy: PollPolicy = PollPolicy(),
    describe: str = "resource",
) -> T:
    """反复调用 probe 直到返回非 None（就绪）或超时。

    - 间隔：initial_s 起、每次 ×factor、封顶 max_interval_s
    - probe 返回 None 表示未就绪；抛 IngestFailed 表示终态失败，立即上抛
    - 超时抛 WaitTimeout（携带最后一次观测值）
    """
    deadline = time.monotonic() + policy.timeout_s
    interval = policy.initial_s
    while True:
        value = await probe()  # IngestFailed 直接上抛
        if value is not None:
            return value
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise WaitTimeout(f"等待 {describe} 就绪超时（{policy.timeout_s:.0f}s）")
        await asyncio.sleep(min(interval, remaining))
        interval = min(interval * policy.factor, policy.max_interval_s)
