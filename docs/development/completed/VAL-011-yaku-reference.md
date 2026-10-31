# VAL-011 · 役种参考、实时隔离与复盘引用

类别：验收。完成日期：2026-10-31（Asia/Shanghai）。

标准四麻50项参考条目中，45项普通役/役满逐一与锁定mahjong 1.4.0的番数、门清/副露和倍数交叉核验；双倍变体替代基础项，宝牌三类与累计上限、流局满贯分开。来源、家族、互斥/替代与版本引用均由加载器验证。流局满贯保留partially_verified和特殊结算分类，未把未核实房间差异说成确定规则。具体计算与房规矩阵见VAL-002。

真实渠道出站捕获runtime/yaku-reference-validation-v2.json保存实际发送的构造局面payload、instructions及Schema，不含鉴权headers。核对其中没有完整表、catalog_version、verification_status、excluded_local_yaku等定义；仍有当前役路线、门清、无役约束和完整合法候选。该实际指定模型决策正常完成，输入9011、输出142、推理0 token；这是构造样本接口测试，不算真实游戏操作或速度验收。

同一独立验证库使用正式Reviewer、ModelClient和指定渠道两次请求，第一步按验证提示请求tanyao/chanta/junchan，第二步收到四个相关条目（包含必要互斥关联项）后给最终复盘。查询参数、standard-four-player.v1、完整实际返回和usage存本机。首次查询由测试明确要求，不假称模型自发生产查询；正式牌谱/经验库未修改。早期v1同样完成两次复盘调用，无打牌请求；v2补足实际实时出站隔离。

隔离test_review_lifecycle.py证明引用版本进入经验、无效证据事务回滚；test_yaku_reference_matrix.py和test_core.py核对选择性读取、未知ID拒绝及全表仅显式请求。经验更新仅能写memories，不具备役表/提示词/计算器写入口；来源版本与yaku_ids可追溯。207项pytest及ruff通过。

本项完成参考与引用专项，不把资料读取当成策略证明或牌局成绩改善。Git实现见8d02d92/8260d6d及eb6bb32，真实模型验证脚本已入库、响应仅在runtime；回退本批文档及检索代码不删除历史经验或旧版本引用。
