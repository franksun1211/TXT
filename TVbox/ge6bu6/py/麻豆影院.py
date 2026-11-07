# -*- coding: utf-8 -*-
import json
import ssl
import urllib.parse
import urllib.request
import re

try:
    from base.spider import Spider as BaseSpider
except Exception:
    class BaseSpider(object):
        pass

def unescape_js(text):
    if not text:
        return ""
    text = re.sub(r"%u([0-9a-fA-F]{4})", lambda m: chr(int(m.group(1), 16)), text)
    text = urllib.parse.unquote(text)
    return text

class Spider(BaseSpider):
    def __init__(self):
        super(Spider, self).__init__()
        self.site_url = "https://mdyy627887c0.1010953.xyz"
        self.ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"

    def init(self, extend=""):
        pass

    def homeContent(self, filter):
        classes = [
            {"type_id": "1", "type_name": "麻豆传媒"},
            {"type_id": "2", "type_name": "高清国产"},
            {"type_id": "3", "type_name": "日韩无码"},
            {"type_id": "4", "type_name": "亚洲有码"},
            {"type_id": "5", "type_name": "中文字幕"},
            {"type_id": "7", "type_name": "岛国女优"},
            {"type_id": "8", "type_name": "明星换脸"},
            {"type_id": "9", "type_name": "美女主播"},
            {"type_id": "10", "type_name": "萝莉少女"},
            {"type_id": "11", "type_name": "网曝黑料"},
            {"type_id": "12", "type_name": "变态调教"},
            {"type_id": "13", "type_name": "网红头条"},
            {"type_id": "14", "type_name": "极品媚黑"},
            {"type_id": "15", "type_name": "激情动漫"},
            {"type_id": "16", "type_name": "熟女人妻"},
            {"type_id": "20", "type_name": "强奸乱伦"},
            {"type_id": "21", "type_name": "淫妻作乐"},
            {"type_id": "22", "type_name": "足浴撩妹"},
            {"type_id": "23", "type_name": "反差母狗"},
            {"type_id": "24", "type_name": "多人群交"}
        ]
        return {
            "class": classes,
            "filters": {},
            "list": []
        }

    def homeVideoContent(self):
        return {"list": []}

    def categoryContent(self, tid, pg, filter, extend):
        page_num = int(pg) if str(pg).isdigit() else 1
        if page_num <= 1:
            url = "%s/index.php/vod/type/id/%s.html" % (self.site_url, tid)
        else:
            url = "%s/index.php/vod/type/id/%s/page/%d.html" % (self.site_url, tid, page_num)

        raw_html = self._fetch_url(url)
        unpacked_html = self._extract_unpacked_html(raw_html)
        videos, pagecount = self._parse_card_list(unpacked_html, page_num)

        return {
            "page": page_num,
            "pagecount": pagecount,
            "limit": len(videos),
            "total": pagecount * len(videos) if len(videos) > 0 else 0,
            "list": videos
        }

    def detailContent(self, ids):
        vod_id = ids[0] if ids else ""
        if not vod_id:
            return {"list": []}

        role_url = "%s/index.php/vod/role/id/%s.html" % (self.site_url, vod_id)
        role_raw = self._fetch_url(role_url)
        role_html = self._extract_unpacked_html(role_raw)

        title_match = re.search(r'<title>(.*?)</title>', role_html)
        vod_name = title_match.group(1).replace("-麻豆影院", "").strip() if title_match else ("视频_%s" % vod_id)

        pic_match = re.search(r'(?:data-src|data-original|src)=["\']([^"\']+\.(?:jpg|jpeg|png|webp)[^"\']*)["\']', role_html, re.IGNORECASE)
        vod_pic = pic_match.group(1) if pic_match else ""
        if vod_pic and not vod_pic.startswith("http"):
            vod_pic = urllib.parse.urljoin(self.site_url, vod_pic)

        desc_match = re.search(r'<meta\s+name=["\']description["\']\s+content=["\']([^"\']+)["\']', role_html)
        raw_desc = desc_match.group(1).strip() if desc_match else ""
        vod_content = "🦋 蝴蝶影视 独家呈现 | TG群: @tvshare23\n%s" % raw_desc

        real_play_url = "%s/index.php/vod/detail/id/%s.html" % (self.site_url, vod_id)

        return {
            "list": [
                {
                    "vod_id": vod_id,
                    "vod_name": vod_name,
                    "vod_pic": vod_pic,
                    "vod_type": "正片",
                    "vod_year": "2026",
                    "vod_area": "国产",
                    "vod_remarks": "蝴蝶影视",
                    "vod_actor": "🦋 TG群: @tvshare23",
                    "vod_director": "🦋 蝴蝶影视",
                    "vod_content": vod_content,
                    "vod_play_from": "蝴蝶秒播",
                    "vod_play_url": "正片$%s" % real_play_url
                }
            ]
        }

    def playerContent(self, flag, id, vipFlags):
        stream_url = ""
        if ".m3u8" in id or ".mp4" in id:
            stream_url = id
        else:
            raw_play_html = self._fetch_url(id)
            play_html = self._extract_unpacked_html(raw_play_html)

            bfz_match = re.search(r'id=["\']bfz["\'][^>]+data-user-name=["\']([^"\']+)["\']', play_html, re.IGNORECASE)
            if not bfz_match:
                bfz_match = re.search(r'data-user-name=["\']([^"\']+)["\'][^>]+id=["\']bfz["\']', play_html, re.IGNORECASE)

            if bfz_match:
                raw_bfz_str = bfz_match.group(1)
                url_match = re.search(r'(https?://[^\s"\'<>]+\.m3u8[^\s"\'<>]*)', raw_bfz_str, re.IGNORECASE)
                if not url_match:
                    url_match = re.search(r'(https?://[^\s"\'<>]+)', raw_bfz_str, re.IGNORECASE)
                if url_match:
                    stream_url = url_match.group(1)

            if not stream_url:
                m3u8_matches = re.findall(r'["\'](https?://[^"\']+\.m3u8[^"\']*)["\']', play_html, re.IGNORECASE)
                if m3u8_matches:
                    for u in m3u8_matches:
                        if "http" in u:
                            stream_url = u
                            break

            if not stream_url:
                any_stream = re.findall(r'https?://[a-zA-Z0-9_\-\./%]+\.m3u8', play_html, re.IGNORECASE)
                if any_stream:
                    stream_url = any_stream[0]

        return {
            "parse": 0,
            "playUrl": "",
            "url": stream_url if stream_url else id,
            "header": {
                "User-Agent": self.ua,
                "Referer": self.site_url
            }
        }

    def searchContent(self, key, quick, pg="1"):
        page_num = int(pg) if str(pg).isdigit() else 1
        search_url = "%s/index.php/vod/search.html" % self.site_url
        post_data = urllib.parse.urlencode({"wd": key}).encode("utf-8")

        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE

        req = urllib.request.Request(
            search_url,
            data=post_data,
            headers={
                "User-Agent": self.ua,
                "Content-Type": "application/x-www-form-urlencoded",
                "Referer": self.site_url,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
            }
        )
        try:
            with urllib.request.urlopen(req, timeout=10.0, context=ctx) as resp:
                raw_bytes = resp.read()
                raw_html = raw_bytes.decode("utf-8", errors="ignore")
        except Exception:
            raw_html = ""

        unpacked_html = self._extract_unpacked_html(raw_html)
        videos, pagecount = self._parse_card_list(unpacked_html, page_num)

        return {
            "page": page_num,
            "pagecount": pagecount,
            "limit": len(videos),
            "total": pagecount * len(videos) if len(videos) > 0 else 0,
            "list": videos
        }

    def action(self, action):
        return None

    def liveContent(self):
        return None

    def localProxy(self, params):
        return [200, "text/plain", b"OK"]

    def manualVideoCheck(self):
        return False

    def isVideoFormat(self, url):
        return False

    def destroy(self):
        pass

    def _fetch_url(self, url):
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": self.ua,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8"
            }
        )
        try:
            with urllib.request.urlopen(req, timeout=10.0, context=ctx) as resp:
                raw_bytes = resp.read()
                return raw_bytes.decode("utf-8", errors="ignore")
        except Exception:
            return ""

    def _extract_unpacked_html(self, raw_html):
        match = re.search(r'var\s+Words\s*=\s*["\']([^"\']+)["\']', raw_html)
        if match:
            return unescape_js(match.group(1))
        return raw_html

    def _parse_card_list(self, html_text, page_num):
        menu_end = html_text.find("</menu>")
        search_scope = html_text[menu_end + 7:] if menu_end != -1 else html_text

        videos = []
        card_matches = re.findall(
            r'(<a[^>]+href=["\']([^"\']*/vod/(?:detail|play|role)/id/(\d+)[^"\']*)["\'][^>]*>(.*?)</a>)',
            search_scope,
            re.IGNORECASE | re.DOTALL
        )

        seen_ids = set()
        for full_tag, href, vod_id, inner_html in card_matches:
            if vod_id in seen_ids:
                continue

            title_m = re.search(r'alt=["\']([^"\']+)["\']', inner_html)
            if not title_m:
                title_m = re.search(r'<cite[^>]*>(.*?)</cite>', inner_html, re.IGNORECASE | re.DOTALL)
            if not title_m:
                title_m = re.search(r'title=["\']([^"\']+)["\']', full_tag)

            raw_name = title_m.group(1) if title_m else ""
            clean_name = re.sub(r'<[^>]+>', '', raw_name).strip()
            if not clean_name:
                continue

            pic_m = re.search(r'(?:data-src|data-original|src)=["\']([^"\']+)["\']', inner_html)
            vod_pic = pic_m.group(1).strip() if pic_m else ""
            if vod_pic and not vod_pic.startswith("http"):
                vod_pic = urllib.parse.urljoin(self.site_url, vod_pic)

            seen_ids.add(vod_id)
            videos.append({
                "vod_id": vod_id,
                "vod_name": clean_name,
                "vod_pic": vod_pic,
                "vod_remarks": "蝴蝶影视",
                "style": {"type": "rect", "ratio": 1.78}
            })

        pagecount = page_num + 1 if len(videos) >= 10 else page_num
        max_page_match = re.search(r'page/(\d+)\.html["\'][^>]*>尾页</a>', search_scope)
        if max_page_match:
            pagecount = int(max_page_match.group(1))

        return videos, pagecount