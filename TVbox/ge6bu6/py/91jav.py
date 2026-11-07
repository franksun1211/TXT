# -*- coding: utf-8 -*-
# 91jav / alcohol.kzjdba.cc 四壳 Python 源
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
        self.host = "https://alcohol.kzjdba.cc"
        self.ua = (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        )
        self.headers = {
            "User-Agent": self.ua,
            "Referer": self.host + "/",
            "Accept-Language": "zh-CN,zh;q=0.9",
        }

    def getName(self):
        return "91jav"

    def init(self, extend=""):
        pass

    def getDependence(self):
        return ""

    def isVideoFormat(self, url):
        u = str(url or "").lower()
        return any(x in u for x in (".m3u8", ".mp4", ".mkv"))

    def manualVideoCheck(self):
        return False

    def destroy(self):
        pass

    def localProxy(self, param):
        return [404, "text/plain", b""]

    def homeContent(self, filter):
        classes = [
            {"type_id": "home", "type_name": "首页最新"},
            {"type_id": "theme/detail/3", "type_name": "中文字幕"},
            {"type_id": "theme/detail/11/hot", "type_name": "无码高清"},
            {"type_id": "actress/hot", "type_name": "热门女优"},
            {"type_id": "theme", "type_name": "专题合集"},
            {"type_id": "recommend", "type_name": "推荐"},
        ]
        result = {"class": classes}
        if filter:
            result["filters"] = {c["type_id"]: [] for c in classes}
        return result

    def homeVideoContent(self):
        return self.categoryContent("home", "1", False, {})

    def categoryContent(self, tid, pg, filter, extend):
        try:
            page = max(1, int(pg or 1))
        except Exception:
            page = 1
        tid = str(tid or "home").strip("/")
        if tid == "home":
            url = self.host + "/"
            if page > 1:
                url = "%s/?page=%s" % (self.host, page)
        else:
            url = "%s/%s/" % (self.host, tid)
            if page > 1:
                url = "%s/%s/?page=%s" % (self.host, tid, page)
        html = self._fetch(url)
        items = self._parse_list(html)
        has_next = bool(html and (("page=%s" % (page + 1)) in html or len(items) >= 8))
        pagecount = page + 1 if has_next and items else max(1, page)
        return {
            "list": items,
            "page": page,
            "pagecount": pagecount,
            "limit": max(1, len(items) or 12),
            "total": pagecount * max(1, len(items) or 12),
        }

    def searchContent(self, key, quick, pg="1"):
        try:
            page = max(1, int(pg or 1))
        except Exception:
            page = 1
        q = str(key or "").strip()
        if not q:
            return {"list": [], "page": 1, "pagecount": 1, "limit": 0, "total": 0}
        # 站内搜索不稳定时用番号路径探测 + 首页过滤
        url = "%s/search?q=%s" % (self.host, self._quote(q))
        if page > 1:
            url += "&page=%s" % page
        html = self._fetch(url)
        items = self._parse_list(html)
        # 若搜索回首页，再按标题过滤
        if items and len(items) > 20:
            ql = q.lower()
            filtered = [it for it in items if ql in (it.get("vod_name") or "").lower() or ql in (it.get("vod_id") or "").lower()]
            if filtered:
                items = filtered
        # 直接尝试 /videos/{key}
        if not items:
            code = q.upper().replace(" ", "-")
            dhtml = self._fetch("%s/videos/%s" % (self.host, code))
            if dhtml and "404" not in (self._meta_title(dhtml) or ""):
                items = [
                    {
                        "vod_id": code,
                        "vod_name": self._meta_title(dhtml) or code,
                        "vod_pic": "",
                        "vod_remarks": "",
                    }
                ]
        has_next = bool(html and len(items) >= 10)
        pagecount = page + 1 if has_next else max(1, page)
        return {
            "list": items,
            "page": page,
            "pagecount": pagecount,
            "limit": max(1, len(items) or 12),
            "total": pagecount * max(1, len(items) or 12),
        }

    def detailContent(self, ids):
        code = str(ids[0] if ids else "").strip().lstrip("/")
        if code.startswith("videos/"):
            code = code[7:]
        if not code:
            return {"list": []}
        url = "%s/videos/%s" % (self.host, code)
        html = self._fetch(url)
        if not html:
            return {"list": []}
        name = self._meta_title(html) or code
        pic = self._first(
            self._findall(
                r'(https://d3dm9qako275qq\.cloudfront\.net/upload[^"\']+\.(?:jpg|jpeg|png|webp)[^"\']*)',
                html,
            )
        )
        m3u8s = self._findall(
            r'(https://[^"\']+\.m3u8[^"\']*)',
            html,
        )
        # 去重保序
        seen = set()
        play = []
        for i, u in enumerate(m3u8s):
            u = u.replace("&amp;", "&")
            if u in seen:
                continue
            seen.add(u)
            play.append("%s$%s" % ("播放%d" % (i + 1) if i else "正片", u))
        if not play:
            return {
                "list": [
                    {
                        "vod_id": code,
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
                    "vod_id": code,
                    "vod_name": name,
                    "vod_pic": pic or "",
                    "vod_content": name,
                    "vod_play_from": "91jav",
                    "vod_play_url": "#".join(play),
                }
            ]
        }

    def playerContent(self, flag, id, vipFlags):
        url = str(id or "").strip()
        if not url or url == "0":
            return {"parse": 0, "jx": 0, "url": "", "msg": "无播放地址"}
        if url.startswith("//"):
            url = "https:" + url
        headers = {
            "User-Agent": self.ua,
            "Referer": self.host + "/",
            "Origin": self.host,
        }
        return {"parse": 0, "jx": 0, "url": url, "header": headers}

    def _parse_list(self, html):
        if not html:
            return []
        items = []
        seen = set()
        for m in self._finditer(
            r'href="(/videos/([^"]+))"[\s\S]{0,800}?(?:src|data-src)="(https://[^"]+)"[\s\S]{0,300}?alt="([^"]*)"',
            html,
        ):
            path, code, pic, alt = m.group(1), m.group(2), m.group(3), self._clean(m.group(4))
            if code in seen:
                continue
            seen.add(code)
            if self._skip_minor(alt or code):
                continue
            items.append(
                {
                    "vod_id": code,
                    "vod_name": alt or code,
                    "vod_pic": pic,
                    "vod_remarks": "",
                }
            )
        if not items:
            for m in self._finditer(r'href="/videos/([^"]+)"', html):
                code = m.group(1)
                if code in seen or "/" in code:
                    continue
                seen.add(code)
                if self._skip_minor(code):
                    continue
                items.append(
                    {
                        "vod_id": code,
                        "vod_name": code,
                        "vod_pic": "",
                        "vod_remarks": "",
                    }
                )
        return items

    @staticmethod
    def _skip_minor(name):
        n = (name or "").lower()
        keys = ("萝莉", "幼女", "小学生", "loli", "underage", "未成年", "女童")
        return any(k in n for k in keys)

    def _meta_title(self, html):
        for pat in (
            r'<meta[^>]+property="og:title"[^>]+content="([^"]+)"',
            r"<title>([^<]+)</title>",
        ):
            m = self._search(pat, html)
            if m:
                t = self._clean(m)
                for s in (" - 91jav", " | 91jav", " - 91JAV"):
                    if t.endswith(s):
                        t = t[: -len(s)]
                return t.strip()
        return ""

    def _fetch(self, url):
        try:
            import subprocess

            cmd = [
                "curl",
                "-sL",
                "--max-time",
                "25",
                "-A",
                self.ua,
                "-e",
                self.host + "/",
                url,
            ]
            out = subprocess.check_output(cmd, stderr=subprocess.DEVNULL)
            text = out.decode("utf-8", "ignore")
            if text and "Just a moment" not in text[:400]:
                return text
        except Exception:
            pass
        try:
            import requests
            import urllib3

            urllib3.disable_warnings()
            r = requests.get(url, headers=self.headers, timeout=20, verify=False)
            if r.status_code == 200 and r.text:
                return r.text
        except Exception:
            pass
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
            .replace("&quot;", '"')
            .replace("&#039;", "'")
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
