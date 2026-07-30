"""
龙虎榜盘后全量推送
每交易日 19:00 自动运行，获取当日龙虎榜数据并推送完整分析结果到专用钉钉机器人。

运行方式:
    python longhubang_daily_push.py              # 完整运行 + 推送
    python longhubang_daily_push.py --dry-run    # 仅打印，不推送
    python longhubang_daily_push.py --date 2026-07-29  # 指定日期

依赖:
    - notification_service.send_longhubang_message()
    - longhubang_engine.run_comprehensive_analysis()
"""

import os
import sys
import json
import argparse
from datetime import datetime, timedelta
from dotenv import load_dotenv


def load_config():
    """加载配置"""
    load_dotenv('.env')
    return {
        'longhubang_webhook_url': os.getenv('LONGHUBANG_WEBHOOK_URL', ''),
        'webhook_keyword': os.getenv('LONGHUBANG_WEBHOOK_KEYWORD', '龙虎榜'),
    }


def build_dingtalk_message(result: dict, html_path: str = '') -> str:
    """
    生成 DingTalk 推送用的摘要消息。
    完整内容请查看生成的 HTML 报告。
    """
    lines = []
    ts = result.get('timestamp', datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
    lines.append(f"## 📊 智瞰龙虎 · 盘后全量报告")
    lines.append(f"**{ts}**")
    lines.append("")

    data_info = result.get('data_info', {})
    summary = data_info.get('summary', {})
    recommended = result.get('recommended_stocks', [])
    scoring = result.get('scoring_ranking', [])

    # 数据概览
    net_inflow = summary.get('total_net_inflow', 0)
    inflow_emoji = '🔴' if net_inflow >= 0 else '🟢'
    lines.append(f"📈 **{summary.get('total_records', 0)}** 条记录  |  **{summary.get('total_stocks', 0)}** 只个股  |  🎯 **{summary.get('total_youzi', 0)}** 游资")
    lines.append(f"{inflow_emoji} **净买入**: {abs(net_inflow)/1e8:.2f}亿{'（净流入）' if net_inflow >= 0 else '（净流出）'}")
    lines.append("")

    # 评分排名 TOP5
    if scoring:
        lines.append("**🏆 评分 TOP5**")
        for i, s in enumerate(scoring[:5], 1):
            name = s.get('股票名称', '?')
            code = s.get('股票代码', '')
            score = s.get('综合评分', 0)
            net = s.get('净流入', 0)
            medal = {1:'🥇', 2:'🥈', 3:'🥉'}.get(i, f'{i}.')
            lines.append(f"  {medal} {name}({code})  评分 {score}  净流入 {net/1e8:.2f}亿")
        lines.append("")

    # 推荐 TOP5
    if recommended:
        lines.append("**⭐ 推荐 TOP5**")
        for i, s in enumerate(recommended[:5], 1):
            name = s.get('name', '')
            code = s.get('code', '')
            inflow = s.get('net_inflow', 0) / 1e8
            medal = {1:'🥇', 2:'🥈', 3:'🥉'}.get(i, '🔹')
            lines.append(f"  {medal} {name}({code})  {inflow:.2f}亿")
        lines.append("")

    # 完整报告入口
    if html_path:
        lines.append("---")
        lines.append("")
        lines.append(f"📄 **完整报告已生成**")
        lines.append(f"```")
        lines.append(f"{html_path}")
        lines.append(f"```")
        lines.append(f"双击打开即可查看全部 5 位 AI 分析师的完整分析内容。")
        lines.append("")

    lines.append("---")
    lines.append(f"_智瞰龙虎自动生成 | {ts}_")

    return '\n'.join(lines)


def push_to_dingtalk(webhook_url: str, content: str) -> tuple:
    """推送钉钉 markdown"""
    import requests
    # 确保消息正文包含关键字（钉钉安全校验：关键词=龙虎榜分析）
    safe_content = content
    if '龙虎榜分析' not in content:
        safe_content = f"### 龙虎榜分析\n\n{content}"
    data = {
        "msgtype": "markdown",
        "markdown": {
            "title": "龙虎榜分析 - 盘后龙虎榜全量报告",
            "text": safe_content,
        }
    }
    r = requests.post(webhook_url, json=data, headers={'Content-Type': 'application/json'}, timeout=15)
    if r.status_code == 200:
        result = r.json()
        if result.get('errcode') == 0:
            return True, "推送成功"
        return False, f"钉钉返回错误: {result.get('errmsg')}"
    return False, f"HTTP {r.status_code}"


def main():
    parser = argparse.ArgumentParser(description='龙虎榜盘后全量推送')
    parser.add_argument('--dry-run', action='store_true', help='仅打印不推送')
    parser.add_argument('--date', type=str, default=None, help='分析日期 YYYY-MM-DD（默认今日）')
    parser.add_argument('--days', type=int, default=1, help='分析最近几天数据（默认1天）')
    args = parser.parse_args()

    print('=' * 60)
    print('📊 智瞰龙虎 · 盘后全量推送')
    print('=' * 60)

    cfg = load_config()
    if not cfg['longhubang_webhook_url'] and not args.dry_run:
        print('❌ 未配置 LONGHUBANG_WEBHOOK_URL，请在 .env 中设置')
        return 1
    if not cfg['longhubang_webhook_url']:
        print('⚠️ LONGHUBANG_WEBHOOK_URL 未配置（dry-run 模式仅打印）')

    # 1) 运行完整分析
    print('\n🚀 启动龙虎榜综合分析引擎...')
    from longhubang_engine import LonghubangEngine
    engine = LonghubangEngine()

    # 默认使用今天（盘后19:00运行，当日龙虎榜已出）
    target_date = args.date or datetime.now().strftime('%Y-%m-%d')
    print(f'📅 分析目标: {target_date}（过去 {args.days} 天）')

    result = engine.run_comprehensive_analysis(date=target_date, days=args.days)

    if not result.get('success'):
        print(f'❌ 分析失败: {result.get("error", "未知错误")}')
        return 1

    print(f'✅ 分析完成!')
    print(f'   📄 报告ID: {result.get("report_id")}')
    print(f'   📈 数据: {result["data_info"]["summary"].get("total_records", 0)} 条记录, '
          f'{result["data_info"]["summary"].get("total_stocks", 0)} 只股票')
    print(f'   ⭐ 推荐: {len(result.get("recommended_stocks", []))} 只股票')

    # 2) 生成 HTML 完整报告
    print('\n📄 生成 HTML 完整报告...')
    from longhubang_report_html import generate_html_report
    html_path = generate_html_report(result)

    # 3) 构建 DingTalk 摘要消息
    print('📝 构建推送摘要...')
    content = build_dingtalk_message(result, html_path=html_path)

    if args.dry_run:
        print()
        print('=' * 60)
        print('【DRY-RUN 模式】以下是钉钉将收到的内容:')
        print('=' * 60)
        print(content)
        print(f'\n📄 完整报告: {html_path}')
        return 0

    # 4) 推送
    print(f'\n📤 推送到龙虎榜专用机器人...')
    ok, msg = push_to_dingtalk(
        cfg['longhubang_webhook_url'],
        content
    )
    if not ok:
        print(f'❌ {msg}')
        return 1
    print(f'✅ {msg}')

    return 0


if __name__ == '__main__':
    sys.exit(main())
