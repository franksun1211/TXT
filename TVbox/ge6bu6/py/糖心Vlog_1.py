# -*- coding: utf-8 -*-

import sys
import re
import json
import base64
from html import unescape
from urllib.parse import quote, urljoin

try:
    import requests
except ImportError:
    requests = None

sys.path.append('..')
from base.spider import Spider


class Spider(Spider):
    name = "糖心Vlog"
    host = "https://tangxinvlog.pro"
    timeout = 25
    headers = {
        "User-Agent": "Mozilla/5.0 (Linux; Android 11) AppleWebKit/537.36 Chrome/120.0 Mobile Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,*/*;q=0.9",
        "Accept-Language": "zh-CN,zh;q=0.9",
    }

    def __init__(self):
        try:
            super().__init__()
        except Exception:
            pass
        self.session = requests.Session() if requests else None
        if self.session:
            self.session.headers.update(self.headers)

    def init(self, extend=""):
        cfg = extend if isinstance(extend, dict) else {}
        if isinstance(extend, str) and extend.strip().startswith("{"):
            try:
                cfg = json.loads(extend)
            except Exception:
                cfg = {}
        host = str(cfg.get("host") or cfg.get("siteUrl") or "").strip().rstrip("/")
        if host.startswith(("http://", "https://")):
            self.host = host
        return None

    def getName(self):
        return self.name

    def destroy(self):
        if self.session:
            try:
                self.session.close()
            except Exception:
                pass

    def isVideoFormat(self, url):
        return bool(re.search(r"\.(?:m3u8|mp4)(?:[?#]|$)", str(url or ""), re.I))

    def manualVideoCheck(self):
        return False

    def _get(self, value, referer=None):
        if not self.session:
            return None
        url = value if str(value).startswith("http") else urljoin(self.host + "/", str(value).lstrip("/"))
        headers = dict(self.headers)
        headers["Referer"] = referer or self.host + "/"
        for _ in range(2):
            try:
                r = self.session.get(url, headers=headers, timeout=self.timeout, verify=False)
                if r.status_code == 200:
                    r.encoding = "utf-8"
                    return r
            except Exception:
                continue
        return None

    @staticmethod
    def _clean(value):
        return re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", str(value or "")))).strip()

    def _proxy_pic(self, url):
        if not url or not url.startswith("http"):
            return url
        try:
            encoded = base64.urlsafe_b64encode(url.encode("utf-8")).decode("ascii")
            return self.getProxyUrl() + "&url=" + encoded + "&type=img"
        except Exception:
            return url

    def _parse_video_cards(self, text, base=None):
        """提取视频卡片"""
        result, seen = [], set()
        pattern = r'<a[^>]+class=["\'][^"\']*video-card[^"\']*["\'][^>]*href=["\']([^"\']*/videos/[^"\']+)["\'][^>]*>([\s\S]*?)</a>'
        for href, block in re.findall(pattern, text or "", re.I):
            vid = href.rstrip("/").rsplit("/", 1)[-1]
            if not vid or vid in seen:
                continue
            tm = re.search(r'<img[^>]+alt=["\']([^"\']*)', block, re.I)
            title = self._clean(tm.group(1)) if tm else ""
            im = re.search(r'<img[^>]+src=["\']([^"\']+)', block, re.I)
            pic = urljoin(base or self.host, im.group(1)) if im else ""
            if pic:
                pic = self._proxy_pic(pic)
            if title:
                seen.add(vid)
                result.append({
                    "vod_id": urljoin(base or self.host, href),
                    "vod_name": title,
                    "vod_pic": pic,
                    "vod_remarks": ""
                })
        return result

    def _parse_artist_cards(self, text, base=None):
        """提取博主卡片"""
        items = []
        pattern = r'<a[^>]+class=["\'][^"\']*artist-card[^"\']*["\'][^>]*href=["\']([^"\']*/artists/[^"\']+)["\'][^>]*>[\s\S]*?<div[^>]+class=["\']name["\'][^>]*>(.*?)</div>'
        for href, name in re.findall(pattern, text or "", re.I):
            items.append({
                "vod_id": urljoin(base or self.host, href),
                "vod_name": self._clean(name),
                "vod_pic": "",
                "vod_remarks": "博主"
            })
        return items

    def _filters(self):
        return {"videos": [{"key": "sort", "name": "排序", "value": [{"n": "最新", "v": ""}]}]}

    def homeContent(self, filter=False):
        return {
            "class": [
                {"type_id": "videos", "type_name": "视频"},
                {"type_id": "artists", "type_name": "博主"}
            ],
            "filters": self._filters() if filter else {}
        }

    def homeVideoContent(self):
        r = self._get("/")
        return {"list": self._parse_video_cards(r.text if r else "", r.url if r else self.host)}

    def categoryContent(self, tid, pg, filter=False, extend=None):
        page = max(1, int(pg or 1))
        if tid == "artists":
            path = "/artists/" if page == 1 else "/artists/%d/" % page
            r = self._get(path)
            items = self._parse_artist_cards(r.text if r else "", r.url if r else self.host)
        else:
            path = "/videos/" if page == 1 else "/videos/%d/" % page
            r = self._get(path)
            items = self._parse_video_cards(r.text if r else "", r.url if r else self.host)
        return {
            "list": items,
            "page": page,
            "pagecount": page + 1 if items else page,
            "limit": len(items) or 24,
            "total": 0
        }

    def detailContent(self, ids):
        """详情页 - 支持视频和博主"""
        value = str(ids[0] if isinstance(ids, (list, tuple)) and ids else ids or "").strip()
        r = self._get(value)
        if not r or not r.text:
            return {"list": []}
        text = r.text

        # ========== 博主详情 ==========
        if "/artists/" in value:
            # 提取博主头像
            pic = ""
            pm = re.search(r'<img[^>]+src=["\']([^"\']*/avatars/[^"\']+)', text, re.I)
            if pm:
                pic = urljoin(r.url, pm.group(1))
                pic = self._proxy_pic(pic)
            # 提取博主名称
            name = ""
            nm = re.search(r'<h1[^>]*>(.*?)</h1>', text, re.I)
            if nm:
                name = self._clean(nm.group(1))
            if not name:
                nm = re.search(r'<div[^>]+class=["\']name["\'][^>]*>(.*?)</div>', text, re.I)
                if nm:
                    name = self._clean(nm.group(1))

            # ===== 核心修复：提取博主的所有视频，拼接到 vod_play_url =====
            episodes = []
            pattern = r'<a[^>]+class=["\'][^"\']*video-card[^"\']*["\'][^>]*href=["\']([^"\']*/videos/[^"\']+)["\'][^>]*>([\s\S]*?)</a>'
            for href, block in re.findall(pattern, text, re.I):
                # 提取标题
                tm = re.search(r'<img[^>]+alt=["\']([^"\']*)', block, re.I)
                title = self._clean(tm.group(1)) if tm else ""
                if not title:
                    continue
                full_url = urljoin(r.url, href)
                # 提取封面（可选）
                im = re.search(r'<img[^>]+src=["\']([^"\']+)', block, re.I)
                vid_pic = urljoin(r.url, im.group(1)) if im else ""
                episodes.append({
                    "name": title,
                    "url": full_url,
                    "pic": vid_pic
                })

            # 如果博主有视频，构建播放列表
            play_url = ""
            if episodes:
                # 格式：第1集$url#第2集$url
                play_url = "#".join([f"{ep['name']}${ep['url']}" for ep in episodes])

            # 如果没有视频，给一个提示
            if not play_url:
                play_url = "该博主暂无视频$" + value

            return {
                "list": [{
                    "vod_id": value,
                    "vod_name": name or "博主",
                    "vod_pic": pic,
                    "vod_content": "",
                    "vod_play_from": "博主视频",
                    "vod_play_url": play_url,
                }]
            }

        # ========== 视频详情 ==========
        # 提取 JSON-LD
        ld = {}
        for block in re.findall(r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>([\s\S]*?)</script>', text, re.I):
            try:
                obj = json.loads(block.strip())
                if isinstance(obj, dict) and obj.get("@type") == "VideoObject":
                    ld = obj
                    break
            except Exception:
                pass

        # 标题
        title = ""
        hm = re.search(r'<h1[^>]*>(.*?)</h1>', text, re.I | re.S)
        if hm:
            title = self._clean(hm.group(1))
        if not title:
            title = self._clean(ld.get("name") or "")

        # 描述
        desc = ""
        dm = re.search(r'<meta[^>]+name=["\']description["\'][^>]+content=["\']([^"\']*)', text, re.I)
        if dm:
            desc = self._clean(dm.group(1))
        if not desc:
            desc = self._clean(ld.get("description") or "")

        # 封面
        pic = ld.get("thumbnailUrl") or ld.get("image") or ""
        if not pic:
            pm = re.search(r'<video[^>]+poster=["\']([^"\']+)', text, re.I)
            if pm:
                pic = urljoin(r.url, pm.group(1))
        if pic:
            pic = self._proxy_pic(pic)

        # 播放地址
        play = ld.get("contentUrl") or ""
        if not play:
            vm = re.search(r'<video[^>]+data-src=["\']([^"\']+)', text, re.I)
            if vm:
                play = vm.group(1)
        if not play:
            vm = re.search(r'<source[^>]+src=["\']([^"\']+)', text, re.I)
            if vm:
                play = vm.group(1)
        if play and not play.startswith("http"):
            play = urljoin(r.url, play)

        return {
            "list": [{
                "vod_id": value,
                "vod_name": title or "未知视频",
                "vod_pic": pic,
                "vod_content": desc,
                "vod_play_from": "糖心直链",
                "vod_play_url": "正片$" + play if play else "",
            }]
        }

    def searchContent(self, key, quick=False, pg="1"):
        page = max(1, int(pg or 1))
        enc_key = quote(str(key or ""))
        paths = [
            f"/search/?q={enc_key}",
            f"/search?q={enc_key}"
        ]
        if page > 1:
            paths = [p + ("&page=" if "?" in p else "?page=") + str(page) for p in paths]
        for path in paths:
            r = self._get(path)
            if r and r.text:
                items = self._parse_video_cards(r.text, r.url)
                if items:
                    return {
                        "list": items,
                        "page": page,
                        "pagecount": page + 1,
                        "limit": len(items),
                        "total": 0
                    }
        return {"list": [], "page": page, "pagecount": page, "limit": 24, "total": 0}

    def playerContent(self, flag, id, vipFlags=None):
        value = str(id or "").strip()
        if self.isVideoFormat(value):
            return {
                "parse": 0,
                "url": value,
                "header": {
                    "User-Agent": self.headers["User-Agent"],
                    "Referer": self.host + "/"
                }
            }
        # 如果不是直链，尝试 WebView
        return {
            "parse": 1,
            "url": value,
            "header": {
                "User-Agent": self.headers["User-Agent"],
                "Referer": self.host + "/"
            }
        }

    def localProxy(self, params):
        try:
            if not isinstance(params, dict):
                return None
            url = str(params.get("url") or "")
            if not url:
                return None
            try:
                url = base64.urlsafe_b64decode(url + "=" * ((4 - len(url) % 4) % 4)).decode("utf-8")
            except Exception:
                pass
            if not url.startswith("http"):
                return None
            r = self.session.get(
                url,
                headers={"User-Agent": self.headers["User-Agent"], "Referer": self.host + "/"},
                timeout=self.timeout,
                verify=False
            )
            if r.status_code != 200:
                return [r.status_code, "text/plain", b""]
            return [200, r.headers.get("Content-Type", "image/jpeg"), r.content]
        except Exception:
            return None