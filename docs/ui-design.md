# RAG Testing Plugin — UI 设计

> 版本：v0.1（对应架构 v0.3 §4.2/§15-M5）
> 日期：2026-08-20
> 形态：**`sidebar.footer.action` 菜单按钮 + `shell.overlay` 全屏页**（参照 dsh-agentloop）
> 技术约束：client bundle 为手写 `window.__ModuleLoader__.load` 格式 React；无 URL 路由（页内 tab 状态管理）；组件用 `@deepseek-ai/dsh-client-ui-primitives`（Button/Pill/Modal/Input/Toast/JsonTree/StateDot/TerminalBlock 等）；数据全部来自 host 半 HTTP 路由（§5 数据契约），不直连引擎。

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

### 5.2 运行详情（点击行进入，信息密度最高的页面）

```
┌────────────────────────────────────────────────────────────────┐
│ ← 返回列表   运行 20260819-2210…  · e2e_generation              │
│ Gate: ❌ FAIL（2 项违规）  Baseline: main（可比✓）   时长 4m12s   │
│ ┌──────────────────────────────────────────────────────────┐   │
│ │ 违规: recall@5 0.84 < 0.90 │ faithfulness 回归 -8.7% > 3%  │   │
│ ├──────────────────────────────────────────────────────────┤   │
│ │ 环境指纹: llm=xuanjian-lite · skill=xj-kbase@a1b2 ·        │   │
│ │           emb=qwen3-0.6b · rerank=off · agent=v0.8.2       │   │
│ ├──────────────┬───────────────────────────────────────────┤   │
│ │ Case 列表     │  Case 详情（选中 gen_002）                  │   │
│ │ [过滤: 失败▾] │  ─────────────────────────────────────    │   │
│ │ ✗ gen_002    │  ❌ 单文档事实问答        severity: critical│   │
│ │ ✗ gen_007    │  问题: "十三五规划的主要目标是什么？"         │   │
│ │ ✓ gen_001    │                                            │   │
│ │ ✓ gen_003    │  【指标】                                  │   │
│ │ …            │  ✗ golden_facts   0.50 < 1.0               │   │
│ │              │     缺失事实: "全面建成小康社会"              │   │
│ │              │  ✓ forbidden_fact  pass                     │   │
│ │              │  ✓ citation_format pass（找到 [^1][^2]）     │   │
│ │              │                                            │   │
│ │              │  【归因】🧭 generation_failure              │   │
│ │              │  检索命中 ✓（expected doc 已召回，问题在生成） │   │
│ │              │                                            │   │
│ │              │  【Trace】                                 │   │
│ │              │  agent_tool_calls:                         │   │
│ │              │  ① knowledge_retrieve query="十三五 目标"    │   │
│ │              │     top_k=10 → 8 chunks ✓命中 doc_shisanwu  │   │
│ │              │     320ms · is_error=false                  │   │
│ │              │  client_spans: chat 8.4s · usage 3210+256tok│   │
│ │              │  unavailable: prompt全文 / rerank_scores    │   │
│ │              │                                            │   │
│ │              │  【回答 vs 期望】         [查看 raw JSON]    │   │
│ │              │  实际: "十三五规划提出…（节选）"              │   │
│ │              │  应含: "到 2020 年全面建成小康社会" ✗ 未出现  │   │
│ └──────────────┴───────────────────────────────────────────┘   │
└────────────────────────────────────────────────────────────────┘
```

检索类 case 的详情右栏替换为：
- **Expected vs Actual 对照表**：expected documents（命中✓/未命中✗）× actual top-k 列表（rank/score/是否 expected/是否 forbidden 高亮）
- forbidden 命中的行红色背景（permission_leak 一眼可见）
- score 序列条形图 + degraded 标志 + 轮询时间线（indexing 相关 case）

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
