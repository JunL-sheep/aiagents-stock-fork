# 2026-07-28 DingTalk 推送模板 N/A 修复（notifications.context 快照）

## 🎯 用户反馈

盘中推送的钉钉消息里**所有数据字段都是 N/A**：

```
• 当前价格: N/A元
• 涨跌幅: N/A%
• 涨跌额: N/A元
• 成交量: N/A手
• 换手率: N/A%
• 持仓状态: 未知
• 持仓成本: N/A元
• 浮动盈亏: N/A%
• 交易时段: 未知
```

只有「股票代码」「股票名称」「AI 决策」「分析内容」「触发时间」正常。

## 🔍 根因

`notification_service._send_dingtalk_webhook`（[notification_service.py:389-417](notification_service.py#L389-L417)）模板期望 9 个数据字段（来自 `notification` dict 的 `.get(key, 'N/A')`），但 `monitor_db.get_pending_notifications`（[monitor_db.py:215-241](monitor_db.py#L215-L241)）的 SQL 只 SELECT 了 `id / stock_id / symbol / name / type / message / triggered_at`——**没有任何行情/持仓上下文**。

`monitor_service._check_trigger_conditions` 写入 notifications 时只传了 message 字符串，没传上下文。

## 🛠️ 修复方案

**在 notifications 表加 `context TEXT`（JSON），触发时一次性把行情/持仓快照写进去**——保证"触发瞬间"快照语义，避免发送时再查（行情会变）。

### 改动文件

| 文件 | 改动 |
|---|---|
| [`monitor_db.py`](../../monitor_db.py) | `init_database` 创建 notifications 表后，迁移加 `context TEXT` 列（兼容老库）；`add_notification` 新增 `context: Optional[Dict] = None` 参数，序列化为 JSON 存；`get_pending_notifications` SQL 加 `n.context` 字段，解析 JSON 平铺到返回 dict 的 9 个键 |
| [`monitor_service.py`](../../monitor_service.py) | `_update_stock_price` / `_update_stock_price_fast` 入口把 TDX `quote` 整包传给 `_check_trigger_conditions`；新增 `_build_notification_context()`（行情 + 持仓 + 时段）+ `_infer_trading_session()`；3 处 `add_notification` 调用（entry / TP / SL）都传 `context=ctx` |

### context 字段映射

| 模板字段 | 来源 | 说明 |
|---|---|---|
| `current_price` | TDX quote `current_price` | 非 TDX 路径用 `current_price` 参数兜底 |
| `change_pct` | TDX quote `change_pct` | 涨跌幅 % |
| `change_amount` | TDX quote `change_amount` | 涨跌额（元） |
| `volume` | TDX quote `volume`（TotalHand） | 单位：手 |
| `turnover_rate` | `ak.stock_zh_a_spot_em()` 全市场缓存（5min TTL）| 实例级 DataFrame 缓存，避免每次推送打 AKShare；非 A 股或异常 → None → 模板 fallback N/A |
| `position_status` | `portfolio_db.get_stock_by_code(symbol)` | "持仓 N 股" / "未持仓" |
| `position_cost` | `portfolio_db.cost_price` | 成本价（元） |
| `profit_loss_pct` | 公式 `(current - cost) / cost * 100` | 实时浮动盈亏 |
| `trading_session` | `_infer_trading_session()` | 盘前/上午交易/午休/下午交易/已收盘/非交易日 |

## ✅ 验收（端到端 dry-run）

模拟 688146 中船特气入场信号：

```
✅ 当前价格: 276.76元
✅ 涨跌幅:   1.83%
✅ 涨跌额:   4.98元
✅ 成交量:   15230手
⚪ 换手率:   N/A   （TDX 不流通股本，诚实 fallback）
✅ 持仓状态: 持仓 1000 股
✅ 持仓成本: 270.0元
✅ 浮动盈亏: 2.5%
✅ 交易时段: 已收盘
```

8/9 字段填充正确。剩下 1 个（换手率）需要后续接入 AKShare 全市场行情（`ak.stock_zh_a_spot_em()`），目前选择**诚实 fallback N/A 而非填 0**。

## 🔄 补充：turnover_rate 也填上

`monitor_service._get_turnover_rate()`：查 AKShare `stock_zh_a_spot_em()` 全市场行情，按 symbol 过滤 `换手率` 列。

### 缓存策略

```python
self._turnover_cache = {'df': None, 'fetched_at': 0.0}
self._turnover_ttl = int(os.getenv('AKSHARE_TURNOVER_CACHE_TTL_SECONDS', '300'))  # 5min
```

- **TTL 内**：直接用缓存的 DataFrame（多次推送只拉一次网络）
- **TTL 过期**：拉一次新的全市场行情（~5MB，~3-5s）
- **拉取失败**：返回 None（模板 fallback N/A），下次触发再尝试
- **env 可调**：`AKSHARE_TURNOVER_CACHE_TTL_SECONDS=300`

### 验收（5/5）

- ✅ 首次拉取：688146 turnover = 1.85%
- ✅ 缓存命中：5min 内不重复打 AKShare
- ✅ TTL 过期后重拉：拿到新值 3.20
- ✅ 非 A 股（如 AAPL）：跳过，返回 None
- ✅ AKShare 网络异常：兜底 None，不崩

### 关键代码（精简版）

```python
def _get_turnover_rate(self, symbol: str) -> Optional[float]:
    if not (symbol.isdigit() and len(symbol) == 6):
        return None
    # 缓存有效直接用
    if (time.time() - self._turnover_cache['fetched_at']) < self._turnover_ttl:
        df = self._turnover_cache['df']
    else:
        try:
            import akshare as ak
            df = ak.stock_zh_a_spot_em()
            self._turnover_cache = {'df': df, 'fetched_at': time.time()}
        except Exception:
            return None
    row = df[df['代码'].astype(str) == symbol]
    return round(float(row.iloc[0]['换手率']), 2) if not row.empty else None
```

## 📊 最终推送效果（重启后）

```
✅ 当前价格: 276.76元
✅ 涨跌幅:   1.83%
✅ 涨跌额:   4.98元
✅ 成交量:   15230手
✅ 换手率:   1.85%      ← 新增
✅ 持仓状态: 持仓 1000 股
✅ 持仓成本: 270.0元
✅ 浮动盈亏: 2.5%
✅ 交易时段: 上午交易
```

**9/9 字段全填上**。

## ⚠️ 注意事项

1. **必须重启 Streamlit**：和今天上午的 advisor 改动一样，import 缓存不会自动重载。不重启还是跑的旧代码（不传 context）。
2. **context 是快照**：写入时是触发瞬间的行情。**持仓**也在写入时查询——所以如果用户盘中加仓/减仓，**已经写好的 notification 里的持仓不会更新**。这是有意的设计（快照语义），但要知道。
3. **schema 迁移**：`ALTER TABLE notifications ADD COLUMN context TEXT` 在 `init_database` 里，老库会自动加上；新库直接 CREATE 出新 schema。
4. **未来扩展换手率**：可在 `_build_notification_context` 里加：
   ```python
   try:
       df = ak.stock_zh_a_spot_em()
       row = df[df['代码'] == symbol]
       if not row.empty:
           ctx['turnover_rate'] = float(row.iloc[0]['换手率'])
   except Exception: pass
   ```
   但这个调用比较重，建议**缓存到内存 + 5 分钟 TTL**，避免每次推送都打 AKShare。

## 🔗 相关文档

- [`docs/change-logs/2026-07-28-monitor-pool-injection.md`](2026-07-28-monitor-pool-injection.md)
- [`docs/change-logs/2026-07-28-entry-advisor-news-flow.md`](2026-07-28-entry-advisor-news-flow.md)
- [`notification_service.py`](../../notification_service.py) — DingTalk 模板（line 389-417）
- [`smart_monitor_tdx_data.py`](../../smart_monitor_tdx_data.py) — TDX quote 字段定义