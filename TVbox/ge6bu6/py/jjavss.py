# -*- coding: utf-8 -*-
# jjavss / javkk WordPress 站点四壳 Python 源
# 站点: https://javkk1gdfgd.ttt110.cyou/jjavss
# 播放: v.hersav.me 直链 m3u8
try:
    from base.spider import Spider as _BaseSpider
except Exception:
    class _BaseSpider(object):
        def getName(self):
            return ""

        def init(self, extend=""):
            pass


class Spider(_BaseSpider):
    def __init__(self):
        self.host = "https://javkk1gdfgd.ttt110.cyou"
        self.ua = (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        )
        self.headers = {
            "User-Agent": self.ua,
            "Referer": self.host + "/",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        }

    def getName(self):
        return "jjavss"

    def init(self, extend=""):
        pass

    def getDependence(self):
        return ""

    def isVideoFormat(self, url):
        if not url:
            return False
        u = str(url).lower()
        return any(u.endswith(x) or x in u for x in (".m3u8", ".mp4", ".mkv", ".flv"))

    def manualVideoCheck(self):
        return False

    def destroy(self):
        pass

    def localProxy(self, param):
        return [404, "text/plain", b""]

    def homeContent(self, filter):
        classes = [
            {"type_id": "jjavss", "type_name": "最新"},
            {"type_id": "tag:fc2", "type_name": "Fc2"},
            {"type_id": "tag:素人", "type_name": "素人"},
            {"type_id": "s:4610", "type_name": "H4610"},
            {"type_id": "s:0930", "type_name": "H0930"},
            {"type_id": "s:无码", "type_name": "无码"},
            {"type_id": "s:中出", "type_name": "中出"},
            {"type_id": "s:人妻", "type_name": "人妻"},
        ]
        result = {"class": classes}
        if filter:
            result["filters"] = {c["type_id"]: [] for c in classes}
        return result

    def homeVideoContent(self):
        return self.categoryContent("jjavss", "1", False, {})

    def categoryContent(self, tid, pg, filter, extend):
        try:
            page = max(1, int(pg or 1))
        except Exception:
            page = 1
        tid = str(tid or "jjavss")
        url = self._list_url(tid, page)
        html = self._fetch(url)
        items = self._parse_list(html)
        # WordPress 常见每页 10~50；有下一页则 pagecount=page+1
        has_next = bool(
            html
            and (
                ("/page/%s" % (page + 1)) in html
                or ("paged=%s" % (page + 1)) in html
                or "Older posts" in html
                or "older" in html.lower()
            )
        )
        pagecount = page + 1 if has_next and items else max(1, page)
        return {
            "list": items,
            "page": page,
            "pagecount": pagecount,
            "limit": max(1, len(items)),
            "total": pagecount * max(1, len(items)),
        }

    def searchContent(self, key, quick, pg="1"):
        try:
            page = max(1, int(pg or 1))
        except Exception:
            page = 1
        q = str(key or "").strip()
        if not q:
            return {"list": [], "page": 1, "pagecount": 1, "limit": 0, "total": 0}
        if page <= 1:
            url = "%s/?s=%s" % (self.host, self._quote(q))
        else:
            url = "%s/page/%s/?s=%s" % (self.host, page, self._quote(q))
        html = self._fetch(url)
        items = self._parse_list(html)
        has_next = bool(html and (("/page/%s/" % (page + 1)) in html or "Older" in html))
        pagecount = page + 1 if has_next and items else max(1, page)
        return {
            "list": items,
            "page": page,
            "pagecount": pagecount,
            "limit": max(1, len(items)),
            "total": pagecount * max(1, len(items)),
        }

    def detailContent(self, ids):
        vid = str(ids[0] if ids else "").strip()
        if not vid:
            return {"list": []}
        url = "%s/jav/%s.html" % (self.host, vid)
        html = self._fetch(url)
        if not html:
            return {"list": []}
        name = self._meta_title(html) or ("影片 %s" % vid)
        pic = self._first(
            self._findall(r'<img[^>]+src="(https://v\.hersav\.me/images/[^"]+)"', html)
            or self._findall(r'<img[^>]+src="(https://[^"]+\.(?:jpg|jpeg|png|webp)[^"]*)"', html)
        )
        # 直链 m3u8
        m3u8s = self._findall(
            r'(https://v\.hersav\.me/[0-9a-f]{16,}/[0-9a-f]{16,}\.m3u8)', html
        )
        if not m3u8s:
            m3u8s = self._findall(r'(https://[^"\'\s<>]+\.m3u8)', html)
        # 去重保序
        seen = set()
        play = []
        for i, u in enumerate(m3u8s):
            if u in seen:
                continue
            seen.add(u)
            play.append("%s$%s" % ("播放%d" % (i + 1) if i else "正片", u))
        if not play:
            # iframe /t/t.php?url=
            for u in self._findall(r'/t/t\.php\?url=(https?://[^"\'&\s]+)', html):
                if u not in seen:
                    seen.add(u)
                    play.append("正片$%s" % u)
        if not play:
            return {
                "list": [
                    {
                        "vod_id": vid,
                        "vod_name": name,
                        "vod_pic": pic or "",
                        "vod_content": "未解析到播放地址",
                        "vod_play_from": "提示",
                        "vod_play_url": "暂无$0",
                    }
                ]
            }
        return {
            "list": [
                {
                    "vod_id": vid,
                    "vod_name": name,
                    "vod_pic": pic or "",
                    "vod_content": name,
                    "vod_play_from": "jjavss",
                    "vod_play_url": "#".join(play),
                }
            ]
        }

    def playerContent(self, flag, id, vipFlags):
        url = str(id or "").strip()
        if not url or url == "0":
            return {"parse": 0, "jx": 0, "url": "", "msg": "无播放地址"}
        # 相对补全
        if url.startswith("//"):
            url = "https:" + url
        headers = {
            "User-Agent": self.ua,
            "Referer": self.host + "/",
            "Origin": "https://v.hersav.me",
        }
        return {
            "parse": 0,
            "jx": 0,
            "url": url,
            "header": headers,
        }

    # ---------- helpers ----------
    def _list_url(self, tid, page):
        if tid.startswith("tag:"):
            tag = tid[4:]
            path = "/jav/tag/" + self._quote(tag)
            if page > 1:
                return "%s%s/page/%s" % (self.host, path, page)
            return self.host + path
        if tid.startswith("s:"):
            q = tid[2:]
            if page > 1:
                return "%s/page/%s/?s=%s" % (self.host, page, self._quote(q))
            return "%s/?s=%s" % (self.host, self._quote(q))
        # 默认 jjavss 列表
        if page > 1:
            return "%s/jjavss/page/%s" % (self.host, page)
        return self.host + "/jjavss"

    def _parse_list(self, html):
        if not html:
            return []
        items = []
        seen = set()
        # title 优先
        for m in self._finditer(
            r'href="https://javkk1gdfgd\.ttt110\.cyou/jav/(\d+)\.html"[^>]*title="Permanent Link to ([^"]*)"',
            html,
        ):
            vid, name = m.group(1), self._clean(m.group(2))
            if vid in seen or self._skip_minor(name):
                continue
            seen.add(vid)
            items.append(
                {
                    "vod_id": vid,
                    "vod_name": name or ("影片 %s" % vid),
                    "vod_pic": "",
                    "vod_remarks": "",
                }
            )
        # 补图
        pic_map = {}
        for m in self._finditer(
            r'href="https://javkk1gdfgd\.ttt110\.cyou/jav/(\d+)\.html"[^>]*>\s*<img[^>]+src="([^"]+)"',
            html,
        ):
            pic_map[m.group(1)] = m.group(2)
        for it in items:
            if it["vod_id"] in pic_map:
                it["vod_pic"] = pic_map[it["vod_id"]]
        # 若 title 模式无结果，退回 img 模式
        if not items:
            for m in self._finditer(
                r'href="https://javkk1gdfgd\.ttt110\.cyou/jav/(\d+)\.html"[^>]*>\s*<img[^>]+src="([^"]+)"[^>]*(?:alt="([^"]*)")?',
                html,
            ):
                vid = m.group(1)
                if vid in seen:
                    continue
                name = self._clean(m.group(3) or "")
                if self._skip_minor(name):
                    continue
                seen.add(vid)
                items.append(
                    {
                        "vod_id": vid,
                        "vod_name": name or ("影片 %s" % vid),
                        "vod_pic": m.group(2),
                        "vod_remarks": "",
                    }
                )
        return items

    @staticmethod
    def _skip_minor(name):
        """未成年相关标题跳过（铁律）"""
        n = (name or "").lower()
        keys = (
            "萝莉",
            "幼女",
            "小学生",
            "中学生",
            "js",
            "loli",
            "little girl",
            "underage",
            "未成年",
            "幼",
            "女童",
        )
        # 标题含「学生」且未标明成年时也谨慎：仅拦明确幼向词
        return any(k in n for k in keys)

    def _meta_title(self, html):
        for pat in (
            r'<meta[^>]+property="og:title"[^>]+content="([^"]+)"',
            r"<title>([^<]+)</title>",
            r'title="Permanent Link to ([^"]+)"',
        ):
            m = self._search(pat, html)
            if m:
                t = self._clean(m)
                t = t.replace(" - javkk", "").replace("jjavss", "").strip(" -|")
                if t:
                    return t
        return ""

    def _fetch(self, url):
        try:
            import requests

            r = requests.get(url, headers=self.headers, timeout=20, verify=False)
            if r.status_code == 200 and r.text:
                r.encoding = r.apparent_encoding or "utf-8"
                return r.text
        except Exception:
            pass
        try:
            from urllib.request import Request, urlopen

            req = Request(url, headers=self.headers)
            with urlopen(req, timeout=20) as resp:
                return resp.read().decode("utf-8", "ignore")
        except Exception:
            return ""

    @staticmethod
    def _quote(s):
        try:
            from urllib.parse import quote

            return quote(str(s), safe="")
        except Exception:
            return str(s)

    @staticmethod
    def _clean(s):
        import re

        s = re.sub(r"<[^>]+>", "", str(s or ""))
        return (
            s.replace("&amp;", "&")
            .replace("&#038;", "&")
            .replace("&lt;", "<")
            .replace("&gt;", ">")
            .replace("&quot;", '"')
            .strip()
        )

    @staticmethod
    def _search(pat, text):
        import re

        m = re.search(pat, text or "", re.I)
        return m.group(1) if m else ""

    @staticmethod
    def _findall(pat, text):
        import re

        return re.findall(pat, text or "", re.I)

    @staticmethod
    def _finditer(pat, text):
        import re

        return list(re.finditer(pat, text or "", re.I | re.S))

    @staticmethod
    def _first(arr):
        return arr[0] if arr else ""
