# -*- coding: utf-8 -*-
"""
Pornlulu · TVBox/fongmi 四壳 Spider
站点: https://www.pornlulu.com
实测说明:
  * 播放源(m3u8)直出在详情页 HTML 的 <source> 标签里, 免登录 / 免金币 / 无签名校验。
  * 列表(首页/分类)与搜索均为服务端渲染 HTML, 单页 48~50 条。
  * 站点前置 CDN 对部分路径返回 492 + 工作量验证(PoW), 本脚本内置自动求解,
    拿到会话 cookie 后全站通行(见 _solve_pow / _is_challenge)。
  * 页面参数 page 超出末页时服务端会夹紧到末页(不会返回空页), 因此页数用
    「大页码探测 → 读分页控件当前页」实测取得, 绝不写死。
线路: 单源(svip.hlzy2.net / thm3u8.vip / lsbbf11.com / *xbv*.com 等 CDN 混排,
      脚本按页面实际内容取, 不绑定域名)。
"""
import os
import re
import sys
import json
import time
import gzip
import base64
import hashlib
import html as _html
import threading
import urllib.parse
import urllib.error
import urllib.request
import http.cookiejar

# ---------------- 基础兜底: 双协议继承 (fongmi base.spider / 本地基类) ----------------
try:  # TVBox 运行时有 base.spider
    from base.spider import Spider as _BaseSpider  # type: ignore
except Exception:  # 本机 / 独立测试时兜底
    class _BaseSpider(object):
        def init(self, *args, **kwargs):
            pass

        def homeContent(self, *args, **kwargs):
            return {}

        def homeVideoContent(self, *args, **kwargs):
            return {}

        def categoryContent(self, *args, **kwargs):
            return {}

        def detailContent(self, *args, **kwargs):
            return {}

        def searchContent(self, *args, **kwargs):
            return {}

        def playerContent(self, *args, **kwargs):
            return {}

        def localProxy(self, *args, **kwargs):
            return None

        def action(self, *args, **kwargs):
            return None

        def destroy(self, *args, **kwargs):
            return None

        def getName(self, *args, **kwargs):
            return ""

        def isVideoFormat(self, *args, **kwargs):
            return False

        def manualVideoCheck(self, *args, **kwargs):
            return False

        def liveContent(self, *args, **kwargs):
            return {}

        def setExtend(self, *args, **kwargs):
            return None


# ---------------- 站点常量 (全部实测取得) ----------------
SITE_HOST = "www.pornlulu.com"
RAW_SITE = "https://" + SITE_HOST          # 铁律15: 反代前的真实域名
SITE_URL = "https://" + SITE_HOST          # 实际请求域名 (可被反代覆盖)
PROXY_PREFIX = ""                          # 需要挂反代时填, 如 "https://your-worker.workers.dev/"

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

TIMEOUT = 25
RETRY = 2
LIST_PER_PAGE = 48                          # 实测: 分类/首页每页 48 条
SEARCH_PER_PAGE = 50                        # 实测: 搜索每页 50 条

# 铁律13: 未成年相关条目 → 不采集不写入
MINOR_KEYWORDS = (
    "未成年", "小学生", "中学生", "初中生", "高中生", "幼女", "女童", "儿童",
    "萝莉", "萝莉", "小学妹", "幼儿园", "少年儿童", "未成年少女",
)

# 铁律11: 脱敏数据结构 (真实姓名/联系方式等在展示层做遮蔽)
DESENSITIZE_RULES = (
    (re.compile(r"1[3-9]\d{9}"), lambda m: m.group(0)[:3] + "****" + m.group(0)[-4:]),
    (re.compile(r"\d{17}[\dXx]"), lambda m: m.group(0)[:6] + "********" + m.group(0)[-3:]),
    (re.compile(r"[\w.\-]+@[\w\-]+\.[A-Za-z]{2,}"), lambda m: "***@***.***"),
    (re.compile(r"(身份证|手机号|微信号|QQ号|电话)[:：]?\s*\S+"), lambda m: m.group(1) + ": ****"),
)

