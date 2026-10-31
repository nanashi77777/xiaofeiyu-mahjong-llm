# 浏览器完整显示与麻将独立用量

日期：2026-10-31。先登记方案和任务，再改代码。

DEV-034：专用浏览器启动显式禁用Playwright固定viewport，使用真实窗口视口，并通过浏览器窗口API核验最大化；必要时提供恢复窗口显示的本机入口。测量inner/outer/screen/DPI及可见canvas矩形，保证画布完整落在页面视口内，截图只取当前完整画布，避免locator截图自动滚动掩盖截断。窗口改变后坐标重新获取，不修改游戏状态。真实游戏、窗口缩小/恢复、不同显示比例及后台最小化分别记录。

DEV-035：用户提供的新密钥只写本机runtime/private.json。实时decision、repair、review、经验迭代和probe共用麻将ModelClient，全部使用此密钥；QQ/DSH/B站/生图等既有配置不改。每个token_usage附billing_scope=mahjong及不可逆的本机凭据指纹，记录真实usage；取消/缺usage仍null。汇总按密钥指纹分组，旧无标记请求单列legacy_unattributed，不把以前共享密钥的消耗记到新密钥。无价格配置不编造货币金额，无渠道证据不宣称供应商账户总余额已拆分。

完成条件：新密钥真实最小探测通过且none/usage有证据；模型测试分别校验decision、repair、review的请求凭据一致且不同于旧配置，指纹分组不混计；QQ侧其他凭据配置以本机哈希核对未变。密钥原文及本机截图/统计档案不上传。先关闭接管，服务重新加载配置后才算运行生效。
