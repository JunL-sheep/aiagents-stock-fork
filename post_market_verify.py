"""
盘后验证脚本
每个交易日 15:30 跑一次，验证智瞰龙虎推荐股当天的真实表现

数据流：
  1. 从 longhubang.db 取最新报告的 10 只推荐股
  2. 从 stock_tracking 取当时的 recommended_price / target_price / stop_loss_price
  3. TDX 拉今天的 OHLC (open/high/low/close) + 昨收
  4. 计算 actual_return / hit_take_profit / hit_stop_loss
  5. 写回 stock_tracking 表 (status + profit_loss_pct + notes)
  6. 推钉钉简报 (今日表现 / 一周累计)

执行：python post_market_verify.py
建议定时：每个交易日 15:30 (A 股 15:00 收盘后 30 分钟)
"""
import os
import sys
import json
import sqlite3
import logging
from datetime import datetime, timedelta

import requests
from dotenv import load_dotenv

logging.basicConfig(level=logging.INFO, format='[%(asctime)s] %(levelname)s %(message)s')
logger = logging.getLogger('post_market_verify')

# 切到项目目录
os.chdir(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.getcwd())


def load_config():
    load_dotenv('.env')
    return {
        'webhook_url': os.getenv('WEBHOOK_URL', ''),
        'webhook_enabled': os.getenv('WEBHOOK_ENABLED', 'false').lower() == 'true',
        'webhook_keyword': os.getenv('WEBHOOK_KEYWORD', '股票'),
        'tdx_url': os.getenv('TDX_BASE_URL', 'http://localhost:9999'),
    }


def get_today_recommendations():
    """从 longhubang.db 取最新报告的 10 只推荐股"""
    conn = sqlite3.connect('longhubang.db')
    row = conn.execute('''
        SELECT id, analysis_date, recommended_stocks
        FROM longhubang_analysis ORDER BY id DESC LIMIT 1
    ''').fetchone()
    conn.close()
    if not row or not row[2]:
        return None, []
    return row[0], json.loads(row[2])


def get_tracking_info(analysis_id, code):
    """从 stock_tracking 取目标价/止损价/推荐价"""
    conn = sqlite3.connect('longhubang.db')
    row = conn.execute('''
        SELECT recommended_price, target_price, stop_loss_price
        FROM stock_tracking
        WHERE analysis_id=? AND stock_code=?
    ''', (analysis_id, code)).fetchone()
    conn.close()
    if not row:
        return None
    return {
        'recommended_price': row[0],
        'target_price': row[1],
        'stop_loss_price': row[2],
    }


def fetch_ohlc_via_tdx(code, tdx_url, retries=2):
    """通过 TDX 拉单股的今开/最高/最低/今收 + 昨收"""
    for _ in range(retries):
        try:
            r = requests.get(f'{tdx_url}/api/quote', params={'code': code}, timeout=8)
            data = r.json()
            if data.get('code') == 0 and data.get('data'):
                k = data['data'][0]['K']
                return {
                    'open': k['Open'] / 1000,
                    'high': k['High'] / 1000,
                    'low': k['Low'] / 1000,
                    'close': k['Close'] / 1000,
                    'last_close': k['Last'] / 1000,  # 昨收
                    'volume': data['data'][0].get('TotalHand', 0),  # 手
                }
        except Exception as e:
            logger.warning(f'  TDX {code} 失败: {e}, 重试...')
    return None


