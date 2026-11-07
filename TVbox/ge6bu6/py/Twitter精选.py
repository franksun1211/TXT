# -*- coding: utf-8 -*-
import base64
import html
import http.client
import json
import re
import ssl
import time
import urllib.parse
import urllib.request
from urllib.parse import unquote, urljoin, urlparse

try:
    from base.spider import Spider as BaseSpider
except Exception:
    class BaseSpider(object):
        pass

HOST = "https://pektino.com"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

CATEGORIES = [
    {"type_id": "daily", "type_name": "每日精选"},
    {"type_id": "weekly", "type_name": "每周热门"},
    {"type_id": "monthly", "type_name": "每月排行"},
    {"type_id": "all", "type_name": "历史最佳"},
]


class Spider(BaseSpider):

    def getName(self):
        return "pektino"

    def init(self, extend=""):
        pass

    def _req(self, url, retry=3, timeout=12, referer=None):
        headers = {
            "User-Agent": UA,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en-US;q=0.8,en;q=0.7",
            "Referer": referer if referer else (HOST + "/zh-CN"),
        }
        for i in range(retry):
            try:
                ctx = ssl.create_default_context()
                ctx.check_hostname = False
                ctx.verify_mode = ssl.CERT_NONE

                req_obj = urllib.request.Request(url, headers=headers)
                with urllib.request.urlopen(req_obj, timeout=timeout, context=ctx) as resp:
                    data = resp.read()
                    real_url = resp.geturl()
                    for enc in ["utf-8", "gbk", "gb2312", "latin-1"]:
                        try:
                            return resp.getcode(), real_url, data.decode(enc)
                        except Exception:
                            continue
                    return resp.getcode(), real_url, data.decode("utf-8", errors="ignore")
            except urllib.error.HTTPError as he:
                return he.code, url, ""
            except Exception:
                if i < retry - 1:
                    time.sleep(0.3)
                else:
                    return 0, url, ""
        return 0, url, ""

    def _translate_auto(self, text):
        if not text:
            return ""

        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE

        try:
            yd_url = "https://aidemo.youdao.com/trans"
            data_bytes = urllib.parse.urlencode({
                "q": text,
                "from": "Auto",
                "to": "zh-CHS"
            }).encode("utf-8")
            req_obj = urllib.request.Request(yd_url, data=data_bytes, headers={
                "User-Agent": UA,
                "Content-Type": "application/x-www-form-urlencoded"
            })
            with urllib.request.urlopen(req_obj, timeout=2.5, context=ctx) as resp:
                res_data = json.loads(resp.read().decode("utf-8"))
                trans_result = res_data.get("translation")
                if trans_result and isinstance(trans_result, list) and len(trans_result) > 0:
                    return trans_result[0].strip()
        except Exception:
            pass

        try:
            q = urllib.parse.quote(text)
            gg_url = "https://translate.googleapis.com/translate_a/single?client=gtx&sl=auto&tl=zh-CN&dt=t&q=%s" % q
            req_obj2 = urllib.request.Request(gg_url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req_obj2, timeout=2.0, context=ctx) as resp2:
                data = json.loads(resp2.read().decode("utf-8"))
                translated = "".join([part[0] for part in data[0] if part[0]])
                if translated.strip():
                    return translated.strip()
        except Exception:
            pass

        return text

    def _convert_pic_proxy(self, pic_url):
        if not pic_url:
            return ""
        if pic_url.startswith("//"):
            pic_url = "https:" + pic_url
        if "twimg.com" in pic_url:
            return "https://wsrv.nl/?url=%s" % urllib.parse.quote(pic_url)
        return pic_url

    def _parse_cards(self, html_text):
        items = []
        if not html_text:
            return items

        anchor_pattern = r'(<a[^>]+href="[^"]*(?:/zh-CN)?/movie/[^"]+"[^>]*>.*?</a>)'
        matches = re.findall(anchor_pattern, html_text, re.DOTALL | re.IGNORECASE)

        seen_ids = set()
        for block in matches:
            href_m = re.search(r'href="([^"]*(?:/zh-CN)?/movie/([^"/]+)/?)"', block)
            if not href_m:
                continue

            v_url = href_m.group(1)
            vid = href_m.group(2)
            if vid in seen_ids:
                continue
            seen_ids.add(vid)

            img_m = re.search(r'(?:src|data-src|poster)="([^"]+)"', block, re.IGNORECASE)
            pic = self._convert_pic_proxy(img_m.group(1)) if img_m else ""

            dur_m = re.search(r'(\d+:\d+(?::\d+)?)', block)
            dur = dur_m.group(1) if dur_m else ""

            alt_m = re.search(r'alt="([^"]+)"', block)
            if alt_m:
                raw_title = alt_m.group(1).replace("X (Twitter) adult video ", "").strip()
            else:
                raw_title = vid

            title = "精选影片 " + vid if raw_title == vid else raw_title
            remarks = ("蝴蝶影视 | %s" % dur) if dur else "蝴蝶影视"

            items.append({
                "vod_id": vid,
                "vod_name": title,
                "vod_pic": pic,
                "vod_remarks": remarks,
                "vod_pic_style": "1.78",
                "vod_card_style": "rect",
                "style": {"type": "rect", "ratio": 1.78}
            })

        return items

    def homeContent(self, filter):
        return {
            "class": CATEGORIES,
            "list": [],
            "class_flag": 0,
            "style": {"type": "rect", "ratio": 1.78}
        }

    def homeVideoContent(self):
        code, real_url, html_text = self._req("%s/zh-CN" % HOST)
        return {
            "list": self._parse_cards(html_text),
            "style": {"type": "rect", "ratio": 1.78}
        }

    def categoryContent(self, tid, pg="1", filter=False, extend=None):
        p = int(pg) if str(pg).isdigit() else 1
        clean_tid = tid.strip("/")

        if clean_tid == "daily":
            target_url = "%s/zh-CN" % HOST
        else:
            target_url = "%s/zh-CN/%s" % (HOST, clean_tid)

        if p > 1:
            target_url = "%s?page=%s" % (target_url, p)

        code, real_url, html_text = self._req(target_url)
        items = self._parse_cards(html_text)

        return {
            "list": items,
            "page": p,
            "pagecount": 999,
            "limit": 50,
            "total": 9999,
            "class_flag": 0,
            "style": {"type": "rect", "ratio": 1.78}
        }

    def detailContent(self, ids):
        if not ids:
            return {"list": []}

        vid = str(ids[0]).strip()
        target_url = ("%s/zh-CN/movie/%s" % (HOST, vid)) if not vid.startswith("http") else vid

        code, real_url, html_text = self._req(target_url)

        title = ""
        t_m = re.search(r'<title>([^<]+)</title>', html_text)
        if t_m:
            title = t_m.group(1).split(" - ")[0].strip()

        pic = ""
        pic_m = re.search(r'property="og:image"\s+content="([^"]+)"', html_text)
        if pic_m:
            pic = self._convert_pic_proxy(pic_m.group(1))

        desc = ""
        desc_m = re.search(r'name="description"\s+content="([^"]+)"', html_text)
        if desc_m:
            desc = desc_m.group(1).strip()

        clean_text = html_text.replace(r'\/', '/').replace('\\"', '"').replace('\\\\', '\\')
        raw_mp4s = re.findall(r'(https?://video\.twimg\.com/[^"\'\s<>\\]+\.mp4(?:\?[^"\'\s<>\\]*)?)', clean_text)
        
        valid_streams = []
        for u in raw_mp4s:
            clean_u = u.replace('\\', '').strip()
            if clean_u.startswith("http") and clean_u not in valid_streams:
                valid_streams.append(clean_u)

        video_stream_url = ""
        for u in valid_streams:
            if vid in u:
                video_stream_url = u
                break
        if not video_stream_url and valid_streams:
            video_stream_url = valid_streams[0]

        qualities = []
        if video_stream_url:
            qualities.append(("超清直链", video_stream_url))
        else:
            qualities.append(("网页解析", target_url))

        play_url = "#".join(["%s$%s" % (q[0], q[1]) for q in qualities])
        zh_title = self._translate_auto(title)
        display_title = zh_title if zh_title else title
        notice_desc = "🦋 蝴蝶影视 独家呈现\n官方交流群: @tvshare23\n原名: %s\n%s" % (title, desc)

        vod = {
            "vod_id": vid,
            "vod_name": html.unescape(display_title),
            "vod_pic": pic,
            "vod_year": "",
            "vod_area": "",
            "vod_actor": "🦋 TG群: @tvshare23",
            "vod_director": "🦋 蝴蝶影视",
            "vod_content": notice_desc,
            "vod_remarks": "蝴蝶影视",
            "vod_play_from": "蝴蝶影视",
            "vod_play_url": play_url,
        }
        return {"list": [vod]}

    def playerContent(self, flag, id, vipFlags):
        headers = {
            "User-Agent": UA,
            "Referer": "https://twitter.com/"
        }
        return {
            "parse": 0,
            "playUrl": "",
            "url": id,
            "header": json.dumps(headers)
        }

    def searchContent(self, key, quick=False, pg="1"):
        p = int(pg) if str(pg).isdigit() else 1
        kw = urllib.parse.quote(key)
        url = "%s/zh-CN/search?tag=%s" % (HOST, kw)
        if p > 1:
            url = "%s&page=%s" % (url, p)

        code, real_url, html_text = self._req(url)
        return {
            "list": self._parse_cards(html_text),
            "page": p,
            "pagecount": 999,
            "limit": 24,
            "total": 9999,
            "class_flag": 0,
            "style": {"type": "rect", "ratio": 1.78}
        }

    def localProxy(self, params):
        return [200, "text/plain; charset=utf-8", b"", None]

    def action(self, action):
        return {}

    def liveContent(self):
        return {}

    def manualVideoCheck(self):
        return False

    def isVideoFormat(self, url):
        return False

    def destroy(self):
        pass