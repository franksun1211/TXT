# coding: utf-8
# javgogogo（javgogogo.com）— 線上看日本 AV，T3 爬蟲源（繁體站）
# 架構：WordPress + famoustube 主題，標準 HTML / curl_cffi 後端 / UTF-8。
# 列表卡片：div.video-block > a.thumb[href] + img.video-img[data-src] + span.duration
#          + a.infos[title]。首頁/分類/搜尋共用。
# 詳情頁：<meta itemprop="embedURL" content="https://{embed}/embed/{VID}/?source=..">
#         embed host 會輪替（videovjav.com / videohclips.com …）。
# 播放鏈（KVS / kt_player，純 HTTP 已驗證）：
#   1) GET {embed}/embed/{VID}  取 302 Location → 真實取流 host（vjav.tube…）
#   2) GET 真實 embed 頁一次（種下 source / kt_lang cookie）
#   3) GET {host}/api/videofile.php?video_id={VID}  Referer=真實 embed
#      回 JSON array，[0].video_url 為 KVS 混淆字串
#   4) 解混淆：西里爾同形字→拉丁、逗號→斜線、base64 補 padding 解碼得相對路徑
#   5) 補 host 得 _hq.mp4 直鏈，加 &f=video.m3u8 得 HLS（簽名含 ti 時效，現取現用）

import re
import base64
from urllib.parse import quote, urlparse, urljoin

from base.spider import Spider as BaseSpider


