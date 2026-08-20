#!/usr/bin/env bash
# dsh-rag-testing 卸载：移除 cordis patch 注册 + 卸包
set -euo pipefail

PROFILE_DIR="${DSH_PROFILE_DIR:-$HOME/.dsh/profiles/web}"
PATCH_FILE="$PROFILE_DIR/cordis.patch.yml"

echo "==> 从 web profile 卸包"
cd "$PROFILE_DIR"
pnpm remove dsh-rag-testing || true

echo "==> 移除 cordis patch 注册块（rag-testing 条目）"
if [[ -f "$PATCH_FILE" ]]; then
  python3 - "$PATCH_FILE" <<'EOF'
import re, sys
path = sys.argv[1]
text = open(path, encoding="utf-8").read()
# 移除 insert 块中的 rag-testing 条目（- id: rag-testing 到下一个 - id: 或块尾）
pattern = re.compile(
    r"\n?- insert:\n(?:    .*\n)*?    - id: rag-testing\n(?:      .*\n)*", re.MULTILINE)
new = pattern.sub("\n", text, count=1)
open(path, "w", encoding="utf-8").write(new)
print("    已移除（请人工确认 patch 文件格式）")
EOF
fi

echo "✅ 卸载完成。重启 DSH web 服务生效。"
