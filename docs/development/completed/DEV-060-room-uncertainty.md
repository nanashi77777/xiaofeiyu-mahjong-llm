# DEV-060 · 人机房未知结果持久化

类别：实现。完成日期：2026-10-31（Asia/Shanghai）。

真实房73344创建并添加三个电脑成功，但开始按钮事件后12秒内没有服务端确认，production-5plus10-short-v2.json在开局前停止。此前该路径只返回uncertain，没有写最终状态，重查同命令会一直得到旧submitted。

practice.py现在把server_confirmation_not_observed及uncertain持久化，再释放输入租约。同ID读取最后状态，不重新点击；submitted历史保留。旧回执不能确认新指令，真实新鲜认证与对局接管条件保持原约束。

验证：150项pytest与ruff通过，其中新增故障注入将确认等待缩为0，模拟已按下但无回执，核对持久化最终状态、释放租约及新PracticeRooms实例重查同ID仍不重按。该隔离测试不证明真实开局成功；DEV-059继续验证输入时序，DEV-047与VAL-013跟踪完整房间流程。

Git实际实现随本批提交，后续索引登记哈希。回退practice.py本项改动及测试；不清除真实失败或已提交指令历史。
