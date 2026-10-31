# DEV-008 · 小局、终场与规则身份

类别：实现。完成日期：2026-10-31（Asia/Shanghai）。

整场沿用真实game_uuid；小局身份结合该场和开局事实签名，显示的风/局/本场不作为主键。连庄/新小局分别保留，恢复用已有小局序号和事件身份。和牌保留全部hules，各种流局都是round_end；只有真实NotifyGameEndResult填final_scores/final_ranks并结束整场，NotifyGameTerminate保留interrupted。

规则按实际category/meta/mode识别友人、段位、赛事或unknown，真实房规及不支持项保存；当前顺位按点数/初始座位同分依据计算，最终顺位按服务端真实排序。新小局完整记录与整场完整度分开：中途从南场开始记录可以round_completeness=full而整场partial；新的完整小局不抹去过去缺口。

验证：构造多家和/各类中途流局、荒牌分数汇总、连庄与西入而非终场、真实终场排序、中途接入跨小局仍partial、旧缺口仍gaps。真实300+0半庄13小局结束，本家37800第一，四家分数与终场画面核对；该场含开发失败和补点，未用于完整无干预验收。四人东/南/单局/人机的实际mode已在大厅前轮验证。

实现：engine.py、state.py、domain.py、store.py、game_modes.py。专项VAL-001/006及真实恢复继续开放。Git由提交索引关联；回退恢复源码，既有终场与中断记录保留。
