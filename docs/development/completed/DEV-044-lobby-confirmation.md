# DEV-044 · 局制映射与确认持久恢复

完成日期：2026-10-31（Asia/Shanghai）。按[实施前方案](../../design/lobby-confirmation.md)修正建房核验并保留每次原提交的服务端确认。

通过可见页面分别创建并读取回包：一局3+5对应mode=4，东风5+20对应1，半庄5+10对应2，人机60+0对应3。普通房也有ai=true，不能把它当作人机开关；人机max_player_count=1表示一个人类座位，画面仍为四人麻将。game_modes.py成为导航与规则入口的共享映射，未知/三麻模式拒绝，时间与正整数房号严格校验。这批测试房均已退出，没有准备或开始。

确认在选项校验前落盘，仅保留房号、人类容量、playing/robots、mode/ai与两项计时；不保存扩展字段、账户或认证。重启后仅重验原确认，不重新创建/加入/匹配；原回包缺失时保持未知。历史成功明确标注historical_submission/current_room_verified=false，不冒充仍在房间。旧命令local-create-south-5-10-20261031没有原确认，保持uncertain，没有拿新房倒填。

验证：主项目66项pytest及ruff通过，覆盖缺失/错误局制/时间、普通ai=true、错误回包不污染房间、确认持久化与重启后零点击重验。真实证据仅本机runtime/lobby-mode-verification.json、lobby-mode-matrix.json、lobby-mode-matrix-east-ai.json；游戏截图/模板不入库。

完整入口、权限、满房、匹配取消及自动接管仍在DEV-037..041/VAL-012/013；本任务完成不代表整个M9完成。实际提交见[提交索引](../commit-index.md)。
