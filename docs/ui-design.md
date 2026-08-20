# RAG Testing Plugin — UI 设计

> 版本：v0.2（对应架构 v0.3 §4.2/§15-M5；补充 Run Detail / 失败诊断 Demo）
> 日期：2026-08-20
> 形态：**`sidebar.footer.action` 菜单按钮 + `shell.overlay` 全屏页**（参照 dsh-agentloop）
> 技术约束：client bundle 为手写 `window.__ModuleLoader__.load` 格式 React；无 URL 路由（页内 tab 状态管理）；组件用 `@deepseek-ai/dsh-client-ui-primitives`（Button/Pill/Modal/Input/Toast/JsonTree/StateDot/TerminalBlock 等）；数据全部来自 host 半 HTTP 路由（§5 数据契约），不直连引擎。
> 前端原型：[`docs/rag-run-detail-demo.html`](rag-run-detail-demo.html)（单 HTML，可直接打开，用于评审运行详情信息架构与交互）

---

## 1. 信息架构

```
侧边栏菜单按钮「RAG 测试」
   └─ 全屏 Overlay（shell.overlay）
       ├─ 顶栏：标题 + 套件选择器 + [▶ 运行] + [✕ 关闭]
       └─ Tab 导航（页内状态，无 URL）
           ├─ 总览 Overview        ← 默认页：最近运行 KPI + 趋势 + 失败速览
           ├─ 运行记录 Runs        ← run 列表 → 运行详情（核心页）
           ├─ 测试套件 Suites      ← suite 清单 + 触发运行 + YAML 预览
           ├─ Baseline 对比        ← current vs baseline 回归表
           └─ 缺陷套件 Defects     ← known-issue 证据（不进质量门）
```

设计原则：
1. **测试闭环优先**：能跑、能看结果、能定位失败——三屏走完（Suites → Runs → Run Detail）。
2. **只读契约**：UI 一切数据来自 artifacts 目录（host 半读取），UI 不含测试逻辑。
3. **失败分析是核心场景**：运行详情页的信息密度最高，归因（attribution）与 trace 一等公民。

---

## 2. 入口：侧边栏菜单按钮

```
┌──────────────┐
│  ⋮  Apps      │
│  ───────────  │
│  🧪 Agent 评测 │   ← dsh-agentloop（先例）
│  📊 RAG 测试   │   ← 本插件（sidebar.footer.action, order 紧随其后）
└──────────────┘
```

- 点击 → 打开全屏 overlay；再次点击/按 `Esc`/点 ✕ → 关闭
- 有运行进行中时，按钮旁显示 StateDot（绿=最近 gate 通过 / 红=失败 / 蓝闪=运行中）

---

## 3. 全屏 Overlay 骨架

```
┌────────────────────────────────────────────────────────────────┐
│ 📊 RAG 测试   [套件: rag-basic-retrieval ▾] [▶ 运行]    [✕]    │ ← 顶栏（固定）
├────────────────────────────────────────────────────────────────┤
│  总览 │ 运行记录 │ 测试套件 │ Baseline │ 缺陷套件                │ ← Tab 导航
├────────────────────────────────────────────────────────────────┤
│                                                                │
│                     （当前 Tab 内容区）                          │
│                                                                │
└────────────────────────────────────────────────────────────────┘
```

顶栏运行操作：
- 套件选择器：列出 `suites/golden/*.yaml`（host 扫描）
- `▶ 运行`：POST 触发 → 跳到「运行记录」并聚焦新 run（轮询开始）
- 运行中按钮变为 `■ 取消`（SIGTERM → CANCELLED → CLEANUP）

---

## 4. Tab 1：总览 Overview（默认页）

