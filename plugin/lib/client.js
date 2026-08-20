/**
 * dsh-rag-testing — client bundle（手写 __ModuleLoader__ 格式，无构建步骤）。
 *
 * 设计依据：docs/ui-design.md（5 Tab 信息架构）+ docs/frontend-design.md（工程约束）
 * 槽位：sidebar.footer.action（菜单按钮）+ shell.overlay（全屏页）
 * 数据：全部来自 host 半 /plugins/rag-testing/api/*（fetch），不直连引擎。
 */

/* eslint-disable */
window.__ModuleLoader__.load({
  id: "dsh-rag-testing",
  factory: (require) => {
    var module = { exports: {} };
    var exports = module.exports;

    var React = require("react");
    var h = React.createElement;
    var useState = React.useState;
    var useEffect = React.useEffect;
    var useSyncExternalStore = React.useSyncExternalStore;

    var NS = "ragTesting";
    var inject = ["slots", "locale"];
    var API = "/plugins/rag-testing/api";

    // =====================================================================
    // i18n
    // =====================================================================
    var zh = {
      "menu.label": "RAG 测试",
      "overlay.title": "RAG 测试平台",
      "tab.overview": "总览",
      "tab.runs": "运行记录",
      "tab.suites": "测试套件",
      "tab.baseline": "Baseline",
      "tab.defects": "缺陷套件",
      "action.run": "运行",
      "action.cancel": "取消",
      "action.close": "关闭",
      "action.refresh": "刷新",
      "action.back": "返回列表",
      "empty.runs": "还没有运行记录——先到「测试套件」发起一次运行",
      "empty.suites": "未发现测试套件（suites/golden/*.yaml）",
      "error.engine": "引擎调用失败",
    };
    var en = {
      "menu.label": "RAG Testing",
      "overlay.title": "RAG Testing Platform",
      "tab.overview": "Overview",
      "tab.runs": "Runs",
      "tab.suites": "Suites",
      "tab.baseline": "Baseline",
      "tab.defects": "Defects",
      "action.run": "Run",
      "action.cancel": "Cancel",
      "action.close": "Close",
      "action.refresh": "Refresh",
      "action.back": "Back",
      "empty.runs": "No runs yet — start one from Suites",
      "empty.suites": "No suites found (suites/golden/*.yaml)",
      "error.engine": "Engine call failed",
    };

    // =====================================================================
    // store（useSyncExternalStore，零外部依赖）
    // =====================================================================
    var store = {
      state: {
        open: false,
        tab: "overview",
        suites: [],
        runs: [],
        selectedRun: null,      // run.json 全量
        selectedRunId: null,
        selectedCaseId: null,
        baselines: [],
        error: null,
        runningId: null,        // 正在运行的 run_id
        runningStatus: null,
      },
      listeners: new Set(),
      set(patch) {
        // useSyncExternalStore 用 Object.is 比较快照：必须返回新引用才会重渲染
        this.state = Object.assign({}, this.state, patch);
        this.listeners.forEach(function (fn) { fn(); });
      },
      subscribe(fn) {
        this.listeners.add(fn);
        var self = this;
        return function () { self.listeners.delete(fn); };
      },
      get() { return this.state; },
    };
    function useStore() {
      return useSyncExternalStore(store.subscribe.bind(store), store.get.bind(store));
    }

    // =====================================================================
    // api 层
    // =====================================================================
    async function apiFetch(path, opts) {
      var resp = await fetch(API + path, opts);
      var data = await resp.json().catch(function () { return {}; });
      if (!resp.ok) throw new Error(data.error || ("HTTP " + resp.status));
      return data;
    }
    var api = {
      suites: function () { return apiFetch("/suites"); },
      runs: function () { return apiFetch("/runs"); },
      run: function (id) { return apiFetch("/runs/" + id); },
      runStatus: function (id) { return apiFetch("/runs/" + id + "/status"); },
      runSuite: function (suiteFile, baseline) {
        return apiFetch("/runs", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ suite_file: suiteFile, baseline: baseline || undefined }),
        });
      },
      cancelRun: function (id) { return apiFetch("/runs/" + id + "/cancel", { method: "POST" }); },
      baselines: function (suiteId) { return apiFetch("/baselines?suite=" + encodeURIComponent(suiteId)); },
      suiteYaml: function (file) { return apiFetch("/suites/" + file + "/yaml"); },
    };

    // =====================================================================
    // utils
    // =====================================================================
    function fmtPct(v) { return v == null ? "-" : (v * 100).toFixed(1) + "%"; }
    function fmtMs(v) { return v == null ? "-" : v >= 1000 ? (v / 1000).toFixed(1) + "s" : Math.round(v) + "ms"; }
    function fmtNum(v) { return v == null ? "-" : typeof v === "number" ? v.toFixed(3) : String(v); }
    var STATE_META = {
      DONE: { color: "#047857", label: "通过" },
      PARTIAL: { color: "#D97706", label: "部分" },
      ERROR: { color: "#DC2626", label: "错误" },
      TIMEOUT: { color: "#DC2626", label: "超时" },
      CANCELLED: { color: "#6b7280", label: "已取消" },
    };
    function stateMeta(s) {
      return STATE_META[s] || { color: "#3B82F6", label: s || "进行中" };
    }
    var CLASS_META = {
      improvement: { color: "#047857", label: "提升" },
      stable: { color: "#6b7280", label: "稳定" },
      regression: { color: "#DC2626", label: "回归" },
    };

    // =====================================================================
    // 原子组件
    // =====================================================================
    function StatePill(props) {
      var meta = stateMeta(props.state);
      return h("span", { className: "rt-pill", style: { color: meta.color, borderColor: meta.color } },
        h("span", { className: "rt-dot", style: { background: meta.color } }), meta.label);
    }
    function Bullet(props) {
      // 当前值 vs 阈值 的紧凑条形（design-system：不用大数字英雄卡）
      var pct = props.threshold ? Math.min(100, (props.value / props.threshold) * 100) : 0;
      var ok = props.value >= (props.threshold || 0);
      return h("div", { className: "rt-bullet" },
        h("div", { className: "rt-bullet-label" }, props.label),
        h("div", { className: "rt-bullet-bar" },
          h("div", {
            className: "rt-bullet-fill",
            style: { width: pct + "%", background: ok ? "#047857" : "#DC2626" },
          })),
        h("div", { className: "rt-bullet-value" },
          fmtNum(props.value), props.threshold != null ? " / " + fmtNum(props.threshold) : ""));
    }
    function CaseStatusIcon(props) {
      var map = { passed: ["#047857", "✓"], failed: ["#DC2626", "✗"], error: ["#D97706", "!"], skipped: ["#6b7280", "—"] };
      var m = map[props.status] || map.skipped;
      return h("span", { style: { color: m[0], fontWeight: 700, marginRight: 6 } }, m[1]);
    }

    // =====================================================================
    // Tab: 总览
    // =====================================================================
    function OverviewTab() {
      var s = useStore();
      var latest = s.runs.find(function (r) { return r.summary; });
      if (!latest) {
        return h("div", { className: "rt-empty" }, "还没有运行记录——先到「测试套件」发起一次运行");
      }
      var sm = latest.summary || {};
      var gate = latest.gate;
      return h("div", null,
        h("div", { className: "rt-card" },
          h("div", { className: "rt-card-title" }, "最近运行 ", latest.run_id,
            "  ", h(StatePill, { state: latest.state }),
            gate && !gate.skipped ? h("span", {
              className: "rt-pill",
              style: { color: gate.passed ? "#047857" : "#DC2626", marginLeft: 8 },
            }, gate.passed ? "Gate PASS" : "Gate FAIL") : null),
          h("div", { className: "rt-bullet-row" },
            h(Bullet, { label: "通过率", value: sm.pass_rate || 0, threshold: 0.95 }),
            h(Bullet, { label: "recall@5", value: (sm.metrics_avg || {}).recall_at_5 || 0, threshold: 0.9 }),
            h(Bullet, { label: "mrr", value: (sm.metrics_avg || {}).mrr || 0, threshold: 0.6 }),
            h(Bullet, { label: "search p95", value: (sm.latency || {}).search_p95_ms || 0, threshold: 2000 })),
          gate && gate.violations && gate.violations.length > 0
            ? h("div", { className: "rt-alert", role: "alert" },
                "质量门违规：" + gate.violations.map(function (v) {
                  return v.section + "." + v.metric + " " + v.expr + "（实际 " + v.actual + "）";
                }).join("；"))
            : null),
        h(FailedCasesCard, { runs: s.runs }));
    }

    function FailedCasesCard(props) {
      var s = useStore();
      var latestWithDetail = s.runs.find(function (r) { return r.summary && r.summary.failed > 0; });
      if (!latestWithDetail) return null;
      return h("div", { className: "rt-card" },
        h("div", { className: "rt-card-title" }, "最近失败",
          h("button", {
            className: "rt-link",
            onClick: function () {
              store.set({ tab: "runs", selectedRunId: latestWithDetail.run_id });
              loadRunDetail(latestWithDetail.run_id);
            },
          }, "查看运行 →")));
    }

    // =====================================================================
    // Tab: 运行记录
    // =====================================================================
    function RunsTab() {
      var s = useStore();
      useEffect(function () { refreshRuns(); }, []);
      // 运行中 2s 轮询
      useEffect(function () {
        if (!s.runningId) return;
        var timer = setInterval(function () {
          api.runStatus(s.runningId).then(function (st) {
            store.set({ runningStatus: st });
            var terminal = ["DONE", "PARTIAL", "ERROR", "TIMEOUT", "CANCELLED"].indexOf(st.state) >= 0;
            if (terminal) {
              store.set({ runningId: null, runningStatus: null });
              refreshRuns();
            }
          }).catch(function () {});
        }, 2000);
        return function () { clearInterval(timer); };
      }, [s.runningId]);

      if (s.selectedRun) return h(RunDetailView, null);

      return h("div", null,
        h("div", { className: "rt-toolbar" },
          h("button", { className: "rt-btn", onClick: refreshRuns }, "刷新")),
        s.runningId && s.runningStatus
          ? h("div", { className: "rt-card rt-running" },
              h("div", null,
                h("b", null, "运行中 "), s.runningId,
                h("button", {
                  className: "rt-btn rt-btn-danger", style: { marginLeft: 12 },
                  onClick: function () { api.cancelRun(s.runningId).catch(function () {}); },
                }, "取消")),
              h("div", { className: "rt-progress" },
                h("div", {
                  className: "rt-progress-fill",
                  style: {
                    width: ((s.runningStatus.progress || {}).total
                      ? (100 * s.runningStatus.progress.done / s.runningStatus.progress.total) + "%"
                      : "10%"),
                  },
                })),
              h("div", { className: "rt-muted" },
                "状态: " + s.runningStatus.state
                + (s.runningStatus.current_case ? " · 当前: " + s.runningStatus.current_case : "")))
          : null,
        s.runs.length === 0
          ? h("div", { className: "rt-empty" }, "还没有运行记录——先到「测试套件」发起一次运行")
          : h("table", { className: "rt-table" },
              h("thead", null, h("tr", null,
                h("th", null, "状态"), h("th", null, "Run ID"), h("th", null, "套件"),
                h("th", null, "通过"), h("th", null, "Gate"), h("th", null, "开始时间"))),
              h("tbody", null, s.runs.map(function (r) {
                return h("tr", {
                  key: r.run_id, className: "rt-row",
                  onClick: function () { loadRunDetail(r.run_id); },
                },
                  h("td", null, h(StatePill, { state: r.state })),
                  h("td", { className: "rt-mono" }, r.run_id),
                  h("td", null, r.suite_id || "-"),
                  h("td", null, r.summary
                    ? r.summary.passed + "/" + r.summary.total + " (" + fmtPct(r.summary.pass_rate) + ")"
                    : "-"),
                  h("td", null, r.gate && !r.gate.skipped
                    ? h("span", { style: { color: r.gate.passed ? "#047857" : "#DC2626" } },
                        r.gate.passed ? "PASS" : "FAIL")
                    : "-"),
                  h("td", { className: "rt-muted" }, r.started_at || "-"));
              }))));
    }

    function loadRunDetail(runId) {
      api.run(runId).then(function (run) {
        store.set({ selectedRun: run, selectedRunId: runId, selectedCaseId: null });
      }).catch(function (e) { store.set({ error: String(e) }); });
    }
    function refreshRuns() {
      api.runs().then(function (d) { store.set({ runs: d.runs || [] }); })
        .catch(function (e) { store.set({ error: String(e) }); });
    }

    // 运行详情（失败分析核心页：左 case 列表 + 右详情）
    function RunDetailView() {
      var s = useStore();
      var run = s.selectedRun;
      var cases = run.cases || [];
      var selected = cases.find(function (c) { return c.case_id === s.selectedCaseId; })
        || cases.find(function (c) { return c.status === "failed" || c.status === "error"; })
        || cases[0];
      var gate = run.gate;
      return h("div", null,
        h("div", { className: "rt-toolbar" },
          h("button", {
            className: "rt-btn",
            onClick: function () { store.set({ selectedRun: null, selectedRunId: null }); },
          }, "← 返回列表"),
          h("span", { className: "rt-mono", style: { marginLeft: 12 } }, run.run_id),
          h("span", { style: { marginLeft: 8 } }, h(StatePill, { state: run.lifecycle && run.lifecycle.length
            ? run.lifecycle[run.lifecycle.length - 1].state : "?" }))),
        gate && !gate.skipped && gate.violations && gate.violations.length
          ? h("div", { className: "rt-alert", role: "alert" },
              "质量门失败：" + gate.violations.map(function (v) {
                return v.section + "." + v.metric + " " + v.expr + "（实际 " + v.actual + "）";
              }).join("；"))
          : null,
        h("div", { className: "rt-split" },
          // 左：case 列表
          h("div", { className: "rt-pane rt-pane-left" },
            cases.map(function (c) {
              return h("div", {
                key: c.case_id,
                className: "rt-case-item" + (selected && c.case_id === selected.case_id ? " active" : ""),
                onClick: function () { store.set({ selectedCaseId: c.case_id }); },
              },
                h(CaseStatusIcon, { status: c.status }),
                h("b", null, c.case_id), " ", c.name,
                h("div", { className: "rt-muted", style: { fontSize: 12 } },
                  (c.metrics || []).filter(function (m) { return !m.passed && !m.skipped; })
                    .map(function (m) { return m.name + " " + fmtNum(m.value); }).join(" · ")));
            })),
          // 右：case 详情
          h("div", { className: "rt-pane rt-pane-right" },
            selected ? h(CaseDetail, { c: selected }) : h("div", { className: "rt-empty" }, "选择左侧 case"))));
    }

    function CaseDetail(props) {
      var c = props.c;
      var ret = c.retrieval;
      var gen = c.generation;
      var trace = c.trace || {};
      var children = [];

      // 头部卡片
      children.push(h("div", { className: "rt-card", key: "hdr" },
        h("div", { className: "rt-card-title" },
          h(CaseStatusIcon, { status: c.status }), " ", c.case_id, " ", c.name,
          c.severity === "critical"
            ? h("span", { className: "rt-pill", style: { color: "#DC2626", marginLeft: 8 } }, "critical")
            : null),
        ret ? h("div", { className: "rt-muted" }, "查询: ", h("code", null, ret.query),
          "  ·  top_k=" + ret.top_k + "  ·  " + fmtMs(ret.latency_ms),
          ret.degraded ? h("span", { style: { color: "#D97706" } },
            "  ·  degraded: " + (ret.degraded_reason || "")) : null) : null,
        gen ? h("div", { className: "rt-muted" }, "E2E 耗时 " + fmtMs(gen.latency_ms),
          gen.usage ? "  ·  tokens " + gen.usage.input_tokens + "+" + gen.usage.output_tokens : null) : null));

      // 指标表
      if ((c.metrics || []).length) {
        children.push(h("div", { className: "rt-card", key: "metrics" },
          h("div", { className: "rt-card-title" }, "指标"),
          h("table", { className: "rt-table" },
            h("thead", null, h("tr", null,
              h("th", null, ""), h("th", null, "指标"), h("th", null, "值"),
              h("th", null, "阈值"), h("th", null, "说明"))),
            h("tbody", null, c.metrics.map(function (m, i) {
              return h("tr", {
                key: i,
                className: m.skipped ? "rt-muted" : (m.passed ? "" : "rt-row-fail"),
              },
                h("td", null, m.skipped ? "—" : (m.passed ? "✓" : "✗")),
                h("td", null, m.name),
                h("td", { className: "rt-mono" }, fmtNum(m.value)),
                h("td", { className: "rt-mono" }, m.threshold != null ? fmtNum(m.threshold) : "-"),
                h("td", { className: "rt-muted" }, m.detail));
            })))));
      }

      // 归因（E2E case）
      if (trace.attribution) {
        var attrChildren = [
          h("div", { className: "rt-card-title", key: "t" }, "归因"),
          h("div", { key: "badge" }, h("span", {
            className: "rt-pill",
            style: { color: trace.attribution === "ok" ? "#047857" : "#D97706" },
          }, trace.attribution)),
        ];
        if ((trace.agent_tool_calls || []).length) {
          attrChildren.push(h("table", { className: "rt-table", key: "tools" },
            h("thead", null, h("tr", null,
              h("th", null, "工具"), h("th", null, "参数"), h("th", null, "chunks"),
              h("th", null, "命中"), h("th", null, "错误"))),
            h("tbody", null, trace.agent_tool_calls.map(function (t, i) {
              return h("tr", { key: i },
                h("td", { className: "rt-mono" }, t.tool),
                h("td", { className: "rt-mono", style: { fontSize: 11 } },
                  JSON.stringify(t.args).slice(0, 80)),
                h("td", null, t.chunk_count == null ? "-" : t.chunk_count),
                h("td", { className: "rt-mono", style: { fontSize: 11 } },
                  (t.hit_doc_ids || []).join(", ").slice(0, 60)),
                h("td", null, t.is_error ? "✗" : ""));
            }))));
        }
        children.push(h("div", { className: "rt-card", key: "attr" }, attrChildren));
      }

      // 检索明细
      if (ret && ret.chunks && ret.chunks.length) {
        children.push(h("div", { className: "rt-card", key: "chunks" },
          h("div", { className: "rt-card-title" }, "检索结果 Top-" + ret.chunks.length),
          h("table", { className: "rt-table" },
            h("thead", null, h("tr", null,
              h("th", null, "#"), h("th", null, "文档"), h("th", null, "Score"), h("th", null, "内容"))),
            h("tbody", null, ret.chunks.map(function (ch, i) {
              return h("tr", { key: i },
                h("td", null, ch.rank),
                h("td", { className: "rt-mono", style: { fontSize: 11 } },
                  ch.logical_doc || (ch.document_id || "").slice(0, 8)),
                h("td", { className: "rt-mono" }, (ch.score || 0).toFixed(3)),
                h("td", { className: "rt-muted", style: { fontSize: 12 } },
                  (ch.content_preview || "").slice(0, 60) + "…"));
            })))));
      }

      // 生成回答
      if (gen && gen.answer) {
        children.push(h("div", { className: "rt-card", key: "answer" },
          h("div", { className: "rt-card-title" }, "回答"),
          h("pre", { className: "rt-pre" }, gen.answer)));
      }

      // trace unavailable 诚实标注
      if (trace.unavailable && trace.unavailable.length) {
        children.push(h("div", {
          className: "rt-muted", key: "unavail", style: { fontSize: 12, marginTop: 8 },
        }, "trace 不可得项: " + trace.unavailable.join(", ")));
      }

      return h("div", null, children);
    }

    // Tab: 测试套件
    // =====================================================================
    function SuitesTab() {
      var s = useStore();
      var yamlState = useState(null);
      var yaml = yamlState[0];
      var setYaml = yamlState[1];
      useEffect(function () { refreshSuites(); }, []);
      function refreshSuites() {
        api.suites().then(function (d) { store.set({ suites: d.suites || [] }); })
          .catch(function (e) { store.set({ error: String(e) }); });
      }
      function runSuite(file) {
        api.runSuite(file).then(function (d) {
          store.set({ runningId: d.run_id, tab: "runs", selectedRun: null });
          refreshRuns();
        }).catch(function (e) { store.set({ error: String(e) }); });
      }
      return h("div", null,
        s.suites.length === 0
          ? h("div", { className: "rt-empty" }, "未发现测试套件（suites/golden/*.yaml）")
          : s.suites.map(function (suite) {
              return h("div", { className: "rt-card", key: suite.id },
                h("div", { className: "rt-card-title" },
                  suite.id, " ",
                  h("span", { className: "rt-muted" }, suite.name || "")),
                h("div", { className: "rt-muted" },
                  (suite.tags || []).join(" / "), "  ·  ", suite.case_count + " cases",
                  suite.defect ? "  ·  缺陷套件（不进质量门）" : ""),
                h("div", { style: { marginTop: 8 } },
                  h("button", {
                    className: "rt-btn rt-btn-primary",
                    disabled: !!s.runningId,
                    onClick: function () { runSuite(suite.file); },
                  }, s.runningId ? "运行中…" : "▶ 运行"),
                  h("button", {
                    className: "rt-btn", style: { marginLeft: 8 },
                    onClick: function () {
                      api.suiteYaml(suite.file).then(function (text) {
                        setYaml({ file: suite.file, text: text });
                      }).catch(function () {});
                    },
                  }, "查看 YAML")));
            }),
        yaml ? h("div", {
            className: "rt-modal-mask", role: "dialog",
            onClick: function () { setYaml(null); },
          },
            h("div", { className: "rt-modal", onClick: function (e) { e.stopPropagation(); } },
              h("div", { className: "rt-card-title" }, yaml.file),
              h("pre", { className: "rt-pre rt-modal-body" }, yaml.text),
              h("button", { className: "rt-btn", onClick: function () { setYaml(null); } }, "关闭")))
          : null);
    }

    // =====================================================================
    // Tab: Baseline
    // =====================================================================
    function BaselineTab() {
      var s = useStore();
      var latestWithDiff = s.runs.find(function (r) { return r.summary; });
      var run = s.selectedRun && s.selectedRun.baseline_diff ? s.selectedRun : null;
      if (!run && latestWithDiff) {
        // 懒加载最新 run 详情
        api.run(latestWithDiff.run_id).then(function (full) {
          store.set({ selectedRun: full });
        }).catch(function () {});
      }
      var diff = (s.selectedRun || {}).baseline_diff;
      if (!diff) {
        return h("div", { className: "rt-empty" },
          "暂无 baseline 对比数据（运行时加 --baseline main 生成）");
      }
      if (diff.comparable === false) {
        return h("div", { className: "rt-alert", role: "alert" },
          "⚠️ 与 baseline 不可比（incomparable）：",
          h("ul", null, (diff.incomparable_reasons || []).map(function (r, i) {
            return h("li", { key: i }, r);
          })));
      }
      var rows = (diff.pass_rate ? [diff.pass_rate] : []).concat(diff.metrics || []);
      return h("div", { className: "rt-card" },
        h("div", { className: "rt-card-title" }, "Baseline 对比"),
        h("table", { className: "rt-table" },
          h("thead", null, h("tr", null,
            h("th", null, "指标"), h("th", null, "Baseline"), h("th", null, "Current"),
            h("th", null, "Δ"), h("th", null, "分类"))),
          h("tbody", null, rows.map(function (m, i) {
            var meta = CLASS_META[m.classification] || CLASS_META.stable;
            return h("tr", { key: i },
              h("td", null, m.name),
              h("td", { className: "rt-mono" }, fmtNum(m.baseline)),
              h("td", { className: "rt-mono" }, fmtNum(m.current)),
              h("td", { className: "rt-mono" }, m.delta_pct != null
                ? (m.delta_pct * 100).toFixed(1) + "%"
                : (m.delta != null ? m.delta.toFixed(3) : "-")),
              h("td", { style: { color: meta.color } }, meta.label));
          }))));
    }

    // =====================================================================
    // Tab: 缺陷套件
    // =====================================================================
    function DefectsTab() {
      var s = useStore();
      var defectRuns = s.runs.filter(function (r) { return r.suite_id === "rag-defects"; });
      return h("div", null,
        h("div", { className: "rt-card", style: { borderLeft: "3px solid #3B82F6" } },
          "本套件记录已知产品缺陷证据，不参与质量门。缺陷已复现 = 证据有效；意外通过 = 缺陷疑似已修复。"),
        defectRuns.length === 0
          ? h("div", { className: "rt-empty" }, "还没有缺陷套件运行记录")
          : defectRuns.map(function (r) {
              return h("div", {
                key: r.run_id, className: "rt-card rt-row",
                onClick: function () { store.set({ tab: "runs" }); loadRunDetail(r.run_id); },
              },
                h(StatePill, { state: r.state }), " ",
                h("span", { className: "rt-mono" }, r.run_id), " ",
                h("span", { className: "rt-muted" }, r.started_at || ""));
            }));
    }

    // =====================================================================
    // Overlay 骨架
    // =====================================================================
    var TABS = [
      ["overview", "总览"],
      ["runs", "运行记录"],
      ["suites", "测试套件"],
      ["baseline", "Baseline"],
      ["defects", "缺陷套件"],
    ];

    function OverlayRoot() {
      var s = useStore();
      // Esc 分层关闭
      useEffect(function () {
        if (!s.open) return;
        function onKey(e) {
          if (e.key === "Escape") store.set({ open: false, selectedRun: null });
        }
        window.addEventListener("keydown", onKey);
        return function () { window.removeEventListener("keydown", onKey); };
      }, [s.open]);
      // 打开时拉数据
      useEffect(function () {
        if (s.open) { refreshRuns(); }
      }, [s.open]);
      if (!s.open) return null;
      return h("div", { className: "rt-overlay" },
        h("div", { className: "rt-topbar" },
          h("b", null, "RAG 测试平台"),
          h("span", { style: { flex: 1 } }),
          s.error ? h("span", { style: { color: "#DC2626", marginRight: 12, fontSize: 12 } }, s.error) : null,
          h("button", {
            className: "rt-btn",
            onClick: function () { store.set({ open: false, selectedRun: null, error: null }); },
          }, "✕ 关闭")),
        h("div", { className: "rt-tabs" },
          TABS.map(function (t) {
            return h("button", {
              key: t[0],
              className: "rt-tab" + (s.tab === t[0] ? " active" : ""),
              "aria-pressed": s.tab === t[0],
              onClick: function () { store.set({ tab: t[0], selectedRun: null }); },
            }, t[1]);
          })),
        h("div", { className: "rt-content" },
          s.tab === "overview" ? h(OverviewTab) :
          s.tab === "runs" ? h(RunsTab) :
          s.tab === "suites" ? h(SuitesTab) :
          s.tab === "baseline" ? h(BaselineTab) :
          h(DefectsTab)));
    }

    // =====================================================================
    // 入口：经由 agentloop「菜单」下拉的 RAG 测试 条目（window.__RAG_TESTING_OPEN__）打开
    // =====================================================================
    // =====================================================================
    // 样式（design-system token：密度 9/10、8px 节奏、Phosphor 前禁 emoji）
    // =====================================================================
    var CSS = `
.rt-menu-btn { display:flex; align-items:center; gap:6px; width:100%; padding:6px 10px;
  border:none; background:none; cursor:pointer; font-size:13px; color:inherit; text-align:left; }
.rt-menu-btn:hover { background: rgba(0,0,0,0.06); border-radius:6px; }
.rt-dot { width:8px; height:8px; border-radius:50%; display:inline-block; margin-right:2px; }
.rt-overlay { position:fixed; inset:0; background:#F8FAFC; z-index:1000; display:flex;
  flex-direction:column; font-size:13px; color:#1E3A8A; }
.rt-topbar { display:flex; align-items:center; padding:8px 16px; background:#fff;
  border-bottom:1px solid #e5e7eb; }
.rt-tabs { display:flex; gap:2px; padding:0 16px; background:#fff; border-bottom:1px solid #e5e7eb; }
.rt-tab { padding:8px 14px; border:none; background:none; cursor:pointer; font-size:13px;
  color:#6b7280; border-bottom:2px solid transparent; }
.rt-tab.active { color:#1E40AF; border-bottom-color:#1E40AF; font-weight:600; }
.rt-content { flex:1; overflow:auto; padding:12px 16px; }
.rt-card { background:#fff; border:1px solid #e5e7eb; border-radius:6px;
  padding:12px 16px; margin-bottom:8px; }
.rt-card-title { font-weight:600; margin-bottom:8px; }
.rt-table { width:100%; border-collapse:collapse; }
.rt-table th { text-align:left; font-weight:500; color:#6b7280; padding:4px 8px;
  border-bottom:1px solid #e5e7eb; font-size:12px; }
.rt-table td { padding:6px 8px; border-bottom:1px solid #f3f4f6; }
.rt-row { cursor:pointer; } .rt-row:hover { background:#F1F5F9; }
.rt-row-fail { background:#FEF2F2; }
.rt-pill { display:inline-flex; align-items:center; gap:4px; padding:1px 8px;
  border:1px solid; border-radius:10px; font-size:12px; }
.rt-btn { padding:4px 12px; border:1px solid #d1d5db; background:#fff; border-radius:4px;
  cursor:pointer; font-size:12px; }
.rt-btn:hover { background:#F1F5F9; }
.rt-btn:disabled { opacity:0.5; cursor:not-allowed; }
.rt-btn-primary { background:#1E40AF; color:#fff; border-color:#1E40AF; }
.rt-btn-primary:hover { background:#1e3a8a; }
.rt-btn-danger { color:#DC2626; border-color:#DC2626; }
.rt-link { border:none; background:none; color:#3B82F6; cursor:pointer; font-size:12px; }
.rt-empty { padding:40px; text-align:center; color:#6b7280; }
.rt-muted { color:#6b7280; }
.rt-mono { font-family:ui-monospace,monospace; font-size:12px; }
.rt-pre { background:#F8FAFC; border:1px solid #e5e7eb; border-radius:4px; padding:8px;
  font-size:12px; white-space:pre-wrap; max-height:300px; overflow:auto; }
.rt-toolbar { display:flex; align-items:center; margin-bottom:8px; }
.rt-alert { background:#FEF2F2; border:1px solid #DC2626; color:#DC2626; border-radius:6px;
  padding:8px 12px; margin-bottom:8px; }
.rt-split { display:flex; gap:8px; height:100%; }
.rt-pane-left { width:28%; overflow:auto; }
.rt-pane-right { flex:1; overflow:auto; }
.rt-case-item { padding:6px 10px; border-radius:4px; cursor:pointer; margin-bottom:2px;
  background:#fff; border:1px solid #e5e7eb; }
.rt-case-item.active { border-color:#1E40AF; background:#EFF6FF; }
.rt-bullet-row { display:flex; gap:16px; flex-wrap:wrap; }
.rt-bullet { min-width:180px; }
.rt-bullet-label { font-size:12px; color:#6b7280; }
.rt-bullet-bar { height:6px; background:#e5e7eb; border-radius:3px; margin:4px 0; }
.rt-bullet-fill { height:100%; border-radius:3px; transition:width 150ms; }
.rt-bullet-value { font-family:ui-monospace,monospace; font-size:12px; }
.rt-progress { height:6px; background:#e5e7eb; border-radius:3px; margin:8px 0; }
.rt-progress-fill { height:100%; background:#3B82F6; border-radius:3px; transition:width 200ms; }
.rt-running { border-left:3px solid #3B82F6; }
.rt-modal-mask { position:fixed; inset:0; background:rgba(0,0,0,0.4); z-index:1100;
  display:flex; align-items:center; justify-content:center; }
.rt-modal { background:#fff; border-radius:8px; padding:16px; width:70%; max-width:800px; }
.rt-modal-body { max-height:60vh; }
@media (prefers-reduced-motion: reduce) { .rt-progress-fill, .rt-bullet-fill { transition:none; } }
`;
    function injectCss() {
      var el = document.createElement("style");
      el.textContent = CSS;
      document.head.appendChild(el);
      return function () { el.remove(); };
    }

    // =====================================================================
    // 装配
    // =====================================================================
    function apply(ctx) {
      ctx.effect(function () {
        return ctx.locale.register(NS, { zh: zh, en: en });
      }, "rag-testing: i18n");
      var disposeStyle = injectCss();

      ctx.slots.inject("shell.overlay", function () {
        return ctx.slots.register({
          name: "shell.overlay",
          id: "rag-testing",
          order: 20,
          locale: NS,
        }, OverlayRoot);
      });
      // 全局 opener：供 agentloop「菜单」里的 RAG 测试 条目调用（window.__RAG_TESTING_OPEN__）
      window.__RAG_TESTING_OPEN__ = function () {
        store.set({ open: true, selectedRun: null });
      };
      function onOpenEvent() { store.set({ open: true, selectedRun: null }); }
      window.addEventListener("rag-testing:open", onOpenEvent);
      return function () {
        if (window.__RAG_TESTING_OPEN__) window.__RAG_TESTING_OPEN__ = null;
        window.removeEventListener("rag-testing:open", onOpenEvent);
        disposeStyle && disposeStyle();
      };
    }

    exports.apply = apply;
    exports.inject = inject;
    exports.NS = NS;
    return module.exports;
  },
});
