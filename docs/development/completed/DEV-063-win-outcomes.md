# DEV-063 · 荣和放铳与自摸事实归属

- 类别：实现；完成日期：2026-10-31（Asia/Shanghai）。
- 用户要求：牌谱明确记录谁向谁放铳、弃出什么牌，或谁自摸，以便复盘检查本家放铳；仍尽量减少持久化字段。

## 变更与原因

原始ActionHule已保存每位和牌者、hu_tile、zimo和点数，但HuLeInfo没有可靠放铳座位。新outcomes.py从同小局紧邻的实际弃牌、摸牌或杠事件构建归属，不能从负点数、包牌或更早同张弃牌猜来源。每个和牌者稳定使用event_id:index，保留绝对座位、ron/tsumo、和牌张及来源引用。

一炮多响保留所有和牌者，本家deal_in_events按同一次和牌去重，deal_in_winners逐位计数。抢杠区分kakan/ankan；赤牌保留0编码，暗杠只知道牌种时不编造五的颜色。新局、终局、流局、未知行动和序号缺口隔断旧来源。来源缺失或矛盾返回unknown/conflict、issues和null放铳者；非confirmed的来源引用只供诊断。自摸始终没有放铳者。

Store.win_outcomes只读原events，数据库保持v2，不新增表列或重复结果JSON，旧牌谱和服务重开都能重建。已结束小局及整场小局索引返回wins，整场返回本家win_summary，大牌补outcome。活动小局继续隐藏暗手/解释/计划；结果独立于事件分页。QQ复用已有mahjong_history入口，无需修改或重启QQ源码。

Reviewer输入增加win_outcomes和win_summary，提示区分事后结果与当时已知，不把单次放铳当作策略错误证明。输入版本review-input.v2-win-outcomes纳入指纹；原已完成复盘任务不自动重排或批量重新消费模型。

## 实现位置

- src/majsoul_llm/outcomes.py、store.py：可重建的归属投影。
- queries.py、review.py：查询与复盘输入。
- tests/test_win_outcomes.py：关键归属、异常、分页、重开和复盘去重。
- tools/audit_win_outcomes.py：生产库只读一致性事务审计，不请求模型、不写生产库。

## 验证证据

1. `python -m pytest tests/test_win_outcomes.py tests/test_queries_and_model.py tests/test_compact_archive.py tests/test_review_lifecycle.py -q`：44 passed。
2. `python -m pytest -q`：237 passed，1条上游Starlette/httpx弃用提示；`python -m ruff check .`通过。
3. `python tools/audit_win_outcomes.py --output runtime/win-outcome-audit-v1.json`：16场、45次荣和、8次自摸，53条归属全部confirmed，unknown与不变量错误均0。本家合计荣和13、自摸2、放铳事件11、被荣和人数12。该结果是已有真实档案的归属核验，不是新开53局或新模型性能测试。
4. 生产服务v16加载实现提交728d7f44fc2c1e41fa25c4654d2d9807c988871f，source_digest=b69391dabb884309f4ca70594d834d288f42bdf6135887ce0d99f7a045c039f7；终场且无输入任务后受控重载，保留Chrome并重新连接/绑定。`runtime/win-outcome-http-validation-v1.json`：最近整场9小局、大牌2条归属与原事件一致，登录恢复，后台输入模式保留，接管停止。
5. 通过QQ当前白名单调用正式MCP `mahjong_history` 三次：整场、小局第一页与后页，归属一致。最近场本家seat=2，荣和2、自摸0、放铳1，弃3s给seat=0；来源161b269a529a84a10cb9。证据位于QQ本机 `runtime/mahjong-win-outcome-mcp-verification-v1.json`。externalMessages、navigationSubmissions、modelRequests均0；QQ源码未改。
6. 首次实现暂存秘密审计12文件零命中；完成视图与文档另随完成提交检查。

## 限制与后续

缺失/冲突处理由构造专项覆盖，已有53条真实结果均有完整来源；不据此宣称真实生产已发生过全部抢杠/多家和/缺包组合。复盘新字段的正式代码路径与测试客户端输入已验证，本轮不为同一历史再请求真实模型；后续正常赛后复盘使用新格式。500窗口、三场完整5+10及低频输入矩阵仍由原VAL任务跟踪，不随本功能关闭。

## Git关联与回退

实现提交：b3f6a72454cefc2d1af580e82f67cfe3f5935096。任务消费与实际提交关联随之后的完成日志提交记录。回退可revert实现提交并受控重载服务；无需回滚数据库，原始牌谱及已有复盘记录保持完整。
