"""
股吧情绪数据 fetcher (基于 AKShare 官方接口，无反爬风险)

数据源（全部 AKShare 官方）：
- stock_comment_em                  5000+ 股 关注指数/综合得分/上升  ← 主要
- stock_hot_keyword_em              概念热度排行
- stock_comment_detail_scrd_focus_em  单股 30 日关注指数趋势
- stock_comment_detail_scrd_desire_em 单股 市场参与意愿

设计原则：
- 单次调用拿全市场数据（不逐股爬）
- 写入 SQLite，跨调用缓存
- 计算"单股热度综合分"，喂给 news_flow AI
"""

import os
import sys
import sqlite3
import warnings
from datetime import datetime
from typing import Dict, List, Optional
import logging

warnings.filterwarnings('ignore')
logging.basicConfig(level=logging.INFO, format='[%(asctime)s] %(levelname)s %(message)s')
logger = logging.getLogger('guba_fetcher')

DB_PATH = 'guba_sentiment.db'


def _init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS stock_sentiment (
            trade_date TEXT,
            code TEXT,
            name TEXT,
            price REAL,
            change_pct REAL,
            turnover REAL,
            main_cost REAL,
            institution_pct REAL,
            composite_score REAL,
            rise_rank INTEGER,
            attention_index REAL,
            attention_rank INTEGER,
            updated_at TEXT,
            PRIMARY KEY (trade_date, code)
        )
    ''')
    c.execute('CREATE INDEX IF NOT EXISTS idx_code ON stock_sentiment(code)')
    c.execute('CREATE INDEX IF NOT EXISTS idx_attention ON stock_sentiment(attention_index DESC)')
    c.execute('''
        CREATE TABLE IF NOT EXISTS concept_hot (
            update_time TEXT,
            code TEXT,
            concept TEXT,
            concept_code TEXT,
            hot_score REAL,
            PRIMARY KEY (update_time, code, concept)
        )
    ''')
    conn.commit()
    conn.close()


def _save_stock_sentiment(df):
    import pandas as pd
    today = datetime.now().strftime('%Y-%m-%d')
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    rows = []
    for _, r in df.iterrows():
        rows.append((
            today, str(r['代码']).zfill(6), r['名称'],
            float(r['最新价']) if pd.notna(r['最新价']) else 0,
            float(r['涨跌幅']) if pd.notna(r['涨跌幅']) else 0,
            float(r['换手率']) if pd.notna(r['换手率']) else 0,
            float(r['主力成本']) if pd.notna(r['主力成本']) else 0,
            float(r['机构参与度']) if pd.notna(r['机构参与度']) else 0,
            float(r['综合得分']) if pd.notna(r['综合得分']) else 0,
            int(r['上升']) if pd.notna(r['上升']) else 0,
            float(r['关注指数']) if pd.notna(r['关注指数']) else 0,
            int(r['目前排名']) if pd.notna(r['目前排名']) else 0,
            now
        ))
    c.executemany('''INSERT OR REPLACE INTO stock_sentiment
        (trade_date, code, name, price, change_pct, turnover, main_cost,
         institution_pct, composite_score, rise_rank, attention_index, attention_rank, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''', rows)
    conn.commit()
    conn.close()
    logger.info(f'已保存 {len(rows)} 条 stock_sentiment (date={today})')


def _save_concept_hot(df):
    import pandas as pd
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    rows = []
    for _, r in df.iterrows():
        rows.append((
            now,
            str(r['股票代码']) if pd.notna(r['股票代码']) else '',
            str(r['概念名称']) if pd.notna(r['概念名称']) else '',
            str(r['概念代码']) if pd.notna(r['概念代码']) else '',
            float(r['热度']) if pd.notna(r['热度']) else 0,
        ))
    c.executemany('''INSERT OR REPLACE INTO concept_hot
        (update_time, code, concept, concept_code, hot_score) VALUES (?, ?, ?, ?, ?)''', rows)
    conn.commit()
    conn.close()
    logger.info(f'已保存 {len(rows)} 条 concept_hot')


def fetch_all(force_refresh: bool = False) -> Dict:
    """
    拉取所有股吧情绪数据 (主入口)
    - 5000+ 股 关注指数/综合得分
    - 概念热度排行
    耗时 ~5 秒
    """
    import akshare as ak
    import pandas as pd

    _init_db()

    # 1. 个股综合数据 (5000+ 股)
    today = datetime.now().strftime('%Y-%m-%d')
    conn = sqlite3.connect(DB_PATH)
    cnt = conn.execute('SELECT COUNT(*) FROM stock_sentiment WHERE trade_date=?', (today,)).fetchone()[0]
    conn.close()

    if cnt == 0 or force_refresh:
        logger.info('拉取 stock_comment_em (5000+ 股 关注数据)...')
        df = ak.stock_comment_em()
        _save_stock_sentiment(df)
    else:
        logger.info(f'今天 ({today}) 已有 {cnt} 条股吧数据，跳过拉取')

    # 2. 概念热度
    logger.info('拉取 stock_hot_keyword_em (概念热度)...')
    try:
        df_kw = ak.stock_hot_keyword_em()
        _save_concept_hot(df_kw)
    except Exception as e:
        logger.warning(f'概念热度拉取失败: {e}')

    return {'ok': True, 'date': today, 'count': cnt}


def query_top_stocks(top_n: int = 30, sort_by: str = 'attention_index') -> List[Dict]:
    """
    取关注度 Top N 股
    sort_by: attention_index / composite_score / rise_rank
    """
    _init_db()
    today = datetime.now().strftime('%Y-%m-%d')
    conn = sqlite3.connect(DB_PATH)
    if sort_by == 'rise_rank':
        # 上升数值越大越好 (按降序)
        rows = conn.execute(f'''SELECT code, name, price, change_pct, turnover,
            main_cost, institution_pct, composite_score, rise_rank, attention_index
            FROM stock_sentiment
            WHERE trade_date=?
            ORDER BY rise_rank DESC LIMIT ?''', (today, top_n)).fetchall()
    else:
        rows = conn.execute(f'''SELECT code, name, price, change_pct, turnover,
            main_cost, institution_pct, composite_score, rise_rank, attention_index
            FROM stock_sentiment
            WHERE trade_date=?
            ORDER BY {sort_by} DESC LIMIT ?''', (today, top_n)).fetchall()
    conn.close()
    return [
        {'code': r[0], 'name': r[1], 'price': r[2], 'change_pct': r[3],
         'turnover': r[4], 'main_cost': r[5], 'institution_pct': r[6],
         'composite_score': r[7], 'rise_rank': r[8], 'attention_index': r[9]}
        for r in rows
    ]


def query_top_concepts(top_n: int = 20) -> List[Dict]:
    """取最热概念 (按 hot_score 排序)"""
    _init_db()
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute('''SELECT code, concept, hot_score, MAX(update_time) as t
        FROM concept_hot GROUP BY concept ORDER BY hot_score DESC LIMIT ?''', (top_n,)).fetchall()
    conn.close()
    return [{'code': r[0], 'concept': r[1], 'hot_score': r[2]} for r in rows]


def query_specific_stocks(codes: List[str]) -> List[Dict]:
    """查询特定股票的情绪 (给智瞰龙虎推荐股用)"""
    _init_db()
    today = datetime.now().strftime('%Y-%m-%d')
    conn = sqlite3.connect(DB_PATH)
    placeholders = ','.join('?' * len(codes))
    rows = conn.execute(f'''SELECT code, name, price, change_pct, turnover,
        main_cost, institution_pct, composite_score, rise_rank, attention_index
        FROM stock_sentiment
        WHERE trade_date=? AND code IN ({placeholders})''', (today, *codes)).fetchall()
    conn.close()
    return [
        {'code': r[0], 'name': r[1], 'price': r[2], 'change_pct': r[3],
         'turnover': r[4], 'main_cost': r[5], 'institution_pct': r[6],
         'composite_score': r[7], 'rise_rank': r[8], 'attention_index': r[9]}
        for r in rows
    ]


def format_for_ai_prompt(top_n_attention: int = 20, top_n_concept: int = 15,
                          target_codes: List[str] = None) -> str:
    """
    把股吧数据格式化成 AI 提示词友好的 markdown
    """
    lines = ['## 📊 个股情绪数据 (T2 数据源)']
    lines.append('')

    # 关注度 top N
    top_attention = query_top_stocks(top_n_attention, 'attention_index')
    lines.append(f'### 关注指数 Top {top_n_attention} (散户最关注)')
    lines.append('| 代码 | 名称 | 最新价 | 涨跌幅 | 换手率 | 综合得分 | 上升 | 关注指数 |')
    lines.append('|------|------|--------|--------|--------|----------|------|----------|')
    for s in top_attention:
        chg = f"{s['change_pct']:+.2f}%" if s['change_pct'] is not None else 'N/A'
        lines.append(f"| {s['code']} | {s['name']} | ¥{s['price']:.2f} | {chg} | {s['turnover']:.2f}% | {s['composite_score']:.0f} | {s['rise_rank']} | {s['attention_index']:.0f} |")
    lines.append('')

    # 上升排名 (涨幅/排名上升)
    top_rise = query_top_stocks(10, 'rise_rank')
    lines.append(f'### 排名上升 Top 10 (新进入或跃升) [T2 关键信号]')
    lines.append('| 代码 | 名称 | 最新价 | 涨跌幅 | 综合得分 | 关注指数 | 上升 |')
    lines.append('|------|------|--------|--------|----------|----------|------|')
    for s in top_rise:
        chg = f"{s['change_pct']:+.2f}%" if s['change_pct'] is not None else 'N/A'
        lines.append(f"| {s['code']} | {s['name']} | ¥{s['price']:.2f} | {chg} | {s['composite_score']:.0f} | {s['attention_index']:.0f} | {s['rise_rank']} |")
    lines.append('')

    # 概念热度
    top_concepts = query_top_concepts(top_n_concept)
    lines.append(f'### 热门概念 Top {top_n_concept}')
    lines.append('| 概念 | 热度 | 代表股 |')
    lines.append('|------|------|--------|')
    from stock_keyword_map import find_sectors_by_keyword
    for c in top_concepts:
        # 找这个概念对应的代表股
        sector_stocks = find_sectors_by_keyword(c['concept'])
        rep = ''
        for _, stocks in sector_stocks[:1]:
            if stocks:
                rep = ', '.join(f"{s[0]}{s[1]}" for s in stocks[:3])
                break
        lines.append(f"| {c['concept']} | {c['hot_score']:.0f} | {rep} |")
    lines.append('')

    # 目标股票 (智瞰龙虎推荐股)
    if target_codes:
        target_data = query_specific_stocks(target_codes)
        if target_data:
            lines.append(f'### 智瞰龙虎推荐股 情绪快照 ({len(target_data)} 只)')
            lines.append('| 代码 | 名称 | 最新价 | 涨跌幅 | 综合得分 | 关注指数 |')
            lines.append('|------|------|--------|--------|----------|----------|')
            for s in target_data:
                chg = f"{s['change_pct']:+.2f}%" if s['change_pct'] is not None else 'N/A'
                lines.append(f"| {s['code']} | {s['name']} | ¥{s['price']:.2f} | {chg} | {s['composite_score']:.0f} | {s['attention_index']:.0f} |")
            lines.append('')

    return '\n'.join(lines)


if __name__ == '__main__':
    import sys
    sys.path.insert(0, '.')
    print('=== 拉取所有股吧数据 ===')
    fetch_all()
    print()
    print('=== 测试 format_for_ai_prompt ===')
    md = format_for_ai_prompt(top_n_attention=10, top_n_concept=8,
                              target_codes=['301526', '688146', '002409'])
    print(md)
