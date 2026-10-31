# 本机运行与验证

专用Chrome现在独立于服务进程运行。服务重启后，先用控制鉴权POST `/control/reattach-browser`，JSON为当前专用Chrome的`pid`，再绑定核对的`hwnd`。入口核验可执行文件、专用profile和唯一游戏页面，重新加载页面以收取真实认证/恢复；不会从旧缓存生成当前操作。若调试端口已有进程，`/control/browser`拒绝另开，不能把任意浏览器直接当作专用窗口。服务退出会保留独立浏览器，窗口可由用户正常关闭。

正式人机验收使用`tools/practice_suite.py --games 3 --profile 5+10 --output runtime/<本次报告>.json`，只在已认证空闲大厅、无输入所有者且源码摘要一致时开始。失败场不删、不续作合格场，恢复/离开提示干预独立记账。当前已有5+10模型超时失败证据，500窗口和三完整场尚未通过。

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

CLI以`majsoul-llm navigate --path <命令JSON>`提交，`majsoul-llm navigation --id <原ID>`查询；取消未提交指令或验证后续跑分别添加`--action cancel/resume`。JSON必须带稳定command_id，kind为login/ranked/join_friend/create_friend，具体字段见lobby_commands.py。join_friend默认ready=true，入房确认后识别“准备”标签并点击；明确只入房传ready=false。创建房间不自动开始。成功提交的匹配/入房不能用本机取消任务冒充服务端退出，可见取消适配尚待DEV-038。

入房和准备分别保存脱敏joinRoom/readyPlay确认；准备提交结果未知时不重按，加入已确认而准备尚未提交时续跑只在同一房间准备。历史确认可在重启后重验，但current_room_verified=false的历史结果不能说成仍在房间。局制已按可见UI与真实回包核验：一局mode=4、东风=1、半庄=2、人机=3；GameMode.ai=true也出现在普通房，不能据此判断选了人机。导航room.players是服务端人类座位容量，人机为1，但仍是四人麻将。

当前客户端房码键盘实测最多5位，54321完整加入并准备通过，01234前导零只做读回未提交。契约保留字符串输入，但超出客户端长度或读回不符会停止，不能宣称更长房码已支持。

QQ runtime/mahjong-navigation.json仅包含固定loopback baseUrl和navigationToken；原mahjong-readonly.json仍只含readToken。mahjong_navigate绑定当前会话和本轮真实消息生成ID；mahjong_navigation查进度或对原ID操作。新凭据登录、所有入口矩阵和自动接管仍按DEV-037..041、VAL-012/013跟踪；工具可调用不等于自动打牌/5秒目标已经完成。

## 协议与恢复

从当前客户端或自己有权使用的来源取得匹配版本的 protobuf schema，以 `majsoul-llm import-proto --path <文件>` 导入。生成代码和原 schema 只在 runtime/。当前本机协议已编译且真实读取成功；尚未把许可未确认的第三方 schema 复制入仓库。协议指纹显示在面板，更新客户端后需要重验。

SQLite runtime/archive.sqlite 使用 WAL，逐事件保存投影，保留 interrupted 记录。只有真实终场通知提供 final_result；小局和牌、流局或退出不能冒充终场。重连动作重放不重新点击历史 operation，快照缺时序时标 partial 并暂停接管，下一完整小局可重新建立状态。不清理本机数据库以制造“干净”测试。

## 界面校准

2026-10-31 当前入口为 Unity WebGL。定位从可见游戏画布截图读取，采用本机校准的牌面/按钮/组合模板，核对全部本家手牌的牌数和多重集合，单独核验赤牌与摸牌槽。已校准并核验的理牌行可根据至少三种已知牌面锚点学习新牌面，任一已知位置不符时停止，不凭坐标猜牌。模板保存在 runtime/calibration，不上传。

`POST /control/calibration` 仅控制令牌可用，参数 label、box=[x,y,w,h]、可选 hand_region；从当前画布截取，不接受任意文件路径。label 如 tile_0m、button_pass、combo_chi_3m_4m。组合模板包含完整所选消费组合；缺模板时不先点开菜单。界面缩放会按画布比例换算，比例或规则无法确认时暂停。真实鸣牌多组合、立直两步与所有皮肤仍需专项验收。

