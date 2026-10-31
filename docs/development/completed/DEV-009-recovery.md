# DEV-009 · 断线恢复、旧窗口失效与去重

类别：实现。完成日期：2026-10-31（Asia/Shanghai）。

断线立即撤销旧窗口；完整动作恢复幂等归并。真实同场重载收到128动作，passed_waiting_time=189秒，与原窗口年龄188.359秒吻合。已校准路径额外扣1秒，保留原时间池下界；过期或不完整恢复不开放。浏览器独立于服务运行，重绑核验PID、exe、专用profile与唯一同源页面。

实现位置：src/majsoul_llm/engine.py、browser.py、app.py。

验证与证据：tests/test_runtime_lifecycle.py、tests/test_core.py、tests/test_browser_layout.py；runtime/reload-recovery-evidence.json；新启动Chrome PID16192在服务重启后保留且重绑成功。

限制与后续：VAL-001/006继续验证新恢复当前窗口实际行动和完整中断续局；旧绑定浏览器被服务关闭的失败保留。

Git关联：本批实现提交后写入注册表和commit-index，不预填哈希。历史代码来源8d02d92044f5fa1272d4c0ff199523eb60db1c67及前序提交；新补强以实际提交为准。回退使用Git恢复相应源码与测试，保留本机所有事实和失败档案；本批不迁移或删除真实事件。
