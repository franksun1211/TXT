# coding=utf-8
try:
    from base.spider import Spider as BaseSpider
except Exception:
    class BaseSpider:
        def getName(self): return ""
        def init(self, extend=""): pass
        def homeContent(self, filter=None): return {}
        def homeVideoContent(self): return {}
        def categoryContent(self, tid, pg, filter=None, extend=None): return {}
        def detailContent(self, ids): return {}
        def searchContent(self, key, quick=False, pg="1"): return {}
        def playerContent(self, flag, id, vipFlags=None): return {}
        def localProxy(self, param): return [404, "text/plain", ""]
        def getDependence(self): return ""
        def isVideoFormat(self, url): return False
        def manualVideoCheck(self): return False

import re, time, json, urllib.request
from urllib.parse import quote, urljoin, urlparse

class Spider(BaseSpider):
    def getName(self):
        return "51Hub"

    def init(self, extend=""):
        self.host = "https://51hubvod.com"
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Referer": self.host + "/",
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Accept-Encoding": "identity",
        }

    def getDependence(self):
        return ""

    def _get(self, path, retry=2):
        url = path if path.startswith("http") else self.host + path
        for i in range(retry + 1):
            try:
                req = urllib.request.Request(url, headers=self.headers)
                with urllib.request.urlopen(req, timeout=15) as resp:
                    return resp.read().decode("utf-8", "ignore")
            except Exception:
                if i == retry:
                    return ""
                time.sleep(0.3)
        return ""

    def _unescape(self, s):
        try:
            import html as H
            return H.unescape(s or "")
        except Exception:
            return s or ""

    def homeContent(self, filter=None):
        classes = [
            {"type_id": "japanese-av", "type_name": "日本AV"},
            {"type_id": "domestic-media", "type_name": "国产传媒"},
            {"type_id": "chinese-subtitles", "type_name": "中文字幕"},
            {"type_id": "ribenwuma", "type_name": "无码"},
            {"type_id": "AI", "type_name": "AI"},
        ]
        return {"class": classes, "filters": {}}

    def homeVideoContent(self):
        return self.categoryContent("japanese-av", "1")

    def categoryContent(self, tid, pg, filter=None, extend=None):
        pg = str(pg or "1")
        tid = str(tid)
        if pg == "1":
            path = f"/avt/{tid}/"
        else:
            path = f"/avt/{tid}/page/{pg}/"
        html = self._get(path)
        if not html:
            return {"page": int(pg), "pagecount": 1, "limit": 0, "total": 0, "list": []}
        videos = self._parse_list(html)
        pagecount = int(pg)
        pages = re.findall(r'aria-label="Go to page (\d+)"', html)
        if pages:
            pagecount = max(int(x) for x in pages if str(x).isdigit())
        elif len(videos) >= 20:
            pagecount = int(pg) + 1
        return {
            "page": int(pg),
            "pagecount": pagecount,
            "limit": 24,
            "total": pagecount * 24,
            "list": videos,
        }

    def _parse_list(self, html):
        videos = []
        seen = set()
        # prefer aria-label on /av/ links
        for m in re.finditer(r'href="/av/([^"/]+)"[^>]*aria-label="([^"]+)"', html):
            slug, name = m.group(1), self._unescape(m.group(2)).strip()
            if slug in seen:
                continue
            seen.add(slug)
            videos.append({"vod_id": slug, "vod_name": name or slug, "vod_pic": "", "vod_remarks": ""})
        # fill covers
        covers = {}
        for m in re.finditer(r'href="/av/([^"/]+)"[\s\S]{0,600}?src="(https?://[^"]+)"', html):
            covers.setdefault(m.group(1), m.group(2))
        for m in re.finditer(r'src="(https?://[^"]+)"[^>]*alt="([^"]*)"[\s\S]{0,200}?href="/av/([^"/]+)"', html):
            covers.setdefault(m.group(3), m.group(1))
        # fallback: unique /av/ slugs + img alt nearby
        if not videos:
            for m in re.finditer(r'href="/av/([^"/]+)"', html):
                slug = m.group(1)
                if slug in seen:
                    continue
                seen.add(slug)
                chunk = html[max(0, m.start() - 100):m.start() + 500]
                name = ""
                am = re.search(r'alt="([^"]+)"', chunk)
                if am:
                    name = self._unescape(am.group(1)).strip()
                videos.append({"vod_id": slug, "vod_name": name or slug, "vod_pic": covers.get(slug, ""), "vod_remarks": ""})
        for v in videos:
            if not v["vod_pic"] and v["vod_id"] in covers:
                v["vod_pic"] = covers[v["vod_id"]]
        return videos

    def detailContent(self, ids):
        if not ids:
            return {"list": []}
        slug = str(ids[0] if isinstance(ids, (list, tuple)) else ids)
        html = self._get(f"/av/{quote(slug)}")
        if not html:
            return {"list": []}
        name = ""
        m = re.search(r"<h1[^>]*>([^<]+)</h1>", html)
        if m:
            name = self._unescape(m.group(1)).strip()
        if not name:
            m = re.search(r"<title>([^<]+)</title>", html)
            if m:
                name = self._unescape(m.group(1)).split("|")[0].strip()
        pic = ""
        m = re.search(r'src="(https?://[^"]+/upload/vod/[^"]+)"', html)
        if m:
            pic = m.group(1)
        return {
            "list": [{
                "vod_id": slug,
                "vod_name": name or slug,
                "vod_pic": pic,
                "vod_content": name,
                "vod_play_from": "线路1",
                "vod_play_url": f"正片${slug}",
            }]
        }

    def searchContent(self, key, quick=False, pg="1"):
        pg = str(pg or "1")
        q = quote(key)
        if pg == "1":
            path = f"/avsearch/?wd={q}"
        else:
            path = f"/avsearch/page/{pg}/?wd={q}"
        html = self._get(path)
        videos = self._parse_list(html or "")
        pages = re.findall(r'aria-label="Go to page (\d+)"', html or "")
        pagecount = max((int(x) for x in pages if str(x).isdigit()), default=1)
        return {
            "list": videos,
            "page": int(pg),
            "pagecount": pagecount,
            "limit": 24,
            "total": pagecount * 24,
        }

    def _extract_player_url(self, html):
        m = re.search(r"player_aaaa\s*=\s*(\{.+?\})\s*</script>", html or "", re.S)
        if m:
            try:
                return (json.loads(m.group(1)).get("url") or "").strip()
            except Exception:
                u = re.search(r'"url"\s*:\s*"([^"]+)"', m.group(1))
                if u:
                    return u.group(1).replace("\\/", "/")
        ms = re.findall(r'https?://[^\s"\']+\.m3u8[^\s"\']*', html or "")
        return ms[0] if ms else ""

    def _fetch_media(self, url):
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": self.headers["User-Agent"],
                "Referer": self.host + "/",
            })
            with urllib.request.urlopen(req, timeout=15) as resp:
                return resp.read().decode("utf-8", "ignore")
        except Exception:
            return ""

    def _resolve_m3u8(self, url):
        if not url or ".m3u8" not in url:
            return url
        body = self._fetch_media(url)
        if not body:
            return url
        if "#EXTINF" in body and "#EXT-X-STREAM-INF" not in body:
            return url
        for line in body.splitlines():
            line = line.strip()
            if line and not line.startswith("#") and ".m3u8" in line:
                return line if line.startswith("http") else urljoin(url, line)
        return url

    def playerContent(self, flag, id, vipFlags=None):
        raw = id or ""
        header = {
            "User-Agent": self.headers["User-Agent"],
            "Referer": self.host + "/",
        }
        if raw.startswith("http"):
            url = self._resolve_m3u8(raw)
        else:
            html = self._get(f"/av/{quote(raw)}")
            url = self._extract_player_url(html)
            if url:
                url = self._resolve_m3u8(url)
        if not url:
            return {"parse": 0, "jx": 0, "url": "", "header": header}
        if ".m3u8" in url and hasattr(self, "getProxyUrl") and self.getProxyUrl():
            base = self.getProxyUrl()
            proxy = base + ("&" if "?" in base else "?") + "url=" + quote(url, safe="") + "&type=m3u8"
            return {"parse": 0, "jx": 0, "url": proxy, "header": header}
        return {"parse": 0, "jx": 0, "url": url, "header": header}

    def localProxy(self, param):
        try:
            url = param.get("url") if isinstance(param, dict) else str(param or "")
            if not url:
                return [404, "text/plain", ""]
            body = self._fetch_media(url)
            if not body:
                return [502, "text/plain", "fetch failed"]
            if "#EXT-X-STREAM-INF" in body and "#EXTINF" not in body:
                for line in body.splitlines():
                    line = line.strip()
                    if line and not line.startswith("#") and ".m3u8" in line:
                        sub = line if line.startswith("http") else urljoin(url, line)
                        body = self._fetch_media(sub)
                        url = sub
                        break
            cleaned = self._clean_m3u8(body or "", url)
            return [200, "application/vnd.apple.mpegurl", cleaned]
        except Exception as e:
            return [500, "text/plain", str(e)[:200]]

    def _clean_m3u8(self, content, base_url=""):
        if not content or "#EXTM3U" not in content:
            return content
        parsed = urlparse(base_url)
        base_origin = f"{parsed.scheme}://{parsed.netloc}"
        base_dir = base_url.rsplit("/", 1)[0] + "/"
        out = []
        for line in content.splitlines():
            s = line.strip()
            if not s:
                continue
            if s.startswith("#EXT-X-KEY"):
                mk = re.search(r'URI="([^"]+)"', s)
                if mk:
                    key_uri = mk.group(1)
                    if key_uri.startswith("/"):
                        s = s.replace(f'URI="{key_uri}"', f'URI="{base_origin + key_uri}"')
                    elif not key_uri.startswith("http"):
                        s = s.replace(f'URI="{key_uri}"', f'URI="{urljoin(base_dir, key_uri)}"')
                out.append(s)
            elif s.startswith("#"):
                out.append(s)
            else:
                if s.startswith("http"):
                    out.append(s)
                elif s.startswith("/"):
                    out.append(base_origin + s)
                else:
                    out.append(urljoin(base_dir, s))
        return "\n".join(out)

    def isVideoFormat(self, url):
        return bool(re.search(r"\.(m3u8|mp4|flv|mkv)(\?|$)", url or "", re.I))

    def manualVideoCheck(self):
        return False
