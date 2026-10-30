# DEV-003 · 私密配置与运行边界

类别：实现；日期：2026-10-31（Asia/Shanghai）。

独立配置与运行目录已实现。Settings以SecretStr保存模型、控制及只读凭据，普通序列化不包含真值；init-config生成本机凭据，环境变量可覆盖模型密钥。公开示例config.example.json不放凭据，默认正式5+20、停止接管、关闭未经验证的紧凑格式和计时语义。

实现位置：src/majsoul_llm/config.py、cli.py、app.py、model.py、.gitignore、config.example.json、docs/runbook.md。后台模式为用户另行新增的可选配置，300+0只在本机integration_test及真实操作时间匹配时生效。

验证：25项pytest回归通过；其中配置往返、秘密不进入dump、只读/控制权限分离、Origin及无鉴权拒绝、模型none/store=false与真实usage验证通过。ruff与pip check通过。本机服务重启、四类GET查询200。实际牌局速度不属于本任务通过范围，仍由VAL-004/005跟踪。

Git关联由后续提交索引绑定实际实现提交。回退先停止接管，再恢复同一版本源码；私密配置及运行数据库独立备份，不能随源码回退删除。
