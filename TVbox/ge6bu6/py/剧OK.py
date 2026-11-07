# coding=utf-8
# !/usr/bin/python
"""
================================================================================
 剧OK · OK影视 / TVBox 单文件 PY 源
 站点：https://juok7.top/ （剧OK，镜像 juok1~juok10.top 通用）
================================================================================

【站点结构】
  Next.js 站点，数据全部走 /api/ 接口：
    /api/filter?catId=1&page=1&size=24&type=&area=&year=   分类列表
        catId：1电影 2电视剧 3综艺 4动漫
        返回 {"movies":[{id,title,cdncover,cover,pubdate,area,actor,director,
                        description,total,upinfo,moviecategory,playlinks}],total}
    /api/detail?cat=2&id=PrNsaH7nTGHpM3                    详情
        返回 {"data":{...同上..., allepidetail:{站点:[{playlink_num,url}]}}}
    /api/search?q=关键词&page=1                             搜索（360影视源）
    /api/search?q=关键词&page=1&source=bf                  搜索（B线路采集源）

【播放 */
  本站自身只有爱奇艺/优酷/腾讯/芒果的正版网页链接，没有直链，
  必须靠解析接口。所以本脚本用「同名影片跨源取直链」的方案：

    通道1  B线路云播 —— source=bf 采集源，vod_play_url 本身就是可播 m3u8
            （CDN 多为 v.fengbao14.com / ddbbffcdn.com，实测 HLS 正常）
    通道2  D线路云播 —— source=dytt 采集源，同为直链 m3u8
    通道3  官方解析 —— 爱奇艺/腾讯/优酷正版页，交给 OK影视 自带解析器

  列表接口不返回「标题 -> 播放」映射，故 vod_id 编码为
      电影@PrNsaH7nTGHpM3@兰香如故
  详情时用标题去 bf / dytt 源反查同名资源，归一化 + 年份加权打分挑选。

【用法】
  OK影视 → 配置中心 → 接口填本文件 http 地址，或本地 python3 直接跑自测。
================================================================================
"""

import gzip
import html as htmllib
import json
import re
import threading
import urllib.parse
import urllib.request

try:
    from base.spider import Spider as BaseSpider
except Exception:  # 单文件本地调试
    class BaseSpider(object):
        def init(self, extend=""):
            pass


SITE = "https://juok7.top"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
TIMEOUT = 15
PAGE_SIZE = 24

# 分类 catId -> 名称（取自 /api/config 的 navConfig）
CLASSES = [("1", "电影"), ("2", "电视剧"), ("3", "综艺"), ("4", "动漫")]

# 下面三张表全部抄自 /api/config 的 filterConfig，与站点 PC 端筛选一致
TYPES = {
    "1": ["喜剧", "爱情", "动作", "恐怖", "科幻", "剧情", "犯罪", "奇幻", "战争",
          "悬疑", "动画", "文艺", "纪录", "传记", "歌舞", "古装", "历史", "惊悚",
          "伦理", "其他"],
    "2": ["言情", "剧情", "伦理", "喜剧", "悬疑", "都市", "偶像", "古装", "军事",
          "警匪", "历史", "励志", "神话", "谍战", "青春", "家庭", "动作", "情景",
          "武侠", "科幻", "其他"],
    "3": ["脱口秀", "真人秀", "搞笑", "选秀", "八卦", "访谈", "情感", "生活", "晚会",
          "音乐", "职场", "美食", "时尚", "游戏", "少儿", "体育", "纪实", "科教",
          "曲艺", "歌舞", "财经", "汽车", "播报", "其他"],
    "4": ["热血", "科幻", "美少女", "魔幻", "经典", "励志", "少儿", "冒险", "搞笑",
          "推理", "恋爱", "治愈", "幻想", "校园", "动物", "机战", "亲子", "儿歌",
          "运动", "悬疑", "怪物", "战争", "益智", "青春", "童话", "竞技", "动作",
          "社会", "友情", "真人版", "电影版", "OVA版", "TV版", "新番动画", "完结动画"],
}
AREAS = {
    "1": ["大陆", "香港", "台湾", "泰国", "美国", "韩国", "日本", "法国", "英国",
          "德国", "印度", "其他"],
    "2": ["大陆", "香港", "台湾", "泰国", "日本", "韩国", "美国", "英国", "新加坡"],
    "3": ["大陆", "香港", "台湾", "日本", "欧美"],
    "4": ["大陆", "日本", "美国"],
}
YEARS = [str(y) for y in range(2026, 2013, -1)]

# 备用直链采集源（按优先级）
SRC_BF = "bf"
SRC_DYTT = "dytt"

