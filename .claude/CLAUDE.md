# CLAUDE.md — 智瞰龙虎（aiagents-stock）

> 智瞰龙虎：A股自动化监测 + AI 策略决策 + 钉钉推送系统
> 数据源：TDX 本地行情 / AKShare / Tushare / 问财
> 推 送：钉钉 Webhook

---

## 项目架构

```
盘前 09:00 → 每日操盘建议.py → 龙虎榜报告 → 钉钉推送 + 监测池注入（10 只）
盘    中 → monitor_service.py → TDX 1Hz 轮询 → advisor 决策 → 钉钉推送 + journal 归档
盘后 15:30 → 盘后验证系统
```

## 核心文件速览

### 监测模块
| 文件 | 作用 |
|------|------|
| `monitor_service.py` | 核心：股票价格监测 + advisor 决策 + 自动推送 |
| `monitor_db.py` | 数据库层：监测池 / 推送队列 / 策略日志（`recommendation_journal`） |
| `monitor_manager.py` | Streamlit UI：监测管理页面 |
| `monitor_scheduler.py` | 定时调度：交易日自动启停 |
| `notification_service.py` | 通知：钉钉 Webhook 推送模板 |
| `每日操盘建议.py` | 盘前报告生成 + 推送 + 注入监测池 |

### 数据模块
| 文件 | 作用 |
|------|------|
| `smart_monitor_tdx_data.py` | TDX 行情接口：实时价 / K 线 / 技术指标 |
| `fund_flow_akshare.py` | AKShare 资金流向 |
| `risk_data_fetcher.py` | 问财风险数据（解禁/减持/事件） |
| `data_source_manager.py` | 多数据源自动降级调度 |
| `stock_data.py` | 通用股票数据获取 |

### 策略 / Agent 模块
| 文件 | 作用 |
|------|------|
| `longhubang_engine.py` | 龙虎榜分析主引擎 |
| `longhubang_agents.py` | AI 分析师团队（个股潜力/游资/风险/首席） |
| `ai_agents.py` | 通用 AI 分析调用 |
| `news_flow_engine.py` | 新闻流量分析引擎 |
| `sector_strategy_engine.py` | 板块策略引擎 |
| `low_price_bull_strategy.py` | 低价擒牛策略 |

## advisor 体系（入场决策链路）

```
价格命中入场区间
  ↓
_evaluate_entry_decision(7 位 advisor 投票)
  ↓
#1 trading_session — 非交易时段 → suppress
#2 news_flow      — 板块 danger  → suppress
#3 trend           — 趋势向下     → suppress
#4 volume          — 缩量上攻     → warn
#5 position        — 浮盈>+5%    → suppress
#6 triple_screen   — 评分<40     → suppress
#7 market_regime   — 大盘熊市     → suppress
  ↓
未被抑制 → 推钉钉（9 字段全填上）
止盈/止损 → 永远推（风控纪律，不走 advisor）
```

### env flag 速查
```bash
# Phase 1（默认 true）
ENABLE_TRADING_SESSION_ENTRY_FILTER=true
ENABLE_TREND_ENTRY_FILTER=true
ENABLE_VOLUME_ENTRY_FILTER=true
ENABLE_POSITION_ENTRY_FILTER=true

# Phase 2（默认 true）
ENABLE_TRIPLE_SCREEN_ENTRY_FILTER=true
ENABLE_MARKET_REGIME_ENTRY_FILTER=true

# 已有
ENABLE_NEWS_FLOW_ENTRY_FILTER=true
TECH_INDICATORS_CACHE_TTL_SECONDS=60
TRIPLE_SCREEN_CACHE_TTL_SECONDS=60
MARKET_REGIME_CACHE_TTL_SECONDS=1800
```

## 启动方式

```bash
# 开发模式
cd d:/projects/projects/aiagents-stock-main/aiagents-stock-main
python run.py

# 盘前推送
python 每日操盘建议.py

# 运行测试
python -m tests.test_advisors
```

## 关键数据库

`stock_monitor.db` 包含：
- `monitored_stocks` — 监测池（10-20 只）
- `notifications` — 推送队列
- `recommendation_journal` — **操盘策略日志（永久归档，用于复盘优化）**
- `price_history` — 价格历史

## release 和版本

| 日期 | 版本/标签 | 说明 |
|------|-----------|------|
| 2026-07-28 | （current）| 6 策略 advisor 集成 + 监测池注入 + TDX 秒级 + DingTalk 修复 |
| 2026-07-27 | （base） | 龙虎榜全链路打通 + news_flow + 盘后验证 |

## 发布流程

```bash
git tag -a v2026-07-28 -m "多策略 advisor + 监测池注入 + DingTalk 上下文修复"
git push origin --tags

# 或切 release 分支
git checkout -b release/2026-07-28
git push origin release/2026-07-28
```