真实鼠标在目标处立即按下，跨一个显示帧释放并归位；人工移动后不覆盖其新位置。mouse_sent 与 server_confirmed 独立。跳过通过服务端 cancel_operation RPC 接受回执确认，不能一直等不存在的“跳过牌谱事件”；弃牌通过实际弃牌事件确认。

用户于2026-10-31要求尝试后台点击，新增browser_background模式：面板切换后重新开始接管，使用浏览器鼠标事件，系统鼠标不移动，也不抢前台。此模式允许窗口最小化；仍要求绑定窗口/页面身份、截图与手牌核验、状态版本/期限和急停。Unity需先更新一帧悬停目标再按下，不依赖系统前台焦点。隔离最小化测试与一次真实8m弃牌均已有证据，连续及多组合验收仍独立进行。

新增最小化真实1p弃牌服务端确认，系统光标与前台不变。为避免最小化后画布停帧，后台模式维持原视口尺寸CDP screencast，不请求缩放，帧立即丢弃、不进入牌谱；切回物理模式释放。停止/过期或渲染期间状态变化不按下。

2026-10-31修复用户反馈的反复伸缩：定位取图改为完整原生视口CDP截图，不传clip/scale，不滚动或设置设备度量；Python本地裁剪画布、归一化，单锁串行并核验取图前后几何。真实页面连续12次、DPR1.25/1.5各12次及独立最小化窗口12次取图验证通过；用户随后确认“现在稳定了”。独立最小化还验证持续渲染及浏览器鼠标事件，不代表所有实战按钮矩阵通过，当前54321准备窗口未为该测试最小化或重启。

大厅联调进一步修复低帧率Unity漏按：后台模式按下后等待两次渲染回调，抬起后再等一帧才归位；渲染异常也必须抬起。该行为会用于后续打牌执行，正常速度仍须重新实战计量，不能沿用旧样本证明5秒达标。

GitHub首页使用main，已验证开发提交须与main核对。若本机Git HTTPS因失效代理/网络不可达，执行`.venv\Scripts\python.exe tools/github_sync.py --also-main`通过GitHub API同步已提交的线性历史；脚本核验自有私库、所有将上传文件的秘密审计和原对象哈希，不强推、不上传工作区。QQ仓库可加`--root <qq-project>`。签名/merge等不支持时保留本机历史，走标准Git。

## 最小牌谱存储

SQLite v2的事件payload仅事实数据，场/局/序号等复用表列；小局与和牌引用原事件，最新缓存不含可重建公开时序。QQ小局事件只返回id/seq/kind/data。模型计划和执行日志独立，不为缩小牌谱删除已发生事实。迁移前先备份，失败事务回滚；旧程序拒写v2，回退须停止服务并配套恢复before-v2备份。

可先在副本检查旧库：`python tools/compact_archive.py <v1库路径> --report <本机报告路径>`。该工具只迁移临时副本，比较每条事件及最新场况。真实4场289事件样本的牌谱相关JSON减少68.44%，不代表日志或SQLite文件页有相同比例。

## 模型和计时

指定 your-model-id、Responses、reasoning.effort=none、store=false。完整独立上下文，每次不延长远程会话。非流式模式使用q/a/r/p；stream_actions=true时以完整首动作字段及逗号提前提交，r/p继续有界接收，正常服务器事件不取消解释。stream_action_format=nested输出d={q,a}/r/p，short输出a/r/p并由本机请求绑定q，默认保留nested。p不变为空，变化才输出完整本小局计划。两种流式格式都核对候选、窗口、版本、停止和输入截止；尾段失败不重发动作。token_usage保存真实usage；中断和缺usage保留null。详见streaming-action.md。

2026-10-31切换麻将专用密钥：decision、repair、review及经验迭代共用同一ModelClient，本机runtime/private.json的api_key覆盖全部麻将用途，不读取QQ/DSH凭据。控制面板usage_summary按billing_scope=mahjong及凭据指纹分组，并分decision/repair/review/probe；旧无标记请求单列legacy_unattributed，不记到新密钥。cached/reasoning是输入/输出中的子项，不能重复加到总token。缺usage显示未知，不据此推算供应商余额或货币金额。

