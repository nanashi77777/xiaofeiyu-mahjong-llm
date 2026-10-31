# 实现提交索引

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

已核验两个仓库归nanashi77777且private=true、默认分支main。上述本批实现分别同步feat/initial-websocket-runtime与feat/mahjong-readonly及各自main，均非强制快进且远端读回一致；重复同步上传数为0，见DEV-043。源代码和脱敏测试入库，本机凭据/牌谱/截图/浏览器/日志没有提交；QQ现有qq-system-prompt.js的无关工作区变更保留，未纳入本次提交。
