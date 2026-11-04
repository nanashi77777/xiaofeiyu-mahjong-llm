# DEV-097 · 自动打牌入口与首局接管时序修复

完成日期：2026-11-04（Asia/Shanghai）。用户反馈本机与QQ均无法开启自动打牌；本次仅修改麻将AI项目。QQ主项目只读，实战与综合验收仍暂停。

## 实现结果

面板start/shadow改用与QQ相同的持久操作入口，自动绑定已核验的专用窗口，随后复验认证、同局、完整场况与真实行动期限。增加“等待下一局（10分钟内一次）”按钮，分开显示原command_id的受理、等待、绑定、成功、失败或取消；等待期间主状态明确显示“等待接管”。

等待入局不再在authGame生成active/partial快照时提前失败。授权前后两种认证顺序均等待ActionNewRound补齐首局；已过期/历史窗口等待新的真实有效窗口。等待与绑定共用原授权期限，不重置时间池；绑定后行动刚好过期时继续等新事实。目标对局一旦确定不转授另一局；绑定过程中认证变化、停止/Esc或会话代际变化均不能开启输入。

QQ已有command schema、工具及权限保持兼容。status.startup增加无暗手的阶段/固定恢复提示，失败回执提供固定reason与下一步action；start只接管当前活动局，arm等待一次入局，不自动开房、准备或开局。

## 证据与限制

- 真实排查复现旧面板未绑定时400和笼统错误。已有QQ记录确认打开浏览器、加入友人房及准备成功；之后arm被stop取消。不能将这些事实说成QQ工具完全不可用或已经完成自动打牌。
- 修改前新增的三项隔离复现均失败：认证先于首局（授权在认证前/后两种顺序），以及窗口已过期时等待接管提前失败。
- 修改后完整pytest：501 passed，130.10秒；一条既有FastAPI/Starlette测试客户端弃用提示。Ruff、任务表一致性、Git差异检查通过。
- 专项覆盖真实Engine认证到首局事件链、未完整期间不启用、停止不可复活、认证变化、原期限覆盖等待和绑定、过期窗口不补池、绑定期间过期后等新事实、旧认证/第二场拒绝，以及面板自动绑定、鉴权、持久幂等和大厅恢复提示。
- 实际Chrome隔离加载当前panel.html，所有HTTP均拦截为fixture，依次操作start、arm、pause；失败原因保留、等待状态明确、暂停显示cancelled，JavaScript错误为0。没有真实游戏操作或模型调用；证据留ignored runtime。
- 部署前以SQLite一致性备份保留已有33场、24,327条事件及3条教学，备份留ignored runtime，不能以隔离结果冒充实战通过。

## 实际部署与接口验证

实现提交为`1bc0f13d9e8b741d9123d341ca00d020c1b75ffb`。在无活动局、输入、等待授权、在途操作或复盘时，通过原服务service_stop正常退出，只重启本项目服务；重连已核验的专用Chrome成功，保留登录态。运行构建HEAD与该提交相符，Python源码摘要与工作区一致。

真实鉴权HTTP检查：大厅点击start得到明确active_game_required_use_arm及arm恢复提示；本机/control/arm和QQ原operations接口分别进入waiting_game/awaiting_new_game，enabled始终false，随后stop均确认cancelled。检查使用10秒、shadow=true的额外输入保护，完成后无等待授权。最终connected=true、enabled=false、input_busy=false、waiting_game=null。

检查前后33场、24,327条事件、3条教学、2,884条token_usage记录完全一致；未新增对局或模型用量记录，未发QQ测试消息。QQ本机三类凭据、端点及serviceRoot匹配，查询/导航/操作GET均200。实际DSH只读session.toolStatus核对4个现有映射会话，每个均有9个麻将工具且agentAvailable，运行中回合为0。

上述为实际服务接口验证及工具目录检查，没有冒充管理员签名QQ对话，也没有验证新的一场真实开局后的模型接管。QQ主项目没有修改、部署或重启。实战与综合验收保持暂停；用户实际开启需再次明确arm/start，测试结束不会留下接管授权。

DEV-097已实现并完成本任务验证，从TODO消费；VAL-003/005/006/009/012/013/014的真实综合验收不因此通过或恢复。更新仅同步两个麻将仓库，QQ主项目不纳入Git。