实战5+20，正式模拟5+10。timer_verified=false时保守只使用基础时间，不假定可用整个共享池。已真实核验房规time_fixed为秒、operation.time_fixed/time_add为毫秒及5+10共享池消耗；恢复passed_waiting_time以秒计，实测189与本机188.359秒相符。恢复扣经过时间及保守余量，并保留此前共享池下界，不给旧窗口重新发满池。证据保留runtime/reload-recovery-evidence.json；5+20完整连续验收仍独立进行。

本次用户明确使用300+0联调，runtime/private.json 的 integration_test=true 仅在实际操作消息为300000毫秒且附加池为0时采用长联调期限。它不改变正式验收设置，300+0数据不能算5+10通过。

早期紧凑格式曾出现解释/引用错误，失败已保留；后续可读mj.readable.2只共享重复条件说明，semantic_actions可完全还原。最新配对每格式20次合法且无关键事实误读，真实input约减少12.94%，本机compact_validated=true启用此版本。仍保留完整牌河、事件、候选和当前相关役种事实，完整役种表不进入实时请求。流式和并行识别已接入，但模型TTFT及输入仍有长尾，500窗口和三场完整5+10尚未通过。

手牌空闲预识别与当前操作并行观察共用输入租约。只有新截图手牌像素、手牌/摸牌槽/副露签名、尺度和校准版本全同才复用牌实例映射；点击目标仍有原0.6秒缓存和1.2秒按下检查。vision_cache命中数不等于已节省毫秒。

## QQ查询

和牌归属无需迁移或复制旧牌谱。`GET /api/v1/mahjong/rounds/{id}`的wins和`matches/{id}`的小局索引给出荣和/自摸、winner_seat、from_seat、tile与原事件引用；绝对座位以self_seat对照本家。整场win_summary的deal_in_events按一次多家和去重，deal_in_winners分别计和牌者，自摸支付不计放铳。来源缺失/冲突保留unknown/conflict与issues，不能把未知补成猜测。big-hands的outcome补充相同归属，旧字段继续可用。QQ现有mahjong_history工具按需读取即可，不增加对话自动查询。

`python tools/audit_win_outcomes.py --output runtime/win-outcome-audit-<批次>.json`以只读一致性事务核验已有档案；passed仅表示无核对错误，fully_attributed另行表示没有未知/冲突。该工具不请求模型、不写生产库。复盘输入版本为review-input.v2-win-outcomes，新增win_outcomes及win_summary；已有完成复盘任务不会因版本升级自动重新排队。

<qq-project>\runtime\mahjong-readonly.json 只保存 loopback 地址与 readToken；四类 MCP 工具按需查询。不会每次对话访问，不提供控制或经验写接口。活动小局隐藏暗手、完整计划及解释自由文本；已结束小局可以读取记录。最近二十场范围由 scope_cursor 固定，包含中断记录；大牌只统计同一集合中的本家6番以上或役满。

## 验证工具

物理输入专项会实际操作一次性本机窗口并合成Esc；运行前自动查询生产输入必须空闲，接管/导航/结算进行中直接拒绝。先暂停生产接管，再运行`python tools/validate_physical_input.py --output runtime/physical-input-validation-<批次>.json`，不能与正式计时重叠。报告分别记录真实Windows点击和合成干预，不控制游戏。默认完整验证约数秒，桌面无法取得前台时保留受阻结果。

`python tools/validate_panel.py --output runtime/panel-validation-<批次>.json`在独立headless浏览器加载正式面板，拦截全部路由；面板断线/恢复、既有QQ导航指令和表单/响应布局可复现，所有控制请求只到构造处理器。`python tools/audit_archive.py --output runtime/archive-replay-audit-<批次>.json`对正式库建立只读一致性事务，重建事件并比较事实缓存，不修改生产数据；未知老字段或差异会保留并返回失败，不自动重写缓存。

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
