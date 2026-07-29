"""
每日操盘建议推送
- 读取 longhubang.db 里最新一份智瞰龙虎报告
- 拼成精炼的操盘简报（含次日重点推荐 + AI 策略要点）
- 从 news_flow.db 拉板块级 AI 分析（流量、情绪、风险等级）
- 通过钉钉 Webhook 推送
- 推送成功后，把 10 只重点股注入 monitor_db 监测池（覆盖式 + 7 天 TTL + 20 只上限）

运行方式：
    python 每日操盘建议.py                # 推送 + 注入监测池
    python 每日操盘建议.py --dry-run      # 只打印不推送，不注入
    python 每日操盘建议.py --skip-monitor # 只推送，不注入监测池

建议定时：每个交易日 09:00-09:05
"""

import os
import sys
import json
import sqlite3
import argparse
from datetime import datetime
from dotenv import load_dotenv
import requests


def load_config():
    """加载 .env 配置"""
    load_dotenv('.env')
    return {
        'webhook_url': os.getenv('WEBHOOK_URL', ''),
        'webhook_enabled': os.getenv('WEBHOOK_ENABLED', 'false').lower() == 'true',
        'webhook_keyword': os.getenv('WEBHOOK_KEYWORD', '股票'),
        'tdx_url': os.getenv('TDX_BASE_URL', 'http://localhost:9999'),
    }


def get_latest_report():
    """从 longhubang.db 取最新一份报告"""
    conn = sqlite3.connect('longhubang.db')
    row = conn.execute('''
        SELECT id, analysis_date, recommended_stocks, analysis_content, summary
        FROM longhubang_analysis
        ORDER BY id DESC LIMIT 1
    ''').fetchone()
    conn.close()
    if not row:
        return None
    rec = json.loads(row[2]) if row[2] else []
    content = json.loads(row[3]) if row[3] else {}
    return {
        'id': row[0],
        'date': row[1],
        'recommended_stocks': rec,
        'agents': content.get('agents_analysis', {}),
        'summary': row[4],
    }


def fetch_realtime_quotes(codes):
    """通过 TDX 拉当前价（用于盘前预测时的参考价）"""
    quotes = {}
    tdx = os.getenv('TDX_BASE_URL', 'http://localhost:9999')
    for code in codes:
        try:
            r = requests.get(f'{tdx}/api/quote', params={'code': code}, timeout=5)
            data = r.json()
            if data.get('code') == 0 and data.get('data'):
                k = data['data'][0]['K']
                quotes[code] = {
                    'price': k['Close'] / 1000,
                    'open': k['Open'] / 1000,
                    'high': k['High'] / 1000,
                    'low': k['Low'] / 1000,
                }
        except Exception as e:
            quotes[code] = {'error': str(e)}
    return quotes


def extract_top5_stocks(agents):
    """从个股潜力分析师的报告里抓 5 只"次日大概率上涨"股票"""
    stock_report = agents.get('stock', {}).get('analysis', '')
    # 匹配 "N. 名称 (代码)" 模式（如 "1. 珍宝岛 (603567)"）
    import re
    pattern = r'^\*?\*?\s*\d+\.\s+([^\s(（]+)\s*[（(](\d{6})[）)]'
    matches = re.findall(pattern, stock_report, re.MULTILINE)
    # 去重保序
    seen = set()
    result = []
    for name, code in matches:
        if code not in seen:
            seen.add(code)
            result.append({'name': name.strip('*'), 'code': code})
        if len(result) >= 5:
            break
    return result


def extract_strategy_summary(agents):
    """从 4 个分析师的报告里抓核心策略段"""
    summary = {}
    youzi = agents.get('youzi', {}).get('analysis', '')
    stock = agents.get('stock', {}).get('analysis', '')
    risk = agents.get('risk', {}).get('analysis', '')
    chief = agents.get('chief', {}).get('analysis', '')

    # 抓"风险提示"段（从 risk 和 youzi 里各取一段）
    for label, text in [('youzi', youzi), ('risk', risk)]:
        idx = text.find('风险提示')
        if idx >= 0:
            summary[f'{label}_risk'] = text[idx:idx+500]

    # 抓"投资策略"段
    for label, text in [('youzi', youzi), ('chief', chief)]:
        idx = text.find('投资策略')
        if idx >= 0:
            summary[f'{label}_strategy'] = text[idx:idx+600]

    return summary


