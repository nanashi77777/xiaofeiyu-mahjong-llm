# DEV-015 · 指定模型Responses渠道与用量

类别：实现。完成日期：2026-10-31（Asia/Shanghai）。

实时选择与最多一次期限内纠错、场后复盘共用本机麻将专用凭据，固定your-model-id、Responses、reasoning none、store false。独立请求带完整当前上下文。HTTP没有隐式重试；SSE只在收到completed终态和完整JSON后成功，拒绝片段、不完整、非法JSON。动态严格Schema约束q、合法a、p.v，optional用nullable required；本地仍校验全部引用与版本。

每次尝试独立记录真实input/output/total/cached/reasoning token、TTFT、完整时延、用途、状态及不泄密的凭据指纹。取消关闭请求并保存usage unknown，缺失不能当零；实时decision/repair与异步review分账，QQ其他功能凭据未改。复盘低优先级，实时窗口到来可取消；同一事实版本最多三次尝试，重启/抢占不重置计数。

验证：实际多批配对和真实牌局使用该模型、none、严格Schema和SSE，usage推理token为0；真实终场牌谱复盘成功提交candidate。构造渠道测试覆盖无终态/缺usage/取消/字段脱敏；复盘跨重启与第三次抢占封顶测试通过。动态Schema修复前的计划版本错误、HTTP400和超时全部保留。渠道取消后的实际计费无法据缺失usage推断，仍unknown。

实现：model.py、usage.py、wire.py、review.py及测试。VAL-004负责综合实际渠道专项，VAL-005负责500窗口与速度；本记录不宣称五秒目标已达到。Git由注册表/提交索引关联。回退源码并保留用量审计，不回退凭据分账或复用QQ额度。
