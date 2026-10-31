# DEV-035 · 麻将专用密钥与全用途分账

类别：实现；日期：2026-10-31（Asia/Shanghai）。

用户明确新密钥供麻将决策与复盘迭代共用。本机runtime/private.json已切换，模型、渠道和none不变；decision/repair/review/probe通过同一ModelClient捕获请求凭据，usage记录billing_scope=mahjong及不可逆指纹。独立usage_summary按凭据和用途计量；旧未标记记录legacy_unattributed、取消/缺usage未知，缓存和推理token不重复计入total。修复同UTC时间戳记录的读取顺序，以rowid作次序保证最新失败/未知usage不会被前条成功覆盖。

实现：model.py、usage.py、app.py、panel.html、store.py、tests/test_usage.py。密钥原文只在本机配置，不进入源码、统计或提交。

验证：35项pytest通过，分别校验decision/repair/review/probe真实HTTP请求构造用同一项目凭据，跨旧/新/无标记密钥与用途汇总不混计，未知usage及同时间戳顺序正确。真实新密钥probe成功，54输入/25输出/79总token，0推理；服务恢复的一次真实review为23940输入/373输出/24313总token，0推理，同指纹归档。完成这两请求时新密钥累计24392已知token；这是当时证据，不是静态余额。QQ侧private.json、bridge/config.json及DSH profile逐文件哈希核验未变。

本机服务已加载新密钥，自动接管保持停止；复盘正常使用新密钥。后续token随实际请求增加；没有价格依据不编造货币金额，也未改供应商总账户额度。实际提交由后续索引绑定。回退代码不回退用户指定的新密钥；如需更换凭据，保留旧指纹的历史计量。

实际实现/方案提交：76eeda2f60bec59b224eb6ecbb52b76571ae29f2；已推送私有开发分支feat/initial-websocket-runtime。
