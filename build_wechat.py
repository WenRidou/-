# -*- coding: utf-8 -*-
"""
把"响应"的聊天记录导出 html 变成微信版查看器可用的 data.js（可分享给同学）。

用法：
    python build_wechat.py [聊天记录html所在的文件夹]

运行时会先询问：
    1) 保留云端链接 —— 图片/语音仍指向网页链接，文件小，需联网查看
    2) 下载到本地 —— 把图片/语音下载到本地，可离线查看（需足够磁盘空间，会先确认）

完成后：
    把 聊天记录.html 复制到与 data.js 相同的文件夹，双击打开即可。
    若选了下载，图片/语音会存到该文件夹下 html\\images 和 html\\audio（与查看器的 html/ 前缀对应）。
"""
import glob, json, os, re, shutil, ssl, subprocess, sys, urllib.request
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.stdout.reconfigure(encoding='utf-8')

BS = chr(92)  # 反斜杠
EXT_IMG = {'jpg','jpeg','png','gif','webp','bmp'}
EXT_AUD = {'amr','mp3','wav','m4a','aac','ogg','mpeg'}

# ---------------- 路径（脚本放哪都行，可命令行指定） ----------------
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

if len(sys.argv) > 1:
    # 命令行指定来源文件夹
    HTML_DIR = os.path.abspath(sys.argv[1])
    LOCAL_ROOT = os.path.join(HTML_DIR, 'html')
    DATA_JS = os.path.join(HTML_DIR, 'data.js')
else:
    share_html = os.path.join(SCRIPT_DIR, 'html')
    if glob.glob(os.path.join(share_html, '*.html')):
        # 分享模式：脚本在根目录，用户把聊天记录 html 放进 html 子文件夹
        HTML_DIR = share_html
        LOCAL_ROOT = share_html          # 下载到 html\images、html\audio
        DATA_JS = os.path.join(SCRIPT_DIR, 'data.js')   # 生成到 exe 所在根目录
    elif os.path.basename(SCRIPT_DIR) in ('_scripts', 'scripts', 'tools'):
        HTML_DIR = os.path.dirname(SCRIPT_DIR)
        LOCAL_ROOT = os.path.join(HTML_DIR, 'html')
        DATA_JS = os.path.join(HTML_DIR, 'data.js')
    else:
        HTML_DIR = SCRIPT_DIR
        LOCAL_ROOT = os.path.join(HTML_DIR, 'html')
        DATA_JS = os.path.join(HTML_DIR, 'data.js')

# ---------------- 解析工具 ----------------
def find_array(data):
    i = data.find('var array =')
    if i < 0:
        i = data.find('var array=')
    if i < 0:
        return None
    start = data.find('[', i)
    depth = 0
    instr = None
    j = start
    while j < len(data):
        ch = data[j]
        if instr:
            if ch == BS:
                j += 2
                continue
            if ch == instr:
                instr = None
        else:
            if ch in ('"', "'"):
                instr = ch
            elif ch == '[':
                depth += 1
            elif ch == ']':
                depth -= 1
                if depth == 0:
                    return data[start:j + 1]
        j += 1
    return None

def sanitize(text):
    text = re.sub(r'</script', r'<\\/script', text, flags=re.IGNORECASE)
    text = text.replace('<!--', '<\\!--')
    text = text.replace('-->', '--\\>')
    return text

def clean_title(fn):
    t = os.path.splitext(os.path.basename(fn))[0]
    t = re.sub(r'[、。！？，。\s]+$', '', t)
    # "联云课小助手-<用户名>" 统一改成 "联云课小助手"（对所有人通用）
    if t.startswith('联云课小助手-'):
        t = '联云课小助手'
    return t

def count_messages(arr_src):
    n = 0
    i = 0
    L = len(arr_src)
    while i < L:
        if arr_src[i] == '{':
            depth = 0
            instr = None
            j = i
            while j < L:
                ch = arr_src[j]
                if instr:
                    if ch == BS:
                        j += 2
                        continue
                    if ch == instr:
                        instr = None
                else:
                    if ch in ('"', "'"):
                        instr = ch
                    elif ch == '{':
                        depth += 1
                    elif ch == '}':
                        depth -= 1
                        if depth == 0:
                            seg = arr_src[i:j + 1]
                            if re.search(r'["\']msgId["\']', seg):
                                n += 1
                            i = j + 1
                            break
                j += 1
            else:
                break
        else:
            i += 1
    return n

