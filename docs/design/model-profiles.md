# 本机供应商与模型配置

2026-11-01，用户要求模型访问无法改善时优先优化其他环节，并预留供应商和模型切换。登记DEV-076，先保持当前terra、none和麻将专用凭据，不增加渠道探测或正式并发请求。

在ignored runtime/model-profiles.json保存命名配置：供应商标识、base_url、model、api_style及SecretStr密钥。当前协议明确为responses，保留独立协议边界；不把仅兼容chat/completions的渠道误当成已支持。推理仍固定none，决策、修复、复盘使用同一选择。CLI提供list/capture/select，密钥只能从本机文件或既有环境变量读取，输出只含标识与指纹。

捕获当前配置便于回退；选择配置仅允许服务端口未占用且归档没有活动对局时，原子写本机private.json，重启后生效。不提供QQ修改模型设置的入口。新目标身份（供应商、模型、协议、URL或凭据）清除compact/table/schema/stream相关验证与副请求开关，不能继承旧渠道结论；精确回退原配置才可恢复原门禁。拒绝含用户信息、query、fragment的URL，HTTPS为默认，仅本机允许HTTP。

测试覆盖切换、回退、活动阻止、密钥不出现在list/status/异常、目标身份变化导致门禁清空、拒绝不支持的协议。隔离MockTransport核验实际请求路由、模型、none和专用凭据；不访问新供应商。不以配置入口交付冒充新模型已验收。
