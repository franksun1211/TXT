# -*- coding: utf-8 -*-
# avtoday.io -> TVBox py 源（蜘蛛脚本）
# 用法：丢进 TVBox 的 py 目录，配置里 api 指向本文件
#   {"key":"avtoday","name":"avtoday","type":3,"api":"./py/avtoday.py",
#    "searchable":1,"quickSearch":1,"filterable":1}
# 纯标准库，无需 requests。播放自带 127.0.0.1 转发（站点直链绑 UA，不转发必 403）。
# 详情页线路：正片 + 「女優:XXX 作品集(N)」；搜索框或详情页点演员名，可直接列出她名下所有作品。
# 嫌详情页多抓几页慢，可把下面 ACTOR_PAGES 改成 0（关闭作品集）或 1。

import sys
import os
import re
import json
import time
import ssl
import socket
import threading
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# TVBox py 运行时的基类（没有也不影响）
sys.path.append('..')
try:
    from base.spider import Spider as _BaseSpider
except Exception:
    try:
        from spider import Spider as _BaseSpider
    except Exception:
        _BaseSpider = object

# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------

SITE = "https://avtoday.io"
LANG = "cht"                     # cht=繁中  chs=简中  en=英文
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
TIMEOUT = 15
RETRIES = 2

USE_RELAY = True                 # True=本机转发播放流（推荐，稳）；False=直链+header
RELAY_HOST = "127.0.0.1"
RELAY_PORT = 19787

LIST_CACHE_TTL = 300             # 列表页缓存
PLAY_CACHE_TTL = 1800            # 播放直链缓存
CAT_CACHE_TTL = 6 * 3600         # 类型目录缓存
SEARCH_PAGES = 6                 # 搜索横扫页数

# 详情页「女優作品集」：进详情时顺手把她名下的片都列出来（0=关闭）
ACTOR_PAGES = 3                  # 最多抓几页，每页 20 条，抓不满会自动停
ACTOR_MAX = 100                  # 最多列多少条

# 固定板块：(名称, 路径)
BASE_SECTIONS = [
    ("新片上架", "new.html"),
    ("人氣影片", "hot.html"),
    ("無碼專區", "no-mosaic.html"),
    ("中文字幕", "catalog/中文字幕.html"),
]

# 关键词繁简归一（类型目录是繁中，用户多半输简中）
_NORM = {"无码": "無碼", "人妻": "人妻", "巨乳": "巨乳", "素人": "素人", "熟女": "熟女",
         "痴女": "癡女", "制服": "制服", "中文字幕": "中文字幕", "潮吹": "潮吹",
         "自慰": "自慰", "多人": "多人", "泡泡浴": "泡泡浴", "足交": "足交",
         "贫乳": "貧乳", "萝莉": "蘿莉", "丝袜": "絲襪", "及膝袜": "及膝襪",
         "素股": "素股", "fc2": "FC2"}

_SSL = ssl.create_default_context()
try:
    _SSL.check_hostname = True
except Exception:
    pass

_cache = {}
_lock = threading.Lock()


def _log(msg):
    try:
        print("[avtoday] %s" % msg, flush=True)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# 网络
# ---------------------------------------------------------------------------

def http_get(url, referer=None):
    for _ in range(RETRIES + 1):
        try:
            headers = {"User-Agent": UA, "Accept-Encoding": "identity"}
            if referer:
                headers["Referer"] = referer
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=TIMEOUT, context=_SSL) as r:
                raw = r.read()
            enc = "utf-8"
            m = re.search(rb'charset=["\']?([\w-]+)', raw[:2000], re.I)
            if m:
                enc = m.group(1).decode("ascii", "ignore")
            return raw.decode(enc, "ignore")
        except Exception:
            time.sleep(0.4)
    return None


def cache_get(key, ttl):
    with _lock:
        hit = _cache.get(key)
    if hit and hit[0] + ttl > time.time():
        return hit[1]
    return None


def cache_put(key, val):
    with _lock:
        _cache[key] = (time.time(), val)
        if len(_cache) > 400:
            for k in sorted(_cache, key=lambda x: _cache[x][0])[:100]:
                _cache.pop(k, None)
    return val


