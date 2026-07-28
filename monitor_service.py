import time
import threading
import schedule
import json
from datetime import datetime, timedelta
from typing import Dict, List, Optional
import streamlit as st
import os
import logging

from monitor_db import monitor_db
from stock_data import StockDataFetcher
from miniqmt_interface import miniqmt, get_miniqmt_status
from notification_service import notification_service

# 导入TDX数据源（如果可用）
try:
    from smart_monitor_tdx_data import SmartMonitorTDXDataFetcher
    TDX_AVAILABLE = True
except ImportError:
    TDX_AVAILABLE = False
    logging.warning("TDX数据源模块未找到，将使用默认数据源")

class StockMonitorService:
    """股票监测服务"""
    
    def __init__(self):
        self.fetcher = StockDataFetcher()
        
        # 初始化TDX数据源（如果启用）
        self.tdx_fetcher = None
        self.use_tdx = False
        
        # 从环境变量获取TDX配置
        tdx_enabled = os.getenv('TDX_ENABLED', 'false').lower() == 'true'
        tdx_base_url = os.getenv('TDX_BASE_URL', 'http://192.168.1.222:8181')
        
        if tdx_enabled and TDX_AVAILABLE:
            try:
                self.tdx_fetcher = SmartMonitorTDXDataFetcher(base_url=tdx_base_url)
                self.use_tdx = True
                logging.info(f"✅ TDX数据源已启用: {tdx_base_url}")
            except Exception as e:
                logging.warning(f"TDX数据源初始化失败，将使用默认数据源: {e}")
        
        self.running = False
        self.thread = None

        # 秒级快路径专用：每只股票距离上次写 price_history 的秒数
        # （TDX 模式下每秒 tick，price_history 节流到每 60s 写一行，避免表爆炸）
        self._last_history_at: dict[int, float] = {}
        # 价格历史写入节流间隔（秒）
        self._history_throttle_seconds = 60
        # 快路径最大监测股数（与 monitor_db.upsert_with_ttl 的 max_pool 保持一致）
        self._fast_poll_cap = 20

        # ============== 决策点（advisor 体系）==============
        # A 股行业缓存：A 股 → industry 字符串（如"半导体"、"化学制药"）。
        # 拉一次就缓存，板块名几乎不变；查询失败 → 缓存 None（视为不抑制，宁可漏不可错）。
        self._sector_cache: dict[str, str | None] = {}
        # news_flow 预警缓存：{alerts: [...], fetched_at: ts}，60s 过期
        self._news_flow_cache: dict = {'alerts': [], 'fetched_at': 0.0}
        # 环境变量控制的 feature flag（便于回退到无 advisor 时代）
        self._enable_news_flow_filter = os.getenv('ENABLE_NEWS_FLOW_ENTRY_FILTER', 'true').lower() == 'true'
        self._news_flow_window_hours = int(os.getenv('NEWS_FLOW_SUPPRESS_WINDOW_HOURS', '4'))
        self._news_flow_cache_ttl = int(os.getenv('NEWS_FLOW_CACHE_TTL_SECONDS', '60'))

        # 换手率缓存（ak.stock_zh_a_spot_em 全市场拉一次，缓存 5min）
        self._turnover_cache: dict = {'df': None, 'fetched_at': 0.0}
        self._turnover_ttl = int(os.getenv('AKSHARE_TURNOVER_CACHE_TTL_SECONDS', '300'))
    
    def start_monitoring(self):
        """启动监测服务"""
        if self.running:
            return
        
        self.running = True
        self.thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self.thread.start()
        st.success("✅ 监测服务已启动")
    
    def stop_monitoring(self):
        """停止监测服务"""
        self.running = False
        if self.thread:
            self.thread.join(timeout=5)
        st.info("⏹️ 监测服务已停止")
    
    def _monitor_loop(self):
        """监测循环

        - TDX 启用时：1s 主循环，走 _fast_poll_tdx（每只 A 股每秒拉一次价 + 触发检查）
        - TDX 未启用：60s 主循环，走 _check_all_stocks（兼容 AKShare/yfinance）
        - news_flow 板块预警：仍按 5 分钟一次（与主循环节奏解耦）
        """
        print("监测服务已启动")
        # news_flow 检查：每 5 分钟一次。计数器以"秒"为单位，避免主循环节奏变化时漂移。
        self._news_flow_tick_seconds = 0
        self._news_flow_interval_seconds = 300  # 5 分钟
        while self.running:
            try:
                if self.use_tdx:
                    self._fast_poll_tdx()
                else:
                    self._check_all_stocks()

                # 新闻流量预警（5 分钟一次）
                self._news_flow_tick_seconds += 1 if self.use_tdx else 60
                if self._news_flow_tick_seconds >= self._news_flow_interval_seconds:
                    self._news_flow_tick_seconds = 0
                    self._check_news_flow_alerts()

                # 主循环 sleep：TDX 走 1s，否则 60s
                sleep_seconds = 1 if self.use_tdx else 60
                time.sleep(sleep_seconds)
            except Exception as e:
                print(f"监测服务错误: {e}")
                time.sleep(5 if self.use_tdx else 60)

    def _check_news_flow_alerts(self):
        """
        检查 news_flow 模块的未通知预警（板块级"流量高潮/退潮"等信号），
        一旦发现推钉钉。失败时降级为仅打印日志，不影响主监测循环。
        """
        try:
            from news_flow_db import NewsFlowDatabase
            from news_flow_alert import NewsFlowAlertSystem
            from notification_service import notification_service
        except Exception as e:
            print(f"[news_flow] 模块加载失败: {e}")
            return

        try:
            db = NewsFlowDatabase()
            alerts = db.get_unnotified_alerts()
            if not alerts:
                return

            # 过滤出高优先级（danger / warning）的板块信号
            high_priority = [a for a in alerts if a.get('alert_level') in ('danger', 'warning')]
            if not high_priority:
                return

            # 用现有的 NewsFlowAlertSystem 推送（已封装好分组+标记已读）
            alert_system = NewsFlowAlertSystem()
            alert_system.notification_service = notification_service
            alert_system.db = db
            ok = alert_system.send_notification(high_priority)

            if ok:
                print(f"[news_flow] 已推送 {len(high_priority)} 条板块预警 (danger/warning)")
            else:
                print(f"[news_flow] 推送失败: {len(high_priority)} 条板块预警")
        except Exception as e:
            print(f"[news_flow] 检查预警异常: {e}")
    
    def _check_all_stocks(self):
        """检查所有监测股票"""
        stocks = monitor_db.get_monitored_stocks()
        current_time = datetime.now()
        
        updated_count = 0
        for stock in stocks:
            # 检查是否需要更新价格
            last_checked = stock.get('last_checked')
            check_interval = stock.get('check_interval', 1)  # 默认 1 分钟（实时监测）
            
            if last_checked:
                last_checked_dt = datetime.fromisoformat(last_checked)
                next_check = last_checked_dt + timedelta(minutes=check_interval)
                if current_time < next_check:
                    # 显示距离下次检查的时间
                    time_left = (next_check - current_time).total_seconds() / 60
                    print(f"股票 {stock['symbol']} 距离下次检查还有 {time_left:.1f} 分钟")
                    continue
            
            try:
                print(f"正在更新股票 {stock['symbol']} 的价格...")
                self._update_stock_price(stock)
                updated_count += 1
                
                # 在每个股票请求之间增加延迟，避免API限流
                if updated_count < len(stocks):
                    time.sleep(3)  # 每个股票之间等待3秒
            except Exception as e:
                print(f"❌ 更新股票 {stock['symbol']} 价格失败: {e}")
                time.sleep(3)  # 失败后也等待3秒再继续
        
        if updated_count > 0:
            print(f"✅ 本轮共更新了 {updated_count} 只股票")

    def _fast_poll_tdx(self):
        """TDX 秒级快路径：每秒轮询所有 A 股监测股票。

        - 通过 monitor_db.get_a_share_stocks_for_fast_poll 拉取未过期、A股、按 expires_at 排序、上限 20 只
        - 逐只调 TDX 拉价 + 触发条件检查
        - price_history 由 _update_stock_price_fast 按 60s 节流
        - 单只失败不影响其他股票
        """
        try:
            stocks = monitor_db.get_a_share_stocks_for_fast_poll(cap=self._fast_poll_cap)
        except Exception as e:
            print(f"[fast_poll] 拉取监测股失败: {e}")
            return

        if not stocks:
            return

        for stock in stocks:
            if not stock.get('notification_enabled', True):
                continue
            try:
                self._update_stock_price_fast(stock)
            except Exception as e:
                print(f"[fast_poll] {stock['symbol']} 更新失败: {e}")
                # 单只失败不影响其他股票；不更新 last_checked 避免持续重试同一只

    def _update_stock_price_fast(self, stock: Dict):
        """TDX 秒级 tick：拉一次价 + 触发检查 + 节流写 price_history。

        - 每秒都更新 current_price + last_checked（UI 实时显示）
        - 仅当距上次 price_history > _history_throttle_seconds 时才追加一行历史
        """
        symbol = stock['symbol']
        quote = self.tdx_fetcher.get_realtime_quote(symbol)
        if not quote or not quote.get('current_price'):
            return

        try:
            price = float(quote['current_price'])
        except (ValueError, TypeError):
            return

        now = time.time()
        stock_id = stock['id']
        last_h = self._last_history_at.get(stock_id, 0)

        if now - last_h >= self._history_throttle_seconds:
            # 走完整 update：current_price + last_checked + price_history
            monitor_db.update_stock_price(stock_id, price)
            self._last_history_at[stock_id] = now
        else:
            # tick-only：仅 current_price + last_checked，不写 price_history
            monitor_db.update_current_price_only(stock_id, price)

        # 触发条件检查（沿用 60 分钟去重逻辑；把 quote 整包传下去，补模板字段用）
        self._check_trigger_conditions(stock, price, quote=quote)
    
    def _update_stock_price(self, stock: Dict):
        """更新股票价格并检查条件"""
        symbol = stock['symbol']
        current_price = None
        quote = None  # TDX 返回的整包行情（用于补 DingTalk 模板字段）

        # 获取最新价格
        try:
            # 优先使用TDX数据源（如果已启用且为A股）
            if self.use_tdx and self._is_a_stock(symbol):
                print(f"🔄 使用TDX数据源获取 {symbol} 行情...")
                quote = self.tdx_fetcher.get_realtime_quote(symbol)

                if quote and quote.get('current_price'):
                    current_price = float(quote['current_price'])
                    print(f"✅ TDX获取成功: {symbol} 当前价格: ¥{current_price}")
                else:
                    # TDX失败，降级到默认数据源
                    print(f"⚠️ TDX获取失败，降级到默认数据源: {symbol}")
                    current_price = self._get_price_from_default_source(symbol)
            else:
                # 使用默认数据源（AKShare/yfinance）
                current_price = self._get_price_from_default_source(symbol)

            # 处理获取到的价格
            if current_price and current_price > 0:
                try:
                    current_price = float(current_price)
                    # 更新数据库（包括更新last_checked时间）
                    monitor_db.update_stock_price(stock['id'], current_price)
                    print(f"✅ {symbol} 当前价格: ¥{current_price}")

                    # 检查触发条件（把 quote 整包传下去，补模板字段用）
                    self._check_trigger_conditions(stock, current_price, quote=quote)
                except (ValueError, TypeError) as e:
                    print(f"❌ 股票 {symbol} 价格格式错误: {current_price}")
                    # 即使失败也更新last_checked，避免持续重试
                    monitor_db.update_last_checked(stock['id'])
            else:
                print(f"⚠️ 无法获取股票 {symbol} 的当前价格")
                # 更新last_checked，避免持续重试
                monitor_db.update_last_checked(stock['id'])

        except Exception as e:
            print(f"❌ 获取股票 {symbol} 数据失败: {e}")
            # 即使失败也更新last_checked，避免持续重试
            try:
                monitor_db.update_last_checked(stock['id'])
            except:
                pass
    
    def _is_a_stock(self, symbol: str) -> bool:
        """判断是否为A股（6位数字）"""
        return symbol.isdigit() and len(symbol) == 6
    
    def _get_price_from_default_source(self, symbol: str) -> float:
        """从默认数据源获取价格"""
        try:
            stock_info = self.fetcher.get_stock_info(symbol)
            current_price = stock_info.get('current_price')
            
            if current_price and current_price != 'N/A':
                return float(current_price)
            return None
        except Exception as e:
            print(f"默认数据源获取失败: {e}")
            return None
    
    def _check_trigger_conditions(self, stock: Dict, current_price: float, quote: Optional[Dict] = None):
        """检查触发条件

        - 入场（entry）：走 _evaluate_entry_decision 决策点（news_flow 板块过滤可抑制/改写）
        - 止盈/止损（take_profit / stop_loss）：风控纪律优先，不走 advisor，永远推送
        """
        if not stock.get('notification_enabled', True):
            return

        entry_range = stock.get('entry_range', {})
        take_profit = stock.get('take_profit')
        stop_loss = stock.get('stop_loss')

        # 构造 DingTalk 模板需要的上下文快照（一次性，避免后续字段 N/A）
        ctx = self._build_notification_context(stock, current_price, quote)

        # 检查进场区间（走 advisor 决策点：A 方案 + 未来扩展 B/C）
        if entry_range and entry_range.get('min') and entry_range.get('max'):
            if current_price >= entry_range['min'] and current_price <= entry_range['max']:
                # 60 分钟去重（避免同一信号反复推）
                if not monitor_db.has_recent_notification(stock['id'], 'entry', minutes=60):
                    base_msg = (
                        f"股票 {stock['symbol']} ({stock['name']}) "
                        f"价格 {current_price} 进入进场区间 "
                        f"[{entry_range['min']}-{entry_range['max']}]"
                    )
                    decision = self._evaluate_entry_decision(stock, 'entry', base_msg)

                    # 总是写 notifications（抑制也写，方便 UI 追溯）
                    monitor_db.add_notification(stock['id'], 'entry', decision['final_message'], context=ctx)

                    # 未被抑制时才推 DingTalk
                    if decision['should_push']:
                        notification_service.send_notifications()

                    # 自动交易：未抑制 + quant_enabled 时执行
                    if decision['should_push'] and stock.get('quant_enabled', False):
                        self._execute_quant_trade(stock, 'entry', current_price)

                    if decision['suppressed']:
                        print(
                            f"[advisor] {stock['symbol']} 入场已抑制: "
                            f"{' / '.join(decision['reasons']) or '(no reason)'}"
                        )
        
        # 检查止盈
        if take_profit and current_price >= take_profit:
            # 检查是否在最近60分钟内已发送过相同通知，避免重复
            if not monitor_db.has_recent_notification(stock['id'], 'take_profit', minutes=60):
                message = f"股票 {stock['symbol']} ({stock['name']}) 价格 {current_price} 达到止盈位 {take_profit}"
                monitor_db.add_notification(stock['id'], 'take_profit', message, context=ctx)
                
                # 立即发送通知（包括邮件）
                notification_service.send_notifications()
            
            # 如果启用量化交易，执行自动交易
            if stock.get('quant_enabled', False):
                self._execute_quant_trade(stock, 'take_profit', current_price)
        
        # 检查止损
        if stop_loss and current_price <= stop_loss:
            # 检查是否在最近60分钟内已发送过相同通知，避免重复
            if not monitor_db.has_recent_notification(stock['id'], 'stop_loss', minutes=60):
                message = f"股票 {stock['symbol']} ({stock['name']}) 价格 {current_price} 达到止损位 {stop_loss}"
                monitor_db.add_notification(stock['id'], 'stop_loss', message, context=ctx)
                
                # 立即发送通知（包括邮件）
                notification_service.send_notifications()
            
            # 如果启用量化交易，执行自动交易
            if stock.get('quant_enabled', False):
                self._execute_quant_trade(stock, 'stop_loss', current_price)

    def _build_notification_context(self, stock: Dict, current_price: float,
                                     quote: Optional[Dict]) -> Dict:
        """
        构造 DingTalk 模板需要的上下文快照（写入 notifications.context 列）。

        来源:
          - TDX quote: change_pct / change_amount / volume
          - portfolio_db: 持仓状态 + 成本价（query at trigger time）
          - 当前时间: 交易时段判断

        任何字段查不到 → 不放进去 → 模板自动 fallback 'N/A' / '未知'，避免 0 误判
        """
        ctx: Dict = {}

        # 1) 行情快照（来自 TDX quote；非 TDX 路径 quote=None，跳过）
        if quote:
            if quote.get('current_price') is not None:
                ctx['current_price'] = float(quote['current_price'])
            if quote.get('change_pct') is not None:
                ctx['change_pct'] = round(float(quote['change_pct']), 2)
            if quote.get('change_amount') is not None:
                ctx['change_amount'] = round(float(quote['change_amount']), 3)
            if quote.get('volume') is not None:
                ctx['volume'] = int(quote['volume'])

        # turnover_rate：TDX 不流通股本 → 从 AKShare 全市场行情取（缓存 5min）
        ctx['turnover_rate'] = self._get_turnover_rate(stock.get('symbol', ''))

        # 非 TDX 路径：用 current_price 兜底
        if 'current_price' not in ctx:
            ctx['current_price'] = current_price

        # 2) 持仓快照（portfolio_db；查不到就 None）
        try:
            from portfolio_db import PortfolioDB
            pf = PortfolioDB()
            pos = pf.get_stock_by_code(stock.get('symbol', ''))
            if pos:
                quantity = pos.get('quantity') or 0
                cost = pos.get('cost_price')
                ctx['position_status'] = f"持仓 {quantity} 股"
                if cost:
                    ctx['position_cost'] = round(float(cost), 3)
                    if ctx['current_price'] and cost:
                        pct = (ctx['current_price'] - float(cost)) / float(cost) * 100
                        ctx['profit_loss_pct'] = round(pct, 2)
            else:
                ctx['position_status'] = '未持仓'
        except Exception as e:
            print(f"[ctx] 查询持仓失败: {e}")

        # 3) 交易时段判断
        ctx['trading_session'] = self._infer_trading_session()

        return ctx

    @staticmethod
    def _infer_trading_session() -> str:
        """判断当前交易时段（与 monitor_scheduler 规则一致；A 股简化版）。"""
        now = datetime.now()
        if now.weekday() + 1 not in (1, 2, 3, 4, 5):
            return '非交易日'
        hm = now.strftime('%H:%M')
        if hm < '09:30':
            return '盘前'
        if '09:30' <= hm <= '11:30':
            return '上午交易'
        if '11:30' < hm < '13:00':
            return '午休'
        if '13:00' <= hm <= '15:00':
            return '下午交易'
        return '已收盘'

    def _get_turnover_rate(self, symbol: str) -> Optional[float]:
        """
        查 A 股换手率（%）。

        来源: ak.stock_zh_a_spot_em() 返回全市场实时行情（含"换手率"列）。
        缓存: 实例级 DataFrame 缓存，TTL 默认 5 分钟（env 可调）。
            - 5 分钟内多次查询只拉一次网络
            - 拉取失败 → 返回 None（模板 fallback N/A）

        返回: float（百分比值，如 2.5 表示 2.5%）或 None
        """
        # 非 A 股跳过
        if not (symbol.isdigit() and len(symbol) == 6):
            return None

        now = time.time()
        cache_age = now - self._turnover_cache['fetched_at']

        # 缓存有效：直接用
        if cache_age < self._turnover_ttl and self._turnover_cache['df'] is not None:
            df = self._turnover_cache['df']
        else:
            # 缓存过期或首次：拉一次全市场
            try:
                import akshare as ak
                df = ak.stock_zh_a_spot_em()
                self._turnover_cache = {'df': df, 'fetched_at': now}
                print(f"[turnover] 缓存刷新 {len(df)} 只股票")
            except Exception as e:
                print(f"[turnover] ak.stock_zh_a_spot_em 失败: {e}")
                # 失败时仍返回旧缓存（可能为空）
                df = self._turnover_cache.get('df')

        if df is None or df.empty:
            return None

        # akshare 列名可能是 '代码' / 'symbol'，做容错
        code_col = None
        for c in ('代码', 'symbol', '股票代码'):
            if c in df.columns:
                code_col = c
                break
        if code_col is None or '换手率' not in df.columns:
            return None

        # 过滤（用 str 比较，akshare 列可能是 int64）
        try:
            row = df[df[code_col].astype(str) == str(symbol)]
        except Exception:
            return None
        if row.empty:
            return None

        val = row.iloc[0]['换手率']
        # pandas NaN / None 防御
        try:
            import math
            if val is None or (isinstance(val, float) and math.isnan(val)):
                return None
        except Exception:
            pass
        try:
            return round(float(val), 2)
        except (ValueError, TypeError):
            return None

    # ============== 决策点 + Advisor 体系（A 方案，可扩展 B/C） ==============

    def _evaluate_entry_decision(self, stock: Dict, signal_type: str, base_message: str) -> Dict:
        """
        入场信号的最终推送决策点。

        当前 advisor:
          - _advisor_news_flow: 板块级 news_flow 风险过滤（danger 抑制、warning 改写）

        未来扩展:
          - B 方案: append `_advisor_agents_realtime(stock, price)` → 调个股潜力分析师
          - C 方案: append `_advisor_sector_pre_filtered(stock)` → 选股阶段已过滤板块
        主流程不动，只往 advisors 列表加 verdict 即可。

        Returns:
            {
              'should_push': bool,        # False = 抑制（不推 DingTalk，但写 notification 留痕）
              'final_message': str,       # 实际写入 notifications 表 + 推送的消息
              'suppressed': bool,         # 是否被任一 advisor 抑制
              'reasons': list[str],       # 各 advisor 的理由（用于日志/UI）
            }
        """
        # TP/SL 不走 advisor（风控纪律优先）
        if signal_type != 'entry':
            return {
                'should_push': True,
                'final_message': base_message,
                'suppressed': False,
                'reasons': [],
            }

        advisors = [
            self._advisor_news_flow(stock),
        ]
        # future:
        # advisors.append(self._advisor_agents_realtime(stock, current_price))

        reasons: list[str] = []
        final_message = base_message
        suppressed = False

        for verdict in advisors:
            reason = verdict.get('reason') or ''
            source = verdict.get('source', '?')
            reasons.append(f"[{source}] {reason}" if reason else f"[{source}] {verdict['decision']}")

            if verdict['decision'] == 'suppress':
                suppressed = True
                # 把"已抑制"原因写到消息里（即便不推 DingTalk，UI 也能看到）
                final_message = (
                    f"🚫 [入场已抑制] {base_message}\n\n"
                    f"🚫 抑制原因: {reason}"
                )
            elif verdict['decision'] == 'warn' and not suppressed:
                # 仅在未被抑制时附加 warning（避免消息过长）
                final_message = f"{base_message}\n\n⚠️ {reason}"

        return {
            'should_push': not suppressed,
            'final_message': final_message,
            'suppressed': suppressed,
            'reasons': reasons,
        }

    def _advisor_news_flow(self, stock: Dict) -> Dict:
        """
        news_flow 板块级风险过滤 advisor。

        规则:
          - 板块命中 active danger 预警 → suppress（板块高危时不入场）
          - 板块命中 active warning 预警 → warn（推送但附加提示）
          - 没有匹配或板块未知 → pass（默认放行，宁可漏不可错）

        数据源:
          - news_flow_db.get_alerts(since=N hours) → 包含已通知 + 未通知的近期预警
          - 行业映射: ak.stock_individual_info_em(symbol).industry（缓存到 self._sector_cache）

        返回 dict: {'source': 'news_flow', 'decision': 'pass'|'warn'|'suppress', 'reason': str}
        """
        if not self._enable_news_flow_filter:
            return {'source': 'news_flow', 'decision': 'pass', 'reason': 'filter disabled by env flag'}

        symbol = stock.get('symbol', '')
        industry = self._get_stock_industry(symbol)
        if not industry:
            # 拉不到行业 → 默认放行（保守抑制反而误杀）
            return {'source': 'news_flow', 'decision': 'pass', 'reason': 'industry unknown'}

        alerts = self._get_active_news_flow_alerts()
        if not alerts:
            return {'source': 'news_flow', 'decision': 'pass', 'reason': 'no active alerts'}

        # 匹配逻辑：alert 的 related_topics 含 symbol 或 affected_sectors 含 industry
        sym = str(symbol)
        ind = str(industry)
        matched_danger = None
        matched_warning = None

        for alert in alerts:
            level = alert.get('alert_level')
            if level not in ('danger', 'warning'):
                continue

            # 1) related_topics 是否含股票代码
            topics = alert.get('related_topics') or []
            if isinstance(topics, str):
                try:
                    topics = json.loads(topics)
                except Exception:
                    topics = []
            hit_by_symbol = any(sym in str(t) for t in topics)

            # 2) affected_sectors 是否含行业（子串模糊匹配）
            affected = alert.get('affected_sectors') or []
            if isinstance(affected, str):
                try:
                    affected = json.loads(affected)
                except Exception:
                    affected = []
            hit_by_sector = any(
                ind in str(s.get('name', '')) or str(s.get('name', '')) in ind
                for s in affected if isinstance(s, dict)
            )

            if hit_by_symbol or hit_by_sector:
                if level == 'danger' and matched_danger is None:
                    matched_danger = alert
                elif level == 'warning' and matched_warning is None:
                    matched_warning = alert

        if matched_danger:
            return {
                'source': 'news_flow',
                'decision': 'suppress',
                'reason': f"板块 {industry} 命中 danger 预警: {str(matched_danger.get('title', ''))[:60]}",
            }
        if matched_warning:
            return {
                'source': 'news_flow',
                'decision': 'warn',
                'reason': f"板块 {industry} 命中 warning 预警: {str(matched_warning.get('title', ''))[:60]}",
            }
        return {'source': 'news_flow', 'decision': 'pass', 'reason': 'no matching alert'}

    def _get_stock_industry(self, symbol: str) -> Optional[str]:
        """
        查 A 股所属行业（带缓存）。
        用 akshare 的 stock_individual_info_em（项目里 stock_data / smart_monitor_data / macro_analysis_data 都在用）。
        失败时缓存 None → 调用方按"行业未知"放行（宁可漏不可错）。
        """
        if symbol in self._sector_cache:
            return self._sector_cache[symbol]

        # 6 位数字才走 AKShare（A 股；news_flow 主要管 A 股）
        if not (symbol.isdigit() and len(symbol) == 6):
            self._sector_cache[symbol] = None
            return None

        try:
            import akshare as ak
            df = ak.stock_individual_info_em(symbol=symbol)
            industry = None
            if df is not None and not df.empty:
                # akshare 该接口字段可能是 '行业' / 'industry'（不同版本）
                for col in ('行业', 'industry', '所属行业'):
                    if col in df.columns:
                        val = df[col].iloc[0]
                        if val and str(val) != 'nan':
                            industry = str(val)
                            break
            self._sector_cache[symbol] = industry
            return industry
        except Exception as e:
            print(f"[advisor_news_flow] 查询 {symbol} 行业失败: {e}")
            self._sector_cache[symbol] = None
            return None

    def _get_active_news_flow_alerts(self) -> List[Dict]:
        """取最近 N 小时的 danger/warning 预警（带 60s 内存缓存，避免 1Hz 轮询打爆 DB）。"""
        now = time.time()
        cache_age = now - self._news_flow_cache['fetched_at']
        if cache_age < self._news_flow_cache_ttl and self._news_flow_cache['alerts']:
            return self._news_flow_cache['alerts']

        try:
            from news_flow_db import NewsFlowDatabase
            db = NewsFlowDatabase()
            # news_flow_db.get_alerts 接受 since 参数；时间窗由 self._news_flow_window_hours 控制
            since = f'-{self._news_flow_window_hours} hours'
            if hasattr(db, 'get_alerts'):
                all_alerts = db.get_alerts(since=since)
            else:
                all_alerts = db.get_unnotified_alerts()
            alerts = [a for a in all_alerts if a.get('alert_level') in ('danger', 'warning')]
            self._news_flow_cache = {'alerts': alerts, 'fetched_at': now}
            return alerts
        except Exception as e:
            print(f"[advisor_news_flow] 查询预警失败: {e}")
            # 失败时返回旧缓存（可能为空），保守不抑制
            return self._news_flow_cache.get('alerts', [])

    def _execute_quant_trade(self, stock: Dict, signal_type: str, current_price: float):
        """执行量化交易"""
        try:
            # 检查MiniQMT是否连接
            if not miniqmt.is_connected():
                print(f"MiniQMT未连接，无法执行 {stock['symbol']} 的量化交易")
                return
            
            # 获取量化配置
            quant_config = stock.get('quant_config', {})
            if not quant_config:
                print(f"股票 {stock['symbol']} 未配置量化参数")
                return
            
            # 执行策略信号
            signal = {
                'type': signal_type,
                'price': current_price,
                'message': f"{signal_type} signal triggered"
            }
            
            position_size = quant_config.get('max_position_pct', 0.2)
            success, msg = miniqmt.execute_strategy_signal(
                stock['id'], 
                stock['symbol'], 
                signal, 
                position_size
            )
            
            if success:
                print(f"✅ 量化交易成功: {stock['symbol']} - {msg}")
                # 记录交易通知（量化交易通知不检查重复，因为每次交易都应该通知）
                monitor_db.add_notification(
                    stock['id'], 
                    'quant_trade', 
                    f"量化交易执行: {msg}"
                )
                # 立即发送通知（包括邮件）
                notification_service.send_notifications()
            else:
                print(f"❌ 量化交易失败: {stock['symbol']} - {msg}")
                
        except Exception as e:
            print(f"执行量化交易异常: {stock['symbol']} - {str(e)}")
    
    def get_stocks_needing_update(self) -> List[Dict]:
        """获取需要更新价格的股票"""
        stocks = monitor_db.get_monitored_stocks()
        current_time = datetime.now()
        need_update = []
        
        for stock in stocks:
            last_checked = stock.get('last_checked')
            check_interval = stock.get('check_interval', 1)  # 默认 1 分钟（实时监测）
            
            if not last_checked:
                need_update.append(stock)
                continue
            
            last_checked_dt = datetime.fromisoformat(last_checked)
            next_check = last_checked_dt + timedelta(minutes=check_interval)
            if current_time >= next_check:
                need_update.append(stock)
        
        return need_update
    
    def manual_update_stock(self, stock_id: int):
        """手动更新股票价格"""
        stock = monitor_db.get_stock_by_id(stock_id)
        if stock:
            self._update_stock_price(stock)
            return True
        return False
    
    def get_scheduler(self):
        """获取调度器实例"""
        from monitor_scheduler import get_scheduler
        return get_scheduler(self)

# 全局监测服务实例
monitor_service = StockMonitorService()