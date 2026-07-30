"""
报告 HTTP 服务器
轻量级后台服务，用于在手机/平板上查看生成的 HTML 报告。

启动方式:
    python report_server.py              # 前台运行
    python report_server.py --daemon     # 后台运行（Windows）

访问方式:
    http://电脑IP:8765/                    # 报告列表
    http://电脑IP:8765/xxx.html           # 具体报告

关闭方式:
    python report_server.py --stop
"""

import os
import sys
import socket
import json
import subprocess
import time
from http.server import HTTPServer, SimpleHTTPRequestHandler
from datetime import datetime

REPORTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'reports')
PORT = 8765
PID_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.report_server.pid')


def get_lan_ip() -> str:
    """获取本机局域网 IP（用于手机访问）"""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(('8.8.8.8', 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return '127.0.0.1'


def get_server_url(report_filename: str = '') -> str:
    """获取报告可访问 URL"""
    ip = get_lan_ip()
    base = f'http://{ip}:{PORT}'
    if report_filename:
        return f'{base}/{report_filename}'
    return base


class ReportHandler(SimpleHTTPRequestHandler):
    """自定义报告目录的 HTTP 处理器"""

    def __init__(self, *args, **kwargs):
        # 确保 reports 目录存在
        os.makedirs(REPORTS_DIR, exist_ok=True)
        super().__init__(*args, directory=REPORTS_DIR, **kwargs)

    def log_message(self, format, *args):
        print(f'[报告服务器 {datetime.now().strftime("%H:%M:%S")}] {args[0]} {args[1]} {args[2]}')


def start_server(daemon: bool = False):
    """启动 HTTP 服务器"""
    os.makedirs(REPORTS_DIR, exist_ok=True)

    if daemon:
        # 后台启动
        if is_running():
            print(f'✅ 报告服务器已在运行: {get_server_url()}')
            return
        # 用 subprocess 启动自身（前台模式），写入 PID 文件
        proc = subprocess.Popen(
            [sys.executable, __file__],
            stdout=open(os.devnull, 'w'),
            stderr=open(os.devnull, 'w'),
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0,
        )
        with open(PID_FILE, 'w') as f:
            f.write(str(proc.pid))
        time.sleep(1)
        if is_running():
            print(f'✅ 报告服务器已启动: {get_server_url()}')
        else:
            print('❌ 报告服务器启动失败')
        return

    # 前台运行
    server = HTTPServer(('0.0.0.0', PORT), ReportHandler)
    print(f'📄 报告服务器运行中: {get_server_url()}')
    print(f'   按 Ctrl+C 停止')
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()
        print('\n⏹ 报告服务器已停止')


def stop_server():
    """停止服务器"""
    if not os.path.exists(PID_FILE):
        print('❌ 报告服务器未在运行')
        return
    with open(PID_FILE) as f:
        pid = int(f.read().strip())
    try:
        if sys.platform == 'win32':
            subprocess.run(['taskkill', '/F', '/PID', str(pid)], capture_output=True)
        else:
            os.kill(pid, 15)
        os.remove(PID_FILE)
        print('✅ 报告服务器已停止')
    except Exception as e:
        print(f'❌ 停止失败: {e}')
        os.remove(PID_FILE)


def is_running() -> bool:
    """检查服务器是否在运行"""
    if not os.path.exists(PID_FILE):
        return False
    with open(PID_FILE) as f:
        pid = f.read().strip()
    try:
        os.kill(int(pid), 0)
        return True
    except Exception:
        os.remove(PID_FILE)
        return False


def ensure_running():
    """确保服务器在运行，不在则启动"""
    if not is_running():
        start_server(daemon=True)


if __name__ == '__main__':
    if '--stop' in sys.argv:
        stop_server()
    elif '--daemon' in sys.argv:
        start_server(daemon=True)
    else:
        start_server(daemon=False)
