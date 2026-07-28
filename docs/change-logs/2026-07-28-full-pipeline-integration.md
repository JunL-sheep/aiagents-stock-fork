# 2026-07-28 龙虎榜全链路集成 + TDX 本地行情 + 4 层加权 AI

> 一次性的大改造：把"智瞰龙虎报告 → 监测池 → 钉钉推送 + 新闻流量 AI + 盘后验证"全链路打通，并解决了底层数据源问题。

---

## 1. 背景与用户决策

按时间顺序，用户**逐步**确定的设计选择：

1. **数据源问题**：智瞰龙虎对 7-27 拿不到数据 → 切到 AKShare（更稳）
2. **实时监测**：60s 周期、1min/股，配合 TDX 避免 API 限流
3. **TDX 本地化**：避开公网限流，单股从 13s 降到 0.5s
4. **钉钉推送**：关键词 `俊一隅`（用户自定）
5. **10 只推荐股入池**：基于当日报告 + 算阈值（进场 ±3% / 止盈 +8% / 止损按涨停价×0.95）
6. **每日 09:00 简报**：自动推"今日操盘计划"
7. **盘中推送**：60s 周期拉价，命中阈值推钉钉
8. **新闻流量分析**：6 种预警、3 个集成点、4 层加权数据框架
9. **盘后验证**：15:30 拿真实 OHLC 验证推荐准确度

每个决策都有 E2E 测试验证（详见第 3 节）。

---

## 2. 改动文件清单

### 2.1 数据源与抓取层
| 文件 | 改动 |
|---|---|
| [longhubang_data.py](../longhubang_data.py) | 主力改 AKShare（`_akshare_helper` 已有），HTTP 留作回退；scoring 字段名 `gpdm` 必须有值 |
| [news_flow_data.py](../news_flow_data.py) | 22 平台 → 18 平台（删 8 个 API 不支持的 + 改 tskr→36kr + 加 douban/hupu/v2ex/hackernews）；100% 成功率 |
| [smart_monitor_tdx_data.py](../smart_monitor_tdx_data.py) | 已存在，未改 |

### 2.2 实时监测层
| 文件 | 改动 |
|---|---|
| [monitor_service.py](../monitor_service.py) | 主循环 300s → 60s；加 `_check_news_flow_alerts()` 每 5 分钟轮询；监测池 1min/股默认 |
| [monitor_db.py](../monitor_db.py) | 已有 `trading_hours_only` 字段；`check_interval` 默认 30→1 分钟 |
| [monitor_manager.py](../monitor_manager.py) | UI 滑块范围 5-120 → 1-60，默认 1 |

### 2.3 通知推送层
| 文件 | 改动 |
|---|---|
| [notification_service.py](../notification_service.py) | **新增 `send_analysis_result(subject, content)` 方法**（修 news_flow 缺接口的 bug） |
| [monitor_service.py](../monitor_service.py) | `_check_trigger_conditions` 调 notification_service 推钉钉 |

### 2.4 新闻流量 + 4 层加权（**核心新增**）
| 文件 | 改动 |
|---|---|
| [stock_keyword_map.py](../stock_keyword_map.py) | **新文件** - 22 板块 150 只龙头股映射 + 关键词归一化 |
| [guba_fetcher.py](../guba_fetcher.py) | **新文件** - AKShare `stock_comment_em` 5000+ 股关注度 + 排名上升 + 概念热度 |
| [weighted_context.py](../weighted_context.py) | **新文件** - 4 层加权数据上下文生成器（T1-T4） |
| [news_flow_agents.py](../news_flow_agents.py) | 4 个 agent 全部加 `weighted_context` 参数注入 prompt；`run_full_analysis` 加 `target_codes` |
| [news_flow_engine.py](../news_flow_engine.py) | `run_full_analysis` 加 `target_codes` 透传 |
| [news_flow_sync.py](../news_flow_sync.py) | 3 步：guba_fetch_all → run_full_analysis(target_codes) → run_alert_check |
| [news_flow_agents.py](../news_flow_agents.py) | 各 agent prompt 注入"4 层加权数据上下文"段 |

### 2.5 定时任务 + 自动化
| 文件 | 改动 |
|---|---|
| [每日操盘建议.py](../每日操盘建议.py) | **新文件** - 09:00 简报：智瞰龙虎 10 只 + 风险 + news_flow 板块 AI + T2 信号段 |
| [post_market_verify.py](../post_market_verify.py) | **新文件** - 15:30 盘后验证：拿当日真实 OHLC 对比推荐阈值 |
| [news_flow_sync.py](../news_flow_sync.py) | **新文件** - 30 分钟定时任务入口 |
| [启动所有服务.bat](../启动所有服务.bat) | 双击恢复 tdx-api + Streamlit（不依赖本机 git） |
| Windows 任务 "智瞰龙虎-每日操盘建议" | 09:00 周一-五（已存在） |
| Windows 任务 "智瞰龙虎-news_flow同步" | 每 30 分钟（已注册） |
| Windows 任务 "智瞰龙虎-盘后验证" | 15:30 周一-五（**新注册**） |

