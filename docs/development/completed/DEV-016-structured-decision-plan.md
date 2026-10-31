# DEV-016 · 结构化决策与具体小局打法计划

类别：实现。完成日期：2026-10-31（Asia/Shanghai）。

输出q/a/r/p严格校验，Schema动态限制本请求、合法ID、旧计划版本和证据ID；不执行半截JSON或无终态流。具体小局计划只在真实动作确认后采用，新小局重置，与实际场况分离。

实现位置：src/majsoul_llm/domain.py、wire.py、model.py、coordinator.py。

验证与证据：tests/test_core.py、test_queries_and_model.py及真实配对；非法引用/过期版本拒绝，完整JSON、结构化输出、流终态、usage和取消样例通过；正式5+10首10次响应均合法。

限制与后续：无效输出仅在同预算内一次修复；实际速度由VAL-005验收。

Git关联：本批实现提交后写入注册表和commit-index，不预填哈希。历史代码来源8d02d92044f5fa1272d4c0ff199523eb60db1c67及前序提交；新补强以实际提交为准。回退使用Git恢复相应源码与测试，保留本机所有事实和失败档案；本批不迁移或删除真实事件。
