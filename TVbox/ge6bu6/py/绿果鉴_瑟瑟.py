# -*- coding: utf-8 -*-
"""
绿果鉴 · 瑟瑟线  py 源
=========================
从「绿果鉴 0.2.252」内核 libduanju_core.so 里扒出来的成人线站源适配，
按「真果鉴.py」同款骨架重写为纯 Python（零第三方依赖，只用标准库）。

覆盖站源（内核里有适配器的那些）：
    好色TV / JavDB / MissAV / 夜色A片 / 黄色仓库 /
    JavBus / 91热爆 / 2AV / PornHub / JavBobo

首页顶部 tab = 站源，每个站源下面二级筛选栏 = 该站自己的分类。
搜索是所有站源并发出结果，名字前带 [站源] 前缀区分。

纯标准库：urllib / json / re / html / threading / concurrent.futures。
不需要 requests，不需要外挂文件，文件名不变即可任意改。
"""

import binascii
import gzip
import html as _htmlmod
import io
import json
import random
import re
import socket
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib

try:
    from base.spider import Spider as BaseSpider
except Exception:
    class BaseSpider(object):
        pass

try:
    from concurrent.futures import ThreadPoolExecutor
except Exception:
    ThreadPoolExecutor = None

__version__ = "1.0.0"

PC_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
         "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
MOB_UA = ("Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 "
          "(KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36")

TIMEOUT = 18
MAX_PAGE = 9999


# ============================================================
# 基础小工具
# ============================================================

def _s(v):
    if v is None:
        return ""
    if isinstance(v, bool):
        return "true" if v else ""
    return str(v).strip()


def _q(s):
    return urllib.parse.quote(_s(s), safe="")


def _uq(s):
    try:
        return urllib.parse.unquote(_s(s))
    except Exception:
        return _s(s)


def _abs(base, href):
    try:
        return urllib.parse.urljoin(base, _htmlmod.unescape(_s(href)))
    except Exception:
        return _s(href)


