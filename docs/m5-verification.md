# M5 验收与运维记录

> 日期：2026-08-21 ｜ 插件：`dsh-rag-testing`（host + client，同 agentloop 范式）

## 验收结果（playwright 对真实 DSH 页面实测）

| 项 | 结果 |
|---|---|
| client bundle 挂载路径 | `/plugins/dsh-rag-testing/client.js`（**包名**，不是 entry id `rag-testing`；boot graph 按 package.json 的 `dsh.client` 元数据解析） |
| 侧边栏菜单按钮 | ✅ 出现（`sidebar.footer.action`，与 Settings 平级） |
| overlay 打开 | ✅（需 `store.set` 替换 state 引用，见下） |
| 五 Tab | ✅ 总览 / 运行记录 / 测试套件 / Baseline / 缺陷套件 |
| 总览 KPI | ✅ bullet chart 渲染最近运行数据 |
| 套件页「运行」 | ✅ 点击 → POST /runs |
| 错误路径 | ✅ host 缺密码 → 503 → UI 顶栏显示错误 |

**未完成**：GUI 触发的**运行中进度 → 完成 → 详情展示**全链路——需要 host 进程携带
`RAGTEST_ARAG_ADMIN_PASSWORD` 环境变量重启后复核（见「运维要求」）。

## 实现要点（踩坑记录）

1. **useSyncExternalStore 陷阱**：`store.set` 若 `Object.assign(this.state, patch)` 原地
   变更，getSnapshot 引用不变 → **点击后不重渲染**（按钮可见但 overlay 永不打开）。
   修复：`this.state = Object.assign({}, this.state, patch)`（commit `d375035`）。
2. **client 路径**：`/plugins/<pkg-name>/client.js`——排查 404 时先试包名。
3. **CaseDetail 嵌套括号**：手写 createElement 深嵌套 ternary 极易括号失衡；
   改为「children 数组 + if 推入」结构（提交前 `node --check`）。
4. **状态符号**：design-system 禁 emoji 当状态 → `CaseStatusIcon` 用 ✓/✗/!/— 文本符号
   + 颜色（Phosphor 内联 SVG 留作后续优化）。
5. **patch 热生效**：host 路由（webServer.register）在追加 `cordis.patch.yml` 后
   **无需重启即生效**；client bundle 需重启（boot graph 启动时构建）。

## 运维要求（host 环境）

```bash
# 启动 DSH web 前必须导出（引擎登录被测 arag 管理账号用；不落盘、不进插件配置）
export RAGTEST_ARAG_ADMIN_PASSWORD=<被测 arag 的 ADMIN/OPS_PASSWORD>
```

插件配置（`cordis.patch.yml` 的 config 段）可覆盖：engineDir / aragBaseUrl / aragAuthUrl /
aragAdminEmail / agentBaseUrl。

## 触发语义

- 单并发：host 拒绝第二个运行（409）
- 取消：POST /runs/:id/cancel → 进程组 SIGTERM → 引擎 CANCELLED → CLEANUP → status.json 终态
- 进度：client 2s 轮询 GET /runs/:id/status（status.json heartbeat >30s 视为僵死）