def build_news_flow_section():
    """
    从 news_flow.db 拉板块级 AI 分析，拼成 markdown 段。
    出错时降级返回空字符串，不影响主简报。
    """
    try:
        from news_flow_engine import NewsFlowEngine
        e = NewsFlowEngine()
        d = e.get_dashboard_data()
    except Exception as ex:
        return f"\n> ⚠️ news_flow 模块异常: {str(ex)[:80]}\n"

    sentiment = d.get('latest_sentiment') or {}
    ai = d.get('latest_ai_analysis') or {}
    snap = d.get('latest_snapshot') or {}

    # 数据时间
    snap_time = snap.get('fetch_time', '')[:16] if snap.get('fetch_time') else '未知'

    lines = []
    lines.append("---")
    lines.append("")
    lines.append("## 🌊 新闻流量分析 (板块级)")
    lines.append("")
    lines.append(f"**数据时间**: {snap_time}  |  **流量总分**: {snap.get('total_score', 'N/A')}/100 ({snap.get('flow_level', 'N/A')})")
    lines.append("")

    # 情绪
    sent_idx = sentiment.get('sentiment_index')
    sent_cls = sentiment.get('sentiment_class', '中性')
    flow_stage = sentiment.get('flow_stage', '未知')
    if sent_idx is not None:
        sent_emoji = {'乐观': '🟢', '中性': '🟡', '悲观': '🔴'}.get(sent_cls, '⚪')
        lines.append(f"{sent_emoji} **情绪指数**: {sent_idx} ({sent_cls})  |  **市场阶段**: {flow_stage}")
        lines.append("")

    # 板块影响
    sectors = ai.get('affected_sectors') or []
    if sectors:
        lines.append("**🔥 板块影响**:")
        for s in sectors[:5]:
            if isinstance(s, dict):
                impact = s.get('impact', '')
                impact_emoji = {'利好': '🟢', '利空': '🔴'}.get(impact, '⚪')
                lines.append(f"  {impact_emoji} **{s.get('name', '?')}** (置信度 {s.get('confidence', '?')}%) — {impact}")
                reason = s.get('reason', '')
                if reason:
                    lines.append(f"     {reason[:120]}")
        lines.append("")

    # 风险 + 建议
    risk_level = ai.get('risk_level', '?')
    risk_emoji = {'低': '🟢', '中': '🟡', '高': '🔴'}.get(risk_level, '⚪')
    advice = ai.get('advice', '观望')
    confidence = ai.get('confidence', 0)
    lines.append(f"{risk_emoji} **风险等级**: {risk_level}  |  **AI 建议**: {advice}  |  **置信度**: {confidence}%")

    risk_factors = ai.get('risk_factors') or []
    if risk_factors:
        lines.append("")
        lines.append("**⚠️ 风险因素**:")
        for f in risk_factors[:3]:
            lines.append(f"  - {str(f)[:120]}")

    summary_text = ai.get('summary', '')
    if summary_text:
        lines.append("")
        lines.append(f"**📝 AI 摘要**: {summary_text[:300]}")

    return "\n".join(lines)


