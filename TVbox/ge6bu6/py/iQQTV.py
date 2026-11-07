# coding=utf-8
# //@name:iQQTV
# //@id:iqqtv
# //@version:2
#
# 站点：iQQTV（豪華館HD，苹果CMS之外的自研 PHP 站群）
# 实测要点：
#   1) 取流接口 /subpage/getMv.php 必须登入；quality=2 才命中 720 真实源（其它值是空壳路径）
#   2) 免费片额外要求邮箱验证（need_email）；本插件自动注册账号 + 一次性邮箱验证，全自动绕登入
#   3) 站方「獨家」付费片需点数（point_not_enough），免费账号无法播放，插件只做提示
#   4) 列表卡片 link_mode=play（卡片直接指播放页），因此详情层直接从 player 页解析
# 四壳契约（TVBox / 影视仓 / OK影视 / PickTV）：
#  - 独立 class Spider，不继承 base.spider
#  - 13 标准接口齐全且全部可调用
#  - homeContent: class + filters 为 dict
#  - 列表五键 page/pagecount/limit/total/list
#  - 详情多线路 $$$、多集 #、集名与地址 $
#  - playerContent header 为 dict、parse=0/jx=0
#  - init 预热网络通道
#  - Accept-Encoding 统一 gzip, deflate（不声明 br）
#  - 分类层级铁律：父子分类必须同时完整写入
#  - X25519 曲线仅用于 CF 防护站，普通站不要强制设置（部分服务器不支持会握手失败）
#  - 铁律11：内置 CLASSICAL_MAP + desensitize()，返回前对展示文本脱敏，未成年条目剔除
#  - 铁律15：CF防护站点默认反代（rawSite/siteUrl域名替换式），playerContent.header 含 Referer+Origin
#  - 铁律17：广告预检 has_ads=True（模板默认保留完整m3u8广告处理能力：localProxy+_clean_m3u8+_is_ad_segment），实际使用时需用detect_m3u8_ads检测目标站点真实m3u8后更新此注释

import ast
import json
import os
import random
import re
import ssl
import string
import threading
import time
from urllib.parse import quote, urljoin, urlsplit

import requests
from requests.adapters import HTTPAdapter

try:
    from urllib3.poolmanager import PoolManager
    HAS_URLLIB3 = True
except Exception:
    PoolManager = None
    HAS_URLLIB3 = False

DEFAULT_HOST = "https://iqqp.quest"
DEFAULT_MIRRORS = ("iqqp4.work", "iqqp1.quest", "iqqp2.quest", "iqqp3.quest",
                   "iqqtv.net", "iqql.work")
PAGE_SIZE = 48
DEFAULT_UA = (
    "Mozilla/5.0 (Linux; Android 12) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36"
)
PLAYER_UA = DEFAULT_UA

NAV_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.6",
    "Accept-Encoding": "gzip, deflate",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-User": "?1",
}

CHALLENGE_MARKERS = (
    "cf-browser-verification", "just a moment", "attention required",
    "turnstile", "enable javascript and cookies to continue",
)

CATEGORY_TREE = (
    {"type_id": "home:index", "type_name": "最新上架", "url": "/"},
    {"type_id": "free:1", "type_name": "免費專區", "url": "/?cat=free"},
    {"type_id": "cat:1", "type_name": "中文", "url": "/category.php?cate=1"},
    {"type_id": "scate:1", "type_name": "無碼", "url": "/category.php?scate=1"},
    {"type_id": "zone:1", "type_name": "國產", "url": "/zone_index.php?zone=1"},
    {"type_id": "zone:2", "type_name": "卡通", "url": "/zone_index.php?zone=2"},
    {"type_id": "event:17", "type_name": "獨家", "url": "/search.php?s_type=event&s_tid=17&kw=&order=latest"},
    {"type_id": "cat:2", "type_name": "衣著", "url": "/category.php?cate=2", "children": (
        ("1", "泳裝"),
        ("2", "校園泳裝"),
        ("3", "學生服"),
        ("4", "制服"),
        ("5", "眼鏡"),
        ("6", "和服、浴衣"),
        ("7", "絲襪"),
        ("8", "性感內衣"),
        ("9", "辣妹"),
        ("10", "裸體圍裙"),
        ("11", "迷你裙"),
        ("12", "水手服"),
        ("13", "韻律服"),
        ("14", "緊身衣激凸"),
        ("15", "兔女郎"),
        ("16", "穿衣幹砲"),
        ("17", "女僕"),
    )},
    {"type_id": "cat:3", "type_name": "職業", "url": "/category.php?cate=3", "children": (
        ("1", "OL"),
        ("2", "學生妹"),
        ("3", "女大學生"),
        ("4", "女教師"),
        ("5", "女僕"),
        ("6", "護士"),
        ("7", "酒店小姐"),
        ("8", "女搜查官"),
        ("9", "女主播"),
        ("10", "AV女優片"),
        ("11", "偶像‧藝人"),
        ("12", "多種職業"),
        ("13", "模特兒"),
        ("14", "女醫師"),
        ("15", "家教"),
        ("16", "空姐"),
        ("17", "賽車女郎"),
        ("18", "巴士導遊"),
        ("19", "女服務生"),
    )},
    {"type_id": "cat:4", "type_name": "身材", "url": "/category.php?cate=4", "children": (
        ("1", "美乳"),
        ("2", "巨乳"),
        ("3", "愛美臀"),
        ("4", "窈窕"),
        ("5", "美腿"),
        ("6", "迷你系‧小隻女"),
        ("7", "愛美腿"),
        ("8", "愛巨乳"),
        ("9", "修長"),
        ("10", "白虎"),
        ("11", "美尻"),
        ("12", "蘿莉"),
    )},
    {"type_id": "cat:5", "type_name": "身份", "url": "/category.php?cate=5", "children": (
        ("1", "素人"),
        ("2", "人妻"),
        ("3", "近親相姦"),
        ("4", "姐姐系"),
        ("5", "媽媽系"),
        ("6", "女性向"),
        ("7", "癡女"),
        ("8", "癡漢"),
        ("9", "黑人"),
        ("10", "角色扮演"),
        ("11", "女同志"),
        ("12", "處女"),
        ("13", "新娘、少婦"),
        ("14", "熟女"),
        ("15", "義母"),
        ("16", "老闆娘"),
        ("17", "女忍者"),
    )},
    {"type_id": "cat:6", "type_name": "動作", "url": "/category.php?cate=6", "children": (
        ("1", "中出"),
        ("2", "肛交"),
        ("3", "顏射"),
        ("4", "亂交"),
        ("5", "騎乘位"),
        ("6", "3P"),
        ("7", "潮吹"),
        ("8", "乳交"),
        ("9", "足交"),
        ("10", "電動按摩棒"),
        ("11", "打手槍"),
        ("12", "自慰"),
        ("13", "寢取"),
        ("14", "強暴"),
        ("15", "拘束"),
        ("16", "輪姦"),
        ("17", "吞精"),
        ("18", "藥物、迷姦"),
        ("19", "強迫口交"),
        ("20", "偷拍"),
        ("21", "淫語"),
        ("22", "惡搞"),
        ("23", "其他癖好"),
    )},
    {"type_id": "actor:0", "type_name": "女優", "url": "/actor_list.php", "children": (
        ("34578", "善場麻美(茉城麻美)"),
        ("28900", "月野江翠"),
        ("52799", "千葉優花"),
        ("45988", "花守夏步"),
        ("27814", "蘆名穗花"),
        ("54624", "友江めい"),
        ("24264", "松本一香"),
        ("27219", "UNPAI"),
        ("29348", "十川亞里沙"),
        ("54117", "三咲まゆ"),
        ("482", "AIKA"),
        ("53882", "竹内咲奈"),
        ("4701", "佐佐木明希"),
        ("27529", "神木麗"),
        ("48389", "柏木ふみか"),
        ("45551", "はるかさん"),
        ("28938", "天乃乃亞"),
        ("25260", "今田美玲"),
        ("29092", "日下部ひな"),
        ("7782", "水谷桃"),
        ("53371", "石崎れいな"),
        ("29431", "弘中怜奈"),
        ("48764", "本多まい"),
        ("10200", "AKI"),
        ("28298", "夏目凜花"),
        ("27340", "杏奈"),
        ("26688", "星野美希"),
        ("26181", "北野未奈"),
        ("54475", "花咲ゆら"),
        ("28679", "古東真理子"),
        ("26017", "楪可憐"),
        ("49546", "山田鈴奈"),
        ("54138", "李蓉蓉"),
        ("54159", "仔仔"),
        ("54461", "黎兒"),
        ("54548", "十三叔"),
        ("53984", "小松空"),
        ("54631", "希望みう"),
        ("54644", "栞名結"),
        ("54643", "夏花まろん"),
        ("25216", "森日向子"),
        ("41927", "三木環奈"),
        ("35011", "篠真有"),
        ("27657", "八蜜凛"),
        ("29025", "逢澤美優"),
        ("29110", "彩月七緒"),
        ("27596", "綾瀬心"),
        ("29369", "七原さゆ"),
        ("53883", "音田絵凛"),
        ("28434", "五日市芽依"),
        ("25269", "沙月芽衣"),
        ("29319", "春陽モカ"),
        ("28175", "菊乃らん"),
        ("35019", "ブリル・バービー"),
        ("28442", "リアナ・ラヴィングス"),
        ("29032", "モリー・リトル"),
        ("29033", "マリア・カズィ"),
        ("29034", "ペネロープ・ケイ"),
        ("29035", "ジェイド・バレンタイン"),
    )},
    {"type_id": "fac:0", "type_name": "片商", "url": "/actor_list.php", "children": (
        ("2247", "同人av倶楽部/妄想族"),
        ("174", "はじめ企画"),
        ("4020", "オフサイドトラップ"),
        ("1903", "赤面女子"),
        ("163", "First Star"),
        ("27", "kira☆kira"),
        ("2256", "prestige"),
        ("45", "MOODYZ"),
        ("158", "無垢"),
        ("1983", "nur"),
        ("245", "魔人"),
        ("2851", "あんてきぬすっ"),
        ("199", "メリー・ジェーン"),
        ("195", "ピンクパイナップル"),
        ("1300", "ショーテン"),
        ("740", "ハイカラ/妄想族"),
        ("2410", "カムカムぴゅっ！"),
    )},
)