```
┌────────────────────────────────────────────────────────────────┐
│ 最近运行  20260820-101500-a1b2c3 · rag-basic-retrieval          │
│ ┌──────────┐┌──────────┐┌──────────┐┌──────────┐┌───────────┐  │
│ │ Pass Rate││ Recall@5 ││   MRR    ││ P95 延迟 ││   Gate    │  │
│ │  96.7%   ││  0.93    ││  0.87    ││  310ms   ││ ✅ PASS   │  │
│ │ ▲ +1.2%  ││ ▼ -2.1%  ││ ─ stable ││ ▲ +54%⚠ ││           │  │
│ └──────────┘└──────────┘└──────────┘└──────────┘└───────────┘  │
│  （KPI 卡第二行 = 与 baseline 的 diff，回归红色高亮）              │
├────────────────────────────────────────────────────────────────┤
│ 趋势（近 10 次运行）                                              │
│ pass_rate  ▁▃▅▄▆▅▇▆▅▇    recall@5  ▂▃▄▅▄▆▄▅▃▅                   │
├────────────────────────────────────────────────────────────────┤
│ 最近失败 Case（3）                                    查看全部 → │
│ ✗ rag_007  多文档检索   recall@5 0.50 < 1.0   检索未命中 doc_011 │
│ ✗ gen_002  事实问答     golden_facts 0.5<1.0  归因: generation  │
│ ✗ rag_012  越权防护     permission_leak 1 > 0  ⚠ 越权召回!      │
└────────────────────────────────────────────────────────────────┘
```

- 数据来源：最新一次 `run.json` + `artifacts/baselines/` diff
- 空态（从未运行）：引导卡「先运行一个 smoke 套件」+ `▶ 运行` 按钮

## 5. Tab 2：运行记录 Runs（核心页）

### 5.1 列表视图

```
┌────────────────────────────────────────────────────────────────┐
│ 状态: [全部▾]  套件: [全部▾]                          🔄 刷新    │
│ ┌──────────────────────────────────────────────────────────┐   │
│ │ ● 运行中  20260820-1030…  rag-basic   ██████░░ 12/18  ■ │   │ ← 置顶，2s 轮询 status.json
│ ├──────────────────────────────────────────────────────────┤   │
│ │ ✅ 通过   20260820-1015…  rag-basic   18/18  96.7%  82s │   │
│ │ ❌ Gate败 20260819-2210…  e2e-gen     9/12   75.0%  4m  │   │
│ │ ⚠ 部分    20260819-1800…  rag-robust  20/23  87.0%  3m  │   │
│ │ ⛔ 取消   20260819-1500…  rag-basic   5/18   —     21s  │   │
│ └──────────────────────────────────────────────────────────┘   │
└────────────────────────────────────────────────────────────────┘
```

状态图标映射终态机：`DONE`✅ / `DONE(gate_failed)`❌ / `PARTIAL`⚠ / `ERROR`⛔ / `TIMEOUT`⏱ / `CANCELLED`⛔ / 运行中●（进度条来自 `status.json.progress`）

### 5.2 运行详情 / 失败诊断（点击行进入，信息密度最高的页面）

运行详情页的核心目标：

- **3 秒知道哪里失败**：Run Header、Metric Bar、Quality Gate 直接暴露失败状态与违规规则。
- **30 秒知道为什么失败**：Case Navigator + Evaluation Panel 直接展示失败 Case、未达标指标、Failure Diagnosis。
- **3 分钟定位到具体 Chunk / Metric / Stage**：Case Detail 内展开 Pipeline、Retrieval TopK、Actual vs Expected、Latency Trend、Raw Data、Logs。

页面整体参考 Prometheus / Grafana / Datadog / GitHub Actions Run Detail，但必须保持 RAG 调试语义，不做普通 CRUD 后台。