def build_dingtalk_message(report, top5, quotes, cfg, news_flow_md=""):
    """拼成钉钉 markdown 消息"""
    recs = report['recommended_stocks']
    if not recs:
        return None

    today = report['date'][:10] if report['date'] else '未知'
    keyword = cfg['webhook_keyword']

    # 主体内容
    lines = []
    lines.append(f"### {keyword} - 每日操盘建议")
    lines.append("")
    lines.append(f"**报告日期**: {today}  |  **推送时间**: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## 🎯 次日重点推荐 (10 只)")
    lines.append("")

    def limit_pct(code):
        return 0.20 if code.startswith(('300', '301', '688')) else 0.10

    for i, s in enumerate(recs[:10], 1):
        code = s.get('code', '')
        name = s.get('name', '')
        inflow = s.get('net_inflow', 0) / 1e8
        conf = s.get('confidence', '中')
        if not code:
            continue
        q = quotes.get(code, {})
        price = q.get('price', 0)
        limit_up = price * (1 + limit_pct(code)) if price else 0
        stop = limit_up * 0.95 if limit_up else 0
        tp = price * 1.08 if price else 0
        ent_lo = price * 0.97 if price else 0
        ent_hi = price * 1.03 if price else 0
        emoji = '🥇🥈🥉' if i <= 3 else '🔹'
        lines.append(f"{emoji} **{name}** ({code}) - 净流入 {inflow:.2f}亿 - 置信度 {conf}")
        if price:
            lines.append(f"   现价 ¥{price:.2f} | 进场 [{ent_lo:.2f}, {ent_hi:.2f}] | 止盈 ¥{tp:.2f} | 止损 ¥{stop:.2f}")
        lines.append("")

    if top5:
        lines.append("---")
        lines.append("")
        lines.append("## ⭐ AI 看好 TOP5 (来自个股潜力分析师)")
        lines.append("")
        for t in top5:
            lines.append(f"- **{t['name']}** ({t['code']})")
        lines.append("")

    # 风险提示 - 取首段非空内容
    risk_text = ""
    for key in ('risk_risk', 'youzi_risk'):
        if key in report.get('_summary', {}):
            risk_text = report['_summary'][key]
            break
    if risk_text:
        # 跳过 "风险提示**" 这种 markdown 标记行，取后续非空行
        risk_lines = [l.strip().rstrip('*').strip() for l in risk_text.split('\n') if l.strip() and l.strip() != '风险提示']
        risk_excerpt = ' '.join(risk_lines[:3])[:400]  # 前 3 行，拼成一段
        if risk_excerpt:
            lines.append("---")
            lines.append("")
            lines.append("## ⚠️ 风险提示")
            lines.append("")
            lines.append(risk_excerpt)
            lines.append("")

    # 新闻流量分析 (板块级) - 来自 news_flow 模块
    if news_flow_md:
        lines.append(news_flow_md)
        lines.append("")

    lines.append("---")
    lines.append("")
    lines.append(f"_本简报由智瞰龙虎自动生成（报告 #{report['id']}），交易时段命中阈值会单独推送_")

    return "\n".join(lines)


def send_dingtalk(webhook_url, keyword, title, content):
    """发钉钉 markdown"""
    data = {
        "msgtype": "markdown",
        "markdown": {
            "title": f"{keyword} - 每日操盘建议",
            "text": content,
        }
    }
    r = requests.post(webhook_url, json=data, headers={'Content-Type': 'application/json'}, timeout=10)
    if r.status_code == 200:
        result = r.json()
        if result.get('errcode') == 0:
            return True, "推送成功"
        return False, f"钉钉返回错误: {result.get('errmsg')}"
    return False, f"HTTP {r.status_code}"


# ========== 监测池注入（每日报告 → monitor_db） ==========

def _limit_pct(code: str) -> float:
    """涨跌停限制：创业板/科创板 20%，主板 10%"""
    return 0.20 if code.startswith(('300', '301', '688')) else 0.10


def _confidence_to_rating(conf: str) -> str:
    """置信度 → 投资评级（喂给监测卡片的评级 chip）"""
    return {'高': '买入', '中': '持有', '低': '卖出'}.get(conf, '持有')


