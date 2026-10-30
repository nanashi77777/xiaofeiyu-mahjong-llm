# 实现提交索引

日期：2026-10-31（Asia/Shanghai）。哈希均为已存在的实际提交，不以提交自身内容猜测哈希。

| 范围 | 仓库与实际提交 | 任务/记录 |
| --- | --- | --- |
| 私有仓库与实施授权基线 | xiaofeiyu-mahjong-llm · ced75d3 | DEV-001 |
| 首版运行代码、任务消费工具、私密配置、后台输入及最小牌谱 | xiaofeiyu-mahjong-llm · e53fe8a | DEV-002、DEV-003、DEV-033；其余任务的实现进展见2026-10-31-runtime-progress.md，尚未冒充全部验收 |
| QQ按需只读工具、幂等角色补充与媒体测试修复 | qq-integration-project · a4d0b25 | 独立库P033完成记录 |

已核验两个仓库归nanashi77777且private=true。实现分别推送feat/initial-websocket-runtime与feat/mahjong-readonly开发分支。源代码和脱敏测试入库，本机凭据/牌谱/截图/浏览器/日志没有提交；QQ现有qq-system-prompt.js的无关工作区变更保留，未纳入本次提交。