# ---------------- 提取云端链接（含三种转义形态） ----------------
def normalize(u):
    while u.endswith('\\') and not u.endswith('\\\\/'):
        u = u[:-1]
    return u.replace('\\\\/', '/').replace('\\/', '/')

PAT_PLAIN = re.compile(r'https?://[A-Za-z0-9._~:/?#@!$&()*+,;=%\-\[\]]+')
PAT_DBL   = re.compile(r'https?:\\\\/\\\\/[A-Za-z0-9._~:/?#@!$&()*+,;=%\-\[\]\\\\]+')
PAT_SGL   = re.compile(r'https?:\\/\\/[A-Za-z0-9._~:/?#@!$&()*+,;=%\-\[\]\\]+')
PATS = (PAT_PLAIN, PAT_DBL, PAT_SGL)

def extract_forms(text):
    """返回 {文件形态: 规范化URL}。"""
    out = {}
    for p in PATS:
        for m in p.finditer(text):
            f = m.group(0)
            while f.endswith('\\') and not f.endswith('\\\\/'):
                f = f[:-1]
            u = normalize(f)
            if 'ztytech.com' in u or 'aliyuncs.com' in u:
                out[f] = u
    return out

def classify(u):
    ext = u.rsplit('/', 1)[-1].split('?')[0].rsplit('.', 1)[-1].lower()
    if ext in EXT_IMG:
        return 'img'
    if ext in EXT_AUD:
        return 'aud'
    return 'oth'

# ---------------- 下载 ----------------
ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

def download_one(url, target):
    if os.path.exists(target) and os.path.getsize(target) > 0:
        return True
    os.makedirs(os.path.dirname(target), exist_ok=True)
    for _ in range(3):
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'})
            with urllib.request.urlopen(req, timeout=30, context=ctx) as r:
                body = r.read()
            if not body:
                raise ValueError('empty')
            tmp = target + '.part'
            with open(tmp, 'wb') as fh:
                fh.write(body)
            os.replace(tmp, target)
            return True
        except Exception:
            continue
    return False

def download_all(urls, ffmpeg=None):
    """下载全部资源（带进度条），返回 {规范化URL: data.js 里的相对路径}。"""
    import hashlib, time
    total = len(urls)
    start = time.time()
    base_map = defaultdict(list)
    for u in urls:
        base_map[u.rsplit('/', 1)[-1]].append(u)
    collisions = {b for b, v in base_map.items() if len(v) > 1}
    target_map = {}
    for u in urls:
        b = u.rsplit('/', 1)[-1]
        kind = classify(u)
        sub = 'audio' if kind == 'aud' else 'images'
        if b in collisions:
            b = hashlib.md5(u.encode('utf-8')).hexdigest()[:6] + '_' + b
        target_map[u] = (os.path.join(LOCAL_ROOT, sub, b), sub + '/' + b)

    def show_progress(done, ok, fail):
        pct = 100.0 * done / total if total else 100.0
        width = 30
        filled = int(width * done / total) if total else width
        bar = '#' * filled + '-' * (width - filled)
        elapsed = time.time() - start
        speed = done / elapsed if elapsed > 0 else 0
        eta = int((total - done) / speed) if speed > 0 else 0
        sys.stdout.write('\r下载中 [%s] %3.0f%%  %d/%d  成功%d 失败%d  预计剩余 %d 秒' % (bar, pct, done, total, ok, len(fail), eta))
        sys.stdout.flush()

    ok = 0
    fail = []
    done = 0
    with ThreadPoolExecutor(max_workers=16) as ex:
        futs = {ex.submit(download_one, u, target_map[u][0]): u for u in urls}
        for fut in as_completed(futs):
            u = futs[fut]
            if fut.result():
                ok += 1
                t = target_map[u][0]
                if ffmpeg and t.lower().endswith('.amr'):
                    dst = t[:-4] + '.mp3'
                    try:
                        subprocess.run([ffmpeg, '-y', '-i', t, '-codec:a', 'libmp3lame', '-q:a', '6', dst],
                                       capture_output=True, timeout=120)
                        if os.path.exists(dst) and os.path.getsize(dst) > 0:
                            target_map[u] = (dst, 'audio/' + os.path.basename(dst))
                    except Exception:
                        pass
            else:
                fail.append(u)
            done += 1
            show_progress(done, ok, fail)
    sys.stdout.write('\n')
    sys.stdout.flush()
    print(f'下载完成：成功 {ok}，失败 {len(fail)}')
    for u in fail[:10]:
        print('  失败:', u)
    return {u: target_map[u][1] for u in urls if u not in fail}

