# DSH 架构分析报告（面向 RAG Testing Plugin 设计）

> 调查对象：`/Users/chen/.npm/_npx/1e7f6d9597241db0/node_modules/@deepseek-ai/`（下文记 `$NM`）与示例插件 `/Users/chen/deepseek_harness_plugins/dsh-client-plugin-manager/`（下文记 `$PM`）。结论均标注来源文件。

## 1. DSH 总体架构

### 1.1 Cordis 插件机制
- Cordis 是 TS 插件框架：`new Context()` 建根依赖容器；`ctx.plugin()` 启动插件返回 Fiber；插件声明 `inject: [...]` 等待所需服务；fiber dispose 时其效果、事件监听、服务一并回收（`$NM/cordis/README.md`）。
- 插件形态：导出 `apply(ctx)`（host 入口惯例，见 `$PM/lib/index.js`）或 `Service` 子类。服务通过 `declare module 'cordis' { interface Context {...} }` 声明合并挂到 ctx（如 `webServer`，见 `$NM/dsh-host-webserver/lib/types/index.d.ts`）。
- Loader（`@deepseek-ai/cordis-plugin-loader`）以条目树治理插件：entry 字段 `{id, name, config, group, disabled, inject}`（`$NM/cordis-plugin-loader/README.md`）。HMR 由 `cordis-plugin-hmr` 提供，配置层热更新由 `watchUserPatches` 提供（`$NM/dsh-app-boot/README.md`）。

### 1.2 dsh CLI 启动与 profile
- `dsh web` 是 `--profile web` 的别名；`dsh plugin --profile <name> <pnpm args>` 转发 pnpm 管理 profile 插件（`$NM/dsh/README.md`）。
- profile 目录 `~/.dsh/profiles/<name>` 含 `package.json`（`dsh.profile.bundles` 有序 bundle 列表 + file: 依赖）和用户 `cordis.patch.yml`。组合顺序：各 bundle patch（按序）→ profile patch → 家目录 patch → `--patch`（`$NM/dsh/README.md`、`$NM/dsh-app-boot/README.md`）。web profile 自动从模板初始化。
- bundle = package.json 声明 `"dsh": {"bundle": {"patch": "./cordis.patch.yml"}}` 的包。基础能力在 `dsh-base`，web 面在 `dsh-web-app`（插入 webserver、API gateway、storage、client-hmr 等行，`$NM/dsh-web-app/README.md`）。

### 1.3 Web shell 与 client plugin
- 浏览器端模块系统 `dsh-client-modules`：lazy CJS 模型，bundle 执行仅调 `window.__ModuleLoader__.load({id, factory})` 注册工厂，物化时才执行（`$NM/dsh-client-modules/README.md`）。
- Node 半扫描启用的 Loader 条目中带 `dsh.client` 元数据的包，解析其 `exports["./client"]`，把构建产物哈希进 boot graph（即 `window.__DSH_BOOT__`），经 `/plugins/<id>/client.js` 提供；boot manifest 通过 webserver 的 `tapIndex` 注入 index.html（`$NM/dsh-host-frontend-static/README.md`）。
- shell 内核 `dsh-client-web`：`new AppWebEntry(el).run()` 两阶段启动——先建模块系统并并行 prefetch `immediately` 层，再挂载 vendored cordis Loader 逐条目物化，全部 ACTIVE 后一次性显示 UI（`$NM/dsh-client-web/README.md`）。
- 打包用 tsdown（`clientBundle`），共享模块由 `PLATFORM_MODULES` 定义：react、react-dom、@deepseek-ai/cordis、dsh-client-ui-slots、dsh-client-web-react、dsh-client-ui-primitives、dsh-client-schema-form（`$NM/dsh-client-web/lib/index.js` L423）。插件 bundle 里 `require("react")` 等走共享实例。
- client HMR：`dsh-client-hmr` 浏览器半订阅 SSE `GET /plugins/events`，Node 半 stat 轮询 bundle 文件哈希，变化即 invalidate→prefetch→重建 fiber；需 `pnpm run dev:web` watcher 重写 bundle 才生效（`$NM/dsh-client-hmr/README.md`）。

