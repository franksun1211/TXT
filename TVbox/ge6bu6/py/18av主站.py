# -*- coding: utf-8 -*-
"""
18AV 主站 (18av.mm-cg.com) — TVBox / WebHTV 四壳通用 Python 源
================================================================================
与 Java 侧 csp_Zaka18avMcgAmns 同一套接口、同一套 tid / 字段口径，两边可互校。

站点血统：mm-cg 綜合論壇附屬分站，非苹果CMS，自带「隨機/列表/搜索/详情」路径体系，
          /zh/ 语言前缀，整站 Cloudflare 前置但无挑战（直连 200，实测 165KB 首页）。

实测结构（2026-09-30 现探，全部真请求）
  首页      /zh/                                  页头带搜索框
  每日更新  /zh/content_news/all/<yyyy-MM-dd>.html
  列表分页  /zh/<cat>_list/all/<N>.html           **24 条/页**
  分类标签  /zh/<cat>_category/<id>/<名字>/<N>.html  ★名字段服务端忽略，只需 id
  無碼廠牌  /zh/uncensored_makersr/<id>/<名字>/<N>.html  同上
  女優页    /zh/<cat>_avperformer/<id>/<名字>/<N>.html
  搜索      /zh/<type>_search/all/<关键词>/<N>.html   type: fc/chineseonly/censoredonly/...
  详情      /zh/<cat>_content/<id>/<slug>.html

详情页高价值数据源：页头内嵌 <script type="application/ld+json"> 的 VideoObject，
字段 name / description(番號+女優) / keywords(分类标签) / thumbnailUrl / uploadDate。

播放链路（四段，缺一段黑屏）
  1) mvarr['10_1']=[['<domid>','<加密串>','<iframe...>','//…/play.php?numresolution=1080&id=','','…'],]
     加密串**每次刷新都变**（流密码），密钥也在页里。
  2) 解码三步（复刻自 /js/content_mv_protect.js 的 decrypto + decr_sun，双层 packer 混淆）：
       ① 按 chr(hcdeedg252+97) 切开（本站实测 'k' → radix=10）
       ② 每段当 hcdeedg252 进制 parseInt，再 ^ hadeedg252 → 拼回标准 base64
       ③ base64 → AES-CBC/PKCS5 解密（key=argdeqweqweqwe, iv=hdddedg252）→ 真 id
     ★ 四个常量每页随机（实测同页两次抓到不同 key），必须现页现抓。
  3) /js/player/play.php?numresolution=<q>&id=<真id> → const videoSources=[{src:'…m3u8',size:…}]
     CDN 域每次现签，绝不缓存。
  4) 防盗链实测：清单裸取 200；密钥 /aavv.vv 裸取 200（16 字节明文）；**分片裸取 404**，
     带 Referer 才回真数据；分片域与清单域差一个字符，不能互推。
     → 本插件统一把 Referer/UA 放进 playerContent().header，由播放器对所有子请求带上。
"""
import base64
import json
import re
import sys
import time
import urllib.parse
import urllib.request
import urllib.error

sys.path.append('..')
try:
    from base.spider import Spider as _Base
except Exception:
    _Base = object

HOST = "https://18av.mm-cg.com"
# 主站无备用域（www 前缀实测不解析），保留数组便于以后加镜像
HOSTS = [HOST]

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

PER_PAGE = 24
PAGE_TTL = 30 * 60

