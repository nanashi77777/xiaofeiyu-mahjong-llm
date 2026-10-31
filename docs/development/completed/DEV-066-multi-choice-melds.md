# DEV-066 · 三吃与更多鸣牌组合

类别：实现；完成日期：2026-10-31（Asia/Shanghai）。用户追加少数三吃等情况也必须处理。

## 变更与原因

规则层原已遍历全部服务端combination并保留赤牌消费实例，独立模型上下文也不截断为两个；本次补齐跨层验证。定位层先按行整理牌面再按间隔分组，避免多行菜单被全局x排序交错；可识别三组、更多赤牌变体与多行，不按界面顺序给组合身份。沿用DEV-064全菜单核对，不完整、额外、重复、错赤牌、低置信或歧义不点击。

## 验证范围与证据

- tests/test_multi_choice_melds.py构造菜单图：五组组合包括普通五/赤五两条路线，单行及两行排列均逐项匹配；遗漏、额外或重复组合拒绝。
- 三吃完整链路使用正式legal_actions→build_context→ActionPrefix→Executor.execute，模型动作测试输入分别选三组中的每一组，UI顺序与服务器不同；每次只点吃按钮和同一消费牌组合，输入租约释放。规则提供3种吃及pass，没有裁剪；赤五样本5种吃及pass全部进入模型上下文。
- 此处字形分类使用显式marker测试替身，验证的是图像分割、分组、动作绑定及执行事务，不冒充真实游戏三吃或真实模型推理。真实字形分类另由runtime/combo-completeness-validation-v1.json的既有双吃画面验证，两个组合正确、缺项/错赤牌拒绝。
- `python -m pytest tests/test_multi_choice_melds.py tests/test_locator.py -q`：11 passed；ruff通过。全量结果随最终提交前验证登记。
- 最终全量`python -m pytest -q`：246 passed，ruff通过。

## 限制与后续

实际游戏三吃暂未自然出现，不在生产构造操作窗口或强制模型选吃来凑验收。真实三吃/低频动作及皮肤、DPI完整矩阵由VAL-003持续跟踪；看不全的菜单保持拒绝，不能把测试通过解释为所有分辨率菜单都支持。

## Git关联与回退

实现哈希在后续commit-index和任务commits登记。回退可revert本提交，运行档案和失败报告不删除。
