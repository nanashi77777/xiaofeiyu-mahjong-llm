# DEV-056 · 结算按钮就绪与实际提交点

类别：实现。完成日期：2026-10-31（Asia/Shanghai）。

结算标题出现时，按钮可能仍处于加载或倒计时阶段。RoundFlow在原45秒截止内继续观察，WaitingUser且未提交时不退出；只有LobbyUI完成可见按钮预检并调用before_submit，才记录submitted。已经提交后的释放帧超时保留等待场景切换状态，不重复按压。停止、换局或对局身份改变会撤销流程并释放输入租约。

主要实现：src/majsoul_llm/round_flow.py，实际实现提交b82ee23f19a8c206bbe4fb9c2ea77b06a5acde57。tests/test_result_readiness.py覆盖标题先于按钮、未预检不得记录提交及租约释放；背景输入回归覆盖已按下后释放帧超时只释放一次。全量149项pytest通过，ruff通过。

真实证据：混合续录场recorded-match-004第9小局记录round_win、round_score和final三个实际提交。runtime/results/656108ba1ed05c4e69a7bfc6.png、7903d90c81880501eede41c9.png、c5ca851bcf5eea040f4a8cd8.png保留可见页面，终场图与权威最终点数[25600,28300,13000,33100]、顺位[3,2,4,1]一致，本家seat2为13000点第四。随后真实房91679为playing=false，result_flow.active=false且last_scene=room，截图确认已返回原房。

限制：该场此前正式测试失败并长期暂停、自动摸切，完整度partial；本记录仅确认结算功能，不把184总机会中的33普通确认冒充完整AI对局。正式失败报告runtime/production-5plus10-short-v1.json保持原样，续录汇总另存runtime/mixed-short-v1-final.json。三场及性能仍由VAL-003/005/006跟踪。

回退：回退上述实现提交相关RoundFlow改动，保留所有牌谱和失败证据。