# ---------------- ffmpeg 工具（用于 amr → mp3 语音转换） ----------------
def get_ffmpeg():
    """获取 ffmpeg 路径；未装 imageio-ffmpeg 则返回 None。"""
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None

def ensure_ffmpeg(auto=False):
    """确保有 ffmpeg；auto=True 时尝试自动 pip 安装。返回路径或 None。"""
    ff = get_ffmpeg()
    if ff:
        return ff
    if auto:
        print('未安装 imageio-ffmpeg，尝试自动安装...')
        try:
            r = subprocess.run([sys.executable, '-m', 'pip', 'install', 'imageio-ffmpeg'],
                               capture_output=True, timeout=300)
            if r.returncode == 0:
                return get_ffmpeg()
            print('自动安装失败（可能需要联网或权限）。')
        except Exception:
            print('自动安装失败。')
    return None

def remedy_convert(ffmpeg):
    """补救：把已下载的 .amr 语音转成可播放的 .mp3，并更新 data.js。无需重新下载。"""
    aud_dir = os.path.join(LOCAL_ROOT, 'audio')
    amrs = sorted(glob.glob(os.path.join(aud_dir, '*.amr')))
    if not amrs:
        print('没有找到已下载的 .amr 语音文件，无需转换。')
        return
    ok = fail = 0
    for src in amrs:
        dst = src[:-4] + '.mp3'
        if os.path.exists(dst) and os.path.getsize(dst) > 0:
            ok += 1
            continue
        try:
            r = subprocess.run([ffmpeg, '-y', '-i', src, '-codec:a', 'libmp3lame', '-q:a', '6', dst],
                               capture_output=True, timeout=120)
            if r.returncode == 0 and os.path.exists(dst) and os.path.getsize(dst) > 0:
                ok += 1
            else:
                fail += 1
        except Exception:
            fail += 1
    print(f'语音转换：成功 {ok}，失败 {fail}')
    if os.path.exists(DATA_JS):
        d = open(DATA_JS, encoding='utf-8').read()
        changed = 0
        for src in amrs:
            name = os.path.basename(src)[:-4]
            old = 'audio/' + name + '.amr'
            if old in d:
                d = d.replace(old, 'audio/' + name + '.mp3')
                changed += 1
        if changed:
            open(DATA_JS, 'w', encoding='utf-8').write(d)
            print(f'data.js 已更新 {changed} 处语音引用为 .mp3。')
        else:
            print('data.js 中没有需要更新的语音引用。')

# ---------------- 交互确认 ----------------
print('聊天记录文件夹：', HTML_DIR)
if not os.path.isdir(HTML_DIR):
    print('文件夹不存在：', HTML_DIR)
    sys.exit(1)
files = sorted(glob.glob(os.path.join(HTML_DIR, '*.html')))
if not files:
    print('该文件夹下没有 .html 文件，请确认路径。')
    sys.exit(1)
print(f'发现 {len(files)} 个 html 文件。')

mode = input('图片/语音处理方式？\n  [1] 保留云端链接（需联网查看）\n  [2] 下载到本地（可离线，先确认空间）\n  [3] 补救：把已下载的语音转成可播放的 mp3（无需重新下载）\n请输入 1、2 或 3：').strip()
while mode not in ('1', '2', '3'):
    mode = input('请输入 1、2 或 3：').strip()

