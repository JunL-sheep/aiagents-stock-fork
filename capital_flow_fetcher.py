"""
资金面 fetcher (T5 数据源)
从 longhubang.db 龙虎榜数据计算资金面信号

数据流：
  longhubang_records (1989 条) → 聚合
    - 最新龙虎榜 Top N (按净流入)
    - 7日累计净流入 (多次上榜 = 主力共识)
    - 特定股票的资金面 (智瞰龙虎推荐股验证)

输出：结构化 dict，可直接拼接到 weighted_context.py
"""

import os
import sys
import sqlite3
import logging
from datetime import datetime, timedelta
from typing import List, Dict, Optional

logging.basicConfig(level=logging.INFO, format='[%(asctime)s] %(levelname)s %(message)s')
logger = logging.getLogger('capital_flow_fetcher')

LONGHUBANG_DB = 'longhubang.db'


def _connect():
    """连接 longhubang.db (只读)"""
    return sqlite3.connect(f'file:{os.path.abspath(LONGHUBANG_DB)}?mode=ro', uri=True)


def query_latest_top_inflow(top_n: int = 10) -> List[Dict]:
    """
    最新龙虎榜净流入 Top N (longhubang_records 里最新的交易日)
    返回: [{'code', 'name', 'net_inflow', 'buy_amount', 'sell_amount', 'n_records', 'list_types'}, ...]
    """
    try:
        conn = _connect()
        c = conn.cursor()
        # 取最新一天的所有数据
        rows = c.execute('''
            SELECT date, stock_code, stock_name, youzi_name, yingye_bu, list_type,
                   buy_amount, sell_amount, net_inflow
            FROM longhubang_records
            WHERE date = (SELECT MAX(date) FROM longhubang_records)
            ORDER BY net_inflow DESC
        ''').fetchall()
        conn.close()

        if not rows:
            return []

        # 按股票聚合 (一只股可能多个营业部记录)
        # SELECT 列顺序: date, stock_code, stock_name, youzi_name, yingye_bu, list_type, buy_amount, sell_amount, net_inflow
        # 索引:        0      1          2           3            4          5         6           7            8
        agg = {}
        for r in rows:
            code, name = r[1], r[2]
            if code not in agg:
                agg[code] = {
                    'date': r[0],
                    'code': code,
                    'name': name,
                    'net_inflow': 0,
                    'buy_amount': 0,
                    'sell_amount': 0,
                    'n_records': 0,
                    'list_types': set(),
                    'top_youzi': [],
                }
            agg[code]['net_inflow'] += (r[8] or 0)   # net_inflow
            agg[code]['buy_amount'] += (r[6] or 0)    # buy_amount
            agg[code]['sell_amount'] += (r[7] or 0)   # sell_amount
            agg[code]['n_records'] += 1
            agg[code]['list_types'].add(r[5])  # list_type
            if r[4]:  # yingye_bu
                agg[code]['top_youzi'].append({
                    'youzi': r[3], 'yingye_bu': r[4],
                    'buy': r[6], 'net': r[8],
                })

        # 按净流入排序
        result = []
        for code, info in sorted(agg.items(), key=lambda x: -x[1]['net_inflow'])[:top_n]:
            # 保留 top 3 营业部 (按净买额)
            top3 = sorted(info['top_youzi'], key=lambda x: -x['net'])[:3]
            result.append({
                'code': code,
                'name': info['name'],
                'date': info['date'],
                'net_inflow': info['net_inflow'],
                'buy_amount': info['buy_amount'],
                'sell_amount': info['sell_amount'],
                'n_records': info['n_records'],
                'list_types': list(info['list_types']),
                'top_youzi': top3,
            })
        return result
    except Exception as e:
        logger.warning(f'query_latest_top_inflow 失败: {e}')
        return []


def query_week_top_inflow(top_n: int = 10, min_days: int = 2) -> List[Dict]:
    """
    7 日累计净流入 Top N (多次上榜 = 主力共识)
    只返回多次上榜的股 (min_days >= 2)
    """
    try:
        conn = _connect()
        c = conn.cursor()
        rows = c.execute('''
            SELECT stock_code, stock_name,
                   SUM(net_inflow) as total_net,
                   SUM(buy_amount) as total_buy,
                   SUM(sell_amount) as total_sell,
                   COUNT(DISTINCT date) as n_days,
                   COUNT(*) as n_records,
                   MAX(date) as latest_date
            FROM longhubang_records
            WHERE date >= date('now', '-7 days')
            GROUP BY stock_code, stock_name
            HAVING n_days >= ?
            ORDER BY total_net DESC
            LIMIT ?
        ''', (min_days, top_n)).fetchall()
        conn.close()
        return [
            {
                'code': r[0], 'name': r[1],
                'total_net': r[2], 'total_buy': r[3], 'total_sell': r[4],
                'n_days': r[5], 'n_records': r[6], 'latest_date': r[7],
            }
            for r in rows
        ]
    except Exception as e:
        logger.warning(f'query_week_top_inflow 失败: {e}')
        return []


