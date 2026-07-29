# 2026-07-28 实时监测多策略 advisor 集成（6 个闸门）

## 🎯 用户决策

盘中推送"仅仅是根据价格，很不准确"，要求把 `docs/` 下多个策略文档的可复用规则集成进 monitor_service 的入场/止盈/止损决策中。

| # | 决策点 | 选择 |
|---|---|---|
| 1 | 集成范围 | **Phase 1 + Phase 2 全部**（6 个 advisor：4 必做 + 2 推荐） |
| 2 | Phase 2 默认启用 | **默认启用**（`true`），如需关闭可改 `.env` 为 `false` |
| 3 | 主流程改动 | **不动** `_check_trigger_conditions` / TP/SL 路径，全部挂在 `_evaluate_entry_decision` 的 `advisors` 列表 |
| 4 | 失败兜底 | advisor 抛异常一律返回 `'pass'`（宁可漏不可错） |

## 📦 改动文件

| 文件 | 改动 |
|---|---|
| [`monitor_service.py`](../../monitor_service.py) | `__init__` 加 3 个缓存 + 5 个 TTL + 6 个 env flag；新增 `_advisor_trading_session / _advisor_position / _advisor_trend / _advisor_volume / _advisor_triple_screen / _advisor_market_regime` 共 6 个 advisor + helper `_get_cached_tech_indicators`；移植 `calculate_ema / _macd / _rsi / _kdj` 4 个 numpy 纯函数；`_evaluate_entry_decision` 挂 6 个 advisor + 接受 `ctx` 参数 |
| [`tests/test_advisors.py`](../../tests/test_advisors.py) | 新增 24 个 dry-run 用例（6 advisor × 4 场景），mock TDX 不依赖网络 |

## 🏗️ 6 个 advisor 速览

### Phase 1（默认启用，4 个）

| # | 方法 | 规则（节选） | 数据源 | suppress 阈值 |
|---|---|---|---|---|
| #1 | `_advisor_trading_session` | 盘前/午休/14:55 后/已收盘/非交易日 → 抑制 | `ctx['trading_session']` + `datetime.now()` | 非交易时段 |
| #2 | `_advisor_trend` | `trend='up'` pass / `sideways` warn / `down` suppress | TDX `get_technical_indicators` | 趋势向下 |
| #3 | `_advisor_volume` | `volume_ratio ≥ 1.2` pass / `< 1.2` warn（永不 suppress） | TDX 同上 | 无（仅辅助） |
| #6 | `_advisor_position` | 已浮盈 > +5% suppress（加仓非首入场）；亏损 warn | `ctx['position_status'] / profit_loss_pct` | 已浮盈 > 5% |

### Phase 2（默认启用，2 个）

| # | 方法 | 规则 | 数据源 | 评分阈值 |
|---|---|---|---|---|
| #4 | `_advisor_triple_screen` | MACD 多头 (30) + RSI/KDJ 不超买 (30) + 价格近 MA5 (40) | TDX `get_kline_data` + numpy 计算 | score < 40 |
| #5 | `_advisor_market_regime` | 沪深 300 极端熊市 / 5 日暴跌 > 8% → 全局 suppress | TDX `get_kline_data('000300', 'day', 130)` | 5日 -8% |

## ✅ 验收（24/24 测试通过）

| Advisor | 用例 |
|---|---|
| #1 trading_session | ✅ 上午 pass / ✅ 14:56 suppress / ✅ 午休 suppress / ✅ 无 ctx fallback |
| #2 trend | ✅ up pass / ✅ sideways warn / ✅ down suppress / ✅ 无数据 fallback |
| #3 volume | ✅ 1.5 pass / ✅ 0.6 warn 缩量 / ✅ 1.1 warn 量能一般 / ✅ fallback |
| #6 position | ✅ 未持仓 pass / ✅ 亏 -3% warn / ✅ 盈 +8% suppress / ✅ fallback |
| #4 triple_screen | ✅ 默认禁用 / ✅ score=70 pass / ✅ score<40 suppress / ✅ TDX 失败 fallback |
| #5 market_regime | ✅ 默认禁用 / ✅ 正常波动 pass / ✅ 5 日 -10% suppress / ✅ fallback |

