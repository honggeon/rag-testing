# RAG Testing Plugin — 前端工程设计

> 版本：v0.1（对应 `ui-design.md` v0.1 交互设计、架构 v0.3）
> 日期：2026-08-20
> 范围：`plugin/`（npm 包 `dsh-rag-testing`）的 **client 半**（浏览器 bundle）工程设计 + 与 host 半的边界
> 约束事实（已核验）：
> - bundle 格式 `window.__ModuleLoader__.load({id, factory})`，**手写、无构建步骤**（先例：`dsh-client-plugin-manager/lib/client.js`、`dsh-agentloop/lib/client.js`）
> - `require("react")` 等走 PLATFORM_MODULES 共享实例：react、react-dom、cordis、dsh-client-ui-slots、dsh-client-web-react、**dsh-client-ui-primitives**、dsh-client-schema-form（`dsh-client-web/lib/index.js` L423）
> - 槽位：`sidebar.footer.action`（list）+ `shell.overlay`（list、root）——用 `ctx.slots.inject(name, cb)` 等槽位声明后再注册（agentloop `lib/client.js:1406-1423` 范式）
> - HMR：改 bundle 后自动重载（React 状态重置），需 `pnpm run dev:web` watcher 重写 bundle 才生效

---

## 1. 文件与装配

```
plugin/
├── package.json          # main=lib/index.js；exports["./client"]=lib/client.js；
│                         # dsh.bundle.patch=./cordis.patch.yml；
│                         # dsh.client: {inject: ["@deepseek-ai/dsh-client-runtime",
│                         #   "@deepseek-ai/dsh-client-ui-slots", "@deepseek-ai/dsh-client-locale",
│                         #   "@deepseek-ai/dsh-client-ui-primitives"], platform: "web", immediately: true}
├── cordis.patch.yml      # - insert: [{id: rag-testing, name: 'dsh-rag-testing'}]
├── lib/
│   ├── index.js          # host 半（§7，不在本文档展开）
│   └── client.js         # 浏览器 bundle，单文件手写（MVP 目标 ≤1500 行，超出则拆 client/ 多文件 + 简单拼接脚本）
├── install.sh / uninstall.sh   # pnpm add -w + patch 追加/移除
```

**client bundle 骨架**（装配顺序固定）：

```js
window.__ModuleLoader__.load({
  id: "dsh-rag-testing",
  factory: (require) => {
    var module = { exports: {} };
    var React = require("react");
    var UI = require("@deepseek-ai/dsh-client-ui-primitives");

    var NS = "ragTesting";                    // locale 命名空间
    var inject = ["slots", "locale"];          // cordis 服务依赖

    // …常量 / api 层 / store / 组件 / 样式 / apply…

    function apply(ctx) {
      ctx.effect(() => ctx.locale.register(NS, { zh: zh, en: en }), "rag-testing: i18n");
      var disposeStyle = injectCss();
      ctx.slots.inject("sidebar.footer.action", () =>
        ctx.slots.register({ name: "sidebar.footer.action", id: "rag-testing",
          order: 20, locale: NS, inject: () => ({ t: ctx.locale.bind(NS) }) }, MenuButton));
      ctx.slots.inject("shell.overlay", () =>
        ctx.slots.register({ name: "shell.overlay", id: "rag-testing",
          order: 20, locale: NS, inject: () => ({ t: ctx.locale.bind(NS) }) }, OverlayRoot));
      return () => disposeStyle && disposeStyle();
    }
    module.exports = { apply, inject, NS };
    return module.exports;
  },
});
```

## 2. 组件树