# ============================ 分类（与 Java 侧 CLASSES 逐条对齐） ============================
CLASSES = [
    ("🔥 每日更新", "news"),
    ("中文字幕AV", "L:chinese"),
    ("有碼AV", "L:censored"),
    ("無碼AV", "L:uncensored"),
    ("素人AV", "L:amateurjav"),
    ("無碼破解", "L:reducing-mosaic"),
    ("H動畫", "L:animation"),
    ("H有碼動畫", "L:CensoredAnimation"),
    ("H無碼動畫", "L:UncensoredAnimation"),
    ("H_3D動畫", "L:tdAnimation"),
    ("國產自拍", "L:dt"),

    ("🎬一本道", "M:32"),
    ("🎬カリビアンコム", "M:30"),
    ("🎬天然むすめ", "M:31"),
    ("🎬HEYZO", "M:17"),
    ("🎬東京熱", "M:29"),
    ("🎬ガチん娘", "M:35"),
    ("🎬パコパコママ", "M:36"),
    ("🎬エッチな4610", "M:34"),
    ("🎬人妻斬り0930", "M:38"),
    ("🎬エッチな0930", "M:39"),
    ("🎬CaribbeancomPPV", "M:40"),
    ("🎬XXX-AV", "M:126"),

    ("🔖巨乳", "G:127"),
    ("🔖美少女", "G:80"),
    ("🔖中出", "G:142"),
    ("🔖口交", "G:151"),
    ("🔖騎乗位", "G:161"),
    ("🔖多P", "G:169"),
    ("🔖巨尻", "G:50"),
    ("🔖近親相姦", "G:7"),
    ("🔖通姦", "G:21"),
    ("🔖出軌", "G:34"),
    ("🔖癡漢", "G:15"),
    ("🔖奴隷", "G:17"),
    ("🔖鬼畜", "G:22"),
    ("🔖野外・露出", "G:4"),
    ("🔖偷窥", "G:12"),
    ("🔖偶像藝人", "G:5"),
    ("🔖女主播", "G:58"),
    ("🔖各種職業", "G:70"),
    ("🔖眼鏡", "G:95"),
    ("🔖旗袍", "G:100"),
    ("🔖運動", "G:32"),
    ("🔖女同性戀", "G:2"),
    ("🔖觸手", "G:38"),
    ("🔖拷問", "G:40"),
    ("🔖學校作品", "G:23"),
    ("🔖家庭教師", "G:63"),
    ("🔖M男", "G:44"),
    ("🔖巨根", "G:47"),
    ("🔖ハーレム", "G:51"),
    ("🔖高畫質", "G:210"),
    ("🔖DMM獨家", "G:195"),
    ("🔖4小時以上作品", "G:207"),
]

# ============================ 正则 ============================
P_POST = re.compile(r"<div class='post[^']*'>(.*?)(?=<div class='post[^']*'>|</div>\s*</div>\s*</div>)", re.S)
P_HREF = re.compile(r"""href=["'](?:https?://[^"']*?)?(/zh/[a-zA-Z-]+_content/\d+/[^"']+?\.html)["']""")
P_H3 = re.compile(r"""itemprop=['"]name headline['"]><a[^>]*>(.*?)</a>""", re.S)
P_IMG = re.compile(r"""<img src=['"]([^'"]+)['"]""")
P_META = re.compile(r"<div class='meta'>([^<]*)</div>")
P_LASTPAGE = re.compile(r"""href=['"][^'"]*?/(\d{1,6})\.html['"]""")
P_H1 = re.compile(r"<h1[^>]*>(.*?)</h1>", re.S)
P_INFO_LI = re.compile(r"<li class='posts-headline'>(.*?)</li>\s*<li class='posts-message'>(.*?)</li>", re.S)
P_LDJSON = re.compile(r'<script type="application/ld\+json">(.*?)</script>', re.S)
P_DETAIL_PIC2 = re.compile(r"""<img[^>]+src=['"]([^'"]*(?:imgstream|eemmhh)[^'"]*)['"]""")
P_PERFORMER_TXT = re.compile(r"""/zh/[a-z0-9-]+_avperformer/\d+/[^'"]+['"]\s*>(.*?)</a>""", re.S)
P_CATLINK_TXT = re.compile(r"""/zh/[a-z0-9-]+_category/\d+/[^/'"]+/1\.html['"]\s*>(.*?)</a>""", re.S)

P_MVARR = re.compile(r"mvarr\['(\d+_\d+)'\]=\[\['([^']*)','([^']+)'")
P_NUMRES = re.compile(r"numresolution=(\d+)")
P_VSRC = re.compile(r"src:\s*'([^']+)'[^}]*?size:\s*(\d+)")
P_VSRC2 = re.compile(r"src:\s*'([^']+\.m3u8[^']*)'")

