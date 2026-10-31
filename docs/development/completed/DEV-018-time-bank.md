# DEV-018 · 5+20/5+10共享池与真实计时校准

类别：实现。完成日期：2026-10-31（Asia/Shanghai）。

monotonic贯穿读取/规则/模型/输入；共享池按小局重置、同窗扣账幂等、服务器余量向下夹紧，发牌额外基本时间独立处理，恢复按校准经过秒数扣时。

实现位置：src/majsoul_llm/timing.py、engine.py、coordinator.py。

验证与证据：test_time_bank_round_scoped_and_no_reconnect_refill、test_verified_server_bank_clamps_and_deal_animation_does_not_spend_pool、test_recovered_current_clock_subtracts_calibrated_age_and_never_refills_saved_bank；真实5+10与恢复校准见批次日志。

限制与后续：VAL-005尚未通过：首轮11机会、10确认、4次五秒内，最后模型超时；全部失败保留，未承诺性能已达标。

Git关联：本批实现提交后写入注册表和commit-index，不预填哈希。历史代码来源8d02d92044f5fa1272d4c0ff199523eb60db1c67及前序提交；新补强以实际提交为准。回退使用Git恢复相应源码与测试，保留本机所有事实和失败档案；本批不迁移或删除真实事件。