### 1.4 服务端 vs 客户端插件
- 一个 DSH 包可同时有两半：`lib/index.js`（host cordis 插件）+ `lib/client.js`（浏览器 bundle），package.json 里 `dsh.client: {inject: [共享包...], platform: "web", immediately?}` 声明客户端装配（范例：`$PM/package.json`、`$NM/dsh-client-ui-jobs/package.json`）。
- host↔browser RPC 走 Typert：host 侧 `ctx.typertGateway`，浏览器侧 `ctx.remote`（`$NM/dsh-api-gateway/README.md`）。`dsh-host-*` 是宿主基础设施：webserver（HTTP/upgrade 路由注册）、frontend-static（SPA dist 服务）、plugin-inventory（只读插件清单 Remote）。

## 2. 插件开发方式（以 plugin-manager 为范式）

### 2.1 目录结构与加载
`$PM` 的结构即最小范式：
- `package.json`：`main` 指 host 入口；`exports["./client"]` 指浏览器 bundle；`dsh.bundle.patch` 指注册片段；`dsh.client.inject/platform/immediately` 声明客户端依赖（`$PM/package.json`）。
- `lib/index.js`：host 入口，纯客户端插件可空 `export function apply(){}`（Loader 要求存在）。
- `lib/client.js`：手写 `window.__ModuleLoader__.load({id, factory})` 格式，无构建步骤亦可（`$PM/lib/client.js` L15）。
- `cordis.patch.yml`：`- insert: [{id, name}]`（`$PM/cordis.patch.yml`）。
- 安装 = `pnpm add -w <dir>`（在 profile 目录）+ 把 insert 片段追加进 `~/.dsh/profiles/web/cordis.patch.yml` + 重启（`$PM/install.sh`）。host 会 watch 用户 patch 层热应用启停（`$PM/helper/server.mjs` 注释，`watchUserPatches`）。

### 2.2 UI 页面/路由注册（slot 系统）
DSH 没有 URL 路由器，UI 是 slot 组合（导航态在 client-runtime sessions 服务，`$NM/dsh-client-runtime/README.md`）：
- `ctx.slots.register({name, id, order, label, inject}, Component)` 向已声明槽位贡献 React 组件；`ctx.slots.inject(name, cb)` 等槽位声明存在后再注册（`$NM/dsh-client-ui-slots/README.md`）。
- 范例：`$PM/lib/client.js` L698-721 向 `settings.plugins.tab` 注册一个设置标签页；`ctx.locale.register(NS, {zh, en})` 注册 i18n 词典。
- 可用槽位（`$NM/dsh-client-ui-settings/lib/types/client/contract/slots.d.ts`、`$NM/dsh-client-ui-layout/lib/types/client/index.d.ts`）：`settings.section`（**一个功能一页的设置页**，list 型，最适合第三方整页）、`settings.plugins.tab`（插件区内标签页）、`sidebar`/`conversation`/`details`（single 型，注册即**替换**原占用者）、`conversation.*`、`hero`、`home` 等。
- 参考实现：`@deepseek-ai/dsh-client-ui-jobs`（向 `conversation.session.header.actions` 贡献条目，其 package.json `dsh.client.inject` 即客户端依赖声明范式）、`@deepseek-ai/dsh-client-ui-settings-plugins`（声明 `settings.plugins.tab` 并贡献 tab，`$NM/dsh-client-ui-settings-plugins/README.md`）。

### 2.3 服务端插件暴露 HTTP API（两条路径）
- **原始 HTTP 路由**（最简单，推荐）：host 插件 `inject: ['webServer']` 后 `ctx.webServer.register({kind:'exact'|'prefix', path, handler(req,res)})`，可挂 SSE；重复路径抛错，返回 disposer（`$NM/dsh-host-webserver/lib/types/index.d.ts`）。浏览器直接 fetch 即可。
- **Typert Remote**（类型安全 RPC）：host 服务 `extends TypertRemoteService` + `@Remote` 装饰方法，客户端 `ctx.remote.$mount(contribution)` 挂载（`$NM/dsh-typert-protocol/README.md`、`$NM/dsh-api-gateway/README.md`）。但 strict contribution 的 zod 描述符由 `@deepseek-ai/dsh-typert-generator` 构建期生成（产物样例 `$NM/dsh-goal/lib/typert.remote-client.js`），**该生成器未随 npm 发布**——第三方插件手写描述符理论上可仿制，未确认有公开文档支持。
- 事件下发受 `API_REMOTE_FORWARDED_EVENTS` 白名单控制，硬编码在 `dsh-api-remotes` 源码（其 README "Forwarded Host events" 节），第三方新增事件需改该包或走自有 SSE 路由。

