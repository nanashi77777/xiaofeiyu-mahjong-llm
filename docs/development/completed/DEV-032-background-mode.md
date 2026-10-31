# DEV-032 · 后台浏览器输入模式

完成日期：2026-11-01（Asia/Shanghai）；实际实现提交：9054eaac25afb603ea1eefbde219d4472b53d875。

实现独立physical_foreground/browser_background选择。后台仅对绑定页面发浏览器鼠标移动、按下和释放事件，截图核对牌面、全部候选、几何、状态版本、期限和急停，不调用游戏动作方法或发送操作协议。原生视口screencast保渲染，无clip/scale设备度量；系统鼠标和前台保持不变。

实际证据runtime/minimized-probe.jsonl：300＋0联调中最小化弃1p获得ActionDiscardTile确认，minimized=true、cursor_unchanged=true、foreground_unchanged=true，input_ms=1047。前一非最小化弃3z也确认。此处仅证明功能，不计正式速度。独立最小化浏览器12次稳定取图、180帧渲染推进及鼠标事件见stable-capture-minimized.json；15Hz输入隔离20/20与真实后续按钮见DEV-059。

本轮251项pytest核对渲染期间过期/停止不按下、按压后场景切换仍释放、未发送与已发送状态区别及租约串行。当前生产5＋10使用后台模式，其失败如实保留；不能以若干成功动作称500窗口/三整场通过。所有皮肤和低频操作继续VAL-003。实现来源e53fe8a、后续DEV-046/057/059；提交见索引，回退输入模块或停止后切回physical_foreground，不删除事实。