P_RADIX = re.compile(r"hcdeedg252\s*=\s*(\d+)")
P_XOR = re.compile(r"hadeedg252\s*=\s*(\d+)")
P_AESKEY = re.compile(r"argdeqweqweqwe\s*=\s*['\"]([^'\"]+)['\"]")
P_AESIV = re.compile(r"hdddedg252\s*=\s*['\"]([^'\"]+)['\"]")

# 实测默认值（2026-09-30 现抓本站一页）
DEF_RADIX, DEF_XOR = 10, 30
DEF_AESKEY, DEF_AESIV = "36f5cf81cc5c38d6", "457a1791140b5af8"


# ============================ 纯标准库 AES-CBC（Chaquopy 不保证有 pycryptodome） ============================
def _build_tables():
    """按 FIPS-197 的标准生成 S 盒 / 逆 S 盒（算法生成，避免手抄 512 个字节出错）。"""
    p = q = 1
    sbox = [0] * 256
    while True:
        p = p ^ ((p << 1) & 0xFF) ^ (0x1B if p & 0x80 else 0)
        q ^= (q << 1) & 0xFF
        q ^= (q << 2) & 0xFF
        q ^= (q << 4) & 0xFF
        if q & 0x80:
            q ^= 0x09
        x = q
        for shift in (1, 2, 3, 4):
            x ^= ((q << shift) | (q >> (8 - shift))) & 0xFF
        sbox[p] = (x ^ 0x63) & 0xFF
        if p == 1:
            break
    sbox[0] = 0x63
    rsbox = [0] * 256
    for i, v in enumerate(sbox):
        rsbox[v] = i
    return tuple(sbox), tuple(rsbox)


_SBOX, _RSBOX = _build_tables()
_RCON = (0x00, 0x01, 0x02, 0x04, 0x08, 0x10, 0x20, 0x40, 0x80, 0x1B, 0x36)


def _gmul(a, b):
    p = 0
    for _ in range(8):
        if b & 1:
            p ^= a
        hi = a & 0x80
        a = (a << 1) & 0xFF
        if hi:
            a ^= 0x1B
        b >>= 1
    return p