## 3. 可复用能力清单（均已在 dsh-base / dsh-web-app 组合中）

| 能力 | 服务 | 关键事实与来源 |
|---|---|---|
| 配置管理 | `ctx.settings` | `register(ns, schema, {base})` 得 `SettingsScope`（get/watch/update），三层解析：schema 默认→组合 base→用户文档；file provider YAML/JSON 热 watch、原子写、保留注释（`$NM/dsh-settings/README.md`、`dsh-settings-file/README.md`） |
| 持久化存储 | `ctx.storageDomain` / `ctx.storage` | `defineDomain`（zod schema）+ `open`；读同步、写串行、先落盘后更新内存并发 `domain/changed`；json backend 一单元一 `<unit>.json`，temp-write+rename 原子替换。**插件可持久化自有 JSON**（`$NM/dsh-storage-domain/README.md`、`dsh-storage-json/README.md`；web bundle 已组合 storage 行） |
| Web UI 组件 | ui-primitives | Button/Pill/Menu/Modal/Input、Toast、MarkdownText、JsonTree、StateDot、TerminalBlock 等纯 React 原子，可 `require("@deepseek-ai/dsh-client-ui-primitives")`（`$NM/dsh-client-ui-primitives/README.md`，在 PLATFORM_MODULES 内） |
| 后台任务 | `ctx.jobs` | start/get/list/read/kill/wait/onJobDone；owner 隔离；进程内实现 dsh-jobs-local（`$NM/dsh-jobs/README.md`、`dsh-jobs-local/README.md`） |
| LLM 调用 | `ctx.llm` | `stream(options)` 流式调用、`prepareCall` 一次性解析模型与重试策略；复用已配置的 provider/适配器，**可直接用于 LLM Judge**（`$NM/dsh-llm/README.md`） |
| 子进程 | `ctx.subprocess` | `spawn(spec)` 显式 argv（不经 shell，要 shell 自己传 `['bash','-c',...]`）、树级 terminate、collect 模式限界缓冲+spill；**可 spawn python**（`$NM/dsh-subprocess/README.md`；local 实现在 base） |

浏览器侧另有 `ctx.settingsScope.bind(spec)`（Host settings 的浏览器绑定，带 revision 冲突防护，`$NM/dsh-client-ui-settings/README.md`）；简单状态也可用 localStorage（`$PM` 做法）。

## 4. 限制与缺口

1. **无独立顶层页面/URL 路由**：第三方插件能安全使用的整页入口只有 `settings.section`（设置面板内一页）和 `settings.plugins.tab`。`sidebar`/`conversation` 是 single 槽，注册会替换整个导航列/会话区（`$NM/dsh-client-ui-layout/lib/types/client/index.d.ts` 注释明确 "registering here replaces…"）；新顶层子槽的声明权属于槽占用者（ui-layout），第三方无法声明。结论：RAG 测试平台页面适合做成 `settings.section` 大页，或接受"设置内应用"形态。
2. **Typert 生成器未发布**：新增严格 Remote 需手写 zod InvocationDescriptor（可仿 `$NM/dsh-goal/lib/typert.remote-client.js`），未确认有官方支持路径；务实选择是 `ctx.webServer.register` 原始路由 + 浏览器 fetch/SSE。
3. **host→client 事件白名单封闭**：`API_REMOTE_FORWARDED_EVENTS` 在 dsh-api-remotes 源码内，插件自定义推送需走自有 HTTP/SSE 路由。
4. **无现成测试领域能力**：test case 管理、测试运行编排、报告存储均无对应包，需自建（持久化可落在 `ctx.storageDomain` + json backend）。
5. **其他边界**：webServer 无 TLS/auth/origin 策略（`dsh-host-webserver` README "Known Limitations"）；settings RPC 仅 loopback 可用；client HMR 无失败回滚、React 状态丢失（`dsh-client-hmr` README）。