_ENT = [("&nbsp;", " "), ("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">"),
        ("&quot;", '"'), ("&#39;", "'"), ("&apos;", "'"), ("&mdash;", "-"),
        ("&hellip;", "..."), ("&middot;", "."), ("&ldquo;", '"'),
        ("&rdquo;", '"'), ("&amp;", "&")]


def _txt(s):
    """去标签 + decode 实体 + 压空白。"""
    s = _s(s)
    if not s:
        return ""
    s = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", s)
    s = re.sub(r"(?s)<[^>]+>", " ", s)
    s = _htmlmod.unescape(s)
    for a, b in _ENT:
        s = s.replace(a, b)
    s = s.replace("\u3000", " ").replace("\xa0", " ")
    return re.sub(r"\s+", " ", s).strip()


def _attr(tag, name):
    m = re.search(r'%s\s*=\s*"([^"]*)"' % re.escape(name), tag, re.I)
    if m:
        return _htmlmod.unescape(m.group(1)).strip()
    m = re.search(r"%s\s*=\s*'([^']*)'" % re.escape(name), tag, re.I)
    if m:
        return _htmlmod.unescape(m.group(1)).strip()
    m = re.search(r"%s\s*=\s*([^\s>]+)" % re.escape(name), tag, re.I)
    return _htmlmod.unescape(m.group(1)).strip() if m else ""


def _pick(d, *keys):
    if not isinstance(d, dict):
        return ""
    for k in keys:
        v = d.get(k)
        if v is None or v is False:
            continue
        s = _s(v)
        if s:
            return s
    return ""


def _json_in(html, marker):
    """从页面里抠一个 } 配平的 JSON 对象（player_aaaa = {...} 那种）。"""
    i = html.find(marker)
    if i < 0:
        return None
    i = html.find("{", i)
    if i < 0:
        return None
    depth, instr, esc = 0, False, False
    for j in range(i, min(len(html), i + 200000)):
        c = html[j]
        if instr:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                instr = False
            continue
        if c == '"':
            instr = True
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(html[i:j + 1])
                except Exception:
                    return None
    return None


def _fix_url(u):
    u = _s(u)
    if not u:
        return ""
    u = _htmlmod.unescape(u)
    if u.startswith("//"):
        u = "https:" + u
    elif u.startswith("/"):
        u = u
    return u.replace("\\/", "/")


# ============================================================
# HTTP 会话（Cookie / gzip / 重试 / 跟随跳转）
# ============================================================

class _Resp(object):
    __slots__ = ("url", "text", "code", "headers")

    def __init__(self, url, text, code=200, headers=None):
        self.url = url
        self.text = text
        self.code = code
        self.headers = headers or {}


class _Session(object):
    def __init__(self, base, ua=None, referer=None):
        self.base = _s(base).rstrip("/")
        self.ua = ua or PC_UA
        self.referer = referer or (self.base + "/")
        self.cookies = {}
        self.lock = threading.Lock()
        self._ctx = None

    # ---------- cookie ----------
    def _cookie_header(self, url):
        host = urllib.parse.urlparse(url).hostname or ""
        out = []
        with self.lock:
            for (dm, nm), val in list(self.cookies.items()):
                if dm and host.endswith(dm.lstrip(".")):
                    out.append("%s=%s" % (nm, val))
        return "; ".join(out)

    def _store_cookies(self, url, headers):
        host = urllib.parse.urlparse(url).hostname or ""
        try:
            raw = headers.get_all("Set-Cookie") or []
        except Exception:
            raw = []
        with self.lock:
            for line in raw:
                for part in _s(line).split(","):
                    part = part.strip()
                    if "=" not in part:
                        continue
                    kv = part.split(";")[0].strip()
                    if "=" not in kv:
                        continue
                    k, v = kv.split("=", 1)
                    k, v = k.strip(), v.strip()
                    if not k:
                        continue
                    # 只存一层域名即可
                    dm = "." + ".".join(host.split(".")[-2:]) if host.count(".") >= 1 else host
                    self.cookies[(dm, k)] = v

    def _ssl_ctx(self):
        if self._ctx is None:
            try:
                ctx = ssl.create_default_context()
                ctx.check_hostname = False
                ctx.verify_mode = ssl.CERT_NONE
                try:
                    ctx.set_ciphers("DEFAULT@SECLEVEL=1")
                except Exception:
                    pass
                self._ctx = ctx
            except Exception:
                self._ctx = ssl._create_unverified_context()
        return self._ctx

    # ---------- 请求 ----------
    def get(self, url, headers=None, referer=None, timeout=None, tries=3,
            binary=False):
        url = _fix_url(url)
        if url.startswith("/"):
            url = self.base + url
        last = None
        for n in range(max(1, tries)):
            try:
                hd = {
                    "User-Agent": self.ua,
                    "Accept": ("text/html,application/xhtml+xml,application/xml;"
                               "q=0.9,image/avif,image/webp,*/*;q=0.8"),
                    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                    "Accept-Encoding": "gzip, deflate",
                    "Referer": referer or self.referer,
                    "Connection": "close",
                }
                ck = self._cookie_header(url)
                if ck:
                    hd["Cookie"] = ck
                if headers:
                    hd.update(headers)
                req = urllib.request.Request(url, headers=hd)
                resp = urllib.request.urlopen(
                    req, timeout=timeout or TIMEOUT, context=self._ssl_ctx())
                raw = resp.read()
                self._store_cookies(url, resp.headers)
                enc = _s(resp.headers.get("Content-Encoding")).lower()
                if enc == "gzip":
                    try:
                        raw = gzip.GzipFile(fileobj=io.BytesIO(raw)).read()
                    except Exception:
                        pass
                elif enc == "deflate":
                    try:
                        raw = zlib.decompress(raw)
                    except Exception:
                        try:
                            raw = zlib.decompress(raw, -zlib.MAX_WBITS)
                        except Exception:
                            pass
                code = getattr(resp, "status", None) or resp.getcode() or 200
                final = resp.geturl() or url
                if binary:
                    return _Resp(final, raw, code, dict(resp.headers))
                txt = _decode(raw, resp.headers)
                if _looks_blocked(txt):
                    last = _Blocked("站点返回了人机校验页")
                    time.sleep(0.8 + 0.7 * n)
                    continue
                return _Resp(final, txt, code, dict(resp.headers))
            except Exception as e:
                last = e
                if n < tries - 1:
                    time.sleep(0.5 + 0.8 * n)
        raise last if last else RuntimeError("request failed")

    def text(self, url, **kw):
        return self.get(url, **kw).text

    def page(self, url, **kw):
        """返回 (最终URL, 文本) —— 处理那种后台换域名的站。"""
        r = self.get(url, **kw)
        return r.url, r.text

    def post_json(self, url, payload=None, headers=None, referer=None):
        url = _fix_url(url)
        if url.startswith("/"):
            url = self.base + url
        body = json.dumps(payload or {}).encode("utf-8")
        hd = {
            "User-Agent": self.ua,
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Content-Type": "application/json;charset=UTF-8",
            "Referer": referer or self.referer,
        }
        ck = self._cookie_header(url)
        if ck:
            hd["Cookie"] = ck
        if headers:
            hd.update(headers)
        req = urllib.request.Request(url, data=body, headers=hd)
        try:
            resp = urllib.request.urlopen(req, timeout=TIMEOUT,
                                          context=self._ssl_ctx())
        except urllib.error.HTTPError as e:
            resp = e
        raw = resp.read()
        self._store_cookies(url, resp.headers)
        if _s(resp.headers.get("Content-Encoding")).lower() == "gzip":
            try:
                raw = gzip.GzipFile(fileobj=io.BytesIO(raw)).read()
            except Exception:
                pass
        return _decode(raw, resp.headers)


class _Blocked(Exception):
    pass


def _decode(raw, headers=None):
    ctype = ""
    try:
        ctype = _s(headers.get("Content-Type")).lower() if headers else ""
    except Exception:
        pass
    m = re.search(r"charset=([\w-]+)", ctype)
    encs = []
    if m:
        encs.append(m.group(1))
    encs += ["utf-8", "gb18030", "big5", "latin-1"]
    for enc in encs:
        try:
            return raw.decode(enc)
        except Exception:
            continue
    return raw.decode("utf-8", "ignore")


_BLOCK_MARK = ("just a moment", "cf-browser-verification", "checking your browser",
               "enable javascript and cookies to continue", "ddos protection",
               "attention required! | cloudflare", "请开启javascript并刷新")


def _looks_blocked(txt):
    low = _s(txt)[:4000].lower()
    return any(k in low for k in _BLOCK_MARK)


# ============================================================
# 通用解析
# ============================================================

def _imgs(block, base):
    """从一段 HTML 里挑出最像封面的图。"""
    cands = re.findall(
        r'(?:data-original|data-src|data-lazy-src|data-echo|data-webp|src)='
        r'"([^"]+)"', block, re.I)
    best = ""
    for c in cands:
        c = _fix_url(c)
        if not c or c.startswith("data:"):
            continue
        if re.search(r"(logo|icon|avatar|blank|placeholder|1x1|loading|sprite)", c, re.I):
            continue
        if not best or c.startswith("http"):
            best = _abs(base, c)
        if best.startswith("http"):
            break
    return best


def _pages(html, base, cur=1):
    """探测这个站的下一页 / 翻页方式。返回 (has_more, next_hint)。"""
    h = _s(html)
    # "下一页" 链接
    for m in re.finditer(r'<a[^>]+href="([^"]+)"[^>]*>([^<]{0,24})</a>', h, re.I):
        if re.search(r"(下一页|下页|next|›|»|后一页)", m.group(2), re.I):
            href = _fix_url(m.group(1))
            if href and not href.startswith(("javascript", "#")):
                return True, _abs(base, href)
    # MacCMS 分页条
    if re.search(r'<a[^>]+href="[^"]*[/_-]%d[/_.-]' % (cur + 1), h):
        return True, ""
    return False, ""


def _m3u8_in(html, base=""):
    """页面里所有的 m3u8/mp4 直链，挑最像正片的那个。"""
    txt = _s(html)
    pats = [
        r'''["'](https?://[^"'<>\\\s]+?\.m3u8[^"'<>\\\s]*)['"]''',
        r'''["'](//[^"'<>\\\s]+?\.m3u8[^"'<>\\\s]*)['"]''',
        r'''["'](/[^"'<>\\\s]+?\.m3u8[^"'<>\\\s]*)['"]''',
        r'''["'](https?://[^"'<>\\\s]+?\.mp4[^"'<>\\\s]*)['"]''',
    ]
    out = []
    for p in pats:
        for m in re.findall(p, txt):
            u = _fix_url(m)
            if not u:
                continue
            if re.search(r"(preview|trailer|advert|ads?/|sample|gif)", u, re.I):
                continue
            u = _abs(base, u) if u.startswith("/") else u
            if u not in out:
                out.append(u)
    # 优先 m3u8，其次 mp4
    for u in out:
        if ".m3u8" in u:
            return u
    return out[0] if out else ""


def _magnets(html, top=12):
    out = []
    for m in re.findall(r'magnet:\?xt=urn:btih:[^"\'<>\s\\]+', _s(html)):
        m = _htmlmod.unescape(m).strip()
        if m not in out:
            out.append(m)
        if len(out) >= top:
            break
    return out


def _clean_title(t):
    t = _txt(t)
    t = re.sub(r"^\[[^\]]{0,20}\]\s*", "", t)
    # JavBobo 那种「站名+口号-真标题」
    t = re.sub(r"^JavBobo\.com[^-]*[-–]\s*", "", t, flags=re.I)
    t = re.sub(r"\s*[-|_]\s*(MISSAV|JavBus|JavDB|JavBobo\S*|好色\S*|91热爆\S*|"
               r"黄色仓库\S*|2AV\S*|夜色A片網\S*|Pornhub\S*)\s*$", "", t, flags=re.I)
    return t.strip()[:140]


# ============================================================
# 站源基类
# ============================================================

class _SrcErr(Exception):
    """站源级可预期错误，隔离在单个站源里，不影响别的站。"""
    pass


class _Src(object):
    id = ""
    name = ""
    base = ""
    cats = [("", "推荐")]
    ua = PC_UA
    filt = []          # 二级筛选组（可选）

    def __init__(self):
        self._sess = None
        self.last_err = ""

    @property
    def sess(self):
        if self._sess is None:
            self._sess = _Session(self.base, self.ua)
        return self._sess

    def _cat0(self):
        return self.cats[0][0] if self.cats else ""

    # 子类按需覆盖这五个
    def categories(self):
        """动态分类（可联网取），返回 [(id,名)]。默认用静态 cats。"""
        return []

    def home(self, pg=1):
        return self.cat(self._cat0(), pg)

    def cat(self, catid, pg=1):
        raise _SrcErr("本站源暂无目录")

    def detail(self, sid):
        raise _SrcErr("本站源暂无详情")

    def play(self, sid, ep):
        """返回 (url, headers)；返回 ('', {}) 表示本站取流要嗅探/不可用。"""
        raise _SrcErr("本站源暂无播放")

    def search(self, key, pg=1):
        raise _SrcErr("本站源不支持搜索")


_SOURCES = []


def _register(cls):
    _SOURCES.append(cls())
    return cls


def _find_src(src_id):
    for s in _SOURCES:
        if s.id == src_id:
            return s
    return None


def _tid(src, cat=""):
    return "%s::c::%s" % (src, _q(cat))


def _did(src, sid):
    return "%s::d::%s" % (src, _q(sid))


def _pid(src, sid, ep):
    return "%s::p::%s::%s" % (src, _q(sid), _q(ep))


def _split_tid(tid):
    head, _, cat = _s(tid).partition("::c::")
    return head, _uq(cat)


def _split_did(did):
    head, _, sid = _s(did).partition("::d::")
    return head, _uq(sid)


def _split_pid(pid):
    head, _, rest = _s(pid).partition("::p::")
    sid, _, ep = rest.partition("::")
    return head, _uq(sid), _uq(ep)


def _vod_item(src_id, sid, title, pic="", remark="", content=""):
    return {
        "vod_id": _did(src_id, sid),
        "vod_name": _clean_title(title) or "未命名",
        "vod_pic": _s(pic),
        "vod_remarks": _s(remark),
        "vod_content": _s(content),
    }


def _vod_detail(src, sid, title, pic="", remark="", content="", year="",
                area="", actor="", chapters=(), play_from=None):
    chapters = list(chapters or [])
    if chapters:
        play_url = "#".join(
            "%s$%s" % (_s(t) or ("线路%d" % (i + 1)), _pid(src.id, sid, k))
            for i, (k, t) in enumerate(chapters))
    else:
        play_url = "暂无播放线路$%s" % _pid(src.id, sid, "@@none@@")
    d = {
        "vod_id": _did(src.id, sid),
        "vod_name": _clean_title(title),
        "vod_pic": _s(pic),
        "vod_remarks": _s(remark),
        "vod_content": _s(content),
        "vod_year": _s(year),
        "vod_area": _s(area),
        "vod_actor": _s(actor),
        "vod_play_from": _s(play_from) or src.name,
        "vod_play_url": play_url,
    }
    return d


def _err_text(e):
    try:
        if isinstance(e, urllib.error.HTTPError):
            code = int(getattr(e, "code", 0) or 0)
            if code in (401, 403, 412, 429):
                return "站点挡了本机网络(HTTP %d)，换个网络或稍后再试" % code
            if code == 404:
                return "页面不存在(404)"
            return "站点返回 HTTP %d" % code
        if isinstance(e, urllib.error.URLError):
            return "网络连不上：%s" % (_s(getattr(e, "reason", "")) or "超时")
        if isinstance(e, _Blocked):
            return "站点弹了人机校验，稍后再试"
        if isinstance(e, _SrcErr):
            return _s(e)
    except Exception:
        pass
    return "%s" % _s(e)[:100]


# ============================================================
# 1. 好色TV  hsex.tv
#    分类：6 个列表页 + 首页热搜词（动态）
#    取流：详情页里 <source id="video-source"> 是 m3u8 直链
# ============================================================

@_register
class _Hsex(_Src):
    id = "hsex"
    name = "好色TV"
    base = "https://hsex.tv"
    cats = [("new", "最新"), ("top", "Top榜"), ("top7", "Top7"),
            ("long", "长片"), ("5min", "5分钟")]

    _LIST = {"new": "/list-%d.htm", "top": "/top_list-%d.htm",
             "top7": "/top7_list-%d.htm", "long": "/long_list-%d.htm",
             "5min": "/5min_list-%d.htm", "hot": "/list-%d.htm"}

    def refresh(self):
        """首页热搜词，站方会换，所以运行时拉。"""
        out = []
        try:
            h = self.sess.text(self.base + "/")
            for m in re.finditer(
                    r'href="search\.htm\?search=([^&"]+)&sort=([a-z]+)"[^>]*>'
                    r'\s*&nbsp;([^<&]+)&nbsp;', h):
                w = _uq(m.group(1))
                nm = _txt(m.group(3)) or w
                out.append(("s:%s" % w, "搜·%s" % nm))
        except Exception:
            pass
        return out

    def categories(self):
        # 列表页分类放前面（稳定），首页热搜词跟在后面
        res = list(self.cats)
        seen = set(x[1] for x in res)
        for k, v in self.refresh():
            if v not in seen:
                seen.add(v)
                res.append((k, v))
        return res[:30]

    def _card(self, block, base):
        m = re.search(r'<a[^>]+href="(video-(\d+)\.htm)"', block, re.I)
        if not m:
            return None
        sid = m.group(2)
        t = ""
        tm = re.search(r'title\s*=\s*"([^"]*)"', block, re.I)
        if tm:
            t = tm.group(1)
        if not t:
            tm = re.search(r'<h5>\s*<a[^>]*>([^<]+)</a>', block, re.I)
            t = tm.group(1) if tm else ""
        pic = _imgs(block, base)
        dur = ""
        dm = re.search(r'<var class="duration">([^<]*)</var>', block, re.I)
        if dm:
            dur = _txt(dm.group(1))
        return _vod_item(self.id, sid, t, pic, dur)

    def _parse(self, h, base):
        items = []
        # 每张卡片一个 <div class="thumbnail"> ... </div></div>
        for m in re.finditer(r'(?s)<div class="thumbnail">(.*?)</div>\s*</div>', h):
            it = self._card(m.group(1), base)
            if it:
                items.append(it)
        if not items:
            for m in re.finditer(r'(?s)<div class="thumbnail">(.*?)(?=<div class="thumbnail">|</body>)', h):
                it = self._card(m.group(1), base)
                if it:
                    items.append(it)
        seen, out = set(), []
        for it in items:
            k = it["vod_id"]
            if k in seen:
                continue
            seen.add(k)
            out.append(it)
        return out

    def cat(self, catid, pg=1):
        catid = _s(catid) or "hot"
        pg = max(1, int(pg or 1))
        if catid.startswith("s:"):
            kw = catid[2:]
            return self.search(kw, pg)
        tpl = self._LIST.get(catid)
        if not tpl:
            tpl = self._LIST["hot"]
        url = self.base + (tpl % pg)
        base, h = self.sess.page(url)
        items = self._parse(h, base)
        if not items and pg == 1:
            # 分类页挂了就退回首页取点东西
            b2, h2 = self.sess.page(self.base + "/")
            items = self._parse(h2, b2)
        more = bool(items) and pg < 200
        return items, more

    def search(self, key, pg=1):
        key = _s(key)
        if not key:
            return [], False
        url = "%s/search.htm?search=%s&sort=new" % (self.base, _q(key))
        base, h = self.sess.page(url)
        items = self._parse(h, base)
        seen, out = set(), []
        for it in items:
            if it["vod_id"] in seen:
                continue
            seen.add(it["vod_id"])
            out.append(it)
        return out, False

    def detail(self, sid):
        url = "%s/video-%s.htm" % (self.base, _s(sid).lstrip("/"))
        base, h = self.sess.page(url, referer=self.base + "/")
        title = ""
        m = re.search(r'<h3 class="panel-title">([^<]+)</h3>', h, re.I)
        if m:
            title = m.group(1)
        if not title:
            m = re.search(r'<title>(.*?)</title>', h, re.S)
            title = m.group(1) if m else ""
        pic = ""
        m = re.search(r'poster="([^"]+)"', h, re.I)
        if m:
            pic = _abs(base, m.group(1))
        if not pic:
            m = re.search(r'<meta[^>]+property="og:image"[^>]+content="([^"]+)"', h, re.I)
            if m:
                pic = _abs(base, m.group(1))
        # 时长 / 观看数当备注
        remark = ""
        m = re.search(r'class="duration">([^<]+)</', h, re.I)
        if m:
            remark = _txt(m.group(1))
        m3u8 = ""
        m = re.search(r'<source[^>]+id="video-source"[^>]+src="([^"]+)"', h, re.I)
        if m:
            m3u8 = _abs(base, m.group(1))
        if not m3u8:
            m3u8 = _m3u8_in(h, base)
        content = "好色TV · 直链播放" if m3u8 else "好色TV · 该页没给直链，走嗅探"
        chs = [("main", "播放")] if m3u8 else [("sniff", "嗅探播放")]
        vod = _vod_detail(self, sid, title, pic, remark, content, chapters=chs)
        vod["_m3u8"] = m3u8
        return vod

    def play(self, sid, ep):
        # 详情已经拿到直链就现取一遍（地址会过期，每次重新取）
        if _s(ep) == "sniff":
            return "", {}
        d = self.detail(sid)
        u = _s(d.get("_m3u8"))
        if not u:
            raise _SrcErr("这页没直接给播放地址")
        return u, {"User-Agent": self.ua, "Referer": self.base + "/"}


# ============================================================
# 2. JavDB  javdb.com
#    分类：有码/无码/欧美/动漫/FC2 + 日周月榜 + TOP250
#    取流：详情页里的磁力（多为高清原盘）
# ============================================================

@_register
class _Javdb(_Src):
    id = "javdb"
    name = "JavDB"
    base = "https://javdb.com"
    cats = [
        ("/censored", "有码"), ("/western", "欧美"), ("/", "最新"),
        ("/rankings/movies?p=daily&t=censored", "日榜·有码"),
        ("/rankings/movies?p=weekly&t=censored", "周榜·有码"),
        ("/rankings/movies?p=monthly&t=censored", "月榜·有码"),
        ("/rankings/movies?p=daily&t=western", "日榜·欧美"),
        ("/rankings/movies?p=weekly&t=western", "周榜·欧美"),
        ("/?vft=1&vst=2", "磁链更新"),
    ]

    def _parse(self, h, base):
        items = []
        for m in re.finditer(r'(?s)<div class="item">\s*<a href="([^"]+)"'
                             r'[^>]*title="([^"]*)"[^>]*>(.*?)</a>', h):
            href, title, block = m.group(1), m.group(2), m.group(3)
            sid = href.strip("/")
            pic = _imgs(block, base)
            if not pic:
                mm = re.search(r'src="([^"]+\.jpe?g[^"]*)"', block, re.I)
                if mm:
                    pic = _abs(base, mm.group(1))
            remark = ""
            mm = re.search(r'<span class="tag is-success">([^<]*)</span>', block)
            if mm:
                remark = _txt(mm.group(1))
            mm = re.search(r'class="meta">\s*([^<]+?)\s*<', block)
            if mm:
                d = _txt(mm.group(1))
                remark = (remark + " " + d).strip()
            if len(sid) > 60 or not re.match(r"^v/", sid):
                continue
            items.append(_vod_item(self.id, sid, title, pic, remark))
        seen, out = set(), []
        for it in items:
            if it["vod_id"] in seen:
                continue
            seen.add(it["vod_id"])
            out.append(it)
        return out

    def cat(self, catid, pg=1):
        path = _s(catid) or "/censored"
        pg = max(1, int(pg or 1))
        if pg > 1:
            sep = "&" if "?" in path else "?"
            url = "%s%s%spage=%d" % (self.base, path, sep, pg)
        else:
            url = self.base + path
        base, h = self.sess.page(url)
        items = self._parse(h, base)
        more = bool(items) and pg < 60
        return items, more

    def search(self, key, pg=1):
        key = _s(key)
        if not key:
            return [], False
        url = "%s/search?q=%s&f=all" % (self.base, _q(key))
        if int(pg or 1) > 1:
            url += "&page=%s" % int(pg)
        base, h = self.sess.page(url)
        items = self._parse(h, base)
        # 搜索页有时是 .movie-list 里的 .item，兜一层
        if not items:
            for m in re.finditer(r'(?s)<a href="(/v/[^"]+)"[^>]*title="([^"]*)"(.*?)</a>', h):
                pic = _imgs(m.group(3), base)
                items.append(_vod_item(self.id, m.group(1).strip("/"),
                                       m.group(2), pic))
        return items, False

    def detail(self, sid):
        sid = _s(sid).strip("/")
        url = sid if sid.startswith("http") else "%s/%s" % (self.base, sid)
        base, h = self.sess.page(url)
        title = ""
        m = re.search(r'<title>(.*?)</title>', h, re.S)
        if m:
            title = _clean_title(m.group(1))
        m = re.search(r'<h2 class="title is-4">\s*(?:<strong>([^<]*)</strong>)?\s*([^<]*)<', h)
        if m:
            title = "%s %s" % (_s(m.group(1)), _txt(m.group(2)))
        pic = ""
        m = re.search(r'<img[^>]+class="video-cover"[^>]+src="([^"]+)"', h, re.I)
        if not m:
            m = re.search(r'<meta[^>]+property="og:image"[^>]+content="([^"]+)"', h, re.I)
        if m:
            pic = _abs(base, m.group(1))
        info = []
        for mm in re.finditer(r'<span class="value">([^<]{1,60})</span>', h):
            v = _txt(mm.group(1))
            if v and v not in info:
                info.append(v)
        mgs = _magnets(h, top=20)
        remark = ("%d 个磁力" % len(mgs)) if mgs else ""
        content = "JavDB · " + (" / ".join(info[:4]) if info else "")
        if mgs:
            content += "\n提示：本站给的是磁力，播放器要支持磁力/BT 才能直接播。"
        chs = [(m["h"], "磁力 %d" % (i + 1)) for i, m in enumerate(
            [{"h": mm} for mm in mgs])]
        if not chs:
            chs = [("magnet-0", "暂无磁力")]
        vod = _vod_detail(self, sid, title, pic, remark, content, chapters=chs)
        vod["_magnets"] = mgs
        return vod

    def play(self, sid, ep):
        d = self.detail(sid)
        mgs = d.get("_magnets") or []
        try:
            i = int(_s(ep).split("-")[-1])
        except Exception:
            i = 0
        if mgs:
            idx = 0 if i <= 0 else min(i - 1, len(mgs) - 1)
            return mgs[idx], {}
        raise _SrcErr("这片现在没挂磁力")


# ============================================================
# 3. MissAV  missav.app   （MacCMS）
# ============================================================

@_register
class _Missav(_Src):
    id = "missav"
    name = "MissAV"
    base = "https://missav.app"
    cats = [("/vodtype/28/", "中文字幕"), ("/vodtype/1/", "最新"),
            ("/vodtype/2/", "日韩"), ("/vodtype/3/", "欧美"),
            ("/vodtype/4/", "动漫")]

    def categories(self):
        try:
            h = self.sess.text(self.base + "/")
            out = []
            for m in re.finditer(r'href="(/vodtype/(\d+)/)"[^>]*>\s*([^<]{1,16})\s*<', h):
                nm = _txt(m.group(3))
                if nm and nm not in [x[1] for x in out]:
                    out.append((m.group(1), nm))
            return out[:24]
        except Exception:
            return []

    def _parse(self, h, base):
        items = []
        # 条目： <a href="/vodplay/{id}-1-1/"> ... 标题/封面 在附近
        blocks = re.split(r'(?=<a href="/vodplay/)', h)
        for b in blocks[1:]:
            b = b[:1400]
            m = re.match(r'<a href="/vodplay/(\d+)-(\d+)-(\d+)/"', b)
            if not m:
                continue
            sid = m.group(1)
            pic = _imgs(b, base)
            t = ""
            tm = re.search(r'alt="([^"]{2,120})"', b)
            if tm:
                t = tm.group(1)
            if not t:
                tm = re.search(r'title="([^"]{2,120})"', b)
                t = tm.group(1) if tm else ""
            if not t:
                tm = re.search(r'<div class="text-secondary[^"]*">\s*([^<]{2,120})\s*<', b)
                t = tm.group(1) if tm else ""
            if not t:
                t = "作品 %s" % sid
            remark = ""
            dm = re.search(r'>(\d{1,2}:\d{2}(?::\d{2})?)<', b)
            if dm:
                remark = dm.group(1)
            items.append(_vod_item(self.id, sid, t, pic, remark))
        seen, out = set(), []
        for it in items:
            if it["vod_id"] in seen:
                continue
            seen.add(it["vod_id"])
            out.append(it)
        return out

    def cat(self, catid, pg=1):
        path = _s(catid) or "/vodtype/28/"
        pg = max(1, int(pg or 1))
        if not path.startswith("/"):
            path = "/" + path
        if not path.endswith("/"):
            path += "/"
        url = "%s%s" % (self.base, path) if pg == 1 else \
            "%s%s%d/" % (self.base, path, pg)
        base, h = self.sess.page(url)
        items = self._parse(h, base)
        if not items and pg == 1:
            base, h = self.sess.page(self.base + "/")
            items = self._parse(h, base)
        return items, bool(items) and pg < 500

    def search(self, key, pg=1):
        key = _s(key)
        if not key:
            return [], False
        cands = ["%s/vodsearch/%s-------------.html" % (self.base, _q(key)),
                 "%s/vodsearch/%s-------------/%s/" % (self.base, _q(key), int(pg or 1)),
                 "%s/vodsearch/%s----------%s---.html" % (self.base, _q(key), int(pg or 1))]
        for url in cands:
            try:
                base, h = self.sess.page(url)
            except Exception:
                continue
            items = self._parse(h, base)
            if items:
                return items, False
        return [], False

    def detail(self, sid):
        sid = _s(sid)
        url = "%s/vodplay/%s-1-1/" % (self.base, sid)
        base, h = self.sess.page(url)
        j = _json_in(h, "player_aaaa")
        title, pic, cover = "", "", ""
        if isinstance(j, dict):
            vd = j.get("vod_data") or {}
            title = _pick(vd, "vod_name") or _pick(j, "vod_name")
            pic = _pick(vd, "vod_pic") or _pick(j, "vod_pic")
            cover = _pick(vd, "vod_pic_thumb")
        if not title:
            m = re.search(r'<title>(.*?)</title>', h, re.S)
            title = _clean_title(m.group(1)) if m else "作品 %s" % sid
        if not pic:
            m = re.search(r'<meta[^>]+property="og:image"[^>]+content="([^"]+)"', h, re.I)
            if m:
                pic = _abs(base, m.group(1))
        m3u8 = _m3u8_in(h, base)
        remark = "直链" if m3u8 else "嗅探"
        vod = _vod_detail(self, sid, title, pic, remark,
                          "MissAV · " + ("m3u8 直链" if m3u8 else "走嗅探"),
                          chapters=[("main", "播放")])
        vod["_m3u8"] = m3u8
        return vod

    def play(self, sid, ep):
        d = self.detail(sid)
        u = _s(d.get("_m3u8"))
        if not u:
            raise _SrcErr("这页没解析出直链")
        return u, {"User-Agent": self.ua, "Referer": self.base + "/"}


# ============================================================
# 4. 夜色A片網  yese.co  （MacCMS 魔改）
# ============================================================

@_register
class _Yese(_Src):
    id = "yese"
    name = "夜色A片"
    base = "https://yese.co"
    cats = [("无码专区", "无码专区"), ("中文字幕", "中文字幕"),
            ("国产主播", "国产主播"), ("国产自拍", "国产自拍"),
            ("亚洲情色", "亚洲情色"), ("欧美性爱", "欧美性爱"),
            ("卡通动画", "卡通动画"), ("熟女人妻", "熟女人妻"),
            ("巨乳美乳", "巨乳美乳"), ("制服诱惑", "制服诱惑"),
            ("Hey动画", "Hey动画")]

    def categories(self):
        try:
            h = self.sess.text("%s/index.php/vod/show/class/无码专区/id/1.html" % self.base)
            out = []
            for m in re.finditer(r'href="/vod/show/class/([^"/]+)/id/\d+/"[^>]*>([^<]{1,14})<', h):
                nm = _txt(m.group(2))
                if nm and nm not in [x[1] for x in out]:
                    out.append((_uq(m.group(1)), nm))
            return out[:28]
        except Exception:
            return []

    def _parse(self, h, base):
        items = []
        blocks = re.split(r'(?=<a[^>]+href="/vod/play/id/)', h)
        if len(blocks) <= 1:
            blocks = re.split(r'(?=<div class="col-6 col-sm-4 col-lg-3">)', h)
        for b in blocks[1:]:
            b = b[:1500]
            m = re.search(r'href="(/vod/play/id/(\d+)/sid/(\d+)/nid/(\d+)/)"', b)
            if not m:
                continue
            sid = m.group(2)
            pic = _imgs(b, base)
            t = ""
            tm = re.search(r'title="([^"]{2,140})"', b)
            if tm:
                t = tm.group(1)
            if not t:
                tm = re.search(r'<h[34][^>]*>\s*<a[^>]*>([^<]{2,140})</a>', b)
                t = tm.group(1) if tm else ""
            if not t:
                tm = re.search(r'class="title[^"]*"[^>]*>\s*(?:<[^>]+>\s*)*([^<]{2,140})', b)
                t = tm.group(1) if tm else ""
            if not t:
                t = "作品 %s" % sid
            remark = ""
            dm = re.search(r'>(\d{1,2}:\d{2}(?::\d{2})?)<', b)
            if dm:
                remark = dm.group(1)
            items.append(_vod_item(self.id, sid, t, pic, remark))
        return items

    def cat(self, catid, pg=1):
        cat = _s(catid) or (self.cats[0][0] if self.cats else "")
        pg = max(1, int(pg or 1))
        url = "%s/index.php/vod/show/class/%s/id/%d.html" % (
            self.base, _q(cat), pg)
        base, h = self.sess.page(url)
        items = self._parse(h, base)
        return items, bool(items) and pg < 300

    def search(self, key, pg=1):
        key = _s(key)
        if not key:
            return [], False
        cands = ["%s/index.php/vod/search.html?wd=%s" % (self.base, _q(key)),
                 "%s/index.php/vod/search/page/%d/wd/%s.html" % (
                     self.base, int(pg or 1), _q(key))]
        for url in cands:
            try:
                base, h = self.sess.page(url)
            except Exception:
                continue
            items = self._parse(h, base)
            if items:
                return items, False
        return [], False

    def detail(self, sid):
        sid = _s(sid)
        url = "%s/index.php/vod/detail/id/%s.html" % (self.base, sid)
        base, h = self.sess.page(url)
        title = ""
        m = re.search(r'<title>(.*?)</title>', h, re.S)
        if m:
            title = _clean_title(m.group(1))
        pic = ""
        m = re.search(r'<meta[^>]+property="og:image"[^>]+content="([^"]+)"', h, re.I)
        if m:
            pic = _abs(base, m.group(1))
        info = []
        for mm in re.finditer(r'<span[^>]*>\s*(?:类型|地区|年份|主演|导演|时间)[:：]\s*'
                              r'</span>\s*([^<]{1,60})', h):
            v = _txt(mm.group(1))
            if v:
                info.append(v)
        if not info:
            m = re.search(r'<p class="[^"]*desc[^"]*"[^>]*>(.*?)</p>', h, re.S)
            if m:
                info.append(_txt(m.group(1))[:160])
        content = "夜色A片網 · " + " / ".join(info[:3])
        # 线路：从播放页里拿 m3u8，先尝试第一线路
        m3u8 = ""
        for pu in ["%s/index.php/vod/play/id/%s/sid/1/nid/1.html" % (self.base, sid),
                   "%s/vod/play/id/%s/sid/1/nid/1/" % (self.base, sid)]:
            try:
                b2, h2 = self.sess.page(pu)
                m3u8 = _m3u8_in(h2, b2)
                if m3u8:
                    break
            except Exception:
                continue
        chs = [("sid1nid1", "线路1")] if m3u8 else [("sniff", "嗅探播放")]
        vod = _vod_detail(self, sid, title, pic,
                          "直链" if m3u8 else "嗅探", content, chapters=chs)
        vod["_m3u8"] = m3u8
        return vod

    def play(self, sid, ep):
        if _s(ep) == "sniff":
            raise _SrcErr("这站要壳去嗅探")
        d = self.detail(sid)
        u = _s(d.get("_m3u8"))
        if not u:
            raise _SrcErr("没解析出直链")
        return u, {"User-Agent": self.ua, "Referer": self.base + "/"}


# ============================================================
# 5. 黄色仓库  hsck.cc（域名会跳，跟随跳转）
# ============================================================

@_register
class _Hsck(_Src):
    id = "hsck"
    name = "黄色仓库"
    base = "https://hsck.cc"
    cats = [("/vodtype/1.html", "日韩AV"), ("/vodtype/2.html", "国产系列"),
            ("/vodtype/3.html", "欧美"), ("/vodtype/4.html", "成人动漫")]
    _live = ""

    def _entry(self):
        """这站会 301 到一串轮换域名，先跟一次拿到当前可用基址。"""
        if self._live:
            return self._live
        for probe in (self.base + "/", self.base):
            try:
                u, _ = self.sess.page(probe)
                if u and u.startswith("http"):
                    p = urllib.parse.urlparse(u)
                    if p.netloc and p.netloc != urllib.parse.urlparse(self.base).netloc:
                        self._live = "%s://%s" % (p.scheme, p.netloc)
                        return self._live
            except Exception:
                continue
        self._live = self.base
        return self._live

    def _parse(self, h, base):
        items = []
        blocks = re.split(r'(?=<div class="stui-vodlist__box")', h)
        for b in blocks[1:]:
            b = b[:1400]
            m = re.search(r'href="([^"]*?/v5/(\d+)-\d+-\d+\.html)"', b)
            if not m:
                # 有些条目直接外跳，跳过
                continue
            sid = m.group(2)
            pic = _imgs(b, base)
            t = ""
            tm = re.search(r'title="([^"]{2,140})"', b)
            if tm:
                t = tm.group(1)
            if not t:
                tm = re.search(r'<h4 class="title">\s*<a[^>]*>([^<]{2,140})</a>', b)
                t = tm.group(1) if tm else ""
            if not t:
                t = "作品 %s" % sid
            items.append(_vod_item(self.id, sid, t, pic))
        if not items:
            for m in re.finditer(r'href="(/v5/(\d+)-\d+-\d+\.html)"[^>]*>\s*'
                                 r'([^<]{2,140})\s*<', h):
                items.append(_vod_item(self.id, m.group(2), m.group(3)))
        return items

    def cat(self, catid, pg=1):
        path = _s(catid) or "/vodtype/1.html"
        pg = max(1, int(pg or 1))
        live = self._entry()
        if pg > 1:
            path = re.sub(r"\.html$", "", path) + ("-%d.html" % pg)
        url = live + path
        base, h = self.sess.page(url, referer=live + "/")
        items = self._parse(h, base)
        if not items and pg == 1:
            b2, h2 = self.sess.page(live + "/", referer=live + "/")
            items = self._parse(h2, b2 or live)
        return items, bool(items) and pg < 200

    def search(self, key, pg=1):
        key = _s(key)
        if not key:
            return [], False
        live = self._entry()
        cands = ["%s/vodsearch/%s-------------.html" % (live, _q(key)),
                 "%s/vodsearch/-------------.html?wd=%s" % (live, _q(key))]
        for url in cands:
            try:
                base, h = self.sess.page(url, referer=live + "/")
            except Exception:
                continue
            items = self._parse(h, base)
            if items:
                return items, False
        return [], False

    def _media(self, h, base, sid):
        """这站的流是点「立即播放」后 POST 出来的：
        POST {live}/static/count.php  body=id/sid/nid/tk/g/x/y/dt/sw/sh/tz/t
        返回 {ok:true, u: base64(直链)}。"""
        g = lambda k: (re.search(r"%s\s*=\s*'([^']+)'" % k, h) or [None, ""])[1]
        aid = g("AID") or _s(sid).split("-")[0]
        asid = g("ASID") or "1"
        anid = g("ANID") or "1"
        tk = g("AK")
        if not (aid and tk):
            return ""
        p = urllib.parse.urlparse(base if _s(base).startswith("http") else (self._live or self.base))
        origin = "%s://%s" % (p.scheme or "https", p.netloc)
        body = urllib.parse.urlencode({
            "id": aid, "sid": asid, "nid": anid, "tk": tk, "g": "1",
            "x": "120", "y": "300", "dt": "1500", "sw": "412", "sh": "915",
            "tz": "-480", "t": str(int(time.time() * 1000)),
        }).encode("utf-8")
        try:
            req = urllib.request.Request(
                origin + "/static/count.php", data=body,
                headers={
                    "User-Agent": self.ua,
                    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                    "X-Requested-With": "XMLHttpRequest",
                    "Referer": "%s/v5/%s-%s-%s.html" % (origin, aid, asid, anid),
                })
            raw = urllib.request.urlopen(req, timeout=TIMEOUT,
                                         context=self.sess._ssl_ctx()).read()
            j = json.loads(raw.decode("utf-8", "ignore") or "{}")
            u = _s(j.get("u"))
            if u:
                import base64 as _b64
                return _b64.b64decode(u).decode("utf-8", "ignore").strip()
        except Exception:
            pass
        return ""

    def detail(self, sid):
        sid = _s(sid)
        live = self._entry()
        url = "%s/v5/%s-1-1.html" % (live, sid)
        base, h = self.sess.page(url, referer=live + "/")
        title = ""

        tm = re.search(r'<title>(.*?)</title>', h, re.S)
        if tm:
            title = _clean_title(tm.group(1))
        pic = ""
        m = re.search(r'<meta[^>]+property="og:image"[^>]+content="([^"]+)"', h, re.I)
        if m:
            pic = _abs(base, m.group(1))
        m3u8 = _m3u8_in(h, base)
        if not m3u8:
            m3u8 = self._media(h, base or live, sid)
        vod = _vod_detail(self, sid, title, pic, "直链" if m3u8 else "嗅探",
                          "黄色仓库 · " + ("m3u8 直链" if m3u8 else "走嗅探"),
                          chapters=[("main", "播放")])
        vod["_m3u8"] = m3u8
        return vod

    def play(self, sid, ep):
        d = self.detail(sid)
        u = _s(d.get("_m3u8"))
        if not u:
            raise _SrcErr("没解析出直链")
        return u, {"User-Agent": self.ua, "Referer": (self._live or self.base) + "/"}


# ============================================================
# 6. JavBus  javbus.com
#    分类：有码 / 无码；取流：详情页的磁力（AJAX 接口）
# ============================================================

@_register
class _Javbus(_Src):
    id = "javbus"
    name = "JavBus"
    base = "https://www.javbus.com"
    cats = [("", "有码"), ("/uncensored", "无码"), ("/genre", "类别"),
            ("/actresses", "女优"), ("/studio", "片商")]

    def _parse(self, h, base):
        items = []
        blocks = re.split(r'(?=<a class="movie-box")', h)
        if len(blocks) <= 1:
            blocks = re.split(r'(?=<div class="item")', h)
        if len(blocks) <= 1:
            # 兜底：直接从链接+标题抠
            for m in re.finditer(
                    r'href="(https?://[^"]*?/([A-Za-z0-9_\-]{4,40}))"[^>]*>\s*'
                    r'(?:<[^>]+>\s*)*([^<]{2,120})', h):
                sid = m.group(2)
                if sid.lower() in ("genre", "actresses", "studio", "uncensored",
                                   "page", "search", "forum", "en", "ja", "ko",
                                   "www", "com"):
                    continue
                items.append(_vod_item(self.id, sid, m.group(3)))
            return items
        for b in blocks[1:]:
            b = b[:1200]
            m = re.search(r'href="([^"]*?/([A-Za-z0-9_\-]{4,40}))"', b)
            if not m:
                continue
            href, sid = m.group(1), m.group(2)
            if sid.lower() in ("genre", "actresses", "studio", "uncensored",
                               "page", "search", "forum", "en", "ja", "ko"):
                continue
            pic = _imgs(b, base)
            t = ""
            tm = re.search(r'<span class="title">([^<]{2,120})</span>', b)
            if tm:
                t = tm.group(1)
            if not t:
                tm = re.search(r'<div class="photo-info">(.*?)</div>', b, re.S)
                if tm:
                    t = _txt(tm.group(1))
            if not t:
                tm = re.search(r'<date>([^<]{2,80})</date>', b)
                t = tm.group(1) if tm else ""
            if not t:
                t = sid
            remark = ""
            dm = re.search(r'<date>([^<]+)</date>', b)
            if dm:
                remark = _txt(dm.group(1))
            items.append(_vod_item(self.id, sid, t, pic, remark))
        return items

    def cat(self, catid, pg=1):
        path = _s(catid)
        pg = max(1, int(pg or 1))
        if path in ("/genre", "/actresses", "/studio", ""):
            # 这些是目录页，不是片列表；用最新有码/无码兜底
            path = "" if path != "/uncensored" else "/uncensored"
        url = self.base + path
        if pg > 1:
            url = url.rstrip("/") + ("/page/%d" % pg if path else "/page/%d" % pg)
        base, h = self.sess.page(url)
        items = self._parse(h, base)
        return items, bool(items) and pg < 200

    def search(self, key, pg=1):
        key = _s(key)
        if not key:
            return [], False
        cands = ["%s/search/%s" % (self.base, _q(key)),
                 "%s/search/%s/1" % (self.base, _q(key))]
        for url in cands:
            try:
                base, h = self.sess.page(url)
            except Exception:
                continue
            items = self._parse(h, base)
            if items:
                return items, False
        return [], False

    def _magnet_ajax(self, h):
        gid = ""
        m = re.search(r"var\s+gid\s*=\s*(\d+)", h) or \
            re.search(r"gid\s*=\s*['\"]?(\d+)", h)
        if m:
            gid = m.group(1)
        uc = "1"
        m = re.search(r"var\s+uc\s*=\s*(\d+)", h)
        if m:
            uc = m.group(1)
        img = ""
        m = re.search(r"var\s+img\s*=\s*['\"]([^'\"]+)", h)
        if m:
            img = m.group(1)
        if not gid:
            return []
        u = ("%s/ajax/uncledatoolsbyajax.php?gid=%s&lang=zh&img=%s&uc=%s"
             % (self.base, gid, _q(img), uc))
        try:
            t = self.sess.text(u, referer=self.base + "/",
                               headers={"X-Requested-With": "XMLHttpRequest"})
        except Exception:
            return []
        return _magnets(t, top=20)

    def detail(self, sid):
        sid = _s(sid).strip("/")
        url = sid if sid.startswith("http") else "%s/%s" % (self.base, sid)
        base, h = self.sess.page(url)
        title = ""
        m = re.search(r'<title>(.*?)</title>', h, re.S)
        if m:
            title = _clean_title(m.group(1))
        pic = ""
        m = re.search(r'<a class="bigImage"[^>]+href="([^"]+)"', h)
        if m:
            pic = _abs(base, m.group(1))
        if not pic:
            m = re.search(r'<meta[^>]+property="og:image"[^>]+content="([^"]+)"', h, re.I)
            if m:
                pic = _abs(base, m.group(1))
        info = []
        for mm in re.finditer(r'<span class="header">([^<]{1,10})</span>\s*</p>\s*'
                              r'<p[^>]*>(.*?)</p>', h, re.S):
            v = _txt(mm.group(2))
            if v:
                info.append("%s%s" % (_txt(mm.group(1)), v[:60]))
        mgs = self._magnet_ajax(h)
        chs = [("magnet-%d" % (i + 1), "磁力 %d" % (i + 1)) for i in range(len(mgs))]
        if not chs:
            chs = [("magnet-0", "暂无磁力")]
        content = "JavBus · " + (" / ".join(info[:3]) or "")
        if mgs:
            content += "\n本站给的是磁力，播放器要支持磁力/BT。"
        vod = _vod_detail(self, sid, title, pic,
                          ("%d 磁力" % len(mgs)) if mgs else "", content, chapters=chs)
        vod["_magnets"] = mgs
        return vod

    def play(self, sid, ep):
        d = self.detail(sid)
        mgs = d.get("_magnets") or []
        if not mgs:
            raise _SrcErr("这片现在没挂磁力")
        try:
            i = int(_s(ep).split("-")[-1])
        except Exception:
            i = 1
        return mgs[max(0, min(i - 1, len(mgs) - 1))], {}


# ============================================================
# 7. 91热爆  91rb.net  （列表/分类/搜索/详情齐全，取流要会员）
# ============================================================

@_register
class _Rb91(_Src):
    id = "rb91"
    name = "91热爆"
    base = "https://www.91rb.net"
    cats = [("/latest-updates/", "最新"), ("/most-popular/", "最热"),
            ("/categories/cnav/", "国产"), ("/categories/ry/", "日韩"),
            ("/categories/wm/", "无码"), ("/categories/dm/", "动漫"),
            ("/categories/cctv/", "偷拍"), ("/categories/tan-hua/", "探花")]

    def _parse(self, h, base):
        items = []
        # 这站卡片 class 有两种写法：thumb thumb_rel item / thumb item thumb_rel
        for m in re.finditer(
                r'<div class="thumb[^"]*">\s*'
                r'<a href="([^"]+)"\s+title="([^"]*)"(.*?)(?=<div class="thumb[^"]*">|</body>)',
                h, re.S):
            href, title, block = m.group(1), m.group(2), m.group(3)
            mm = re.search(r'/videos/(\d+)/', href)
            if not mm:
                continue
            # sid 存成相对路径，详情页才找得到（真地址带一串 hash）
            sid = urllib.parse.urlparse(_abs(base, href)).path.strip("/")
            if not sid:
                sid = "videos/%s/" % mm.group(1)
            pic = _imgs(block, base)
            dur = ""
            dm = re.search(r'class="item-time[^"]*">\s*([^<]+?)\s*<', block)
            if dm:
                dur = _txt(dm.group(1))
            items.append(_vod_item(self.id, sid, title, pic, dur))
        seen, out = set(), []
        for it in items:
            if it["vod_id"] in seen:
                continue
            seen.add(it["vod_id"])
            out.append(it)
        return out

    def _cat_list(self, h):
        out = []
        for m in re.finditer(r'href="([^"]*/categories/[^"/]+/)"[^>]*>\s*([^<]{1,12})\s*<', h):
            nm = _txt(m.group(2))
            if nm and nm not in [x[1] for x in out]:
                out.append((m.group(1), nm))
        return out

    def categories(self):
        # 静态分类放前面（实测有货），站方导航里挖出来的跟后面
        res = list(self.cats)
        seen = set(x[1] for x in res)
        try:
            h = self.sess.text(self.base + "/")
            for cid, cname in self._cat_list(h):
                # 只收分类页，站点导航里那些特殊页（频道/收藏）是死页
                cid = _s(cid)
                if "/categories/" not in cid:
                    continue
                if cname and cname not in seen:
                    seen.add(cname)
                    res.append((cid, cname))
        except Exception:
            pass
        return res[:28]

    def cat(self, catid, pg=1):
        path = _s(catid) or "/latest-updates/"
        pg = max(1, int(pg or 1))
        if not path.startswith("http"):
            path = self.base + path
        if pg > 1:
            path = path.rstrip("/") + "/%d/" % pg
        base, h = self.sess.page(path)
        items = self._parse(h, base)
        return items, bool(items) and pg < 500

    def search(self, key, pg=1):
        key = _s(key)
        if not key:
            return [], False
        url = "%s/search/%s/" % (self.base, _q(key))
        try:
            base, h = self.sess.page(url)
        except Exception:
            base, h = self.sess.page("%s/search/?q=%s" % (self.base, _q(key)))
        items = self._parse(h, base)
        return items, False

    def detail(self, sid):
        sid = _s(sid).strip("/")
        if "videos/" in sid or "/" in sid:
            url = "%s/%s/" % (self.base, sid)
        else:
            url = "%s/videos/%s/" % (self.base, sid)
        try:
            base, h = self.sess.page(url)
        except Exception:
            base, h = "", ""
        if not h or "<video" not in h.lower() and "og:title" not in h:
            # 没跳到位，去最新列表里把真地址捞回来
            try:
                b2, h2 = self.sess.page(self.base + "/latest-updates/")
                m = re.search(r'href="([^"]*?%s[^"]*)"' % re.escape(sid[:12]), h2)
                if m:
                    base, h = self.sess.page(_abs(b2, m.group(1)))
            except Exception:
                pass
        if not h:
            raise _SrcErr("这片详情页打不开")
        title = ""
        m = re.search(r'<h1[^>]*>(.*?)</h1>', h, re.S)
        if m:
            title = _txt(m.group(1))
        if not title:
            m = re.search(r'<meta[^>]+property="og:title"[^>]+content="([^"]+)"', h, re.I)
            title = m.group(1) if m else "作品 %s" % sid
        pic = ""
        m = re.search(r'<meta[^>]+property="og:image"[^>]+content="([^"]+)"', h, re.I)
        if m:
            pic = _abs(base, m.group(1))
        m3u8 = _m3u8_in(h, base)
        vip = bool(re.search(r"(试看已达上限|登入会员|login-required|VIP)", h))
        if m3u8:
            content = "91热爆 · 拿到直链"
            chs = [("main", "播放")]
        elif vip:
            content = ("91热爆 · 这站正片要登录会员才给流，"
                       "免费线路只放试看。列表/分类/搜索/详情都能用，"
                       "播放这条本站在源里拿不到。")
            chs = [("sniff", "嗅探试看")]
        else:
            content = "91热爆 · 这页没给直链，走嗅探"
            chs = [("sniff", "嗅探播放")]
        vod = _vod_detail(self, sid, title, pic, "", content, chapters=chs)
        vod["_m3u8"] = m3u8
        return vod

    def play(self, sid, ep):
        d = self.detail(sid)
        u = _s(d.get("_m3u8"))
        if not u:
            raise _SrcErr("91热爆正片要会员才给流，源里取不到")
        return u, {"User-Agent": self.ua, "Referer": self.base + "/"}


# ============================================================
# 8. 2AV  2av.com
# ============================================================

@_register
class _Av2(_Src):
    id = "av2"
    name = "2AV"
    base = "https://www.2av.com"
    cats = [("", "热门"), ("/pic/5.html", "分类5"), ("/pic/6.html", "分类6"),
            ("/pic/7.html", "分类7"), ("/pic/8.html", "分类8")]

    def categories(self):
        try:
            h = self.sess.text(self.base + "/")
            out = []
            for m in re.finditer(r'href="(/pic/(\d+)\.html)"[^>]*>\s*'
                                 r'(?:<[^>]+>\s*)*([^<]{1,14})\s*<', h):
                nm = _txt(m.group(3))
                if nm:
                    out.append((m.group(1), nm))
            for m in re.finditer(r'<a[^>]+href="(/tag/\d+\.html)"[^>]*>\s*([^<]{1,14})\s*<', h):
                nm = _txt(m.group(2))
                if nm and len(out) < 24:
                    out.append((m.group(1), nm))
            return out[:24]
        except Exception:
            return []

    def _parse(self, h, base):
        items = []
        for m in re.finditer(r'(?s)<a href="(/video/(\d+)\.html)"[^>]*>(.*?)</a>', h):
            sid, block = m.group(2), m.group(3)
            pic = _imgs(block, base)
            t = ""
            tm = re.search(r'title="([^"]{2,140})"', m.group(0))
            if tm:
                t = tm.group(1)
            if not t:
                tm = re.search(r'alt="([^"]{2,140})"', block)
                t = tm.group(1) if tm else ""
            if not t:
                tm = re.search(r'<h\d[^>]*>([^<]{2,140})</h\d>', block)
                t = tm.group(1) if tm else ""
            if not t:
                t = "作品 %s" % sid
            items.append(_vod_item(self.id, sid, t, pic))
        seen, out = set(), []
        for it in items:
            if it["vod_id"] in seen:
                continue
            seen.add(it["vod_id"])
            out.append(it)
        return out

    def cat(self, catid, pg=1):
        path = _s(catid)
        pg = max(1, int(pg or 1))
        if path and pg > 1:
            path = re.sub(r"\.html$", "_%d.html" % pg, path)
        url = self.base + path if not path.startswith("http") else path
        base, h = self.sess.page(url)
        items = self._parse(h, base)
        if not items and pg == 1:
            b2, h2 = self.sess.page(self.base + "/")
            items = self._parse(h2, b2)
        return items, bool(items) and pg < 300

    def search(self, key, pg=1):
        key = _s(key)
        if not key:
            return [], False
        cands = ["%s/search.html?wd=%s" % (self.base, _q(key)),
                 "%s/search?keyword=%s" % (self.base, _q(key)),
                 "%s/video/search.html?wd=%s" % (self.base, _q(key))]
        for url in cands:
            try:
                base, h = self.sess.page(url)
            except Exception:
                continue
            items = self._parse(h, base)
            if items:
                return items, False
        return [], False

    def detail(self, sid):
        sid = _s(sid)
        url = "%s/video/%s.html" % (self.base, sid)
        base, h = self.sess.page(url)
        title = ""
        m = re.search(r'<h2[^>]*class="videoName[^"]*"[^>]*>(.*?)</h2>', h, re.S)
        if m:
            title = _txt(m.group(1))
        if not title:
            m = re.search(r'<title>(.*?)</title>', h, re.S)
            title = _clean_title(m.group(1)) if m else "作品 %s" % sid
        pic = ""
        m = re.search(r'poster="([^"]+)"', h, re.I)
        if m:
            pic = _abs(base, m.group(1))
        if not pic:
            m = re.search(r'<meta[^>]+property="og:image"[^>]+content="([^"]+)"', h, re.I)
            if m:
                pic = _abs(base, m.group(1))
        m3u8 = ""
        m = re.search(r'<source[^>]+src="([^"]+\.m3u8[^"]*)"', h, re.I)
        if m:
            m3u8 = _abs(base, m.group(1))
        if not m3u8:
            m3u8 = _m3u8_in(h, base)
        vip = bool(re.search(r"(未登录只能试看|signIn\.html)", h))
        note = "2AV · m3u8 直链"
        if vip:
            note += "（这站没登录只放试看片段，源里拿的就是公开的那条）"
        vod = _vod_detail(self, sid, title, pic, "直链" if m3u8 else "嗅探",
                          note, chapters=[("main", "播放")])
        vod["_m3u8"] = m3u8
        return vod

    def play(self, sid, ep):
        d = self.detail(sid)
        u = _s(d.get("_m3u8"))
        if not u:
            raise _SrcErr("没解析出直链")
        if u.startswith("/"):
            u = self.base + u
        return u, {"User-Agent": self.ua, "Referer": self.base + "/"}


# ============================================================
# 9. PornHub
# ============================================================

@_register
class _Pornhub(_Src):
    id = "pornhub"
    name = "PornHub"
    base = "https://www.pornhub.com"
    cats = [("/video?o=mv", "最热"), ("/video?o=cm", "最新"),
            ("/video?o=tr", "好评"), ("/categories/japanese", "日本"),
            ("/categories/chinese", "国产"), ("/categories/asian", "亚洲"),
            ("/categories/hentai", "动漫"), ("/categories/lesbian", "女同"),
            ("/categories/uncensored", "无码")]

    def _parse(self, h, base):
        items = []
        for m in re.finditer(r'(?s)<li class="[^"]*pcVideoListItem[^"]*"(.*?)</li>', h):
            b = m.group(1)
            mm = re.search(r'href="(/view_video\.php\?viewkey=[^"]+)"', b)
            if not mm:
                continue
            sid = mm.group(1)
            pic = ""
            im = re.search(r'data-image="([^"]+)"', b) or \
                re.search(r'data-mediumthumb="([^"]+)"', b) or \
                re.search(r'src="(https?://[^"]+\.jpe?g[^"]*)"', b)
            if im:
                pic = _abs(base, im.group(1))
            t = ""
            tm = re.search(r'title="([^"]{3,160})"', b)
            if tm:
                t = tm.group(1)
            if not t:
                tm = re.search(r'<span class="title">\s*<a[^>]*>([^<]{3,160})</a>', b)
                t = tm.group(1) if tm else ""
            if not t:
                t = "video %s" % sid[-8:]
            dur = ""
            dm = re.search(r'class="duration">([^<]+)<', b)
            if dm:
                dur = _txt(dm.group(1))
            items.append(_vod_item(self.id, sid, t, pic, dur))
        return items

    def cat(self, catid, pg=1):
        path = _s(catid) or "/video?o=mv"
        pg = max(1, int(pg or 1))
        url = self.base + path
        if pg > 1:
            url += ("&page=%d" % pg if "?" in path else "?page=%d" % pg)
        base, h = self.sess.page(url)
        items = self._parse(h, base)
        return items, bool(items) and pg < 200

    def search(self, key, pg=1):
        key = _s(key)
        if not key:
            return [], False
        url = "%s/video/search?search=%s" % (self.base, _q(key))
        if int(pg or 1) > 1:
            url += "&page=%s" % int(pg)
        base, h = self.sess.page(url)
        items = self._parse(h, base)
        return items, False

    def detail(self, sid):
        sid = _s(sid)
        if sid.startswith("http"):
            url = sid
        else:
            url = "%s%s" % (self.base, sid if sid.startswith("/")
                            else "/view_video.php?viewkey=%s" % sid)
        base, h = self.sess.page(url)
        title = ""
        m = re.search(r'<h1[^>]*class="title"[^>]*>\s*<span[^>]*>(.*?)</span>', h, re.S)
        if m:
            title = _txt(m.group(1))
        if not title:
            m = re.search(r'<meta[^>]+property="og:title"[^>]+content="([^"]+)"', h, re.I)
            title = m.group(1) if m else "video"
        pic = ""
        m = re.search(r'<meta[^>]+property="og:image"[^>]+content="([^"]+)"', h, re.I)
        if m:
            pic = _abs(base, m.group(1))
        # mediaDefinitions 里的 master.m3u8
        m3u8 = ""
        for pat in (r'"hls"\s*:\s*"([^"]+master\.m3u8[^"]*)"',
                    r'"videoUrl"\s*:\s*"([^"]+\.m3u8[^"]*)"',
                    r'\\"([^"\\]*master\.m3u8[^"\\]*)\\"'):
            mm = re.search(pat, h)
            if mm:
                m3u8 = mm.group(1).replace("\\/", "/")
                break
        if m3u8 and not m3u8.startswith("http"):
            m3u8 = _fix_url(m3u8)
        if not m3u8:
            m3u8 = _m3u8_in(h, base)
        vod = _vod_detail(self, sid, title, pic, "直链" if m3u8 else "嗅探",
                          "PornHub · m3u8 直链" if m3u8 else "PornHub · 走嗅探",
                          chapters=[("main", "播放")])
        vod["_m3u8"] = m3u8
        return vod

    def play(self, sid, ep):
        d = self.detail(sid)
        u = _s(d.get("_m3u8"))
        if not u:
            raise _SrcErr("没解析出直链")
        return u, {"User-Agent": self.ua, "Referer": self.base + "/"}


# ============================================================
# 10. JavBobo  javbobo.com
#     列表/详情齐；播放地址是页面脚本现拼的 → 交给壳嗅探
# ============================================================

@_register
class _Javbobo(_Src):
    id = "javbobo"
    name = "JavBobo"
    base = "https://javbobo.com"
    cats = [("47", "日本有码"), ("48", "日本无码"), ("49", "国产AV"),
            ("50", "网红主播")]

    def _parse(self, h, base):
        items = []
        for m in re.finditer(
                r'<a href="(/vod/player\.html\?cate_id=(\d+)&(?:amp;)?id=(\d+)'
                r'&(?:amp;)?type_id=(\d+))"[^>]*title="([^"]*)"(.*?)</a>', h, re.S):
            cate, vid, tid = m.group(2), m.group(3), m.group(4)
            pic = _imgs(m.group(6), base)
            t = _txt(m.group(5))
            if not t:
                t = "作品 %s" % vid
            items.append(_vod_item(self.id, "%s|%s|%s" % (cate, vid, tid), t, pic))
        seen, out = set(), []
        for it in items:
            if it["vod_id"] in seen:
                continue
            seen.add(it["vod_id"])
            out.append(it)
        return out

    def cat(self, catid, pg=1):
        tid = _s(catid) or "47"
        url = "%s/vod/index.html?type_id=%s" % (self.base, _q(tid))
        base, h = self.sess.page(url)
        items = self._parse(h, base)
        if not items and pg == 1:
            b2, h2 = self.sess.page(self.base + "/")
            items = self._parse(h2, b2)
        return items, bool(items) and pg < 100

    def search(self, key, pg=1):
        key = _s(key)
        if not key:
            return [], False
        # 这站的搜索结果是 AJAX 出来的
        url = "%s/public/auto/search1.html?keyword=%s" % (self.base, _q(key))
        try:
            h = self.sess.text(
                url,
                referer="%s/search/index.html?keyword=%s" % (self.base, _q(key)),
                headers={"X-Requested-With": "XMLHttpRequest"})
        except Exception:
            return [], False
        return self._parse(h, self.base), False

    def detail(self, sid):
        parts = _s(sid).split("|")
        if len(parts) == 3:
            cate, vid, tid = parts
        else:
            cate, vid, tid = "0", _s(sid), "49"
        url = "%s/vod/player.html?cate_id=%s&id=%s&type_id=%s" % (
            self.base, cate, vid, tid)
        base, h = self.sess.page(url)
        title = ""
        m = re.search(r'<title>(.*?)</title>', h, re.S)
        if m:
            title = _clean_title(m.group(1))
        pic = ""
        m = re.search(r'<meta[^>]+property="og:image"[^>]+content="([^"]+)"', h, re.I)
        if m:
            pic = _abs(base, m.group(1))
        m3u8 = _m3u8_in(h, base)
        note = ("JavBobo · 播放地址是页面脚本现拼的，"
                "交给壳嗅探；能嗅探的壳直接出流。")
        if m3u8:
            note = "JavBobo · m3u8 直链"
        vod = _vod_detail(self, sid, title, pic, "直链" if m3u8 else "嗅探",
                          note, chapters=[("main", "播放")])
        vod["_m3u8"] = m3u8
        vod["_page"] = base
        return vod

    def play(self, sid, ep):
        d = self.detail(sid)
        u = _s(d.get("_m3u8"))
        if u:
            return u, {"User-Agent": self.ua, "Referer": self.base + "/"}
        # 没直链就交给壳去嗅探页面
        raise _SrcErr("这站要壳嗅探")


# ============================================================
# Spider 入口（跟 真果鉴.py 同款约定，两个源可以一起挂）
# ============================================================

class Spider(BaseSpider):

    def getName(self):
        return "绿果鉴·瑟瑟线"

    def init(self, extend=""):
        self.extend = _s(extend)
        return {}

    def destroy(self):
        return {}

    def isVideoFormat(self, url):
        u = _s(url).lower()
        return (u.startswith("magnet:") or ".m3u8" in u or ".mp4" in u
                or ".flv" in u or ".ts" in u or ".mkv" in u)

    def manualVideoCheck(self):
        return False

    def localProxy(self, param):
        return [404, "text/plain; charset=utf-8", b"no proxy"]

    proxy = localProxy

    # ---------- 首页 ----------
    def _cats_of(self, src):
        try:
            c = src.categories()
            if c:
                return c[:30]
        except Exception:
            pass
        return src.cats or [("", "推荐")]

    def homeContent(self, filter=False):
        cats_map = {}
        srcs = list(_SOURCES)
        if ThreadPoolExecutor:
            try:
                with ThreadPoolExecutor(max_workers=6) as ex:
                    futs = {ex.submit(self._cats_of, s): s for s in srcs}
                    for f in futs:
                        try:
                            cats_map[futs[f].id] = f.result(timeout=25)
                        except Exception:
                            cats_map[futs[f].id] = futs[f].cats
            except Exception:
                cats_map = {}
        classes, filters = [], {}
        for src in srcs:
            if src.id not in cats_map:
                cats_map[src.id] = self._cats_of(src)
            tid = _tid(src.id, "")
            classes.append({"type_id": tid, "type_name": src.name})
            opts, seen = [], set()
            for cid, cname in (cats_map.get(src.id) or [])[:30]:
                if not cname or cname in seen:
                    continue
                seen.add(cname)
                opts.append({"n": cname, "v": cid})
            if opts:
                filters[tid] = [{"key": "cat", "name": "分类", "value": opts}]
        vods = []
        try:
            items, _ = srcs[0].home(1)
            vods = items[:24]
        except Exception:
            pass
        return {"class": classes, "list": vods, "filters": filters}

    def homeVideoContent(self):
        try:
            items, _ = _SOURCES[0].home(1)
            return {"list": items[:24]}
        except Exception:
            return {"list": []}

    # ---------- 分类 ----------
    def categoryContent(self, tid, pg="1", filter=False, extend=None):
        src_id, _ = _split_tid(tid)
        src = _find_src(src_id)
        if not src:
            return {"list": [], "page": 1, "pagecount": 1, "limit": 20, "total": 0}
        try:
            pg = max(1, int(pg))
        except Exception:
            pg = 1
        cat = ""
        ext = extend
        if isinstance(ext, str) and ext:
            try:
                ext = json.loads(ext)
            except Exception:
                ext = {}
        if isinstance(ext, dict):
            cat = _s(ext.get("cat", ""))
        if cat:
            try:
                cats = self._cats_of(src)
                ids = set(c[0] for c in cats)
                if cat not in ids:
                    by_name = dict((c[1], c[0]) for c in cats)
                    cat = by_name.get(cat, "")
            except Exception:
                pass
        if not cat:
            try:
                cats = self._cats_of(src)
                cat = cats[0][0] if cats else ""
            except Exception:
                cat = ""
        try:
            items, more = src.cat(cat, pg)
            if items:
                src.last_err = ""
        except Exception as e:
            src.last_err = _err_text(e)
            items, more = [], False
        if not items and not more:
            note = src.last_err or "该分类当前没数据"
            items = [_vod_item(src.id, "@@err@@",
                               "%s：%s" % (src.name, note))]
        return {"list": items or [], "page": pg,
                "pagecount": MAX_PAGE if more else pg,
                "limit": 20, "total": 999999 if more else len(items or [])}

    # ---------- 详情 ----------
    def detailContent(self, ids):
        did = ids[0] if ids else ""
        src_id, sid = _split_did(did)
        src = _find_src(src_id)
        if not src:
            return {"list": []}
        if sid == "@@err@@":
            vod = _vod_detail(src, sid, "%s 这会儿用不了" % src.name, "", "",
                              src.last_err or "这站当前没返回数据")
            vod["vod_id"] = did
            return {"list": [vod]}
        try:
            vod = src.detail(sid)
        except Exception as e:
            vod = _vod_detail(src, sid, "加载失败", "", "",
                              "原因：%s" % _err_text(e))
            vod["vod_id"] = did
        vod["vod_id"] = did
        vod.pop("_m3u8", None)
        vod.pop("_magnets", None)
        vod.pop("_page", None)
        return {"list": [vod]}

    # ---------- 搜索 ----------
    def _search_one(self, src, key):
        try:
            items, _ = src.search(key, 1)
            return items or []
        except Exception:
            return []

    def searchContent(self, key, quick=False, pg="1"):
        key = _s(key).strip()
        if not key:
            return {"list": []}
        try:
            pg = int(pg)
        except Exception:
            pg = 1
        if pg > 1:
            return {"list": []}
        out = []
        _seen_ids = set()
        srcs = list(_SOURCES)
        if ThreadPoolExecutor:
            with ThreadPoolExecutor(max_workers=8) as ex:
                futs = {ex.submit(self._search_one, s, key): s for s in srcs}
                for f in futs:
                    try:
                        items = f.result(timeout=35)
                    except Exception:
                        continue
                    src = futs[f]
                    for v in (items or [])[:10]:
                        if v["vod_id"] in _seen_ids:
                            continue
                        _seen_ids.add(v["vod_id"])
                        if not v["vod_name"].startswith(src.name):
                            v["vod_name"] = "[%s] %s" % (src.name, v["vod_name"])
                        out.append(v)
                    if len(out) >= 200:
                        break
        else:
            for s in srcs:
                for v in (self._search_one(s, key) or [])[:10]:
                    if v["vod_id"] in _seen_ids:
                        continue
                    _seen_ids.add(v["vod_id"])
                    if not v["vod_name"].startswith(s.name):
                        v["vod_name"] = "[%s] %s" % (s.name, v["vod_name"])
                    out.append(v)
        return {"list": out}

    # ---------- 播放 ----------
    def playerContent(self, flag, id, vipFlags=None):
        src_id, sid, ep = _split_pid(id)
        src = _find_src(src_id)
        if not src:
            return {"parse": 0, "playUrl": "", "url": "", "header": {}}
        try:
            url, header = src.play(sid, ep)
        except Exception:
            return {"parse": 0, "playUrl": "", "url": "", "header": {}}
        url = _s(url)
        if url.startswith("magnet:"):
            return {"parse": 0, "playUrl": "", "url": url, "header": {}}
        return {"parse": 0, "playUrl": "", "url": url,
                "header": dict(header or {})}
