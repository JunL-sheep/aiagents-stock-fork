"""
test_advisors.py — 多 advisor 决策点单元测试（2026-07-28）

不依赖真 TDX / 网络，全 mock。覆盖：
- #1 _advisor_trading_session（4 用例）
- #2 _advisor_trend（4 用例）
- #3 _advisor_volume（4 用例）
- #6 _advisor_position（4 用例）
- #4 _advisor_triple_screen（4 用例，默认禁用）
- #5 _advisor_market_regime（4 用例，默认禁用）

跑法：
    cd d:/projects/projects/aiagents-stock-main/aiagents-stock-main
    python -m tests.test_advisors
"""

import os
import sys
from datetime import datetime
from unittest.mock import patch, MagicMock

# 让 import 走项目根
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# mock 掉 akshare / portfolio_db / monitor_db / notification_service 防止初始化时拉网络
import types


class _FakeAkModule:
    @staticmethod
    def stock_individual_info_em(symbol):
        # 返回带 '行业' 列的 DataFrame
        import pandas as pd
        return pd.DataFrame({'行业': ['玻璃制造']})


sys.modules['akshare'] = _FakeAkModule()

import monitor_db as md  # noqa


class _FakeMonitorDB:
    def __init__(self):
        self.notifications = []

    def add_monitored_stock(self, *a, **k):
        return 1

    def has_recent_notification(self, *a, **k):
        return False

    def update_stock_price(self, *a, **k):
        pass

    def update_last_checked(self, *a, **k):
        pass

    def update_current_price_only(self, *a, **k):
        pass

    def get_a_share_stocks_for_fast_poll(self, cap=50):
        return []

    def get_monitored_stocks(self):
        return []


md.monitor_db = _FakeMonitorDB()

# notification_service
import notification_service as ns  # noqa


class _FakeNS:
    @classmethod
    def send_notifications(cls):
        pass


ns.notification_service = _FakeNS()


# 加载 monitor_service
from monitor_service import StockMonitorService  # noqa: E402


def _make_service(env_overrides=None):
    """构造一个干净的 service 实例，所有缓存清空，env flag 按需覆盖"""
    env = {
        'ENABLE_TRADING_SESSION_ENTRY_FILTER': 'true',
        'ENABLE_TREND_ENTRY_FILTER': 'true',
        'ENABLE_VOLUME_ENTRY_FILTER': 'true',
        'ENABLE_POSITION_ENTRY_FILTER': 'true',
        'ENABLE_TRIPLE_SCREEN_ENTRY_FILTER': 'true',
        'ENABLE_MARKET_REGIME_ENTRY_FILTER': 'true',
    }
    if env_overrides:
        env.update(env_overrides)
    with patch.dict(os.environ, env, clear=False):
        svc = StockMonitorService()
    svc._tech_indicators_cache.clear()
    svc._triple_screen_cache.clear()
    svc._market_regime_cache = {'regime': 'NORMAL_INIT', 'fetched_at': 0.0}
    svc._news_flow_cache = {'alerts': [], 'fetched_at': 0.0}
    svc._sector_cache.clear()
    return svc


def _pass(verdict):
    return verdict['decision'] == 'pass'


def _warn(verdict):
    return verdict['decision'] == 'warn'


def _suppress(verdict):
    return verdict['decision'] == 'suppress'


# ============ #1 trading_session ============
def test_advisor_01_trading_session_pass_morning():
    svc = _make_service()
    stock = {'symbol': '600519'}
    ctx = {'trading_session': '上午交易'}
    v = svc._advisor_trading_session(stock, ctx)
    assert _pass(v), v
    assert '上午' in v['reason']


def test_advisor_01_trading_session_suppress_afternoon_1455():
    svc = _make_service()
    stock = {'symbol': '600519'}
    ctx = {'trading_session': '下午交易'}
    # 把"现在时间"强制设成 14:56
    fake_now = datetime(2026, 7, 28, 14, 56, 0)
    with patch('monitor_service.datetime') as mock_dt:
        mock_dt.now.return_value = fake_now
        v = svc._advisor_trading_session(stock, ctx)
    assert _suppress(v), v
    assert '尾盘' in v['reason']


def test_advisor_01_trading_session_suppress_lunch():
    svc = _make_service()
    stock = {'symbol': '600519'}
    ctx = {'trading_session': '午休'}
    v = svc._advisor_trading_session(stock, ctx)
    assert _suppress(v), v


def test_advisor_01_trading_session_fallback_no_ctx():
    svc = _make_service()
    stock = {'symbol': '600519'}
    v = svc._advisor_trading_session(stock, {})  # 无 trading_session
    assert _pass(v), v
    assert 'fallback' in v['reason']


# ============ #2 trend ============
def test_advisor_02_trend_pass_up():
    svc = _make_service()
    stock = {'symbol': '600519'}
    with patch.object(svc, '_get_cached_tech_indicators',
                      return_value={'trend': 'up', 'ma5': 11, 'ma20': 10, 'ma60': 9}):
        v = svc._advisor_trend(stock)
    assert _pass(v), v


