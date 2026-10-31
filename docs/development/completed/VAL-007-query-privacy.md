# VAL-007 · QQ按需查询、权限与真实经历

类别：专项验收；完成日期：2026-11-01（Asia/Shanghai）。不包含大厅自动接管或QQ外发测试。

实际QQ stdio MCP发现并按需调用mahjong_live/history/big_hands/memories，四工具全部available=true；导航工具只读状态，零导航提交/外发。2026-11-01复核报告为QQ runtime/mahjong-mcp-verification.json及mahjong-navigation-verification.json，旧版本已备份。未改QQ源码，保留用户qq-system-prompt.js工作区修改。角色说明qq-mahjong-prompt.js要求用真实记录自然表达“我这把”“我最近在试”，旧记录不说正在打、candidate不当定论；schema也标明事实与指令边界。

正式HTTP只读核验runtime/query-acceptance-v1-20261101.json：当前17场固定范围，无重复；同scope内2条本家跳满及以上均归属集合，当前已终场以last_observed展示，不冒充live。历史真实53次荣和/自摸归属及QQ详情读取由DEV-063留证。test_twenty_match_scope_stays_fixed_across_new_matches_and_big_hands_filter用22场验证固定20场、分页中插新场及大牌集合一致，补足目前实际库不足20的边界。

本轮新增test_query_privacy三项：active和interrupted当前小局隐藏暗手、摸牌、reason/计划及动作自由文本；旧局结束后历史可读，恢复旧局后其经验列表/详情/全部修订再次隐藏；read和navigation令牌逐一拒绝全部通用控制入口，Origin检查有效。连同现有查询/导航测试31项通过，全部使用临时数据库与拦截服务，未把构造样本当真实聊天。无服务时QQ读取器返回无法确认当前战况，身份与白名单未改变。

实现和证据源为麻将queries/app及QQ独立库DEV-023关联提交；本次新增验证实际哈希后续登记索引。回退测试/文档不改生产事实、经验或QQ配置。本验收证明按需可查、范围、隐私与权限机制，不能证明长期麻将强度、五秒或完整实战完成。
