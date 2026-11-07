# -*- coding: utf-8 -*-
"""
橙果短剧 (chengguodj.com) — CatVod / TVBox 爬虫

修复要点（分类/首页封面不显示）：
  站点的封面原图托管在 pic.wlwvch.cn，返回的是【加密字节流】
  （HTTP 200 但 Content-Type 为 binary/octet-stream，文件头 0a 45 74 8f 而非 FF D8 FF），
  播放器直接取原图解码必然失败 → 封面空白。
  站点自身渲染时用的是站内图片代理：
      <img src="/_img/<原图URL的base64(去padding)>">
  该接口会返回正常的 image/jpeg（FF D8 FF）。
  因此所有封面统一走 _proxy() 包装成 /_img/<b64> 再输出。
"""
import sys
import re
import json
import base64
import html as htmllib
import urllib.parse

sys.path.append('..')
try:
    from base.spider import Spider as _Base
except ImportError:
    class _Base:
        pass

try:
    import requests as rq
    rq.packages.urllib3.disable_warnings()
except Exception:
    rq = None

UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 16_6_1 like Mac OS X) AppleWebKit/605.1.15 "
      "(KHTML, like Gecko) Version/16.6.1 Mobile/15E148 Safari/604.1")
SITE = "https://chengguodj.com"
DEBUG = True

# 图片扩展名白名单（用于从原始文本里兜底抠图）
IMG_EXT = r'(?:jpg|jpeg|png|webp|gif|avif|bmp)'


def _dbg(*a):
    if DEBUG:
        print("[橙果]", *a)


def _clean(s):
    if not s:
        return ""
    s = htmllib.unescape(str(s))
    s = re.sub(r'<[^>]+>', ' ', s)
    return re.sub(r'\s+', ' ', s).strip()


def _de_esc(u):
    """还原 Nuxt 转义后的 URL：\\u002F / \\/ 都转成 /"""
    u = str(u)
    u = u.replace('\\u002F', '/').replace('\\u002f', '/')
    u = u.replace('\\/', '/').replace('\\\\', '\\')
    u = u.replace('\\u0026', '&')
    return u


def _abs(u):
    """相对路径 → 绝对 URL"""
    if not u:
        return ""
    u = _de_esc(u).strip().strip('\'"')
    if not u:
        return ""
    if u.startswith('//'):
        return "https:" + u
    if u.startswith('/'):
        return SITE + u
    return u


def _proxy(u):
    """
    关键修复：把封面原图包成站内图片代理地址。
    CDN 直连拿到的是加密字节，无法解码；/_img/<b64> 返回正常图片。
    使用 urlsafe base64 并去掉尾部 '='，与站点渲染出的 src 完全一致。
    """
    u = _abs(u)
    if not u.startswith('http'):
        return ""
    try:
        b = base64.urlsafe_b64encode(u.encode('utf-8')).decode('ascii')
    except Exception:
        return ""
    return "%s/_img/%s" % (SITE, b.rstrip('='))


