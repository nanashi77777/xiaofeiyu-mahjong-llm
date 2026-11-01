# 实现提交索引

DEV-081公开发布时重建了完整脱敏历史，当前表中本项目提交引用已映射为公开历史的实际哈希；原始映射只留本机。外部QQ集成记录独立保留，不随本仓库公开其部署内容。

| 公开发布实现 | 本项目实际提交 | 完成记录 |
| --- | --- | --- |
| 全历史脱敏、作者通用化、公开仓库所有权核验与完整历史审计 | e90dca14df54e8df1afd67c9074350a32a7e9072 | DEV-081；330项pytest，初次43提交/1036对象审计，旧172敏感对象不可读取，原始库始终私有归档 |

2026-11-01后续双库更新已按用户要求恢复私有库写入；原分支保留，新增mirror/分支保存与公开库相同的脱敏历史。

| 后续实现 | 本项目实际提交 | 完成记录 |
| --- | --- | --- |
| 默认双库同步、私有镜像分支与暂缓恢复TODO | 22dceea0af0ddf5916106b07a302f3f2e79d3606 | DEV-083；DEV-082与VAL-014仅登记，未开发 |
| GitHub连接复用、批量审计及安全错误报告 | 3c01c8d9e0ba50a7b92a2638c63dae819446ea1c | DEV-083；30项相关pytest/ruff，真实四ref一致 |
| 实际请求引用、自动操作排除及只读输入归因审计 | 3fad22d7269094682288a41b6e9e2f7a6e8ba387 | DEV-084；368项pytest/ruff，新真实弃牌/跳过/立直/荣和/吃请求关联通过；完整VAL另验 |
| 非弃牌识别原帧与目标诊断 | c33eb9ada818cb97a1d2b30e8c22e7f0671b1dd1 | DEV-055原诊断实现，后续请求关联由DEV-084完成；恢复已移交暂缓任务 |
| 有限模型/定位重试、不可重复输入阶段与新窗口恢复 | 20006e861e78d6522cb12b2e40913dbfd32c9a6c | DEV-082；402项pytest/ruff，VAL-014真实恢复验收独立记录 |
| 独立功能实战模式与专用浏览器断开失效 | e2bd37f52497f83eab8971ee2b0c417b080a546b | DEV-085/086；410项pytest/ruff，三场功能与真实恢复继续单独验收 |
| 食替置灰牌完整定位及加入友人房实现结项 | d08e533eb4abf5a67c4210d4b5ec9362d2c76c70 | DEV-087/039；418项pytest/ruff，两库四ref一致且写权限核验通过；普通筒子纹样遗漏另由DEV-088修复 |
| 普通牌灰色纹样与禁打牌分割隔离 | 4befec8a24795fff749c243824038b7f7beca9a2 | DEV-088；418项pytest/ruff，两份真实冻结帧正确映射；双库四ref核验通过 |
| 可审计旧索引重建、终场奖励页及不可覆盖截图 | 83ba4a6 | DEV-089/090；最终433项pytest/ruff，真实旧索引修复及20场重放一致，真实奖励页确认返回room |

日期：2026-10-31（Asia/Shanghai）。哈希均为已存在的实际提交，不以提交自身内容猜测哈希。

