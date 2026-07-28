# 2026-07-28 监测池注入 + TDX 秒级快路径改造

## 🎯 用户决策（4 条）

| # | 决策点 | 选择 |
|---|---|---|
| 1 | 盘前报告 10 只股票与监测池已有股票冲突时 | **覆盖旧的**（不另开新行） |
| 2 | 监测股票保留观察期 | **7 天**（TTL 到期自动 `purge_expired`） |
| 3 | 监测池容量上限 + 盘中轮询频率 | **20 只上限 + TDX 秒级（每秒拉价）** |
| 4 | MiniQMT 自动交易 | **暂不启用**（保持 `quant_enabled=False`） |

## 📦 改动文件

| 文件 | 关键变化 |
|---|---|
| [`monitor_db.py`](../../monitor_db.py) | 加 `expires_at` 列（迁移兼容老库，老记录回填为 `created_at + 7d`）；新增 `purge_expired()` / `upsert_with_ttl()` / `update_current_price_only()` / `get_a_share_stocks_for_fast_poll(cap=20)` / `get_pool_stats()` |
| [`monitor_service.py`](../../monitor_service.py) | 主循环 sleep：TDX 模式 **1s**，非 TDX 模式 **60s**；news_flow 检查改成"按秒计数"；新增 `_fast_poll_tdx()` + `_update_stock_price_fast()`，每秒更价、price_history 60s 节流 |
| [`每日操盘建议.py`](../../每日操盘建议.py) | 钉钉推送成功 → 自动调 `inject_to_monitor_pool()` 灌入 10 只；新增 `--dry-run` / `--skip-monitor` 参数；新增 `_limit_pct()` / `_confidence_to_rating()` / `inject_to_monitor_pool()` |
| [`monitor_manager.py`](../../monitor_manager.py) | 监测卡片"监测状态"从 3 列改 4 列，新增"剩余观察天数"chip（🟢>3天 / 🟡1-3天 / 🟠今日 / 🔴过期） |

## ✅ 验收点（全部通过）

- ✅ TTL 写入正确（新增/覆盖都刷成 NOW + 7d，老记录回填）
- ✅ 过期清理：`purge_expired()` 在每次注入前自动跑
- ✅ 20 只上限：超限按 `created_at` 升序淘汰最旧的（今日新加的优先保留）
- ✅ 覆盖式 upsert：已有股票 entry_range / TP / SL / rating 全部被新报告覆盖
- ✅ 缺价跳过：盘前 TDX 没拉到价的股票（停牌/未开盘）静默跳过，不计入 failed
- ✅ 置信度映射：高→买入 / 中→持有 / 低→卖出
- ✅ TDX 秒级：每秒拉价 + 触发条件检查，`price_history` 60s 节流（避免 1Hz 把表写爆）
- ✅ 端到端：首次注入 → 二次注入覆盖 → TTL 刷新全部联动

## ⚠️ 踩坑 / 注意事项

1. **`get_monitor_by_code` 之前缺 `expires_at` 字段**——本次顺手补了；同时修正了 `quant_config` 列索引（原来错指到 `quant_enabled` 列）。`upsert_with_ttl` 只用 `existing['id']`，所以旧 bug 没爆出来过，但 API 完整性有缺口。

2. **`inject_to_monitor_pool` 用的是全局 `monitor_db`（指向 `stock_monitor.db`）**——不要在测试里只 `StockMonitorDatabase(db_path=...)` 创建实例而不替换 `monitor_db.monitor_db` 引用，否则 `from monitor_db import monitor_db` 还是会拿到全局那个，落库落到生产表。测试时要 `import monitor_db as md; md.monitor_db = test_db` 这样替换。

3. **TDX 没启时**（`TDX_ENABLED=false`），监测仍走 60s 慢路径，跟旧的 `monitor-realtime-tuning` 约束兼容。**3 只股是 60s 预算的硬上限——这点没变**，但快路径覆盖了"股多"的场景。

4. **20 只上限的淘汰逻辑只在 `upsert_with_ttl` 里跑**，所以**手动加的股票不会被自动踢**——除非新一轮日报推入时超限。如果用户手动加了 25 只股票，那池子就是 25。

5. **`expires_at` 迁移是惰性的**：本次只在老库执行了 `ALTER TABLE + UPDATE`，新库初始化时直接建好。**新增字段时一定记得加 try/except 兼容老库**（见 `init_database` 里的 `trading_hours_only` / `expires_at` 两段，模式固定）。

## 🔗 相关文档

- [`docs/UPDATE_LOG.md`](../UPDATE_LOG.md) — 项目主更新日志（连续追加式）
- [`docs/AI盯盘和实时监测交易时段优化总结.md`](../AI盯盘和实时监测交易时段优化总结.md) — 监测相关历史优化
- [`docs/智能盯盘配置说明.md`](../智能盯盘配置说明.md) — 智能盯盘 UI 配置
- 代码：[`monitor_db.py`](../../monitor_db.py) / [`monitor_service.py`](../../monitor_service.py) / [`monitor_manager.py`](../../monitor_manager.py) / [`每日操盘建议.py`](../../每日操盘建议.py)
- 旧 memory（不在项目内，在 AI 记忆系统）：`monitor-realtime-tuning` / `tdx-deployment-recovery` / `daily-trading-workflow`