def test_advisor_02_trend_warn_sideways():
    svc = _make_service()
    stock = {'symbol': '600519'}
    with patch.object(svc, '_get_cached_tech_indicators',
                      return_value={'trend': 'sideways'}):
        v = svc._advisor_trend(stock)
    assert _warn(v), v


def test_advisor_02_trend_suppress_down():
    svc = _make_service()
    stock = {'symbol': '600519'}
    with patch.object(svc, '_get_cached_tech_indicators',
                      return_value={'trend': 'down'}):
        v = svc._advisor_trend(stock)
    assert _suppress(v), v


def test_advisor_02_trend_fallback_no_data():
    svc = _make_service()
    stock = {'symbol': '600519'}
    with patch.object(svc, '_get_cached_tech_indicators', return_value=None):
        v = svc._advisor_trend(stock)
    assert _pass(v), v
    assert 'fallback' in v['reason']


# ============ #3 volume ============
def test_advisor_03_volume_pass_high():
    svc = _make_service()
    stock = {'symbol': '600519'}
    with patch.object(svc, '_get_cached_tech_indicators',
                      return_value={'volume_ratio': 1.5}):
        v = svc._advisor_volume(stock)
    assert _pass(v), v


def test_advisor_03_volume_warn_shrink():
    svc = _make_service()
    stock = {'symbol': '600519'}
    with patch.object(svc, '_get_cached_tech_indicators',
                      return_value={'volume_ratio': 0.6}):
        v = svc._advisor_volume(stock)
    assert _warn(v), v
    assert '缩量' in v['reason']


def test_advisor_03_volume_warn_normal():
    svc = _make_service()
    stock = {'symbol': '600519'}
    with patch.object(svc, '_get_cached_tech_indicators',
                      return_value={'volume_ratio': 1.1}):
        v = svc._advisor_volume(stock)
    assert _warn(v), v


def test_advisor_03_volume_fallback_no_data():
    svc = _make_service()
    stock = {'symbol': '600519'}
    with patch.object(svc, '_get_cached_tech_indicators', return_value=None):
        v = svc._advisor_volume(stock)
    assert _pass(v), v


# ============ #6 position ============
def test_advisor_06_position_pass_no_holding():
    svc = _make_service()
    stock = {'symbol': '600519'}
    ctx = {'position_status': '未持仓'}
    v = svc._advisor_position(stock, ctx)
    assert _pass(v), v


def test_advisor_06_position_warn_small_loss():
    svc = _make_service()
    stock = {'symbol': '600519'}
    ctx = {'position_status': '持仓 1000 股', 'profit_loss_pct': -3.0}
    v = svc._advisor_position(stock, ctx)
    assert _warn(v), v


def test_advisor_06_position_suppress_big_profit():
    svc = _make_service()
    stock = {'symbol': '600519'}
    ctx = {'position_status': '持仓 1000 股', 'profit_loss_pct': 8.0}
    v = svc._advisor_position(stock, ctx)
    assert _suppress(v), v
    assert '浮盈' in v['reason']


def test_advisor_06_position_fallback_no_ctx():
    svc = _make_service()
    stock = {'symbol': '600519'}
    v = svc._advisor_position(stock, {})
    assert _pass(v), v


# ============ #4 triple_screen（默认禁用，需手动启用） ============
def test_advisor_04_triple_screen_disabled_by_default():
    # Phase 2 已改为默认启用（2026-07-28 用户要求）
    # 如果要测禁用行为，需 env ENABLE_TRIPLE_SCREEN_ENTRY_FILTER=false
    svc = _make_service(env_overrides={'ENABLE_TRIPLE_SCREEN_ENTRY_FILTER': 'false'})
    stock = {'symbol': '600519'}
    v = svc._advisor_triple_screen(stock)
    assert _pass(v), v
    assert 'filter disabled' in v['reason']


def test_advisor_04_triple_screen_pass_high_score():
    svc = _make_service(env_overrides={'ENABLE_TRIPLE_SCREEN_ENTRY_FILTER': 'true'})
    stock = {'symbol': '600519'}
    svc._tech_indicators_cache['warmup'] = {'data': {}, 'fetched_at': 1}  # 解除首轮保护

    # 构造一个 200 根的 DataFrame，让 MACD/RSI/KDJ/MA5 都给出高分
    import pandas as pd
    import numpy as np
    dates = pd.date_range('2025-01-01', periods=200)
    # 单调上涨 → MACD 多头 + RSI 中等 + J 不超买
    close = np.linspace(10, 30, 200)
    high = close * 1.01
    low = close * 0.99
    df = pd.DataFrame({
        '日期': dates, '开盘': close * 0.99, '收盘': close,
        '最高': high, '最低': low, '成交量': [100000] * 200,
    })
    svc.tdx_fetcher.get_kline_data = MagicMock(return_value=df)

    v = svc._advisor_triple_screen(stock)
    assert _pass(v), v
    assert '三重滤网评分' in v['reason']


