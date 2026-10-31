# 本机运行与验证

源码：<project-root>。私有远端：https://github.com/nanashi77777/xiaofeiyu-mahjong-llm 。

## 安装和启动

Python 3.12，Windows，Chrome。首次在本目录执行：

```powershell
C:\Python312\python.exe -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.lock.txt
.\.venv\Scripts\python.exe -m pip install --no-deps -e .
.\.venv\Scripts\majsoul-llm.exe init-config
```

在被忽略的 runtime/private.json 配置 api_key；也可通过 MAJSOUL_API_KEY 环境变量覆盖。read_token、navigation_token和control_token分别用于查询、窄导航、通用本机控制，自动生成且权限分离。旧配置升级需保存新增导航令牌后重启，再把navigationToken单独提供给QQ导航适配器。禁止将配置、浏览器资料、牌谱或截图提交到 Git。

```powershell
.\.venv\Scripts\majsoul-llm.exe serve
.\.venv\Scripts\majsoul-llm.exe panel
```

面板在 http://127.0.0.1:8765 。panel 命令通过本机 URL fragment 带入控制令牌，不进入 HTTP 请求地址或服务访问日志。专用浏览器由面板打开，独立 profile，不读取其他浏览器的登录资料。手动登录、进入友人场；默认停止输入。先使用仅记录模型选择，确认状态、计时和定位，再绑定窗口和开始接管。Esc、暂停、停止均阻止后续输入。不要使用未验证定位开始段位场。

启动显式关闭Playwright固定视口，并核验本窗口最大化，游戏根据实际可用区域适配。面板“恢复完整窗口”先停接管再最大化；需要重新开启接管。截图前核验完整canvas在当前页面视口内，截断时暂停定位，防止截图自动滚动掩盖不完整窗口。本机125%缩放的真实大厅、手动缩小/恢复、后台最小化已核验；其他显示器/皮肤仍需专项验证。

登录与选场已提供面板表单、CLI和QQ工具，见lobby-automation.md及本批开发日志。登录命令复用有效profile并等待实际认证回包；没有有效登录态时显示waiting_user，新凭据/验证码需在专用浏览器完成，不能将此阶段说成完整自动登录已交付。页面适配使用runtime/navigation本地模板，未校准/歧义/精确选项读回失败时暂停。

CLI以`majsoul-llm navigate --path <命令JSON>`提交，`majsoul-llm navigation --id <原ID>`查询；取消未提交指令或验证后续跑分别添加`--action cancel/resume`。JSON必须带稳定command_id，kind为login/ranked/join_friend/create_friend，具体字段见lobby_commands.py。入房不会自行准备/开局。成功提交的匹配/入房不能用本机取消任务冒充服务端退出，可见取消适配尚待DEV-038。

QQ runtime/mahjong-navigation.json仅包含固定loopback baseUrl和navigationToken；原mahjong-readonly.json仍只含readToken。mahjong_navigate绑定当前会话和本轮真实消息生成ID；mahjong_navigation查进度或对原ID操作。新凭据登录、所有入口矩阵和自动接管仍按DEV-037..041、VAL-012/013跟踪；工具可调用不等于自动打牌/5秒目标已经完成。

## 协议与恢复

从当前客户端或自己有权使用的来源取得匹配版本的 protobuf schema，以 `majsoul-llm import-proto --path <文件>` 导入。生成代码和原 schema 只在 runtime/。当前本机协议已编译且真实读取成功；尚未把许可未确认的第三方 schema 复制入仓库。协议指纹显示在面板，更新客户端后需要重验。

SQLite runtime/archive.sqlite 使用 WAL，逐事件保存投影，保留 interrupted 记录。只有真实终场通知提供 final_result；小局和牌、流局或退出不能冒充终场。重连动作重放不重新点击历史 operation，快照缺时序时标 partial 并暂停接管，下一完整小局可重新建立状态。不清理本机数据库以制造“干净”测试。

## 界面校准

2026-10-31 当前入口为 Unity WebGL。定位从可见游戏画布截图读取，采用本机校准的牌面/按钮/组合模板，核对全部本家手牌的牌数和多重集合，单独核验赤牌与摸牌槽。已校准并核验的理牌行可根据至少三种已知牌面锚点学习新牌面，任一已知位置不符时停止，不凭坐标猜牌。模板保存在 runtime/calibration，不上传。

`POST /control/calibration` 仅控制令牌可用，参数 label、box=[x,y,w,h]、可选 hand_region；从当前画布截取，不接受任意文件路径。label 如 tile_0m、button_pass、combo_chi_3m_4m。组合模板包含完整所选消费组合；缺模板时不先点开菜单。界面缩放会按画布比例换算，比例或规则无法确认时暂停。真实鸣牌多组合、立直两步与所有皮肤仍需专项验收。

真实鼠标在目标处立即按下，跨一个显示帧释放并归位；人工移动后不覆盖其新位置。mouse_sent 与 server_confirmed 独立。跳过通过服务端 cancel_operation RPC 接受回执确认，不能一直等不存在的“跳过牌谱事件”；弃牌通过实际弃牌事件确认。

