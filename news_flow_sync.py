"""
新闻流量调度器 - 独立可执行
供 Windows 任务计划程序每 30 分钟调用一次

执行：python news_flow_sync.py

流程：
  0. guba_fetcher 拉个股情绪 (T2 数据源)  - 5000+ 股 关注指数 + 排名上升
  1. run_full_analysis()  - 18 平台爬取 + 流量/情绪计算 + DeepSeek AI 分析
                          + 4 层加权数据上下文 (T1-T4)
  2. run_alert_check()    - 再跑一次快分析 + 检查 6 种预警阈值

总耗时约 200s (30 分钟一次的频率完全可以接受)
"""
import os
import sys
import logging
from datetime import datetime

logging.basicConfig(level=logging.INFO, format='[%(asctime)s] %(levelname)s %(message)s')
logger = logging.getLogger('news_flow_sync')

# 切到脚本所在目录，保证 news_flow_* 模块可 import
os.chdir(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.getcwd())

# 智瞰龙虎推荐股代码 — 作为 AI 验证目标 (T2 验证段会查这些股的情绪)
# 默认从 longhubang.db 最新报告取 10 只推荐股
def get_longhubang_targets():
    """从 longhubang.db 最新报告取 10 只推荐股代码"""
    try:
        import sqlite3, json
        conn = sqlite3.connect('longhubang.db')
        row = conn.execute(
            'SELECT recommended_stocks FROM longhubang_analysis ORDER BY id DESC LIMIT 1'
        ).fetchone()
        conn.close()
        if not row or not row[0]:
            return []
        recs = json.loads(row[0])
        return [r['code'] for r in recs if r.get('code')][:10]
    except Exception as e:
        logger.warning(f'取智瞰龙虎推荐股失败: {e}')
        return []


try:
    overall_t0 = datetime.now()

    # ===== 步骤 0: 拉股吧情绪 (T2 数据源) =====
    logger.info('=' * 50)
    logger.info('步骤 0/3: guba_fetcher.fetch_all() — 5000+ 股 关注数据 (约 5s)')
    logger.info('=' * 50)
    t0 = datetime.now()
    try:
        from guba_fetcher import fetch_all as guba_fetch_all
        guba_fetch_all()
        logger.info(f'步骤0 完成: 耗时 {(datetime.now()-t0).total_seconds():.1f}s')
    except Exception as e:
        logger.warning(f'步骤0 失败 (降级继续): {e}')

    # 取智瞰龙虎推荐股作为 AI 验证目标
    target_codes = get_longhubang_targets()
    logger.info(f'  AI 验证目标: {len(target_codes)} 只智瞰龙虎推荐股')

    # ===== 步骤 1: 完整分析（含 AI + 4 层加权）=====
    logger.info('=' * 50)
    logger.info('步骤 1/3: run_full_analysis() — 18平台 + AI + 加权数据 (约 150s)')
    logger.info('=' * 50)
    t0 = datetime.now()
    from news_flow_engine import NewsFlowEngine
    engine = NewsFlowEngine()
    full_result = engine.run_full_analysis(target_codes=target_codes)
    elapsed = (datetime.now() - t0).total_seconds()
    if not full_result.get('success'):
        logger.error(f'run_full_analysis 失败: {full_result.get("error")}')
        sys.exit(1)
    snapshot_id = full_result.get('snapshot_id')
    ai = full_result.get('ai_analysis') or {}
    logger.info(f'步骤1 完成: 耗时 {elapsed:.1f}s, snapshot_id={snapshot_id}')
    logger.info(f'  AI 风险等级: {ai.get("risk_assess", {}).get("risk_level", "?")}')
    logger.info(f'  AI 建议: {ai.get("investment_advice", {}).get("advice", "?")}')

    # ===== 步骤 2: 预警检查 =====
    logger.info('=' * 50)
    logger.info('步骤 2/3: run_alert_check() — 检查 6 种预警阈值 (约 42s)')
    logger.info('=' * 50)
    t0 = datetime.now()
    alert_result = engine.run_alert_check()
    elapsed = (datetime.now() - t0).total_seconds()
    if not alert_result.get('success'):
        logger.error(f'run_alert_check 失败: {alert_result.get("error")}')
        sys.exit(1)
    n_alerts = len(alert_result.get('alerts', []))
    logger.info(f'步骤2 完成: 耗时 {elapsed:.1f}s, 触发 {n_alerts} 个预警')

    overall_elapsed = (datetime.now() - overall_t0).total_seconds()
    logger.info('=' * 50)
    logger.info(f'全部完成, 总耗时 {overall_elapsed:.1f}s')
    logger.info('=' * 50)
except Exception as e:
    logger.error(f'执行失败: {e}', exc_info=True)
    sys.exit(1)