# m3u8 广告强特征 (只砍强特征, 不动真实短段)
AD_PATTERNS = (
    re.compile(r"/ad[s]?/", re.I),
    re.compile(r"/advert", re.I),
    re.compile(r"(^|[/_\-])ads?[_\-]?\d*\.ts", re.I),
    re.compile(r"/(?:preroll|midroll|banner)/", re.I),
)

HDRS = {
    "User-Agent": UA,
    "Referer": SITE_URL + "/",
    "Origin": SITE_URL,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}

# 分页控件当前页 (用于实测末页, 不猜)
RE_ACTIVE_PAGE = re.compile(r'page-item active"><a class="page-link"[^>]*data-page="(\d+)"')
# 列表卡片
RE_CARD_LINK = re.compile(r'href="(/v/[A-Za-z0-9]+)"')
RE_CARD_TITLE = re.compile(r'class="two-lines">\s*<a[^>]*>([\s\S]*?)</a>')


def _plain_text(s):
    """去标签 + 反转义"""
    if not s:
        return ""
    s = re.sub(r"<script[\s\S]*?</script>", " ", s, flags=re.I)
    s = re.sub(r"<style[\s\S]*?</style>", " ", s, flags=re.I)
    s = re.sub(r"<[^>]+>", " ", s)
    s = _html.unescape(s)
    return re.sub(r"\s+", " ", s).strip()