def _key_expand(key):
    nk = len(key) // 4
    nr = nk + 6
    w = [list(key[4 * i:4 * i + 4]) for i in range(nk)]
    for i in range(nk, 4 * (nr + 1)):
        t = list(w[i - 1])
        if i % nk == 0:
            t = t[1:] + t[:1]
            t = [_SBOX[b] for b in t]
            t[0] ^= _RCON[i // nk]
        elif nk > 6 and i % nk == 4:
            t = [_SBOX[b] for b in t]
        w.append([w[i - nk][j] ^ t[j] for j in range(4)])
    return w, nr


def _add_round_key(st, w, rnd):
    for c in range(4):
        for r in range(4):
            st[r][c] ^= w[rnd * 4 + c][r]


def _inv_shift_rows(st):
    for r in range(1, 4):
        st[r] = st[r][-r:] + st[r][:-r]


def _dec_block(block, w, nr):
    st = [[block[r + 4 * c] for c in range(4)] for r in range(4)]
    _add_round_key(st, w, nr)
    for rnd in range(nr - 1, 0, -1):
        _inv_shift_rows(st)
        for r in range(4):
            for c in range(4):
                st[r][c] = _RSBOX[st[r][c]]
        _add_round_key(st, w, rnd)
        for c in range(4):
            a = [st[r][c] for r in range(4)]
            st[0][c] = _gmul(a[0], 14) ^ _gmul(a[1], 11) ^ _gmul(a[2], 13) ^ _gmul(a[3], 9)
            st[1][c] = _gmul(a[0], 9) ^ _gmul(a[1], 14) ^ _gmul(a[2], 11) ^ _gmul(a[3], 13)
            st[2][c] = _gmul(a[0], 13) ^ _gmul(a[1], 9) ^ _gmul(a[2], 14) ^ _gmul(a[3], 11)
            st[3][c] = _gmul(a[0], 11) ^ _gmul(a[1], 13) ^ _gmul(a[2], 9) ^ _gmul(a[3], 14)
    _inv_shift_rows(st)
    for r in range(4):
        for c in range(4):
            st[r][c] = _RSBOX[st[r][c]]
    _add_round_key(st, w, 0)
    return bytes(st[r][c] for c in range(4) for r in range(4))


def aes_cbc_decrypt(data, key, iv):
    """AES-CBC 解密 + PKCS7 去填充（纯标准库）。key 支持 16/24/32 字节。"""
    if len(key) not in (16, 24, 32):
        raise ValueError('bad aes key len %d' % len(key))
    if len(iv) != 16:
        raise ValueError('bad iv len %d' % len(iv))
    if not data or len(data) % 16 != 0:
        raise ValueError('bad ciphertext len %d' % len(data))
    w, nr = _key_expand(key)
    out = bytearray()
    prev = iv
    for off in range(0, len(data), 16):
        blk = data[off:off + 16]
        dec = _dec_block(blk, w, nr)
        out.extend(bytes(dec[i] ^ prev[i] for i in range(16)))
        prev = blk
    pad = out[-1]
    if 1 <= pad <= 16:
        out = out[:-pad]
    return bytes(out)


# ============================ HTTP 层 ============================
class _HTTP(object):
    def __init__(self):
        self.engine = "urllib"
        self._curl = None
        try:
            from curl_cffi import requests as c
            self._curl = c
            self.engine = "curl_cffi"
        except Exception:
            self._curl = None

    def get(self, url, referer="", timeout=20):
        headers = {
            "User-Agent": UA,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,zh-TW;q=0.8,en;q=0.7",
        }
        if referer:
            headers["Referer"] = referer
        if self._curl is not None:
            try:
                r = self._curl.get(url, headers=headers, timeout=timeout, impersonate="chrome")
                return r.text or ""
            except Exception:
                pass
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read()
            for enc in ("utf-8", "gb18030", "big5", "latin-1"):
                try:
                    return raw.decode(enc)
                except Exception:
                    continue
            return raw.decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            try:
                return e.read().decode("utf-8", "replace")
            except Exception:
                return ""
        except Exception:
            return ""


def _clean(s):
    if not s:
        return ""
    s = re.sub(r"<[^>]*>", "", s)
    s = s.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">") \
         .replace("&quot;", '"').replace("&#39;", "'").replace("&nbsp;", " ")
    s = re.sub(r"&#(\d+);", lambda m: chr(int(m.group(1))), s)
    return s.strip()


def _unquote(s):
    return urllib.parse.quote(s, safe="")


