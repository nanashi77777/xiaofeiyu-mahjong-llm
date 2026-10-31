# DEV-073 · 退出房间的真实回执与可见弹窗

完成日期2026-11-01。创建矩阵v1在真实单局3＋5房61594点击返回后已收到leaveRoom并回大厅；旧实现仍报dialog_opened，继续确认不存在的弹窗导致HTTP失败。原报告保留。

leave与confirm_leave均在按下前持久化leaveRoom意图，按新回执确定confirmed/rejected；仅实际看见已校准confirm_leave场景才报dialog_opened，其余无回执uncertain。重复ID返回原记录，不能重按。正式practice_suite只在真实弹窗结果时再确认，并核对当前房间已清空。

8项practice回归通过，含直接退出只点一次、真实弹窗、无回执、重复ID、错误房间及停止。全量266项pytest和ruff/任务检查通过。真实矩阵v2前四种时间房间均创建、获退出确认并读回room=null；第5条在下一次大厅按下前TargetExpired，保留为失败，不归因于退出错误。v3独立重新开展，完整创建选项由DEV-040/VAL-013验收，不随本任务关闭。

实际实现6b7c0ed（完整哈希由提交索引登记）。源码回退不删除本机原矩阵报告和真实回执；生产配置、截图与账号数据不入Git。