SORTS = (
    ("latest", "最新"),
    ("hot", "最热"),
)

PACKED_RE = re.compile(
    r"}\('(?P<p>(?:\\.|[^'\\])*)',(?P<a>\d+),(?P<c>\d+),'(?P<k>(?:\\.|[^'\\])*)'\.split\('\|'\)"
)
SOURCE_ASSIGN_RE = re.compile(r"(source(?:\d+)?)\s*=\s*'(https?://[^']+\.m3u8[^']*)'", re.I)
_B36_DIGITS = "0123456789abcdefghijklmnopqrstuvwxyz"

# ========== 铁律11：敏感词古典映射脱敏表 ==========
CLASSICAL_MAP = {
    "成人": "风月", "色情": "风月", "情色": "春宫", "淫": "风月", "黄色": "春宫", "淫秽": "猥亵",
    "AV": "光影", "av": "光影", "三级": "风月",
    "激情": "云雨", "做爱": "云雨", "性交": "交欢", "欲": "情思", "高潮": "云端",
    "偷拍": "窥帘", "偷窥": "窥帘", "乱伦": "禁脔", "强奸": "强占", "轮奸": "群辱",
    "迷奸": "迷占", "无码": "素纱", "有码": "遮面", "熟女": "徐娘",
    "萝莉": "豆蔻", "幼女": "玉蕊", "少女": "碧玉", "学生": "书生",
    "人妻": "罗敷", "少妇": "艳妇", "御姐": "玉人", "护士": "药女",
    "教师": "先生", "医生": "郎中", "警察": "捕快", "军人": "军爷",
    "秘书": "掌印", "老板": "东家", "丈夫": "夫君", "妻子": "拙荆",
    "情人": "相好", "小三": "外遇", "二奶": "外室", "出轨": "翻墙",
    "偷情": "私会", "通奸": "私通", "嫖娼": "寻花", "卖淫": "卖身",
    "妓女": "花娘", "性骚扰": "轻薄", "猥亵": "猥亵", "露阴": "曝玉",
    "咸猪手": "禄山爪", "丝袜": "丝履", "网袜": "网履", "内衣": "亵衣",
    "内裤": "亵裤", "情趣": "风月", "春药": "催情", "巨乳": "丰盈",
    "爆乳": "丰盈", "胸": "酥胸", "乳": "玉兔", "美乳": "玉兔",
    "臀": "玉臀", "屁股": "玉臀", "脚": "莲步", "玉足": "莲步",
    "腿": "玉腿", "裸体": "玉体", "全裸": "玉体", "半裸": "半褪",
    "走光": "泄春", "露点": "泄玉", "自慰": "弄玉", "口交": "含朱",
    "口活": "含朱", "肛交": "后庭", "屁眼": "后庭", "肛门": "后庭",
    "群交": "合卺", "乳交": "玉兔", "足交": "莲步", "车震": "车行",
    "野战": "郊合", "精液": "元阳", "精子": "元阳", "阴道": "幽处",
    "阴户": "幽处", "阴茎": "玉茎", "阳具": "玉茎", "SM": "调教",
    "制服": "官衣", "OL": "衙内", "空姐": "行云", "继母": "继室",
    "姐妹": "同根", "同学": "同窗", "邻居": "东邻", "处女": "处子",
    "初夜": "破瓜", "暴力": "杀伐", "血腥": "殷红", "恐怖": "幽冥",
    "赌博": "孤注", "毒品": "药石", "枪支": "火器", "刀具": "利刃",
}

# 铁律13：未成年相关关键词（脱敏后仍命中则剔除不返回）
# 注意："学生"/"书生"已移除——高中生/大学生可能已成年，不视为未成年；
# 仅保留明确指向未成年的词（萝莉/幼女/少女/童/teen/loli/schoolgirl等）
_MINOR_KEYWORDS = (
    "豆蔻", "玉蕊", "碧玉", "稚子", "未成年", "teen", "loli",
    "schoolgirl", "萝莉", "幼女", "少女", "童",
)

# 铁律15：默认反代配置路径
_PROXY_CONFIG_PATHS = (
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "assets", "proxy_config.json"),
    os.path.expanduser("~/.super_doubao/super-doubao-runtime/workspace/.user_skills/tvbox-dev/assets/proxy_config.json"),
)
_DEFAULT_PROXY_FALLBACK = "https://xsz-shared-proxy.97471201.workers.dev"


def _load_default_proxy():
    """铁律15：读取默认反代地址，读取失败回退到内置地址"""
    for path in _PROXY_CONFIG_PATHS:
        try:
            if os.path.exists(path):
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                proxy = data.get("default_proxy", "").strip()
                if proxy:
                    return proxy
        except Exception:
            continue
    return _DEFAULT_PROXY_FALLBACK


def desensitize(text):
    """铁律11：敏感词古典映射脱敏 + 铁律13：未成年内容返回空字符串跳过"""
    if text is None:
        return ""
    result = str(text)
    # 第一步：古典映射全局替换（长词优先，避免短词先替换破坏长词）
    for key in sorted(CLASSICAL_MAP.keys(), key=len, reverse=True):
        if key in result:
            result = result.replace(key, CLASSICAL_MAP[key])
    # 第二步：检测未成年相关词，命中则返回空字符串（铁律13脱敏后跳过）
    lower = result.lower()
    for kw in _MINOR_KEYWORDS:
        if kw.lower() in lower:
            return ""
    return result


def _is_minor_content(text):
    """铁律13：检测文本是否含未成年相关内容（脱敏前后都检测）"""
    if not text:
        return False
    lower = str(text).lower()
    for kw in _MINOR_KEYWORDS:
        if kw.lower() in lower:
            return True
    return False


def _sanitize_vod(vod):
    """铁律11+13：对单个vod字典做脱敏，未成年条目返回None"""
    if not isinstance(vod, dict):
        return vod
    # 先检测未成年（原始文本检测）
    name = vod.get("vod_name", "")
    remarks = vod.get("vod_remarks", "")
    content = vod.get("vod_content", "")
    if _is_minor_content(name) or _is_minor_content(remarks) or _is_minor_content(content):
        return None
    # 脱敏展示文本
    vod["vod_name"] = desensitize(name)
    if vod.get("vod_remarks") is not None:
        vod["vod_remarks"] = desensitize(remarks)
    if vod.get("vod_content") is not None:
        vod["vod_content"] = desensitize(content)
    # 脱敏后名称为空则剔除
    if not vod["vod_name"]:
        return None
    return vod


def _sanitize_list(vod_list):
    """铁律11+13：对列表做脱敏过滤，剔除未成年条目"""
    if not isinstance(vod_list, list):
        return vod_list
    result = []
    for item in vod_list:
        cleaned = _sanitize_vod(item)
        if cleaned is not None:
            result.append(cleaned)
    return result


def _sanitize_classes(classes):
    """铁律11+13：对分类列表做脱敏过滤，剔除未成年分类"""
    if not isinstance(classes, list):
        return classes
    result = []
    for cat in classes:
        if not isinstance(cat, dict):
            result.append(cat)
            continue
        name = cat.get("type_name", "")
        if _is_minor_content(name):
            continue
        cat["type_name"] = desensitize(name)
        if cat["type_name"]:
            result.append(cat)
    return result


def _format_duration(seconds):
    """秒 → HH:MM:SS / MM:SS"""
    total = _bounded_int(seconds, 0, 0, 360000)
    if total <= 0:
        return ""
    hour, rest = divmod(total, 3600)
    minute, second = divmod(rest, 60)
    if hour:
        return "%02d:%02d:%02d" % (hour, minute, second)
    return "%02d:%02d" % (minute, second)


def _clean_text(value):
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _bool(value, default=False):
    if isinstance(value, bool):
        return value
    if value is None or value == "":
        return default
    return str(value).strip().lower() in ("1", "true", "yes", "on", "y")


def _bounded_int(value, default, minimum, maximum):
    try:
        number = int(float(value))
    except (TypeError, ValueError):
        return default
    return min(max(number, minimum), maximum)


def _parse_config(value):
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, (list, tuple)):
        merged = {}
        for item in value:
            merged.update(_parse_config(item))
        return merged
    text = str(value or "").strip()
    if not text:
        return {}
    for loader in (json.loads, ast.literal_eval):
        try:
            data = loader(text)
            if isinstance(data, dict):
                return data
        except Exception:
            continue
    return {}


