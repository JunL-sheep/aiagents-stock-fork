import sqlite3
import json
from datetime import datetime, timedelta
from typing import Dict, List, Optional
import os

class StockMonitorDatabase:
    """股票监测数据库管理类"""
    
    def __init__(self, db_path: str = "stock_monitor.db"):
        self.db_path = db_path
        # 确保数据库所在目录存在
        db_dir = os.path.dirname(self.db_path)
        if db_dir and not os.path.exists(db_dir):
            os.makedirs(db_dir, exist_ok=True)
        self.init_database()
    
    def init_database(self):
        """初始化数据库表结构"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # 创建监测股票表
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS monitored_stocks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                name TEXT NOT NULL,
                rating TEXT NOT NULL,
                entry_range TEXT NOT NULL,  -- JSON格式: {"min": 10.0, "max": 12.0}
                take_profit REAL,
                stop_loss REAL,
                current_price REAL,
                last_checked TIMESTAMP,
                check_interval INTEGER DEFAULT 1,  -- 分钟（默认 1 分钟，配合 60s 主循环实现近实时监测）
                notification_enabled BOOLEAN DEFAULT TRUE,
                trading_hours_only BOOLEAN DEFAULT TRUE,  -- 仅交易时段监控
                quant_enabled BOOLEAN DEFAULT FALSE,  -- 量化交易开关
                quant_config TEXT,  -- 量化配置JSON
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # 检查并添加trading_hours_only字段（兼容已有数据库）
        try:
            cursor.execute("SELECT trading_hours_only FROM monitored_stocks LIMIT 1")
        except sqlite3.OperationalError:
            cursor.execute("ALTER TABLE monitored_stocks ADD COLUMN trading_hours_only BOOLEAN DEFAULT TRUE")
            print("✅ 已添加trading_hours_only字段")

        # 检查并添加expires_at字段（TTL：7 天观察期）
        try:
            cursor.execute("SELECT expires_at FROM monitored_stocks LIMIT 1")
        except sqlite3.OperationalError:
            # 新列默认 = 创建时间 + 7 天；老记录按 created_at 兜底回填
            cursor.execute(
                "ALTER TABLE monitored_stocks ADD COLUMN expires_at TIMESTAMP"
            )
            cursor.execute('''
                UPDATE monitored_stocks
                SET expires_at = datetime(created_at, '+7 days')
                WHERE expires_at IS NULL
            ''')
            print("✅ 已添加expires_at字段并回填老记录")

        # 创建价格历史表
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS price_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                stock_id INTEGER,
                price REAL NOT NULL,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (stock_id) REFERENCES monitored_stocks (id)
            )
        ''')
        
        # 创建提醒记录表
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS notifications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                stock_id INTEGER,
                type TEXT NOT NULL,  -- entry/take_profit/stop_loss
                message TEXT NOT NULL,
                triggered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                sent BOOLEAN DEFAULT FALSE,
                FOREIGN KEY (stock_id) REFERENCES monitored_stocks (id)
            )
        ''')

        # 兼容已有库：补 notifications.context 列（DingTalk 模板行情/持仓快照）
        try:
            cursor.execute("SELECT context FROM notifications LIMIT 1")
        except sqlite3.OperationalError:
            cursor.execute("ALTER TABLE notifications ADD COLUMN context TEXT")
            print("✅ 已添加notifications.context字段")

        conn.commit()
        conn.close()
    
    def add_monitored_stock(self, symbol: str, name: str, rating: str,
                           entry_range: Dict, take_profit: float,
                           stop_loss: float, check_interval: int = 1,  # 默认 1 分钟（实时监测）
                           notification_enabled: bool = True,
                           trading_hours_only: bool = True,
                           quant_enabled: bool = False,
                           quant_config: Dict = None) -> int:
        """添加监测股票"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        quant_config_json = json.dumps(quant_config) if quant_config else None
        
        cursor.execute('''
            INSERT INTO monitored_stocks
            (symbol, name, rating, entry_range, take_profit, stop_loss, check_interval,
             notification_enabled, trading_hours_only, quant_enabled, quant_config,
             expires_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    datetime('now', '+7 days'))
        ''', (symbol, name, rating, json.dumps(entry_range), take_profit, stop_loss,
              check_interval, notification_enabled, trading_hours_only, quant_enabled, quant_config_json))
        
        stock_id = cursor.lastrowid
        conn.commit()
        conn.close()
        
        return stock_id
    
    def get_monitored_stocks(self) -> List[Dict]:
        """获取所有监测股票"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT id, symbol, name, rating, entry_range, take_profit, stop_loss,
                   current_price, last_checked, check_interval, notification_enabled,
                   trading_hours_only, quant_enabled, quant_config, created_at, updated_at,
                   expires_at
            FROM monitored_stocks
            ORDER BY created_at DESC
        ''')
        
        stocks = []
        for row in cursor.fetchall():
            try:
                quant_config = json.loads(row[13]) if row[13] else None
                entry_range = json.loads(row[4]) if row[4] else None
            except (json.JSONDecodeError, TypeError) as e:
                print(f"警告: 股票 {row[1]} 的JSON解析失败: {e}")
                entry_range = None
                quant_config = None
                
            stocks.append({
                'id': row[0],
                'symbol': row[1],
                'name': row[2],
                'rating': row[3],
                'entry_range': entry_range,
                'take_profit': row[5],
                'stop_loss': row[6],
                'current_price': row[7],
                'last_checked': row[8],
                'check_interval': row[9],
                'notification_enabled': bool(row[10]),
                'trading_hours_only': bool(row[11]) if row[11] is not None else True,
                'quant_enabled': bool(row[12]),
                'quant_config': quant_config,
                'created_at': row[14],
                'updated_at': row[15],
                'expires_at': row[16],
            })
        
        conn.close()
        return stocks
    
    def update_stock_price(self, stock_id: int, price: float):
        """更新股票价格"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # 更新当前价格
        cursor.execute('''
            UPDATE monitored_stocks 
            SET current_price = ?, last_checked = CURRENT_TIMESTAMP
            WHERE id = ?
        ''', (price, stock_id))
        
        # 记录价格历史
        cursor.execute('''
            INSERT INTO price_history (stock_id, price)
            VALUES (?, ?)
        ''', (stock_id, price))
        
        conn.commit()
        conn.close()
    
    def update_last_checked(self, stock_id: int):
        """仅更新最后检查时间（用于获取失败的情况）"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            UPDATE monitored_stocks 
            SET last_checked = CURRENT_TIMESTAMP
            WHERE id = ?
        ''', (stock_id,))
        
        conn.commit()
        conn.close()
    
    def has_recent_notification(self, stock_id: int, notification_type: str, minutes: int = 60) -> bool:
        """检查是否在最近X分钟内已有相同类型的通知"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT COUNT(*) FROM notifications
            WHERE stock_id = ? AND type = ?
            AND datetime(triggered_at) > datetime('now', '-' || ? || ' minutes')
        ''', (stock_id, notification_type, minutes))
        
        count = cursor.fetchone()[0]
        conn.close()
        
        return count > 0
    
    def add_notification(self, stock_id: int, notification_type: str, message: str,
                        context: Optional[Dict] = None):
        """添加提醒记录

        Args:
            stock_id: 监测股票 id
            notification_type: entry / take_profit / stop_loss / quant_trade
            message: 推送主消息（必填，给 UI 看）
            context: 行情/持仓快照（可选，给 DingTalk 模板用）
                    建议字段: current_price, change_pct, change_amount, volume,
                             turnover_rate, position_status, position_cost,
                             profit_loss_pct, trading_session
        """
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        context_json = json.dumps(context, ensure_ascii=False) if context else None

        cursor.execute('''
            INSERT INTO notifications (stock_id, type, message, context)
            VALUES (?, ?, ?, ?)
        ''', (stock_id, notification_type, message, context_json))

        conn.commit()
        conn.close()
    
    def get_pending_notifications(self) -> List[Dict]:
        """获取待发送的提醒（含 context 行情/持仓快照）"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute('''
            SELECT n.id, n.stock_id, s.symbol, s.name, n.type, n.message, n.triggered_at, n.context
            FROM notifications n
            JOIN monitored_stocks s ON n.stock_id = s.id
            WHERE n.sent = FALSE
            ORDER BY n.triggered_at
        ''')

        notifications = []
        for row in cursor.fetchall():
            ctx_raw = row[7]
            ctx = {}
            if ctx_raw:
                try:
                    ctx = json.loads(ctx_raw)
                except (json.JSONDecodeError, TypeError):
                    ctx = {}
            notifications.append({
                'id': row[0],
                'stock_id': row[1],
                'symbol': row[2],
                'name': row[3],
                'type': row[4],
                'message': row[5],
                'triggered_at': row[6],
                # 行情 + 持仓 + 时段快照（供 DingTalk 模板用）
                'current_price': ctx.get('current_price'),
                'change_pct': ctx.get('change_pct'),
                'change_amount': ctx.get('change_amount'),
                'volume': ctx.get('volume'),
                'turnover_rate': ctx.get('turnover_rate'),
                'position_status': ctx.get('position_status'),
                'position_cost': ctx.get('position_cost'),
                'profit_loss_pct': ctx.get('profit_loss_pct'),
                'trading_session': ctx.get('trading_session'),
            })

        conn.close()
        return notifications
    
    def get_all_recent_notifications(self, limit: int = 10) -> List[Dict]:
        """获取最近的所有通知（包括已发送和未发送的）"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT n.id, n.stock_id, s.symbol, s.name, n.type, n.message, n.triggered_at, n.sent
            FROM notifications n
            JOIN monitored_stocks s ON n.stock_id = s.id
            ORDER BY n.triggered_at DESC
            LIMIT ?
        ''', (limit,))
        
        notifications = []
        for row in cursor.fetchall():
            notifications.append({
                'id': row[0],
                'stock_id': row[1],
                'symbol': row[2],
                'name': row[3],
                'type': row[4],
                'message': row[5],
                'triggered_at': row[6],
                'sent': bool(row[7])
            })
        
        conn.close()
        return notifications
    
    def mark_notification_sent(self, notification_id: int):
        """标记提醒已发送"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            UPDATE notifications SET sent = TRUE WHERE id = ?
        ''', (notification_id,))
        
        conn.commit()
        conn.close()
    
    def mark_all_notifications_sent(self):
        """标记所有通知为已读"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('UPDATE notifications SET sent = TRUE WHERE sent = FALSE')
        
        conn.commit()
        conn.close()
        
        return cursor.rowcount
    
    def clear_all_notifications(self):
        """清空所有通知"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('DELETE FROM notifications')
        
        conn.commit()
        conn.close()
        
        return cursor.rowcount
    
    def remove_monitored_stock(self, stock_id: int):
        """移除监测股票"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            # 删除相关记录
            cursor.execute('DELETE FROM price_history WHERE stock_id = ?', (stock_id,))
            cursor.execute('DELETE FROM notifications WHERE stock_id = ?', (stock_id,))
            cursor.execute('DELETE FROM monitored_stocks WHERE id = ?', (stock_id,))
            
            affected_rows = cursor.rowcount
            conn.commit()
            conn.close()
            
            return affected_rows > 0
        except Exception as e:
            print(f"删除股票失败: {e}")
            return False
    
    def update_monitored_stock(self, stock_id: int, rating: str, entry_range: Dict, 
                              take_profit: float, stop_loss: float, 
                              check_interval: int, notification_enabled: bool,
                              trading_hours_only: bool = None,
                              quant_enabled: bool = None,
                              quant_config: Dict = None):
        """更新监测股票"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        if quant_enabled is not None and quant_config is not None:
            quant_config_json = json.dumps(quant_config) if quant_config else None
            trading_hours_sql = ", trading_hours_only = ?" if trading_hours_only is not None else ""
            params = [rating, json.dumps(entry_range), take_profit, stop_loss, 
                      check_interval, notification_enabled, quant_enabled, quant_config_json]
            if trading_hours_only is not None:
                params.append(trading_hours_only)
            params.append(stock_id)
            
            cursor.execute(f'''
                UPDATE monitored_stocks 
                SET rating = ?, entry_range = ?, take_profit = ?, stop_loss = ?, 
                    check_interval = ?, notification_enabled = ?, 
                    quant_enabled = ?, quant_config = ?{trading_hours_sql},
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
            ''', tuple(params))
        else:
            trading_hours_sql = ", trading_hours_only = ?" if trading_hours_only is not None else ""
            params = [rating, json.dumps(entry_range), take_profit, stop_loss, check_interval, notification_enabled]
            if trading_hours_only is not None:
                params.append(trading_hours_only)
            params.append(stock_id)
            
            cursor.execute(f'''
                UPDATE monitored_stocks 
                SET rating = ?, entry_range = ?, take_profit = ?, stop_loss = ?, 
                    check_interval = ?, notification_enabled = ?{trading_hours_sql}, 
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
            ''', tuple(params))
        
        conn.commit()
        conn.close()
        
        return cursor.rowcount > 0
    
    def toggle_notification(self, stock_id: int, enabled: bool):
        """切换通知状态"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            UPDATE monitored_stocks 
            SET notification_enabled = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
        ''', (enabled, stock_id))
        
        conn.commit()
        conn.close()
        
        return cursor.rowcount > 0
    
    def get_stock_by_id(self, stock_id: int) -> Optional[Dict]:
        """根据ID获取股票信息"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT id, symbol, name, rating, entry_range, take_profit, stop_loss,
                   current_price, last_checked, check_interval, notification_enabled,
                   trading_hours_only, quant_enabled, quant_config, expires_at
            FROM monitored_stocks WHERE id = ?
        ''', (stock_id,))
        
        row = cursor.fetchone()
        conn.close()
        
        if row:
            try:
                quant_config = json.loads(row[13]) if row[13] else None
                entry_range = json.loads(row[4]) if row[4] else None
            except (json.JSONDecodeError, TypeError) as e:
                print(f"警告: 股票 {row[1]} 的JSON解析失败: {e}")
                entry_range = None
                quant_config = None
                
            return {
                'id': row[0],
                'symbol': row[1],
                'name': row[2],
                'rating': row[3],
                'entry_range': entry_range,
                'take_profit': row[5],
                'stop_loss': row[6],
                'current_price': row[7],
                'last_checked': row[8],
                'check_interval': row[9],
                'notification_enabled': bool(row[10]),
                'trading_hours_only': bool(row[11]) if row[11] is not None else True,
                'quant_enabled': bool(row[12]),
                'quant_config': quant_config,
                'expires_at': row[14],
            }
        return None
    
    def get_monitor_by_code(self, symbol: str) -> Optional[Dict]:
        """
        根据股票代码获取监测信息
        
        Args:
            symbol: 股票代码
            
        Returns:
            监测股票信息字典，不存在则返回None
        """
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT * FROM monitored_stocks WHERE symbol = ?
        ''', (symbol,))

        row = cursor.fetchone()
        conn.close()

        if row:
            try:
                entry_range = json.loads(row[4]) if row[4] else None
                quant_config = json.loads(row[13]) if row[13] else None
            except (json.JSONDecodeError, TypeError) as e:
                print(f"警告: 股票 {row[1]} 的JSON解析失败: {e}")
                entry_range = None
                quant_config = None

            return {
                'id': row[0],
                'symbol': row[1],
                'name': row[2],
                'rating': row[3],
                'entry_range': entry_range,
                'take_profit': row[5],
                'stop_loss': row[6],
                'current_price': row[7],
                'last_checked': row[8],
                'check_interval': row[9],
                'notification_enabled': row[10],
                'trading_hours_only': bool(row[11]) if row[11] is not None else True,
                'quant_enabled': bool(row[12]),
                'quant_config': quant_config,
                'expires_at': row[16] if len(row) > 16 else None,
            }
        return None
    
    def batch_add_or_update_monitors(self, monitors_data: List[Dict]) -> Dict[str, int]:
        """
        批量添加或更新监测股票
        
        Args:
            monitors_data: 监测股票数据列表，每个字典包含：
                - code/symbol: 股票代码
                - name: 股票名称  
                - rating: 投资评级
                - entry_min, entry_max: 进场区间
                - take_profit: 止盈位
                - stop_loss: 止损位
                - check_interval: 检查间隔（可选，默认60秒）
                - notification_enabled: 是否启用通知（可选，默认True）
                
        Returns:
            统计字典 {"added": X, "updated": Y, "failed": Z, "total": N}
        """
        added = 0
        updated = 0
        failed = 0
        
        for data in monitors_data:
            try:
                # 兼容code和symbol两种字段名
                symbol = data.get('code') or data.get('symbol')
                name = data.get('name', symbol)
                rating = data.get('rating', '持有')
                entry_min = data.get('entry_min')
                entry_max = data.get('entry_max')
                take_profit = data.get('take_profit')
                stop_loss = data.get('stop_loss')
                check_interval = data.get('check_interval', 60)
                notification_enabled = data.get('notification_enabled', True)
                trading_hours_only = data.get('trading_hours_only', True)
                
                # 验证必需字段
                if not symbol or not all([entry_min, entry_max, take_profit, stop_loss]):
                    print(f"[WARN] {symbol} 参数不完整，跳过")
                    failed += 1
                    continue
                
                # 构建entry_range
                entry_range = {"min": entry_min, "max": entry_max}
                
                # 检查是否已存在
                existing = self.get_monitor_by_code(symbol)
                
                if existing:
                    # 更新现有监测
                    self.update_monitored_stock(
                        existing['id'],
                        rating=rating,
                        entry_range=entry_range,
                        take_profit=take_profit,
                        stop_loss=stop_loss,
                        check_interval=check_interval,
                        notification_enabled=notification_enabled,
                        trading_hours_only=trading_hours_only
                    )
                    updated += 1
                    print(f"[OK] 更新监测: {symbol}")
                else:
                    # 添加新监测
                    self.add_monitored_stock(
                        symbol=symbol,
                        name=name,
                        rating=rating,
                        entry_range=entry_range,
                        take_profit=take_profit,
                        stop_loss=stop_loss,
                        check_interval=check_interval,
                        notification_enabled=notification_enabled,
                        trading_hours_only=trading_hours_only
                    )
                    added += 1
                    print(f"[OK] 添加监测: {symbol}")
                    
            except Exception as e:
                symbol_str = data.get('code') or data.get('symbol', 'Unknown')
                print(f"[ERROR] 处理监测失败 ({symbol_str}): {str(e)}")
                failed += 1
        
        result = {
            "added": added,
            "updated": updated,
            "failed": failed,
            "total": added + updated + failed
        }

        print(f"\n[OK] 批量同步完成: 新增{added}只, 更新{updated}只, 失败{failed}只")
        return result

    # ========================================================================
    # TTL / 容量上限 / TDX 快路径 相关（2026-07-28 新增）
    # ========================================================================

    def purge_expired(self) -> int:
        """
        清理已过期（expires_at < NOW）的监测股票。
        返回删除的监测股票条数（不含关联的 price_history / notifications，由外键级联）。
        """
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('''
            DELETE FROM monitored_stocks
            WHERE expires_at IS NOT NULL AND datetime(expires_at) < datetime('now')
        ''')
        purged = cursor.rowcount
        conn.commit()
        conn.close()
        if purged:
            print(f"[monitor_db] purge_expired: 删除 {purged} 只过期监测股")
        return purged

    def upsert_with_ttl(self, monitors_data: List[Dict],
                         ttl_days: int = 7,
                         max_pool: int = 20) -> Dict[str, int]:
        """
        带 TTL 的批量 upsert（每日报告 → 监测池注入专用）。

        行为：
        1. 先 purge_expired() 清理过期股
        2. 对 monitors_data 里每只：
           - 已存在（按 symbol）：覆盖 entry_range / TP / SL / rating，
             同时刷新 expires_at = NOW + ttl_days（重置观察期）
           - 不存在：插入新行，expires_at = NOW + ttl_days
        3. upsert 后若池子仍 > max_pool，按 created_at 升序淘汰最旧的（直至 == max_pool）
           —— 新 upsert 的 created_at 是最新的，所以会保留；老非当日股先被踢。

        参数：
            monitors_data: 列表，每项至少含 symbol/code + entry_min/max + TP/SL
            ttl_days: 观察天数，默认 7
            max_pool: 监测池最大股票数，默认 20

        返回：
            {"added", "updated", "expired_purged", "evicted", "failed", "total"}
        """
        # 1. 先清理过期
        expired_purged = self.purge_expired()

        added = updated = failed = 0
        new_symbols = set()

        for data in monitors_data:
            try:
                symbol = data.get('code') or data.get('symbol')
                if not symbol:
                    failed += 1
                    continue

                name = data.get('name', symbol)
                rating = data.get('rating', '持有')
                entry_min = data.get('entry_min')
                entry_max = data.get('entry_max')
                take_profit = data.get('take_profit')
                stop_loss = data.get('stop_loss')
                check_interval = data.get('check_interval', 1)
                notification_enabled = data.get('notification_enabled', True)
                trading_hours_only = data.get('trading_hours_only', True)

                if not all([entry_min, entry_max, take_profit, stop_loss]):
                    print(f"[monitor_db] {symbol} 参数不完整，跳过")
                    failed += 1
                    continue

                entry_range = {"min": entry_min, "max": entry_max}
                existing = self.get_monitor_by_code(symbol)

                if existing:
                    self.update_monitored_stock(
                        existing['id'],
                        rating=rating,
                        entry_range=entry_range,
                        take_profit=take_profit,
                        stop_loss=stop_loss,
                        check_interval=check_interval,
                        notification_enabled=notification_enabled,
                        trading_hours_only=trading_hours_only,
                    )
                    # 刷新 expires_at（重置 7 天观察期）+ updated_at
                    conn = sqlite3.connect(self.db_path)
                    conn.execute('''
                        UPDATE monitored_stocks
                        SET expires_at = datetime('now', '+' || ? || ' days'),
                            updated_at = CURRENT_TIMESTAMP
                        WHERE id = ?
                    ''', (ttl_days, existing['id']))
                    conn.commit()
                    conn.close()
                    updated += 1
                    new_symbols.add(symbol)
                else:
                    stock_id = self.add_monitored_stock(
                        symbol=symbol,
                        name=name,
                        rating=rating,
                        entry_range=entry_range,
                        take_profit=take_profit,
                        stop_loss=stop_loss,
                        check_interval=check_interval,
                        notification_enabled=notification_enabled,
                        trading_hours_only=trading_hours_only,
                    )
                    # 给新行补 expires_at
                    conn = sqlite3.connect(self.db_path)
                    conn.execute('''
                        UPDATE monitored_stocks
                        SET expires_at = datetime('now', '+' || ? || ' days')
                        WHERE id = ?
                    ''', (ttl_days, stock_id))
                    conn.commit()
                    conn.close()
                    added += 1
                    new_symbols.add(symbol)

            except Exception as e:
                sym = data.get('code') or data.get('symbol', 'Unknown')
                print(f"[monitor_db] 处理 {sym} 失败: {e}")
                failed += 1

        # 3. 容量上限：淘汰最旧（保留今日 upsert 的）
        evicted = 0
        if max_pool and max_pool > 0:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute('SELECT COUNT(*) FROM monitored_stocks')
            total = cursor.fetchone()[0]
            if total > max_pool:
                over = total - max_pool
                # 按 created_at 升序取最早 N 行，剔除今日刚 upsert 的
                cursor.execute('''
                    SELECT id, symbol FROM monitored_stocks
                    WHERE symbol NOT IN ({})
                    ORDER BY created_at ASC
                    LIMIT ?
                '''.format(','.join('?' * len(new_symbols))), (*new_symbols, over))
                evict_ids = [r[0] for r in cursor.fetchall()]

                if evict_ids:
                    placeholders = ','.join('?' * len(evict_ids))
                    cursor.execute(
                        f'DELETE FROM price_history WHERE stock_id IN ({placeholders})',
                        evict_ids,
                    )
                    cursor.execute(
                        f'DELETE FROM notifications WHERE stock_id IN ({placeholders})',
                        evict_ids,
                    )
                    cursor.execute(
                        f'DELETE FROM monitored_stocks WHERE id IN ({placeholders})',
                        evict_ids,
                    )
                    evicted = cursor.rowcount
                else:
                    # 新 upsert 的 symbol 已占满了池子（不太可能），兜底按 created_at 踢
                    cursor.execute('''
                        SELECT id FROM monitored_stocks
                        ORDER BY created_at ASC
                        LIMIT ?
                    ''', (over,))
                    evict_ids = [r[0] for r in cursor.fetchall()]
                    placeholders = ','.join('?' * len(evict_ids))
                    cursor.execute(
                        f'DELETE FROM monitored_stocks WHERE id IN ({placeholders})',
                        evict_ids,
                    )
                    evicted = cursor.rowcount

                print(f"[monitor_db] 监测池超限，淘汰最早 {evicted} 只")

            conn.commit()
            conn.close()

        result = {
            "added": added,
            "updated": updated,
            "expired_purged": expired_purged,
            "evicted": evicted,
            "failed": failed,
            "total": added + updated + failed,
        }
        print(
            f"[monitor_db] upsert_with_ttl: +{added} / ~{updated} / "
            f"过期清理 {expired_purged} / 容量淘汰 {evicted} / 失败 {failed}"
        )
        return result

    def update_current_price_only(self, stock_id: int, price: float):
        """
        秒级 tick 用：只更新 current_price + last_checked，不写 price_history。
        避免 20 只 × 1Hz = 20 行/秒 把历史表写爆（history 由调用方按 60s 节流）。
        """
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('''
            UPDATE monitored_stocks
            SET current_price = ?, last_checked = CURRENT_TIMESTAMP
            WHERE id = ?
        ''', (price, stock_id))
        conn.commit()
        conn.close()

    def get_a_share_stocks_for_fast_poll(self, cap: int = 20) -> List[Dict]:
        """
        取所有未过期的 A 股监测股票（symbol 为 6 位数字），
        供 monitor_service 的 TDX 秒级快路径使用。
        包含 expires_at / created_at 字段。
        """
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('''
            SELECT id, symbol, name, rating, entry_range, take_profit, stop_loss,
                   current_price, last_checked, check_interval, notification_enabled,
                   trading_hours_only, quant_enabled, quant_config,
                   created_at, updated_at, expires_at
            FROM monitored_stocks
            WHERE (expires_at IS NULL OR datetime(expires_at) >= datetime('now'))
              AND length(symbol) = 6
            ORDER BY
              CASE WHEN expires_at IS NULL THEN 1 ELSE 0 END,
              expires_at ASC
            LIMIT ?
        ''', (cap,))

        stocks = []
        for row in cursor.fetchall():
            try:
                entry_range = json.loads(row[4]) if row[4] else None
                quant_config = json.loads(row[13]) if row[13] else None
            except (json.JSONDecodeError, TypeError):
                entry_range = None
                quant_config = None
            stocks.append({
                'id': row[0],
                'symbol': row[1],
                'name': row[2],
                'rating': row[3],
                'entry_range': entry_range,
                'take_profit': row[5],
                'stop_loss': row[6],
                'current_price': row[7],
                'last_checked': row[8],
                'check_interval': row[9],
                'notification_enabled': bool(row[10]),
                'trading_hours_only': bool(row[11]) if row[11] is not None else True,
                'quant_enabled': bool(row[12]),
                'quant_config': quant_config,
                'created_at': row[14],
                'updated_at': row[15],
                'expires_at': row[16],
            })
        conn.close()
        return stocks

    def get_pool_stats(self) -> Dict:
        """返回监测池统计：总数 / 活跃 / 即将过期（<1 天）"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('SELECT COUNT(*) FROM monitored_stocks')
        total = cursor.fetchone()[0]
        cursor.execute('''
            SELECT COUNT(*) FROM monitored_stocks
            WHERE expires_at IS NULL OR datetime(expires_at) >= datetime('now')
        ''')
        active = cursor.fetchone()[0]
        cursor.execute('''
            SELECT COUNT(*) FROM monitored_stocks
            WHERE expires_at IS NOT NULL
              AND datetime(expires_at) >= datetime('now')
              AND datetime(expires_at) <= datetime('now', '+1 day')
        ''')
        expiring_soon = cursor.fetchone()[0]
        conn.close()
        return {
            'total': total,
            'active': active,
            'expiring_soon': expiring_soon,
        }

# 全局数据库实例
monitor_db = StockMonitorDatabase()