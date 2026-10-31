# 后台输入采样与新轮开局验证

日期：2026-10-31（Asia/Shanghai）。关联DEV-055/059/060、DEV-047及VAL-003/005/006。

新服务v13加载a2b198c及此前按压前TargetExpired修复。production-5plus10-short-v2.json自动建房73344、设置四南5+10并添加三个电脑均确认；开始步骤记录submitted但未收到确认，38.14秒后测试停止，零新对局。失败截图仍为准备完毕的原房间，不能算完整对局或点击成功。

当前绑定浏览器只读61帧测量中，RAF间隔中位8.3ms，范围7.2～25.1ms，详见runtime/room-start-frame-intervals.json。两RAF不等于游戏输入循环的一帧，旧注释所称保证并不成立。新后台按压及释放后目标停留各同时等待100ms最短间隔与原RAF，原deadline和停止校验保留，按下后不重按。

隔离headless可见画布按15Hz采样按压/释放和指针位置，每种20次，不控制游戏：旧RAF方式0/20完成，新方式20/20完成。runtime/background-sampling-v1.json含全部样本与浏览器事件时间戳；tools/validate_background_sampling.py可复现。此证据证明旧方式会漏掉低频采样，不证明此前真实碰故障唯一根因已查明。新的雀魂开始/鸣牌仍待加载修复后验证，DEV-059保持进行中。

另修复没有确认时只返回uncertain却不持久化的缺陷，DEV-060独立消费。所有原失败报告保持原样，新轮使用独立文件，不把人工续录转换为正式通过。