def compute_metrics(ohlc, tracking):
    """
    计算单只股的实际表现 vs 推荐时设定的阈值
    """
    if not ohlc or not tracking:
        return None

    rec_price = tracking.get('recommended_price') or 0
    target = tracking.get('target_price') or 0
    stop_loss = tracking.get('stop_loss_price') or 0
    last = ohlc['last_close']
    close = ohlc['close']
    high = ohlc['high']
    low = ohlc['low']
    today_open = ohlc['open']

    if last <= 0 or rec_price <= 0:
        return None

    # 推荐时设定的进场区间 (rec_price ± 3%) - 用来判断"今天开盘是否在区间内"
    entry_low = rec_price * 0.97
    entry_high = rec_price * 1.03

    # 实际表现 (基于昨收 - 真实意义上"今天涨了多少")
    actual_return = (close - last) / last
    # 盘中最大涨幅 (昨日收盘 → 今日最高)
    max_gain = (high - last) / last if high > last else 0
    # 盘中最大回撤 (昨日收盘 → 今日最低)
    max_drawdown = (low - last) / last if low < last else 0
    # 相对推荐价 (推荐时到现在)
    vs_recommend = (close - rec_price) / rec_price

    # 命中判断
    in_entry_range = entry_low <= today_open <= entry_high
    hit_take_profit = high >= target if target > 0 else False
    hit_stop_loss = low <= stop_loss if stop_loss > 0 else False
    reached_8pct = max_gain >= 0.08  # 至少涨 8%
    fell_5pct = max_drawdown <= -0.05  # 至少跌 5%

    # 状态
    if hit_take_profit:
        status = 'hit_take_profit'
    elif hit_stop_loss:
        status = 'hit_stop_loss'
    elif actual_return > 0.02:
        status = 'profit'  # 收涨 > 2%
    elif actual_return < -0.02:
        status = 'loss'  # 收跌 > 2%
    else:
        status = 'flat'

    return {
        'today_open': today_open,
        'today_high': ohlc['high'],
        'today_low': ohlc['low'],
        'today_close': ohlc['close'],
        'today_volume': ohlc['volume'],
        'last_close': last,
        'actual_return': actual_return,        # 今日真实涨跌幅
        'max_gain': max_gain,                  # 盘中最大涨幅
        'max_drawdown': max_drawdown,          # 盘中最大回撤
        'vs_recommend': vs_recommend,          # 相对推荐价的变化
        'in_entry_range': in_entry_range,      # 今天开盘是否在进场区间
        'hit_take_profit': hit_take_profit,    # 盘中是否触达止盈
        'hit_stop_loss': hit_stop_loss,        # 盘中是否触达止损
        'reached_8pct': reached_8pct,          # 至少涨 8%
        'fell_5pct': fell_5pct,                # 至少跌 5%
        'status': status,
    }


def update_tracking(analysis_id, code, metrics):
    """写回 stock_tracking 表"""
    conn = sqlite3.connect('longhubang.db')
    notes = (f"今日 {metrics['today_open']:.2f}→{metrics['today_high']:.2f}→"
             f"{metrics['today_low']:.2f}→{metrics['today_close']:.2f}  "
             f"实际涨跌 {metrics['actual_return']:+.2%}  "
             f"盘中最高 {metrics['max_gain']:+.2%}  "
             f"盘中最低 {metrics['max_drawdown']:+.2%}  "
             f"vs推荐 {metrics['vs_recommend']:+.2%}  "
             f"进场命中:{'是' if metrics['in_entry_range'] else '否'}  "
             f"止盈:{'是' if metrics['hit_take_profit'] else '否'}  "
             f"止损:{'是' if metrics['hit_stop_loss'] else '否'}")
    conn.execute('''
        UPDATE stock_tracking
        SET current_price=?, profit_loss_pct=?, status=?, notes=?, updated_at=CURRENT_TIMESTAMP
        WHERE analysis_id=? AND stock_code=?
    ''', (
        metrics['today_close'],
        metrics['vs_recommend'] * 100,  # 存为百分比数字
        metrics['status'],
        notes,
        analysis_id, code
    ))
    conn.commit()
    conn.close()