用户于2026-10-31要求尝试后台点击，新增browser_background模式：面板切换后重新开始接管，使用浏览器鼠标事件，系统鼠标不移动，也不抢前台。此模式允许窗口最小化；仍要求绑定窗口/页面身份、截图与手牌核验、状态版本/期限和急停。Unity需先更新一帧悬停目标再按下，不依赖系统前台焦点。隔离最小化测试与一次真实8m弃牌均已有证据，连续及多组合验收仍独立进行。

新增最小化真实1p弃牌服务端确认，系统光标与前台不变。为避免最小化后画布停帧，后台模式维持低分辨率CDP screencast，帧立即丢弃、不进入牌谱；切回物理模式释放。停止/过期或渲染期间状态变化不按下。

大厅联调进一步修复低帧率Unity漏按：后台模式按下后等待两次渲染回调，抬起后再等一帧才归位；渲染异常也必须抬起。该行为会用于后续打牌执行，正常速度仍须重新实战计量，不能沿用旧样本证明5秒达标。

GitHub首页使用main，已验证开发提交须与main核对。若本机Git HTTPS因失效代理/网络不可达，执行`.venv\Scripts\python.exe tools/github_sync.py --also-main`通过GitHub API同步已提交的线性历史；脚本核验自有私库、所有将上传文件的秘密审计和原对象哈希，不强推、不上传工作区。QQ仓库可加`--root <qq-project>`。签名/merge等不支持时保留本机历史，走标准Git。

## 最小牌谱存储

SQLite v2的事件payload仅事实数据，场/局/序号等复用表列；小局与和牌引用原事件，最新缓存不含可重建公开时序。QQ小局事件只返回id/seq/kind/data。模型计划和执行日志独立，不为缩小牌谱删除已发生事实。迁移前先备份，失败事务回滚；旧程序拒写v2，回退须停止服务并配套恢复before-v2备份。

可先在副本检查旧库：`python tools/compact_archive.py <v1库路径> --report <本机报告路径>`。该工具只迁移临时副本，比较每条事件及最新场况。真实4场289事件样本的牌谱相关JSON减少68.44%，不代表日志或SQLite文件页有相同比例。

## 模型和计时

指定 your-model-id、Responses、reasoning.effort=none、store=false。完整独立上下文，每次不延长远程会话。输出短键 q/a/r/p，程序还原领域字段；p 可为空或为完整本小局计划。token_usage 保存真实 usage；中断和缺 usage 保留 null。

2026-10-31切换麻将专用密钥：decision、repair、review及经验迭代共用同一ModelClient，本机runtime/private.json的api_key覆盖全部麻将用途，不读取QQ/DSH凭据。控制面板usage_summary按billing_scope=mahjong及凭据指纹分组，并分decision/repair/review/probe；旧无标记请求单列legacy_unattributed，不记到新密钥。cached/reasoning是输入/输出中的子项，不能重复加到总token。缺usage显示未知，不据此推算供应商余额或货币金额。

实战5+20，正式模拟5+10。timer_verified=false 时保守只使用基础时间，不假定可用整个共享池。实际房规 time_fixed 以秒、operation.time_fixed 以毫秒已有300+0样本证据；剩余 time_add 和恢复经过时间的语义仍待5+10/5+20验证。

本次用户明确使用300+0联调，runtime/private.json 的 integration_test=true 仅在实际操作消息为300000毫秒且附加池为0时采用长联调期限。它不改变正式验收设置，300+0数据不能算5+10通过。

紧凑格式增加完整特征/子事实共享，semantic_actions 可完全还原；目前真实配对仍发现解释或引用错误，因此 compact_validated=false，尚未正式启用。小样本已将部分请求从十多秒降至4至6秒，仍有超时，未达到“绝大部分低于5秒”验收。

## QQ查询

<qq-project>\runtime\mahjong-readonly.json 只保存 loopback 地址与 readToken；四类 MCP 工具按需查询。不会每次对话访问，不提供控制或经验写接口。活动小局隐藏暗手、完整计划及解释自由文本；已结束小局可以读取记录。最近二十场范围由 scope_cursor 固定，包含中断记录；大牌只统计同一集合中的本家6番以上或役满。

## 验证工具

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\ruff.exe check src tests tools
.\.venv\Scripts\python.exe tools/tasks.py check
.\.venv\Scripts\python.exe tools/audit_secrets.py --staged
.\.venv\Scripts\python.exe tools/benchmark.py --count 16 --schema
.\.venv\Scripts\python.exe tools/integration_once.py
```

benchmark 是真实渠道调用加构造场况，明确不包含物理点击，不能冒充500窗口或完整实战验收。integration_once 默认只记录；--execute 才使用实际鼠标，并且仅接受本次已授权的300+0模式。--hover-frame 仅用于诊断一帧悬停与立即点击的差异。

停止服务可 Ctrl+C，保留浏览器 profile、数据库及任务证据；先停止接管再手动操作。回退源码使用具体提交 git revert，QQ集成在其独立仓库回退；不回滚/删除运行牌谱来覆盖真实经历。
