# DEV-096 · 管理员对话技巧与全局经验接入

2026-11-03完成实现、隔离验收和本机只读核验。实战和综合验收继续暂停。

## 已交付

独立mahjong_knowledge版本表保存短原则、必要例外、阶段/顺位/门清/立直威胁/场类/役路线/点差/自己弃牌数/剩余牌数等适用范围。管理员教学固定taught_unverified，active/inactive；重复教导不会算作实战检验。新建无需match/event，精确规范化重复去重；改已有技巧需id/revision，停用保留历史，省略status保留原状态。语义相近原则由模型明确修订，不能靠字符串自动合并掉例外。

QQ沿用9个麻将工具：control增加knowledge_write/memory_disable，operations增加knowledge/learning；原memories读取真实实战经验。QQ入口和实际签名HTTP提交均核验配置管理员、当前真实消息及本人直接片段，教学摘录逐字匹配；误设autonomous也不能放开，旧memory_revise和手动review_enqueue同步受限。普通用户、转发/引用/历史/后台任务不授权。原自动复盘保留真实事件证据生命周期，不自动改教学库。教学不混入QQ人际记忆，独立打牌模型不接收社交人格和聊天原文。

实战经验资格不再限最近50条，改为全部活动经验的ID/版本引用，减少冻结快照重复。实时联合相关召回教学和经验，共享原0～8条上限；精确规范化去重、每轴风格最多一条，省略空投影字段。教学更正从下一决策快照读取，已构建请求不变；经验维持整场冻结，停用立即退出后续召回。不增加实时模型请求，不筛选/排序合法动作。

memory_recall日志保存context_prepared、所选ID/版本/来源及projection_chars，表示构建上下文时选择，不证明网络送达、模型遵从或牌力改善。复盘可读取当场所选的精确历史教学版本，只讨论边界和反例；字符量不冒充token，整请求usage继续记录真实供应商用量。

## 验证

- 完整Python回归488 passed，88.20秒；最后上下文记录阶段措辞调整后相关50项再次通过。ruff和任务表检查通过。新增覆盖教学版本/停用/冲突/去重、超过50条实战经验、联合上限/条件、真实Coordinator入口完整候选与教学投影、关闭召回、复盘精确版本以及持久操作/隐私。
- QQ配套P038相关80项通过，包括管理员群/私聊、普通人/引用/转发/历史/伪造来源/后台拒绝、策略误放宽、旧入口和无签名回退拒绝。新QQ根人设实际渠道入口已接麻将学习说明，B站不带麻将工具说明。
- 真实服务先检查enabled=false、无输入/等待任务，SQLite备份quick_check通过后仅调用service_stop正常退出并隐藏重启。保留24303事件、31场和12个经验修订行；新增教学表空表正常，quick_check=ok，knowledge/learning/catalog均HTTP200。没有向真实教学库写入测试数据。
- 真实stdio MCP发现9工具，新增命令及资料Schema可见；16次固定鉴权HTTP GET全部可用，包含knowledge/learning。没有伪造QQ直接事件执行签名工具；签名边界由隔离测试验证。QQ四映射可用，服务保持stopped、browser disconnected，无新牌局/真实模型调用/QQ测试外发。

本机证据留ignored runtime/dev096-pytest.log、dev096-deployment.json及QQ runtime/p038-focused.log、p038-readonly.log，不上传牌谱/教学/凭据/截图。

## 实际提交和剩余边界

实现提交0b86c82c143cb1200373ce16f0efaacf2d80e6dd；配套QQ实现a6eeb23，任务消费与最终同步在后续文档提交登记。新教学是否在真实模型决策中被采用或改善牌力，仍需用户恢复实战后验证；本次不关闭暂停的实战VAL。QQ完整生产重载及最终全量回归证据以P038完成记录为准。
