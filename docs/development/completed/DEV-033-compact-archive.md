# DEV-033 · 最小持久化牌谱与无损迁移

类别：实现；日期：2026-10-31（Asia/Shanghai）。

用户要求减少bot保存牌谱时的字段数。现在SQLite v2的事件payload只存实际事件数据，元信息复用SQL列；小局只引用结果事件，和牌索引只引用事件内的和牌者。整场仅留一份最新恢复缓存，省略默认值和可由事件重建的公开时序；不会逐事件另存全量快照。QQ小局查询每条事件仅id/seq/kind/data，共同的场、小局和响应版本放外层。解释、计划、点击、耗时和经验继续独立存放。

实现位置：store.py、state.py的public_event、queries.py、tools/compact_archive.py、tests/test_compact_archive.py。未知载荷字段保留；历史事件不持久化单调时钟，读回强制historical，不能打开历史操作期限。v1先SQLite backup、再BEGIN IMMEDIATE事务迁移；失败回滚，不写入未来版本库。

验证：25项pytest回归通过，其中两项独立覆盖多小局/赤牌/摸切/鸣牌/立直/多人和/终场逐步回放、最新快照重建、查询、重开、v1备份、迁移失败回滚、未知字段保留与中断保存。真实本机档案副本4场289事件及4份最新场况逐项一致；牌谱相关UTF-8 JSON共253752→80091字节，减少68.44%。这不是SQLite文件页大小，也不包含模型诊断日志压缩。报告在本机runtime/compact-archive-verification.json。

已应用本机v2库，289条事件完整保留，foreign_key_check为0；自动备份留runtime，四类只读HTTP及QQ MCP调用通过。完整规则与连续实战验收仍见VAL-001/006。

本任务只消费此次新增的最小存储要求，可独立依据逐事件/快照等价验收；既有DEV-004完整领域契约与DEV-010综合归档/恢复分别保留其剩余验证，不把它们冒充完成。实际实现提交e53fe8a，见../commit-index.md，注册表已绑定。回退前停止服务，并使用本机before-v2备份恢复旧库与匹配源码，不让旧程序写v2。
