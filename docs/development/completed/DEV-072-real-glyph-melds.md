# DEV-072 · 真实字形的多组菜单构造验证

完成日期2026-11-01。新增tools/validate_meld_glyphs.py，从ignored本机calibration读取真实48×78牌面字形，记录来源文件及SHA256，显式构造90×60牌面和菜单布局。全程使用正式ScreenLocator及TileClassifier ONNX，没有分类替身、候选提示或生产输入。

runtime/meld-glyph-validation-v2-20261101.json的4类菜单为三吃与含赤五五组变体、单行和向上双行，UI顺序与合法候选相反；正式分类均置信1.0，16次Browser.background_click在独立headless画布选择正确组。12项缺组、额外组、错赤候选核对全部拒绝，生产校准SHA不变、真实页面输入0、截图clip/scale及真实视口改动0。首次工具v1因隔离about:blank域与正式游戏域保护不匹配而停止，未产生生产点击；修正仅为工具实例的本机隔离URL，正式保护不变。

真实字形经过本地组合，并非实际游戏生成的三吃菜单；不能以此虚报真实三吃自然出现。实际三吃/低频真实布局仍在VAL-003，既有完整动作绑定/复合事务回归不变。源码工具可回退，字形/构造图与报告均不入Git。提交检查记录于本轮日志，实际哈希后补。
