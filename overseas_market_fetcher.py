"""
隔夜外盘行情获取模块

盘前拉取美股指数、AI 头部公司、韩股半导体等关键标的的隔夜涨跌幅，
供每日操盘简报的「🌍 隔夜外盘速览」段使用。

数据源: yfinance (Yahoo Finance)
策略: 逐股拉取（加 1s 间隔避免限流），失败时降级返回当前可用数据。

使用方法:
    from overseas_market_fetcher import fetch_overseas_snapshot
    data = fetch_overseas_snapshot()
    # data = {
    #     'indices': [{'name':..., 'close':..., 'change_pct':..., ...}],
    #     'ai_stocks': [...],
    #     'kr_semicon': [...],
    #     'fetch_time': '2026-07-29 08:55',
    #     'errors': [...],
    # }
"""

import time
import logging
from datetime import datetime
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# ============================================================
# 配置：要跟踪的标的
# ============================================================

# 美股三大指数
INDICES = [
    {'symbol': '^DJI',     'name': '道琼斯',  'alias': 'DJIA'},
    {'symbol': '^GSPC',    'name': '标普500',  'alias': 'S&P 500'},
    {'symbol': '^IXIC',    'name': '纳斯达克', 'alias': 'NASDAQ'},
]

# 美股 AI 头部公司（按业务板块分组，直接影响 A 股对应板块情绪）
AI_STOCKS = {
    'AI芯片': [
        {'symbol': 'NVDA',  'name': '英伟达',   'alias': 'NVIDIA'},
        {'symbol': 'AMD',   'name': '超威',      'alias': 'AMD'},
    ],
    '存储芯片': [
        {'symbol': 'MU',    'name': '美光',      'alias': 'Micron'},
    ],
    'AI云平台': [
        {'symbol': 'MSFT',  'name': '微软',      'alias': 'Microsoft'},
        {'symbol': 'GOOGL', 'name': '谷歌',      'alias': 'Alphabet'},
        {'symbol': 'META',  'name': 'Meta',      'alias': 'Meta'},
    ],
    '半导体代工/设备': [
        {'symbol': 'AVGO',  'name': '博通',      'alias': 'Broadcom'},
        {'symbol': 'TSM',   'name': '台积电',    'alias': 'TSMC ADR'},
    ],
}

# 韩股半导体（影响 A 股半导体/存储芯片板块情绪）
KR_SEMICON = [
    {'symbol': '005930.KS', 'name': '三星电子',   'alias': 'Samsung'},
    {'symbol': '000660.KS', 'name': 'SK海力士',   'alias': 'SK Hynix'},
]

# 汇率
CURRENCY = [
    {'symbol': 'USDKRW=X', 'name': '美元/韩元',   'alias': 'USD/KRW'},
]


def _fetch_single(symbol: str, name: str, retries: int = 2) -> Optional[Dict]:
    """
    拉取单只股票/指数的隔夜行情。

    Returns:
        {'symbol': str, 'name': str, 'date': str, 'close': float,
         'open': float, 'high': float, 'low': float, 'change_pct': float,
         'volume': int} 或 None（全部失败时）
    """
    for attempt in range(retries):
        try:
            import yfinance as yf
            ticker = yf.Ticker(symbol)
            hist = ticker.history(period='5d')
            if hist.empty:
                logger.warning(f'{symbol}({name}) 返回空数据')
                return None

            last = hist.iloc[-1]
            prev = hist.iloc[-2] if len(hist) > 1 else last
            prev_close = prev['Close']
            close = last['Close']
            change_pct = round((close - prev_close) / prev_close * 100, 2) if prev_close else 0.0

            return {
                'symbol': symbol,
                'name': name,
                'date': str(last.name.date()),
                'close': round(close, 2),
                'open': round(last['Open'], 2),
                'high': round(last['High'], 2),
                'low': round(last['Low'], 2),
                'change_pct': change_pct,
                'volume': int(last['Volume']) if 'Volume' in hist.columns else 0,
            }
        except Exception as e:
            if attempt < retries - 1:
                time.sleep(2)
                continue
            logger.warning(f'{symbol}({name}) 拉取失败({retries}次): {e}')
            return None


def fetch_overseas_snapshot(
    include_indices: bool = True,
    include_ai_stocks: bool = True,
    include_kr_semicon: bool = True,
    stock_delay: float = 1.0,
) -> Dict:
    """
    拉取完整的隔夜外盘行情快照。

    Args:
        include_indices: 是否拉美股三大指数
        include_ai_stocks: 是否拉美股 AI 头部公司
        include_kr_semicon: 是否拉韩股半导体
        stock_delay: 每只股票间的间隔（秒），默认 1s 防限流

    Returns:
        {
            'indices': [...],       # 美股三大指数
            'ai_stocks': [...],     # 美股 AI 头部公司
            'kr_semicon': [...],    # 韩股半导体
            'currency': [...],      # 汇率
            'fetch_time': str,      # 拉取时间
            'ai_summary': str,      # 由调用方生成，这里留空
            'errors': [...],        # 失败列表
        }
    """
    results = {
        'indices': [],
        'ai_stocks': [],        # 拉取统一存这里
        'kr_semicon': [],
        'currency': [],
        'fetch_time': datetime.now().strftime('%Y-%m-%d %H:%M'),
        'ai_summary': '',
        'errors': [],
    }

    def _batch(items, target_key):
        for item in items:
            r = _fetch_single(item['symbol'], item['name'])
            if r:
                results[target_key].append(r)
            else:
                results['errors'].append(f"{item['name']}({item['symbol']})")
            time.sleep(stock_delay)

    if include_indices:
        _batch(INDICES, 'indices')
    if include_ai_stocks:
        # 展平分组字典为列表进行拉取
        flat_ai = []
        for group_name, group_stocks in AI_STOCKS.items():
            for s in group_stocks:
                flat_ai.append(s)
        _batch(flat_ai, 'ai_stocks')
    if include_kr_semicon:
        _batch(KR_SEMICON, 'kr_semicon')
    _batch(CURRENCY, 'currency')

    return results