```
MenuButton                        ← sidebar.footer.action；StateDot 反映最新 gate/运行中
└─ （点击 → store.ui.open = true）

OverlayRoot                       ← shell.overlay；始终挂载，store.ui.open 控制显隐
├─ TopBar                         ← 标题 / SuitePicker / RunButton(或 CancelButton) / CloseButton
├─ TabNav                         ← 5 tab；active 来自 store.ui.tab
└─ TabPanel
   ├─ OverviewTab
   │  ├─ KpiRow        ×5（KpiCard：value + BaselineDelta 徽标）
   │  ├─ TrendStrip    ×2（字符火花线，无图表库）
   │  └─ FailureList   → 点击跳 RunDetailTab（store.ui 联动）
   ├─ RunsTab
   │  ├─ FilterBar（状态/套件下拉）
   │  ├─ RunningCard（置顶；ProgressBar + CancelButton；2s 轮询）
   │  └─ RunTable → 行点击进 RunDetailView
   ├─ RunDetailView（RunsTab 内二级视图，store.ui.selectedRun 非空时替换列表）
   │  ├─ RunHeader（gate 徽章、违规清单、指纹行、时长）
   │  ├─ CaseListPane（左；状态过滤）
   │  └─ CaseDetailPane（右；按 case 类型分发）
   │     ├─ RetrievalCaseDetail（ExpectedActualTable / ScoreBar / DegradedBadge / PollTimeline）
   │     └─ GenerationCaseDetail（MetricRows / AttributionBadge / ToolCallTimeline / AnswerCompare）
   ├─ SuitesTab（SuiteCard 列表 + YamlModal）
   ├─ BaselineTab（CompareTable + IncomparableBanner + SetBaselineButton）
   └─ DefectsTab（复用 RunTable/CaseDetailPane，状态语义映射不同）
└─ ToastHost（ui-primitives Toast）
```

**状态语义集中一处**：`runStatusMeta(state) → {icon, color, label}` 与 `caseStatusMeta(case_, isDefectSuite)`（defect 套件反转逻辑只在这里，组件不各自判断）。

## 3. 状态管理（无外部依赖）

单 `store` 模块：React `useSyncExternalStore` 包一个普通 JS 对象（~80 行），不引 Redux/zustand（bundle 手写、无构建，依赖越少越好）。

```js
store = {
  ui:  { open: false, tab: "overview", selectedRun: null, selectedCase: null, filters: {...} },
  data: { suites: null, runs: null, runDetail: {/* runId -> cache */}, baselines: null },
  running: { runId: null, status: null, lastHeartbeat: null },   // 轮询写入
  error: null,
}
// 动作：openOverlay/closeOverlay/selectTab/selectRun/runSuite/cancelRun/refresh…
// 规则：组件只读 store + 调动作；fetch 只在 api 层；轮询只在 poller 模块
```

- overlay 关闭时**保留** tab/选中态（重开恢复现场）；HMR 重载后状态重置（接受，同 agentloop）
- `runDetail` 按 runId 缓存，切 run 不重拉；`raw/case` 按需拉取不缓存

## 4. 数据层（api client + 轮询）

```js
// api.js —— 唯一允许 fetch 的地方（~120 行）
var BASE = "/plugins/rag-testing/api";
api = {
  listSuites:        ()      => get(`/suites`),
  getSuiteYaml:      (id)    => get(`/suites/${id}/yaml`),
  listRuns:          (f)     => get(`/runs?${qs(f)}`),
  getRun:            (id)    => get(`/runs/${id}`),
  getRunStatus:      (id)    => get(`/runs/${id}/status`),
  getRawCase:        (id,c)  => get(`/runs/${id}/raw/${c}`),
  runSuite:          (suite) => post(`/runs`, { suite_id: suite }),
  cancelRun:         (id)    => post(`/runs/${id}/cancel`),
  listBaselines:     (suite) => get(`/baselines?suite=${suite}`),
  setBaseline:       (suite, runId, name) => post(`/baselines/update`, {...}),
};
// 统一错误：非 2xx → throw {code, message}，组件层 catch → Toast + error 态
```

```js
// poller.js —— 运行中轮询（~60 行）
// start(runId): setInterval 2s 调 getRunStatus → 写 store.running
// 停止条件：status 进入终态（DONE*/PARTIAL/ERROR/TIMEOUT/CANCELLED）→ 拉一次 getRun 全量 → 停轮询
// 僵死检测：heartbeat_at > 30s 未变 → store.running.stale = true（UI 黄徽章 + 强制 kill）
// overlay 关闭 / 组件卸载 → clearInterval（effect cleanup，防泄漏）
```

## 5. 样式策略