| 范围 | 仓库与实际提交 | 任务/记录 |
| --- | --- | --- |
| 私有仓库与实施授权基线 | xiaofeiyu-mahjong-llm · ced75d3 | DEV-001 |
| 首版运行代码、任务消费工具、私密配置、后台输入及最小牌谱 | xiaofeiyu-mahjong-llm · e53fe8a | DEV-002、DEV-003、DEV-033；其余任务的实现进展见2026-10-31-runtime-progress.md，尚未冒充全部验收 |
| 自动登录/选场扩展方案，真实视口修复与麻将新密钥全用途分账 | xiaofeiyu-mahjong-llm · fe11c9f | DOC-004、DEV-034、DEV-035；M9运行实现仍待开发 |
| QQ按需只读工具、幂等角色补充与媒体测试修复 | qq-integration-project · a4d0b25 | 独立库P033完成记录 |
| 大厅命令/输入租约、QQ导航接口、后台按帧点击与GitHub API同步 | xiaofeiyu-mahjong-llm · 5a7d89c3342f2c401d95043f4dda3324adfea8aa | DEV-036、DEV-042、DEV-043；页面完整矩阵和自动打牌交接仍在M9/VAL任务 |
| QQ选场工具、当前真实消息绑定与独立导航配置 | qq-integration-project · a46546a92b2d71ac70b0c8a076fa0045b8b2382b | 独立库P034；与麻将DEV-036/DEV-042配套 |
| 真实局制/持久确认、54321加入准备、原生稳定取图 | xiaofeiyu-mahjong-llm · 3f4b2d4228e78481c173039075d6b7bcffb615d3 | DEV-044、DEV-045、DEV-046；66项pytest/ruff、真实与隔离取图、用户确认稳定 |
| QQ加入默认准备及只入房契约、角色部署 | qq-integration-project · 7fd45435d1e6f9ec6204133666098af0bc39fba1 | 独立库P035；193项回归、六类MCP读取、4会话重载可用，零测试外发 |

| 自主人机房、输入生命周期与首轮实战联调 | xiaofeiyu-mahjong-llm · afe7ffa923e2cf282f19986031e05b589f52b222 | 自主人机进度记录；该轮未消费完整实战VAL |
| 领域/采集/协议/归并/场局/归档/模型/役种任务消费与生产测试工具 | xiaofeiyu-mahjong-llm · b58eda77e80cabc35a59417d3fe068e439ac4b4c | DEV-004/005/006/007/008/010/015/031；108项pytest、ruff及任务检查，实战VAL继续开放 |
| 恢复时钟、并行界面观察、离开恢复与实现任务消费 | xiaofeiyu-mahjong-llm · 94fc9601057988670bc3465852d97077ae402e8f | DEV-009/011/012/013/014/016/017/018/022/024/025/026/030/048；124项pytest，生产5+10首轮失败如实保留，两个远端分支均已读回核验 |
| 过期识别撤销与已恢复旧复盘撤队 | xiaofeiyu-mahjong-llm · 41f90a2348d4263415e685c7c4b71eb602ad1d8d | DEV-049/050；126项pytest，两个远端分支已核验 |
| 流式首动作、延后解释、手牌预观察和立直选牌修复 | xiaofeiyu-mahjong-llm · d48837f8c214288eacb4eaa984faea6f65c02a8f | DEV-023/051/052/053；143项pytest、两种格式各10次真实模型探针、新真实立直，两个远端分支已核验；short实战及完整VAL另跟踪 |
| 同帧归一化复用、低频图像诊断及真实结算消费 | xiaofeiyu-mahjong-llm · cc653dd0280f7d9ed845743bb53477aae8f5cc63 | DEV-056/058；149项pytest、真实三页面确认并返回房间 |
| 低频输入采样保护与房间未知状态持久化 | xiaofeiyu-mahjong-llm · 28356145be3b0b06b2665b2e3ae43ff6e2a2e6e4 | DEV-059/060；15Hz隔离20/20，真实开始/碰/立直/荣和后续确认 |
| 服务端时间池校正、多赤和暗杠未知事实、规则专项 | xiaofeiyu-mahjong-llm · 6d27e4bba61f059eda4b9e9ef07ea83ecb2a8111 | DEV-047/061/062、VAL-002；207项pytest，正式5+10第37机会失败继续保留 |
| 经验生命周期及生产只读证据审计 | xiaofeiyu-mahjong-llm · 1baa37b9878455ae760e698ad72afd7e81de5e3a | VAL-008；6条经验7版本、500非空召回，非性能验收分母 |
| 指定模型与役种参考真实验收消费 | xiaofeiyu-mahjong-llm · 161d7aca40c960c1af1d01ddfedfdbf705aff5b9 | VAL-004/011；沿用真实出站和Reviewer证据 |
| 物理输入隔离、面板恢复与只读事件重放审计 | xiaofeiyu-mahjong-llm · b96aa5d2bcf7dfa7abf861022b9b7e34b68cdbe0 | 物理13项、面板13项；近期13场缓存匹配，3早期差异保留，完整VAL继续 |
| 荣和放铳/自摸归属、历史查询及复盘输入 | xiaofeiyu-mahjong-llm · b3f6a72454cefc2d1af580e82f67cfe3f5935096 | DEV-063；237项pytest、16场53条真实归属、HTTP与QQ MCP历史详情通过 |
| 低频按钮校准、全菜单核对与面板慢请求期限 | xiaofeiyu-mahjong-llm · 2cf6c7aa05cef59f82b02489ac729bc86697df74 | DEV-064/065；240项pytest、真实双吃核验、面板20项；两远端ref已读回核验 |
| 三吃与更多赤牌组合跨层验证 | xiaofeiyu-mahjong-llm · 5ee227541e79cc9683e88c1becf3b0c5dbce302c | DEV-066；246项pytest，真实三吃及布局范围仍由独立输入验收跟踪 |
| 多组合观察范围、面板模式、具名列候选及实现任务消费 | xiaofeiyu-mahjong-llm · 9054eaac25afb603ea1eefbde219d4472b53d875 | DEV-019/020/021/027/028/032/068/069；251项pytest，具名列DEV-067仍有独立门禁；两个远端ref已读回核验 |