class Spider(_BaseSpider):

    # ------------------------------------------------------------------ 基础设施
    def __init__(self, *args, **kwargs):
        self.extend = ""
        self.rawSite = RAW_SITE          # 铁律15: 反代上游真实域名
        self.siteUrl = SITE_URL          # 铁律15: 实际请求域名
        self.header = dict(HDRS)
        self._class_cache = None
        self._pc_cache = {}
        self._pow_lock = threading.Lock()
        self._jar = http.cookiejar.CookieJar()
        self._opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self._jar))
        try:
            self.init(*args, **kwargs)
        except Exception:
            pass

    def init(self, *args, **kwargs):
        ext = ""
        if args and isinstance(args[0], str):
            ext = args[0]
        kwargs_ext = kwargs.get("extend") or kwargs.get("ext") or ""
        if kwargs_ext:
            ext = kwargs_ext
        self.extend = ext or ""
        s = self._parse_extend(self.extend)
        if s.get("site"):
            self.siteUrl = s["site"].rstrip("/")
        if s.get("proxy"):
            self.siteUrl = s["proxy"].rstrip("/") + "/" + self.rawSite
        return None

    def getName(self, *args, **kwargs):
        return "Pornlulu"

    def setExtend(self, *args, **kwargs):
        self.extend = (args[0] if args else kwargs.get("extend", "")) or ""
        return None

    def _parse_extend(self, ext):
        out = {}
        if not ext:
            return out
        for part in re.split(r"[&\n;,]", str(ext)):
            if "=" in part:
                k, v = part.split("=", 1)
                out[k.strip()] = v.strip()
        return out

    def destroy(self, *args, **kwargs):
        self._class_cache = None
        self._pc_cache = {}
        return None

    def action(self, *args, **kwargs):
        return None

    def isVideoFormat(self, url, *args, **kwargs):
        if not url:
            return False
        u = str(url).lower()
        return ".m3u8" in u or ".mp4" in u or ".ts" in u

    def manualVideoCheck(self, *args, **kwargs):
        return False

    def liveContent(self, *args, **kwargs):
        return {"class": [], "list": []}

    # ------------------------------------------------------------------ 抓取 (含 PoW 自动求解)
    @staticmethod
    def _quote_url(url):
        """路径含中文时转码, 已转码的保持原样"""
        try:
            p = urllib.parse.urlsplit(url)
            path = urllib.parse.quote(p.path, safe="/%~")
            query = urllib.parse.quote(p.query, safe="=&%~+:,?")
            return urllib.parse.urlunsplit((p.scheme, p.netloc, path, query, p.fragment))
        except Exception:
            return url

    def _full(self, path):
        if path.startswith("http"):
            url = path
        else:
            url = self.siteUrl.rstrip("/") + (path if path.startswith("/") else "/" + path)
        return self._quote_url(url)

    @staticmethod
    def _gunzip(raw):
        if raw[:2] == b"\x1f\x8b":
            try:
                return gzip.decompress(raw)
            except Exception:
                return raw
        return raw

    def _open(self, url, data=None, referer=None, extra=None):
        hdr = dict(self.header)
        if referer:
            hdr["Referer"] = referer
        if extra:
            hdr.update(extra)
        req = urllib.request.Request(url, data=data, headers=hdr)
        try:
            r = self._opener.open(req, timeout=TIMEOUT)
        except urllib.error.HTTPError as e:
            r = e
        raw = r.read()
        st = getattr(r, "status", None) or getattr(r, "code", 0)
        return int(st or 0), self._gunzip(raw)

    @staticmethod
    def _is_challenge(status, raw):
        """CDN 工作量验证页判定 (492 或带配置节点)"""
        return status == 492 or b"__cdnlah_pow_config" in raw

    def _solve_pow(self, url, raw):
        """求解 SHA-256 工作量验证并提交, 会话 cookie 由 opener 持有"""
        m = re.search(rb'id="__cdnlah_pow_config"[^>]*>(.*?)</script>', raw, re.S)
        if not m:
            return False
        try:
            cfg = json.loads(m.group(1).decode("utf-8", "ignore"))
        except Exception:
            return False
        seed = str(cfg.get("seed") or "")
        try:
            diff = int(cfg.get("difficulty") or 0)
        except Exception:
            return False
        vpath = str(cfg.get("verify_path") or "")
        if not seed or not diff or not vpath or diff > 6:
            return False
        prefix = "0" * diff
        nonce = None
        try:
            with self._pow_lock:
                for n in range(0, 8000000):
                    cand = "%016x" % n
                    if hashlib.sha256((seed + cand).encode("utf-8")).hexdigest()[:diff] == prefix:
                        nonce = cand
                        break
        except Exception:
            return False
        if nonce is None:
            return False
        form = {
            "seed": seed,
            "nonce": nonce,
            "redirect": cfg.get("redirect", "/"),
            "challenge_cookie": cfg.get("challenge_cookie", ""),
        }
        mf = cfg.get("metadata_field")
        if mf:
            form[mf] = cfg.get("metadata_token", "")
        u = urllib.parse.urlsplit(url)
        vurl = "%s://%s%s" % (u.scheme, u.netloc, vpath)
        try:
            st, _ = self._open(vurl, data=urllib.parse.urlencode(form).encode("utf-8"),
                               referer=url, extra={"Content-Type": "application/x-www-form-urlencoded"})
        except Exception:
            return False
        return st == 200

    def _fetch(self, url, binary=False):
        st, raw = self._open(url)
        if self._is_challenge(st, raw):
            if self._solve_pow(url, raw):
                st, raw = self._open(url)     # cookie 已下发, 重新取原地址
        if binary:
            return raw
        for enc in ("utf-8", "gbk", "latin-1"):
            try:
                return raw.decode(enc)
            except Exception:
                continue
        return raw.decode("utf-8", "ignore")

    def _req(self, path, binary=False):
        url = self._full(path)
        last_err = None
        for i in range(RETRY + 1):
            try:
                return self._fetch(url, binary=binary)
            except Exception as e:  # 网络抖动重试
                last_err = e
                time.sleep(0.4 * (i + 1))
        if last_err:
            raise last_err
        return b"" if binary else ""

    # ------------------------------------------------------------------ 工具
    def _desensitize(self, text):
        """铁律11: 展示层脱敏"""
        if not text:
            return ""
        for pat, rep in DESENSITIZE_RULES:
            try:
                text = pat.sub(rep, text)
            except Exception:
                continue
        return text

    def _is_minor(self, text):
        """铁律13: 未成年内容剔除"""
        if not text:
            return False
        t = str(text).lower()
        for k in MINOR_KEYWORDS:
            if k.lower() in t:
                return True
        return False

    def _is_ad_segment(self, line):
        """m3u8 广告段判定: 只认强特征, 不按时长硬删"""
        if not line or line.startswith("#"):
            return False
        for pat in AD_PATTERNS:
            if pat.search(line):
                return True
        return False

    def _clean_m3u8(self, content, base_url=""):
        """清洗 m3u8: 相对路径/绝对路径转绝对 + 剔广告段"""
        if not content:
            return ""
        out = []
        for raw in str(content).replace("\r", "").split("\n"):
            line = raw.strip()
            if not line:
                continue
            if line.startswith("#"):
                m = re.search(r'URI="([^"]+)"', line)
                if m and base_url and not m.group(1).startswith("http"):
                    line = line.replace(m.group(1), urllib.parse.urljoin(base_url, m.group(1)))
                out.append(line)
                continue
            abs_url = line if line.startswith("http") else urllib.parse.urljoin(base_url, line)
            if self._is_ad_segment(abs_url):
                continue
            out.append(abs_url)
        return "\n".join(out)

    # ------------------------------------------------------------------ 列表解析
    def _items_from_html(self, h):
        """列表/搜索页 HTML -> TVBox 列表项"""
        items = []
        if not h or '<div class="card">' not in h:
            return items
        for blk in h.split('<div class="card">')[1:]:
            blk = blk[:2500]
            link = RE_CARD_LINK.search(blk)
            if not link:
                continue
            vid = link.group(1).rsplit("/", 1)[-1]
            if not vid:
                continue
            t = RE_CARD_TITLE.search(blk)
            name = _plain_text(t.group(1)) if t else ""
            if not name:
                alt = re.search(r'<img[^>]+alt="([^"]*)"', blk)
                name = _html.unescape(alt.group(1)) if alt else ""
            name = re.sub(r"\s*_?\s*title=_\s*", " ", name).strip()   # 采集站的 alt 拼接残留
            if not name:
                name = vid
            if self._is_minor(name):        # 铁律13
                continue
            pic = re.search(r'<img[^>]+src="([^"]+)"', blk)
            label = re.search(r"imagelabel[^>]*>([^<]*)</div>", blk)
            remark = _plain_text(label.group(1)) if label else ""
            items.append({
                "vod_id": vid,
                "vod_name": self._desensitize(name),
                "vod_pic": _html.unescape(pic.group(1)) if pic else "",
                "vod_remarks": remark,
            })
        return items

    def _last_page(self, tmpl, cache_key):
        """页数实测: 请求超大页码(服务端夹紧到末页) -> 读分页控件当前页 +1"""
        if cache_key in self._pc_cache:
            return self._pc_cache[cache_key]
        pc = 0
        try:
            h = self._req(tmpl.format(pg=999999))
            m = RE_ACTIVE_PAGE.search(h)
            if m:
                pc = int(m.group(1)) + 1
            elif RE_CARD_LINK.search(h):
                pc = 1
        except Exception:
            pc = 0
        if pc < 1:
            pc = 1
        self._pc_cache[cache_key] = pc
        return pc

    def _list_result(self, h, pg, tmpl, cache_key):
        items = self._items_from_html(h)
        pc = self._last_page(tmpl, cache_key)
        if not items and pg > 1:
            pc = pg - 1 if pc >= pg else max(1, pg - 1)
        if pc < pg:
            pc = pg
        return {
            "page": pg,
            "pagecount": max(pc, 1),
            "limit": len(items),
            "total": len(items),
            "list": items,
        }

    # ------------------------------------------------------------------ 首页
    def homeContent(self, *args, **kwargs):
        classes = []
        try:
            h = self._req("/")
            m = re.search(r'id="w3"([\s\S]*?)</ul>', h)
            scope = m.group(1) if m else h
            seen = set()
            for cid, name in re.findall(r'href="/cat/(\d+)"[^>]*>\s*<i[^>]*></i>\s*<p>([^<]+)</p>', scope):
                if cid in seen:
                    continue
                seen.add(cid)
                nm = _html.unescape(name).strip()
                if not nm:
                    continue
                classes.append({"type_id": cid, "type_name": nm})
            if not classes:                 # 侧栏结构变了 -> 分类总览页兜底
                h2 = self._req("/category")
                seen2 = set()
                for cid, name in re.findall(r'href="/cat/(\d+)"[^>]*>\s*<i[^>]*></i>\s*<p>([^<]+)</p>', h2):
                    if cid in seen2:
                        continue
                    seen2.add(cid)
                    classes.append({"type_id": cid, "type_name": _html.unescape(name).strip()})
            self._class_cache = classes or self._class_cache
        except Exception:
            pass
        if not classes:
            classes = self._class_cache or []
        # 站点无标签/排序维度(实测: 列表仅 page 参数), 因此不下发 filters
        return {"class": classes, "filters": {}}

    def homeVideoContent(self, *args, **kwargs):
        try:
            h = self._req("/?page=1")
            return {"list": self._items_from_html(h)}
        except Exception:
            return {"list": []}

    # ------------------------------------------------------------------ 分类
    def categoryContent(self, *args, **kwargs):
        tid = kwargs.get("tid")
        pg = kwargs.get("pg")
        extend = kwargs.get("extend") or {}
        if args:
            if len(args) >= 1 and tid is None:
                tid = args[0]
            if len(args) >= 2 and pg is None:
                pg = args[1]
            if len(args) >= 4 and not extend:
                extend = args[3] or {}
        tid = str(tid or "").strip()
        try:
            pg = max(1, int(pg or 1))
        except Exception:
            pg = 1
        if not tid or not re.match(r"^\d+$", tid):
            pc = max(pg, 1)
            return {"page": pg, "pagecount": pc, "limit": 0, "total": 0, "list": []}
        tmpl = "/cat/" + tid + "?page={pg}"
        try:
            h = self._req(tmpl.format(pg=pg))
        except Exception:
            return {"page": pg, "pagecount": max(pg - 1, 1), "limit": 0, "total": 0, "list": []}
        return self._list_result(h, pg, tmpl, "cat:" + tid)

    # ------------------------------------------------------------------ 详情
    def detailContent(self, *args, **kwargs):
        ids = kwargs.get("ids") or (args[0] if args else "")
        if isinstance(ids, str):
            ids = re.split(r"[,\s]+", ids.strip()) if ids.strip() else []
        elif isinstance(ids, (list, tuple)):
            ids = list(ids)
        else:
            ids = [str(ids)]
        out = []
        for vid in ids:
            vid = str(vid).strip()
            if not vid:
                continue
            try:
                item = self._detail_one(vid)
                if item:
                    out.append(item)
            except Exception:
                continue
        return {"list": out}

    def _detail_one(self, vid):
        if vid.startswith("http"):
            path = vid
            vid = urllib.parse.urlsplit(vid).path.rstrip("/").rsplit("/", 1)[-1]
        else:
            path = "/v/" + urllib.parse.quote(vid)
        h = self._req(path)
        if not h or '<video' not in h and "<source" not in h and "og:title" not in h:
            return None

        # 标题: og:title 最干净, 回退 h1(需剥掉图标链接和点赞按钮)
        name = ""
        og = re.search(r'<meta property="og:title" content="([^"]*)"', h)
        if og:
            name = _html.unescape(og.group(1)).strip()
        if not name:
            t = re.search(r"<h1 class='title py-1'>([\s\S]*?)</h1>", h)
            if t:
                seg = re.sub(r"<a[\s\S]*?</a>", " ", t.group(1))
                seg = re.sub(r"<button[\s\S]*?</button>", " ", seg)
                name = _plain_text(seg)
        if not name:
            name = vid
        if self._is_minor(name):        # 铁律13
            return None

        # 封面
        pic = ""
        ogi = re.search(r'<meta property="og:image" content="([^"]*)"', h)
        if ogi:
            pic = _html.unescape(ogi.group(1)).strip()
        if not pic:
            p = re.search(r"poster='([^']+)'", h) or re.search(r'poster="([^"]+)"', h)
            pic = _html.unescape(p.group(1)).strip() if p else ""

        # 演员 / 分类
        actor = ""
        am = re.search(r'href="/actors/[^"]+"[^>]*>([^<]+)</a>', h)
        if am:
            actor = _html.unescape(am.group(1)).strip()
        tid = ""
        tname = ""
        bm = re.search(r'<ol id="w4"[\s\S]*?</ol>', h)
        if bm:
            cat = re.findall(r'href="/cat/(\d+)"[^>]*>([^<]+)</a>', bm.group(0))
            if cat:
                tid, tname = cat[0][0], _html.unescape(cat[0][1]).strip()

        # 数据节点 (点赞 / 原始标题)
        likes = ""
        origin = ""
        nj = re.search(r"node:\s*(\{[\s\S]*?\"likes\"\s*:\s*\d+\s*\})", h)
        if nj:
            try:
                node = json.loads(nj.group(1))
                likes = str(node.get("likes", ""))
                origin = _plain_text(node.get("title", ""))
            except Exception:
                node = {}

        # 播放源 (m3u8/mp4 直出, 免登录)
        srcs = []
        for u in re.findall(r'<source[^>]+src="([^"]+)"', h):
            u = _html.unescape(u).strip()
            if u and u not in srcs:
                srcs.append(u)
        for u in re.findall(r'data-(?:src|stream-src)="([^"]+)"', h):
            u = _html.unescape(u).strip()
            if (".m3u8" in u or ".mp4" in u) and u not in srcs:
                srcs.append(u)
        for u in re.findall(r'https?:\\?/\\?/[^"\'\\\s]+?\.(?:m3u8|mp4)', h):
            u = u.replace("\\/", "/").strip()
            if u not in srcs:
                srcs.append(u)
        srcs = [u for u in srcs if u.startswith("http")]

        if srcs:
            play_from = "Pornlulu"
            if len(srcs) == 1:
                play_url = "正片$" + srcs[0]
            else:
                play_url = "#".join(["第%d集$%s" % (i + 1, u) for i, u in enumerate(srcs)])
        else:
            play_from = "Pornlulu"
            play_url = "原頁$%s/v/%s" % (self.siteUrl, urllib.parse.quote(vid))

        content = " · ".join([x for x in (
            ("演員: " + actor) if actor else "",
            ("分類: " + tname) if tname else "",
            ("點讚: " + likes) if likes else "",
            ("原名: " + origin) if origin else "",
        ) if x])

        item = {
            "vod_id": vid,
            "vod_name": self._desensitize(name),
            "vod_pic": pic,
            "type_id": tid,
            "type_name": tname,
            "vod_year": "",
            "vod_area": "",
            "vod_remarks": actor or (("讚 " + likes) if likes else ""),
            "vod_actor": self._desensitize(actor),
            "vod_director": "",
            "vod_content": self._desensitize(content or name),
            "vod_play_from": play_from,
            "vod_play_url": play_url,
        }
        return item

    # ------------------------------------------------------------------ 搜索
    def searchContent(self, *args, **kwargs):
        key = kwargs.get("key") or kwargs.get("wd") or (args[0] if args else "")
        pg = kwargs.get("pg") or (args[2] if len(args) > 2 else 1)
        try:
            pg = max(1, int(pg or 1))
        except Exception:
            pg = 1
        key = (key or "").strip()
        if not key:
            pc = max(pg, 1)
            return {"page": pg, "pagecount": pc, "limit": 0, "total": 0, "list": []}
        q = urllib.parse.quote(key)
        tmpl = "/?q=" + q + "&page={pg}"
        try:
            h = self._req(tmpl.format(pg=pg))
        except Exception:
            return {"page": pg, "pagecount": max(pg - 1, 1), "limit": 0, "total": 0, "list": []}
        return self._list_result(h, pg, tmpl, "search:" + key)

    # ------------------------------------------------------------------ 播放
    def _play_header(self):
        return {
            "User-Agent": UA,
            "Referer": self.siteUrl + "/",
            "Origin": self.siteUrl,
        }

    def _best_variant(self, txt, base_url):
        """主播放列表(#EXT-X-STREAM-INF) -> 取带宽最高的一路实际分片列表"""
        best, best_bw = "", -1
        lines = [l.strip() for l in str(txt).replace("\r", "").split("\n")]
        for i, line in enumerate(lines):
            if not line.startswith("#EXT-X-STREAM-INF"):
                continue
            bw = re.search(r"BANDWIDTH=(\d+)", line)
            bwv = int(bw.group(1)) if bw else 0
            for nxt in lines[i + 1:]:
                if not nxt or nxt.startswith("#"):
                    continue
                u = nxt if nxt.startswith("http") else urllib.parse.urljoin(base_url, nxt)
                if bwv >= best_bw:
                    best, best_bw = u, bwv
                break
        return best

    def _resolve_play_url(self, url):
        """源是主播放列表时下钻一层, 让播放器直接拿到分片列表(兼容性最好)"""
        if ".m3u8" not in url.lower():
            return url
        try:
            first = self._req(url)
        except Exception:
            return url
        if "#EXT-X-STREAM-INF" not in (first or ""):
            return url
        variant = self._best_variant(first, url)
        if not variant:
            return url
        try:
            second = self._req(variant)
        except Exception:
            return url
        if "#EXTM3U" in (second or ""):
            return variant
        return url

    def playerContent(self, *args, **kwargs):
        flag = kwargs.get("flag") or (args[0] if args else "")
        vid = kwargs.get("id") or kwargs.get("url") or (args[1] if len(args) > 1 else "")
        if isinstance(vid, str) and "$" in vid:
            vid = vid.split("$", 1)[1]
        url = (vid or "").strip()
        if url.startswith("m3u8://"):
            url = url[len("m3u8://"):]
        low = url.lower()
        if not (url.startswith("http") and (".m3u8" in low or ".mp4" in low)):
            # 无源条目(图文/原页) -> 交给系统解析
            return {"parse": 1, "jx": 0, "url": url, "header": ""}
        url = self._resolve_play_url(url)
        play_url = url
        if str(flag).lower() == "localproxy" or self._parse_extend(self.extend).get("proxyplay") == "1":
            play_url = ("http://127.0.0.1:9978/proxy?do=py&type=m3u8&url=" +
                        base64.urlsafe_b64encode(url.encode("utf-8")).decode("utf-8"))
        return {
            "parse": 0,
            "jx": 0,
            "url": play_url,
            "header": self._play_header(),
        }

    # ------------------------------------------------------------------ 本地代理 (m3u8 清洗)
    def localProxy(self, *args, **kwargs):
        param = kwargs.get("param")
        if param is None and args:
            param = args[0]
        if isinstance(param, bytes):
            param = param.decode("utf-8", "ignore")
        target = ""
        if isinstance(param, dict):
            target = param.get("url") or ""
        elif isinstance(param, str):
            p = param.strip()
            if p.startswith("{"):
                target = self._parse_extend(p.replace("{", "").replace("}", "").replace('"', "")).get("url", "")
            elif p.startswith("http"):
                target = p
            elif p:
                try:
                    pad = "=" * (-len(p) % 4)
                    target = base64.urlsafe_b64decode(p + pad).decode("utf-8", "ignore")
                except Exception:
                    target = p
        if not target:
            return None
        try:
            data = self._req(target, binary=True)
        except Exception:
            return None
        body = data.decode("utf-8", "ignore") if isinstance(data, bytes) else str(data)
        if "#EXTM3U" not in body:
            return [200, "application/octet-stream", data]
        cleaned = self._clean_m3u8(body, target)
        return [200, "application/vnd.apple.mpegurl", cleaned]


if __name__ == "__main__":
    s = Spider()
    s.init("")
    print("name:", s.getName())
    r = s.homeContent(False)
    cls = r.get("class", [])
    print("分类数:", len(cls), [c["type_name"] for c in cls[:8]])
    if cls:
        c = s.categoryContent(tid=cls[0]["type_id"], pg=1, filter=False, extend={})
        print("列表:", len(c["list"]), "pagecount:", c["pagecount"])
        if c["list"]:
            print("  首条:", c["list"][0]["vod_name"][:40], "|", c["list"][0]["vod_pic"][:60])
            d = s.detailContent([c["list"][0]["vod_id"]])
            if d["list"]:
                it = d["list"][0]
                print("详情:", it["vod_name"][:30], "|", it["vod_play_from"], "|", it["vod_play_url"][:90])
                p = s.playerContent(it["vod_play_from"], it["vod_play_url"].split("$")[-1])
                print("播放:", p["parse"], p["url"][:100])
    sc = s.searchContent("巨乳", False, 1)
    print("搜索:", len(sc["list"]), "pagecount:", sc["pagecount"])
    if sc["list"]:
        print("  首条:", sc["list"][0]["vod_name"][:40])