# 正版站点中文名
SITE_CN = {"qiyi": "爱奇艺", "youku": "优酷", "qq": "腾讯", "mgtv": "芒果",
           "imgo": "芒果", "bilibili": "B站", "le": "乐视", "sohu": "搜狐",
           "pptv": "PPTV", "1905": "1905"}


# ============================== 工具 ==========================================

def _clean(s):
    """去标签、反转义、压缩空白"""
    if not s:
        return ""
    s = re.sub(r"<[^>]+>", "", str(s))
    s = htmllib.unescape(s).replace("\u00a0", " ")
    return re.sub(r"\s+", " ", s).strip()


def _pic(u):
    """封面补全协议头"""
    u = (u or "").strip()
    if not u:
        return ""
    if u.startswith("//"):
        u = "https:" + u
    if not u.startswith("http"):
        return ""
    return u


def _jget(url):
    """GET + gzip 解压 + JSON 解析，任何失败返回 {}"""
    for _ in range(2):
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": UA,
                "Accept": "application/json, text/plain, */*",
                "Accept-Language": "zh-CN,zh;q=0.9",
                "Accept-Encoding": "gzip",
                "Referer": SITE + "/",
            })
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                raw = r.read()
                if (r.headers.get("Content-Encoding") or "").lower() == "gzip":
                    try:
                        raw = gzip.decompress(raw)
                    except OSError:
                        pass
                return json.loads(raw.decode("utf-8", "ignore"))
        except Exception:  # noqa: BLE001
            continue
    return {}


_NOISE = re.compile(
    r"[\[\]【】()（）<>《》:：·,，.。!！?？、;；\"'“”‘’\s\-_/\\|~`@#$%^&*+=]"
    r"|国语|粤语|中字|中文字幕|双语|高清|蓝光|完整版|未删减|抢先版|"
    r"第[一二三四五六七八九十\d]+季|电视剧|动漫|综艺")


def _norm(s):
    """标题归一化：去标点/季数/画质词，用于跨源同名匹配"""
    return _NOISE.sub("", (s or "").lower())


def _num_ep(name, idx):
    """从 '第12集'/'12'/'EP12' 里抠出集数序号，失败用 idx"""
    m = re.search(r"(\d+)", name or "")
    if m:
        try:
            return int(m.group(1))
        except ValueError:
            pass
    return idx


def _m3u8_of(vod_play_url):
    """采集站 vod_play_url -> [(集名, 直链)]，只保留 http 的 m3u8/mp4"""
    out = []
    for seg in (vod_play_url or "").split("#"):
        seg = seg.strip()
        if not seg:
            continue
        name, _, url = seg.partition("$")
        url = url.strip()
        if not url.startswith("http"):
            continue
        if not re.search(r"\.(m3u8|mp4)", url.split("?")[0], re.I):
            continue
        nm = _clean(name) or ("第%d集" % (len(out) + 1))
        out.append((nm, url))
    return out


def _sort_eps(eps):
    """按集数排序去重"""
    seen = set()
    uniq = []
    for i, (nm, u) in enumerate(eps):
        if u in seen:
            continue
        seen.add(u)
        uniq.append((_num_ep(nm, i + 1), nm, u))
    uniq.sort(key=lambda x: x[0])
    return [(nm, u) for _, nm, u in uniq]


