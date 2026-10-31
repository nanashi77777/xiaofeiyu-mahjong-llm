# DEV-006 · 当前协议与实时/恢复解码

类别：实现。完成日期：2026-10-31（Asia/Shanghai）。

协议描述从本机runtime/protocol加载、编译并计算描述指纹，不向仓库再分发许可尚未确认的schema。实时ActionPrototype按当前字节异或编码解码；恢复actions按未异或载荷解码。RPC用连接与请求号配对，另一连接/重复回复不能串线。大厅登录请求完全不解码；登录/房间回复采用最小投影。游戏auth映射只在内存，实际操作请求和服务器ack仅作执行证据。

未知/无法解码的Action明确暂停并保存仅该Action的名称、schema/载荷SHA256、历史标志和最多64KiB原始Action证据，排除外层认证/RPC载荷；全部证据留忽略的本机records。已解码但reducer不支持的消息保存失败类型和对应游戏Action，不能静默丢掉后继续打。正常牌谱只保存解码事实一份，不为每帧增加原文副本。

验证：本项目独立编写的最小protobuf样例核对实时/恢复同义、RPC去重、登录载荷不解码、准备布尔值及未知Action证据恢复。当前客户端真实友人场开局、摸打、副露、立直、小局/终场已持续解码；真实恢复历史曾有16个动作，opened_operation=false。5+10新鲜计时已直接核对；恢复passed_waiting_time尚未校准，旧窗口保持关闭，由DEV-018/VAL-001继续跟踪。不能宣称支持未来任意协议版本。

实现：protocol.py、engine.py；来源与许可见THIRD_PARTY_NOTICES.md。当前回归见同批日志。Git关联由注册表/提交索引补入；回退恢复源码和本机先前schema，旧事实档案保留。