```
┌──────────────────────────────────────────────────────────────────────────────┐
│ Sidebar                                                                      │
│  测试运行 active                                                              │
├──────────────────────────────────────────────────────────────────────────────┤
│ RAG 测试 / 运行详情 / e2e_generation                                          │
│ e2e_generation  FAILED                                      [重新运行][导出] │
│ Run ID · Trigger · Branch · Baseline · Duration · Start Time                 │
├──────────────────────────────────────────────────────────────────────────────┤
│ 通过率 18/20 │ Recall@5 0.84 │ Faithfulness 0.81 │ P95 1.95s │ Token 1411  │
├──────────────────────────────────────────────────────────────────────────────┤
│ 质量门失败：2 个规则未通过                                                    │
│ Recall@5 < 0.90   Current 0.84 / Threshold 0.90                              │
│ Faithfulness Regression   Current 0.81 / Baseline 0.89 / Regression -8.7%    │
├──────────────────┬────────────────────────────────────────┬──────────────────┤
│ Case Navigator   │ Case Detail                            │ Evaluation       │
│ 搜索 / 全部失败通过│ Query                                  │ Tabs             │
│ ✗ gen_002        │ RAG Pipeline                            │ Baseline Switch  │
│ ✗ gen_007        │ Query + Copy                            │ Metric Table     │
│ ✓ gen_001        │ Retrieval Top 5                         │ Failure Diagnosis│
│ ✓ gen_003        │ Actual vs Expected                       │ Trace / Latency  │
│                  │ 最近 20 次运行延迟趋势                  │                  │
├──────────────────┴────────────────────────────────────────┴──────────────────┤
│ > 原始数据 Raw Data（默认折叠，Request / Response / Prompt / Chunks / JSON）  │
│ > 日志 Logs（默认折叠，All / INFO / WARN / ERROR 过滤）                       │
└──────────────────────────────────────────────────────────────────────────────┘
```

#### 5.2.1 Run Header

顶部只放运行身份、状态与低频操作：

- 面包屑：`RAG 测试 / 运行详情 / e2e_generation`
- 标题：`e2e_generation` + `FAILED` badge
- 元信息：`Run ID`、`Trigger`、`Branch`、`Baseline`、`Duration`、`Start Time`
- 操作：`重新运行`、`导出报告`、`更多操作`

Demo 阶段按钮只需 toast / console 反馈；正式接入时分别映射到 rerun、report export、artifact 操作。

#### 5.2.2 Metric Bar

Header 下方是一排紧凑 Stat Panel，不使用大卡片：

| 指标 | Current | 辅助信息 |
|---|---:|---|
| 通过率 | `18 / 20` | `90.0%` |
| Recall@5 | `0.84` | `Threshold 0.90`、`↓ 6.7%` |
| Faithfulness | `0.81` | `Baseline 0.89`、`↓ 8.7%` |
| P95 Latency | `1.95s` | `Baseline 1.82s`、`↑ 7.1%` |
| Token / Case | `1,411` | `Baseline 1,320`、`↑ 6.9%` |

颜色语义：质量下降为红色，成本/延迟上升为橙色，达标为绿色。所有数值优先使用等宽字体。

#### 5.2.3 Quality Gate

Quality Gate 使用浅红背景 + 红色边框的紧凑条，不做巨大 Alert：

- 标题：`质量门失败：2 个规则未通过`
- 规则 1：`Recall@5 < 0.90`，显示 `Current 0.84` / `Threshold 0.90`
- 规则 2：`Faithfulness Regression`，显示 `Current 0.81` / `Baseline 0.89` / `Regression -8.7%`

该区域用于回答“为什么整个 Run 失败”，不是展示所有指标。

#### 5.2.4 三栏主工作区

桌面优先，推荐列宽：

| 区域 | 宽度 | 作用 |
|---|---:|---|
| Case Navigator | 18% | 让用户快速定位失败 Case |
| Case Detail | 52% | 展示当前 Case 的 RAG Pipeline 与证据 |
| Evaluation | 30% | 指标、Baseline Diff、失败诊断、Trace |

1440 / 1920 / 2560 宽度下保持高信息密度；窄屏可把 Evaluation 下移，但 M5 以桌面评审为主。

#### 5.2.5 Case Navigator

Case Navigator 必须支持页内真实交互，不刷新页面：

