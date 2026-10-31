# DEV-045 · 加入友人房后准备

完成日期：2026-10-31（Asia/Shanghai）。按[实施前方案](../../design/friend-room-ready.md)实现加入与准备分步确认，配套QQ任务P035。

JoinFriend.ready为严格布尔，默认true，明确只入房传false；QQ、面板与CLI/HTTP契约一致。准备必须通过可见“准备”标签锚点，不能用房主“开始”的同位置替代。绑定当前登录代次、房号及未开局状态，异步等待后重查；确认joinRoom后才允许唯一第二步readyPlay。加入已完成但准备未提交时续跑只准备；准备提交结果未知时不重按，只等原回包。升级前无ready的旧记录续跑保留旧行为。

真实命令user-join-54321-ready-20261031已成功：joinRoom于2026-10-31T09:02:42.993093+00:00返回error_code=0，readyPlay于09:04:03.921852+00:00返回error_code=0且对应请求ready=true；最终prepared=true、started_by_bot=false、current_room_verified=true。房间为四人半庄300+0，自动打牌enabled=false。没有点击开始。

协议仅对白名单readyPlay请求解码布尔ready并与响应绑定；登录请求不解码，不发游戏操作包。真实房码逐位读回54321通过；01234前导零仅输入读回，未提交。当前客户端最多接受5位，契约较长值遇到读回不符会暂停；不能据此宣称长房码已支持。

验证：主项目66项pytest/ruff、QQ193项完整回归通过；准备确认、未知去重、同房代次保护、严格类型、歧义数字及协议私密字段隔离均覆盖。QQ重载前items[].running计数0，重载后3群+1私聊均agentAvailable=true；两类导航及四类查询真实MCP发现/读取通过，未提交伪造QQ命令或发送测试消息。证据仅本机runtime/friend-ready-verification.json及QQ的mahjong-ready-reload.json、mahjong-navigation-verification.json、mahjong-mcp-verification.json。

所有房码错误矩阵及自动入局接管仍由DEV-039/041、VAL-012/013跟踪。实际提交见[提交索引](../commit-index.md)。
