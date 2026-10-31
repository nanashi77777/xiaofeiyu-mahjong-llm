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

已核验两个仓库归nanashi77777且private=true、默认分支main。上述本批实现分别同步feat/initial-websocket-runtime与feat/mahjong-readonly及各自main，均非强制快进且远端读回一致；重复同步上传数为0，见DEV-043。源代码和脱敏测试入库，本机凭据/牌谱/截图/浏览器/日志没有提交；QQ现有qq-system-prompt.js的无关工作区变更保留，未纳入本次提交。
