# coding: utf-8
# =========================================================
# 站点: Px8x (px8x1) — 成人影视站
# 主域名: https://www.px8x1.vip
# 发布页: /fabuye   备用导航: px8x.com / yt8x.com / fkk8.com
# 内容类型: 成人视频（py_18）
# 特征: 标题/分类名 base64 混淆（d() = atob -> escape -> decodeURIComponent）
#       播放地址内联 hls.loadSource('https://m.892539.xyz/play.php?site_id=..&source_id=..')
#       取链直接返回 m3u8，单目录分片、无广告特征 → 直链不代理
# 最后验证: 2026-10-22
# =========================================================
import re
import base64
from urllib.parse import quote, unquote, urljoin

from base.spider import Spider as BaseSpider


class Spider(BaseSpider):

    def __init__(self):
        self.extend = ""
        self.host = "https://www.px8x1.vip"
        self.hosts = [
            "https://www.px8x1.vip",
            "https://www.px8x.com",
            "https://www.yt8x.com",
            "https://www.fkk8.com",
        ]
        self._cached_host = ""
        self.classes = [
            {"type_id": "901179", "type_name": "国产"},
            {"type_id": "911179", "type_name": "有码"},
            {"type_id": "921179", "type_name": "无码"},
            {"type_id": "931179", "type_name": "欧美"},
            {"type_id": "941179", "type_name": "传媒"},
            {"type_id": "951179", "type_name": "探花"},
            {"type_id": "961179", "type_name": "中文"},
            {"type_id": "971179", "type_name": "动漫"},
        ]
        self.filters = {}
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 "
                          "(KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36",
            "Referer": self.host + "/",
        }

    # ---------------- 基础 ----------------

    def getName(self):
        return "Px8x"

    def getDependence(self):
        return []

    def init(self, extend=""):
        self.extend = extend or ""

    def destroy(self):
        self._cached_host = ""

    # ---------------- 工具函数 ----------------

    @staticmethod
    def _b64d(s):
        """站点 d() 等价实现：atob -> escape -> decodeURIComponent"""
        if not s:
            return ""
        s = s.replace("\\/", "/").replace("\\", "")
        try:
            raw = base64.b64decode(s + "=" * (-len(s) % 4))
        except Exception:
            return ""
        try:
            t = raw.decode("utf-8")
        except Exception:
            t = raw.decode("latin-1", errors="ignore")
        try:
            return unquote(t)
        except Exception:
            return t

    @staticmethod
    def _clean(t):
        """去掉 display:none 的噪音 span，再剥标签"""
        if not t:
            return ""
        t = re.sub(r"<span[^>]*display\s*:\s*none[^>]*>.*?</span>", "", t, flags=re.S | re.I)
        t = re.sub(r"<script[^>]*>.*?</script>", "", t, flags=re.S | re.I)
        t = re.sub(r"<[^>]+>", "", t)
        t = t.replace("&nbsp;", " ").replace("&amp;", "&").replace("&quot;", '"')
        return re.sub(r"\s+", " ", t).strip()

    def _decode_title(self, html_frag):
        """从片段里取 d('...') 解码后的真实标题；无混淆则走清洗"""
        if not html_frag:
            return ""
        m = re.search(r"d\('([^']+)'\)", html_frag)
        if m:
            return self._clean(self._b64d(m.group(1)))
        return self._clean(html_frag)

    @staticmethod
    def _norm_ids(ids):
        """ids 兼容 list/str/int/bytes"""
        if ids is None:
            return ""
        if isinstance(ids, (list, tuple)):
            if not ids:
                return ""
            ids = ids[0]
        if isinstance(ids, bytes):
            ids = ids.decode("utf-8", errors="ignore")
        return str(ids).strip()

    def _pick_host(self):
        """懒加载域名探测，首页零网络"""
        if self._cached_host:
            return self._cached_host
        for h in self.hosts:
            try:
                r = self.fetch(h + "/", headers=self.headers, timeout=12)
                if r and r.status_code == 200 and len(r.text or "") > 2000:
                    self._cached_host = h
                    self.host = h
                    self.headers["Referer"] = h + "/"
                    return h
            except Exception:
                continue
        self._cached_host = self.host
        return self.host

    def _get(self, url, referer=None):
        headers = dict(self.headers)
        if referer:
            headers["Referer"] = referer
        try:
            r = self.fetch(url, headers=headers, timeout=15)
            if r is None:
                return ""
            if r.status_code != 200:
                return ""
            return r.text or ""
        except Exception:
            return ""

    # ---------------- 列表解析 ----------------

    def _parse_cards(self, html):
        """卡片解析（多级兜底）"""
        if not html:
            return []
        blocks = html.split('<div class="thumbnail group">')
        if len(blocks) < 2:
            blocks = re.split(r'<div[^>]*class="[^"]*thumbnail[^"]*"[^>]*>', html)
        out = []
        seen = set()
        for b in blocks[1:]:
            m = re.search(r'href="(/video/(\d+)\.html)"', b)
            if not m:
                continue
            vid = m.group(2)
            if vid in seen:
                continue
            seen.add(vid)
            title = ""
            a = re.search(r'<a class="text-secondary[^"]*"[^>]*>(.*?)</a>', b, re.S)
            if a:
                title = self._decode_title(a.group(1))
            if not title:
                title = self._decode_title(b[:1500])
            pic = ""
            im = re.search(r'<img[^>]+(?:data-original|data-src|src)="([^"]+)"', b)
            if im:
                pic = im.group(1)
                if pic.startswith("//"):
                    pic = "https:" + pic
                elif pic.startswith("/"):
                    pic = urljoin(self.host, pic)
            out.append({
                "vod_id": vid,
                "vod_name": title or ("视频" + vid),
                "vod_pic": pic,
                "vod_remarks": "",
            })
        return out

    # ---------------- 5 大接口 ----------------

    def homeContent(self, filter):
        return {"class": self.classes, "filters": self.filters}

    def getHomeContent(self, filter):
        return self.homeContent(filter)

    def homeVideoContent(self):
        host = self._pick_host()
        html = self._get(host + "/")
        return {"list": self._parse_cards(html)}

    def categoryContent(self, tid, pg, filter, extend):
        host = self._pick_host()
        page = str(pg or "1")
        tid = str(tid or "")
        url = "%s/list/%s-%s.html" % (host, tid, page)
        html = self._get(url, referer=host + "/")
        items = self._parse_cards(html)
        return {
            "list": items,
            "page": int(page),
            "pagecount": 9999,
            "limit": 20,
            "total": 999999,
        }

    @staticmethod
    def _parse_extend(extend):
        """dict / '{a=b}' / 'a=b' 三种格式"""
        if not extend:
            return {}
        if isinstance(extend, dict):
            return extend
        s = str(extend).strip()
        if s.startswith("{") and s.endswith("}"):
            s = s[1:-1]
        out = {}
        for kv in s.split("&"):
            if "=" in kv:
                k, v = kv.split("=", 1)
                out[k.strip()] = v.strip()
        return out

    def detailContent(self, ids):
        raw = self._norm_ids(ids)
        if not raw:
            return {"list": []}
        vid = raw.split("|$|")[0].split("$")[0].strip()
        host = self._pick_host()
        url = "%s/video/%s.html" % (host, vid)
        html = self._get(url, referer=host + "/")

        if not html or len(html) < 1000:
            return self._skeleton(vid)

        title = ""
        m = re.search(r"<h1[^>]*>(.*?)</h1>", html, re.S)
        if m:
            title = self._decode_title(m.group(1))
        if not title:
            m = re.search(r"<title[^>]*>(.*?)</title>", html, re.S)
            if m:
                title = self._decode_title(m.group(1))
        if not title:
            title = "视频" + vid

        pic = ""
        for p in [r'<meta[^>]+property="og:image"[^>]+content="([^"]+)"',
                  r'<img[^>]+src="([^"]*(?:upload|images)[^"]+)"']:
            mm = re.search(p, html)
            if mm:
                pic = mm.group(1)
                if pic.startswith("//"):
                    pic = "https:" + pic
                break

        # 播放地址：L1 hls.loadSource / L2 播放器变量 / L3 全文正则
        play_url = ""
        m = re.search(r"hls\.loadSource\(\s*['\"]([^'\"]+)['\"]", html)
        if m:
            play_url = m.group(1)
        if not play_url:
            m = re.search(r"source\s*:\s*['\"]([^'\"]+?\.(?:m3u8|mp4))['\"]", html)
            if m:
                play_url = m.group(1)
        if not play_url:
            m = re.search(r'https?://[^\s\'"<>]+play\.php\?[^\s\'"<>]+', html)
            if m:
                play_url = m.group(0)
        if not play_url:
            return self._skeleton(vid, title, pic)

        play_url = play_url.replace("\\/", "/")
        if play_url.startswith("//"):
            play_url = "https:" + play_url
        elif play_url.startswith("/"):
            play_url = urljoin(host, play_url)

        pid = play_url.replace("$", "|")
        return {"list": [{
            "vod_id": vid,
            "vod_name": title,
            "vod_pic": pic,
            "vod_remarks": "",
            "vod_content": title,
            "vod_play_from": "Px8x",
            "vod_play_url": "正片$" + pid,
        }]}

    def _skeleton(self, vid, title="", pic="", remarks=""):
        pid = str(vid).split("|$|")[0].replace("$", "|")
        return {"list": [{
            "vod_id": vid,
            "vod_name": title or ("视频" + str(vid)),
            "vod_pic": pic or "",
            "vod_remarks": remarks,
            "vod_content": "",
            "vod_play_from": "Px8x",
            "vod_play_url": "正片$" + pid,
        }]}

    def searchContent(self, key, quick, pg="1"):
        host = self._pick_host()
        page = str(pg or "1")
        kw = quote(str(key or "").encode("utf-8"))
        url = "%s/search.php?content=%s" % (host, kw)
        html = self._get(url, referer=host + "/")
        items = self._parse_cards(html)
        return {"list": items, "page": int(page)}

    def playerContent(self, flag, id, vipFlags):
        raw = str(id or "").replace("|", "$")
        if raw.startswith("http"):
            url = raw
        elif raw.startswith("/"):
            url = urljoin(self.host, raw)
        else:
            vid = raw.split("|$|")[0].split("$")[0].strip()
            url = "%s/video/%s.html" % (self._pick_host(), vid)
            html = self._get(url, referer=self.host + "/")
            m = re.search(r"hls\.loadSource\(\s*['\"]([^'\"]+)['\"]", html)
            if not m:
                m = re.search(r'https?://[^\s\'"<>]+play\.php\?[^\s\'"<>]+', html)
            if not m:
                return {"parse": 1, "url": url, "header": dict(self.headers)}
            url = m.group(1).replace("\\/", "/")

        # 取链 play.php 直接返回 m3u8，单目录无广告特征 → 原始直链，不代理
        return {
            "parse": 0,
            "url": url,
            "header": {
                "User-Agent": self.headers["User-Agent"],
                "Referer": self.host + "/",
            },
        }

    def recommendContent(self, ids, pg):
        return {"list": []}