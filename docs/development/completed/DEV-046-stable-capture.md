# DEV-046 · 稳定画布取图

完成日期：2026-10-31（Asia/Shanghai）。用户反馈自动操作时画面反复伸缩，按[实施前方案](../../design/stable-capture.md)暂停输入、移除取图中的渲染缩放路径。

旧canvas元素截图采用CSS比例clip，后台保帧还请求低分辨率screencast。监测没有捕获到JS窗口/画布几何反复变化，不能断言原生窗口一直最大化/缩小。修复改为Page.captureScreenshot完整原视口，不传clip/scale、不滚动、不设置设备度量；PNG返回后在Python本地裁剪与归一化，capture_lock串行取图，前后布局变化时拒绝定位。后台保帧不再指定maxWidth/maxHeight，帧立即丢弃。

验证：66项pytest及ruff通过，新增DPI裁剪像素、串行截图参数和几何变化拒绝测试。独立真实Chromium在DPR1.25/1.5下偏移画布各12次取图，尺寸/像素正确、布局不变；真实雀魂页面连续12次取图布局不变。另一个独立窗口最小化后12次原生取图，布局/像素不变、渲染推进180帧且浏览器鼠标点击被接收，窗口保持最小化；54321已准备窗口未受该测试影响。

用户在新链路完成入房准备后明确反馈“现在稳定了”，作为本次可见伸缩修复的真实验收。证据只存runtime/stable-capture-isolation.json、stable-capture-live.json、stable-capture-minimized.json；截图和浏览器资料不提交。

隔离最小化鼠标事件不能代替全部真实麻将动作；更多皮肤/显示器、鸣牌组合及连续实战继续由VAL-003等验收。实际提交见[提交索引](../commit-index.md)。