- 搜索：`搜索用例名称 / 关键词`
- 过滤：`全部 20`、`失败 2`、`通过 18`
- 列表项显示：case id、中文名称、状态、失败摘要

示例：

```
gen_002
中文档事实问答
FAILED
Recall@5 0.40 < 0.90

gen_007
越权防护
FAILED
Faithfulness 0.71 < 0.85
```

点击 Case 后，右侧 Case Detail、Evaluation、Raw Data 当前上下文同步更新。

#### 5.2.6 Case Detail

Case Detail 顶部固定展示紧凑 RAG Pipeline：

```
Query -> Retrieve -> Rerank -> Context -> Generate -> Evaluate
```

每个 Stage 显示 `status` 与 `latency`：

| Stage | 示例状态 | 示例耗时 |
|---|---|---:|
| Query | PASS | 12ms |
| Retrieve | FAIL | 210ms |
| Rerank | PASS | 43ms |
| Context | WARN | 18ms |
| Generate | PASS | 1.2s |
| Evaluate | FAIL | 120ms |

Pipeline 风格参考 OpenTelemetry Trace + CI Pipeline：失败节点红色，成功绿色，警告橙色。节点必须紧凑，不做横向大流程图。

Case Detail 内容顺序：

1. **问题 Query**：显示用户问题，右侧 `复制` 按钮真实可用。
2. **检索结果 Top 5**：表格列为 `Rank`、`Document / Chunk`、`Score`、`Expected`、`Status`。
3. **Retrieval Diagnosis**：当 expected chunk 未达标时显示 `Expected Chunk 排名过低` 或 `Expected Chunk 未进入 TopK`。
4. **Actual vs Expected**：左右等宽双栏，保留关键差异高亮。
5. **最近 20 次运行延迟趋势**：轻量 SVG 折线图，至少包含 `End-to-End P95` 与 `Evaluator P95` 两个系列。

Retrieval TopK 表格要求：

- Score 显示数字 + 小型水平进度条。
- Expected Chunk 使用绿色或明显标记。
- Forbidden / Risk 行使用浅红背景。
- `Rank Too Low` 使用 warning badge。

#### 5.2.7 Evaluation Panel

Evaluation 是右侧常驻诊断面板，顶部包含：

- Tab：`Retrieval`、`Generation`、`Safety`、`综合`
- Switch：`与基线对比`

Tab 切换必须为真实交互。Baseline Switch 关闭时只显示 `Current / Threshold`；开启时显示 `Current / Threshold / Baseline / Diff`。

Retrieval 示例指标：

| Metric | Current | Threshold | Baseline |
|---|---:|---:|---:|
| Recall@5 | 0.40 | 0.90 | 0.91 |
| Precision@5 | 0.80 | 0.80 | 0.88 |
| MRR | 0.50 | 0.80 | 0.82 |
| NDCG@5 | 0.71 | 0.80 | 0.87 |

Generation 示例指标：`Faithfulness`、`Correctness`、`Relevancy`、`Citation`。

Safety 示例指标：`Permission`、`Forbidden Facts`、`Leakage`、`Prompt Injection`。

Evaluation Panel 下半部分固定包含：

- **失败原因**：例如 `Recall@5 未达到阈值 / Expected Chunk 排名过低`、`Faithfulness 未达到阈值 / Answer 中存在无法从 Context 支撑的事实`。
- **Trace / 耗时分布**：按 Query、Retrieve、Rerank、Context、Generate、Evaluator 展示水平条，Generate 应能明显看出耗时最高。

#### 5.2.8 Raw Data 与 Logs

页面底部保留两个默认折叠区：

- `> 原始数据 Raw Data`
  - Tab：`Request`、`Response`、`Prompt`、`Retrieved Chunks`、`Model Output`、`Evaluator`、`Raw JSON`
  - 至少 3-4 个 Tab 有 Mock 数据。
  - JSON 使用 monospace 代码块。
  - 支持复制当前 Tab 内容。

