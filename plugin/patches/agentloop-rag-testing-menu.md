# agentloop 菜单集成补丁（RAG 测试 条目）

> 用户需求：RAG 测试 放进侧边栏底部「菜单」下拉，与 Agent测评 纵向排列。
> agentloop 的菜单弹层是私有组件（非槽位），唯一可行方式是补丁其 bundle。

## 改动位置

`/Users/chen/agentloop/dsh-plugin/lib/client.js`（web profile 里以 `link:` 安装指向该目录）

在 `appsItem`（Agent测评）按钮之后追加一个菜单项：

```js
// ── rag-testing 集成（dsh-rag-testing 插件注入，2026-08-21）──
h("button", {
  className: "al-menu-item",
  role: "menuitem",
  onClick: function () {
    setMenuOpen(false);
    if (window.__RAG_TESTING_OPEN__) window.__RAG_TESTING_OPEN__();
  },
}, appsIcon(16), h("span", null, "RAG 测试"))
```

## 协作契约

- agentloop 菜单项调用 `window.__RAG_TESTING_OPEN__()`（由 dsh-rag-testing 的
  client 在 apply 时暴露，dispose 时置空）
- dsh-rag-testing 不再注册 `sidebar.footer.action` 独立按钮（入口唯一化）

## 验证（playwright 实测 2026-08-21）

- 底部独立按钮数：0
- 菜单条目：`["Settings", "Agent Eval", "RAG 测试"]`（zh 会话下为 设置 / Agent测评 / RAG 测试）
- 点击 RAG 测试 → overlay 打开（五 Tab 正常）
- 无 JS 错误

## 维护注意

- ⚠️ agentloop 升级会覆盖此补丁（bundle 重编）。升级后按本文件重打即可。
- 若 agentloop 未来把菜单弹层改为槽位化（`sidebar.footer.menu` 之类），
  本补丁可废弃，改回纯 slot 注册。
