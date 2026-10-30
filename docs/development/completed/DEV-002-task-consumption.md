# DEV-002 · 任务消费与完成日志

类别：实现；日期：2026-10-30（Asia/Shanghai）。

实现 tools/tasks.py，以注册表为唯一来源生成 TODO/DONE，支持 start、ready、block、cancel、done。done 必须存在完成记录及证据，依赖必须完成；关闭任务不能重新打开，应另建后续任务。取消与受阻必须说明原因，代码落地验证中显示 code_ready。

验证：`.venv/Scripts/python.exe -m pytest tests/test_tasks.py -q`，1 passed。临时工作区覆盖消费后不回流 TODO、视图过期、重复编号、依赖缺失与环、受阻/取消/代码落地视图；真实注册表 check 通过（45项）。

实现文件：tools/tasks.py、tests/test_tasks.py。Git 关联由后续日志索引绑定实际提交，不填自引用哈希。后续所有实现与实战验收继续独立消费。

实际实现提交：e53fe8a，见../commit-index.md；任务注册表同步绑定。

回退：通过 Git 恢复工具与注册表及两份视图的一致版本；不手工只修改 TODO。
