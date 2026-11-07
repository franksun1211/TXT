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

import re, time, json, http.cookiejar, urllib.request
from urllib.parse import quote, unquote, urljoin, urlparse

# 新铁律：教师≠未成年；仅跳过明确未成年向分类/标题
SKIP_TYPE_IDS = set()  # 无整类因「学生/教师」跳过；若日后出现萝莉专类再加
SKIP_NAME_KEYS = ("萝莉", "幼女", "小学生", "儿童", "未成年", "幼幼", "初中生", "小孩")

class Spider(BaseSpider):
    def getName(self):
        return "草榴社区"

    def init(self, extend=""):
        self.host = "https://q9r0s1t2.caoliu40.cc"
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Referer": self.host + "/",
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Accept-Encoding": "identity",
        }
        self._cj = http.cookiejar.CookieJar()
        self._opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self._cj))

    def getDependence(self):
        return ""

    def _req(self, path, method="GET", data=None, headers=None, retry=2):
        import gzip, zlib
        url = path if path.startswith("http") else self.host + path
        hdrs = dict(self.headers)
        hdrs["Accept-Encoding"] = "gzip, deflate"
        if headers:
            hdrs.update(headers)
        body = None
        if data is not None:
            body = data if isinstance(data, (bytes, bytearray)) else (
                data.encode() if isinstance(data, str) else json.dumps(data).encode()
            )
        for _ in range(retry + 1):
            try:
                req = urllib.request.Request(url, data=body, headers=hdrs, method=method)
                with self._opener.open(req, timeout=18) as resp:
                    raw = resp.read()
                    enc = (resp.headers.get("Content-Encoding") or "").lower()
                    if enc == "gzip" or (len(raw) >= 2 and raw[0] == 0x1F and raw[1] == 0x8B):
                        try:
                            raw = gzip.decompress(raw)
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
                    return raw.decode("utf-8", "ignore")
            except Exception:
                time.sleep(0.25)
        return ""

    def _get(self, path):
        return self._req(path)

    def _unescape(self, s):
        try:
            import html as H
            return H.unescape(s or "")
        except Exception:
            return s or ""

    def _bad_name(self, name):
        return any(k in (name or "") for k in SKIP_NAME_KEYS)

    def homeContent(self, filter=None):
        classes = [
            {"type_id": "1", "type_name": "国产传媒"},
            {"type_id": "2", "type_name": "国产剧情"},
            {"type_id": "58", "type_name": "网曝黑料"},
            {"type_id": "3", "type_name": "特色仓库"},
            {"type_id": "69", "type_name": "精品资源"},
            {"type_id": "78", "type_name": "热播片库"},
            {"type_id": "92", "type_name": "原创资源"},
            {"type_id": "6", "type_name": "偷拍自拍"},
            {"type_id": "7", "type_name": "国产大制作"},
            {"type_id": "8", "type_name": "乱伦毁三观"},
            {"type_id": "9", "type_name": "主播女网红"},
            {"type_id": "10", "type_name": "黑料网曝"},
            {"type_id": "11", "type_name": "会所技师"},
            {"type_id": "12", "type_name": "制服诱惑"},
            {"type_id": "20", "type_name": "淫乱学生妹"},
            {"type_id": "82", "type_name": "教师学生"},
            {"type_id": "57", "type_name": "高清无码"},
            {"type_id": "21", "type_name": "高清有码"},
            {"type_id": "22", "type_name": "中文字幕"},
            {"type_id": "24", "type_name": "日本素人"},
            {"type_id": "25", "type_name": "无码流出"},
            {"type_id": "26", "type_name": "FC2"},
            {"type_id": "30", "type_name": "东京热"},
            {"type_id": "31", "type_name": "一本道"},
            {"type_id": "60", "type_name": "国产推荐"},
            {"type_id": "61", "type_name": "探花约炮"},
            {"type_id": "62", "type_name": "淫妻绿帽"},
            {"type_id": "63", "type_name": "国产直播"},
            {"type_id": "64", "type_name": "重口猎奇"},
            {"type_id": "65", "type_name": "韩国直播"},
            {"type_id": "66", "type_name": "欧美精品"},
            {"type_id": "67", "type_name": "动漫精选"},
            {"type_id": "37", "type_name": "强奸乱伦"},
            {"type_id": "71", "type_name": "精品探花"},
            {"type_id": "32", "type_name": "AV明星"},
            {"type_id": "33", "type_name": "SM重味"},
            {"type_id": "28", "type_name": "颜射系列"},
            {"type_id": "29", "type_name": "口交视频"},
            {"type_id": "35", "type_name": "AV解说"},
            {"type_id": "70", "type_name": "日韩无码"},
            {"type_id": "72", "type_name": "动漫精品"},
            {"type_id": "73", "type_name": "日韩精品"},
            {"type_id": "74", "type_name": "欧美精品2"},
            {"type_id": "75", "type_name": "中文字幕2"},
            {"type_id": "77", "type_name": "国产精品"},
            {"type_id": "79", "type_name": "伦理影片"},
            {"type_id": "80", "type_name": "自拍偷拍"},
            {"type_id": "81", "type_name": "自慰系列"},
            {"type_id": "83", "type_name": "大秀视频"},
            {"type_id": "86", "type_name": "颜值正义"},
            {"type_id": "87", "type_name": "熟女少妇"},
            {"type_id": "90", "type_name": "人妻系列"},
            {"type_id": "91", "type_name": "制服诱惑2"},
            {"type_id": "34", "type_name": "巨乳系列"},
            {"type_id": "76", "type_name": "精品网红"},
            {"type_id": "84", "type_name": "明星换脸"},
            {"type_id": "85", "type_name": "反差母狗"},
            {"type_id": "88", "type_name": "人兽乱交"},
            {"type_id": "89", "type_name": "国产传媒2"},
        ]
        return {"class": classes, "filters": {}}

    def homeVideoContent(self):
        return self.categoryContent("1", "1")

    def categoryContent(self, tid, pg, filter=None, extend=None):
        pg = str(pg or "1")
        tid = str(tid)
        if tid in SKIP_TYPE_IDS:
            return {"page": int(pg), "pagecount": 1, "limit": 0, "total": 0, "list": []}
        if pg == "1":
            path = f"/vodtype/{tid}.html"
        else:
            path = f"/vodtype/{tid}-{pg}.html"
        html = self._get(path)
        videos = self._parse_list(html or "")
        pages = re.findall(rf"/vodtype/{re.escape(tid)}-(\d+)\.html", html or "")
        nums = [int(x) for x in pages if str(x).isdigit() and int(x) < 5000]
        pagecount = max(nums) if nums else (int(pg) + 1 if len(videos) >= 20 else int(pg))
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
        for m in re.finditer(r'href="(/voddetail/(\d+)\.html)"', html):
            vid = m.group(2)
            if vid in seen:
                continue
            seen.add(vid)
            before = html[max(0, m.start() - 250):m.start()]
            after = html[m.end():m.end() + 900]
            chunk = before + after
            name = ""
            # 优先取详情链接后的 <h3> 标题（列表项），避免命中分类名
            tm = re.search(r'<h3>([^<]+)</h3>', after)
            if tm:
                name = self._unescape(tm.group(1)).strip()
            if not name:
                for pat in [r'alt="([^"]+)"', r'title="([^"]+)"']:
                    tm = re.search(pat, chunk)
                    if tm and len(self._unescape(tm.group(1)).strip()) > 2:
                        name = self._unescape(tm.group(1)).strip()
                        break
            if self._bad_name(name):
                continue
            pic = ""
            pm = re.search(r'data-original="(https?://[^"]+)"', chunk)
            if pm:
                pic = pm.group(1)
            if not pic:
                for pm in re.finditer(r'(?:data-src|src)="(https?://[^"]+)"', chunk):
                    u = pm.group(1)
                    if ("/upload/" in u or any(u.lower().endswith(ext) for ext in (".jpg", ".jpeg", ".png", ".webp"))) and "gif" not in u.lower() and "load.png" not in u:
                        pic = u
                        break
            videos.append({
                "vod_id": vid,
                "vod_name": name or vid,
                "vod_pic": pic,
                "vod_remarks": "",
            })
        return videos

    def detailContent(self, ids):
        if not ids:
            return {"list": []}
        vid = str(ids[0] if isinstance(ids, (list, tuple)) else ids)
        html = self._get(f"/voddetail/{vid}.html")
        if not html:
            return {"list": []}
        name = ""
        for m in re.finditer(r"<h1[^>]*>([^<]+)</h1>", html):
            t = self._unescape(m.group(1)).strip()
            if t and t not in ("草榴社区",):
                name = t
                break
        if not name:
            m = re.search(r"<title>([^<]+)</title>", html)
            if m:
                name = self._unescape(m.group(1)).split("-")[0].strip()
        if self._bad_name(name):
            return {"list": []}
        pic = ""
        m = re.search(r'(?:data-original|data-src|src)="(https?://[^"]+/upload/[^"]+\.(?:jpg|jpeg|png|webp)[^"]*)"', html, re.I)
        if m:
            pic = m.group(1)
        plays = list(dict.fromkeys(re.findall(
            rf'/vodplay/{re.escape(vid)}-(\d+)-(\d+)\.html', html
        )))
        if not plays:
            plays = [("1", "1")]
        parts = []
        for i, (sid, nid) in enumerate(plays, 1):
            label = f"第{i}集" if len(plays) > 1 else "正片"
            parts.append(f"{label}${vid}-{sid}-{nid}")
        return {
            "list": [{
                "vod_id": vid,
                "vod_name": name or vid,
                "vod_pic": pic,
                "vod_content": name,
                "vod_play_from": "线路1",
                "vod_play_url": "#".join(parts),
            }]
        }

    def searchContent(self, key, quick=False, pg="1"):
        pg = str(pg or "1")
        q = quote(key)
        if pg == "1":
            path = f"/vodsearch/-------------.html?wd={q}"
        else:
            path = f"/vodsearch/{q}----------{pg}---.html"
        html = self._get(path)
        if not html or "404" in (re.findall(r"<title>([^<]+)", html) or [""])[0]:
            html = self._get(f"/index.php/vod/search.html?wd={q}&page={pg}")
        videos = self._parse_list(html or "")
        pages = re.findall(r"/page/(\d+)|----------(\d+)---", html or "")
        nums = []
        for a, b in pages:
            for x in (a, b):
                if x and x.isdigit() and int(x) < 500:
                    nums.append(int(x))
        pagecount = max(nums) if nums else 1
        return {
            "list": videos,
            "page": int(pg),
            "pagecount": pagecount,
            "limit": 24,
            "total": pagecount * 24,
        }

    def _decode_player_url(self, url_field, encrypt):
        u = (url_field or "").strip()
        if not u:
            return ""
        try:
            enc = int(encrypt or 0)
        except Exception:
            enc = 0
        if enc == 1:
            u = unquote(u)
        elif enc == 2:
            try:
                import base64
                u = base64.b64decode(u).decode("utf-8", "ignore")
            except Exception:
                pass
        return u

    def playerContent(self, flag, id, vipFlags=None):
        raw = (id or "").strip()
        header = {
            "User-Agent": self.headers["User-Agent"],
            "Referer": self.host + "/",
        }
        if raw.startswith("http"):
            return {"parse": 0, "jx": 0, "url": raw, "header": header}
        play_key = raw
        if play_key.isdigit():
            play_key = f"{play_key}-1-1"
        path = f"/vodplay/{play_key}.html"
        header["Referer"] = self.host + path
        html = self._get(path)
        play_url = ""
        m = re.search(r"player_aaaa\s*=\s*(\{.+?\})\s*</script>", html or "", re.S)
        if m:
            try:
                d = json.loads(m.group(1))
                play_url = self._decode_player_url(d.get("url"), d.get("encrypt"))
            except Exception:
                pass
        if not play_url:
            ms = re.findall(r'https?://[^\s"\']+\.m3u8[^\s"\']*', html or "")
            play_url = ms[0] if ms else ""
        if not play_url:
            return {"parse": 0, "jx": 0, "url": "", "header": header}
        if hasattr(self, "getProxyUrl") and self.getProxyUrl() and "m3u8" in play_url:
            base = self.getProxyUrl()
            proxy = base + ("&" if "?" in base else "?") + "url=" + quote(play_url, safe="") + "&type=m3u8"
            return {"parse": 0, "jx": 0, "url": proxy, "header": header}
        return {"parse": 0, "jx": 0, "url": play_url, "header": header}

    def localProxy(self, param):
        try:
            url = param.get("url") if isinstance(param, dict) else str(param or "")
            if not url:
                return [404, "text/plain", ""]
            text = self._req(url, headers={"Referer": self.host + "/", "Accept": "*/*"})
            if not text:
                return [502, "text/plain", "fetch failed"]
            if "#EXT-X-STREAM-INF" in text and "#EXTINF" not in text:
                for line in text.splitlines():
                    line = line.strip()
                    if line and not line.startswith("#") and (".m3u8" in line or line.startswith("/")):
                        sub = line if line.startswith("http") else urljoin(url, line)
                        text = self._req(sub, headers={"Referer": self.host + "/"})
                        url = sub
                        break
            cleaned = self._clean_m3u8(text or "", url)
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
            if s.startswith("#EXT-X-KEY") or s.startswith("#EXT-X-MAP"):
                mk = re.search(r'URI="([^"]+)"', s)
                if mk:
                    uri = mk.group(1)
                    if uri.startswith("/"):
                        s = s.replace(f'URI="{uri}"', f'URI="{base_origin + uri}"')
                    elif not uri.startswith("http"):
                        s = s.replace(f'URI="{uri}"', f'URI="{urljoin(base_dir, uri)}"')
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
        return bool(re.search(r"\.(m3u8|mp4)(\?|$)", url or "", re.I))

    def manualVideoCheck(self):
        return False