class Spider(_Base):
    def __init__(self):
        self.host = HOSTS[0]
        self._cache = {}
        self.http_client = _HTTP()

    # ---------------- 生命周期 ----------------
    def init(self, extend=""):
        self.host = HOSTS[0]
        ext = (extend or "").strip()
        if ext.startswith("{"):
            try:
                o = json.loads(ext)
                h = o.get("host") or o.get("site") or ""
                if len(h) > 6:
                    if not h.startswith("http"):
                        h = "https://" + h
                    p = h.find("/", 8)
                    self.host = h[:p] if p > 8 else h
            except Exception:
                pass
        elif ext.startswith("http"):
            p = ext.find("/", 8)
            self.host = ext[:p] if p > 8 else ext

    def getName(self):
        return "18AV主站"

    def destroy(self):
        self._cache = {}

    def isVideoFormat(self, url):
        if not url:
            return False
        low = url.lower()
        return (".m3u8" in low) or (".mp4" in low) or (".ts" in low)

    def manualVideoCheck(self):
        return False

    def localProxy(self, param):
        return None

    # ---------------- 取页 ----------------
    def _fetch(self, url):
        if not url:
            return ""
        if url.startswith("//"):
            url = "https:" + url
        if url.startswith("/"):
            url = self.host + url
        hit = self._cache.get(url)
        if hit and time.time() - hit[0] < PAGE_TTL:
            return hit[1]
        t = self.http_client.get(url, referer=self.host + "/zh/")
        if not t or len(t) < 500:
            for h in HOSTS:
                if h == self.host:
                    continue
                t2 = self.http_client.get(url.replace(self.host, h), referer=h + "/zh/")
                if t2 and len(t2) > 500:
                    self.host = h
                    t = t2
                    break
        t = t or ""
        self._cache[url] = (time.time(), t)
        return t

    # ---------------- 列表解析 ----------------
    def _parse_cards(self, html):
        out = []
        if not html:
            return out
        seen = set()
        for m in P_POST.finditer(html):
            blk = m.group(1)
            mh = P_HREF.search(blk)
            if not mh:
                continue
            path = mh.group(1)
            if path in seen:
                continue
            name = ""
            mt = P_H3.search(blk)
            if mt:
                name = _clean(mt.group(1))
            if not name:
                for ma in re.finditer(r">([^<>]{4,})</a>", blk, re.S):
                    s = _clean(ma.group(1))
                    if len(s) > 3:
                        name = s
                        break
            if not name:
                continue
            seen.add(path)
            img = ""
            mi = P_IMG.search(blk)
            if mi:
                img = mi.group(1)
            if img.startswith("//"):
                img = "https:" + img
            elif img and not img.startswith("http"):
                img = urllib.parse.urljoin(self.host + "/", img)
            remarks = ""
            mm = P_META.search(blk)
            if mm:
                remarks = _clean(mm.group(1))
            out.append({
                "vod_id": path,
                "vod_name": name[:90],
                "vod_pic": img,
                "vod_remarks": remarks,
            })
        return out

    @staticmethod
    def _last_page(html, default=100000):
        if not html:
            return default
        mx = 0
        for m in P_LASTPAGE.finditer(html):
            try:
                n = int(m.group(1))
                if n > mx:
                    mx = n
            except Exception:
                pass
        return mx if mx > 0 else default

    def _list_url(self, tid, page):
        t = (tid or "").strip() or "L:censored"
        if t == "news":
            return "/zh/content_news/all/%s.html" % time.strftime("%Y-%m-%d", time.localtime())
        if t.startswith("M:"):
            # 無碼廠牌：名字段服务端忽略，填 x 占位
            return "/zh/uncensored_makersr/%s/x/%d.html" % (t[2:].strip() or "32", page)
        if t.startswith("G:"):
            return "/zh/censored_category/%s/x/%d.html" % (t[2:].strip() or "127", page)
        cat = t[2:].strip() if t.startswith("L:") else t
        return "/zh/%s_list/all/%d.html" % (cat or "censored", page)

    # ---------------- 首页 / 分类 ----------------
    def homeContent(self, filter):
        cls = [{"type_id": tid, "type_name": nm} for nm, tid in CLASSES]
        return {"class": cls, "list": self._home_list()}

    def homeVideoContent(self):
        return {"list": self._home_list()}

    def _home_list(self):
        try:
            day = time.strftime("%Y-%m-%d", time.localtime())
            out = self._parse_cards(self._fetch("/zh/content_news/all/%s.html" % day))
            if not out:
                out = self._parse_cards(self._fetch("/zh/uncensored_random/all/index.html"))
            return out
        except Exception:
            return []

    def categoryContent(self, tid, pg, filter, extend):
        try:
            page = max(1, int(str(pg).strip() or "1"))
        except Exception:
            page = 1
        html = self._fetch(self._list_url(tid, page))
        lst = self._parse_cards(html)
        last = self._last_page(html, 100000)
        if not lst and page > 1:
            last = page - 1
        return {
            "list": lst,
            "page": page,
            "pagecount": last,
            "limit": PER_PAGE,
            "total": last * PER_PAGE,
        }

    # ---------------- 搜索 ----------------
    def searchContent(self, key, quick, pg="1"):
        try:
            page = max(1, int(str(pg).strip() or "1"))
        except Exception:
            page = 1
        kw = (key or "").strip()
        if not kw:
            return {"list": []}
        html = self._fetch("/zh/fc_search/all/%s/%d.html" % (_unquote(kw), page))
        return {"list": self._parse_cards(html), "page": page}

    # ---------------- 详情 ----------------
    def detailContent(self, ids):
        vid = (ids or [""])[0].strip()
        if not vid:
            return {"list": []}
        url = vid if vid.startswith("http") else self.host + (vid if vid.startswith("/") else "/" + vid)
        html = self._fetch(url)

        # ① JSON-LD VideoObject 优先
        ld = {}
        ml = P_LDJSON.search(html or "")
        if ml:
            try:
                ld = json.loads(ml.group(1))
            except Exception:
                ld = {}

        name = _clean(ld.get("name", ""))
        if not name:
            mh = P_H1.search(html or "")
            if mh:
                name = _clean(mh.group(1))

        pic = ld.get("thumbnailUrl", "") or ""
        if not pic:
            mp = P_DETAIL_PIC2.search(html or "")
            if mp:
                pic = mp.group(1)
        if pic.startswith("//"):
            pic = "https:" + pic

        # ② 信息表
        info = []
        for m in P_INFO_LI.finditer(html or ""):
            k = _clean(m.group(1)).replace(":", "").replace("：", "")
            v = _clean(m.group(2))
            if not k or not v or v.upper() == "N/A":
                continue
            info.append("%s: %s" % (k, v))
        date = ld.get("uploadDate", "") or ""
        if date and not any(date in i for i in info):
            info.append("發佈: %s" % date)

        # ③ 女優 / ④ 分类标签
        actors, seen_a = [], set()
        for m in P_PERFORMER_TXT.finditer(html or ""):
            a = _clean(m.group(1))
            if a and len(a) <= 40 and a not in seen_a:
                seen_a.add(a)
                actors.append(a)

        tags, seen_t = [], set()
        for m in P_CATLINK_TXT.finditer(html or ""):
            t = _clean(m.group(1))
            if t and len(t) <= 24 and t not in seen_t:
                seen_t.add(t)
                tags.append(t)
        if not tags and ld.get("keywords"):
            tags.append(_clean(ld["keywords"]))

        content = ["【Zaka】"]
        content.append("片名: %s" % name)
        if actors:
            content.append("女優: %s" % " / ".join(actors))
        if tags:
            content.append("分類: %s" % " / ".join(tags))
        if info:
            content.append("信息: %s" % " · ".join(info))
        if ld.get("description"):
            content.append("簡介: %s" % _clean(ld["description"]))
        content.append("來源: %s" % url)

        # ⑤ 解码出每路清晰度的真 id
        lines = []
        try:
            d = self._decoder(html or "")
            by_q = {}
            for m in P_MVARR.finditer(html or ""):
                enc = m.group(3)
                tail = (html or "")[m.end():m.end() + 2500]
                mq = P_NUMRES.search(tail)
                q = mq.group(1) if mq else "0"
                pid = self.decode_id(enc, d)
                if pid:
                    by_q[q] = pid
            for q, pid in by_q.items():
                q = q if q and q != "0" else "1080"
                lines.append(("%s·%sP" % (self.getName(), q), pid, q))
        except Exception:
            pass

        play_from, play_url = [], []
        if lines:
            flag = "$$$".join(x[0] for x in lines)
            nm = name[:40] if name else "正片"
            play_url.append("$$$".join("%s$%s@%s" % (nm, x[1], x[2]) for x in lines))
            play_from.append(flag)
        else:
            # 兜底：把详情页交给 playerContent 现场再解一次
            play_from.append("%s·正片" % self.getName())
            play_url.append("%s$page@%s" % ((name[:40] or "正片"), url))

        return {"list": [{
            "vod_id": vid,
            "vod_name": name,
            "vod_pic": pic,
            "vod_remarks": date or (" · ".join(info) if info else ""),
            "vod_content": "\n".join(content),
            "vod_play_from": "$$$".join(play_from),
            "vod_play_url": "$$$".join(play_url),
        }]}

    # ---------------- 解码器 ----------------
    def _decoder(self, html):
        d = {"radix": DEF_RADIX, "xor": DEF_XOR, "key": DEF_AESKEY, "iv": DEF_AESIV}
        try:
            m = P_RADIX.search(html)
            if m:
                d["radix"] = int(m.group(1))
        except Exception:
            pass
        try:
            m = P_XOR.search(html)
            if m:
                d["xor"] = int(m.group(1))
        except Exception:
            pass
        try:
            m = P_AESKEY.search(html)
            if m and len(m.group(1)) == 16:
                d["key"] = m.group(1)
        except Exception:
            pass
        try:
            m = P_AESIV.search(html)
            if m and len(m.group(1)) == 16:
                d["iv"] = m.group(1)
        except Exception:
            pass
        # 站方 JS 里做了 clamp：> 25 取模
        if d["radix"] > 25:
            d["radix"] = d["radix"] % 25
        if d["radix"] < 2 or d["radix"] > 36:
            d["radix"] = DEF_RADIX
        return d

    @staticmethod
    def decode_id(enc, d):
        """加密串 -> 播放器 id：拆分隔符 → radix 进制 parseInt → XOR → base64 → AES-CBC。"""
        if not enc or not d:
            return ""
        try:
            sep = chr(d["radix"] + 97)
            b64 = []
            for tok in enc.split(sep):
                if not tok:
                    continue
                try:
                    val = int(tok, d["radix"])
                except Exception:
                    v = 0
                    for ch in tok:
                        try:
                            c = int(ch, d["radix"])
                        except Exception:
                            break
                        v = v * d["radix"] + c
                    val = v
                b64.append(chr((val ^ d["xor"]) & 0xFF))
            raw = base64.b64decode("".join(b64))
            if not raw or len(raw) % 16 != 0:
                return ""
            out = aes_cbc_decrypt(raw, d["key"].encode("utf-8"), d["iv"].encode("utf-8"))
            s = out.decode("utf-8", "ignore").strip()
            for ch in s:
                if not (ch.isalnum() or ch in "_-="):
                    return ""
            return s
        except Exception:
            return ""

    # ---------------- 播放 ----------------
    def _pick_source(self, page, q):
        first = ""
        for m in P_VSRC.finditer(page or ""):
            u, size = m.group(1), m.group(2)
            if ".m3u8" not in u:
                continue
            if size == q:
                return u
            if not first:
                first = u
        if not first:
            m2 = P_VSRC2.search(page or "")
            if m2:
                first = m2.group(1)
        return first

    def playerContent(self, flag, id, vipFlags):
        header = {"User-Agent": UA, "Referer": self.host + "/zh/"}
        try:
            pid, q, page = "", "1080", ""
            if "@" in (id or ""):
                pid, q = id.split("@", 1)
            elif (id or "").startswith("page@"):
                page = id[5:]
            if pid.startswith("http"):
                return {"parse": 0, "url": pid, "header": header}
            if not pid or len(pid) < 4:
                if not page:
                    return {"parse": 0, "url": "", "header": header, "msg": "没取到播放地址，稍后重试"}
                html = self._fetch(page)
                d = self._decoder(html)
                m = P_MVARR.search(html or "")
                if not m:
                    return {"parse": 0, "url": "", "header": header, "msg": "没取到播放地址，稍后重试"}
                pid = self.decode_id(m.group(3), d)
                mq = P_NUMRES.search((html or "")[m.end():m.end() + 2500])
                if mq:
                    q = mq.group(1)
            if len(pid) < 4:
                return {"parse": 0, "url": "", "header": header, "msg": "没取到播放地址，稍后重试"}

            m3u8 = ""
            for _ in range(3):
                ph = self._fetch("%s/js/player/play.php?numresolution=%s&id=%s" % (self.host, q, pid))
                if ph:
                    m3u8 = self._pick_source(ph, q)
                if m3u8:
                    break
                time.sleep(0.3)
            if not m3u8:
                return {"parse": 0, "url": "", "header": header, "msg": "没取到播放地址，稍后重试"}
            if m3u8.startswith("//"):
                m3u8 = "https:" + m3u8
            return {"parse": 0, "url": m3u8, "header": header,
                    "format": "application/x-mpegURL"}
        except Exception:
            return {"parse": 0, "url": "", "header": header, "msg": "没取到播放地址，稍后重试"}