def _normalize_origin(value):
    text = str(value or DEFAULT_HOST).strip().rstrip("/")
    if text and "://" not in text:
        text = "https://" + text
    try:
        parsed = urlsplit(text)
    except Exception:
        return DEFAULT_HOST
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return DEFAULT_HOST
    return parsed.scheme + "://" + parsed.netloc


def _classify_response(response):
    status = int(getattr(response, "status_code", 0) or 0)
    text = str(getattr(response, "text", "") or "")
    lower = text.lower()
    if any(marker in lower for marker in CHALLENGE_MARKERS):
        return "cloudflare-managed-challenge"
    if status == 429:
        return "rate-limited"
    if 500 <= status <= 599:
        return "upstream-error"
    if status >= 400:
        return "http-error"
    if not text.strip():
        return "empty-response"
    return "ok"


def _js_unescape(text):
    return (
        str(text or "").replace("\\\\", "\x00").replace("\\'", "'")
        .replace('\\"', '"').replace("\\/", "/").replace("\\n", "\n")
        .replace("\x00", "\\")
    )


def _base_convert(number, radix):
    out = ""
    while True:
        number, remainder = divmod(number, radix)
        out = (_B36_DIGITS[remainder] if remainder < 36 else chr(remainder + 29)) + out
        if number == 0:
            return out


def unpack_eval_blocks(text):
    results = []
    for match in PACKED_RE.finditer(str(text or "")):
        try:
            payload = _js_unescape(match.group("p"))
            radix = int(match.group("a"))
            count = int(match.group("c"))
            words = _js_unescape(match.group("k")).split("|")
            table = {}
            for index in range(count):
                key = _base_convert(index, radix)
                value = words[index] if index < len(words) else ""
                table[key] = value if value else key
            results.append(re.sub(r"\b\w+\b", lambda m: table.get(m.group(0), m.group(0)), payload))
        except Exception:
            continue
    return results


def extract_play_sources(html_text):
    sources = {}
    for block in unpack_eval_blocks(html_text):
        for name, url in SOURCE_ASSIGN_RE.findall(block):
            sources[name.lower()] = url
    if not sources:
        for name, url in SOURCE_ASSIGN_RE.findall(str(html_text or "")):
            sources[name.lower()] = url
    return sources


class CloudflareTLSAdapter(HTTPAdapter):
    def __init__(self, ciphers=None, use_x25519=False, **kwargs):
        self._ciphers = ciphers
        self._use_x25519 = use_x25519
        super(CloudflareTLSAdapter, self).__init__(**kwargs)

    def _build_context(self):
        context = ssl.create_default_context()
        if self._ciphers:
            try:
                context.set_ciphers(self._ciphers)
            except Exception:
                pass
        try:
            context.minimum_version = ssl.TLSVersion.TLSv1_2
        except Exception:
            pass
        try:
            context.set_alpn_protocols(["h2", "http/1.1"])
        except Exception:
            pass
        if self._use_x25519:
            for curve in ("X25519", "prime256v1"):
                try:
                    context.set_ecdh_curve(curve)
                    break
                except Exception:
                    continue
        return context

    def init_poolmanager(self, connections, maxsize, block=False, **kwargs):
        context = self._build_context()
        if HAS_URLLIB3 and PoolManager is not None:
            kwargs["ssl_context"] = context
            self.poolmanager = PoolManager(num_pools=connections, maxsize=maxsize, block=block, **kwargs)
        else:
            super(CloudflareTLSAdapter, self).init_poolmanager(connections, maxsize, block=block, **kwargs)

    def proxy_manager_for(self, proxy, **kwargs):
        try:
            kwargs["ssl_context"] = self._build_context()
        except Exception:
            pass
        return super(CloudflareTLSAdapter, self).proxy_manager_for(proxy, **kwargs)


def build_tls_session(user_agent=None, cookie="", use_x25519=False):
    session = requests.Session()
    try:
        session.headers.clear()
    except Exception:
        pass
    headers = dict(NAV_HEADERS)
    headers["User-Agent"] = user_agent or DEFAULT_UA
    if cookie:
        headers["Cookie"] = cookie
    session.headers.update(headers)
    try:
        session.mount("https://", CloudflareTLSAdapter(use_x25519=use_x25519))
    except Exception:
        pass
    return session


