"""
龙虎榜盘后全量推送
每交易日 19:00 自动运行，获取当日龙虎榜数据，完整内容拆成多条钉钉消息推送。

运行方式:
    python longhubang_daily_push.py                 # 完整运行 + 推送
    python longhubang_daily_push.py --dry-run       # 仅打印，不推送
    python longhubang_daily_push.py --date 2026-07-29  # 指定日期

推送策略:
    钉钉单条消息限制 20000 字符，完整 AI 分析约 28000 字符，
    拆为 3 条消息依次发送，每条包含 1-2 位 AI 分析师的完整内容。
"""

import os
import sys
import json
import re
import argparse
from datetime import datetime
from dotenv import load_dotenv


def load_config():
    """加载配置"""
    load_dotenv('.env')
    return {
        'longhubang_webhook_url': os.getenv('LONGHUBANG_WEBHOOK_URL', ''),
    }


def _strip_reasoning(text: str) -> str:
    """只去掉【推理过程】段落，完整保留分析内容"""
    text = re.sub(r'【推理过程】.*?\n(###|\*\*核心|\Z)', r'\1', text, flags=re.DOTALL)
    text = re.sub(r'^嗯.*?。\n\n', '', text, flags=re.DOTALL)
    return text.strip()


# ============================================================
# 消息构建（每条独立，均可点击钉钉 markdown 链接）
# ============================================================

def build_msg_overview(summary: dict, scoring: list, recommended: list, ts: str) -> str:
    """消息1：数据概览 + 评分排名 + 精选推荐"""
    lines = []
    lines.append(f"## 📊 智瞰龙虎 · 盘后全量报告")
    lines.append(f"**{ts}**")
    lines.append("")
    net_inflow = summary.get('total_net_inflow', 0)
    inflow_emoji = '🔴' if net_inflow >= 0 else '🟢'
    lines.append(f"**📈 数据概览**")
    lines.append(f"  🔹 记录: {summary.get('total_records', 0)} 条  |  个股: {summary.get('total_stocks', 0)} 只")
    lines.append(f"  🔹 游资: {summary.get('total_youzi', 0)} 个")
    lines.append(f"  {inflow_emoji} 净买入: {abs(net_inflow)/1e8:.2f}亿{'（净流入）' if net_inflow >= 0 else '（净流出）'}")
    lines.append("")

    if scoring:
        lines.append("**🏆 AI 评分排名 TOP10**")
        for i, s in enumerate(scoring[:10], 1):
            name = s.get('股票名称', '?')
            code = s.get('股票代码', '')
            score = s.get('综合评分', 0)
            net = s.get('净流入', 0)
            medal = {1:'🥇', 2:'🥈', 3:'🥉'}.get(i, f'  {i}.')
            lines.append(f"  {medal} **{name}** ({code})  评分 {score}  净流入 {net/1e8:.2f}亿")
        lines.append("")

    if recommended:
        lines.append("**⭐ 精选推荐 TOP10**")
        for i, s in enumerate(recommended[:10], 1):
            code = s.get('code', '')
            name = s.get('name', '')
            inflow = s.get('net_inflow', 0) / 1e8
            conf = s.get('confidence', '中')
            medal = {1:'🥇', 2:'🥈', 3:'🥉'}.get(i, '🔹')
            lines.append(f"  {medal} **{name}** ({code})  {inflow:.2f}亿  {conf}")
        lines.append("")

    lines.append(f"---\n_完整 AI 分析见后续消息_")
    return '\n'.join(lines)


def build_msg_ai(text: str, label: str, emoji: str, seq: str) -> str:
    """构建一条 AI 分析师完整内容消息"""
    clean = _strip_reasoning(text)
    header = f"## {seq}/3 {emoji} {label}\n\n"
    if len(header) + len(clean) > 19500:
        # 超长时截断（保留尾部关键段落）
        clean = clean[:19000] + "\n\n...（内容超长已截断）"
    return header + clean


def build_msg_combined(texts: list, seq: str) -> str:
    """
    构建组合消息（多位分析师合并在一条，各自有标题分隔）。
    texts: [(emoji, label, full_text), ...]
    """
    parts = []
    parts.append(f"## {seq}/3\n")
    for emoji, label, text in texts:
        clean = _strip_reasoning(text)
        parts.append(f"**{emoji} {label}**\n")
        parts.append(clean)
        parts.append("")
    combined = '\n'.join(parts)
    if len(combined) > 19500:
        # 截断到 19000 并在末尾加提示
        combined = combined[:19000] + "\n\n...（内容超长已截断）"
    return combined


def push_dingtalk(webhook_url: str, content: str, title: str) -> bool:
    """推送一条钉钉 markdown 消息"""
    import requests
    safe_content = content
    if '龙虎榜分析' not in content:
        safe_content = f"### 龙虎榜分析\n\n{content}"
    data = {
        "msgtype": "markdown",
        "markdown": {
            "title": f"龙虎榜分析 - {title}",
            "text": safe_content,
        }
    }
    try:
        r = requests.post(webhook_url, json=data, headers={'Content-Type': 'application/json'}, timeout=15)
        if r.status_code == 200:
            result = r.json()
            return result.get('errcode') == 0
        return False
    except Exception:
        return False