| 停止后显式结果页入口、QQ查询隐私、运行文档及具名列阈值 | xiaofeiyu-mahjong-llm · cba927fdcfa51aa17b549d0d0b8a1d199eb7ce56 | DEV-029、VAL-007；259项pytest；DEV-070仍待真实入口验证，两个远端ref已读回核验 |

| 具名列验证结项与剩余验收口径 | xiaofeiyu-mahjong-llm · d51d2ad9ebc1ff58a039bebc5132637f772e69d8 | DEV-067；双ref已核验 |
| 跨小局恢复序号及真实字形多选验证 | xiaofeiyu-mahjong-llm · dcc4bb257a47d87879b5bcbd3cb200474a70d622 | DEV-071/072；263项pytest、四类菜单16次独立点击；双ref已核验 |

| 直接退房回执及最终rich/compact对照工具 | xiaofeiyu-mahjong-llm · d26aface75464a9db8b058858418121a75f509f8 | DEV-073、DEV-040、VAL-010；266项pytest，20种真实建房组合及18组真实模型配对；完整实战另验 |

| 本机模型配置、并行帧交接、图像线程及无明显收益试验结项 | xiaofeiyu-mahjong-llm · ffc8401fc01b4ab20f86afca46c1b4852a273e02 | DEV-070/074/075/076/077；300项pytest/ruff，两个远端ref已读回核验；新5＋10另验 |

| 选择相关观察交接、等待上限和完整输入耗时诊断 | xiaofeiyu-mahjong-llm · b02fb0ae67c20d1079542ded1b0dfa9b8bbbeac8 | DEV-078；307项pytest/ruff，新5＋10真实路径验证；两个远端ref已核验，完整场仍独立验收 |

| 经验召回8条与生成端短文本约束 | xiaofeiyu-mahjong-llm · 5d7131f1388b74bc2c97a1996afac9aa44388a7b | DEV-079；最终31项相关测试/ruff，长历史不截断；部署与正式实战条件分别记录 |

| 实际模型配置仅留本机、暂存/上传隐私审计与历史情况披露 | xiaofeiyu-mahjong-llm · e2e8c8f3c75ea3158224533afc424c8a4e94affe | DEV-080及DEV-079补充回归；324项pytest、ruff、当前248文件零命中；旧提交中的渠道/模型信息未改写 |

2026-10-31时已核验麻将与QQ两个项目仓库归nanashi77777且private=true、默认分支main。上述当时实现分别同步feat/initial-websocket-runtime与feat/mahjong-readonly及各自main，均非强制快进且远端读回一致；重复同步上传数为0，见DEV-043。源代码和脱敏测试入库，本机凭据/牌谱/截图/浏览器/日志没有提交；QQ现有qq-system-prompt.js的无关工作区变更保留，未纳入该批提交。当前麻将公开库与私有镜像的双库状态以DEV-083完成记录为准。