# 模式 3：补救——转换已下载的 amr 语音，无需重新下载
if mode == '3':
    ffmpeg = get_ffmpeg()
    if not ffmpeg:
        ans = input('需要 imageio-ffmpeg 才能转换语音，是否自动安装？(y/n)：').strip().lower()
        if ans in ('y', 'yes'):
            ffmpeg = ensure_ffmpeg(auto=True)
        if not ffmpeg:
            print('无法获得 ffmpeg。请先手动运行：pip install imageio-ffmpeg，然后再运行本脚本选 3。')
            sys.exit(1)
    remedy_convert(ffmpeg)
    sys.exit(0)

# ---------------- 收集所有云端链接 ----------------
form_map = defaultdict(set)
for fn in files:
    data = open(fn, encoding='utf-8').read()
    arr = find_array(data)
    if arr is None:
        continue
    for f, u in extract_forms(arr).items():
        form_map[u].add(f)

urls = sorted(form_map.keys())
img_urls = [u for u in urls if classify(u) == 'img']
aud_urls = [u for u in urls if classify(u) == 'aud']

local_map = {}
if mode == '2':
    if not (img_urls or aud_urls):
        print('没有发现需要下载的云端图片/语音（可能已经是本地路径）。')
    else:
        usage = shutil.disk_usage(HTML_DIR)
        est = len(img_urls) * 350 * 1024 + len(aud_urls) * 60 * 1024
        print(f'\n待下载：图片 {len(img_urls)} 张、语音 {len(aud_urls)} 条，预计约 {est / 1048576:.0f} MB')
        print(f'目标：{os.path.join(LOCAL_ROOT, "images")} 和 {os.path.join(LOCAL_ROOT, "audio")}')
        print(f'磁盘剩余：{usage.free / 1073741824:.1f} GB')
        if usage.free < est * 1.5 + 300 * 1024 * 1024:
            print('警告：磁盘空间可能不足，请清理后再试！')
        if input('继续下载？(y/n)：').strip().lower() not in ('y', 'yes'):
            print('已取消。')
            sys.exit(0)
        # 检查 ffmpeg：缺省时提示/自动安装，否则语音(amr)无法转成可播放 mp3
        ffmpeg = get_ffmpeg()
        if not ffmpeg:
            print('提示：未安装 imageio-ffmpeg，语音(amr)将无法转换成可播放的 mp3（语音会显示"已失效"）。')
            ans = input('要现在自动安装吗？(y=安装并继续 / n=继续但不装)：').strip().lower()
            if ans in ('y', 'yes'):
                ffmpeg = ensure_ffmpeg(auto=True)
                if not ffmpeg:
                    print('自动安装失败（可能需要联网）。语音将无法播放；可之后运行本脚本选 3 补救（无需重新下载）。')
            else:
                print('已跳过语音转换。可之后运行本脚本选 3 补救（无需重新下载）。')
        local_map = download_all(img_urls + aud_urls, ffmpeg)

# ---------------- 生成 data.js ----------------
parts = []
total_msgs = 0
for fn in files:
    data = open(fn, encoding='utf-8').read()
    arr = find_array(data)
    title = clean_title(fn)
    if arr is None:
        parts.append('  ' + json.dumps(title, ensure_ascii=False) + ': []')
        continue
    arr = sanitize(arr)
    if local_map:
        forms = extract_forms(arr)
        for f in sorted(forms, key=len, reverse=True):
            u = forms[f]
            if u in local_map:
                arr = arr.replace(f, local_map[u])
    parts.append('  ' + json.dumps(title, ensure_ascii=False) + ': ' + arr)
    total_msgs += count_messages(arr)

content = 'window.__CHATDATA__ = {\n' + ',\n'.join(parts) + '\n};\n'
with open(DATA_JS, 'w', encoding='utf-8') as fh:
    fh.write(content)

print('\n已生成 data.js：', DATA_JS)
print('会话数：', len(parts), ' 消息总数：', total_msgs)
print('\n下一步：')
print('  1. 把 聊天记录.html 复制到与 data.js 相同的文件夹')
print('  2. 双击打开 聊天记录.html')
if mode == '2':
    print('  图片/语音已下载到 html\\images 和 html\\audio，可离线查看（界面会自动识别当前账号）')
else:
    print('  图片/语音仍为云端链接，需要联网查看')
