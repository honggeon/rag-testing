"""报告写出：run.json 原子写（tmp + rename；M2 补 summary.md / junit.xml）。"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from ragtest.models.result import TestRun


def write_run_json(run: TestRun, run_dir: Path) -> Path:
    """原子写 run.json（读者永不看到半写文件，契约 §5.1）。"""
    run_dir = Path(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    target = run_dir / "run.json"
    payload = run.model_dump_json(indent=2)
    fd, tmp = tempfile.mkstemp(dir=run_dir, prefix=".run-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(payload)
        os.replace(tmp, target)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    return target


def load_run_json(path: Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))