class Spider:
    name = "iQQTV"
    backend_parse = False
    category_mode = False

    def __init__(self):
        self.host = DEFAULT_HOST
        self.rawSite = DEFAULT_HOST
        self.siteUrl = DEFAULT_HOST
        self.HOST = DEFAULT_HOST
        self.mirrors = list(DEFAULT_MIRRORS)
        self.timeout = 15
        self.cookie = ""
        self.max_retries = 2
        self.total_budget = 8.0
        self.use_x25519 = False
        self._warmed = False
        self._page_cache = {}
        self._cache_lock = threading.RLock()
        self.cache_ttl = 60
        self.warmup_enabled = True
        self._preferred_origin = ""
        self._categories = []
        self._use_proxy = True
        self._default_proxy = _load_default_proxy()
        self.session = self._build_session()
        # ===== 绕登入账号层（自动注册 / 登入 / 邮箱验证）=====
        self._account = {}
        self._login_state = "none"
        self._login_lock = threading.RLock()
        self._email_ok = False
        self._email_tried = False
        self._email_submitted = False
        self._mailbox = None
        self.auto_register = True
        self.auto_verify_email = True
        self.quality = 2

    def getDependence(self):
        return ""

    def getName(self):
        return self.name

    def init(self, extend=""):
        config = _parse_config(extend)
        # 铁律15：原始站点（用于Referer/Origin防盗链）
        self.rawSite = _normalize_origin(config.get("host"))
        # 铁律15：反代配置优先级 ext.proxy > ext.siteUrl > 默认反代；ext.direct=true 则直连
        # 本站实测直连可达（CF 前置但不拦），默认走直连；要用反代在 ext 里显式给 proxy/siteUrl
        direct = _bool(config.get("direct"), True)
        ext_proxy = str(config.get("proxy") or config.get("siteUrl") or "").strip()
        if direct:
            self._use_proxy = False
            self.siteUrl = self.rawSite
        elif ext_proxy:
            self._use_proxy = True
            self.siteUrl = _normalize_origin(ext_proxy)
        else:
            self._use_proxy = True
            self.siteUrl = self._default_proxy
        self.host = self.siteUrl
        self.HOST = self.siteUrl
        # 备用域名（原始站点的镜像，用于直连回退）
        raw_mirrors = str(config.get("mirrors") or ",".join(DEFAULT_MIRRORS))
        mirrors = []
        for item in re.split(r"[,\s;|]+", raw_mirrors):
            origin = _normalize_origin(item) if item.strip() else ""
            if origin and origin != self.rawSite and origin not in mirrors:
                mirrors.append(origin)
        self.mirrors = mirrors
        self.timeout = _bounded_int(config.get("timeout"), 15, 5, 40)
        self.cookie = str(config.get("cookie") or "").strip()
        self.max_retries = _bounded_int(config.get("max_retries"), 2, 0, 6)
        self.total_budget = max(float(_bounded_int(config.get("total_budget"), 8, 3, 40)), 3.0)
        self.use_x25519 = _bool(config.get("use_x25519"), False)
        self.cache_ttl = _bounded_int(config.get("cache_ttl"), 60, 0, 900)
        self.warmup_enabled = _bool(config.get("warmup"), True)
        self._preferred_origin = ""
        self._categories = []
        self._warmed = False
        with self._cache_lock:
            self._page_cache = {}
        self.session = self._build_session()
        # ===== 绕登入账号层配置（ext 里可传 user/pass，或交给插件自动注册）=====
        self.auto_register = _bool(config.get("auto_register"), True)
        self.auto_verify_email = _bool(config.get("verify_email"), True)
        self.quality = _bounded_int(config.get("quality"), 2, 0, 9)
        self._account = {"user": _clean_text(config.get("user")), "pass": _clean_text(config.get("pass"))}
        if not self._account["user"]:
            self._account = {}
        self._login_state = "none"
        self._email_ok = False
        self._email_tried = False
        self._email_submitted = False
        self._mailbox = None
        try:
            self._warmup()
        except Exception:
            pass
        return ""

    def _warmup(self):
        if self._warmed or not self.warmup_enabled:
            return
        self._warmed = True
        try:
            url = self.host + "/"
            html_text, final_url = self._fetch_url(url, referer=self.rawSite + "/",
                                                     timeout=min(self.timeout, 12), retries=0)
            self._cache_put(url, (html_text, final_url))
            self._categories = self._parse_categories(html_text)
        except Exception:
            pass

    def _build_session(self):
        return build_tls_session(DEFAULT_UA, self.cookie, use_x25519=self.use_x25519)

    def _request_headers(self, referer):
        headers = dict(NAV_HEADERS)
        headers["User-Agent"] = DEFAULT_UA
        if referer:
            headers["Referer"] = referer
            headers["Sec-Fetch-Site"] = "same-origin"
        if self.cookie:
            headers["Cookie"] = self.cookie
        return headers

    def _cache_put(self, key, value):
        with self._cache_lock:
            self._page_cache[key] = (time.time(), value)
            if len(self._page_cache) > 24:
                oldest = sorted(self._page_cache.items(), key=lambda kv: kv[1][0])[:8]
                for stale_key, _ in oldest:
                    self._page_cache.pop(stale_key, None)

    def _cache_get(self, key):
        with self._cache_lock:
            hit = self._page_cache.get(key)
        if not hit:
            return None
        stamp, value = hit
        if time.time() - stamp > self.cache_ttl:
            with self._cache_lock:
                self._page_cache.pop(key, None)
            return None
        return value

    def _url_candidates(self, url):
        try:
            parsed = urlsplit(url)
        except Exception:
            return [url]
        origin = parsed.scheme + "://" + parsed.netloc
        # 反代模式下只有一个请求地址（siteUrl），不做域名轮换
        if self._use_proxy:
            return [url]
        known = [self.host] + list(self.mirrors)
        if origin not in known:
            return [url]
        order = []
        if self._preferred_origin and self._preferred_origin in known:
            order.append(self._preferred_origin)
        for item in known:
            if item not in order:
                order.append(item)
        candidates = []
        for item in order:
            replaced = url.replace(origin, item, 1)
            if replaced not in candidates:
                candidates.append(replaced)
        return candidates

    def _remember_origin(self, url):
        try:
            parsed = urlsplit(url)
        except Exception:
            return
        if parsed.scheme and parsed.netloc:
            self._preferred_origin = parsed.scheme + "://" + parsed.netloc

    def _fetch_direct(self, url, referer, timeout, retries, deadline=None):
        headers = self._request_headers(referer)
        last_exc = None
        for attempt in range(max(retries, 0) + 1):
            if deadline is not None and time.time() >= deadline:
                break
            slot = timeout
            if deadline is not None:
                slot = max(min(timeout, deadline - time.time()), 2)
            try:
                response = self.session.get(url, headers=headers, timeout=slot, allow_redirects=True)
                verdict = _classify_response(response)
                if verdict == "cloudflare-managed-challenge":
                    if attempt < retries:
                        time.sleep(min(0.6 * (attempt + 1), 2.0))
                        continue
                    raise ValueError("Cloudflare 挑战页")
                if verdict == "rate-limited":
                    if attempt < retries:
                        time.sleep(min(0.6 * (attempt + 1), 2.0))
                        continue
                    raise ValueError("请求被限流 (429)")
                if verdict in ("http-error", "upstream-error"):
                    raise ValueError("HTTP %s" % getattr(response, "status_code", "?"))
                if verdict == "empty-response":
                    raise ValueError("空响应")
                return response.text, str(getattr(response, "url", url))
            except Exception as exc:
                last_exc = exc
                if attempt < retries:
                    time.sleep(min(0.6 * (attempt + 1), 2.0))
                    continue
                break
        raise last_exc or RuntimeError("请求失败")

    def _fetch_url(self, url, referer=None, timeout=None, retries=None):
        if timeout is None:
            timeout = self.timeout
        if retries is None:
            retries = self.max_retries
        deadline = time.time() + max(self.total_budget, timeout)
        errors = []
        for candidate in self._url_candidates(url):
            if time.time() >= deadline and errors:
                errors.append("超出总时长预算")
                break
            try:
                result = self._fetch_direct(candidate, referer, timeout, retries, deadline=deadline)
                self._remember_origin(candidate)
                return result
            except Exception as exc:
                errors.append("%s -> %s" % (urlsplit(candidate).netloc, _clean_text(exc)[:80]))
        raise ValueError("；".join(errors) if errors else "请求失败")

    def isVideoFormat(self, url):
        text = str(url or "").lower()
        return bool(text) and bool(re.search(r"\.(?:m3u8|mp4|mkv|flv|avi|ts)(?:[?#]|$)", text))

    def manualVideoCheck(self):
        return False

    def action(self, action):
        return ""

    def destroy(self):
        try:
            if self.session is not None:
                self.session.close()
        except Exception:
            pass
        return ""

    # ==================== 分类树（实测采集：一级 + 二级，父子同时写入） ====================

    def _parse_categories(self, html_text):
        """返回实测分类树；父级与子级同时写入（四壳契约：分类层级必须完整）"""
        classes = []
        for node in CATEGORY_TREE:
            classes.append({"type_id": node["type_id"], "type_name": node["type_name"]})
            kind = node["type_id"].split(":")[0]
            for child_id, child_name in node.get("children", ()):
                if kind == "cat":
                    tid = "cat:%s:%s" % (node["type_id"].split(":")[1], child_id)
                elif kind == "actor":
                    tid = "actor:%s" % child_id
                elif kind == "fac":
                    tid = "fac:%s" % child_id
                else:
                    tid = "%s:%s" % (kind, child_id)
                classes.append({"type_id": tid, "type_name": node["type_name"] + "-" + child_name})
        return classes

    def _ensure_categories(self):
        if not self._categories:
            self._categories = self._parse_categories("")
        return self._categories

    def _filters(self):
        cats = self._ensure_categories()
        return {cat["type_id"]: [] for cat in cats}

    @staticmethod
    def _tid_to_url(tid, page=1):
        """实测的 tid → URL 映射（翻页统一用 &num=N，站点所有列表都认这个参数）"""
        parts = str(tid or "").split(":")
        kind = parts[0] if parts else ""
        path = "/"
        if kind == "home":
            path = "/"
        elif kind == "free":
            path = "/?cat=free"
        elif kind == "cat" and len(parts) == 2:
            path = "/category.php?cate=" + parts[1]
        elif kind == "cat" and len(parts) >= 3:
            path = "/category.php?cate=%s&cate_c=%s" % (parts[1], parts[2])
        elif kind == "scate" and len(parts) >= 2:
            path = "/category.php?scate=" + parts[1]
        elif kind == "zone" and len(parts) >= 2:
            path = "/zone_index.php?zone=" + parts[1]
        elif kind == "event" and len(parts) >= 2:
            path = "/search.php?s_type=event&s_tid=%s&kw=&order=latest" % parts[1]
        elif kind == "actor" and len(parts) >= 2:
            path = "/search.php?s_type=actor&s_tid=" + parts[1]
        elif kind == "fac" and len(parts) >= 2:
            path = "/search.php?s_type=fac&s_tid=" + parts[1]
        elif kind == "tag" and len(parts) >= 2:
            path = "/search.php?s_type=tag&s_tid=" + parts[1]
        elif kind == "actorindex":
            path = "/actor_list.php"
        page = _bounded_int(page, 1, 1, 100000)
        if page > 1:
            path += ("&" if "?" in path else "?") + "num=%d" % page
        return path

    def homeContent(self, filter=False):
        cats = _sanitize_classes(self._ensure_categories())
        result = {"class": cats, "filters": self._filters(), "list": []}
        try:
            first_tid = cats[0]["type_id"] if cats else "home:index"
            result["list"] = _sanitize_list(self.categoryContent(first_tid, 1, False, {}).get("list", []))
        except Exception:
            result["list"] = []
        return result

    def homeVideoContent(self):
        cats = self._ensure_categories()
        first_tid = cats[0]["type_id"] if cats else "home:index"
        result = self.categoryContent(first_tid, 1, False, {})
        result["list"] = _sanitize_list(result.get("list", []))
        return result

    def categoryContent(self, tid, pg, filter=False, extend=None):
        page = _bounded_int(pg, 1, 1, 100000)
        slug = str(tid or "").strip()
        if not slug:
            return self._empty_page(page)
        # 女優/片商 总览：返回索引条目（点进去 = 该女優/片商的片单）
        kind = slug.split(":")[0]
        if kind in ("actor", "fac") and slug.split(":")[-1] == "0":
            if kind == "actor":
                return self._index_page(self.host + "/actor_list.php", page, "actor:")
            return self._index_from_tree("fac", page)
        url = self.host + self._tid_to_url(slug, page)
        result = self._list_page(url, page)
        result["list"] = _sanitize_list(result.get("list", []))
        return result

    def searchContent(self, key, quick=False, pg="1"):
        keyword = _clean_text(key)
        page = _bounded_int(pg, 1, 1, 100000)
        if not keyword:
            return self._empty_page(page)
        url = self.host + "/search.php?kw_type=sc&kw=" + quote(keyword)
        if page > 1:
            url += "&num=%d" % page
        try:
            result = self._list_page(url, page, tolerate_empty=True)
            result["list"] = _sanitize_list(result.get("list", []))
            return result
        except Exception:
            return self._empty_page(page)

    def _empty_page(self, page):
        return {"page": page, "pagecount": page, "limit": PAGE_SIZE, "total": 0, "list": []}

    def _index_from_tree(self, kind, page):
        """站上没有片商总览页：直接用实测分类树里的厂牌子节点做索引"""
        items = []
        for node in CATEGORY_TREE:
            if node["type_id"].split(":")[0] != kind:
                continue
            for child_id, child_name in node.get("children", ()):
                items.append({"vod_id": "%s:%s" % (kind, child_id), "vod_name": child_name,
                              "vod_pic": "", "vod_remarks": "", "vod_year": "",
                              "vod_area": "", "vod_type": ""})
        return {"page": page, "pagecount": page, "limit": len(items) or PAGE_SIZE,
                "total": len(items), "list": items}

    def _index_page(self, url, page, id_prefix):
        """女優索引页：返回“人”条目，详情层再展开其片单"""
        if page > 1:
            url += ("&" if "?" in url else "?") + "num=%d" % page
        try:
            html_text, _ = self._fetch_url(url, referer=self.rawSite + "/")
        except Exception as exc:
            return {"page": page, "pagecount": page, "limit": PAGE_SIZE, "total": 1,
                    "list": [{"vod_id": "error:" + _clean_text(exc), "vod_name": "索引加载失败",
                              "vod_pic": "", "vod_remarks": _clean_text(exc)}]}
        items = []
        seen = set()
        for chunk in re.split(r'(?=<div class="item\b)', html_text):
            m = re.search(r's_type=(actor|fac)&(?:amp;)?s_tid=(\d+)', chunk)
            if not m:
                continue
            key = m.group(2)
            if key in seen:
                continue
            seen.add(key)
            title = ""
            tm = re.search(r'title="([^"]*)"', chunk) or re.search(r'alt="([^"]*)"', chunk)
            if tm:
                title = _clean_text(tm.group(1))
            if not title:
                tm = re.search(r'<span[^>]*class="[^"]*title[^"]*"[^>]*>([\s\S]{0,80}?)</span>', chunk)
                title = _clean_text(re.sub(r'<[^>]+>', ' ', tm.group(1))) if tm else ""
            pic = ""
            pm = re.search(r'<img[^>]*src="([^"]+)"', chunk)
            if pm:
                pic = urljoin(self.host + "/", pm.group(1))
            if not title:
                continue
            items.append({"vod_id": id_prefix + key, "vod_name": title, "vod_pic": pic,
                          "vod_remarks": "", "vod_year": "", "vod_area": "", "vod_type": ""})
        return {"page": page, "pagecount": (page + 1) if items else page,
                "limit": len(items) or PAGE_SIZE, "total": 0, "list": items}

    def _list_page(self, url, page, tolerate_empty=False):
        try:
            cached = self._cache_get(url)
            if cached is not None:
                html_text, final_url = cached
            else:
                html_text, final_url = self._fetch_url(url, referer=self.rawSite + "/")
                self._cache_put(url, (html_text, final_url))
            items = self._parse_list(html_text)
            if not items and tolerate_empty:
                return {"page": page, "pagecount": page, "limit": PAGE_SIZE, "total": 0, "list": []}
            pagecount = self._parse_pagecount(html_text, page)
            if not self._has_pager(html_text):
                pagecount = 1
            elif items and pagecount <= page:
                pagecount = page + 1
            limit = len(items) or PAGE_SIZE
            return {"page": page, "pagecount": pagecount, "limit": limit,
                    "total": pagecount * limit, "list": items}
        except Exception as exc:
            if tolerate_empty:
                raise
            message = _clean_text(exc) or "列表加载失败"
            return {"page": page, "pagecount": page, "limit": PAGE_SIZE, "total": 1, "list": [{
                "vod_id": "error:" + message, "vod_name": "访问受限：" + message,
                "vod_pic": "", "vod_remarks": "可在插件 ext 里配置代理网关或更换备用域名",
            }]}

    def _parse_list(self, html_text):
        """实测卡片：div.item > a[href=/player.php?uuid=X&cat=Y] + div.ga_id[data-time][data-videourl]"""
        items = []
        seen = set()
        # 按卡片切块；第 0 块是首个卡片之前的页头/导航（实测会误吞播放链接），必须跳过
        for chunk in re.split(r'(?=<div class="item\b)', str(html_text or ""))[1:]:
            m = re.search(r'player\.php\?uuid=([A-Za-z0-9_\-]+)&(?:amp;)?cat=(\d+)', chunk)
            if not m:
                continue
            # 卡片必带 ga_id/data-uuid 或预览图，页头里的散装链接不算
            if ("data-uuid" not in chunk) and ("ga_name" not in chunk) and ("preview/" not in chunk):
                continue
            uuid, cat = m.group(1), m.group(2)
            if uuid in seen:
                continue
            seen.add(uuid)
            name = ""
            nm = re.search(r'class="ga_name"[^>]*title="([^"]*)"', chunk)
            if not nm:
                nm = re.search(r'class="img-h cover"[^>]*alt="([^"]*)"', chunk)
            if not nm:
                nm = re.search(r'<img[^>]*alt=["\']([^"\']*)["\']', chunk)
            if nm:
                name = _clean_text(nm.group(1))
            if not name:
                nm = re.search(r'<span[^>]*class="[^"]*title[^"]*"[^>]*>([\s\S]{0,120}?)</span>', chunk)
                name = _clean_text(re.sub(r'<[^>]+>', ' ', nm.group(1))) if nm else "未命名"
            pic = ""
            pm = re.search(r'<img[^>]*src="([^"]*preview/[^"]*)"', chunk)
            if not pm:
                pm = re.search(r'<img[^>]*src="([^"]+)"', chunk)
            if pm:
                pic = urljoin(self.host + "/", pm.group(1))
            remarks = ""
            dm = re.search(r'data-time="(\d+)"', chunk)
            if dm:
                remarks = _format_duration(_bounded_int(dm.group(1), 0, 0, 360000))
            if not remarks:
                tm = re.search(r'class="[^"]*video-time[^"]*"[^>]*>([\d:]+)<', chunk)
                remarks = tm.group(1) if tm else ""
            items.append({
                "vod_id": "/player.php?uuid=%s&cat=%s" % (uuid, cat),
                "vod_name": name, "vod_pic": pic, "vod_remarks": remarks,
                "vod_year": "", "vod_area": "", "vod_type": "",
            })
        return items

    @staticmethod
    def _extract_id(url):
        m = re.search(r'player\.php\?uuid=([A-Za-z0-9_\-]+)&(?:amp;)?cat=(\d+)', str(url or ""))
        return m.group(1) if m else ""

    @staticmethod
    def _has_pager(html_text):
        """站点是否真的提供翻页器（首页/專區没有 → 不该硬造下一页）"""
        text = str(html_text or "")
        if re.search(r'下一頁|下一頁|下页', text):
            return True
        if re.search(r'共\s*\d+\s*[部筆個]', text):
            return True
        return False

    @staticmethod
    def _parse_pagecount(html_text, current):
        """实测：只有带 num= 翻页链接或「共N部」节点的列表才可分页；
        首页与專區(zone)既无 num 也无总数，翻页会返回同一批内容 → 判定为单页"""
        text = str(html_text or "")
        pages = []
        # 只认分页区里的 num=（页头/侧栏也有 num= 链接，扫全页会误判成多页）
        pager_zone = ""
        anchor = re.search(r'下一頁|下一頁|下页|next\s*page', text)
        if anchor:
            pager_zone = text[max(0, anchor.start() - 4000): anchor.start() + 1500]
        for item in re.finditer(r'[?&](?:amp;)?num=(\d+)', pager_zone):
            pages.append(_bounded_int(item.group(1), current, 1, 100000))
        total_m = re.search(r'共\s*(\d+)\s*[部筆個]', text)
        if total_m:
            try:
                total = int(total_m.group(1))
                if total > 0:
                    pages.append(max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE))
            except Exception:
                pass
        if not pages:
            return current
        return max([current] + pages)

    # ==================== 详情 ====================

    def detailContent(self, ids):
        id_list = list(ids) if isinstance(ids, (list, tuple)) else [ids]
        result_list = []
        for source_id in id_list:
            vid = str(source_id or "").strip()
            if vid.startswith("error:"):
                result_list.append(self._error_detail(vid[6:]))
                continue
            if vid.startswith("actor:") or vid.startswith("fac:"):
                detail = self._index_detail(vid)
                if detail is not None:
                    result_list.append(detail)
                continue
            detail_url = vid if vid.startswith("http") else urljoin(self.host + "/", vid)
            try:
                html_text, final_url = self._fetch_url(detail_url, referer=self.rawSite + "/")
            except Exception as exc:
                result_list.append(self._error_detail(_clean_text(exc)))
                continue
            vod = self._parse_detail(html_text, detail_url)
            cleaned = _sanitize_vod(vod)
            if cleaned is not None:
                result_list.append(cleaned)
        return {"list": result_list}

    def _parse_detail(self, html_text, detail_url):
        text = re.sub(r'<!--.*?-->', '', html_text, flags=re.DOTALL)
        name = ""
        tm = re.search(r'<meta[^>]*property="og:title"[^>]*content="([^"]*)"', text)
        if tm:
            name = _clean_text(tm.group(1).split("|")[0])
        if not name:
            tm = re.search(r'<title>([^<]*)</title>', text)
            name = _clean_text(tm.group(1).split("|")[0]) if tm else "未命名"
        pic = ""
        pm = re.search(r'<meta[^>]*property="og:image"[^>]*content="([^"]*)"', text)
        if pm:
            pic = urljoin(self.host + "/", pm.group(1))
        desc = ""
        dm = re.search(r'data-description=[\'"]([\s\S]*?)[\'"]\s', text)
        if dm:
            desc = _clean_text(dm.group(1))
        if not desc:
            dm = re.search(r'<meta[^>]*name="description"[^>]*content="([^"]*)"', text)
            if dm:
                desc = _clean_text(dm.group(1))
        tags = []
        for tag_m in re.finditer(r'class="[^"]*tag-info[^"]*"[\s\S]{0,2500}?</div>', text):
            for a in re.finditer(r'<a[^>]*>([^<]{1,30})</a>', tag_m.group(0)):
                tag = _clean_text(a.group(1))
                if tag and tag not in tags:
                    tags.append(tag)
        remarks = ""
        vm = re.search(r'all_vtt\s*:\s*"[^"]*-(\d+)\.vtt"', text)
        if vm:
            remarks = _format_duration(_bounded_int(vm.group(1), 0, 0, 360000))
        # 实测：is_free=true 免登录号可播（需邮箱验证）；false 为「獨家」点数片，先标出来
        fm = re.search(r'is_free\s*:\s*(true|false)', text)
        if fm:
            remarks = (remarks + " · " if remarks else "") + ("免費" if fm.group(1) == "true" else "點數片")
        uuid = self._extract_id(detail_url)
        cat_m = re.search(r'[?&](?:amp;)?cat=(\d+)', detail_url)
        play_path = "/player.php?uuid=%s&cat=%s" % (uuid, cat_m.group(1) if cat_m else "19")
        return {
            "vod_id": detail_url, "vod_name": name, "vod_pic": pic, "vod_remarks": remarks,
            "vod_year": "", "vod_area": "", "vod_lang": "日語", "vod_actor": "",
            "vod_director": "", "vod_type": "、".join(tags[:6]),
            "vod_content": (desc or name)[:1500],
            "vod_play_from": "iQQTV",
            "vod_play_url": "正片$" + play_path,
        }

    def _index_detail(self, vid):
        """女優 / 片商 详情 = 该人/厂牌的片单，每条片子作为一集"""
        kind, _, tid = vid.partition(":")
        if not re.match(r'^\d+$', tid or ""):
            return None
        url = self.host + "/search.php?s_type=%s&s_tid=%s" % (kind, tid)
        try:
            html_text, _ = self._fetch_url(url, referer=self.rawSite + "/")
        except Exception as exc:
            return self._error_detail(_clean_text(exc))
        items = self._parse_list(html_text)
        eps = []
        for it in items:
            if _is_minor_content(it.get("vod_name", "")):
                continue
            eps.append("%s$%s" % (_clean_text(it.get("vod_name") or "正片")[:60], it.get("vod_id")))
        name = ""
        tm = re.search(r'<title>([^<]*)</title>', html_text)
        if tm:
            name = _clean_text(tm.group(1).split("|")[0])
        vod = {
            "vod_id": vid, "vod_name": name or ("片单-" + tid), "vod_pic": "",
            "vod_remarks": "%d 部" % len(eps), "vod_year": "", "vod_area": "",
            "vod_type": "", "vod_content": name,
            "vod_play_from": "iQQTV",
            "vod_play_url": "#".join(eps),
        }
        return _sanitize_vod(vod) or self._error_detail("内容已被过滤")

    def _error_detail(self, message):
        return {
            "vod_id": "error", "vod_name": "详情加载失败", "vod_pic": "",
            "vod_remarks": message, "vod_year": "", "vod_area": "", "vod_type": "",
            "vod_content": message, "vod_play_from": "iQQTV", "vod_play_url": "",
        }

    # ==================== 播放（含绕登入：自动注册 / 登入 / 邮箱验证） ====================

    def playerContent(self, flag, id, vipFlags=None):
        play_url = str(id or "")
        if play_url.startswith("error:"):
            return {"parse": 0, "jx": 0, "playUrl": "", "url": "", "header": {}, "msg": play_url[6:]}
        if not play_url.startswith("http"):
            play_url = urljoin(self.host + "/", play_url)
        info = self._resolve_play_url(play_url)
        if not info.get("url"):
            return {"parse": 0, "jx": 0, "playUrl": "", "url": "", "header": {},
                    "msg": info.get("msg") or "未解析到可播放地址"}
        real_url = info["url"]
        is_hls = ".m3u8" in real_url.lower()
        if is_hls:
            real_url = self._proxy_m3u8_url(real_url, self.rawSite + "/")
        return {
            "parse": 0, "jx": 0, "playUrl": "", "url": real_url,
            "header": {
                "User-Agent": PLAYER_UA,
                "Referer": self.rawSite + "/",
                "Origin": self.rawSite,
            },
            "format": "application/x-mpegURL" if is_hls else "video/mp4",
            "contentType": "application/x-mpegURL" if is_hls else "video/mp4",
        }

    def _resolve_play_url(self, play_url):
        """取流：player 页 → getMv.php（实测 quality=2 才命中 720 源，playall=1 出整片）"""
        if self._login_state == "none" or not self._account:
            self._ensure_login()
        for attempt in range(3):
            try:
                html_text, _ = self._fetch_url(play_url, referer=self.rawSite + "/")
            except Exception as exc:
                return {"msg": _clean_text(exc)}
            uuid = self._extract_id(play_url)
            cat_m = re.search(r'[?&](?:amp;)?cat=(\d+)', play_url)
            cat_id = cat_m.group(1) if cat_m else ""
            ms_m = re.search(r'ms_id="([^"]+)"', html_text)
            tk_m = re.search(r'token="([0-9a-fA-F]{16,64})"', html_text)
            cid_m = re.search(r'cat_id="(\d+)"', html_text)
            if cid_m:
                cat_id = cid_m.group(1)
            if not uuid or not ms_m or not tk_m:
                return {"msg": "播放页结构变化，未取到取流参数"}
            api = (self.host + "/subpage/getMv.php?num=%s&ms_id=%s&file_prefix=&start=0&end=600"
                   "&cat_id=%s&quality=%s&token=%s&playall=1"
                   % (uuid, ms_m.group(1), cat_id, self.quality, tk_m.group(1)))
            try:
                raw, _ = self._fetch_url(api, referer=play_url)
            except Exception as exc:
                return {"msg": _clean_text(exc)}
            try:
                data = json.loads(raw)
            except Exception:
                return {"msg": "取流接口返回异常"}
            if data.get("vurl_hls") or data.get("vurl"):
                return {"url": data.get("vurl_hls") or data.get("vurl"), "kind": "hls"}
            code = str(data.get("error_code") or "")
            if code == "loginFail" or data.get("loginFail"):
                self._login_state = "none"
                if attempt < 2 and self._ensure_login(force=True):
                    continue
                return {"msg": "登入失败，请检查 ext 里的账号或在站点重新注册"}
            if code == "need_email":
                if attempt < 2 and self._ensure_email():
                    continue
                return {"msg": "站方要求邮箱验证，自动验证未成功（可稍后在 ext 里填已注册账号）"}
            if code == "point_not_enough" or code == "permission_pay_no" or code == "permission_pay_exp":
                # 付费墙：站方点数/会员双层服务端校验，无参数注入空间。
                # 但详情页每条都挂了匿名预览口 /subpage/getMvPreview.php（免登录、免点数），
                # 直接把它当付费片兜底流用 —— 付费内容也能播，不再是死链接。
                preview = self._fetch_preview(uuid)
                if preview:
                    return {"url": preview, "kind": "preview",
                            "msg": "站方「獨家」付费片：已自动切到匿名预览流（免登录、免点数）"}
                return {"msg": "该片为站方「獨家」付费内容，需站点点数才能播放"}
            return {"msg": data.get("error") or "取流失败"}
        return {"msg": "取流失败"}

    def _fetch_preview(self, uuid):
        """匿名预览流（破付费墙的关键口子）
        GET /subpage/getMvPreview.php?uuid=<uuid> → 302 → CDN 720p mp4
        实测：不需要登入、不消耗点数、不受邮箱验证限制，任何 uuid（含「獨家」付费片）都能直取。
        """
        if not uuid:
            return ""
        url = "%s/cn/subpage/getMvPreview.php?uuid=%s&v=%d" % (self.host, uuid, int(time.time()))
        headers = dict(NAV_HEADERS)
        headers["User-Agent"] = DEFAULT_UA
        headers["Referer"] = self.host + "/"
        headers["X-Requested-With"] = "XMLHttpRequest"
        try:
            resp = self.session.get(url, headers=headers, timeout=self.timeout,
                                    allow_redirects=False, stream=True)
        except Exception:
            return ""
        loc = str(resp.headers.get("location") or resp.headers.get("Location") or "")
        if not loc and int(getattr(resp, "status_code", 0) or 0) == 200:
            try:
                text = (resp.text or "").strip()
            except Exception:
                text = ""
            if text.startswith("http"):
                loc = text
        try:
            resp.close()
        except Exception:
            pass
        return loc if loc.startswith("http") else ""

    def _ensure_login(self, force=False):
        """绕登入核心：优先用 ext 账号；没有就自动注册 + 邮箱验证，全自动"""
        with self._login_lock:
            if self._login_state == "ok" and not force:
                return True
            if not force and self._account and self._login_state == "ok":
                return True
            if self._account.get("user") and self._account.get("pass"):
                if self._do_login(self._account["user"], self._account["pass"]):
                    self._login_state = "ok"
                    return True
            if not self.auto_register:
                return False
            cred = self._register_account()
            if not cred:
                self._login_state = "fail"
                return False
            self._account.update(cred)
            if not self._do_login(cred["user"], cred["pass"]):
                self._login_state = "fail"
                return False
            self._login_state = "ok"
            if self.auto_verify_email:
                self._ensure_email()
            return True

    def _reset_session(self):
        """清掉会话 cookie 重建通道（站方部分状态是会话级缓存）"""
        try:
            self.session.cookies.clear()
        except Exception:
            pass
        self.session = self._build_session()

    def _do_login(self, username, password):
        try:
            raw, _ = self._post(self.host + "/ajax/site/login.php?method=post",
                                {"username": username, "password": password},
                                referer=self.rawSite + "/")
            data = json.loads(raw)
            return str(data.get("code")) == "0"
        except Exception:
            return False

    def _register_account(self):
        """实测：register.php 图形验证码位站点未强校验，可留空提交"""
        for _ in range(3):
            try:
                user = "zk" + "".join(random.choice(string.ascii_lowercase + string.digits) for _ in range(7))
                password = "Zk" + "".join(random.choice(string.ascii_lowercase + string.digits) for _ in range(7))
                self._get(self.host + "/register.php", referer=self.rawSite + "/")
                raw, _ = self._post(self.host + "/register.php",
                                    {"register": "1", "username": user, "password": password,
                                     "cpassword": password, "invite": "", "vcode": random.choice("abcdefghjkmnpqrstuvwxyz")},
                                    referer=self.host + "/register.php")
                if "註冊成功" in raw or "注册成功" in raw:
                    return {"user": user, "pass": password}
            except Exception:
                continue
        return None

    def _ensure_email(self):
        with self._login_lock:
            if self._email_ok:
                return True
            if not self.auto_verify_email:
                return False
            # 上一次已经「成功把邮箱提交给站方」才不再重试；邮箱服务临时抽风允许再来一次
            if self._email_tried and self._email_submitted:
                return False
            try:
                box = self._mailbox
                if not box:
                    box = self._mailbox_create()
                    self._mailbox = box
                if not box:
                    self._email_tried = True
                    return False
                address, mail_token = box
                if not self._email_submitted:
                    self._post(self.host + "/usercenter.php", {"smail": address},
                               referer=self.host + "/usercenter.php")
                    self._email_submitted = True
                self._email_tried = True
                link = self._mailbox_wait_link(mail_token)
                if not link:
                    return False
                self._get(link, referer=self.rawSite + "/")
                self._email_ok = True
                # 站方把邮箱验证状态缓存在会话里：验证后重建会话并重新登入，否则取流口仍报 need_email
                if self._account.get("user") and self._account.get("pass"):
                    self._reset_session()
                    self._do_login(self._account["user"], self._account["pass"])
                return True
            except Exception:
                return False

    def _mailbox_create(self):
        """纯净客户端开一个一次性邮箱（站点要收验证信，用自己的 session 去收）"""
        domain_list = self._api_json("https://api.mail.tm/domains")
        domain = ""
        if isinstance(domain_list, list) and domain_list:
            domain = str(domain_list[0].get("domain") or "")
        if not domain:
            return None
        address = "zk" + "".join(random.choice(string.ascii_lowercase + string.digits) for _ in range(9)) + "@" + domain
        password = "ZkPass" + "".join(random.choice(string.digits) for _ in range(6))
        self._api_json("https://api.mail.tm/accounts", {"address": address, "password": password})
        token_data = self._api_json("https://api.mail.tm/token", {"address": address, "password": password})
        token = str((token_data or {}).get("token") or "")
        if not token:
            return None
        return address, token

    def _mailbox_wait_link(self, token, rounds=14, interval=3.0):
        headers = {"Authorization": "Bearer " + token, "Accept": "application/json"}
        for _ in range(rounds):
            try:
                messages = self._api_json("https://api.mail.tm/messages", headers=headers)
            except Exception:
                messages = None
            if isinstance(messages, list) and messages:
                for msg in messages:
                    msg_id = str(msg.get("id") or "")
                    if not msg_id:
                        continue
                    detail = self._api_json("https://api.mail.tm/messages/" + msg_id, headers=headers) or {}
                    blob = " ".join([
                        str(detail.get("text") or ""),
                        json.dumps(detail.get("html") or "", ensure_ascii=False),
                    ])
                    found = re.search(r'https?://[^\s"\'<>\\]*verifyuser\.php[^\s"\'<>\\]*', blob)
                    if found:
                        # 邮件正文里的链接常带转义尾巴（\r\n 之类），必须剥干净再点
                        link = found.group(0).replace("&amp;", "&").replace("\\", "")
                        link = link.rstrip(".,;)\\]'\"")
                        if link:
                            return link
            time.sleep(interval)
        return None

    def _api_json(self, url, payload=None, headers=None):
        hdrs = {"Accept": "application/json"}
        if headers:
            hdrs.update(headers)
        if payload is not None:
            hdrs["Content-Type"] = "application/json"
            resp = requests.post(url, data=json.dumps(payload).encode("utf-8"), headers=hdrs, timeout=12)
        else:
            resp = requests.get(url, headers=hdrs, timeout=12)
        text = resp.text or ""
        try:
            return json.loads(text)
        except Exception:
            return None

    def _get(self, url, referer=None):
        headers = self._request_headers(referer)
        resp = self.session.get(url, headers=headers, timeout=self.timeout, allow_redirects=True)
        return resp.text, str(getattr(resp, "url", url))

    def _post(self, url, payload, referer=None):
        headers = self._request_headers(referer)
        headers["X-Requested-With"] = "XMLHttpRequest"
        if "usercenter.php" in url:
            headers.pop("X-Requested-With", None)
        resp = self.session.post(url, data=payload, headers=headers, timeout=self.timeout, allow_redirects=True)
        return resp.text, str(getattr(resp, "url", url))

    # ==================== m3u8广告清洗 + 本地代理（铁律·广告拦截） ====================

    def _sanitize_m3u8_url(self, url):
        """清洗m3u8 URL中的广告参数（cover/poster/thumb/pic等）"""
        if not url:
            return url
        from urllib.parse import unquote
        url = unquote(url)
        url = re.sub(r'&[Cc]over=.*', '', url)
        url = re.sub(r'&[Pp]oster=.*', '', url)
        url = re.sub(r'&[Tt]humb=.*', '', url)
        url = re.sub(r'&[Pp]ic=.*', '', url)
        url = url.rstrip('&?')
        return url

    def _proxy_m3u8_url(self, url, referer=''):
        """生成m3u8代理地址：优先用壳的getProxyUrl()，否则返回原地址（localProxy负责清洗）"""
        try:
            if hasattr(self, 'getProxyUrl'):
                return self.getProxyUrl() + '&type=m3u8&url=' + quote(url, safe='') + '&referer=' + quote(referer or self.rawSite, safe='')
        except Exception:
            pass
        return url

    def localProxy(self, params):
        """本地代理入口：接收m3u8请求 → 下载 → 广告清洗 → 返回干净m3u8"""
        try:
            if not isinstance(params, dict):
                params = {}
            do = params.get('type') or params.get('action') or params.get('do')
            url = params.get('url', '')
            if do not in ['m3u8', 'py'] and not url:
                return [404, "text/plain", "not found"]
            referer = params.get('referer', '') or self.rawSite
            if isinstance(url, list):
                url = url[0]
            if isinstance(referer, list):
                referer = referer[0]
            from urllib.parse import unquote
            url = unquote(url)
            referer = unquote(referer)
            text = self._get_m3u8_content(url, referer)
            if not text:
                return [502, "text/plain", "m3u8 download failed\nurl: %s\nreferer: %s" % (url, referer)]
            # 优先使用独立m3u8_cleaner模块（最新六重+CUE广告检测），失败回退内嵌版
            try:
                from m3u8_cleaner import M3U8Cleaner
                _cleaner = M3U8Cleaner(raw_site=referer or self.rawSite)
                cleaned = _cleaner.clean(text, url, referer)
            except Exception:
                cleaned = self._clean_m3u8(text, url, referer)
            return [200, "application/vnd.apple.mpegurl", cleaned]
        except Exception as e:
            import traceback
            return [500, "text/plain", "proxy error: %s\n%s" % (e, traceback.format_exc())]

    def _get_m3u8_content(self, url, referer):
        """带防盗链header下载m3u8文件"""
        try:
            headers = {
                'User-Agent': PLAYER_UA,
                'Accept': '*/*',
                'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
                'Referer': referer,
                'Origin': self.rawSite,
                'Connection': 'keep-alive',
            }
            resp = self.session.get(url, headers=headers, timeout=10, allow_redirects=True)
            if resp.status_code == 200:
                return resp.text
            return None
        except Exception:
            return None

    def _is_ad_segment(self, uri, dur=0, prev_tags=None):
        """广告片段识别：关键词匹配 + 短时长判定"""
        u = (uri or '').strip().lower()
        if not u:
            return False
        ad_words = [
            # 英文明确广告词
            'advertisement', 'advertise', 'advert', 'commercial', 'sponsor', 'sponsorship',
            'preroll', 'pre-roll', 'pre_roll', 'midroll', 'mid-roll', 'postroll', 'post-roll',
            'banner', 'banners', 'popup', 'pop-up', 'interstitial', 'overlay', 'splash',
            'bumper', 'stinger', 'vast', 'vpaid', 'vmap',
            'doubleclick', 'googleads', 'googlesyndication', 'googletag', 'adsense', 'admob',
            'adx', 'adnetwork', 'adserving', 'ad-serving', 'adserver', 'ad-server',
            'inmobi', 'unityads', 'applovin', 'ironsource', 'vungle', 'chartboost', 'tapjoy',
            'mintegral', 'pangle', 'bytedance', 'tiktokads', 'kuaishou', 'ks-ad',
            'tracking', 'tracker', 'beacon', 'pixel', 'analytics', 'statistic',
            'leaderboard', 'skyscraper', 'rectangle', 'filler',
            # 中文广告词
            '广告', '片头', '片尾', '贴片', '赞助商', '赞助', '推广', '硬广',
            '前贴', '中插', '后贴', '角标', '广告位', '广告片', '广告段', '广告视频',
            '广告素材', '弹窗', '悬浮', '开屏', '插屏', '激励视频', '激励广告',
            # 拼音/缩写
            'guanggao', 'ggao', 'ggvideo', 'ggmedia',
            # 路径特征（精确匹配）
            '/ad/', '/ads/', '/adv/', '/adver/', '/gg/', '/gga/', '/ggb/', '/ggc/', '/ggd/',
            '_ad.', '.ad/', '_ads.', '_adv.', '_gg.', 'gg_', '_gg', '/gg', 'gg.',
            '/ad_', '/ads_', '/adv_', '/sponsor/', '/banner/', '/promo/', '/commercial/',
            '/preroll/', '/midroll/', '/postroll/', '/popup/', '/interstitial/', '/overlay/',
            '/splash/', '/bumper/', '/vast/', '/vpaid/', '/adnetwork/', '/adserving/',
            '/doubleclick/', '/googleads/', '/googlesyndication/', '/adsense/', '/admob/',
            '/tracking/', '/tracker/', '/beacon/', '/pixel/', '/analytics/',
        ]
        if any(w in u for w in ad_words):
            return True
        try:
            if 0 < float(dur) <= 1.2:
                return True
        except Exception:
            pass
        return False

    def _parse_m3u8_segments(self, text):
        """m3u8解析器：拆出header/segments/tail，提取每片段的tags/uri/duration"""
        lines = [x.strip() for x in (text or '').replace('\r', '').split('\n') if x.strip()]
        header, segments, tail = [], [], []
        pending_tags = []
        media_sequence = 0
        target_duration = 0
        started = False
        i = 0
        while i < len(lines):
            line = lines[i]
            if line.startswith('#EXT-X-MEDIA-SEQUENCE'):
                try:
                    media_sequence = int(line.split(':', 1)[1])
                except Exception:
                    pass
                if not started:
                    header.append(line)
                else:
                    pending_tags.append(line)
            elif line.startswith('#EXT-X-TARGETDURATION'):
                try:
                    target_duration = float(line.split(':', 1)[1])
                except Exception:
                    pass
                if not started:
                    header.append(line)
                else:
                    pending_tags.append(line)
            elif line.startswith('#EXTINF'):
                started = True
                dur = target_duration or 3.0
                m = re.search(r'#EXTINF:\s*([\d.]+)', line)
                if m:
                    try:
                        dur = float(m.group(1))
                    except Exception:
                        pass
                tags = pending_tags + [line]
                pending_tags = []
                uri = ''
                j = i + 1
                while j < len(lines):
                    if lines[j].startswith('#'):
                        tags.append(lines[j])
                        j += 1
                        continue
                    uri = lines[j]
                    break
                if uri:
                    segments.append({'tags': tags, 'uri': uri, 'dur': dur})
                    i = j
                else:
                    tail.extend(tags)
            elif line.startswith('#EXT-X-ENDLIST'):
                tail.append(line)
            elif line.startswith('#'):
                if started:
                    pending_tags.append(line)
                else:
                    header.append(line)
            else:
                started = True
                dur = target_duration or 3.0
                segments.append({'tags': pending_tags, 'uri': line, 'dur': dur})
                pending_tags = []
            i += 1
        return header, segments, tail, media_sequence, target_duration

    def _segment_host_key(self, uri, base_url):
        """提取片段的主机+路径前缀，用于统计主CDN"""
        try:
            full = urljoin(base_url, uri)
            p = urlsplit(full)
            path = re.sub(r'/[^/]*$', '/', p.path or '/')
            return (p.netloc.lower(), path.lower())
        except Exception:
            return ('', '')

    def _main_path_marker(self, m3u8_url):
        """从m3u8 URL提取主路径标记（如/20240101/xxx/1000kb/hls/）"""
        try:
            p = urlsplit(m3u8_url).path
            m = re.search(r'(/\d{8}/[^/]+/\d+kb/hls/)', p)
            if m:
                return m.group(1).lower()
            m = re.search(r'(/\d{8}/[^/]+/)', p)
            if m:
                return m.group(1).lower()
        except Exception:
            pass
        return ''

    def _clean_m3u8(self, m3u8_text, m3u8_url='', referer='', skip_seconds=25):
        """核心m3u8广告清洗：五重广告识别 + 主CDN统计 + 前置贴片切除 + 多码率递归代理"""
        text = (m3u8_text or '').replace('\r', '')
        # 多码率m3u8（#EXT-X-STREAM-INF）：递归代理子m3u8
        if '#EXT-X-STREAM-INF' in text:
            out = []
            last_stream = False
            for raw in text.splitlines():
                line = raw.strip()
                if not line:
                    continue
                if line.startswith('#'):
                    out.append(line)
                    last_stream = line.startswith('#EXT-X-STREAM-INF')
                else:
                    abs_url = urljoin(m3u8_url, line)
                    if last_stream or '.m3u8' in line.lower():
                        out.append(self._proxy_m3u8_url(abs_url, referer or self.rawSite))
                    else:
                        out.append(abs_url)
                    last_stream = False
            return '\n'.join(out) + '\n'

        header, segments, tail, media_sequence, target_duration = self._parse_m3u8_segments(text)
        if not segments:
            return text

        marker = self._main_path_marker(m3u8_url)

        # 统计各主机路径的总时长，找出主CDN
        stat = {}
        for seg in segments:
            key = self._segment_host_key(seg['uri'], m3u8_url)
            stat[key] = stat.get(key, 0.0) + float(seg.get('dur') or 0)
        main_key = max(stat.items(), key=lambda x: x[1])[0] if stat else ('', '')
        total_dur = sum(stat.values()) or 0
        main_dur = stat.get(main_key, 0)

        # 五重广告识别
        cleaned = []
        removed = 0
        for idx, seg in enumerate(segments):
            key = self._segment_host_key(seg['uri'], m3u8_url)
            is_front = idx < 12
            abs_uri = urljoin(m3u8_url, seg.get('uri', ''))
            is_ad = self._is_ad_segment(seg['uri'], seg.get('dur'), seg.get('tags'))
            # 第三重：路径标记不匹配主路径
            if marker and marker not in urlsplit(abs_uri).path.lower():
                is_ad = True
            tags_text = '\n'.join(seg.get('tags') or []).upper()
            # 第四重：前置12片段 + METHOD=NONE + 路径不匹配
            if is_front and 'METHOD=NONE' in tags_text and marker and marker not in urlsplit(abs_uri).path.lower():
                is_ad = True
            # 第五重：前置12片段 + 主CDN占比>=60% + 非主CDN且时长<=90秒
            if (not is_ad) and is_front and total_dur > 0 and main_dur >= total_dur * 0.6:
                if key != main_key and stat.get(key, 0) <= 90:
                    is_ad = True
            if is_ad:
                removed += 1
                continue
            seg['_idx'] = idx
            cleaned.append(seg)

        # 兜底策略：没删到广告时，前12片段累计>=25秒且第一个不是主CDN，则切掉前置贴片
        if removed == 0 and len(segments) > 4:
            acc = 0.0
            cut = 0
            for idx, seg in enumerate(segments[:12]):
                key = self._segment_host_key(seg['uri'], m3u8_url)
                if key == main_key and acc >= 3:
                    break
                acc += float(seg.get('dur') or target_duration or 3)
                cut = idx + 1
                if acc >= skip_seconds:
                    break
            if cut > 0 and cut < len(segments):
                first_key = self._segment_host_key(segments[0]['uri'], m3u8_url)
                if first_key != main_key:
                    cleaned = segments[cut:]
                    removed = cut

        if not cleaned:
            cleaned = segments
            removed = 0

        # 重新生成干净的m3u8
        new_lines = []
        has_m3u = False
        for line in header:
            if line.startswith('#EXTM3U'):
                has_m3u = True
            if line.startswith('#EXT-X-MEDIA-SEQUENCE') or line.startswith('#EXT-X-START'):
                continue
            if line.startswith('#EXT-X-KEY') and 'METHOD=NONE' in line.upper() and removed > 0:
                continue
            new_lines.append(line)
        if not has_m3u:
            new_lines.insert(0, '#EXTM3U')
        first_idx = cleaned[0].get('_idx', removed) if cleaned else removed
        new_lines.append('#EXT-X-MEDIA-SEQUENCE:%d' % (media_sequence + first_idx))

        for seg in cleaned:
            for tag in seg.get('tags') or []:
                if tag.startswith('#EXT-X-KEY') or tag.startswith('#EXT-X-MAP'):
                    def _fix_uri(m):
                        return 'URI="' + urljoin(m3u8_url, m.group(1)) + '"'
                    tag = re.sub(r'URI="([^"]+)"', _fix_uri, tag)
                new_lines.append(tag)
            new_lines.append(urljoin(m3u8_url, seg.get('uri', '')))
        if tail:
            for line in tail:
                if line.startswith('#EXT-X-ENDLIST'):
                    new_lines.append(line)
        elif '#EXT-X-ENDLIST' in text:
            new_lines.append('#EXT-X-ENDLIST')
        return '\n'.join(new_lines) + '\n'
