# DEV-034 · 专用窗口完整显示修复

类别：实现；日期：2026-10-31（Asia/Shanghai）。

实际复现旧代码viewport=None仍创建1280×720模拟视口、1296×807普通窗口，start-maximized未生效。改用no_viewport=True并通过当前page的CDP窗口ID核验最大化，截图前检查完整画布边界；截断/无尺寸直接InputDeferred，不允许locator.screenshot滚动后给出不对应可见区域的坐标。面板新增恢复完整窗口，先停止接管再最大化。

实现：browser.py、app.py、panel.html、tests/test_browser_layout.py。没有调整客户端游戏状态或其他浏览器窗口。

验证：35项pytest与ruff通过，新增画布右/下/上边界及零尺寸拒绝、最小化恢复状态测试。真实雀魂大厅在DPR1.25下内视口1536×826、canvas约1468×825.6完整可见；手动缩小、控制接口恢复、后台最小化一帧及canvas截图均通过。证据留runtime/window-size-verification.json和window-loaded-fixed.png；不是对所有显示器/皮肤与实战点击效果的完整验收，后者留VAL-003。

实际实现提交由后续索引绑定。回退源码后重启专用浏览器；用户不必删除profile或牌谱。
