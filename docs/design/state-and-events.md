# 权威状态、事件与归档

## 来源与未知

在线状态只来自经过协议适配器校验的实际事件/恢复快照。模型的意图和解释存进决策表，不参与 reducer。未来视觉输入可以携带置信度、截图证据和未知字段，但不能为了满足结构而编造点数、牌河或对手手牌。

每条 `SourceEnvelope` 保留 source 类型、连接 ID、接收单调时钟、UTC 时间、协议版本指纹、消息类型和脱敏载荷引用。只捕获游戏所需白名单消息；登录 token、Cookie、完整认证载荷不进入牌局档案或模型上下文。原始二进制若为诊断必需，单独留本机受控目录，不能提交 Git。

## 牌和座位

- 使用 `1m..9m / 1p..9p / 1s..9s / 1z..7z`，`0m/0p/0s` 为赤五；`1z..7z` 是东南西北白发中。
- 计算向听时可以归一化赤五，但动作、打点、消费牌和点击目标保留红五区别。宝牌**指示牌**列表保留顺序和重复项，不是宝牌本身，也不能去重。
- 服务器绝对座位仅为 `0..3`。持久化使用绝对座位，展示时通过已验证映射得到 `0=自家 / 1=下家 / 2=对家 / 3=上家`。用户原示例中的 `0..4` 是五个编号，不沿用。
- 相同牌种的多张手牌用本家可见的逻辑实例 ID 跟踪；摸牌槽另有 `drawn_tile_id`。不伪称已知服务器未公开的实体牌编号；摸切与打出原手牌同种牌仍可分开表达。

## GameSnapshot 最小字段

| 字段组 | 内容 |
| --- | --- |
| 身份 | schema_version、match_id、round_uid、round_seq、state_version、last_event_id、last_source_step |
| 来源 | source、capture_started_at、history_completeness、recovery_state、unknown_fields |
| 规则 | players=4、room_type、原始 mode/config 指纹、标准化规则选项与支持情况 |
| 场况 | 场风、局数、本场、供托、庄家、本家座位、当前行动者、余牌数、阶段 |
| 点数 | 四家实际点数；当前顺位及计算依据；未知时为 null |
| 宝牌 | 公开的有序 dora_indicators、来源事件；赤牌规则；仅在实际揭示后保存 ura_indicators，在线未揭示为 unknown |
| 本家 | concealed_tiles、drawn_tile_id、门清、已成立/声明中立直、可知振听状态 |
| 四家 | 有序牌河、副露、立直声明牌与成立事件、可知手牌张数 |
| 操作 | operation_window_id、原始操作列表、时间字段、窗口接收时刻与有效阶段 |
| 诊断 | freshness、consistency、最近恢复依据；不混入模型推测 |

`GamePhase` 至少区分等待发牌、摸牌后选择、吃碰后待弃牌、杠后待补牌、抢杠响应、小局结算、场间过渡、终场、恢复中与中断。牌河每项包含 tile、actor、event_id、摸切、立直声明、是否被鸣走及鸣牌关联，不能鸣牌后删除历史弃牌。

纯界面阶段另属 `UIExecutionPhase/FreshUILayout`，例如立直选牌、鸣牌组合选择和动画等待。点击立直但尚未弃牌时，协议权威状态仍是待弃牌，不能宣布立直已声明或已成立。UI阶段更新只增加ui_revision，不更新客观state_version；新的协议事件才更新权威状态。执行事务在同一个模型动作下跟踪UI各步，重新核对语义目标和原操作窗口。

`Meld` 包含 meld_id、actor、kind(chi/pon/daiminkan/ankan/kakan)、tiles、called_tile、source_seat、consumed_tiles、成立事件、升级前碰 ID 和 closed。暗杠不破门清；加杠升级已有碰，不能增加面子数量。