class Spider(BaseSpider):

    def getName(self):
        return "剧OK"

    def init(self, extend=""):
        try:
            self.extend = (extend or "").strip()
            self._list = {}
            self._detail = {}
            self._bf = {}
            self._lock = threading.Lock()
        except Exception:  # noqa: BLE001
            pass
        return self

    def destroy(self):
        return ""

    def localProxy(self, param):
        return [200, "text/plain", "", ""]

    def isVideoFormat(self, url):
        return bool(re.search(r"\.(m3u8|mp4|flv)", url or "", re.I))

    def manualVideoCheck(self):
        return False

    # ---------------------------------------------------------------- 首页
    def homeContent(self, filter=False):
        result = {"class": [{"type_id": c[0], "type_name": c[1]} for c in CLASSES]}
        if filter:
            filters = {}
            for cid, _ in CLASSES:
                filters[cid] = [
                    {"key": "type", "name": "类型",
                     "value": [{"n": n, "v": n} for n in TYPES.get(cid, [])]},
                    {"key": "area", "name": "地区",
                     "value": [{"n": n, "v": n} for n in AREAS.get(cid, [])]},
                    {"key": "year", "name": "年份",
                     "value": [{"n": n, "v": n} for n in YEARS]},
                ]
            result["filters"] = filters
        return result

    def homeVideoContent(self):
        """首页推荐：取各分类第 1 页拼一起"""
        rows = []
        for cid, _ in CLASSES[:3]:
            try:
                rows.extend(self._list_vods(cid, 1, {}))
            except Exception:  # noqa: BLE001
                continue
        return {"list": rows[:60]}

    # ---------------------------------------------------------------- 分类
    def categoryContent(self, tid, pg, filter, extend):
        try:
            pg = max(1, int(pg or 1))
        except (TypeError, ValueError):
            pg = 1
        cid = str(tid)
        if cid not in ("1", "2", "3", "4"):
            cid = "1"
        ext = {}
        if extend:
            try:
                ext = json.loads(extend) if isinstance(extend, str) else dict(extend)
            except Exception:  # noqa: BLE001
                ext = {}
        rows, total = self._list_vods(cid, pg, ext, with_total=True)
        pagecount = (total + PAGE_SIZE - 1) // PAGE_SIZE if total else max(pg, 999)
        return {
            "page": pg,
            "pagecount": max(pagecount, pg),
            "limit": PAGE_SIZE,
            "total": total or 99999,
            "list": rows,
        }

    def _list_vods(self, cid, pg, ext, with_total=False):
        q = {"catId": cid, "page": str(pg), "size": str(PAGE_SIZE)}
        for k in ("type", "area", "year"):
            v = str(ext.get(k) or "").strip()
            if v and v != "全部":
                q[k] = v
        key = "list:" + urllib.parse.urlencode(q)
        with self._lock:
            if key in self._list:
                cached = self._list[key]
                return cached if with_total else cached[0]

        data = _jget(SITE + "/api/filter?" + urllib.parse.urlencode(q))
        rows = []
        for it in (data.get("movies") or []):
            vid = str(it.get("id") or "")
            if not vid:
                continue
            title = _clean(it.get("title"))
            if not title:
                continue
            cats = it.get("moviecategory") or []
            remark = ""
            if cats:
                remark = str(cats[0])
            total = int(it.get("total") or 0)
            up = int(it.get("upinfo") or 0)
            if total > 1:
                remark = (remark + " · " if remark else "") + \
                         ("更新至%d集" % up if (up and up < total) else "全%d集" % total)
            elif it.get("pubdate"):
                remark = (remark + " · " if remark else "") + str(it.get("pubdate"))[:4]
            rows.append({
                "vod_id": "%s@%s@%s" % (cid, vid, title),
                "vod_name": title,
                "vod_pic": _pic(it.get("cdncover") or it.get("cover")),
                "vod_remarks": remark,
            })

        try:
            total = int(data.get("total") or 0)
        except (TypeError, ValueError):
            total = 0
        pair = (rows, total)
        if rows:
            with self._lock:
                self._list[key] = pair
        return pair if with_total else rows

    # ---------------------------------------------------------------- 详情
    def detailContent(self, ids):
        try:
            raw = str(ids[0]) if ids else ""
            parts = raw.split("@")
            cid = parts[0] if parts and parts[0] in ("1", "2", "3", "4") else "1"
            vid = parts[1] if len(parts) > 1 else raw
            title_hint = parts[2] if len(parts) > 2 else ""
            meta = self._get(cid, vid, title_hint)

            vod = {
                "vod_id": raw,
                "vod_name": meta.get("name") or title_hint or vid,
                "vod_pic": _pic(meta.get("pic")),
                "vod_year": meta.get("year", ""),
                "vod_area": meta.get("area", ""),
                "vod_actor": meta.get("actor", ""),
                "vod_director": meta.get("director", ""),
                "vod_remarks": meta.get("remarks", ""),
                "vod_content": meta.get("content", ""),
                "vod_play_from": "$$$".join(s[0] for s in meta["sources"]),
                "vod_play_url": "$$$".join(s[1] for s in meta["sources"]),
            }
            if not vod["vod_play_url"]:
                vod["vod_play_from"] = "暂无播放源"
                vod["vod_play_url"] = ""
            return {"list": [vod]}
        except Exception as e:  # noqa: BLE001
            return {"list": [{"vod_id": str(ids[0]) if ids else "",
                              "vod_name": "加载失败",
                              "vod_content": "详情解析异常：%s" % e,
                              "vod_play_from": "暂无", "vod_play_url": ""}]}

    # ---------------------------------------------------------------- 搜索
    def searchContent(self, key, quick, pg="1"):
        """搜索走 360 影视源（片名最全），翻页由站内 page 控制"""
        try:
            key = (key or "").strip()
            if not key:
                return {"list": []}
            try:
                pg = max(1, int(pg or 1))
            except (TypeError, ValueError):
                pg = 1
            q = urllib.parse.urlencode({"q": key, "page": str(pg)})
            data = _jget(SITE + "/api/search?" + q)
            rows = []
            for it in (data.get("results") or []):
                vid = str(it.get("id") or it.get("en_id") or "")
                title = _clean(it.get("titleTxt") or it.get("title") or it.get("vod_name"))
                if not vid or not title:
                    continue
                cat = str(it.get("cat_id") or "")
                if cat not in ("1", "2", "3", "4"):
                    cat = "2" if (it.get("total") or it.get("actList")) else "1"
                tag = it.get("tag") or it.get("type_name") or []
                if isinstance(tag, str):
                    tag = [tag]
                remark = str(it.get("year") or it.get("vod_year") or "")
                if tag:
                    t0 = _clean(tag[0])
                    if t0:
                        remark = (remark + " · " if remark else "") + t0
                rows.append({
                    "vod_id": "%s@%s@%s" % (cat, vid, title),
                    "vod_name": title,
                    "vod_pic": _pic(it.get("cover") or it.get("vod_pic")),
                    "vod_remarks": remark,
                })
            return {"list": rows, "page": pg,
                    "pagecount": 1 if len(rows) < 20 else pg + 1,
                    "limit": 20, "total": len(rows)}
        except Exception:  # noqa: BLE001
            return {"list": []}

    # ---------------------------------------------------------------- 播放
    def playerContent(self, flag, id, vipFlags):
        try:
            url = (id or "").strip()
            if self.isVideoFormat(url):
                # B/D 线路直链，直接播
                return {"parse": 0, "playUrl": "", "url": url,
                        "header": self._hdr(), "jx": 0}
            if url.startswith("http"):
                # 正版平台网页，交给 app 的嗅探/解析器
                return {"parse": 1, "playUrl": "", "url": url,
                        "header": self._hdr(), "jx": 1}
            return {"parse": 1, "playUrl": "", "url": url, "header": self._hdr()}
        except Exception:  # noqa: BLE001
            return {"parse": 1, "playUrl": "", "url": id or "", "header": self._hdr()}

    # ---------------------------------------------------------------- 内部
    def _hdr(self):
        return {"User-Agent": UA, "Referer": SITE + "/"}

    def _get(self, cid, vid, title_hint):
        key = "%s@%s" % (cid, vid)
        with self._lock:
            if key in self._detail:
                return self._detail[key]
        meta = self._build(cid, vid, title_hint)
        if meta.get("name"):
            with self._lock:
                self._detail[key] = meta
        return meta

    def _build(self, cid, vid, title_hint):
        d = _jget("%s/api/detail?%s" % (
            SITE, urllib.parse.urlencode({"cat": cid, "id": vid})))
        it = d.get("data") or {}
        if not it:
            return {"name": title_hint, "sources": []}

        title = _clean(it.get("title")) or title_hint
        year = str(it.get("pubdate") or "")[:4]
        meta = {
            "name": title,
            "pic": it.get("cdncover") or it.get("cover"),
            "year": year,
            "area": "/".join(it.get("area") or []),
            "actor": "/".join((it.get("actor") or [])[:8]),
            "director": "/".join(it.get("director") or []),
            "content": _clean(it.get("description") or it.get("comment")),
        }
        total = int(it.get("total") or 0)
        up = int(it.get("upinfo") or 0)
        if total > 1:
            meta["remarks"] = ("更新至%d集" % up) if (up and up < total) else ("全%d集" % total)
        else:
            cats = it.get("moviecategory") or []
            meta["remarks"] = str(cats[0]) if cats else year

        sources = []

        # 通道1/2：跨源取直链 m3u8
        for src, label in ((SRC_BF, "B线路云播"), (SRC_DYTT, "D线路云播")):
            eps = self._bf_eps(title, year, src)
            if eps:
                sources.append((label, "#".join("%s$%s" % (n, u) for n, u in eps)))

        # 通道3：官方正版页（放最后，走解析）
        official = self._official(it)
        if official:
            sources.append(("官方解析",
                            "#".join("%s$%s" % (n, u) for n, u in official)))

        meta["sources"] = sources
        return meta

    def _official(self, it):
        """拼官方正版页选集：优先 allepidetail 分集，退到 playlinksdetail 整部"""
        out = []
        allepi = it.get("allepidetail") or {}
        pld = it.get("playlinksdetail") or {}
        order = list((it.get("playlink_sites") or [])) or list(allepi.keys())

        for site in order:
            cn = SITE_CN.get(site, site)
            eps = allepi.get(site) or []
            tmp = []
            for e in eps:
                u = (e or {}).get("url") or ""
                if not u.startswith("http"):
                    continue
                no = _clean((e or {}).get("playlink_num")) or str(len(tmp) + 1)
                if (e or {}).get("is_vip") == "1":
                    no += "(VIP)"
                tmp.append((no, u))
            tmp = _sort_eps(tmp)
            if tmp:
                out.append((cn, tmp))
                continue
            info = pld.get(site) or {}
            u = info.get("default_url") or ""
            if u.startswith("http"):
                out.append((cn, [("正片", u)]))

        # allepidetail 里可能有 playlink_sites 没列的站点
        for site, eps in allepi.items():
            if site in order:
                continue
            tmp = []
            for e in (eps or []):
                u = (e or {}).get("url") or ""
                if u.startswith("http"):
                    tmp.append((_clean(e.get("playlink_num")) or str(len(tmp) + 1), u))
            if tmp:
                out.append((SITE_CN.get(site, site), _sort_eps(tmp)))

        # 合并同名站点
        merged = {}
        seq = []
        for cn, eps in out:
            if cn in merged:
                merged[cn].extend(eps)
            else:
                merged[cn] = list(eps)
                seq.append(cn)
        return [(cn, _sort_eps(merged[cn])) for cn in seq]

    def _bf_eps(self, title, year, src=SRC_BF):
        """按标题搜采集源（bf/dytt），挑同名影片，返回 [(集名, 直链)]"""
        title = (title or "").strip()
        if not title:
            return []
        ck = "%s|%s|%s" % (src, title, year)
        with self._lock:
            if ck in self._bf:
                return self._bf[ck]

        q = urllib.parse.urlencode({"q": title, "page": "1", "source": src})
        data = _jget("%s/api/search?%s" % (SITE, q))
        results = data.get("results") or []

        want = _norm(title)
        if not want:
            return []
        best, best_score = None, -1
        for it in results:
            nm = _clean(it.get("vod_name") or it.get("titleTxt"))
            got = _norm(nm)
            if not got:
                continue
            score = 0
            if got == want:
                score = 100
            elif want in got or got in want:
                score = 62
            else:
                common = len(set(want) & set(got))
                if common < max(2, len(want) // 2):
                    continue
                score = 28 + common
            # 花絮/预告/解说不是正片
            if re.search(r"解说|预告|花絮|片段|特辑|彩蛋|混剪", nm):
                score -= 45
            # 综艺期次、分Part 之类不是正片
            elif re.search(r"上期|下期|期$|第\d+期", nm):
                score -= 25
            iy = str(it.get("vod_year") or "")
            if year and iy and iy not in ("0", ""):
                score += 12 if iy == year else -8
            if score > best_score:
                best, best_score = it, score

        eps = []
        if best and best_score >= 28:
            eps = _sort_eps(_m3u8_of(best.get("vod_play_url")))
        with self._lock:
            self._bf[ck] = eps
        return eps


# ============================== 本地自测 ======================================

if __name__ == "__main__":
    sp = Spider()
    sp.init()
    print("分类:", [c["type_name"] for c in sp.homeContent()["class"]])
    print("筛选键:", list(sp.homeContent(True)["filters"]["2"][0].keys()))

    lst = sp.categoryContent("2", "1", False, "")
    print("\n电视剧第1页:", len(lst["list"]), "总数:", lst["total"],
          "总页:", lst["pagecount"])
    for x in lst["list"][:3]:
        print("   ", x["vod_name"], "|", x["vod_remarks"], "|",
              "封面OK" if x["vod_pic"] else "无封面")

    if lst["list"]:
        sid = lst["list"][0]["vod_id"]
        d = sp.detailContent([sid])["list"][0]
        print("\n详情:", d["vod_name"], d["vod_year"], d["vod_area"],
              "|", d["vod_remarks"])
        print("  演员:", d["vod_actor"][:40])
        print("  播放源:", d["vod_play_from"])
        print("  选集前120:", d["vod_play_url"][:120])

    sc = sp.searchContent("小城良方", "", "1")
    print("\n搜索[小城良方]:", len(sc["list"]),
          [x["vod_name"] for x in sc["list"][:3]])
    if sc["list"]:
        d = sp.detailContent([sc["list"][0]["vod_id"]])["list"][0]
        print("  详情:", d["vod_name"], "| 源:", d["vod_play_from"])
        print("  选集前160:", d["vod_play_url"][:160])