### 2.6 配置
| 文件 | 改动 |
|---|---|
| [.env](../.env) | 加 4 行 TDX 配置（`TDX_ENABLED=true` + `TDX_BASE_URL=http://localhost:9999`） |
| [.env](../.env) | WEBHOOK 4 行（URL + keyword `俊一隅`） |

---

## 3. 关键行为 / 验收点

### 3.1 数据源切换
```bash
# 测试: 13/22 失败 → 18/18 成功
python news_flow_data.py  # 改前
# 13/22 = 59%
# 改后: 18/18 = 100%
```

### 3.2 TDX 部署
```bash
# 启动 oficejo/tdx-api Go 服务
cd D:\projects\projects\tdx-api\web
./server.exe
# 监听 :9999（8181 被 Windows 保留）

# 验证
curl http://localhost:9999/api/health
# {"status":"healthy","time":"1730617200"}
```

### 3.3 钉钉推送链路
```bash
python 每日操盘建议.py
# ✅ 推送成功
# 钉钉收到: 智瞰龙虎 10 只 + news_flow 板块 AI + T2 信号 + 风险提示
```

### 3.4 4 层加权 E2E
```bash
python news_flow_sync.py
# 步骤 0: guba_fetcher (1.3s) - 5000+ 股关注数据写入 guba_sentiment.db
# 步骤 1: run_full_analysis (273.4s) - 18 平台 + AI + 2610 字符 weighted_context
# 步骤 2: run_alert_check (22.3s) - 1 个新预警触发

# AI 摘要质变对比:
# 改前: "当前AI板块流量处于低位, 情绪中性, 缺乏方向"
# 改后: "散户情绪高度集中(多只个股关注指数96), 上升名次超3000, 符合'流量高潮=逃命信号'特征"
```

### 3.5 盘后验证 E2E（**特别注意**）
```bash
# 当前 7-28 08:33 跑（盘前）:
python post_market_verify.py
# ⏸️  当前时间 08:33, A 股 15:00 收盘，禁止运行 ← 护栏正确

# 真正跑通的时机: 7-28 15:30+ (由 Windows 任务自动触发)
```

### 3.6 性能预算
| 操作 | 耗时 | 频率 |
|---|---|---|
| 智瞰龙虎报告生成 | 2-3 min | 手动（每日 18:00 后） |
| TDX 拉价（10 只）| 5s | 60s 周期 |
| news_flow_sync (含 AI) | 297s ≈ 5 min | 每 30 min |
| 每日 09:00 简报 | 30s | 每日 09:00 |
| 盘后验证 | 5-10s | 每日 15:30 |
| 多板块 AI 分析 | 104s (瓶颈) | news_flow_sync 内 |

---

## 4. 踩坑 / 注意事项

### 4.1 端口冲突
- **Windows 保留 8080/8181** (Hyper-V/WSL2 范围) → TDX 改用 9999
- 检查方法: `netsh int ipv4 show excludedportrange protocol=tcp`

### 4.2 TDX 预编译版本不能用
- oficcejo/tdx-api 自带 `web/server.exe` 是端口 8080 编译
- 必须改源码 `port := ":9999"` + 重新 `go build`（首次 ~5min，下一次 5s）
- 编译命令: `go env -w GOPROXY=https://goproxy.cn,direct && go build -o server.exe .`

### 4.3 长路径的 WebFetch 限制
- 抓东方财富/同花顺股吧 API 经常 403
- **不要硬爬股吧**，用 AKShare `stock_comment_em` (5000+ 股 一次拿到)

### 4.4 涨停价不同主板标准
- 主板 (000/002/600/603): ±10% 涨停
- 创业板 (300/301) / 科创板 (688): ±20% 涨停
- stop_loss 公式: `rec_price × (1 + limit_pct) × 0.95`
- → 看到 stock_tracking.stop_loss_price **高于** current_price 是正常的

### 4.5 weighted_context 的数据时间戳
- T2 (guba) 和 T3 (18 平台) 数据都是 **上一交易日**收盘
- **不是**今天实时数据
- **禁止**在描述里说"事后验证"——会让 AI 以为"今天预测被验证了"（但其实没开盘）

### 4.6 post_market_verify 的护栏
- 必须 >= 15:05 才能跑
- 周末不跑
- 15:00 收盘后立即跑，价格还没稳定——给 5 分钟缓冲

### 4.7 news_flow 触发预警的 K 值
- 7-28 跑出来 K 值 1.46（离阈值 1.5 差 0.04）
- 未来几天很可能触发 `viral_spread` 病毒传播预警
- 一旦触发 → monitor_service 主循环 5 分钟内推到钉钉