- `injectCss()`：单 `<style>` 标签注入（agentloop 同范式），所有类名加 `rt-` 前缀避免污染 DSH
- 布局：CSS grid/flex 手写；**不引 CSS 框架**
- 色彩：尽量吃 DSH 主题变量（`var(--dsh-*)`，先查 ui-primitives 实际变量名，实现时核对），状态色固定语义：pass=绿 / fail=红 / warn=黄 / running=蓝 / skipped=灰
- JsonTree（raw 查看）、StateDot（状态点）、TerminalBlock（spawn 错误 stderr）、Modal（YAML 预览）、Pill（tag/徽章）直接用 ui-primitives

## 6. 关键交互实现要点

| 交互 | 实现 |
|---|---|
| 打开/关闭 overlay | MenuButton 点击 → `store.ui.open=true`；Esc 监听挂 OverlayRoot effect（打开时 add、关闭时 remove） |
| Overview → 失败 case 联动 | `selectRun(runId); selectCase(caseId); setTab("runs")` 一条动作完成三态切换 |
| 运行按钮 | `runSuite` → 乐观插入 running 卡 → 跳 RunsTab → `poller.start(newRunId)`；运行中全局禁用其他「运行」（引擎单并发） |
| 取消 | `cancelRun` → 不停轮询，等 status.json 落 `CANCELLED` 终态后 poller 自停 |
| Baseline 设为 | Modal 二次确认（输入 baseline 名，默认 main）→ `setBaseline` → Toast |
| defect 状态反转 | `caseStatusMeta(case, {defect: true})`：expected_fail && 实际失败 → ✅「缺陷已复现」；意外通过 → 🎉「疑似已修复」 |

## 7. 与 host 半的边界（lib/index.js，仅列职责）

- `ctx.webServer.register` 挂 `/plugins/rag-testing/api/*` 十条路由（见 ui-design.md §9 表）
- 只读 artifacts 目录 + 两个写动作（spawn run / spawn baseline-update）；引擎安装探测（`uv run ragtest --help` 失败 → 路由返回 503 + 安装提示，前端 Toast）
- run_id 由 host 生成（`yyyyMMdd-HHmmss-短hash`）传入 CLI
- 索引缓存：`ctx.storageDomain` 存 run 列表轻量索引（避免每次全目录扫描），status.json mtime 失效

## 8. 目录内代码组织（client.js 单文件内的 section 顺序）

```
1. constants（NS / TAB 定义 / 状态语义表）
2. i18n 词典（zh / en）
3. api.js（fetch 封装）
4. store.js（useSyncExternalStore）
5. poller.js
6. utils（格式化时长/百分比/delta 着色/逻辑 doc 映射）
7. 原子组件（KpiCard / StatePill / ProgressBar / TrendStrip / DeltaBadge）
8. 视图组件（五个 Tab + RunDetailView + CaseDetail 两种）
9. OverlayRoot / MenuButton
10. injectCss
11. apply（装配）
```

规模预算：总计 ~1200–1500 行；超出即拆 `client/` 多文件 + 一个 30 行 cat 拼接脚本（install 时生成 bundle），仍保持无构建链。

## 9. 验收清单（M5 Done When 的前端部分）

1. `install.sh` 后侧边栏出现「RAG 测试」按钮，与「Agent 评测」平级；点击开 overlay、Esc 关
2. 五个 Tab 空态/错误态文案完整（引擎未安装时给出安装指引）
3. 触发 smoke suite：进度卡 2s 刷新、可取消（终态 CANCELLED）、完成后自动展示结果
4. 失败 case 详情：检索类出 Expected/Actual 对照；E2E 类出归因 + tool_calls 时间线（M4 后）
5. Baseline 页 diff 着色正确；incomparable 横幅在指纹不一致时出现
6. HMR 下改 client.js 自动重载不白屏；刷新页面后 overlay 默认关闭、数据可重新拉取

---

*本文档与 `ui-design.md` 关系：前者定「长什么样、怎么交互」，本文定「怎么实现」。实施 M5 时两文档冲突以本文档的技术约束为准，交互冲突以 ui-design.md 为准。*