固定面子数为 M、其中杠数为 K 时，等待摸牌的暗手通常是 `13−3M`，待弃牌是 `14−3M`；含副露的实体牌总数为 `13＋K` 或 `14＋K`。按阶段验证，不用“所有时候都必须 13/14 张”的简化断言。

## 场与小局身份

整场使用 `game_uuid` 等真实对局 ID；WS 连接 ID 不是对局 ID。恢复仍归原场。服务端无法提供 ID 时创建明确标记的临时身份，后续可靠关联，不能靠“东一局”猜测合并。

小局使用 `match_id + round_seq` 形成稳定 round_uid，另保留源 round_id（若有）。场风/局/本场是显示标签与核对字段，不充当唯一键。step 可能随小局重置，事件去重需结合场、小局与来源序号。

## 事件处理与恢复

1. 解码并校验 envelope，转换为规范事件；不支持的消息留诊断，不能让 reducer 静默猜测。
2. 以协议事件顺序归并，检查手牌计数、操作阶段和点数一致性；将事件与投影更新放入同一 SQLite 事务。
3. 新小局冻结上局事实和结果，增加 round_seq，重置本小局计划与本地计时账本；供托、本场和点数取新事件，不自行推演代替。
4. 立直分声明与成立。`LiQiSuccess.failed` 等真实结果决定是否扣点和增加供托；不能点击按钮即当成立。
5. 保留每个和牌者、各自役种/点数与整小局分数变化；多家和牌不能只取 hules[0]。区分荒牌、中途流局、和牌与终场。
6. 断线后取消旧模型请求与 pending 输入；恢复批次只重建，不对历史 operation 请求模型或补点。恢复完成后仅打开仍有效的当前窗口。
7. 快照和回放缺历史时保留缺口；重连不重建满额时间池，不重复生成小局、牌谱或大牌记录。

## 持久化与完整度

2026-10-31追加DEV-063：和牌归属由原始ActionHule和同小局紧邻的弃牌/摸牌/杠事件投影，不增加重复事实存储。每个和牌者保留稳定的event_id:index、ron/tsumo、绝对座位winner_seat、和牌tile、荣和from_seat及source_event_id。来源存在缺口或冲突时返回明确未知原因，不能倒找更早的同张弃牌或从负点数/包牌猜放铳者。一炮多响逐位保留，但本家放铳事件数按同一和牌事件去重；自摸支付不算放铳。新局、流局和终场隔断来源。抢杠保留kakan/ankan来源，暗杠牌种不能被误认成已知赤牌颜色。派生投影可从旧归档重建。

SQLite WAL 负责查询和事务。已实现v2最小归档，见[最小持久化牌谱](compact-archive.md)：events仅存一份实际数据，rounds及wins引用原事件；matches仅一份最新恢复缓存，不重复保存public_events。决策、执行、token和复盘归独立records，经验归memories，不能混入权威牌谱。wins外键关联match_id、round_uid与来源事件。原先拟议的多张快照/结果表不再重复实现。

每条记录增量落盘；停止、崩溃或断线也保留已经提交的事实。append-only 事件不被复盘覆盖；投影可重建，格式迁移版本化并备份本机库。

对局生命周期与记录完整度是两个维度：`lifecycle=active/ended/interrupted`；`capture_completeness=full/partial/gaps`。中途接入但观察到终场的牌谱可以是 ended+partial，不伪装 full。

只在真实终场结果出现时填 final_scores/final_ranks。中断仅填 last_observed_scores/ranks。断线暂时恢复期间返回 reconnecting，程序停止或已无法续录时标记 interrupted；同 UUID 恢复可重新 active 并合并已存事实。

实时接口附 observed_at、age_ms、freshness 和连接状态。旧点数可以作为“最后观察到”，不能继续声称实时。默认查询层 fresh 阈值先取 4 秒并可配置，但连接仍正常且牌局安静时需结合心跳区分“没有新行动”与“采集失效”，不能仅因长考而误判终场。