def build_summary(analysis_id, results):
    """生成钉钉 markdown 简报"""
    if not results:
        return None, {}

    n = len(results)
    n_profit = sum(1 for r in results if r['metrics']['actual_return'] > 0)
    n_loss = sum(1 for r in results if r['metrics']['actual_return'] < 0)
    n_hit_tp = sum(1 for r in results if r['metrics']['hit_take_profit'])
    n_hit_sl = sum(1 for r in results if r['metrics']['hit_stop_loss'])
    n_in_range = sum(1 for r in results if r['metrics']['in_entry_range'])
    avg_return = sum(r['metrics']['actual_return'] for r in results) / n
    avg_max_gain = sum(r['metrics']['max_gain'] for r in results) / n
    avg_drawdown = sum(r['metrics']['max_drawdown'] for r in results) / n

    # 涨跌家数
    n_5pct_up = sum(1 for r in results if r['metrics']['actual_return'] >= 0.05)
    n_3pct_up = sum(1 for r in results if 0.03 <= r['metrics']['actual_return'] < 0.05)
    n_flat = sum(1 for r in results if -0.03 < r['metrics']['actual_return'] < 0.03)
    n_3pct_dn = sum(1 for r in results if -0.05 < r['metrics']['actual_return'] <= -0.03)
    n_5pct_dn = sum(1 for r in results if r['metrics']['actual_return'] <= -0.05)

    today = datetime.now().strftime('%Y-%m-%d')
    keyword = os.getenv('WEBHOOK_KEYWORD', '股票')

    lines = []
    lines.append(f"### {keyword} - 盘后验证 ({today})")
    lines.append("")
    lines.append(f"**验证样本**: 智瞰龙虎最新报告 (id={analysis_id}) 推荐的 {n} 只股")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## 📊 总体表现")
    lines.append("")
    lines.append(f"| 指标 | 数值 |")
    lines.append(f"|------|------|")
    lines.append(f"| 平均今日涨跌幅 | **{avg_return:+.2%}** |")
    lines.append(f"| 平均盘中最高涨幅 | {avg_max_gain:+.2%} |")
    lines.append(f"| 平均盘中最大回撤 | {avg_drawdown:+.2%} |")
    lines.append(f"| 上涨家数 | {n_profit}/{n} |")
    lines.append(f"| 下跌家数 | {n_loss}/{n} |")
    lines.append(f"| 开盘在进场区间 | {n_in_range}/{n} |")
    lines.append(f"| 盘中触达止盈 | **{n_hit_tp}/{n}** |")
    lines.append(f"| 盘中触达止损 | **{n_hit_sl}/{n}** |")
    lines.append("")
    lines.append("**涨跌幅分布**:")
    lines.append(f"- 涨 ≥5%: {n_5pct_up} | 涨 3-5%: {n_3pct_up} | 平: {n_flat} | 跌 3-5%: {n_3pct_dn} | 跌 ≥5%: {n_5pct_dn}")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## 📋 个股明细 (按涨跌幅排序)")
    lines.append("")
    lines.append("| 代码 | 名称 | 昨收 | 今开 | 今高 | 今低 | 今收 | 涨跌幅 | 触达 | 状态 |")
    lines.append("|------|------|------|------|------|------|------|--------|------|------|")
    # 按涨跌幅降序
    for r in sorted(results, key=lambda x: -x['metrics']['actual_return']):
        m = r['metrics']
        tag = []
        if m['hit_take_profit']: tag.append('止盈')
        if m['hit_stop_loss']: tag.append('止损')
        if m['in_entry_range']: tag.append('进场')
        tag_str = ' '.join(tag) if tag else '-'
        status_emoji = {
            'hit_take_profit': '🎯', 'hit_stop_loss': '🛑',
            'profit': '🟢', 'loss': '🔴', 'flat': '⚪'
        }.get(m['status'], '⚪')
        lines.append(f"| {r['code']} | {r['name']} | {m['last_close']:.2f} | {m['today_open']:.2f} | "
                     f"{m['today_high']:.2f} | {m['today_low']:.2f} | {m['today_close']:.2f} | "
                     f"**{m['actual_return']:+.2%}** | {tag_str} | {status_emoji} {m['status']} |")
    lines.append("")

    return '\n'.join(lines), {
        'total': n, 'profit': n_profit, 'loss': n_loss,
        'hit_tp': n_hit_tp, 'hit_sl': n_hit_sl, 'in_range': n_in_range,
        'avg_return': avg_return, 'avg_max_gain': avg_max_gain, 'avg_drawdown': avg_drawdown,
    }


