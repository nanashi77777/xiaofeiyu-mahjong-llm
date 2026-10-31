# 参考项目、现有接口与许可

最初调查为静态只读；下文区分参考来源与已实施的复用。当前已有真实协议、模型及点击记录，完整计时/三场验收状态以任务登记为准。

| 来源 | 已核验用途 | 复用限制 |
| --- | --- | --- |
| [AutoMajsoul](https://github.com/yuhao7370/AutoMajsoul) | ADB/OpenCV/Torch视觉、牌块与按钮定位；默认分支最近提交2025-12-21 | CC BY-NC-SA 4.0；视觉场况有多项TODO，不能当完整状态引擎；派生代码/模型按实际许可处理 |
| [majsoul_wrapper](https://github.com/747929791/majsoul_wrapper) | mitmproxy/Liqi、操作列表、GUI执行参考；默认分支最近提交2022-06-21 | MIT；旧SDK漏多家和/重连等字段，鼠标固定等待/固定归位不符合新目标 |
| [MajsoulAI](https://github.com/747929791/MajsoulAI) | 事件桥接、座位/牌编码与流程参考；默认分支最近提交2022-03-04 | MIT；传统JianYangAI不能用来做最终动作；其submodule实际指向747929791/MahjongAI fork |
| [erreurt/MahjongAI](https://github.com/erreurt/MahjongAI) | 手牌/役种/向听与传统策略架构参考；默认分支最近提交2019-02-15 | 仓库未发现许可证，不能按默认可复制处理；首版只参考思路，不复制未授权源码 |

首版自主实现领域与协调层，采用明确许可的mahjong计算库。已复用majsoul_wrapper的MIT牌面分类权重并转换为ONNX，来源、原版本、转换说明及许可证见src/majsoul_llm/assets/majsoul_wrapper/source.json和LICENSE.txt，转换工具为tools/convert_tile_classifier.py。AutoMajsoul的受限视觉内容和无许可证的MahjongAI源码未复制。不得为其他派生内容笼统套MIT许可；详见THIRD_PARTY_NOTICES.md。

## 本地项目

- `<legacy-mahjong-project>`：纯视觉＋本地计算玩家。可参考 vision.py、native.py、actions.py、flow.py、replay.py、memory.py、mcp_memory.py 的界面与数据边界；不要直接调用 Strategy.decide()。旧库只保留每类10场/10次大牌，big_hands缺match外键，不能满足本项目最近二十场关联查询。
- `<qq-project>`：实际QQ部署为 NapCat → Node bridge → 本机DSH → 模型API，入口 bridge/src/dsh-desktop-local.mjs。跨活动只读经历可参考 mcp-qq-chat.js 和 bilibili-experience.js。QQ人际记忆有真实用户意图约束，麻将自主经验必须独立。
- 旧麻将AGENTS中的“不调用大模型”针对旧目录，不移植到新项目；共同保留独立目录、真实输入、事实与意图分离、隐私与许可约束。

## 较新的协议证据

- [MahjongRepository/mahjong_soul_api protocol.proto](https://github.com/MahjongRepository/mahjong_soul_api/blob/master/ms/protocol.proto)：认证、快照、operation和计时字段来源之一；引入前核对当时版本与许可证。
- [Akagi v3 雀魂桥接说明](https://github.com/shinkuan/Akagi/blob/v3/src/bridge/majsoul/README.md)：恢复、立直、杠与宝牌时点的交叉参考。
- [Akagi v3 budget.rs](https://github.com/shinkuan/Akagi/blob/v3/src/autoplay/budget.rs)：计时单位与time_add未验证语义的证据。

这些是说明和对照来源，不因此增加用户要求之外的传统AI决策后端。源代码活跃时间不等于本机兼容性；验收使用固定版本和脱敏真实包。

## 模型渠道事实

本项目已使用指定基线模型、Responses、`reasoning.effort=none`，真实验证JSON Schema、SSE首动作、尾段、usage和取消；证据见VAL-004完成记录。`本机供应商监控页`的页面壳可访问，但真正状态接口需网页登录。监控只作人工辅助，不当实时动作依赖；TTFT不等于完整操作耗时。

麻将专用密钥已用于本机决策与复盘，所有用量按mahjong及凭据指纹登记。凭据与实际usage、模型结果保存在被忽略的runtime，代码和文档不包含密钥；未将监控登录设为运行依赖。
