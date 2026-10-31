# DEV-051 · 立直选牌重采样定位修复

类别：实现。完成日期：2026-10-31（Asia/Shanghai）。

原真实5+10场在点击立直后，8p直接模板相关度0.8954低于0.91，导致无法打出所选牌。修复仅在立直选牌使用同核高斯平滑后的相关度与直接值取大，保留原阈值；另核验完整启用亮牌面的白色轮廓，防止相关系数对明暗不敏感而匹配禁用牌。所有同牌实例必须完整可见，普通手牌与最右摸牌槽分别映射，选择仍受服务器提供的立直候选约束。

实现：locator.py及tests/test_riichi_locator.py。143项全量pytest、ruff通过；分片与流式新增回归包含在同批。真实故障图离线恢复8p定位，同核平滑相关度0.95648，暗牌/缺实例负例拒绝。故障图继续保存在本机runtime/calibration/failures/2aafbe2fe42988462205.png，原失败不删。

新真实证据：混合开发场recorded-match-002，窗口8440afc42a23252dc092:op，模型选立直后两步可见点击完成，实际ActionDiscardTile 67768f9dc0b40264dc84与意图一致并带is_liqi=true。read_to_mouse=5563ms，elapsed确认=5672ms；本条验证立直行为，不计五秒通过。意图/实际完整核对存runtime/riichi-resampled-live-evidence.json，该场曾有失败与干预，不计正式完整场。

Git关联：本批实际提交后登记commit-index。回退恢复locator.py；保留故障及新真实证据。全按钮、布局和连续输入专项仍由DEV-019/021及VAL-003继续。
