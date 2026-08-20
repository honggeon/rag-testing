#!/usr/bin/env bash
# dsh-rag-testing 一键安装：装包 → 注册 cordis patch → 提示重启
set -euo pipefail

PLUGIN_DIR="$(cd "$(dirname "$0")" && pwd)"
PROFILE_DIR="${DSH_PROFILE_DIR:-$HOME/.dsh/profiles/web}"
PATCH_FILE="$PROFILE_DIR/cordis.patch.yml"

echo "==> 安装插件包到 web profile"
cd "$PROFILE_DIR"
pnpm add -w "$PLUGIN_DIR"

echo "==> 注册 cordis patch"
if grep -q "id: rag-testing" "$PATCH_FILE" 2>/dev/null; then
  echo "    已注册，跳过"
else
  cat "$PLUGIN_DIR/cordis.patch.yml" >> "$PATCH_FILE"
  echo "    已追加到 $PATCH_FILE"
fi

echo ""
echo "✅ 安装完成。请重启 DSH web 服务后刷新 http://127.0.0.1:3080"
echo "   侧边栏底部菜单将出现「RAG 测试」按钮（与 Agent 评测平级）。"
echo ""
echo "⚠️  前置条件：host 进程需设置 RAGTEST_ARAG_ADMIN_PASSWORD 环境变量"
echo "   （启动 dsh 的 shell 里 export，引擎用它登录被测 arag 的管理账号）"
