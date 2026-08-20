"""根级 conftest：保证 pytest 与脚本能以源码方式 import ragtest（M0 不做包安装）。"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
