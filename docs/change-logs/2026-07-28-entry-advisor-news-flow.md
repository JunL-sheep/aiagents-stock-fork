# 2026-07-28 入场推送 news_flow 板块过滤（A 方案 + 扩展点）

## 🎯 用户决策

实现 **A 方案**：news_flow 板块级预警过滤入场推送，**保留未来加 B/C 方案的扩展点**。

| # | 决策点 | 选择 |
|---|---|---|
| 1 | 抑制 vs 改写 vs 放行 | **danger → 抑制**；**warning → 改写附加提示**；**info → 放行** |
| 2 | 抑制后是否还推 DingTalk | **不推**（但写 notifications 表，`sent=False`，消息前缀 🚫 便于 UI 追溯） |
| 3 | TP/SL 是否走 advisor | **不走**（风控纪律优先，永远推送） |
| 4 | 行业查不到（akshare 失败）时 | **默认放行**（宁可漏不可错） |
| 5 | 扩展点（未来 B/C）位置 | **决策方法 `_evaluate_entry_decision` 末尾 `advisors` 列表 append**；主流程不动 |

## 📦 改动文件

| 文件 | 关键变化 |
|---|---|
| [`monitor_service.py`](../../monitor_service.py) | 加 `_evaluate_entry_decision()` 决策点 + `_advisor_news_flow()` advisor + `_get_stock_industry()` (akshare + 缓存) + `_get_active_news_flow_alerts()` (60s 内存缓存)；`_check_trigger_conditions` 的 entry 分支走决策点；TP/SL 不变 |
| `.env` 模板（可选） | 新增 `ENABLE_NEWS_FLOW_ENTRY_FILTER` (默认 true)、`NEWS_FLOW_SUPPRESS_WINDOW_HOURS` (默认 4)、`NEWS_FLOW_CACHE_TTL_SECONDS` (默认 60) |

> 注：UI 不需要改——`monitor_manager.py` 已有的"⏳ 待发送" + 消息前缀 🚫 已能展示"已抑制"事件。

## ✅ 验收点（10/10 测试通过）

| # | 场景 | 期望 | 结果 |
|---|---|---|---|
| 1 | 无 news_flow 预警 | pass（推 DingTalk） | ✅ |
| 2 | 板块命中 danger（`半导体` in `affected_sectors`） | suppress（不推） | ✅ |
| 3 | 板块命中 warning | warn（推 + 附加提示） | ✅ |
| 4 | 仅 info 级别预警 | pass | ✅ |
| 5a/b | TP / SL 信号 | 不走 advisor → 永远 pass | ✅ |
| 6 | env flag = `false` | 全部 pass（回退生效） | ✅ |
| 7 | symbol 非 6 位（港股/美股）→ 行业未知 | pass | ✅ |
| 8 | 板块名完全不匹配 | pass | ✅ |
| 9 | symbol 命中 alert `related_topics` | suppress（精准定位个股） | ✅ |
| 10 | 行业是 sector 名的子串（如 `半导体` ⊂ `半导体材料`） | warn（子串模糊匹配） | ✅ |

## 🏗️ 架构：决策点 + Advisor 注册表

```
_entry 信号触发
       │
       ▼
_check_trigger_conditions (TP/SL 不走 advisor)
       │
       ▼
_evaluate_entry_decision(stock, 'entry', base_msg)
       │
       ├── _advisor_news_flow(stock)         ← 当前 A 方案
       │     │
       │     ├── _get_stock_industry(symbol) ← akshare + 缓存
       │     └── _get_active_news_flow_alerts() ← DB + 60s 缓存
       │
       ├── [未来] _advisor_agents_realtime(stock, price)  ← B 方案扩展点
       └── [未来] _advisor_sector_pre_filtered(stock)    ← C 方案扩展点
       │
       ▼
verdict 聚合 → final_message + should_push
       │
       ▼
monitor_db.add_notification(总是写) + notification_service.send_notifications(仅当 should_push)
```

## ⚠️ 踩坑 / 注意事项

1. **行业映射的精度**：`ak.stock_individual_info_em` 返回 SW（申万）行业分类，与 news_flow 的 `affected_sectors` 概念分类（如"AI"、"半导体"）不完全一致。**当前用子串模糊匹配**，可能误匹配（如 `半导体` ⊂ `半导体材料`）——但子串命中是 warning 不是 danger，影响有限。未来 C 方案可换成"预计算板块标签"对齐。
2. **news_flow_alerts 的"时效性"**：用 `get_alerts(since=N hours)` 拉所有近期预警（含已通知），与 `_check_news_flow_alerts`（推送后即标记 notified）共用同一张表的不同读取模式，互不干扰。
3. **缓存失效策略**：`_sector_cache` 永不失效（板块名稳定）；`_news_flow_cache` 60s TTL（防 1Hz × 20 只股票打爆 DB）。如果出现"明明有预警但没抑制"，先看是不是缓存里的旧数据——重启服务或等 60s。
4. **抑制时不调量化交易**：`decision['should_push']` 为 False 时跳过 `_execute_quant_trade`，避免 advisor 决策和真钱下单打架（虽然 MiniQMT 暂未启用，但代码要稳）。
5. **TP/SL 风控纪律优先**：哪怕板块 danger 拉满，止损信号照样推——不能让 advisor 阻止用户止损。决策方法里第一个 `if signal_type != 'entry':` 就是这个护栏。
6. **回退方式**：`.env` 设 `ENABLE_NEWS_FLOW_ENTRY_FILTER=false` 即可回到无 advisor 时代，不需要改代码。

## 🔮 未来扩展点

### B 方案：入场前调 agents 实时评估

在 `_evaluate_entry_decision` 的 `advisors` 列表 append：
```python
advisors.append(self._advisor_agents_realtime(stock, current_price))
```
实现 `_advisor_agents_realtime`：
- 调"个股潜力分析师"看当前 K 线 + 新闻 + 资金流向
- 返回 `{'decision': 'pass'|'warn'|'suppress', 'reason': 'agents 建议观望，因为...'}`
- 注意：1Hz 调 LLM 成本爆炸，**必须**在 advisor 内部加节流（如每只股每 5 分钟最多调一次，结果缓存）

### C 方案：板块预筛（选股阶段过滤）

在每日报告生成时（`每日操盘建议.py`），agents 分析时把 news_flow 板块风险作为输入。`inject_to_monitor_pool` 跳过 high-risk 板块的股票。
- 优点：监测池本身就更干净，盘中决策更简单
- 缺点：选股阶段就更复杂，agents prompt 要加 news_flow 注入

## 🔗 相关文档

- [`docs/change-logs/2026-07-28-monitor-pool-injection.md`](2026-07-28-monitor-pool-injection.md) — 同日的"监测池注入 + TDX 秒级"改动（前置）
- [`docs/change-logs/2026-07-28-full-pipeline-integration.md`](2026-07-28-full-pipeline-integration.md) — 全链路集成概览
- [`news_flow_db.py`](../../news_flow_db.py) — `get_alerts(since)` / `get_unnotified_alerts()` / `affected_sectors` 字段定义
- [`stock_data.py`](../../stock_data.py) — A 股行业字段查询接口（参考）