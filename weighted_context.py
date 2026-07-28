"""
加权数据上下文生成器
为 news_flow AI agents 提供 4 层加权数据 (T1-T4)

数据流：
  T1 (30%): 主流媒体 + 短视频评论  ← 现有 18 平台 hot_topics
  T2 (30%): 股吧情绪              ← guba_fetcher 写入 guba_sentiment.db
  T3 (25%): 跨平台共识            ← 现有 18 平台 flow_data
  T4 (15%): 板块-个股映射         ← stock_keyword_map

输出：markdown 字符串，可直接拼接到任何 AI prompt 后面
"""

import os
import sys
import sqlite3
from datetime import datetime
from typing import List, Dict, Optional

# 兼容直接 import 和包内 import
try:
    from guba_fetcher import (
        query_top_stocks, query_top_concepts, query_specific_stocks
    )
    from stock_keyword_map import find_sectors_by_keyword
except ImportError:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from guba_fetcher import (
        query_top_stocks, query_top_concepts, query_specific_stocks
    )
    from stock_keyword_map import find_sectors_by_keyword


def get_weighted_context(top_n_attention: int = 15,
                         top_n_rise: int = 8,
                         top_n_concept: int = 10,
                         target_codes: List[str] = None) -> str:
    """
    生成 4 层加权数据上下文 (T1-T4)，返回 markdown

    任何一步失败都降级为最简化版本（只有 T3 跨平台共识），
    不让数据获取问题影响主流程。

    ⚠️ 重要数据时间戳说明：
    - "涨跌幅" / "最新价" = **上一交易日**的收盘数据（来自 AKShare stock_comment_em）
    - "上升名次" = **上一交易日**到当天的累计变化
    - 这是"今天开盘前的市场情绪"快照，**不是今天实时的盘中数据**
    - 用作"今日操盘计划"参考，不是"事后验证"
    """
    # 数据时间戳 (从 DB 读)
    from guba_fetcher import DB_PATH as _GUBA_DB
    data_date = "未知"
    try:
        conn = sqlite3.connect(_GUBA_DB)
        row = conn.execute('SELECT MAX(trade_date) FROM stock_sentiment').fetchone()
        conn.close()
        if row and row[0]:
            data_date = row[0]
    except Exception:
        pass

    lines = []
    lines.append('## 📊 加权数据上下文 (4 层)')
    lines.append('')
    lines.append(f'**⏰ 数据时间戳**: T2/T3 数据来自 **{data_date}** (最近一个交易日的收盘)')
    lines.append('**权重说明**: T1 主流媒体 30% / T2 股吧情绪 30% / T3 跨平台共识 25% / T4 板块-个股映射 15%')
    lines.append('**⚠️ 数据用途**: 用于判断"今日操盘"的市场情绪基准, **非事后验证**')
    lines.append('')

    # ===== T2: 股吧情绪 (新加，最有价值) =====
    try:
        # 关注度 Top N
        top_attention = query_top_stocks(top_n_attention, 'attention_index')
        if top_attention:
            lines.append(f'### [T2] 个股关注度 Top {top_n_attention} (散户情绪指标, 截至 {data_date} 收盘)')
            lines.append('| 代码 | 名称 | 最新价 | **昨日**涨跌幅 | 换手率 | 综合得分 | 关注指数 | 关注排名 |')
            lines.append('|------|------|--------|--------|--------|----------|----------|----------|')
            for s in top_attention:
                chg = f"{s['change_pct']:+.2f}%" if s.get('change_pct') is not None else 'N/A'
                lines.append(f"| {s['code']} | {s['name']} | ¥{s['price']:.2f} | {chg} | {s.get('turnover', 0):.2f}% | {s.get('composite_score', 0):.0f} | {s.get('attention_index', 0):.0f} | {s.get('attention_rank', '-')} |")
            lines.append('')

        # 排名上升 Top N (关键信号：新进入热点)
        top_rise = query_top_stocks(top_n_rise, 'rise_rank')
        if top_rise:
            lines.append(f'### [T2] 排名上升 Top {top_n_rise} (新进入或跃升信号, 截至 {data_date})')
            lines.append('| 代码 | 名称 | **昨日**涨跌幅 | 关注指数 | 上升名次 | 综合得分 |')
            lines.append('|------|------|--------|----------|----------|----------|')
            for s in top_rise:
                chg = f"{s['change_pct']:+.2f}%" if s.get('change_pct') is not None else 'N/A'
                rise = s.get('rise_rank', 0)
                lines.append(f"| {s['code']} | {s['name']} | {chg} | {s.get('attention_index', 0):.0f} | **+{rise}** | {s.get('composite_score', 0):.0f} |")
            lines.append('')

        # 热门概念
        top_concepts = query_top_concepts(top_n_concept)
        if top_concepts:
            lines.append(f'### [T3+T4] 热门概念 Top {top_n_concept} (跨平台 + 板块映射, 截至 {data_date})')
            lines.append('| 概念 | 热度 | 映射板块 | 代表股 (T4) |')
            lines.append('|------|------|----------|---------------|')
            for c in top_concepts:
                sector_stocks = find_sectors_by_keyword(c['concept'])
                sector_name = ''
                rep = ''
                for sn, stocks in sector_stocks[:1]:
                    sector_name = sn
                    if stocks:
                        rep = ', '.join(f"{s[0]}{s[1]}" for s in stocks[:4])
                        break
                lines.append(f"| {c['concept']} | {c.get('hot_score', 0):.0f} | {sector_name} | {rep} |")
            lines.append('')

        # 智瞰龙虎推荐股 → 当前市场情绪快照 (注意: 数据是昨日收盘的, 用于今日开盘前参考)
        if target_codes:
            target_data = query_specific_stocks(target_codes)
            if target_data:
                lines.append(f'### [T2 信号] 智瞰龙虎推荐股 市场情绪快照 ({len(target_data)} 只, 截至 {data_date} 收盘)')
                lines.append('> ⚠️ 这是昨日收盘快照, 反映推荐股"进入今天开盘前"的市场热度, **不构成对今天预测的事后验证**')
                lines.append('')
                lines.append('| 代码 | 名称 | 最新价 | **昨日**涨跌幅 | 综合得分 | 关注指数 | 上升名次 |')
                lines.append('|------|------|--------|--------|----------|----------|----------|')
                for s in target_data:
                    chg = f"{s['change_pct']:+.2f}%" if s.get('change_pct') is not None else 'N/A'
                    lines.append(f"| {s['code']} | {s['name']} | ¥{s['price']:.2f} | {chg} | {s.get('composite_score', 0):.0f} | {s.get('attention_index', 0):.0f} | +{s.get('rise_rank', 0)} |")
                lines.append('')

    except Exception as e:
        lines.append(f'> ⚠️ T2 股吧数据获取失败: {str(e)[:100]}')
        lines.append('')

    lines.append('---')
    lines.append('')
    lines.append('**🧠 解读指引 (重要, 时间维度提示)**：')
    lines.append('- **关注指数 95+** = 散户最关注 (基于 {t} 数据), 真实情绪指标'.format(t=data_date))
    lines.append('- **上升名次 3000+** = 该股关注度排名短期大幅跃升, 重点信号')
    lines.append('- **综合得分 80+** = 机构+散户一致看好')
    lines.append('- **关注指数高 + 上升名次大** = 流量高潮预兆 (可能见顶)')
    lines.append('- **关注指数高 + 上升名次为负** = 热度高但价格已跌, 逃命信号')
    lines.append('')
    lines.append('**关于"智瞰龙虎推荐股"**:')
    lines.append('- 这只是"今天开盘前"的市场情绪快照, 不是"事后验证"')
    lines.append('- 真正的事后验证需要等今天 (T+1) 收盘后, 拿今天真实收盘价 vs 昨日收盘价')
    lines.append('- 项目里目前**没有**自动的事后验证机制 — 后续可以加 (见 TODO)')

    return '\n'.join(lines)


# 自测
if __name__ == '__main__':
    sys.path.insert(0, '.')
    print('=== 4 层加权数据上下文 ===')
    md = get_weighted_context(
        top_n_attention=10,
        top_n_rise=5,
        top_n_concept=6,
        target_codes=['301526', '688146', '002409']  # 智瞰龙虎推荐股
    )
    print(md)