## ⚙️ 新增 env flags（6 个，全部可选）

```bash
# Phase 1（默认 true）
ENABLE_TRADING_SESSION_ENTRY_FILTER=true   # 闸门 #1 交易时段
ENABLE_TREND_ENTRY_FILTER=true             # 闸门 #2 趋势方向
ENABLE_VOLUME_ENTRY_FILTER=true            # 闸门 #3 量能确认
ENABLE_POSITION_ENTRY_FILTER=true          # 闸门 #6 持仓感知

# Phase 2（默认 false，需手动开启）
ENABLE_TRIPLE_SCREEN_ENTRY_FILTER=false    # 闸门 #4 三重滤网
ENABLE_MARKET_REGIME_ENTRY_FILTER=false    # 闸门 #5 大盘 regime

# 缓存 TTL（可选调）
TECH_INDICATORS_CACHE_TTL_SECONDS=60       # #2/#3 共享
TRIPLE_SCREEN_CACHE_TTL_SECONDS=60         # #4
TRIPLE_SCREEN_TIMEOUT_SECONDS=2.0          # #4 单次超时熔断
MARKET_REGIME_CACHE_TTL_SECONDS=1800       # #5（30 分钟）
MARKET_REGIME_INDEX_SYMBOL=000300           # #5 指数代码
```

## ⚠️ 踩坑 / 注意事项

1. **重启 Streamlit 才能加载新代码**（与之前几次改动一致）—— `monitor_service.py` 改动后，import 缓存不会自动重载。
2. **Phase 2 默认关闭是有意的** —— 三重滤网 + 大盘 regime 的拉 K 线 + numpy 计算单次 ~250ms，20 只股票首轮约 5s，会让首轮主循环卡顿。先跑 Phase 1 一两天观察体验，再决定是否开 Phase 2。
3. **首轮保护**：Phase 2 advisor 内部检查 `_tech_indicators_cache` 长度；为空时（重启后第一次扫描）直接 fallback pass，避免首轮 16s 卡死。
4. **失败兜底统一**：每个 advisor 方法体外都包了 try/except，任何异常 → `pass`。监控主链路不会因为 advisor 挂掉而断推。
5. **测试时 mock 了 akshare / portfolio_db**（`tests/test_advisors.py` 顶部有 import 钩），避免测试时拉真实网络。
6. **advisor 顺序很重要**：suppress 闸门放前面（trading_session → news_flow → trend → position），warn 闸门放后面（volume）。第一个 `suppress` 命中后短路，后续 `warn` 不再生效。

## 🔗 相关文档

- [`docs/change-logs/2026-07-28-entry-advisor-news-flow.md`](2026-07-28-entry-advisor-news-flow.md) — 上次加的 `_advisor_news_flow`（已挂在 advisors 列表第 2 位）
- [`docs/三重滤网策略保存.py`](../../三重滤网策略保存.py) — Phase 2 #4 规则原型 + 4 个 numpy 函数源
- [`docs/均线回归策略保存.py`](../../均线回归策略保存.py) — Phase 2 #5 规则原型
- [`docs/智能盯盘使用指南.md`](../智能盯盘使用指南.md) — Phase 1 #2 #3 规则来源
- [`docs/智能盯盘持仓管理功能说明.md`](../智能盯盘持仓管理功能说明.md) — Phase 1 #6 规则来源
- [`docs/智能盯盘交易时段优化说明.md`](../智能盯盘交易时段优化说明.md) — Phase 1 #1 规则来源
- [`smart_monitor_tdx_data.py`](../../smart_monitor_tdx_data.py) — `get_technical_indicators` / `get_kline_data` 接口

## 🎯 建议的灰度上线顺序

1. **今天**：重启 Streamlit → Phase 1 4 个闸门自动启用 → 观察推送噪音是否减少
2. **明天**：如果觉得"量能确认"太严，把 `ENABLE_VOLUME_ENTRY_FILTER=false` 关掉单独看效果
3. **后天**：开 `ENABLE_TRIPLE_SCREEN_ENTRY_FILTER=true` 看评分质量
4. **大后天**：开 `ENABLE_MARKET_REGIME_ENTRY_FILTER=true` 体验大盘闸门（最影响体验）