def format_overseas_snapshot_md(data: Dict) -> str:
    """
    把 fetch_overseas_snapshot 的结果格式化为 markdown 段，
    嵌入每日操盘简报。

    颜色约定（A 股惯例）：🔴 涨 🟢 跌

    Args:
        data: fetch_overseas_snapshot 的返回值

    Returns:
        markdown 字符串（无内容时返回空字符串）
    """
    indices = data.get('indices', [])
    ai_stocks = data.get('ai_stocks', [])
    kr_semicon = data.get('kr_semicon', [])
    errors = data.get('errors', [])
    fetch_time = data.get('fetch_time', '')

    if not any([indices, ai_stocks, kr_semicon]):
        return ''

    # 把 ai_stocks 展平列表按 AI_STOCKS 分组还原
    def _group_ai():
        """将 ai_stocks 按 AI_STOCKS 的分组归类"""
        by_sym = {s['symbol']: s for s in ai_stocks}
        groups = {}
        for group_name, group_stocks in AI_STOCKS.items():
            items = []
            for gs in group_stocks:
                if gs['symbol'] in by_sym:
                    items.append(by_sym[gs['symbol']])
            if items:
                groups[group_name] = items
        return groups

    lines = []
    lines.append("---")
    lines.append("")
    lines.append("## 🌍 隔夜外盘速览")
    if fetch_time:
        lines.append(f"*数据时间: {fetch_time}（北京时间）*")
    lines.append("")

    # ── 美股三大指数 ──
    if indices:
        lines.append("### 🇺🇸 美股指数")
        for idx in indices:
            chg = idx.get('change_pct', 0)
            emoji = '🔴' if chg >= 0 else '🟢'  # A 股红涨绿跌
            arrow = '↑' if chg >= 0 else '↓'
            lines.append(
                f"  {emoji} **{idx['name']}**  {idx['close']:,}  "
                f"{arrow} {abs(chg):.2f}%"
            )
        lines.append("")

    # ── 美股 AI 头部公司（按业务板块分组）──
    if ai_stocks:
        lines.append("### 🤖 美股 AI 板块")
        groups = _group_ai()
        for group_name, group_items in groups.items():
            lines.append(f"  **{group_name}**")
            for s in group_items:
                chg = s.get('change_pct', 0)
                emoji = '🔴' if chg >= 0 else '🟢'
                arrow = '↑' if chg >= 0 else '↓'
                lines.append(
                    f"    {emoji} {s['name']}  {s['close']:,.2f}  "
                    f"{arrow} {abs(chg):.2f}%"
                )
        lines.append("")

    # ── 韩股半导体 ──
    if kr_semicon:
        lines.append("### 🇰🇷 韩股半导体")
        for s in kr_semicon:
            chg = s.get('change_pct', 0)
            emoji = '🔴' if chg >= 0 else '🟢'
            arrow = '↑' if chg >= 0 else '↓'
            lines.append(
                f"  {emoji} **{s['name']}** {s['close']:,.0f}  "
                f"{arrow} {abs(chg):.2f}%"
            )
        lines.append("")

    # ── 涨跌统计 ──
    all_items = indices + ai_stocks + kr_semicon
    up = sum(1 for x in all_items if x.get('change_pct', 0) > 0)
    down = sum(1 for x in all_items if x.get('change_pct', 0) < 0)
    total = up + down
    if total > 0:
        bar_len = 10
        up_len = round(up / total * bar_len)
        down_len = bar_len - up_len
        bar = '🔴' * up_len + '🟢' * down_len  # 红涨绿跌
        lines.append(f"> **市场情绪**: {bar}  (涨 {up}/{total}  跌 {down}/{total})")
        lines.append("")

    # ── 错误提示（降级信息）──
    if errors:
        lines.append(f"> ⚠️ 部分数据获取失败: {', '.join(errors[:3])}")
        if len(errors) > 3:
            lines.append(f">   ...还有 {len(errors)-3} 个")
        lines.append("")

    return "\n".join(lines)


# ============================================================
# 测试
# ============================================================
if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO, format='%(message)s')

    print("=" * 60)
    print("🌍 隔夜外盘行情获取测试")
    print("=" * 60)

    data = fetch_overseas_snapshot(stock_delay=1.2)
    md = format_overseas_snapshot_md(data)
    print()
    print(md)

    err = data.get('errors', [])
    if err:
        print(f"\n⚠️ {len(err)} 个失败: {', '.join(err)}")
    else:
        print("\n✅ 全部成功")