- `> 日志 Logs`
  - 默认折叠。
  - 展开后展示 `INFO`、`WARN`、`ERROR` 不同级别。
  - 支持 `All`、`INFO`、`WARN`、`ERROR` 过滤。

#### 5.2.9 Demo Mock Data

前端 Demo 使用 Mock Data，不接真实后端。数据结构按后续工程化拆分预留：

```ts
run
cases
metrics
retrievalResults
baseline
trace
logs
rawData
```

核心 Mock Run：

| 字段 | 值 |
|---|---|
| name | `e2e_generation` |
| status | `failed` |
| runId | `20260819-221047-f9e8d7` |
| branch | `main` |
| duration | `4m12s` |
| trigger | `manual` |
| baseline | `main-20260818-221015` |

Demo 原型阶段可以把 Mock Data 内联在单 HTML 中，便于评审；正式实现时迁移到独立 mock / fixture 文件。

#### 5.2.10 视觉规范

运行详情页采用高信息密度的监控平台风格：

| Token | 值 | 用途 |
|---|---|---|
| Background | `#f7f8fa` | 页面底色 |
| Panel | `#ffffff` | 主内容面板 |
| Border | `#e5e7eb` | 面板分割线、表格线 |
| Primary | `#2563eb` | 当前选中、主要按钮 |
| Success | `#16a34a` | PASS、达标 |
| Failure | `#dc2626` | FAILED、质量门失败、未达标 |
| Warning | `#d97706` | WARN、延迟/成本上升、Rank Too Low |
| Text | `#111827` | 主文本 |
| Secondary | `#6b7280` | 辅助文本 |
| Sidebar | `#0f172a` | 左侧导航 |

约束：

- Radius 使用 `4px / 6px / 8px`，除 badge/switch 外不超过 `10px`。
- Panel padding 控制在 `12px ~ 16px`，表格行高 `32px ~ 40px`，section 间距 `8px ~ 12px`。
- 指标、ID、耗时、JSON、日志使用等宽字体。
- 不使用大阴影、玻璃拟态、大渐变、大圆角、营销型 hero 或普通 CRUD 卡片布局。
- 图表优先使用轻量 SVG / CSS；不要为了单个趋势图引入重量级图表依赖。

## 6. Tab 3：测试套件 Suites

```
┌────────────────────────────────────────────────────────────────┐
│ ┌──────────────────────────────────────────────────────────┐   │
│ │ rag-basic-retrieval      [smoke][retrieval]    18 cases  │   │
│ │ 最近: ✅ 96.7% (2h 前)      [▶ 运行] [查看 YAML]          │   │
│ ├──────────────────────────────────────────────────────────┤   │
│ │ e2e_generation           [generation][e2e]     12 cases  │   │
│ │ 最近: ❌ 75.0% (昨天)       [▶ 运行] [查看 YAML]          │   │
│ ├──────────────────────────────────────────────────────────┤   │
│ │ defects                  [defect]               3 cases  │   │
│ │ known-issue 证据套件，不进质量门  [▶ 运行] [查看 YAML]      │   │
│ └──────────────────────────────────────────────────────────┘   │
└────────────────────────────────────────────────────────────────┘
```

「查看 YAML」→ Modal 内只读代码高亮显示（host 读文件原文）。

## 7. Tab 4：Baseline 对比

```
┌────────────────────────────────────────────────────────────────┐
│ 套件: [e2e_generation ▾]  Baseline: [main ▾]  对比运行: [最近 ▾] │
│ 可比性: ✅ 指纹一致（若不一致 → ⚠ incomparable 横幅 + 差异项）     │
│ ┌──────────────────────────────────────────────────────────┐   │
│ │ 指标          Baseline   Current    Δ        分类          │   │
│ │ recall@5      0.92       0.84      -8.7%    🔴 regression │   │
│ │ mrr           0.85       0.87      +2.4%    🟢 improvement│   │
│ │ faithfulness  0.91       0.90      -1.1%    ⚪ stable      │   │
│ │ search_p95    850ms      1310ms    +54%     🔴 regression │   │
│ └──────────────────────────────────────────────────────────┘   │
│ [📌 将本次运行设为 baseline]（按钮确认后调 baseline-update）      │
└────────────────────────────────────────────────────────────────┘
```