### 4.8 多板块 AI 分析慢 (104s)
- 这是 5 个板块各调一次 DeepSeek (每个 ~20s)
- 30 分钟频率下是 25 分钟 idle 余量
- 如果网络慢，可能超时——需要 Windows 任务加"不启动新实例"策略

### 4.9 监测池容量限制
- 默认 20 只股上限
- 加上 7-27 报告的 10 只 + 未来 7 天每天 10 只 = 可能塞满
- 7 天 TTL 自动清理，但 20 只上限可能不够
- 调整: `monitor_db.upsert_with_ttl(max_pool=20)` → 调大

---

## 5. 未来改进 / TODO

### 5.1 数据增强（短期，1-3 天）
- [ ] **加 longhubang 龙虎榜数据打通到 news_flow**——资金面维度（现在两套数据是分离的）
- [ ] **加 `stock_hot_search_baidu`** 百度搜索热度（T1 数据源补充）
- [ ] **加 `stock_comment_detail_scrd_focus_em`** 30 日关注指数趋势——AI 能看到"持续升温 vs 突然爆拉"
- [ ] **加 9jqka 同花顺的"短线跟踪"数据**——比东方财富股吧更偏短线

### 5.2 单股级深化（中期，1-2 周）
- [ ] **单股新闻爬虫**——为每只推荐股抓相关新闻
- [ ] **关键词→个股映射扩展**——`stock_keyword_map` 现在只覆盖 22 板块，可加细分赛道
- [ ] **关注指数 + 排名上升 加权计算**——AI 应该根据"涨 3000 名 vs 涨 100 名"区分信号强度

### 5.3 盘后验证增强（中期）
- [ ] **加准确度累计统计**——5/10/20 个交易日的"智瞰龙虎命中率"趋势
- [ ] **加"为什么命中/未命中"分析**——用 T2 数据 (关注度变化) 解释
- [ ] **加 ASCII 图表**——钉钉推送简单历史走势
- [ ] **数据写入 recommendation_performance 表**——独立于 stock_tracking，专门存每日验证结果

### 5.4 性能优化（中期）
- [ ] **多板块 AI 分析从 5 板块减到 2 板块**——从 104s 降到 40s
- [ ] **多板块分析并行**——用 asyncio.gather 同时跑 5 个 DeepSeek 请求
- [ ] **新闻流量 KOL 维度**——加入 22 平台之外的大 V 影响力评分
- [ ] **TDX 数据缓存**——拉过的 1 分钟 K 线缓存 30 秒

### 5.5 架构改进（长期）
- [ ] **Windows 任务防重叠**——避免 4.4 min 任务 + 30 min 频率重叠
- [ ] **报告生成自动化**——18:30 自动触发智瞰龙虎报告生成（目前需要手动）
- [ ] **多账号支持**——多个钉钉群 / 多个 .env 配置
- [ ] **回测系统**——历史 90 天数据回放，看 AI 推荐的准确度
- [ ] **Web UI 增强**——可视化 4 层加权数据 + 历史准确度图表

### 5.6 智能化方向（远期）
- [ ] **AI 自学习**——把 post_market_verify 真实结果喂回 9jqka agents，让 AI 自己调整推荐策略
- [ ] **多模型集成**——DeepSeek + GPT-4 + Claude 三方共识
- [ ] **实时情绪雷达**——盘中 30 分钟一次的情绪变化趋势，看 K 值/K 值变化
- [ ] **板块轮动预测**——基于历史流量数据训练板块轮动模型

---

## 6. 相关文档链接

- 项目根 [CLAUDE.md](../../CLAUDE.md) - 项目级指令
- 改动日志: [2026-07-28-monitor-pool-injection.md](2026-07-28-monitor-pool-injection.md) - 之前的改动
- memory (项目记忆):
  - [longhubang-data-source.md](../../../.claude/projects/.../memory/longhubang-data-source.md)
  - [monitor-realtime-tuning.md](../../../.claude/projects/.../memory/monitor-realtime-tuning.md)
  - [tdx-deployment-recovery.md](../../../.claude/projects/.../memory/tdx-deployment-recovery.md)
  - [daily-trading-workflow.md](../../../.claude/projects/.../memory/daily-trading-workflow.md)
  - [news-flow-integration.md](../../../.claude/projects/.../memory/news-flow-integration.md)
  - [post-market-verify.md](../../../.claude/projects/.../memory/post-market-verify.md)
- 外部: [oficcejo/tdx-api](https://github.com/oficcejo/tdx-api) - TDX Go 服务

---

## 7. 一句话总结

> **7-28 周二我们做了一次"智瞰龙虎全链路打通"：让 22 平台新闻 + 5000+ 股股吧数据 + TDX 实时行情，4 层加权后喂给 4 个 AI 分析师，再用钉钉把"操盘建议+盘中预警+盘后验证"3 个时间点推给你。**
