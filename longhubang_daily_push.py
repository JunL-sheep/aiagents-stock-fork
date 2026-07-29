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

    格式要点（手机端优化）：
    - 关键数据用要点，不用宽表格
    - AI 分析去冗取精，仅保留结论性内容
    """
    lines = []
    ts = result.get('timestamp', datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
    lines.append(f"## 📊 智瞰龙虎 · 盘后全量报告")
    lines.append(f"**{ts}**")
    lines.append("")

    data_info = result.get('data_info', {})
    summary = data_info.get('summary', {})
    agents = result.get('agents_analysis', {})
    recommended = result.get('recommended_stocks', [])
    scoring = result.get('scoring_ranking', [])

    # ==================== 一、数据概览 ====================
    lines.append("---")
    lines.append("")
    lines.append("### 📈 一、数据概览")
    lines.append("")
    lines.append(f"🔹 **记录**: {summary.get('total_records', 0)} 条  |  **个股**: {summary.get('total_stocks', 0)} 只")
    lines.append(f"🔹 **净买入**: {summary.get('total_net_inflow', 0)/1e8:.2f} 亿")
    lines.append(f"🔹 **推荐股票**: {len(recommended)} 只")
    lines.append("")

    # ==================== 二、评分排名（改用简洁要点）====================
    lines.append("---")
    lines.append("")
    lines.append("### 🏆 二、AI 评分排名 TOP10")
    lines.append("")
    if scoring:
        for i, s in enumerate(scoring[:10], 1):
            name = s.get('股票名称', '?')
            code = s.get('股票代码', '')
            score = s.get('综合评分', 0)
            net = s.get('净流入', 0)
            medal = {1:'🥇', 2:'🥈', 3:'🥉'}.get(i, f'{i}.')
            lines.append(f"  {medal} **{name}** ({code})  — 评分 {score}  净流入 {net/1e8:.2f}亿")
        lines.append("")
    else:
        lines.append("  （评分数据暂不可用）")
        lines.append("")

    # ==================== 三、推荐股票 ====================
    lines.append("---")
    lines.append("")
    lines.append("### ⭐ 三、精选推荐")
    lines.append("")
    if recommended:
        for i, s in enumerate(recommended[:10], 1):
            code = s.get('code', '')
            name = s.get('name', '')
            inflow = s.get('net_inflow', 0) / 1e8
            conf = s.get('confidence', '中')
            emoji = {1:'🥇', 2:'🥈', 3:'🥉'}.get(i, '🔹')
            lines.append(f"  {emoji} **{name}** ({code})  {inflow:.2f}亿  置信度 {conf}")
        lines.append("")
    else:
        lines.append("  （暂无推荐股票）")
        lines.append("")

    # ==================== 四、AI 分析师结论（去推理过程）====================
    # 每个分析师只取关键结论段，去掉漫长的推理过程
    ai_sections = [
        ('🎯', '游资行为', agents.get('youzi', {}).get('analysis', '')),
        ('💎', '个股潜力', agents.get('stock', {}).get('analysis', '')),
        ('🔥', '题材追踪', agents.get('theme', {}).get('analysis', '')),
        ('⚠️', '风险控制', agents.get('risk', {}).get('analysis', '')),
        ('🧠', '首席策略', agents.get('chief', {}).get('analysis', '')),
    ]

    for emoji, label, text in ai_sections:
        if not text:
            continue
        lines.append("---")
        lines.append("")
        lines.append(f"### {emoji} {label}")

        # 去掉推理过程（从【推理过程】到第一个 ### 或 --- 之间的内容）
        clean = _strip_reasoning(text)

        # 只取前 600 字符的核心结论
        if len(clean) > 600:
            clean = clean[:600] + "\n\n  ...（更多内容在完整报告中）"
        lines.append("")
        lines.append(clean)
        lines.append("")

    # ==================== 五、上榜类型分布 ====================
    top_youzi = summary.get('top_youzi', {})
    if top_youzi:
        lines.append("---")
        lines.append("")
        lines.append("### 📋 上榜类型分布（净买入 TOP）")
        lines.append("")
        for i, (name, amount) in enumerate(
            sorted(top_youzi.items(), key=lambda x: x[1], reverse=True)[:8], 1
        ):
            lines.append(f"  {i}. {name}: {amount/1e8:.2f}亿")
        lines.append("")

    # ==================== 六、摘要 ====================
    summary_text = result.get('final_report', {}).get('summary', '')
    if summary_text:
        lines.append("---")
        lines.append("")
        lines.append(f"**📝 核心摘要**: {summary_text}")
        lines.append("")

    lines.append("---")
    lines.append(f"_智瞰龙虎自动生成 | {ts}_")

    return '\n'.join(lines)


def _strip_reasoning(text: str) -> str:
    """
    去掉 AI 分析文本中的推理过程部分，保留核心分析结论。
    """
    import re
    # 去掉【推理过程】...到下一个标题/空行之间的内容
    text = re.sub(r'【推理过程】.*?(\n###|\n\*\*核心|\n———|\Z)', r'\1', text, flags=re.DOTALL)
    # 去掉开头的"嗯，用户"等推理叙述段落（到第一个换行符前）
    text = re.sub(r'^嗯.*?。\n\n', '', text, flags=re.DOTALL)
    text = re.sub(r'^.*?(?=好的，|各位投资者|首先，|【|###)', '', text, flags=re.DOTALL)
    # 去掉 AI 输出中的 markdown 分隔线（避免与消息自身的 --- 混淆）
    text = re.sub(r'\n---+\n', '\n\n', text)
    # 去掉表格（手机端显示差，只保留关键结论）
    text = re.sub(r'\|[^\n]+\|[^\n]*\n\|[:\-\s|]+\|[^\n]*(\n\|[^\n]+\|)*', '', text)
    # 清理多余空行
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


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