class Spider(BaseSpider):
    # ---- 換站只改這三行（法則18）----
    MAIN = "https://javgogogo.com"
    HOST = "https://javgogogo.com"
    FALLBACK_HOSTS = []

    UA = ("Mozilla/5.0 (Linux; Android 14; 22127RK46C/Pixel5) AppleWebKit/537.36 "
          "(KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36")

    # embed 頁的 publisher id（站台固定），缺省沿用
    SOURCE = "99072762"

    # 靜態分類（零網絡，法則16）。(type_id, 顯示名, 種類 category/tag)
    CLASSES = [
        ("素人自拍", "素人自拍", "category"),
        ("無碼", "無碼", "category"),
        ("有碼", "有碼", "category"),
        ("中文字幕", "中文字幕", "tag"),
        ("人妻熟女", "人妻熟女", "category"),
        ("少女美女", "少女美女", "category"),
        ("女同性戀", "女同性戀", "category"),
        ("捆綁調教", "捆綁調教", "category"),
        ("uncategorized", "其他", "category"),
    ]

    # 卡片拆塊以 a.thumb 為界
    RE_HREF = re.compile(r'href="([^"]+)"')
    RE_DATASRC = re.compile(r'data-src="([^"]+)"')
    RE_DUR = re.compile(r'<span class="duration">([^<]+)</span>')
    RE_INFO_TITLE = re.compile(r'<a class="infos"[^>]*title="([^"]+)"')
    RE_SPAN_TITLE = re.compile(r'<span class="title">([^<]+)</span>')

    # 詳情頁欄位
    RE_H1 = re.compile(r'<h1[^>]*>([^<]+)</h1>')
    RE_OGIMG = re.compile(r'property="og:image"\s+content="([^"]+)"')
    RE_DUR_META = re.compile(r'itemprop="duration"\s+content="([^"]+)"')
    RE_DATE_META = re.compile(r'itemprop="uploadDate"\s+content="([^"]+)"')
    RE_CAT = re.compile(r'href="[^"]*/category/[^"]+"\s+title="([^"]+)"')
    RE_EMBED_META = re.compile(r'itemprop="embedURL"\s+content="([^"]+)"')
    RE_EMBED_IFRAME = re.compile(r'<iframe[^>]*width="640"[^>]*src="([^"]+)"')
    RE_EMBED_ANY = re.compile(r'src="(https?://[^"]+/embed/\d+[^"]*)"')
    RE_VID = re.compile(r'/embed/(\d+)')

    # KVS 西里爾同形字 → 拉丁
    HOMO = {c: l for c, l in zip(
        "АВЕКМНОРСТХавекмнорстх", "ABEKMHOPCTXabekmhopctx")}

    # ------------------------------------------------------------- framework
    def getName(self):
        return "javgogogo"

    def getDependence(self):
        return []

    def isVideoFormat(self, url):
        url = url or ""
        return ".m3u8" in url or ".mp4" in url

    def manualVideoCheck(self):
        return False

    def destroy(self):
        return ""

    def liveContent(self, url):
        return ""

    def action(self, action):
        return ""

    def init(self, extend=""):
        self.extend = extend or ""
        self.headers = {
            "User-Agent": self.UA,
            "Referer": self.MAIN + "/",
            "Accept-Language": "zh-TW,zh;q=0.9",
        }
        self.play_header = {"User-Agent": self.UA}

    def host(self):
        return self.HOST

    # ------------------------------------------------------------- helpers
    def _get(self, path):
        url = path if path.startswith("http") else (self.host() + path)
        try:
            r = self.fetch(url, headers=self.headers, timeout=25)
            if r is None or getattr(r, "status_code", 0) != 200:
                return ""
            return r.text or ""
        except Exception as e:
            self.log("get fail %s: %s" % (url, e))
            return ""

    @staticmethod
    def _unescape(s):
        if not s:
            return ""
        for a, b in (("&#8230;", "…"), ("&amp;", "&"), ("&lt;", "<"),
                     ("&gt;", ">"), ("&quot;", '"'), ("&#039;", "'"),
                     ("&#39;", "'"), ("&nbsp;", " ")):
            s = s.replace(a, b)
        return s.strip()

    def _parse_list(self, html, exclude=None):
        """解析列表卡片。vod_id = 詳情頁 slug（path 單段，percent-encoded）。"""
        out = []
        for seg in (html or "").split('<a class="thumb"')[1:]:
            mh = self.RE_HREF.search(seg)
            if not mh:
                continue
            pr = urlparse(mh.group(1))
            if "javgogogo" not in pr.netloc:  # 排除廣告外鏈
                continue
            slug = pr.path.strip("/")
            low = slug.lower()
            if (not slug or "/" in slug
                    or low in ("category", "tag", "author", "page")):
                continue
            if exclude and slug == exclude:
                continue
            mi = self.RE_DATASRC.search(seg)
            pic = mi.group(1) if mi else ""
            md = self.RE_DUR.search(seg)
            remark = md.group(1).strip() if md else ""
            mt = self.RE_INFO_TITLE.search(seg) or self.RE_SPAN_TITLE.search(seg)
            name = self._unescape(mt.group(1)) if mt else slug
            out.append({
                "vod_id": slug,
                "vod_name": name,
                "vod_pic": pic,
                "vod_remarks": remark,
                "style": {"type": "rect", "ratio": 1.33},
            })
        return out

    def _pagecount(self, html, pg, cnt):
        nums = [int(x) for x in re.findall(r'/page/(\d+)/', html or "")]
        if nums:
            return max(max(nums), pg)
        return pg + 1 if cnt >= 20 else pg

    @staticmethod
    def _iso_dur(s):
        m = re.match(r'P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?', s or "")
        if not m:
            return ""
        d, h, mi, se = (int(x) if x else 0 for x in m.groups())
        h += d * 24
        if h:
            return "%d:%02d:%02d" % (h, mi, se)
        return "%d:%02d" % (mi, se)

    # ------------------------------------------------------------- home
    def homeContent(self, filter):
        classes = [{"type_id": t, "type_name": n} for t, n, _ in self.CLASSES]
        return {"class": classes, "filters": {}}

    def getHomeContent(self, filter):
        return self.homeContent(filter)

    def homeVideoContent(self):
        return {"list": self._parse_list(self._get("/"))}

    # ------------------------------------------------------------- category
    def _kind(self, tid):
        for t, _, k in self.CLASSES:
            if t == tid:
                return k
        return "category"

    def categoryContent(self, tid, pg, filter, extend):
        try:
            pg = int(pg or 1)
        except Exception:
            pg = 1
        tid = str(tid).strip("/")
        kind = self._kind(tid)
        seg = "tag" if kind == "tag" else "category"
        slug = tid if tid == "uncategorized" else quote(tid, safe="")
        path = "/%s/%s/" % (seg, slug)
        if pg > 1:
            path += "page/%d/" % pg
        html = self._get(path)
        vods = self._parse_list(html)
        pc = self._pagecount(html, pg, len(vods))
        return {
            "list": vods,
            "page": pg,
            "pagecount": pc,
            "limit": len(vods) or 20,
            "total": (len(vods) or 20) * pc,
        }

    # ------------------------------------------------------------- search
    def searchContent(self, key, quick, pg="1"):
        try:
            pg = int(pg or 1)
        except Exception:
            pg = 1
        if pg > 1:
            path = "/page/%d/?s=%s" % (pg, quote(key))
        else:
            path = "/?s=%s" % quote(key)
        return {"list": self._parse_list(self._get(path)), "page": pg}

    def getSearchContent(self, key, quick, pg="1"):
        return self.searchContent(key, quick, pg)

    # ------------------------------------------------------------- detail
    def _norm_ids(self, ids):
        if ids is None:
            return ""
        if isinstance(ids, (list, tuple)):
            ids = ids[0] if ids else ""
        if isinstance(ids, bytes):
            ids = ids.decode("utf-8", "replace")
        return str(ids).strip().strip("/")

    def _embed_of(self, html):
        """從詳情頁取 (embed_host, VID)。"""
        m = (self.RE_EMBED_META.search(html)
             or self.RE_EMBED_IFRAME.search(html)
             or self.RE_EMBED_ANY.search(html))
        if not m:
            return "", ""
        url = m.group(1)
        mv = self.RE_VID.search(url)
        return urlparse(url).netloc, (mv.group(1) if mv else "")

    def detailContent(self, ids):
        vid = self._norm_ids(ids)
        if not vid:
            return {"list": []}
        slug = vid.split("/")[-1]
        try:
            html = self._get("/%s/" % vid)
            mt = self.RE_H1.search(html)
            name = self._unescape(mt.group(1)) if mt else slug
            mi = self.RE_OGIMG.search(html)
            pic = mi.group(1) if mi else ""
            md = self.RE_DUR_META.search(html)
            dur = self._iso_dur(md.group(1)) if md else ""
            mdt = self.RE_DATE_META.search(html)
            date = (mdt.group(1)[:10] if mdt else "")
            cats = [self._unescape(x) for x in self.RE_CAT.findall(html)]
            cats = list(dict.fromkeys([c for c in cats if c]))
            ehost, evid = self._embed_of(html)

            parts = []
            if date:
                parts.append("發行日期：" + date)
            if dur:
                parts.append("時長：" + dur)
            if cats:
                parts.append("分類：" + "、".join(cats))

            play_url = ""
            if ehost and evid:
                # id 內以 | 分隔 VID 與 embed host（法則21：禁裸 $）
                play_url = "正片$%s|%s" % (evid, ehost)

            vod = {
                "vod_id": vid,
                "vod_name": name,
                "vod_pic": pic,
                "vod_year": date[:4],
                "vod_area": "日本",
                "vod_remarks": dur or (cats[0] if cats else ""),
                "vod_content": "\n".join(parts),
                "type_name": "、".join(cats),
                "vod_play_from": "javgogogo",
                "vod_play_url": play_url or ("正片$%s|" % slug),
            }
            return {"list": [vod]}
        except Exception as e:
            self.log("detail fail %s: %s" % (vid, e))
            return {"list": [{
                "vod_id": vid, "vod_name": slug, "vod_pic": "",
                "vod_remarks": "解析異常", "vod_content": "",
                "vod_play_from": "javgogogo", "vod_play_url": "正片$%s|" % slug,
            }]}

    # ------------------------------------------------------------- recommend
    def recommendContent(self, ids=None, pg="1"):
        vid = self._norm_ids(ids)
        try:
            if vid:
                html = self._get("/%s/" % vid)
                vods = self._parse_list(html, exclude=vid)
                if vods:
                    return {"list": vods[:18]}
            vods = self._parse_list(self._get("/"), exclude=vid)
            return {"list": vods[:18]}
        except Exception as e:
            self.log("recommend fail: %s" % e)
            return {"list": []}

    # ------------------------------------------------------------- player
    def _kvs_decode(self, enc, base):
        """KVS video_url → 絕對直鏈。"""
        s = "".join(self.HOMO.get(c, c) for c in (enc or ""))
        s = s.replace(",", "/")
        pad = len(s) % 4
        if pad:
            s += "=" * (4 - pad)
        rel = base64.b64decode(s).decode("utf-8", "replace")
        if not rel.startswith("/"):
            rel = "/" + rel
        return base.rstrip("/") + rel

    def _resolve_play(self, vid, ehost):
        """embed host + VID → 簽名 m3u8（現取現用，含 ti 時效）。"""
        embed = "https://%s/embed/%s/?source=%s" % (ehost, vid, self.SOURCE)
        # 1) 取 302 真實取流 host
        r0 = self.fetch(embed, headers={"User-Agent": self.UA},
                        allow_redirects=False, timeout=25)
        loc = ""
        try:
            loc = r0.headers.get("Location") or r0.headers.get("location") or ""
        except Exception:
            loc = ""
        real = urljoin(embed, loc) if loc else embed
        base = "https://" + urlparse(real).netloc
        # 2) 載入真實 embed 頁一次（種 cookie）
        try:
            self.fetch(real, headers={"User-Agent": self.UA},
                       allow_redirects=True, timeout=25)
        except Exception:
            pass
        # 3) videofile.php 取混淆字串
        api = "%s/api/videofile.php?video_id=%s" % (base, vid)
        r2 = self.fetch(api, headers={"User-Agent": self.UA, "Referer": real},
                        timeout=25)
        if r2 is None or getattr(r2, "status_code", 0) != 200:
            return "", real
        try:
            arr = r2.json()
        except Exception:
            arr = None
        if not arr:
            m = re.search(r'"video_url"\s*:\s*"([^"]+)"', r2.text or "")
            enc = m.group(1) if m else ""
        else:
            enc = (arr[0] or {}).get("video_url", "")
        if not enc:
            return "", real
        # 4) 解混淆 → mp4 → 加 &f=video.m3u8 得 HLS
        mp4 = self._kvs_decode(enc, base)
        sep = "&" if "?" in mp4 else "?"
        return mp4 + sep + "f=video.m3u8", real

    def playerContent(self, flag, id, vipFlags):
        sid = str(id or "")
        # 直鏈防呆
        if sid.startswith("http") and (".m3u8" in sid or ".mp4" in sid):
            return {"parse": 0, "url": sid, "header": self.play_header}
        try:
            if "|" in sid:
                vid, ehost = sid.split("|", 1)
            else:
                vid, ehost = sid, ""
            vid = vid.strip()
            ehost = ehost.strip()
            if vid and ehost:
                url, real = self._resolve_play(vid, ehost)
                if url:
                    return {"parse": 0, "url": url,
                            "header": {"User-Agent": self.UA, "Referer": real}}
        except Exception as e:
            self.log("player fail %s: %s" % (sid, e))
        return {"parse": 0, "url": "", "header": self.play_header}

    def localProxy(self, param):
        return [200, "text/plain", ""]
