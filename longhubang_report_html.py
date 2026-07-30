"""
龙虎榜 HTML 报告生成器
生成完整的自包含 HTML 报告，包含所有 AI 分析师完整分析内容。
"""

import os
import json
from datetime import datetime


REPORTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'reports')


def _ensure_reports_dir():
    """确保 reports 目录存在"""
    os.makedirs(REPORTS_DIR, exist_ok=True)


def generate_html_report(result: dict) -> str:
    """
    根据 run_comprehensive_analysis 的完整结果，生成 HTML 报告。

    Args:
        result: run_comprehensive_analysis 的返回字典

    Returns:
        str: HTML 文件路径
    """
    _ensure_reports_dir()

    ts = result.get('timestamp', datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
    date_str = ts[:10]
    report_id = result.get('report_id', 'N/A')

    data_info = result.get('data_info', {})
    summary = data_info.get('summary', {})
    agents = result.get('agents_analysis', {})
    recommended = result.get('recommended_stocks', [])
    scoring = result.get('scoring_ranking', [])
    final_report = result.get('final_report', {})

    # === 构建 HTML ===
    sections = []

    # 样式
    sections.append("""
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { font-family: -apple-system, 'Microsoft YaHei', 'PingFang SC', sans-serif;
               background: #f5f6fa; color: #2d3436; line-height: 1.7; }
        .container { max-width: 900px; margin: 0 auto; padding: 20px; }
        .header { background: linear-gradient(135deg, #2d3436, #636e72);
                  color: #fff; padding: 40px; border-radius: 12px; margin-bottom: 24px; }
        .header h1 { font-size: 26px; margin-bottom: 8px; }
        .header .meta { opacity: 0.8; font-size: 14px; }
        .stats-row { display: flex; gap: 12px; flex-wrap: wrap; margin: 20px 0; }
        .stat-card { background: #fff; padding: 20px; border-radius: 10px; flex: 1;
                      min-width: 140px; box-shadow: 0 2px 8px rgba(0,0,0,0.06); }
        .stat-card .num { font-size: 28px; font-weight: 700; color: #0984e3; }
        .stat-card .label { font-size: 13px; color: #636e72; margin-top: 4px; }
        .section { background: #fff; border-radius: 12px; padding: 28px; margin-bottom: 20px;
                    box-shadow: 0 2px 8px rgba(0,0,0,0.06); }
        .section h2 { font-size: 20px; margin-bottom: 16px; padding-bottom: 8px;
                       border-bottom: 3px solid #0984e3; display: flex; align-items: center; gap: 8px; }
        .section h3 { font-size: 16px; margin: 16px 0 8px; color: #2d3436; }
        .rank-item { display: flex; align-items: center; padding: 10px 0;
                      border-bottom: 1px solid #f0f0f0; }
        .rank-item:last-child { border-bottom: none; }
        .rank-num { width: 36px; height: 36px; border-radius: 50%; display: flex;
                     align-items: center; justify-content: center; font-weight: 700;
                     font-size: 14px; margin-right: 14px; flex-shrink: 0; }
        .rank-1 { background: #ffeaa7; color: #d68910; }
        .rank-2 { background: #dfe6e9; color: #636e72; }
        .rank-3 { background: #fadbd8; color: #c0392b; }
        .rank-other { background: #f0f0f0; color: #636e72; }
        .stock-name { font-weight: 600; font-size: 15px; }
        .stock-code { color: #636e72; font-size: 13px; margin-left: 6px; }
        .stock-meta { margin-left: auto; text-align: right; font-size: 13px; color: #636e72; }
        .ai-content { white-space: pre-wrap; font-size: 14px; line-height: 1.8;
                       color: #2d3436; }
        .ai-content p { margin-bottom: 12px; }
        .badge { display: inline-block; padding: 2px 10px; border-radius: 12px;
                  font-size: 12px; font-weight: 600; }
        .badge-blue { background: #dfe6fd; color: #0984e3; }
        .badge-green { background: #d5f5e3; color: #27ae60; }
        .badge-red { background: #fadbd8; color: #e74c3c; }
        .badge-orange { background: #fdebd0; color: #e67e22; }
        .footer { text-align: center; padding: 20px; color: #b2bec3; font-size: 12px; }
        table { width: 100%; border-collapse: collapse; margin: 12px 0; font-size: 14px; }
        th, td { padding: 10px 12px; text-align: left; border-bottom: 1px solid #f0f0f0; }
        th { background: #f8f9fa; font-weight: 600; color: #636e72; font-size: 13px; }
        .rec-card { display: flex; align-items: center; padding: 14px; margin: 8px 0;
                     background: #f8f9fa; border-radius: 8px; border-left: 4px solid #0984e3; }
        .rec-card .rank { font-weight: 700; font-size: 18px; width: 36px; text-align: center; }
        .rec-card .info { flex: 1; margin-left: 14px; }
        .rec-card .info .name { font-weight: 600; }
        .rec-card .info .detail { font-size: 13px; color: #636e72; }
        .rec-card .inflow { font-weight: 600; color: #e74c3c; }
        @media (max-width: 600px) {
            .container { padding: 12px; }
            .header { padding: 24px; }
            .section { padding: 18px; }
            .stat-card { min-width: 100px; padding: 14px; }
            .stat-card .num { font-size: 22px; }
        }
    </style>
    """)

    # === 头部 ===
    net_inflow = summary.get('total_net_inflow', 0)
    inflow_color = '#e74c3c' if net_inflow >= 0 else '#27ae60'
    inflow_text = f"+{net_inflow/1e8:.2f}亿" if net_inflow >= 0 else f"{net_inflow/1e8:.2f}亿"

    sections.append(f"""
    <div class="header">
        <h1>📊 智瞰龙虎 · 盘后全量报告</h1>
        <div class="meta">
            <span>📅 {ts}</span> &nbsp;|&nbsp;
            <span>🆔 报告 #{report_id}</span> &nbsp;|&nbsp;
            <span>📋 数据来源: 东方财富 龙虎榜</span>
        </div>
        <div class="stats-row">
            <div class="stat-card">
                <div class="num">{summary.get('total_records', 0)}</div>
                <div class="label">📄 龙虎榜记录</div>
            </div>
            <div class="stat-card">
                <div class="num">{summary.get('total_stocks', 0)}</div>
                <div class="label">📈 涉及个股</div>
            </div>
            <div class="stat-card">
                <div class="num">{summary.get('total_youzi', 0)}</div>
                <div class="label">🎯 游资席位</div>
            </div>
            <div class="stat-card">
                <div class="num" style="color:{inflow_color}">{inflow_text}</div>
                <div class="label">💰 净买入总额</div>
            </div>
            <div class="stat-card">
                <div class="num">{len(recommended)}</div>
                <div class="label">⭐ 推荐股票</div>
            </div>
        </div>
    </div>
    """)

    # === AI 评分排名 ===
    if scoring:
        sections.append('<div class="section"><h2>🏆 AI 智能评分排名 TOP10</h2>')
        sections.append('<table><tr><th>#</th><th>股票</th><th>评分</th><th>净流入</th><th>游资关注</th></tr>')
        for i, s in enumerate(scoring[:10], 1):
            name = s.get('股票名称', '?')
            code = s.get('股票代码', '')
            score = s.get('综合评分', 0)
            net = s.get('净流入', 0) / 1e8
            youzi = s.get('顶级游资', s.get('买方数', 0))
            sections.append(f'<tr><td>{i}</td><td><strong>{name}</strong><br><span style="color:#636e72;font-size:12px">{code}</span></td>'
                          f'<td><span class="badge badge-blue">{score}</span></td>'
                          f'<td style="color:{chr(35)+"e74c3c" if net>=0 else chr(35)+"27ae60"}">{net:.2f}亿</td>'
                          f'<td>{youzi}</td></tr>')
        sections.append('</table></div>')

    # === 精选推荐 ===
    if recommended:
        sections.append('<div class="section"><h2>⭐ 精选推荐</h2>')
        for i, s in enumerate(recommended[:10], 1):
            code = s.get('code', '')
            name = s.get('name', '')
            inflow = s.get('net_inflow', 0) / 1e8
            conf = s.get('confidence', '中')
            reason = s.get('reason', '')
            medal = {1:'🥇', 2:'🥈', 3:'🥉'}.get(i, '🔹')
            sections.append(f'''
            <div class="rec-card">
                <div class="rank">{medal}</div>
                <div class="info">
                    <div class="name">{name} <span style="color:#636e72;font-size:13px">({code})</span></div>
                    <div class="detail">{reason}</div>
                </div>
                <div class="inflow">{inflow:.2f}亿</div>
            </div>''')
        sections.append('</div>')

    # === AI 分析师完整报告 ===
    ai_labels = [
        ('🎯', '游资行为分析', 'youzi'),
        ('💎', '个股潜力分析', 'stock'),
        ('🔥', '题材追踪分析', 'theme'),
        ('⚠️', '风险控制报告', 'risk'),
        ('🧠', '首席策略师综合判断', 'chief'),
    ]

    for emoji, label, key in ai_labels:
        agent = agents.get(key, {})
        text = agent.get('analysis', '')
        if not text:
            continue
        # 只去掉【推理过程】段落，保留全部核心内容
        import re
        clean = re.sub(r'【推理过程】.*?\n(###|\*\*核心|\Z)', r'\1', text, flags=re.DOTALL)
        clean = re.sub(r'^嗯.*?。\n\n', '', clean, flags=re.DOTALL)
        # HTML 换行转 <br>，段落转 <p>
        paragraphs = [p.strip() for p in clean.split('\n') if p.strip()]
        html_body = ''
        for p in paragraphs:
            if re.match(r'^#{1,3}\s', p):
                # markdown 标题
                level = len(re.match(r'^(#+)', p).group(1))
                text_only = re.sub(r'^#+\s*', '', p)
                html_body += f'<h{"3" if level>=3 else "2"}>{text_only}</h{"3" if level>=3 else "2"}>\n'
            elif re.match(r'^\*\*.*\*\*$', p):
                html_body += f'<p><strong>{p.strip("*")}</strong></p>\n'
            else:
                html_body += f'<p>{p}</p>\n'
        sections.append(f'<div class="section"><h2>{emoji} {label}</h2><div class="ai-content">{html_body}</div></div>')

    # === 上榜类型分布 ===
    top_categories = summary.get('top_youzi', {})
    if top_categories:
        sections.append('<div class="section"><h2>📋 上榜类型分布（净买入 TOP）</h2><table><tr><th>#</th><th>榜单类型</th><th>净买入额</th></tr>')
        for i, (name, amount) in enumerate(sorted(top_categories.items(), key=lambda x: x[1], reverse=True)[:10], 1):
            sections.append(f'<tr><td>{i}</td><td>{name}</td><td style="color:{chr(35)+"e74c3c"}">{amount/1e8:.2f}亿</td></tr>')
        sections.append('</table></div>')

    # === 摘要 ===
    summary_text = final_report.get('summary', '')
    if summary_text:
        sections.append(f'<div class="section"><h2>📝 核心摘要</h2><p>{summary_text}</p></div>')

    sections.append(f'<div class="footer">📊 智瞰龙虎自动生成 | {ts}</div>')

    # === 拼装完整 HTML ===
    html = f'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>智瞰龙虎 · 盘后全量报告 {date_str}</title>
    {''.join(sections)}
</head>
<body>
    <div class="container">
        {''.join(sections)}
    </div>
</body>
</html>'''

    # 修复 sections 重复问题 - 上面已经作为 body 内容了
    html = f'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>智瞰龙虎 · 盘后全量报告 {date_str}</title>
    {sections[0]}
</head>
<body>
    <div class="container">
        {''.join(sections[1:])}
    </div>
</body>
</html>'''

    # 写文件
    filename = f'longhubang_report_{date_str}_#{report_id}.html'
    filepath = os.path.join(REPORTS_DIR, filename)
    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(html)

    print(f'📄 HTML 报告已生成: {filepath}')
    return filepath
