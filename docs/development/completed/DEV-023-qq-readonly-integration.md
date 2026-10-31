# DEV-023 · 小肥鱼按需麻将查询接入

类别：实现。完成日期：2026-10-31（Asia/Shanghai）。

四类只读MCP工具mahjong_live/history/big_hands/memories已在独立QQ部署接入，按需读取本机麻将服务。当前/历史、最近二十场固定范围、本家跳满以上和经验由服务投影；活动小局隐藏暗手、理由、计划及其引用。QQ只读令牌没有打牌控制能力，选场另走既有独立导航工具和令牌。

实现位置：QQ库bridge/src/mahjong-readonly.js、mcp-qq-chat.js、qq-mahjong-prompt.js及setup/verify/test脚本；麻将库queries.py和API。最小角色补充不要求每轮调用，不改变原白名单和触发规则。

验证：QQ完整回归193项通过；本轮再次从真实MCP stdio发现并调用四工具，全部available=true；另两个导航工具只读取状态，navigationSubmissions=0，externalMessages=0。本机报告保存在QQ runtime/mahjong-mcp-verification.json及mahjong-navigation-verification.json。本轮未修改QQ源码，用户未提交qq-system-prompt.js保持原状。

Git关联：QQ实现a4d0b25（P033）、a46546a92b2d71ac70b0c8a076fa0045b8b2382b（P034）、7fd45435d1e6f9ec6204133666098af0bc39fba1（P035）；麻将API及查询投影8260d6d369aa3658116c9bfcab17cba55fa0b4be。两个仓库均为用户私有origin。

限制与后续：此项证明工具接入与本机读链路，不证明每个首版业务VAL均已验收；完整QQ权限/真实经历由VAL-007继续，外部消息测试始终为零。回退分别恢复两库相关提交，不删除用户运行数据和独立记忆。
