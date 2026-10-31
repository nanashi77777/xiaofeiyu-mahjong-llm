# VAL-004 · 指定模型实际渠道

类别：验收。完成日期：2026-10-31（Asia/Shanghai）。

固定渠道本机私有渠道、模型基线模型，Responses且reasoning.effort=none；不换模型、不启用传统策略兜底。实际出站请求通过httpx request hook在发送前核对参数并保留不含headers的构造样本，见runtime/yaku-reference-validation-v2.json。short首动作、严格Schema、SSE首段与尾段均由实际渠道完成，usage显示reasoning_tokens=0；没有以完整尾段耗时冒充首动作耗时。

真实渠道累计快照中，当前麻将专用凭据1453次completed、62次cancelled、27次failed；用途包含1151次decision、346次wire_validation、25次repair、19次review及1次probe。62次取消的usage均未知，保留null；25次实际修复成功记录保留原初次尝试。计数包括开发/探针/失败续录，不能当成正常实战有效窗口或500窗口通过率。用途与凭据归属正确，未知usage不算零，不对缺失缓存计量伪造命中。

实际stream-prefix nested/short探针各10次均保留首段/完整usage；short无计划输出34～36 token，旧nested为41/42，调用批次不同不把全部时延差归因格式。归档真实时延存在TTFT长尾，生产5+10已多次因剩余预算内无响应而停止，性能另行验收。

207项pytest及ruff通过，覆盖严格终止事件/不完整SSE、任意分片、非法ID/请求/计划版本、重复或改写首动作、尾段失败、实际提交后取消、同预算最多一次修复、usage缺失和专用密钥分账。真实取消/超时记录与隔离故障注入分别注明，没有把构造错误当真实服务端故障。配置和错误输出不含密钥，staged秘密审计零命中。

范围限制：本项确认接口契约及取消/计量机制，不保证第三方渠道可用性或五秒SLA。VAL-005/006仍未通过。Git机制见3e7b739、f849faa等实际提交，新增出站审计工具见eb6bb32，验收日志随本批提交；回退使用Git及本机nested配置，不删除实际失败证据。