def test_advisor_04_triple_screen_suppress_low_score():
    svc = _make_service(env_overrides={'ENABLE_TRIPLE_SCREEN_ENTRY_FILTER': 'true'})
    stock = {'symbol': '600519'}
    svc._tech_indicators_cache['warmup'] = {'data': {}, 'fetched_at': 1}

    # 构造一个 MACD 死叉 + RSI 极低 + 价格远离 MA5 的 DataFrame
    import pandas as pd
    import numpy as np
    dates = pd.date_range('2025-01-01', periods=200)
    close = np.linspace(50, 10, 200)  # 持续下跌
    high = close * 1.05
    low = close * 0.95
    df = pd.DataFrame({
        '日期': dates, '开盘': close * 1.01, '收盘': close,
        '最高': high, '最低': low, '成交量': [100000] * 200,
    })
    svc.tdx_fetcher.get_kline_data = MagicMock(return_value=df)

    v = svc._advisor_triple_screen(stock)
    assert _suppress(v), v


def test_advisor_04_triple_screen_fallback_on_error():
    svc = _make_service(env_overrides={'ENABLE_TRIPLE_SCREEN_ENTRY_FILTER': 'true'})
    stock = {'symbol': '600519'}
    svc._tech_indicators_cache['warmup'] = {'data': {}, 'fetched_at': 1}
    svc.tdx_fetcher.get_kline_data = MagicMock(side_effect=ConnectionError('TDX down'))
    v = svc._advisor_triple_screen(stock)
    assert _pass(v), v
    assert 'fallback' in v['reason']


# ============ #5 market_regime（默认禁用） ============
def test_advisor_05_market_regime_disabled_by_default():
    # Phase 2 已改为默认启用（2026-07-28 用户要求）
    svc = _make_service(env_overrides={'ENABLE_MARKET_REGIME_ENTRY_FILTER': 'false'})
    stock = {'symbol': '600519'}
    v = svc._advisor_market_regime(stock)
    assert _pass(v), v
    assert 'filter disabled' in v['reason']


def test_advisor_05_market_regime_pass_normal():
    svc = _make_service(env_overrides={'ENABLE_MARKET_REGIME_ENTRY_FILTER': 'true'})
    stock = {'symbol': '600519'}
    svc._tech_indicators_cache['warmup'] = {'data': {}, 'fetched_at': 1}

    import pandas as pd
    import numpy as np
    dates = pd.date_range('2025-01-01', periods=130)
    # 缓慢上涨 → 正常 regime
    close = np.linspace(3000, 3500, 130)
    df = pd.DataFrame({'日期': dates, '收盘': close})
    svc.tdx_fetcher.get_kline_data = MagicMock(return_value=df)

    v = svc._advisor_market_regime(stock)
    assert _pass(v), v


def test_advisor_05_market_regime_suppress_short_crash():
    svc = _make_service(env_overrides={'ENABLE_MARKET_REGIME_ENTRY_FILTER': 'true'})
    stock = {'symbol': '600519'}
    svc._tech_indicators_cache['warmup'] = {'data': {}, 'fetched_at': 1}

    import pandas as pd
    import numpy as np
    dates = pd.date_range('2025-01-01', periods=130)
    # 5 日内暴跌 10%
    close = np.linspace(3500, 3500, 130)
    close[-6:] = [3500, 3450, 3400, 3300, 3200, 3150]  # 5 日 -10%
    df = pd.DataFrame({'日期': dates, '收盘': close})
    svc.tdx_fetcher.get_kline_data = MagicMock(return_value=df)

    v = svc._advisor_market_regime(stock)
    assert _suppress(v), v


def test_advisor_05_market_regime_fallback_on_error():
    svc = _make_service(env_overrides={'ENABLE_MARKET_REGIME_ENTRY_FILTER': 'true'})
    stock = {'symbol': '600519'}
    svc._tech_indicators_cache['warmup'] = {'data': {}, 'fetched_at': 1}
    svc.tdx_fetcher.get_kline_data = MagicMock(side_effect=ConnectionError('TDX down'))
    v = svc._advisor_market_regime(stock)
    assert _pass(v), v
    assert 'fallback' in v['reason']


# ============ 入口 ============
def run_all():
    """收集所有 test_* 函数并跑一遍，汇总结果"""
    import inspect
    tests = [
        (name, fn) for name, fn in globals().items()
        if name.startswith('test_advisor_') and callable(fn)
    ]
    tests.sort()

    passed, failed = 0, 0
    failures = []
    for name, fn in tests:
        try:
            fn()
            passed += 1
            print(f'  ✅ {name}')
        except AssertionError as e:
            failed += 1
            failures.append((name, str(e)))
            print(f'  ❌ {name}: {e}')
        except Exception as e:
            failed += 1
            failures.append((name, f'{type(e).__name__}: {e}'))
            print(f'  💥 {name}: {type(e).__name__}: {e}')

    print(f'\n{"="*60}')
    print(f'汇总: {passed}/{passed+failed} 通过')
    if failures:
        print(f'\n失败用例:')
        for name, err in failures:
            print(f'  - {name}: {err}')
    return failed == 0


if __name__ == '__main__':
    success = run_all()
    sys.exit(0 if success else 1)