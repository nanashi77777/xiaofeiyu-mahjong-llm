# 来源和许可记录

项目实现为独立新代码，未复制无许可的 MahjongAI 源码，也未将 AutoMajsoul 的 CC BY-NC-SA 视觉模型或素材重新发布为 MIT。四个用户指定项目的研究结论与链接见 docs/design/references.md。

- yuhao7370/AutoMajsoul：CC BY-NC-SA 4.0，仅研究界面/流程。
- 747929791/majsoul_wrapper、747929791/MajsoulAI：MIT，研究协议与服务分层；版本协议不是通用规则保证。
- erreurt/MahjongAI：未确认可用许可证，仅研究思路，不复制实现。
- MahjongRepository/mahjong 1.4.0：MIT，作为依赖用于向听与成役算点事实；不使用传统AI策略打分决定动作。
- MahjongRepository/mahjong_soul_api 当前 schema：本机调查副本，仅置于被忽略 runtime/protocol；发布前未确认其 schema 的可再分发许可，不将它纳入源码仓库。
- shinkuan/Akagi：协议与界面行为调查参照；未复制源码，未附带其资产。
- 本机截图校准模板来自用户此次雀魂界面，仅保存在 runtime/calibration。游戏商标、UI和素材权利归原权利人，不作为本项目开源资产发布。

依赖由 requirements.lock.txt 锁定；使用各上游发行包自带的许可证。主要组件：FastAPI/Pydantic/HTTPX/Uvicorn（MIT/BSD），Playwright/OpenCV（Apache-2.0），NumPy（BSD），protobuf（BSD-3-Clause），grpcio-tools（Apache-2.0），pytest/pytest-asyncio（MIT/Apache-2.0），Ruff（MIT）。此列表不替代发行包完整许可证及其第三方通知。
