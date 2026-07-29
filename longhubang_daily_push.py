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


def build_dingtalk_message(result: dict) -> str:
    """
    把 run_comprehensive_analysis 的完整结果拼成钉钉 markdown 消息。
    尽可能详细展示所有分析维度。
    """
    lines = []
    ts = result.get('timestamp', datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
    lines.append(f"## 📊 智瞰龙虎 · 盘后全量报告")
    lines.append(f"**{ts}**")
    lines.append("")

    data_info = result.get('data_info', {})
    summary = data_info.get('summary', {})
    final_report = result.get('final_report', {})
    agents = result.get('agents_analysis', {})
    recommended = result.get('recommended_stocks', [])
    scoring = result.get('scoring_ranking', [])

    # ==================== 一、数据概览 ====================
    lines.append("---")
    lines.append("")
    lines.append("### 📈 一、数据概览")
    lines.append("")
    data_overview = final_report.get('data_overview', {})
    lines.append(f"| 指标 | 数值 |")
    lines.append(f"|------|------|")
    lines.append(f"| 龙虎榜记录数 | {data_overview.get('total_records', 0)} 条 |")
    lines.append(f"| 涉及个股 | {data_overview.get('total_stocks', 0)} 只 |")
    lines.append(f"| 活跃游资 | {data_overview.get('total_youzi', 0)} 个 |")
    lines.append(f"| 净买入总额 | {data_overview.get('total_net_inflow', 0):,.2f} 元 |")
    lines.append(f"| 推荐股票 | {data_overview.get('recommended_stocks_count', 0)} 只 |")
    lines.append("")

    # ==================== 二、AI 评分排名 TOP10 ====================
    lines.append("---")
    lines.append("")
    lines.append("### 🏆 二、AI 智能评分排名 TOP10")
    lines.append("")
    if scoring:
        lines.append("| 排名 | 股票 | 评分 | 净流入(万) | 游资关注度 |")
        lines.append("|------|------|------|-----------|-----------|")
        for i, s in enumerate(scoring[:10], 1):
            code = s.get('gpdm', s.get('stock_code', ''))
            name = s.get('gpmc', s.get('stock_name', ''))
            score = s.get('total_score', s.get('score', 0))
            net = s.get('net_inflow', 0) / 10000  # 转万元
            youzi = s.get('youzi_count', s.get('youzi_score', 0))
            lines.append(f"| {i} | {name}({code}) | {score} | {net:.1f} | {youzi} |")
        lines.append("")
    else:
        lines.append("（评分数据暂不可用）")
        lines.append("")

    # ==================== 三、推荐股票 ====================
    lines.append("---")
    lines.append("")
    lines.append("### ⭐ 三、精选推荐（Top 10）")
    lines.append("")
    if recommended:
        lines.append("| # | 股票 | 净流入 | 置信度 | 依据 |")
        lines.append("|---|------|--------|--------|------|")
        for i, s in enumerate(recommended[:10], 1):
            code = s.get('code', '')
            name = s.get('name', '')
            inflow = s.get('net_inflow', 0) / 1e8  # 转亿
            conf = s.get('confidence', '中')
            reason = s.get('reason', '')[:40]
            lines.append(f"| {i} | **{name}** ({code}) | {inflow:.2f}亿 | {conf} | {reason} |")
        lines.append("")
    else:
        lines.append("（暂无推荐股票）")
        lines.append("")

    # ==================== 四、游资行为分析 ====================
    youzi_analysis = agents.get('youzi', {})
    youzi_text = youzi_analysis.get('analysis', '')
    if youzi_text:
        lines.append("---")
        lines.append("")
        lines.append("### 🎯 四、游资行为分析")
        lines.append("")
        # 截取关键内容，钉钉markdown有长度限制
        youzi_excerpt = youzi_text[:2000]
        lines.append(youzi_excerpt)
        lines.append("")

        # 活跃游资TOP
        top_youzi = summary.get('top_youzi', {})
        if top_youzi:
            lines.append("**活跃游资 TOP10**")
            lines.append("")
            lines.append("| 排名 | 游资 | 净买入额(万) |")
            lines.append("|------|------|-------------|")
            for i, (name, amount) in enumerate(
                sorted(top_youzi.items(), key=lambda x: x[1], reverse=True)[:10], 1
            ):
                lines.append(f"| {i} | {name} | {amount/10000:.1f} |")
            lines.append("")

    # ==================== 五、个股潜力分析 ====================
    stock_analysis = agents.get('stock', {})
    stock_text = stock_analysis.get('analysis', '')
    if stock_text:
        lines.append("---")
        lines.append("")
        lines.append("### 💎 五、个股潜力分析")
        lines.append("")
        lines.append(stock_text[:2000])
        lines.append("")

    # ==================== 六、题材追踪分析 ====================
    theme_analysis = agents.get('theme', {})
    theme_text = theme_analysis.get('analysis', '')
    if theme_text:
        lines.append("---")
        lines.append("")
        lines.append("### 🔥 六、题材追踪分析")
        lines.append("")
        lines.append(theme_text[:1500])
        lines.append("")

    # ==================== 七、风险控制 ====================
    risk_analysis = agents.get('risk', {})
    risk_text = risk_analysis.get('analysis', '')
    if risk_text:
        lines.append("---")
        lines.append("")
        lines.append("### ⚠️ 七、风险控制")
        lines.append("")
        # 风险分析重点关注"风险提示"段
        risk_idx = risk_text.find('风险提示')
        if risk_idx >= 0:
            lines.append(risk_text[risk_idx:risk_idx+1000])
        else:
            lines.append(risk_text[:1000])
        lines.append("")

    # ==================== 八、首席策略 ====================
    chief_analysis = agents.get('chief', {})
    chief_text = chief_analysis.get('analysis', '')
    if chief_text:
        lines.append("---")
        lines.append("")
        lines.append("### 🧠 八、首席策略师综合判断")
        lines.append("")
        lines.append(chief_text[:2000])
        lines.append("")

    # ==================== 九、最终总结 ====================
    lines.append("---")
    lines.append("")
    lines.append(f"**📝 摘要**: {final_report.get('summary', '')}")
    lines.append("")
    lines.append("---")
    lines.append(f"_智瞰龙虎自动生成 | {ts}_")

    return '\n'.join(lines)


def push_to_dingtalk(webhook_url: str, keyword: str, content: str) -> tuple:
    """推送钉钉 markdown"""
    import requests
    data = {
        "msgtype": "markdown",
        "markdown": {
            "title": f"{keyword} - 盘后龙虎榜全量报告",
            "text": content,
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

    target_date = args.date or (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')
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

    # 2) 构建消息
    print('\n📝 构建推送消息...')
    content = build_dingtalk_message(result)

    if args.dry_run:
        print()
        print('=' * 60)
        print('【DRY-RUN 模式】以下是钉钉将收到的内容:')
        print('=' * 60)
        # 只打印前5000字符避免刷屏
        if len(content) > 5000:
            print(content[:5000])
            print(f'\n...（共 {len(content)} 字符，已截断）')
        else:
            print(content)
        return 0

    # 3) 推送
    print(f'\n📤 推送到龙虎榜专用机器人...')
    ok, msg = push_to_dingtalk(
        cfg['longhubang_webhook_url'],
        cfg['webhook_keyword'],
        content
    )
    if not ok:
        print(f'❌ {msg}')
        return 1
    print(f'✅ {msg}')

    # 钉钉单条消息限制约 20000 字符，超长时告警
    if len(content) > 18000:
        print(f'⚠️ 消息长度 {len(content)} 字符，接近钉钉限制，部分渠道可能截断')

    return 0


if __name__ == '__main__':
    sys.exit(main())