## 8. Tab 5：缺陷套件 Defects

- 与 Runs 相同的列表/详情结构，但 case 状态语义反转：`expected_fail` 且实际失败 = ✅「缺陷已复现（证据有效）」；意外通过 = 🎉「缺陷疑似已修复，请更新套件」
- 页面顶部固定说明横幅：「本套件记录已知产品缺陷证据，不参与质量门」

---

## 9. 数据契约（host 半 HTTP 路由 → artifacts）

| 路由 | 方法 | 来源 | 消费视图 |
|---|---|---|---|
| `/plugins/rag-testing/api/suites` | GET | 扫描 `suites/golden/*.yaml`（id/name/tags/case 数） | Suites、顶栏选择器 |
| `/plugins/rag-testing/api/suites/:id/yaml` | GET | 读文件原文 | Suites YAML Modal |
| `/plugins/rag-testing/api/runs` | POST `{suite_id}` | `ctx.subprocess.spawn("uv run ragtest run …")` | 顶栏/Suites 触发 |
| `/plugins/rag-testing/api/runs` | GET | 扫描 `artifacts/runs/*/status.json` + run.json summary | Runs 列表、Overview |
| `/plugins/rag-testing/api/runs/:id` | GET | 读 `run.json` 全量 | 运行详情 |
| `/plugins/rag-testing/api/runs/:id/status` | GET | 读 `status.json`（运行中 2s 轮询） | Runs 进度条、StateDot |
| `/plugins/rag-testing/api/runs/:id/cancel` | POST | 对 pid 发 `SIGTERM` | 取消按钮 |
| `/plugins/rag-testing/api/runs/:id/raw/:case` | GET | 读 `raw/<case>.*.json` | 「查看 raw JSON」（JsonTree） |
| `/plugins/rag-testing/api/baselines?suite=` | GET | 读 `artifacts/baselines/<suite>/*.json` | Baseline 选择器 |
| `/plugins/rag-testing/api/baselines/update` | POST | spawn `ragtest baseline-update` | 「设为 baseline」 |

host 半对 artifacts 目录**只读**（除 spawn 与 baseline-update 两个写动作）；引擎是唯一写者。

## 10. 状态与交互细节

| 场景 | 行为 |
|---|---|
| 运行中 | Runs 列表置顶进度卡，2s 轮询 `status.json`；`heartbeat_at` >30s 未刷新 → 黄色「疑似僵死」徽章 + 强制 kill 按钮 |
| 取消 | `SIGTERM` → 状态变 `CANCELLED`（清理完成后落终态，轮询停止） |
| 空态 | 无 run：引导卡；无 suite：提示 `suites/` 目录路径 |
| 错误态 | spawn 失败（引擎未安装/uv 不存在）→ Toast + Runs 页内嵌 TerminalBlock 展示 stderr 尾部 |
| 并发 | 同一时刻只允许 1 个运行（引擎层已有 RunLease；UI 在运行中禁用「运行」按钮并提示） |
| i18n | `ctx.locale.register` 注册 zh/en 词典（同 agentloop/plugin-manager 范式） |

## 11. M5 范围（做 / 不做）

**做**：以上全部 5 个 Tab、运行触发/取消/轮询、失败详情（含归因与 trace）、Baseline diff、raw JSON 查看、defect 套件视图。

**不做（后续版本）**：单 case 重跑与实时流式进度（需 `ragtest serve` 服务模式）、Golden Set 在线编辑（保持 git 管理，UI 只读）、图表库级趋势图（MVP 用字符火花线/简单 SVG）、多环境切换、用户权限。

---

*本文档随 M5 里程碑实施；如 UI 实现中发现契约不足，先回到本文档与架构 v0.3 §5.1 更新，再改代码。*