def send_dingtalk(webhook_url, keyword, content):
    """推钉钉"""
    if not webhook_url:
        return False, "未配置 webhook"
    data = {
        'msgtype': 'markdown',
        'markdown': {
            'title': f"{keyword} - 盘后验证",
            'text': content,
        }
    }
    r = requests.post(webhook_url, json=data,
                      headers={'Content-Type': 'application/json'}, timeout=10)
    if r.status_code == 200:
        result = r.json()
        if result.get('errcode') == 0:
            return True, "推送成功"
        return False, f"钉钉: {result.get('errmsg')}"
    return False, f"HTTP {r.status_code}"


def main():
    print('=' * 60)
    print('盘后验证脚本')
    print('=' * 60)

    # ===== 护栏: 必须在 A 股收盘后 (15:00) 才能跑 =====
    now = datetime.now()
    if now.hour < 15 or (now.hour == 15 and now.minute < 5):
        print(f'⏸️  当前时间 {now.strftime("%H:%M")}, A 股 15:00 收盘，禁止运行')
        print('   这个脚本必须等收盘后才能跑 (拿真实收盘价)')
        return 1

    # ===== 护栏: 周末不跑 (周六周日不需要事后验证) =====
    if now.weekday() >= 5:  # 5=周六, 6=周日
        print(f'⏸️  今天是周末 ({now.strftime("%A")})，不需要盘后验证')
        return 1


    cfg = load_config()
    if not cfg['tdx_url']:
        print('❌ TDX_BASE_URL 未配置')
        return 1
    if not cfg['webhook_enabled']:
        print('⚠️  WEBHOOK 未启用，将只写库不推送')

    # 1) 取最新报告
    analysis_id, recommendations = get_today_recommendations()
    if not analysis_id:
        print('❌ longhubang.db 里没有报告')
        return 1
    recommendations = [r for r in recommendations if r.get('code')]
    if not recommendations:
        print('❌ 推荐股列表为空')
        return 1
    print(f'📄 报告 #{analysis_id}, {len(recommendations)} 只推荐股')

    # 2) 逐只拉 TDX 行情 + 写库
    results = []
    print()
    for rec in recommendations:
        code = rec['code']
        name = rec.get('name', code)
        net_inflow = rec.get('net_inflow', 0) / 1e8
        print(f'  拉 {code} {name} ({net_inflow:.2f}亿)...', end=' ')

        # 拿目标价/止损价
        tracking = get_tracking_info(analysis_id, code)
        if not tracking:
            print('⚠️  无 stock_tracking 记录, 跳过')
            continue

        # TDX 行情
        ohlc = fetch_ohlc_via_tdx(code, cfg['tdx_url'])
        if not ohlc:
            print('❌ TDX 拉价失败')
            continue

        # 计算
        metrics = compute_metrics(ohlc, tracking)
        if not metrics:
            print('❌ 计算失败')
            continue

        # 写库
        update_tracking(analysis_id, code, metrics)

        results.append({'code': code, 'name': name, 'metrics': metrics})
        emoji = '🎯' if metrics['hit_take_profit'] else ('🛑' if metrics['hit_stop_loss'] else '')
        print(f'今收 {metrics["today_close"]:.2f} 涨跌 {metrics["actual_return"]:+.2%} {emoji}')

    if not results:
        print('❌ 无有效结果')
        return 1

    # 3) 拼简报
    print()
    print('=' * 60)
    print('生成简报...')
    content, stats = build_summary(analysis_id, results)
    print()
    print(content)
    print('=' * 60)
    print()
    print(f'统计: {stats}')

    # 4) 推钉钉
    if cfg['webhook_enabled'] and cfg['webhook_url']:
        print('推送钉钉...')
        ok, msg = send_dingtalk(cfg['webhook_url'], cfg['webhook_keyword'], content)
        print(f'  {"✅" if ok else "❌"} {msg}')

    return 0


if __name__ == '__main__':
    sys.exit(main())