def query_specific_codes(codes: List[str]) -> List[Dict]:
    """
    查询特定股票的资金面 (智瞰龙虎推荐股用)
    返回每只股:
      - 最新龙虎榜状态 (最近交易日)
      - 7日累计净流入
      - 上榜天数
    """
    if not codes:
        return []
    try:
        conn = _connect()
        c = conn.cursor()
        placeholders = ','.join('?' * len(codes))
        # 最近交易日数据
        today_rows = c.execute(f'''
            SELECT (SELECT MAX(date) FROM longhubang_records) as trade_date,
                   stock_code, stock_name, SUM(net_inflow) as today_net,
                   SUM(buy_amount) as today_buy, COUNT(*) as today_records,
                   GROUP_CONCAT(DISTINCT list_type) as list_types
            FROM longhubang_records
            WHERE date=(SELECT MAX(date) FROM longhubang_records) AND stock_code IN ({placeholders})
            GROUP BY stock_code, stock_name
        ''', codes).fetchall()
        today_map = {r[1]: r for r in today_rows}  # r[1]=stock_code

        # 7日累计
        week_rows = c.execute(f'''
            SELECT stock_code, SUM(net_inflow) as week_net,
                   COUNT(DISTINCT date) as n_days, COUNT(*) as n_records
            FROM longhubang_records
            WHERE date >= date('now', '-7 days') AND stock_code IN ({placeholders})
            GROUP BY stock_code
        ''', codes).fetchall()
        week_map = {r[0]: r for r in week_rows}
        conn.close()

        result = []
        for code in codes:
            t = today_map.get(code)
            w = week_map.get(code)
            result.append({
                'code': code,
                'latest_date': t[0] if t else '',
                'today_in_lhb': t is not None,
                'today_net': t[3] if t else 0,
                'today_records': t[5] if t else 0,
                'today_list_types': (t[6] or '').split(',') if t and t[6] else [],
                'week_net': w[1] if w else 0,
                'week_days': w[2] if w else 0,
                'week_records': w[3] if w else 0,
            })
        return result
    except Exception as e:
        logger.warning(f'query_specific_codes 失败: {e}')
        return []


# 自测
if __name__ == '__main__':
    print('=== 1. 最新龙虎榜 Top 10 (最近交易日) ===')
    for s in query_latest_top_inflow(10):
        print(f"  {s['date']} {s['code']} {s['name']:8s} 净 {s['net_inflow']/1e8:+.2f}亿 买 {s['buy_amount']/1e8:.2f}亿  ({s['n_records']}次)")
        if s['top_youzi']:
            print(f'    TOP 营业部: {s["top_youzi"][0]["yingye_bu"]} ({s["top_youzi"][0]["net"]/1e8:+.2f}亿)')

    print()
    print('=== 2. 7日累计 Top 10 (多次上榜) ===')
    for s in query_week_top_inflow(10, min_days=2):
        print(f"  {s['code']} {s['name']:8s} 净 {s['total_net']/1e8:+.2f}亿 ({s['n_days']}天上榜)")

    print()
    print('=== 3. 智瞑龙虎 7-27 报告 10 只股的资金面 ===')
    codes = ['301526', '688146', '002484', '601678', '002407', '002141',
             '603567', '002409', '002173', '300534', '000636']
    for s in query_specific_codes(codes):
        mark = '🟢' if s['today_in_lhb'] and s['today_net'] > 0 else '⚪'
        date_label = s['latest_date'] if s['today_in_lhb'] else '(未上榜)'
        print(f"  {mark} {s['code']} {date_label} {'净 '+str(round(s['today_net']/1e8, 2))+'亿' if s['today_in_lhb'] else '未上榜'} | 7日净 {s['week_net']/1e8:+.2f}亿 ({s['week_days']}天上榜)")