def main():
    parser = argparse.ArgumentParser(description='龙虎榜盘后全量推送')
    parser.add_argument('--dry-run', action='store_true', help='仅打印不推送')
    parser.add_argument('--date', type=str, default=None, help='分析日期')
    parser.add_argument('--days', type=int, default=1)
    args = parser.parse_args()

    print('=' * 60)
    print('📊 智瞰龙虎 · 盘后全量推送')
    print('=' * 60)

    cfg = load_config()
    if not cfg['longhubang_webhook_url'] and not args.dry_run:
        print('❌ 未配置 LONGHUBANG_WEBHOOK_URL')
        return 1
    if not cfg['longhubang_webhook_url']:
        print('⚠️ WEBHOOK 未配置（dry-run）')

    # 1) 运行分析
    print('\n🚀 启动分析引擎...')
    from longhubang_engine import LonghubangEngine
    engine = LonghubangEngine()

    target_date = args.date or datetime.now().strftime('%Y-%m-%d')
    print(f'📅 目标: {target_date}')

    result = engine.run_comprehensive_analysis(date=target_date, days=args.days)
    if not result.get('success'):
        print(f'❌ 失败: {result.get("error")}')
        return 1

    report_id = result.get('report_id')
    ts = result.get('timestamp', datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
    data_info = result.get('data_info', {})
    summary = data_info.get('summary', {})
    recommended = result.get('recommended_stocks', [])
    scoring = result.get('scoring_ranking', [])
    agents = result.get('agents_analysis', {})

    print(f'✅ 报告 #{report_id}: {summary.get("total_records", 0)} 条记录, '
          f'{summary.get("total_stocks", 0)} 只股票, {len(recommended)} 推荐')

    # 2) 同时生成 HTML 报告（本地存档用）
    try:
        from longhubang_report_html import generate_html_report
        html_path = generate_html_report(result)
        print(f'📄 HTML 已存档: {html_path}')
    except Exception as e:
        print(f'⚠️ HTML 生成失败: {e}')
        html_path = ''

    # 3) 构建消息列表
    messages = []

    # 消息1: 概览
    msg1 = build_msg_overview(summary, scoring, recommended, ts)
    messages.append((msg1, '盘后全量报告'))

    # 消息2: 游资行为 + 个股潜力（合并）
    youzi_text = agents.get('youzi', {}).get('analysis', '')
    stock_text = agents.get('stock', {}).get('analysis', '')
    msg2_parts = []
    if youzi_text:
        msg2_parts.append(('🎯', '游资行为分析', youzi_text))
    if stock_text:
        msg2_parts.append(('💎', '个股潜力分析', stock_text))
    if msg2_parts:
        msg2 = build_msg_combined(msg2_parts, '1')
        messages.append((msg2, '游资与个股'))

    # 消息3: 题材追踪 + 风险控制（合并）
    theme_text = agents.get('theme', {}).get('analysis', '')
    risk_text = agents.get('risk', {}).get('analysis', '')
    msg3_parts = []
    if theme_text:
        msg3_parts.append(('🔥', '题材追踪分析', theme_text))
    if risk_text:
        msg3_parts.append(('⚠️', '风险控制', risk_text))
    if msg3_parts:
        msg3 = build_msg_combined(msg3_parts, '2')
        messages.append((msg3, '题材与风险'))

    # 消息4: 首席策略
    chief_text = agents.get('chief', {}).get('analysis', '')
    if chief_text:
        msg4 = build_msg_ai(chief_text, '首席策略师综合判断', '🧠', '3')
        # 末尾加个结束标记
        msg4 += f"\n\n---\n_📊 智瞰龙虎自动生成 | 报告 #{report_id}_"
        messages.append((msg4, '首席策略'))

    # 4) 推送
    if args.dry_run:
        print(f'\n共 {len(messages)} 条消息:')
        for i, (content, title) in enumerate(messages, 1):
            print(f'\n{"="*40}')
            print(f'【消息 {i}/{len(messages)}】{title} ({len(content)} 字符)')
            print(f'{"="*40}')
            print(content[:2000])
            if len(content) > 2000:
                print(f'\n...（共 {len(content)} 字符，已截断显示）')
        return 0

    url = cfg['longhubang_webhook_url']
    print(f'\n📤 推送 {len(messages)} 条消息...')
    for i, (content, title) in enumerate(messages, 1):
        ok = push_dingtalk(url, content, title)
        status = '✅' if ok else '❌'
        print(f'  {status} [{i}/{len(messages)}] {title} ({len(content)} 字符)')
        if not ok:
            print(f'    推送失败，停止后续消息')
            return 1
        import time
        time.sleep(2)  # 避免消息顺序错乱

    print(f'\n✅ 全部推送完成')
    return 0


if __name__ == '__main__':
    sys.exit(main())
