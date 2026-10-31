# DEV-042 · QQ选场工具与独立导航接口

完成日期：2026-10-31（Asia/Shanghai）。类别：实现/跨库接入。

按用户新增要求提供QQ mahjong_navigate和mahjong_navigation。前者把明确参数转交独立服务，后者查询、取消未完成指令及人工验证后续跑。QQ通过独立navigationToken访问固定loopback导航路径，不持有controlToken或麻将模型密钥；原四类查询继续只读。

QQ侧使用当前会话key和本轮真实消息ID生成稳定command_id；跨会话、引用/历史假冒来源、任意URL/路径和缺参被拒绝。同一消息网络重试不重复提交；超时返回原编号和结果未知。状态列表只投影本会话命令。角色说明只更新自有麻将补充段，原QQ提示、白名单、触发规则和其他API密钥保留。未完成页面流程与服务端结果不同于已受理状态，不能回复成已经开打。

本机同步提供面板登录/选场表单、CLI navigate/navigation及认证接口。导航记录不进入普通牌谱，停止和窗口/输入模式变更取消在途导航；QQ接口不能调用通用控制。已经提交成功的匹配/入房不能由本机“取消任务”误报成服务端已取消，专门的可见取消适配仍由DEV-038等跟踪。

验证：独立项目50项Python回归/ruff通过；QQ仓库193项完整test-qq通过；真实stdio MCP发现两类导航工具并读取本机状态，原四类查询发现/读取仍通过。安装角色补充幂等，重载前检查在途回合为0，重载后3群+1私聊映射全部agentAvailable=true。所有验证未发送QQ/私信/评论等测试消息。

证据留本机：runtime/mahjong-navigation-verification.json、mahjong-mcp-verification.json、mahjong-navigation-tests.log及独立项目lobby-command-verification.json。实现位置：app.py/cli.py/panel.html和QQ库bridge/src/mahjong-navigation.js、mcp-qq-chat.js、dsh-context-safety.mjs、qq-mahjong-prompt.js。完整矩阵仍在DEV-037..041、VAL-012/013；新凭据自动登录和自动打牌交接不能因工具可调用就标为完成。

Git关联：独立库14dc03b685631fda1613ea251abcaecc675abd8b，QQ库a46546a92b2d71ac70b0c8a076fa0045b8b2382b（P034），均已上传开发分支及main，见提交索引。回退两库源码、移除专用导航配置并重载，保留原查询配置与游戏档案。