# ---------------------------------------------------------------------------
# 列表页解析
# ---------------------------------------------------------------------------

_BLOCK_RE = re.compile(r"<!-- album block -->([\s\S]+?)<!-- end of album block -->")
_SPCODE_RE = re.compile(r'data-spcode="([^"]+)"')
_PIC_RE = re.compile(r"url\('([^']+)'\)")
_TITLE_RE = re.compile(r'<div class="video-title[^"]*">\s*<a[^>]*>([\s\S]*?)</a>')
_DUR_RE = re.compile(r'class="video-duration">\s*([^<]+?)\s*<')
_TAG_RE = re.compile(r'class="video-tag[^"]*">\s*([^<]+?)\s*<')
_PAGECOUNT_RE = re.compile(r"var\s+page_count\s*=\s*(\d+)")
_ACTOR_A_RE = re.compile(r'href="/[a-z]{2,3}/actor/([^"]+?)\.html"[^>]*>([\s\S]*?)</a>')


def _clean(s):
    s = re.sub(r"<[^>]+>", "", s or "")
    for a, b in (("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">"), ("&quot;", '"'),
                 ("&#39;", "'"), ("&nbsp;", " "), ("&hellip;", "…")):
        s = s.replace(a, b)
    return re.sub(r"\s+", " ", s).strip()


def parse_list(html):
    items = []
    for b in _BLOCK_RE.findall(html or ""):
        m = _SPCODE_RE.search(b)
        if not m:
            continue
        spcode = m.group(1)
        pic = ""
        pm = _PIC_RE.search(b)
        if pm:
            pic = pm.group(1)
        if not pic:
            im = re.search(r'<img[^>]+src="(/pic/[^"]+)"', b)
            if im:
                pic = im.group(1)
        if pic.startswith("/"):
            pic = SITE + pic
        tm = _TITLE_RE.search(b)
        title = _clean(tm.group(1)) if tm else spcode
        dm = _DUR_RE.search(b)
        dur = _clean(dm.group(1)) if dm else ""
        gm = _TAG_RE.search(b)
        tag = _clean(gm.group(1)) if gm else ""
        items.append({"id": spcode, "name": title or spcode, "pic": pic,
                      "remarks": (dur + (" " + tag if tag else "")).strip()})
    pc = _PAGECOUNT_RE.search(html or "")
    return items, (int(pc.group(1)) if pc else 999)


def section_url(path, page=1):
    return "%s/%s/%s?page=%d" % (SITE, LANG, urllib.parse.quote(path, safe="/%"), int(page or 1))


def fetch_section(path, page=1, ttl=LIST_CACHE_TTL):
    key = "list:%s:%s" % (path, page)
    hit = cache_get(key, ttl)
    if hit is not None:
        return hit
    html = http_get(section_url(path, page))
    data = parse_list(html) if html else ([], 999)
    if data[0]:
        cache_put(key, data)
    return data


def parse_actor_list(html):
    """女优作品页:卡片结构与列表页不同,标题在卡片 div 之外"""
    if not html:
        return [], 1
    h = re.sub(r"<script[\s\S]*?</script>", "", html, flags=re.I)
    items, seen = [], set()
    for b in re.split(r'class="video-card', h)[1:]:
        m = _SPCODE_RE.search(b)
        if not m:
            continue
        code = m.group(1)
        if code.lower() in seen:
            continue
        seen.add(code.lower())
        pic = ""
        pm = _PIC_RE.search(b)
        if pm:
            pic = pm.group(1)
        if pic.startswith("/"):
            pic = SITE + pic
        tm = _TITLE_RE.search(b)
        title = _clean(tm.group(1)) if tm else code
        dm = _DUR_RE.search(b)
        dur = _clean(dm.group(1)) if dm else ""
        items.append({"id": code, "name": title or code, "pic": pic, "remarks": dur})
    pc = _PAGECOUNT_RE.search(h)
    return items, (int(pc.group(1)) if pc else 999)


def fetch_actor(name, page=1):
    """女优作品页:/<lang>/actor/<名字>.html?page=N"""
    nm = (name or "").strip()
    if not nm:
        return [], 1
    page = int(page or 1)
    key = "actor:%s:%s" % (nm, page)
    hit = cache_get(key, LIST_CACHE_TTL)
    if hit is not None:
        return hit
    url = "%s/%s/actor/%s.html" % (SITE, LANG, urllib.parse.quote(nm))
    if page > 1:
        url += "?page=%d" % page
    html = http_get(url)
    items, _pc = parse_actor_list(html) if html else ([], 1)
    pc = page + 1 if len(items) >= 20 else page
    data = (items, pc)
    if items:
        cache_put(key, data)
    return data


def _actor_cands(kw):
    """女优名候选：括号里的原名 / 去掉括号注释的名，都试一遍"""
    k = (kw or "").strip()
    cands = []
    m = re.match(r"^([^(（\[【]+)[(（\[【]([^)）\]】]+)[)）\]】]\s*$", k)
    if m:
        inner = m.group(2).strip()
        if inner:
            cands.append(inner)
    base = re.sub(r"[(（\[【][^)）\]】]*[)）\]】]", "", k).strip()
    for x in (base, k):
        if x and x not in cands:
            cands.append(x)
    return cands


def fetch_actor_works(names, exclude="", max_pages=None, limit=None):
    """女优名下的全部作品（多页合并、去重）—— 详情页的「女優作品集」用它"""
    max_pages = ACTOR_PAGES if max_pages is None else max_pages
    limit = ACTOR_MAX if limit is None else limit
    out, seen = [], set()
    ex = (exclude or "").strip().lower()
    for raw in (names or []):
        got = 0
        for nm in _actor_cands(raw):
            if not nm:
                continue
            for p in range(1, max_pages + 1):
                items, _pc = fetch_actor(nm, p)
                if not items:
                    break
                for it in items:
                    k = it["id"].lower()
                    if k in seen or k == ex:
                        continue
                    seen.add(k)
                    out.append(it)
                    got += 1
                if len(items) < 20 or len(out) >= limit:
                    break
            if got:
                break                      # 这个候选能出片，就不用再试别的写法了
        if len(out) >= limit:
            break
    return out[:limit]


def get_catalogs():
    """类型目录（带缓存）"""
    hit = cache_get("cats", CAT_CACHE_TTL)
    if hit is not None:
        return hit
    out = []
    html = http_get(SITE + "/catalog")
    if html:
        seen = []
        for x in re.findall(r'href="/%s/catalog/([^"]+?)\.html"' % LANG, html):
            if x not in seen:
                seen.append(x)
        out = [("類型:" + urllib.parse.unquote(x), "catalog/%s.html" % x) for x in seen]
    if not out:
        fb = ["無碼", "FC2", "巨乳", "人妻", "制服", "素人", "熟女", "癡女",
              "潮吹", "自慰", "多人", "中文字幕"]
        out = [("類型:" + x, "catalog/%s.html" % x) for x in fb]
    cache_put("cats", out)
    return out


_sections = list(BASE_SECTIONS)
_sections_ready = False


def sections():
    """固定板块 + 类型目录（只拉一次）"""
    global _sections, _sections_ready
    if not _sections_ready:
        exist = set(p for _, p in BASE_SECTIONS)
        _sections = list(BASE_SECTIONS) + [x for x in get_catalogs() if x[1] not in exist]
        _sections_ready = True
    return _sections


# ---------------------------------------------------------------------------
# 详情页
# ---------------------------------------------------------------------------

def parse_detail(spcode):
    key = "detail:%s" % spcode
    hit = cache_get(key, CAT_CACHE_TTL)
    if hit is not None:
        return hit
    url = "%s/%s/video/%s.html" % (SITE, LANG, urllib.parse.quote(spcode))
    html = http_get(url)
    if not html:
        return None
    # 页面混着 JS 模板（${info['duration']} 之类），先把 script 全去掉
    h = re.sub(r"<script[\s\S]*?</script>", "", html, flags=re.I)
    flat = re.sub(r">\s+<", "><", h)

    def field(label):
        m = re.search(label + r":?</span>([\s\S]*?)</div>", flat)
        return _clean(m.group(1)) if m else ""

    def multi(label):
        m = re.search(label + r":?</span>([\s\S]*?)</div>", flat)
        if not m:
            return []
        return [_clean(x) for x in re.findall(r'<a[^>]*>([\s\S]*?)</a>', m.group(1)) if _clean(x)]

    og = lambda k: (re.search(r'property="og:%s"\s+content="([^"]+)"' % k, html).group(1)
                    if re.search(r'property="og:%s"\s+content="([^"]+)"' % k, html) else "")
    pic = og("image")
    if pic.startswith("/"):
        pic = SITE + pic
    date = field("上架日期")
    dur = ""
    dm = re.search(r'class="video-duration">\s*([^<]+?)\s*<', flat)
    if dm:
        dur = _clean(dm.group(1))
    # 女優：页面里是带链接的（/cht/actor/名字.html），链接里的名字才是她页面真正的 slug
    ac_names, ac_slugs = [], []
    am = re.search(r"女優:?</span>([\s\S]*?)</div>", flat)
    if am:
        for slug, nm in _ACTOR_A_RE.findall(am.group(1)):
            s = urllib.parse.unquote(slug).strip()
            if s and s not in ac_slugs:
                ac_slugs.append(s)
            nm2 = _clean(nm)
            if nm2 and nm2 not in ac_names:
                ac_names.append(nm2)

    sp = {
        "id": spcode,
        "code": field("番號") or spcode,
        "name": og("title") or field("標題") or spcode,
        "pic": pic,
        "date": date,
        "year": date[:4] if date[:4].isdigit() else "",
        "actors": ac_names or multi("女優"),
        "actor_slugs": ac_slugs,
        "director": field("導演") or field("导演"),
        "tags": multi("標籤"),
        "content": og("description") or field("標題") or spcode,
        "url": url,
    }
    if dur:
        sp["remarks"] = dur
    cache_put(key, sp)
    return sp


# ---------------------------------------------------------------------------
# 播放地址
# ---------------------------------------------------------------------------

def resolve_play(spcode):
    """直链绑 UA：必须用同一个 UA 取 /player 和取视频流"""
    hit = cache_get("play:%s" % spcode, PLAY_CACHE_TTL)
    if hit:
        return hit
    referer = "%s/%s/video/%s.html" % (SITE, LANG, urllib.parse.quote(spcode))
    html = http_get("%s/player?s=%s" % (SITE, urllib.parse.quote(spcode)), referer=referer)
    if not html:
        return None
    link = ""
    for pat in (r"var\s+video_link\s*=\s*'([^']*)'", r"var\s+m3u8_url\s*=\s*'([^']*)'"):
        m = re.search(pat, html)
        if m and m.group(1).strip():
            link = m.group(1).strip()
            break
    if not link:
        m = re.search(r'<source[^>]+src="(https?://[^"]+)"', html)
        if m:
            link = m.group(1).strip()
    if not link:
        return None
    cache_put("play:%s" % spcode, link)
    return link


# ---------------------------------------------------------------------------
# 本机转发（直链绑 UA/Referer，玩家自己拉会 403）
# ---------------------------------------------------------------------------

def _relay_wrap(target, referer=""):
    name = urllib.parse.urlparse(target).path.rsplit("/", 1)[-1] or "v.mp4"
    q = urllib.parse.urlencode({"u": target, "r": referer or ""})
    return "http://%s:%d/s/%s?%s" % (RELAY_HOST, _relay_port, urllib.parse.quote(name, safe="."), q)


def _rewrite_m3u8(text, base, referer):
    out = []
    for line in text.splitlines():
        s = line.strip()
        if not s:
            continue
        if s.startswith("#"):
            if 'URI="' in s:
                s = re.sub(r'URI="([^"]+)"',
                           lambda m: 'URI="%s"' % _relay_wrap(urllib.parse.urljoin(base, m.group(1)), referer),
                           s)
            out.append(s)
            continue
        out.append(_relay_wrap(urllib.parse.urljoin(base, s), referer))
    return "\n".join(out)


class _RelayHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "avtoday-relay"

    def log_message(self, *a):
        pass

    def do_HEAD(self):
        self._serve(False)

    def do_GET(self):
        self._serve(True)

    def _serve(self, body):
        resp = None
        try:
            parsed = urllib.parse.urlparse(self.path)
            if not parsed.path.startswith("/s/"):
                self.send_error(404)
                return
            qs = urllib.parse.parse_qs(parsed.query)
            target = qs.get("u", [""])[0]
            referer = qs.get("r", [""])[0]
            if not target:
                self.send_error(400)
                return
            headers = {"User-Agent": UA, "Accept-Encoding": "identity", "Connection": "close"}
            if referer:
                headers["Referer"] = referer
            rng = self.headers.get("Range")
            if rng:
                headers["Range"] = rng
            req = urllib.request.Request(target, headers=headers)
            resp = urllib.request.urlopen(req, timeout=TIMEOUT, context=_SSL)
            ctype = (resp.headers.get("Content-Type") or "").lower()
            is_hls = "mpegurl" in ctype or target.split("?")[0].lower().endswith(".m3u8")
            if is_hls:
                text = resp.read().decode("utf-8", "ignore")
                data = _rewrite_m3u8(text, target, referer).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/vnd.apple.mpegurl")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                if body:
                    self.wfile.write(data)
                return
            self.send_response(resp.status)
            for k in ("Content-Type", "Content-Length", "Content-Range",
                      "Accept-Ranges", "ETag", "Last-Modified"):
                v = resp.headers.get(k)
                if v:
                    self.send_header(k, v)
            if not resp.headers.get("Content-Length"):
                self.send_header("Connection", "close")
            self.end_headers()
            if not body:
                return
            while True:
                chunk = resp.read(65536)
                if not chunk:
                    break
                self.wfile.write(chunk)
        except Exception:
            try:
                self.send_error(502)
            except Exception:
                pass
        finally:
            try:
                if resp:
                    resp.close()
            except Exception:
                pass
            self.close_connection = True


_relay_server = None
_relay_port = 0
_relay_dead = False


def ensure_relay():
    global _relay_server, _relay_port, _relay_dead
    if _relay_server is not None:
        return True
    if _relay_dead:
        return False
    for port in range(RELAY_PORT, RELAY_PORT + 20):
        try:
            srv = ThreadingHTTPServer((RELAY_HOST, port), _RelayHandler)
            srv.daemon_threads = True
            threading.Thread(target=srv.serve_forever, daemon=True).start()
            _relay_server, _relay_port = srv, port
            _log("转发已启动 %s:%d" % (RELAY_HOST, port))
            return True
        except Exception:
            continue
    _relay_dead = True
    _log("转发起不来，退回直链模式")
    return False


# ---------------------------------------------------------------------------
# 搜索
# ---------------------------------------------------------------------------

_CODE_RE = re.compile(r"^[A-Za-z0-9]{2,12}[-_]?\d{2,8}$")


def _norm_kw(kw):
    k = (kw or "").strip()
    low = k.lower()
    if low in _NORM:
        return _NORM[low]
    for a, b in _NORM.items():
        if a in k:
            k = k.replace(a, b)
    return k


def do_search(keyword, page=1, limit=20):
    kw = (keyword or "").strip()
    if not kw:
        return [], 1
    ck = "search:%s:%s" % (kw.lower(), page)
    hit = cache_get(ck, 600)
    if hit is not None:
        return hit

    kw_n = _norm_kw(kw)
    low = kw_n.lower()
    results = []
    seen = set()

    # 1) 直接给番號 -> 详情页直查（命中就返回，省得再横扫几十页）
    code = kw.upper().replace(" ", "").replace("_", "-")
    if _CODE_RE.match(code):
        for cand in (code, code.replace("-", "")):
            sp = parse_detail(cand)
            if sp and sp.get("name"):
                out = ([{"id": cand, "name": sp["name"], "pic": sp.get("pic", ""),
                         "remarks": sp.get("remarks", "")}], 1)
                cache_put(ck, out)
                return out
        # 番号样式但站点查不到：标题里也不会出现番号，直接空手回，别白扫几十页
        cache_put(ck, ([], 1))
        return [], 1

    # 2) 命中类型目录 -> 直接给该类型
    for name, path in sections():
        if name.startswith("類型:") and (low in name[3:].lower() or name[3:].lower() in low):
            items, _pc = fetch_section(path, page)
            out = items[:limit], 999
            cache_put(ck, out)
            return out

    # 3) 命中女优名 -> 直接给她的全部作品（支持中文/日文名、括号别名）
    if 2 <= len(kw_n) <= 16:
        for cand in _actor_cands(kw_n):
            if not cand or re.search(r"\d", cand):
                continue
            items, pc = fetch_actor(cand, page)
            if items:
                out = items[:limit], pc
                cache_put(ck, out)
                return out

    # 4) 横扫列表页
    for name, path in list(BASE_SECTIONS) + [x for x in sections() if x[0].startswith("類型:")][:4]:
        for p in range(1, SEARCH_PAGES + 1):
            items, _pc = fetch_section(path, p)
            if not items:
                break
            for it in items:
                if it["id"].lower() in seen:
                    continue
                if low in it["id"].lower() or low in it["name"].lower():
                    seen.add(it["id"].lower())
                    results.append(it)

    total = max(1, (len(results) + limit - 1) // limit)
    start = (page - 1) * limit
    out = results[start:start + limit], total
    cache_put(ck, out)
    return out


# ---------------------------------------------------------------------------
# TVBox 接口
# ---------------------------------------------------------------------------

class Spider(_BaseSpider):

    def __init__(self, *a, **kw):
        try:
            _BaseSpider.__init__(self)
        except Exception:
            pass

    def init(self, extend=""):
        global SITE, LANG
        ext = (extend or "").strip()
        if ext.startswith("http"):
            SITE = ext.rstrip("/")
        elif ext in ("cht", "chs", "en"):
            LANG = ext
        try:
            sections()
        except Exception:
            pass
        return self

    def getName(self):
        return "AVToday"

    def getDependence(self):
        return []

    # --- 首页 -------------------------------------------------------------
    def homeContent(self, filter=False):
        try:
            cls = [{"type_id": str(i), "type_name": n} for i, (n, _p) in enumerate(sections(), 1)]
            cats = [n[3:] for n, _p in sections() if n.startswith("類型:")][:12]
            filters = {}
            if cats:
                opts = [{"n": "全部", "v": ""}] + [{"n": c, "v": "catalog/%s.html" % c} for c in cats]
                for i, (n, _p) in enumerate(sections(), 1):
                    filters[str(i)] = [{"key": "cat", "name": "類型", "value": opts}]
            items, _pc = fetch_section(BASE_SECTIONS[0][1], 1)
            return {"class": cls, "filters": filters, "list": [self._li(x) for x in items]}
        except Exception as e:
            _log("homeContent: %s" % e)
            return {"class": [], "filters": {}, "list": []}

    def homeVideoContent(self):
        try:
            items, _pc = fetch_section(BASE_SECTIONS[0][1], 1)
            return {"list": [self._li(x) for x in items]}
        except Exception:
            return {"list": []}

    # --- 分类 -------------------------------------------------------------
    def categoryContent(self, tid, pg, filter=False, extend=None):
        try:
            page = int(pg or 1)
        except Exception:
            page = 1
        path = None
        extend = extend or {}
        if isinstance(extend, dict) and extend.get("cat"):
            path = extend["cat"]
        if not path:
            secs = sections()
            t = str(tid or "1")
            if ".html" in t:
                path = t
            else:
                try:
                    idx = int(t) - 1
                except Exception:
                    idx = 0
                path = secs[idx][1] if 0 <= idx < len(secs) else BASE_SECTIONS[0][1]
        items, pc = fetch_section(path, page)
        return {"page": page, "pagecount": pc, "limit": 20,
                "total": pc * 20, "list": [self._li(x) for x in items]}

    # --- 详情 -------------------------------------------------------------
    def detailContent(self, array):
        try:
            ids = array if isinstance(array, (list, tuple)) else [array]
            spcode = str(ids[0]).strip()
            sp = parse_detail(spcode) or {"id": spcode, "name": spcode, "pic": "",
                                          "content": spcode, "actors": [], "tags": []}
            actors = ",".join(sp.get("actors") or [])
            tags = ",".join(sp.get("tags") or [])

            # 播放线路：正片 + 该女优的作品集（点线路名就能看到她名下所有片）
            froms = ["AVToday"]
            urls = ["正片$%s" % sp["id"]]
            if ACTOR_PAGES > 0:
                actors_list = sp.get("actors") or []
                slugs = sp.get("actor_slugs") or []
                tries = []
                for i, s in enumerate(slugs[:2]):
                    tries.append((s, actors_list[i] if i < len(actors_list) else s))
                if not tries:
                    tries = [(a, a) for a in actors_list[:2]]
                works, label = [], ""
                for slug, disp in tries:
                    try:
                        works = fetch_actor_works([slug], exclude=sp["id"])
                    except Exception as e:
                        _log("actor works: %s" % e)
                        works = []
                    if works:
                        label = disp or slug
                        break
                if works:
                    label = re.sub(r"[#$]", " ", str(label)).strip()
                    tip = "+" if len(works) >= ACTOR_MAX else ""
                    froms.append("女優:%s 作品集(%d%s)" % (label, len(works), tip))
                    urls.append("#".join("%s$%s" % (w["id"], w["id"]) for w in works))

            vod = {
                "vod_id": sp["id"],
                "vod_name": sp.get("name") or sp["id"],
                "vod_pic": sp.get("pic", ""),
                "vod_year": sp.get("year", ""),
                "vod_area": "日本",
                "vod_remarks": sp.get("remarks") or sp.get("date", ""),
                "vod_actor": actors,
                "vod_director": sp.get("director", ""),
                "vod_lang": "中文字幕" if "中文字幕" in tags else "日語",
                "vod_content": sp.get("content", ""),
                "type_name": "AVToday",
                "vod_play_from": "$$$".join(froms),
                "vod_play_url": "$$$".join(urls),
            }
            return {"list": [vod]}
        except Exception as e:
            _log("detailContent: %s" % e)
            return {"list": []}

    # --- 搜索 -------------------------------------------------------------
    def searchContent(self, key, quick=False, pg="1"):
        try:
            page = int(pg or 1)
        except Exception:
            page = 1
        items, pc = do_search(key, page)
        return {"page": page, "pagecount": pc, "limit": 20,
                "total": pc * 20, "list": [self._li(x) for x in items]}

    # --- 播放 -------------------------------------------------------------
    def playerContent(self, flag, id, vipFlags=None):
        try:
            spcode = str(id).split("$")[-1].strip()
            link = resolve_play(spcode)
            if not link:
                return {"parse": 0, "playUrl": "", "header": "", "url": ""}
            referer = "%s/%s/video/%s.html" % (SITE, LANG, urllib.parse.quote(spcode))
            is_hls = link.split("?")[0].lower().endswith(".m3u8")
            if USE_RELAY and ensure_relay():
                return {"parse": 0, "playUrl": _relay_wrap(link, referer),
                        "header": "", "url": _relay_wrap(link, referer),
                        "format": "application/x-mpegURL" if is_hls else ""}
            return {"parse": 0, "playUrl": link, "url": link, "header": "User-Agent: %s\nReferer: %s" % (UA, referer)}
        except Exception as e:
            _log("playerContent: %s" % e)
            return {"parse": 0, "playUrl": "", "header": "", "url": ""}

    # --- 其它 -------------------------------------------------------------
    def isVideoFormat(self, url):
        return bool(url) and (".m3u8" in url or ".mp4" in url)

    def manualVideoCheck(self):
        return False

    def localProxy(self, param):
        return None

    def destroy(self):
        global _relay_server
        try:
            if _relay_server:
                _relay_server.shutdown()
        except Exception:
            pass
        _relay_server = None
        return "destroy"

    # --- 内部 -------------------------------------------------------------
    @staticmethod
    def _li(it):
        return {"vod_id": it["id"], "vod_name": it["name"],
                "vod_pic": it.get("pic", ""), "vod_remarks": it.get("remarks", "")}


if __name__ == "__main__":
    print("avtoday TVBox py 源 —— 请由 TVBox 加载，无需单独运行。")
