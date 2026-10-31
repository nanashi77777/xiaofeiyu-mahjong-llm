# DEV-076 · 本机供应商与模型配置

2026-11-01实现。CLI models list/capture/select使用ignored runtime/model-profiles.json，保留provider、model、responses协议、专用SecretStr凭据及与完整身份绑定的格式门禁。选择仅停止服务且没有活动归档时允许，原子保存private.json，重启生效；环境凭据冲突拒绝，变更身份清除门禁与副请求，精确回退可恢复原门禁。QQ不新增写设置权限，决策、修复、复盘共用所选配置。

9项配置隔离测试通过：切换及回退、服务占用/活动归档拒绝、URL秘密和协议校验、环境覆盖失效、MockTransport核验实际路由和三个用途的model/none/密钥。状态和usage保留安全标识/身份指纹，密钥不在list或model_dump。URL拒绝userinfo/query/fragment和远程HTTP。新增协议需实现适配与独立验证，当前不声称支持chat/completions。

本机真实CLI已捕获baseline并list验证，保存本机回退点；服务运行时select被实际拒绝且未改private。未访问新供应商、未切生产模型。旧模型测试结论见DEV-075；新模型/渠道理解与时延验收不能继承。

补充实际部署：停服后CLI select baseline成功，v23重启状态核验provider=default、profile=baseline、responses/terra/none，四格式门禁恢复且副请求0。专用Chrome仍同一进程，无新供应商请求。
