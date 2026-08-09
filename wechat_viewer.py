# -*- coding: utf-8 -*-
r"""
响应消息 · 聊天记录查看器（整合版）：
  - 打开后选「聊天记录 html 所在文件夹」，应用自行解析生成数据（不再需要单独跑 build_wechat.py）
  - 图片处理方式解析时选择：在线（保留云端链接）/ 下载到本地（可离线）
  - 设置与数据（data.js、html\images、收藏）都放在程序所在文件夹
  - 本地 HTTP 服务解决相对路径；js_api 处理选文件夹/扫描进度/收藏读写

部署：本文件与 ChatRecords.html 放在同一文件夹（data.js、html\images 由程序生成）。
打包：pyinstaller --onefile --windowed --name ChatRecords wechat_viewer.py
"""
import functools
import http.server
import json
import os
import shutil
import socket
import socketserver
import ssl
import subprocess
import sys
import tempfile
import threading
import urllib.parse
import urllib.request

import webview

import chat_parse


def ensure_webview2():
    """检查 WebView2 Runtime；缺失则自动下载并静默安装 Evergreen 引导安装器。"""
    try:
        import winreg
    except ImportError:
        return True

    def present():
        keys = [
            (winreg.HKEY_LOCAL_MACHINE, r'SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}'),
            (winreg.HKEY_CURRENT_USER, r'SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}'),
        ]
        for hkey, sub in keys:
            try:
                with winreg.OpenKey(hkey, sub) as k:
                    v, _ = winreg.QueryValueEx(k, 'pv')
                    if v:
                        return True
            except OSError:
                pass
        return False

    if present():
        return True
    try:
        dst = os.path.join(tempfile.gettempdir(), 'MicrosoftEdgeWebview2Setup.exe')
        if not (os.path.exists(dst) and os.path.getsize(dst) > 100000):
            req = urllib.request.Request(
                'https://go.microsoft.com/fwlink/p/?LinkId=2124703',
                headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=180) as r, open(dst, 'wb') as f:
                f.write(r.read())
        subprocess.run([dst, '/silent', '/install'], timeout=600)
        return present()
    except Exception:
        return False


def app_dir():
    # 打包后为 exe 所在目录；源码运行则为脚本所在目录
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


APP_DIR = app_dir()
APP_VERSION = '1.0.0'
SETTINGS_FILE = os.path.join(APP_DIR, 'settings.json')
DATA_FILE = os.path.join(APP_DIR, 'wxchat_data.json')
DATA_JS = os.path.join(APP_DIR, 'data.js')
HTML_FILE = 'ChatRecords.html'
MEDIA_ROOT = os.path.join(APP_DIR, 'html')  # 图片/语音下载到 html\images、html\audio


def load_settings():
    try:
        with open(SETTINGS_FILE, encoding='utf-8') as f:
            s = json.load(f)
        return {
            'records_folder': str(s.get('records_folder') or ''),
            'image_mode': str(s.get('image_mode') or 'online'),
        }
    except Exception:
        return {'records_folder': '', 'image_mode': 'online'}


def save_settings(settings):
    try:
        with open(SETTINGS_FILE, 'w', encoding='utf-8') as f:
            json.dump(settings, f, ensure_ascii=False, indent=2)
        return True
    except Exception:
        return False


class Api:
    """暴露给 JS 的接口：window.pywebview.api.xxx()"""

    def __init__(self):
        self.settings = load_settings()
        self.state = {
            'running': False, 'phase': '', 'done': True, 'ok': False,
            'done_count': 0, 'total': 0, 'ok_count': 0, 'fail_count': 0,
            'conversations': 0, 'messages': 0, 'folder': '', 'image_mode': '', 'error': '',
        }

    def get_settings(self):
        s = dict(self.settings)
        s['version'] = APP_VERSION
        return s

    def get_progress(self):
        return dict(self.state)

    def estimate(self, folder):
        """估算本地化所需空间 + 当前磁盘剩余。"""
        try:
            est = chat_parse.estimate_storage(folder)
            if est.get('error'):
                return est
            usage = shutil.disk_usage(APP_DIR)
            est['disk_free'] = usage.free
            est['disk_free_after'] = usage.free - est['needed_bytes']
            return est
        except Exception as e:
            return {'error': str(e)}

    def pick_folder(self):
        try:
            # 注意：不要给 Api 存 window 属性（pywebview 5.4 的 js_api 内省会递归崩溃），
            # 用模块级 webview.windows 访问窗口。
            win = webview.windows[0] if webview.windows else None
            if win is None:
                return {'error': '窗口未就绪'}
            result = win.create_file_dialog(webview.FOLDER_DIALOG)
        except Exception as e:
            return {'error': str(e)}
        if result and result[0]:
            return {'folder': os.path.normpath(result[0])}
        return {'canceled': True}

    def scan(self, folder, image_mode):
        folder = (folder or '').strip()
        if not os.path.isdir(folder):
            self.state.update({'done': True, 'ok': False, 'error': '文件夹不存在'})
            return {'error': '文件夹不存在'}
        if image_mode not in ('online', 'local'):
            image_mode = 'online'

        self.state.update({
            'running': True, 'phase': 'parsing', 'done': False, 'ok': False,
            'done_count': 0, 'total': 0, 'ok_count': 0, 'fail_count': 0,
            'conversations': 0, 'messages': 0, 'error': '',
            'folder': folder, 'image_mode': image_mode,
        })

        def work():
            try:
                def on_progress(done, total, ok, fail):
                    self.state.update({
                        'phase': 'download', 'done_count': done, 'total': total,
                        'ok_count': ok, 'fail_count': fail,
                    })

                content, stats = chat_parse.generate_data_js(
                    folder, image_mode=image_mode, local_root=MEDIA_ROOT,
                    ffmpeg=chat_parse.get_ffmpeg(), on_progress=on_progress)
                if content is None:
                    self.state.update({'done': True, 'ok': False,
                                       'error': stats.get('error', '解析失败')})
                    return
                self.state.update({'phase': 'writing'})
                with open(DATA_JS, 'w', encoding='utf-8') as f:
                    f.write(content)
                self.settings['records_folder'] = folder
                self.settings['image_mode'] = image_mode
                save_settings(self.settings)
                self.state.update({
                    'done': True, 'ok': True,
                    'conversations': stats.get('conversations', 0),
                    'messages': stats.get('messages', 0),
                })
            except Exception as e:
                self.state.update({'done': True, 'ok': False, 'error': str(e)})
            finally:
                self.state.update({'running': False, 'phase': ''})

        threading.Thread(target=work, daemon=True).start()
        return {'started': True}

    # ---- 收藏持久化 ----
    def read_data(self):
        try:
            with open(DATA_FILE, 'r', encoding='utf-8') as f:
                return f.read()
        except Exception:
            return None

    def write_data(self, text):
        try:
            with open(DATA_FILE, 'w', encoding='utf-8') as f:
                f.write(text or '')
            return True
        except Exception:
            return False


class ReuseTCPServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True


class NoCacheHandler(http.server.SimpleHTTPRequestHandler):
    """本地服务不缓存，保证重新生成后 data.js / 页面总是最新的。"""

    def end_headers(self):
        try:
            self.send_header('Cache-Control', 'no-store')
        except Exception:
            pass
        super().end_headers()

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == '/amr_proxy':
            # 代理下载云端 AMR 音频：浏览器 fetch 云端受跨域限制，改由本地服务中转
            url = urllib.parse.parse_qs(parsed.query).get('u', [''])[0]
            if not url or not url.startswith('http'):
                self.send_response(400)
                self.end_headers()
                return
            try:
                ctx = ssl.create_default_context()
                ctx.check_hostname = False
                ctx.verify_mode = ssl.CERT_NONE
                req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
                with urllib.request.urlopen(req, timeout=30, context=ctx) as r:
                    data = r.read()
                self.send_response(200)
                self.send_header('Content-Type', 'audio/amr')
                self.send_header('Content-Length', str(len(data)))
                self.send_header('Cache-Control', 'no-store')
                self.end_headers()
                self.wfile.write(data)
            except Exception:
                self.send_response(502)
                self.send_header('Content-Length', '0')
                self.end_headers()
            return
        super().do_GET()


def find_free_port(start=8765):
    for port in range(start, start + 50):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(('127.0.0.1', port))
                return port
            except OSError:
                continue
    return start


def main():
    if not os.path.exists(os.path.join(APP_DIR, HTML_FILE)):
        print('未找到', HTML_FILE, '请把本程序与 ChatRecords.html 放在同一文件夹。')
        input('按回车退出...')
        sys.exit(1)

    api = Api()

    # 若已配置过记录文件夹，后台自动扫描（前端会轮询进度并刷新）
    if api.settings.get('records_folder') and os.path.isdir(api.settings['records_folder']):
        api.scan(api.settings['records_folder'], api.settings.get('image_mode', 'online'))

    port = find_free_port()
    handler = functools.partial(NoCacheHandler, directory=APP_DIR)
    httpd = ReuseTCPServer(('127.0.0.1', port), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()

    # 若缺 WebView2 Runtime，尝试自动下载安装；失败则提示后退出
    if not ensure_webview2():
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(
                0,
                '缺少 WebView2 Runtime，且自动安装失败。\n请联网后重新启动应用，或手动安装后重试：\nhttps://developer.microsoft.com/microsoft-edge/webview2/',
                '响应消息', 0x10)
        except Exception:
            pass
        sys.exit(1)

    url = 'http://127.0.0.1:%d/%s' % (port, urllib.parse.quote(HTML_FILE))
    webview.create_window('响应消息', url, js_api=api, width=1180, height=800, min_size=(900, 600))

    def on_started():
        # pywebview 窗口默认不继承 exe 图标，这里显式设置任务栏/窗口图标。
        # 先设 WinForms Form.Icon，再用 WM_SETICON 直接改 HWND 图标（双保险）。
        try:
            import ctypes
            import clr
            clr.AddReference('System.Drawing')
            from System.Drawing import Icon
            icon_path = os.path.join(APP_DIR, 'icon.ico')
            icon = Icon(icon_path) if os.path.exists(icon_path) else Icon.ExtractAssociatedIcon(sys.executable)
            win = webview.windows[0] if webview.windows else None
            if win is None:
                return
            native = getattr(win, 'native', None)
            if native is None:
                return
            try:
                native.Icon = icon
            except Exception:
                pass
            try:
                hwnd = int(native.Handle.ToInt64())
                WM_SETICON = 0x0080
                ICON_BIG = 1
                ICON_SMALL = 0
                hIcon = int(icon.Handle.ToInt64())
                ctypes.windll.user32.SendMessageW(hwnd, WM_SETICON, ICON_BIG, hIcon)
                ctypes.windll.user32.SendMessageW(hwnd, WM_SETICON, ICON_SMALL, hIcon)
            except Exception:
                pass
        except Exception:
            pass

    webview.start(on_started)


if __name__ == '__main__':
    main()
