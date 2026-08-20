# Overlay Page Overrides

> **PROJECT:** RAG Testing
> **Page Type:** DSH `shell.overlay` · 失败分析工作台

> 本页规则覆盖 MASTER。MASTER 里的 **Enterprise Gateway**（Hero / Contact Sales / Logo carousel）不适用于插件 overlay，已弃用。

---

## Page-Specific Rules

### Layout Overrides

- **形态：** 侧栏 footer 入口 + 中间栏全屏 overlay，不遮挡 DSH 侧栏
- **导航：** 两 Tab：运行 / 套件。无全局「套件选择器 + 运行」
- **默认落地：** 上次 golden 质量门失败的 run 详情，并选中第一个失败 case
- **主从：** 左 case 列表（默认过滤失败）+ 右详情。详情区独立滚动
- **Max Width:** overlay 铺满中间栏，不做 1400px 居中

### Spacing Overrides

- **Content Density:** 9/10 — 8px 节奏，表与主从优先，少用大卡片
- **KPI：** 用 bullet chart（当前值 vs baseline/阈值），不用大数字英雄卡

### Color Overrides

- 沿用 MASTER 海军蓝 / 灰 / 琥珀
- **失败 / 回归：** `--color-destructive` `#DC2626`，文案 + 图标，不只靠颜色
- **通过 / 提升：** `--color-success` `#047857`（MASTER 未给 success，本页补）
- **需要人处理（意外通过、设 baseline）：** `--color-accent` `#D97706`
- **缺陷证据：** 不用失败红；用 secondary + 横幅「不进质量门」

### Component Overrides

- 图标：Phosphor outline，禁止 emoji 当状态
- 交互芯片：`<button aria-pressed>`，禁止可点 div
- 质量门横幅：`role="alert"`，并提供「下一步」跳到失败 case
- Modal：`role="dialog"` + 可见焦点；Esc 先关弹层再关 overlay
- CTA：套件卡片「运行」为 Primary；详情「再跑一次」绑定当前 suite_id

### Motion Overrides

- 只用 150–200ms hover / 进度条。无 GSAP、无入场滑入
- `prefers-reduced-motion: reduce` 时停脉冲动画
