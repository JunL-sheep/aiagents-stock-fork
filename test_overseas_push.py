"""
独立测试脚本：推送隔夜外盘速览到钉钉
用于验证 overseas_market_fetcher 的完整链路。

使用方法:
    python test_overseas_push.py         # 推送钉钉
    python test_overseas_push.py --dry   # 仅打印，不推送
"""

import os
import sys
import argparse
from dotenv import load_dotenv
import requests
from datetime import datetime


def load_config():
    load_dotenv('.env')
    return {
        'webhook_url': os.getenv('WEBHOOK_URL', ''),
        'webhook_enabled': os.getenv('WEBHOOK_ENABLED', 'false').lower() == 'true',
        'webhook_keyword': os.getenv('WEBHOOK_KEYWORD', '股票'),
    }


def send_dingtalk(webhook_url, keyword, content):
    """发钉钉 markdown"""
    data = {
        "msgtype": "markdown",
        "markdown": {
            "title": f"{keyword} - 隔夜外盘速览",
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


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--dry', action='store_true', help='仅打印不推送')
    args = parser.parse_args()

    print("=" * 60)
    print("🌍 隔夜外盘速览 — 推送测试")
    print("=" * 60)

    # 拉取数据
    from overseas_market_fetcher import fetch_overseas_snapshot, format_overseas_snapshot_md

    print("拉取外盘行情...")
    data = fetch_overseas_snapshot(stock_delay=1.0)
    md = format_overseas_snapshot_md(data)

    # 构建完整消息
    keyword = load_config()['webhook_keyword']
    lines = []
    lines.append(f"### {keyword} - 🌍 隔夜外盘速览")
    lines.append(f"**拉取时间**: {datetime.now().strftime('%Y-%m-%d %H:%M')}  |  *数据来源: Yahoo Finance*")
    lines.append("")
    lines.append(md)

    # 简评（同 build_overseas_section 的逻辑）
    ai_stocks = data.get('ai_stocks', [])
    kr_semicon = data.get('kr_semicon', [])
    indices = data.get('indices', [])
    down_ai = [s for s in ai_stocks if s.get('change_pct', 0) < -5]
    down_kr = [s for s in kr_semicon if s.get('change_pct', 0) < -5]
    parts = []
    if down_ai:
        names = '、'.join(s['name'] for s in down_ai[:3])
        avg = sum(s.get('change_pct', 0) for s in down_ai) / len(down_ai)
        parts.append(f"美股AI板块走弱（{names}等跌幅{avg:.1f}%）")
    if down_kr:
        names = '、'.join(s['name'] for s in down_kr[:2])
        parts.append(f"韩股半导体承压（{names}跌幅显著）")
    if indices:
        avg_idx = sum(i.get('change_pct', 0) for i in indices) / len(indices)
        direction = "偏弱震荡" if avg_idx < -0.3 else ("小幅走高" if avg_idx > 0.3 else "窄幅整理")
    if parts:
        summary = f"> **简评**: 隔夜{'；'.join(parts)}。美股三大指数{direction}，\n> 今日A股AI/半导体板块开盘可能承压，注意仓位控制。"
        lines.append(summary)
    lines.append("")
    lines.append("---")
    lines.append(f"_测试推送 | {datetime.now().strftime('%Y-%m-%d %H:%M')}_")

    content = '\n'.join(lines)

    if args.dry:
        print()
        print("=" * 60)
        print("【DRY-RUN】以下是钉钉将收到的内容:")
        print("=" * 60)
        print(content)
        return 0

    # 推送到主 Webhook（通过 send_analysis_result 模拟盘中预警的双推送）
    from notification_service import notification_service
    ok1 = notification_service.send_analysis_result('🌍 隔夜外盘速览', content)
    print(f"主Webhook+新闻流量Webhook: {'✅' if ok1 else '❌'}")

    return 0


if __name__ == '__main__':
    sys.exit(main())