def inject_to_monitor_pool(recs, quotes, ttl_days: int = 7, max_pool: int = 50):
    """
    把报告里的推荐股灌进 monitor_db 监测池。
    依赖 monitor_db.upsert_with_ttl：覆盖式 + 7 天 TTL + 50 只上限。

    返回 monitor_db.upsert_with_ttl 的结果 dict；模块加载失败时返回 None。
    """
    try:
        from monitor_db import monitor_db
    except Exception as e:
        print(f"⚠️ monitor_db 加载失败，跳过注入: {e}")
        return None

    items = []
    for s in recs:
        code = s.get('code', '')
        if not code:
            continue
        q = quotes.get(code, {})
        price = q.get('price', 0)
        if not price:
            # 盘前没拉到价（如 TDX 还没起或非交易时段）就不进监测池
            print(f"⚠️ {code} 缺实时价，跳过注入")
            continue

        limit_up = price * (1 + _limit_pct(code))
        stop = round(limit_up * 0.95, 2)
        tp = round(price * 1.08, 2)
        ent_lo = round(price * 0.97, 2)
        ent_hi = round(price * 1.03, 2)

        items.append({
            'symbol': code,
            'name': s.get('name', code),
            'rating': _confidence_to_rating(s.get('confidence', '中')),
            'entry_min': ent_lo,
            'entry_max': ent_hi,
            'take_profit': tp,
            'stop_loss': stop,
            'check_interval': 1,            # 1 分钟（TDX 模式下被快路径覆盖）
            'notification_enabled': True,
            'trading_hours_only': True,
        })

    if not items:
        print('⚠️ 无可注入监测股（推荐列表为空或全部缺价）')
        return None

    result = monitor_db.upsert_with_ttl(items, ttl_days=ttl_days, max_pool=max_pool)
    print(
        f"📥 监测池注入: 新增 {result['added']} / 更新 {result['updated']} / "
        f"过期清理 {result['expired_purged']} / 容量淘汰 {result['evicted']} / "
        f"失败 {result['failed']}"
    )
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--dry-run', action='store_true', help='只打印不推送，也不注入监测池')
    parser.add_argument('--skip-monitor', action='store_true', help='推送但不注入监测池')
    args = parser.parse_args()

    print('=' * 60)
    print('每日操盘建议推送')
    print('=' * 60)

    cfg = load_config()
    if not cfg['webhook_enabled'] or not cfg['webhook_url']:
        print('❌ WEBHOOK 未启用或 URL 为空，请检查 .env')
        return 1

    # 1) 取最新报告
    report = get_latest_report()
    if not report:
        print('❌ longhubang.db 里没有报告可推')
        return 1
    print(f'📄 报告 #{report["id"]} ({report["date"]})')

    # 2) 抓 5 只潜力股 + 风险段
    top5 = extract_top5_stocks(report['agents'])
    report['_summary'] = extract_strategy_summary(report['agents'])
    print(f'⭐ 个股潜力 TOP5: {[t["name"] for t in top5]}')

    # 3) 拉实时价（盘前 9 点可能还没开盘，quotes 会有很多空值，但不影响推送）
    codes = [s['code'] for s in report['recommended_stocks'] if s.get('code')]
    quotes = fetch_realtime_quotes(codes)
    print(f'💰 拉取 {len(quotes)}/{len(codes)} 只实时价')

    # 4) 拼消息 (含 news_flow 板块级分析)
    print('🌊 拉取 news_flow 板块级分析...')
    news_flow_md = build_news_flow_section()
    content = build_dingtalk_message(report, top5, quotes, cfg, news_flow_md=news_flow_md)
    if not content:
        print('❌ 推荐股列表为空')
        return 1

    # 5) 推送 or 打印
    if args.dry_run:
        print()
        print('=' * 60)
        print('【DRY-RUN 模式】以下是钉钉将收到的内容:')
        print('=' * 60)
        print(content)
        return 0

    print()
    ok, msg = send_dingtalk(cfg['webhook_url'], cfg['webhook_keyword'], '每日操盘建议', content)
    if not ok:
        print(f'❌ {msg}')
        return 1

    print(f'✅ {msg}')

    # 6) 推送成功后 → 注入监测池（覆盖式 + 7 天 TTL + 50 只上限）
    if args.skip_monitor:
        print('⏭️ --skip-monitor，跳过监测池注入')
        return 0

    print('📥 注入监测池...')
    inject_to_monitor_pool(
        report['recommended_stocks'],
        quotes,
        ttl_days=7,
        max_pool=50,
    )
    return 0


if __name__ == '__main__':
    sys.exit(main())