# ============================ 本地自检 ============================
if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--cat", action="store_true")
    ap.add_argument("--list", nargs=2, metavar=("TID", "PG"))
    ap.add_argument("--detail")
    ap.add_argument("--search")
    ap.add_argument("--play")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    s = Spider()
    s.init("")
    if a.cat:
        h = s.homeContent(True)
        print("分类 %d / 首屏 %d" % (len(h["class"]), len(h.get("list", []))))
        for c in h["class"][:12]:
            print("  ", c["type_id"], c["type_name"])
    if a.list:
        r = s.categoryContent(a.list[0], a.list[1], True, {})
        print("page=%s/%s limit=%s" % (r["page"], r["pagecount"], r["limit"]))
        for v in r["list"][:6]:
            print("  ", v["vod_id"], "|", v["vod_name"][:52], "|", v["vod_remarks"])
    if a.search:
        r = s.searchContent(a.search, True, "1")
        print("命中 %d" % len(r["list"]))
        for v in r["list"][:5]:
            print("  ", v["vod_id"], "|", v["vod_name"][:60])
    if a.detail:
        r = s.detailContent([a.detail])
        for v in r["list"]:
            print("片名:", v["vod_name"])
            print("封面:", v["vod_pic"])
            print("线路:", v["vod_play_from"])
            print("播放:", v["vod_play_url"][:200])
            print("---- 简介 ----")
            print(v["vod_content"])
    if a.play:
        r = s.playerContent("", a.play, [])
        print("url:", r.get("url"))
        print("msg:", r.get("msg", ""))
    if a.selftest:
        _t0 = time.time()
        h = s.homeContent(True)
        print("[首页] 分类 %d / 首屏 %d" % (len(h["class"]), len(h.get("list", []))))
        ok_n = 0
        cats = ["L:censored", "L:uncensored", "L:chinese", "L:amateurjav", "L:reducing-mosaic",
                "L:animation", "L:dt", "M:32", "G:127"]
        for c in cats:
            r = s.categoryContent(c, "1", False, {})
            n = len(r["list"])
            print("  [%s] %d 条" % (c, n))
            if n >= 10:
                ok_n += 1
        print("[分类] %d/%d 出片" % (ok_n, len(cats)))
        d = s.categoryContent("L:uncensored", "1", False, {})
        vid = d["list"][0]["vod_id"] if d["list"] else ""
        print("[详情] %s" % vid)
        det = s.detailContent([vid])["list"][0]
        print("  片名:", det["vod_name"][:60])
        print("  封面:", det["vod_pic"][:80])
        print("  线路:", det["vod_play_from"])
        url = det["vod_play_url"].split("#")[0]
        url = url[url.find("$") + 1:] if "$" in url else url
        pr = s.playerContent("", url, [])
        print("  播放:", (pr.get("url") or pr.get("msg", ""))[:100])
        if pr.get("url"):
            try:
                req = urllib.request.Request(pr["url"], headers=pr.get("header") or {"User-Agent": UA})
                with urllib.request.urlopen(req, timeout=25) as resp:
                    head = resp.read(400).decode("utf-8", "ignore")
                print("  清单头:", head.split("\n")[0])
                print("  #EXTM3U:", "#EXTM3U" in head)
            except Exception as e:
                print("  清单拉取失败:", e)
        print("[耗时] %.1fs" % (time.time() - _t0))
