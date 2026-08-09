# -*- coding: utf-8 -*-
"""解析「响应」导出的聊天记录 html，生成查看器用的 data.js（无交互，供应用调用）。

约定：
  - data.js 中图片/语音引用为相对路径，形如 images/xxx、audio/xxx；
  - 本地模式下下载到 local_root/images 与 local_root/audio（前端 fixPath 会补 html/ 前缀）。
"""
import glob
import hashlib
import json
import os
import re
import shutil
import ssl
import subprocess
import urllib.request
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed

BS = chr(92)  # 反斜杠
EXT_IMG = {'jpg', 'jpeg', 'png', 'gif', 'webp', 'bmp'}
EXT_AUD = {'amr', 'mp3', 'wav', 'm4a', 'aac', 'ogg', 'mpeg'}

# ---------- 数组切取 ----------

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

# ---------- 云端 URL 提取 ----------

def normalize(u):
    while u.endswith('\\') and not u.endswith('\\\\/'):
        u = u[:-1]
    return u.replace('\\\\/', '/').replace('\\/', '/')

PAT_PLAIN = re.compile(r'https?://[A-Za-z0-9._~:/?#@!$&()*+,;=%\-\[\]]+')
PAT_DBL = re.compile(r'https?:\\\\/\\\\/[A-Za-z0-9._~:/?#@!$&()*+,;=%\-\[\]\\\\]+')
PAT_SGL = re.compile(r'https?:\\/\\/[A-Za-z0-9._~:/?#@!$&()*+,;=%\-\[\]\\]+')
PATS = (PAT_PLAIN, PAT_DBL, PAT_SGL)

def extract_forms(text):
    """返回 {文件形态: 规范化URL}，只保留云端（ztytech / aliyuncs）。"""
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

# ---------- 下载 ----------

_CTX = ssl.create_default_context()
_CTX.check_hostname = False
_CTX.verify_mode = ssl.CERT_NONE

def download_one(url, target):
    if os.path.exists(target) and os.path.getsize(target) > 0:
        return True
    os.makedirs(os.path.dirname(target), exist_ok=True)
    for _ in range(3):
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'})
            with urllib.request.urlopen(req, timeout=30, context=_CTX) as r:
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

def get_ffmpeg():
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None

def download_all(urls, local_root, ffmpeg=None, on_progress=None):
    """把云端资源下载到 local_root（其下分 images / audio），返回 {规范化URL: 相对路径}。"""
    total = len(urls)
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
        target_map[u] = (os.path.join(local_root, sub, b), sub + '/' + b)

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
                        subprocess.run(
                            [ffmpeg, '-y', '-i', t, '-codec:a', 'libmp3lame', '-q:a', '6', dst],
                            capture_output=True, timeout=120)
                        if os.path.exists(dst) and os.path.getsize(dst) > 0:
                            target_map[u] = (dst, 'audio/' + os.path.basename(dst))
                    except Exception:
                        pass
            else:
                fail.append(u)
            done += 1
            if on_progress:
                try:
                    on_progress(done, total, ok, len(fail))
                except Exception:
                    pass
    return {u: target_map[u][1] for u in urls if u not in fail}

# ---------- 本地引用（images/xxx 等）复制 ----------

REF_RE = re.compile(r'(?:html/)?(images|audio)/([A-Za-z0-9_.\-]+)')

def collect_local_refs(array_texts):
    """收集数组文本里的本地图片/语音引用，统一为 images/xxx、audio/xxx 形态。"""
    refs = set()
    for text in array_texts:
        for m in REF_RE.finditer(text):
            refs.add(m.group(1) + '/' + m.group(2))
    return refs