class Spider(_Base):

    def getName(self):
        return "橙果短剧"

    def init(self, extend=""):
        self._pcache = {}          # tid -> 真实总页数，避免翻到 404 页
        try:
            self.s = rq.Session()
            self.s.verify = False
            self.s.headers.update({
                "User-Agent": UA,
                "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
                "Accept-Language": "zh-CN,zh;q=0.9",
                "Referer": SITE + "/",
            })
        except Exception:
            self.s = None

    # ────────── HTTP ──────────
    def _get(self, path, ref="/"):
        if path.startswith("http"):
            url = path
        else:
            url = SITE + path
        try:
            headers = {"Referer": SITE + ref}
            if self.s is not None:
                r = self.s.get(url, timeout=20, allow_redirects=True, headers=headers)
            else:
                r = rq.get(url, timeout=20, verify=False,
                           headers={"User-Agent": UA, **headers})
            if r.status_code == 200 and r.text:
                r.encoding = "utf-8"
                return r.text
            _dbg("_get %s -> %s" % (url, r.status_code))
        except Exception as e:
            _dbg("_get error:", e)
        return ""

    # ────────── Nuxt 原始文本 ──────────
    @staticmethod
    def _nuxt_raw(html):
        m = re.search(
            r'<script type="application/json" data-nuxt-data="nuxt-app"[^>]*id="__NUXT_DATA__">(.*?)</script>',
            html, re.S)
        if not m:
            m = re.search(r'id="__NUXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
        return m.group(1) if m else ""

    @classmethod
    def _nuxt_arr(cls, html):
        raw = cls._nuxt_raw(html)
        if not raw:
            return None
        try:
            return json.loads(raw)
        except Exception as e:
            _dbg("nuxt json err:", e)
            return None

    # ────────── Nuxt 扁平引用还原 ──────────
    @classmethod
    def _resolve(cls, arr, i, depth=0):
        if depth > 200:
            return None
        if not isinstance(i, int) or i < 0 or i >= len(arr):
            return None
        v = arr[i]
        if isinstance(v, list):
            if not v:
                return []
            head = v[0]
            if head in ("ShallowReactive", "Reactive", "ShallowRef", "Ref", "ComputedRef"):
                return cls._resolve(arr, v[1], depth + 1) if len(v) > 1 else None
            if head == "Set":
                return [cls._resolve(arr, x, depth + 1) for x in v[1:]]
            return [cls._resolve(arr, x, depth + 1) for x in v]
        if isinstance(v, dict):
            return {k: cls._resolve(arr, x, depth + 1) for k, x in v.items()}
        return v

    @classmethod
    def _nuxt_tree(cls, html):
        arr = cls._nuxt_arr(html)
        if not isinstance(arr, list) or len(arr) < 2:
            return None
        return cls._resolve(arr, 1)

    @classmethod
    def _walk(cls, obj, out):
        if isinstance(obj, dict):
            slug = obj.get("slug")
            if isinstance(slug, str) and slug.startswith("dj-") and "title" in obj:
                out.append(obj)
            for v in obj.values():
                cls._walk(v, out)
        elif isinstance(obj, list):
            for x in obj:
                cls._walk(x, out)

    @classmethod
    def _find_page(cls, obj, node):
        if isinstance(obj, dict):
            if isinstance(obj.get("total_pages"), int) and "page" in obj:
                node.clear()
                node.update(obj)
            for v in obj.values():
                cls._find_page(v, node)
        elif isinstance(obj, list):
            for x in obj:
                cls._find_page(x, node)

    @classmethod
    def _nuxt_items(cls, html):
        tree = cls._nuxt_tree(html)
        if not tree:
            return [], {}
        buf = []
        cls._walk(tree, buf)
        seen = {}
        for it in buf:
            s = it.get("slug")
            if s and s not in seen:
                seen[s] = it
        page = {}
        cls._find_page(tree, page)
        return list(seen.values()), page

    # ────────── 封面：统一走 /_img/ 代理 ──────────
    @staticmethod
    def _pic(u):
        """任意形态封面字段 → 可直接显示的绝对图片地址"""
        u = _abs(u)
        if not u.startswith('http'):
            return ""
        low = u.split('?')[0].lower()
        # 排除明显不是图的地址
        if low.endswith(('.m3u8', '.mp4', '.ts', '.js', '.css', '.html')):
            return ""
        if 'wlwvch' not in low and '/static/' not in low and not low.endswith(
                tuple('.' + e for e in ('jpg', 'jpeg', 'png', 'webp', 'gif', 'avif', 'bmp'))):
            return ""
        return _proxy(u)

    @classmethod
    def _cover_from_item(cls, it):
        """从剧集对象里取封面（cover / poster / thumbnail 等）"""
        # 1) 对象形态：{"url":..., "fallback_url":...}
        for key in ("cover", "poster", "thumbnail", "image", "pic", "thumb"):
            c = it.get(key)
            if isinstance(c, dict):
                for k in ("url", "fallback_url", "source_url", "original_url", "poster_url"):
                    u = cls._pic(c.get(k))
                    if u:
                        return u
            elif isinstance(c, str):
                u = cls._pic(c)
                if u:
                    return u
        # 2) 扁平字段
        for k in ("cover_url", "poster_url", "poster_fallback_url",
                  "thumbnail_url", "image_url", "thumb_url"):
            u = cls._pic(it.get(k))
            if u:
                return u
        return ""

    @classmethod
    def _cover_from_raw(cls, nuxt_raw):
        """兜底：从 Nuxt 原始文本里抠图（先还原 \\u002F 转义）"""
        if not nuxt_raw:
            return ""
        raw = _de_esc(nuxt_raw)
        for m in re.finditer(
                r'"(?:url|fallback_url|poster_url|poster_fallback_url|thumbnailUrl)"'
                r':"(https?://[^"]+?\.' + IMG_EXT + r')"', raw, re.I):
            u = cls._pic(m.group(1))
            if u:
                return u
        return ""

    # ────────── items → 卡片 ──────────
    @staticmethod
    def _episode_remark(it):
        n = it.get("latest_episode_number") or it.get("published_episode_count") \
            or it.get("total_episode_count") or 0
        try:
            n = int(n)
        except Exception:
            n = 0
        if not n:
            return ""
        if it.get("serial_status", "") == "completed":
            return "全%d集" % n
        return "更新至%d集" % n

    @classmethod
    def _cards_from_items(cls, items):
        videos = []
        for it in items:
            slug = it.get("slug", "")
            if not slug:
                continue
            title = _clean(it.get("title", ""))
            if not title:
                continue
            pic = cls._cover_from_item(it)
            videos.append({
                "vod_id": "drama_" + slug,
                "vod_name": title,
                "vod_pic": pic,
                "vod_remarks": cls._episode_remark(it),
            })
        return videos

    # ────────── 详情页取图 ──────────
    def _detail_cover(self, html, slug):
        # 1) 最稳：按 slug 在 Nuxt 数据里定位主剧对象
        items, _ = self._nuxt_items(html)
        for it in items:
            if it.get("slug") == slug:
                u = self._cover_from_item(it)
                if u:
                    return u
        # 2) 原始文本里按 slug 就近匹配
        raw = _de_esc(self._nuxt_raw(html))
        if raw:
            pat = (re.escape(slug) +
                   r'(?:(?!dj-)[^"\\]){0,400}?"url":"(https?://[^"]+?\.' + IMG_EXT + r')"')
            m = re.search(pat, raw, re.S)
            if m:
                u = self._pic(m.group(1))
                if u:
                    return u
            # 3) 整段第一个图片地址
            return self._cover_from_raw(raw)
        return ""

    # ────────── 详情页选集 ──────────
    @staticmethod
    def _detail_episodes(html, slug):
        nums = set()
        for n in re.findall(r'/play/' + re.escape(slug) + r'/(\d+)', html):
            try:
                nums.add(int(n))
            except Exception:
                pass
        return sorted(nums)

    @staticmethod
    def _detail_count(html):
        m = re.search(r'更新至(\d+)集', html)
        if m:
            try:
                return int(m.group(1))
            except Exception:
                pass
        m = re.search(r'共(\d+)集', html)
        if m:
            try:
                return int(m.group(1))
            except Exception:
                pass
        return 0

    # ────────── 分类定义 ──────────
    def homeContent(self, filter):
        return {
            "class": [
                {"type_name": "推荐", "type_id": "home"},
                {"type_name": "原创", "type_id": "yuanchuang"},
                {"type_name": "魔改", "type_id": "mogai"},
                {"type_name": "AI漫剧", "type_id": "manju"},
                {"type_name": "真人短剧", "type_id": "zhenren"},
                {"type_name": "AI短剧", "type_id": "aiduanju"},
                {"type_name": "刷剧", "type_id": "feed"},
                {"type_name": "分类", "type_id": "browse"},
            ]
        }

    # ────────── 首页 ──────────
    def homeVideoContent(self):
        html = self._get("/")
        items, _ = self._nuxt_items(html)
        videos = self._cards_from_items(items)
        miss = sum(1 for v in videos if not v["vod_pic"])
        _dbg("home items:", len(videos), "无封面:", miss)
        return {"list": videos}

    # ────────── 分页 URL ──────────
    @staticmethod
    def _page_url(tid, pg):
        if tid == "home" or not tid:
            return "/" if pg <= 1 else "/page-%d" % pg
        base = "/" + tid
        if pg <= 1:
            return base
        return "%s/page-%d" % (base, pg)

    # ────────── 分类 + 翻页 ──────────
    def categoryContent(self, tid, pg, flt, extend):
        pg = int(pg or 1)
        html = self._get(self._page_url(tid, pg))
        items, page = self._nuxt_items(html)
        videos = self._cards_from_items(items)

        # 首页没有 /page-N 路由（404），不要回退到首页造成重复数据
        if not videos and pg == 1 and tid not in ("home", ""):
            home_html = self._get("/")
            items2, _ = self._nuxt_items(home_html)
            videos = self._cards_from_items(items2)

        # 总页数：优先用本次返回，其次用第 1 页缓存
        total_pages = 1
        if page:
            try:
                total_pages = int(page.get("total_pages", 1))
            except Exception:
                total_pages = 1
        if pg == 1 and total_pages >= 1:
            self._pcache[tid] = total_pages
        elif tid in self._pcache:
            total_pages = max(total_pages, self._pcache[tid])
        if total_pages < 1:
            total_pages = 1
        # 已无数据 → 立刻停止翻页，避免继续请求 404
        if not videos and pg > 1:
            total_pages = pg - 1

        try:
            page_size = int(page.get("page_size", 30)) if page else 30
        except Exception:
            page_size = 30
        try:
            total = int(page.get("total", len(videos))) if page else len(videos)
        except Exception:
            total = len(videos)

        miss = sum(1 for v in videos if not v["vod_pic"])
        _dbg("cat tid=%s pg=%s items=%d 无封面=%d pagecount=%d" %
             (tid, pg, len(videos), miss, total_pages))

        return {
            "list": videos,
            "page": pg,
            "pagecount": total_pages,
            "limit": page_size,
            "total": total,
        }

    # ────────── 详情 ──────────
    def detailContent(self, ids):
        raw = ids[0] if isinstance(ids, list) else ids
        slug = raw.replace("drama_", "")
        m = re.search(r'(dj-[a-zA-Z0-9]+)', slug)
        if m:
            slug = m.group(1)

        html = self._get("/drama/" + slug)
        if len(html) < 200:
            return {"list": []}

        items, _ = self._nuxt_items(html)
        mine = None
        for it in items:
            if it.get("slug") == slug:
                mine = it
                break

        # 标题
        title = _clean(mine.get("title", "")) if mine else ""
        if not title:
            m = re.search(r'<div class="mt-\[1\.2rem\][^>]*>.*?<p[^>]*>([^<]+)</p>', html, re.S)
            if m:
                title = _clean(m.group(1))
        if not title:
            m = re.search(r'<h1>([^<]+)</h1>', html)
            if m:
                title = _clean(m.group(1))
        if not title:
            title = slug

        # 封面
        pic = ""
        if mine:
            pic = self._cover_from_item(mine)
        if not pic:
            pic = self._detail_cover(html, slug)

        # 集数
        cnt = self._detail_count(html)
        if mine:
            n = mine.get("latest_episode_number") or mine.get("published_episode_count") \
                or mine.get("total_episode_count") or 0
            try:
                cnt = max(cnt, int(n))
            except Exception:
                pass

        # 选集
        nums = self._detail_episodes(html, slug)
        if nums:
            eps = ["第%d集$ep_%s_%d" % (n, slug, n) for n in nums]
        elif cnt > 0:
            eps = ["第%d集$ep_%s_%d" % (n, slug, n) for n in range(1, cnt + 1)]
        else:
            eps = ["第1集$ep_%s_1" % slug]

        _dbg("detail slug=", slug, "title=", title, "pic=", pic[:70] if pic else "EMPTY",
             "cnt=", cnt, "eps=", len(eps))

        return {
            "list": [{
                "vod_id": "drama_" + slug,
                "vod_name": title,
                "vod_pic": pic,
                "vod_play_from": "橙果短剧",
                "vod_play_url": "#".join(eps),
                "vod_remarks": ("更新至%d集" % cnt) if cnt else "",
            }]
        }

    # ────────── 播放 ──────────
    @staticmethod
    def _m3u8_from_tree(tree):
        """从 Nuxt 还原树里找 m3u8（media.source_url）"""
        if not tree:
            return ""
        stack = [tree]
        while stack:
            o = stack.pop()
            if isinstance(o, dict):
                for k in ("source_url", "h265_url", "play_url", "url"):
                    v = o.get(k)
                    if isinstance(v, str) and ".m3u8" in _de_esc(v):
                        u = _de_esc(v).strip()
                        if u.startswith("http"):
                            return u
                stack.extend(o.values())
            elif isinstance(o, list):
                stack.extend(o)
        return ""

    def playerContent(self, flag, id, vipFlags):
        try:
            _, slug, num = id.split("_")
        except Exception as e:
            _dbg("player id err:", e, id)
            return {"parse": 0, "url": "", "header": {"User-Agent": UA}}

        html = self._get("/play/" + slug + "/" + num)
        if len(html) < 200:
            return {"parse": 0, "url": "", "header": {"User-Agent": UA}}

        cand = []

        # 0) Nuxt 还原树（最准）
        u = self._m3u8_from_tree(self._nuxt_tree(html))
        if u:
            cand.append(u)

        # 1) __NUXT_DATA__ 原始文本
        nuxt_raw = self._nuxt_raw(html)
        if nuxt_raw:
            raw = _de_esc(nuxt_raw)
            for x in re.findall(r'https?://[^\s"\\\']+?\.m3u8[^\s"\\\']*', raw):
                cand.append(x)

        # 2) HTML 多层兜底
        for x in re.findall(r'(https?:\\?/\\?/[^\s"\'\\)]+?\.m3u8[^\s"\'\\)]*)', html):
            cand.append(x)
        for x in re.findall(r'https?://[^\s"\'\\)]+\.m3u8[^\s"\'\\)]*', html):
            cand.append(x)
        m = re.search(r'<meta property="og:video(?::secure_url)?" content="([^"]+\.m3u8)"', html)
        if m:
            cand.append(m.group(1))
        m = re.search(r'<video[^>]*src="([^"]+\.m3u8)"', html, re.I)
        if m:
            cand.append(m.group(1))

        for raw in cand:
            u = _de_esc(raw).strip().strip('\'"')
            u = u.replace('\\"', '"')
            if '.m3u8' in u and u.startswith('http'):
                _dbg("m3u8 ok:", u[:90])
                return {
                    "parse": 0,
                    "url": u,
                    "header": {
                        "User-Agent": UA,
                        "Referer": "https://chengguodj.com/",
                        "Origin": "https://chengguodj.com",
                    }
                }

        _dbg("未找到 m3u8 slug=", slug, "num=", num, "html_len=", len(html))
        return {"parse": 0, "url": "", "header": {"User-Agent": UA}}

    # ────────── 搜索 ──────────
    def searchContent(self, key, quick, pg="1"):
        pg = int(pg or 1)
        key = str(key)

        videos = []
        page = {}
        for path in (
            "/search/" + urllib.parse.quote(key),
            "/search?s=" + urllib.parse.quote(key),
        ):
            html = self._get(path)
            if not html:
                continue
            items, page = self._nuxt_items(html)
            videos = self._cards_from_items(items)
            if videos:
                break

        total_pages = 1
        if page:
            try:
                total_pages = int(page.get("total_pages", 1))
            except Exception:
                total_pages = 1
        if total_pages < 1:
            total_pages = 1

        try:
            page_size = int(page.get("page_size", 30)) if page else 30
        except Exception:
            page_size = 30
        try:
            total = int(page.get("total", len(videos))) if page else len(videos)
        except Exception:
            total = len(videos)

        _dbg("search %s items=%d" % (key, len(videos)))
        return {
            "list": videos,
            "page": pg,
            "pagecount": total_pages,
            "limit": page_size,
            "total": total,
        }

    def localProxy(self, param):
        return None

    def isVideoFormat(self, url):
        return bool(url) and (".m3u8" in url or url.lower().endswith((".mp4", ".ts")))

    def manualVideoCheck(self):
        return False
