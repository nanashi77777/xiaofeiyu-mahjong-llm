# DEV-024 · 经验与风格的独立修订库

类别：实现。完成日期：2026-10-31（Asia/Shanghai）。

独立经验库保留candidate/stable/inactive与不可变修订历史，来源场局事件、替代/停用原因、catalog版本/役ID可追溯。风格轴限定-2..2，按情境适用；写入口不允许改事实、代码或正式规则表。

实现位置：src/majsoul_llm/memory.py、domain.py。

验证与证据：tests/test_review_lifecycle.py、test_queries_and_model.py；三后续场证据门槛、任意声称复核不计数、改主张重置支持、版本冲突/越界拒绝、事务回滚通过。

限制与后续：VAL-008独立检查真实后续场生效与反例；少量样本不能证明成绩提高。

Git关联：本批实现提交后写入注册表和commit-index，不预填哈希。历史代码来源8d02d92044f5fa1272d4c0ff199523eb60db1c67及前序提交；新补强以实际提交为准。回退使用Git恢复相应源码与测试，保留本机所有事实和失败档案；本批不迁移或删除真实事件。