def copy_local_refs(refs, folder, local_root, on_progress=None):
    """把记录文件夹里的本地图片/语音复制到 local_root（跳过已存在且大小一致的文件）。

    源位置兼容两种结构：folder/<ref>（导出根）或 folder/html/<ref>（html 子目录）。
    """
    total = len(refs)
    done = 0
    for ref in sorted(refs):
        done += 1
        src = None
        for base in (folder, os.path.join(folder, 'html')):
            p = os.path.join(base, ref)
            if os.path.isfile(p) and os.path.getsize(p) > 0:
                src = p
                break
        if src is not None:
            dst = os.path.join(local_root, ref)
            try:
                if not (os.path.exists(dst) and os.path.getsize(dst) == os.path.getsize(src)):
                    os.makedirs(os.path.dirname(dst), exist_ok=True)
                    shutil.copy2(src, dst)
            except Exception:
                pass
        if on_progress:
            try:
                on_progress(done, total, done, 0)
            except Exception:
                pass
    return done

def estimate_storage(folder):
    """估算把该记录文件夹解析并本地化所需的存储空间（字节）。"""
    files = sorted(glob.glob(os.path.join(folder, '*.html')))
    if not files:
        return {'error': '该文件夹下没有 .html 文件'}

    array_texts = []
    for fn in files:
        data = open(fn, encoding='utf-8').read()
        arr = find_array(data)
        array_texts.append(arr or '')

    # 本地引用：按记录文件夹里实际文件大小计算
    local_refs = collect_local_refs(array_texts)
    local_bytes = 0
    for ref in local_refs:
        for base in (folder, os.path.join(folder, 'html')):
            p = os.path.join(base, ref)
            if os.path.isfile(p):
                local_bytes += os.path.getsize(p)
                break

    # 云端 URL：全局去重后按平均大小估算（图片 350KB、语音 60KB）
    img_urls = set()
    aud_urls = set()
    for arr in array_texts:
        if not arr:
            continue
        for _f, u in extract_forms(arr).items():
            if classify(u) == 'img':
                img_urls.add(u)
            elif classify(u) == 'aud':
                aud_urls.add(u)
    est_cloud_bytes = len(img_urls) * 350 * 1024 + len(aud_urls) * 60 * 1024

    return {
        'local_refs': len(local_refs),
        'local_bytes': local_bytes,
        'cloud_img': len(img_urls),
        'cloud_aud': len(aud_urls),
        'est_cloud_bytes': est_cloud_bytes,
        'needed_bytes': local_bytes + est_cloud_bytes,
    }

# ---------- 生成 data.js ----------

def generate_data_js(folder, image_mode='online', local_root=None, ffmpeg=None, on_progress=None):
    """解析 folder 下的 *.html，生成 data.js 内容。

    返回 (content, stats)。
    image_mode: 'online' 保留云端链接；'local' 下载云端到 local_root。
    本地引用（images/xxx、audio/xxx）无论哪种模式都会从记录文件夹复制到 local_root，
    因为本地图没有云端链接可在线显示。
    """
    files = sorted(glob.glob(os.path.join(folder, '*.html')))
    if not files:
        return None, {'conversations': 0, 'messages': 0, 'error': '该文件夹下没有 .html 文件'}

    array_texts = []
    for fn in files:
        data = open(fn, encoding='utf-8').read()
        arr = find_array(data)
        array_texts.append(arr or '')

    # 本地引用：先复制（在线/本地都要，否则本地图无法显示）
    if local_root:
        local_refs = collect_local_refs(array_texts)
        if local_refs:
            copy_local_refs(local_refs, folder, local_root, on_progress)

    # 云端：local 模式下载并替换；online 模式保留原链接
    local_map = {}
    if image_mode == 'local' and local_root:
        form_map = defaultdict(set)
        for arr in array_texts:
            if not arr:
                continue
            for f, u in extract_forms(arr).items():
                form_map[u].add(f)
        urls = sorted(form_map.keys())
        img_urls = [u for u in urls if classify(u) == 'img']
        aud_urls = [u for u in urls if classify(u) == 'aud']
        if img_urls or aud_urls:
            local_map = download_all(img_urls + aud_urls, local_root, ffmpeg, on_progress)

    parts = []
    total_msgs = 0
    for i, fn in enumerate(files):
        arr = array_texts[i]
        title = clean_title(fn)
        if not arr:
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
    return content, {'conversations': len(parts), 'messages': total_msgs}
