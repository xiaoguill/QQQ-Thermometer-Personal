# 前端参考与独立设计边界

## 本次参考的公开项目

本 demo 只参考公开项目常见的产品模式，不复制代码、页面截图、品牌、文案、图标、图片、独特组件样式或其专有数据。

| 项目 | 用途 | 借鉴的通用模式 | 本项目的处理 |
|---|---|---|---|
| [OpenBB](https://github.com/OpenBB-finance/OpenBB) | 研究工作台 | 多模块导航、数据源意识、分析工作区 | 只保留“研究台”信息架构，使用自有 Northstar 视觉和中文文案 |
| [Grafana](https://github.com/grafana/grafana) | 监控仪表盘 | 面板、时间范围、数据健康状态 | 只借鉴面板分组和状态标识；不直接嵌入 Grafana |
| [Lightweight Charts](https://github.com/tradingview/lightweight-charts) | 金融图表 | 轻量时间序列、图例开关、tooltip 目标 | demo 用原生 SVG；未来如引入该库，必须保留其 Apache-2.0 与 attribution 要求 |
| [Streamlit](https://github.com/streamlit/streamlit) | 数据应用原型 | 快速把数据、控件和解释放在一起 | 只借鉴验证节奏；当前 demo 不依赖 Streamlit |

## 为什么不直接使用 AGPL 项目代码

OpenBB 和 Grafana 的主仓库页面标示为 AGPLv3。对个人本地 demo 可以研究其交互思想，但直接复制或改造其代码、组件或前端资产会带来许可证义务和来源追踪问题。因此本阶段选择自己写静态界面，后续如果使用第三方依赖，单独记录版本、许可证、NOTICE/attribution 和是否会触发衍生作品义务。

## 当前 demo 的独立元素

- 产品名：Northstar Personal Research。
- 视觉方向：深海蓝研究台 + 青绿/琥珀风险语义。
- 核心组件：风险温度环、决策摘要、证据一致性列表、目标仓位环图、审计条带。
- 文案：围绕“状态、条件、历史证据、数据质量”，不使用“预测暴跌、收益概率或保证回撤”等表述。
- 数据：全部是明确标注的模拟快照，不代表当前真实行情。

## 后续引入依赖时的最低检查

1. 先读取依赖的完整许可证与 NOTICE。
2. 记录 package 名、版本、来源、许可证和 attribution 方式。
3. 确认没有把第三方品牌或独特视觉资产作为本项目自己的品牌。
4. 为依赖增加锁版本和最小可运行页面测试。
5. 在 UI 的“关于/法律与数据”入口展示必要的来源和免责声明。
