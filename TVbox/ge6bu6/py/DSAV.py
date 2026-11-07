# -*- coding: utf-8 -*-
"""
DSAV（dsav26.com）· TVBox Python 插件（纯标准库）
========================================================================
【站情 · 2026-10-02 实地探站】
  前端是 Next.js(turbopack) 打包的 CSR 单页，首页 HTML 里**一条数据都没有**，
  还挂着一层「我是成年人」年龄门 —— 过门之前一个业务请求都不发。
  站池：dsav26/27/28/29/30.com 与 dsav.app 同一套后端。

【接口 · 一个「加密网关」】
      POST https://api<6位随机>.<230110|230220|230330>.xyz/v1
  ★ URL 里**没有路由** —— 路由（/config、/videos/search、/videos/watch）写在密文里。
  站方还会在 "api." 前插一段本地生成、缓存 24h 的 6 位随机子域，所以域池要现拼。

【加密层（本插件最核心的一段，全部从混淆代码里扒出来 + 拿真包逐字节对过）】
  ① AES-128-CBC / PKCS7
        key = "OPQT123412FRANME"（16 字节）   iv = "MRDCQP12QPM13412"（16 字节）
        输出**标准 Base64**（+ / =），不是 URL-safe。
  ② 信封 {"sign","nonce","timestamp","data"}
        data      = AES(明文 JSON)
        timestamp = 秒级时间戳
        nonce     = uuid4
        sign      = MD5( Base64(明文) + nonce + timestamp + SALT )
     ★ 实测：本插件拼出来的信封跟浏览器发的**逐字节完全一致**（AES 与 sign 都对得上）。
  ③ 除 /config 外还要一套 RFC9421 风格的 HTTP 签名头（站方自研，两个坑都在这儿）：
        Content-Digest  = sha-256=:<base64(sha256(body))>:
        Signature-Input = v1=("@method" "@target-uri" "content-digest" "@created");created=<ts>;keyid="<id>";alg="hmac-sha256"
        待签串（\n 拼接，**最后一行后面没有换行**）：
            "@method": POST
            "@target-uri": <route>
            content-digest: sha-256=:…:          ← ★这行**不带引号**（只有 @ 开头的组件才加引号）
            "@created": <ts>
            "@signature-params": ("@method" "@target-uri" "content-digest" "@created");created=…;keyid="…";alg="hmac-sha256"
        Signature       = v1=:<base64(HMAC-SHA256(待签串, key))>:
     ★★ 两个坑：
        a) content-digest 那行不能加引号 —— 加了服务端一律回 code 3000 param error；
        b) HMAC 的 key 是 **http_sign_key 这串 base64 文本的 UTF-8 字节**，
           不是把它 base64 解码后的 32 字节（按常规 RFC9421 实现会一直验不过）。
     /config 这一发不签名也不校验（站方第一行就 return {}）。

【播放链 · DASH + 逐片时效签名】
  详情直给：
      https://oss.dsav.app/mpd/<日期>/<hash>/index.mpd       ← MPD 本体裸拉 200（不用签名）
      .../4000k/init-stream0.m4s
      .../4000k/chunk-stream0-00001.m4s                      ← ★分片必须签名，否则 403
  分片签名（站方 videoSign，从混淆里还原）：
      sign = base64url_nopad( MD5( SALT + <pathname> + <expire> ) )
      expire 取 /config 的 video_sign_expire
  实测：拿真浏览器抓到的 sign 逐条对过，**一模一样**。
  ★ 所以「把 MPD 原样丢给播放器」播不了（播放器不会算签名）。本插件的解法：
    起一个只监听 127.0.0.1 的极小 HTTP 服务，把 MPD 改写成
    **SegmentList + 每片已签名的绝对地址**再吐给播放器 —— 分片直连 CDN，不占中转带宽；
    同时还生成一条 HLS 线路（master + 视频/音频媒体清单，同样全签名），
    给「HLS 优先、不认 DASH」的壳用。服务起不来时退回原始 MPD（至少地址是对的）。

【分类】/config 的 video_type 给 12 个大类，本插件原样保留站方分类名。

【纯标准库】AES/MD5/HMAC 全部手写（壳里没有密码库），无第三方依赖。
自检: python3 DSAV-TVBox插件.py
"""

import base64
import hashlib
import hmac
import json
import re
import socket
import sys
import threading
import time
import urllib.parse
import urllib.request
import uuid

try:                                              # TVBox 壳内基类
    from base.spider import Spider as _Spider
except Exception:
    class _Spider(object):                        # 原生 python 下自检用
        def init(self, *a, **kw):
            return self

# ---------------------------------------------------------------- 常量
SITE_NAME = 'DSAV'
UA = ('Mozilla/5.0 (Linux; Android 12) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36')

WEB_POOL = ['https://dsav26.com', 'https://dsav27.com', 'https://dsav.app']
API_HOSTS = ['https://api.230110.xyz/v1', 'https://api.230220.xyz/v1', 'https://api.230330.xyz/v1']
OSS = 'https://oss.dsav.app'                       # 媒体域（MPD 与分片都在它上面）

AES_KEY = b'OPQT123412FRANME'
AES_IV = b'MRDCQP12QPM13412'
SIGN_SALT = 'AjPuom638LmWfWyeM5YueKuJ9PuWLdRn'     # apiSign / videoSign 共用盐

PAGE = 24
LANG = 'zh-tw'

# 网关随机子域：一次进程一次（站方也是本地生成、缓存 24h）
_SEED = hashlib.md5(('%d-%d' % (time.time(), id(object()))).encode()).hexdigest()[:6]

_CFG = {'data': None, 'at': 0.0}


# ================================================================ 纯标准库 AES-128-CBC
def _init_sbox():
    p = q = 1
    sbox = [0] * 256
    while True:
        p = p ^ ((p << 1) & 0xFF) ^ (0x1B if p & 0x80 else 0)
        q ^= q << 1
        q ^= q << 2
        q ^= q << 4
        q &= 0xFF
        if q & 0x80:
            q ^= 0x09
        x = q ^ ((q << 1) | (q >> 7)) ^ ((q << 2) | (q >> 6)) ^ ((q << 3) | (q >> 5)) ^ ((q << 4) | (q >> 4))
        sbox[p] = (x ^ 0x63) & 0xFF
        if p == 1:
            break
    sbox[0] = 0x63
    inv = [0] * 256
    for i, v in enumerate(sbox):
        inv[v] = i
    return sbox, inv


SBOX, INV_SBOX = _init_sbox()
RCON = [0x01, 0x02, 0x04, 0x08, 0x10, 0x20, 0x40, 0x80, 0x1B, 0x36]


def _xtime(a):
    a <<= 1
    return (a ^ 0x1B) & 0xFF if a & 0x100 else a


def _mul(a, b):
    r = 0
    while b:
        if b & 1:
            r ^= a
        a = _xtime(a)
        b >>= 1
    return r & 0xFF


def _expand_key(key):
    nk = len(key) // 4
    nr = nk + 6
    w = [list(key[4 * i:4 * i + 4]) for i in range(nk)]
    for i in range(nk, 4 * (nr + 1)):
        t = list(w[i - 1])
        if i % nk == 0:
            t = t[1:] + t[:1]
            t = [SBOX[b] for b in t]
            t[0] ^= RCON[i // nk - 1]
        w.append([w[i - nk][j] ^ t[j] for j in range(4)])
    return w, nr


_W = {}


def _ks(key):
    if key not in _W:
        _W[key] = _expand_key(key)
    return _W[key]


def _ark(s, w, rnd):
    for c in range(4):
        for r in range(4):
            s[r][c] ^= w[rnd * 4 + c][r]


def _enc_blk(blk, w, nr):
    s = [[blk[r + 4 * c] for c in range(4)] for r in range(4)]
    _ark(s, w, 0)
    for rnd in range(1, nr + 1):
        for r in range(4):
            for c in range(4):
                s[r][c] = SBOX[s[r][c]]
        for r in range(1, 4):
            s[r] = s[r][r:] + s[r][:r]
        if rnd != nr:
            for c in range(4):
                a = [s[r][c] for r in range(4)]
                s[0][c] = _mul(a[0], 2) ^ _mul(a[1], 3) ^ a[2] ^ a[3]
                s[1][c] = a[0] ^ _mul(a[1], 2) ^ _mul(a[2], 3) ^ a[3]
                s[2][c] = a[0] ^ a[1] ^ _mul(a[2], 2) ^ _mul(a[3], 3)
                s[3][c] = _mul(a[0], 3) ^ a[1] ^ a[2] ^ _mul(a[3], 2)
        _ark(s, w, rnd)
    return bytes(s[r][c] for c in range(4) for r in range(4))


def _dec_blk(blk, w, nr):
    s = [[blk[r + 4 * c] for c in range(4)] for r in range(4)]
    _ark(s, w, nr)
    for rnd in range(nr - 1, -1, -1):
        for r in range(1, 4):
            s[r] = s[r][-r:] + s[r][:-r]
        for r in range(4):
            for c in range(4):
                s[r][c] = INV_SBOX[s[r][c]]
        _ark(s, w, rnd)
        if rnd != 0:
            for c in range(4):
                a = [s[r][c] for r in range(4)]
                s[0][c] = _mul(a[0], 14) ^ _mul(a[1], 11) ^ _mul(a[2], 13) ^ _mul(a[3], 9)
                s[1][c] = _mul(a[0], 9) ^ _mul(a[1], 14) ^ _mul(a[2], 11) ^ _mul(a[3], 13)
                s[2][c] = _mul(a[0], 13) ^ _mul(a[1], 9) ^ _mul(a[2], 14) ^ _mul(a[3], 11)
                s[3][c] = _mul(a[0], 11) ^ _mul(a[1], 13) ^ _mul(a[2], 9) ^ _mul(a[3], 14)
    return bytes(s[r][c] for c in range(4) for r in range(4))


def aes_encrypt(plain, key=AES_KEY, iv=AES_IV):
    """明文 → 标准 Base64 密文（AES-128-CBC / PKCS7）"""
    data = plain.encode('utf-8')
    pad = 16 - (len(data) % 16)
    data += bytes([pad]) * pad
    w, nr = _ks(key)
    prev = iv
    out = b''
    for i in range(0, len(data), 16):
        blk = bytes(a ^ b for a, b in zip(data[i:i + 16], prev))
        prev = _enc_blk(blk, w, nr)
        out += prev
    return base64.b64encode(out).decode()


def aes_decrypt(b64, key=AES_KEY, iv=AES_IV):
    """标准 Base64 密文 → 明文"""
    try:
        data = base64.b64decode((b64 or '').strip())
        w, nr = _ks(key)
        prev = iv
        out = b''
        for i in range(0, len(data), 16):
            blk = data[i:i + 16]
            out += bytes(a ^ b for a, b in zip(_dec_blk(blk, w, nr), prev))
            prev = blk
        if out:
            out = out[:-out[-1]]
        return out.decode('utf-8', 'replace')
    except Exception:
        return ''


# ================================================================ 签名
def api_sign(plain, nonce, ts):
    """sign = MD5( Base64(明文) + nonce + timestamp + SALT )"""
    raw = base64.b64encode(plain.encode('utf-8')).decode() + nonce + str(ts) + SIGN_SALT
    return hashlib.md5(raw.encode('utf-8')).hexdigest()


def video_sign(pathname, expire):
    """分片签名：sign = base64url_nopad( MD5( SALT + path + expire ) )"""
    h = hashlib.md5((SIGN_SALT + pathname + str(expire)).encode('utf-8')).digest()
    return 'sign=' + base64.urlsafe_b64encode(h).decode().rstrip('=') + '&expire=' + str(expire)


def seg_url(pathname, expire):
    return OSS + pathname + '?' + video_sign(pathname, expire)


def http_sign(body, route, sign_id, sign_key):
    """RFC9421 风格（站方自研）—— content-digest 行不带引号；HMAC key 用 base64 文本自身字节"""
    created = int(time.time())
    digest = 'sha-256=:' + base64.b64encode(hashlib.sha256(body).digest()).decode() + ':'
    params = ('("@method" "@target-uri" "content-digest" "@created")'
              ';created=%d;keyid="%s";alg="hmac-sha256"' % (created, sign_id))
    base = '\n'.join([
        '"@method": POST',
        '"@target-uri": %s' % route,
        'content-digest: %s' % digest,
        '"@created": %d' % created,
        '"@signature-params": %s' % params,
    ])
    sig = base64.b64encode(hmac.new(sign_key.encode('utf-8'), base.encode('utf-8'),
                                    hashlib.sha256).digest()).decode()
    return {
        'Content-Digest': digest,
        'Signature-Input': 'v1=' + params,
        'Signature': 'v1=:%s:' % sig,
    }


# ================================================================ 网关
def _api_hosts():
    return [h.replace('api.', 'api' + _SEED + '.') for h in API_HOSTS]


def _referer():
    return WEB_POOL[0] + '/'


def _post(url, body, headers, timeout=20):
    req = urllib.request.Request(url, data=body, method='POST')
    for k, v in headers.items():
        req.add_header(k, v)
    try:
        import ssl
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
            return r.read()
    except TypeError:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read()


def _get(url, referer=None, timeout=20):
    req = urllib.request.Request(url)
    req.add_header('User-Agent', UA)
    req.add_header('Accept', '*/*')
    if referer:
        req.add_header('Referer', referer)
    try:
        import ssl
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
            return r.read()
    except TypeError:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read()


def api(route, payload, _depth=0):
    """打一发网关，返回解密后的业务 JSON 文本（失败给空串）"""
    p = dict(payload or {})
    p['lang'] = LANG
    p['route'] = route
    plain = json.dumps(p, separators=(',', ':'), ensure_ascii=False)
    nonce = str(uuid.uuid4())
    ts = int(time.time())
    env = {
        'sign': api_sign(plain, nonce, ts),
        'nonce': nonce,
        'timestamp': ts,
        'data': aes_encrypt(plain),
    }
    body = json.dumps(env, separators=(',', ':'), ensure_ascii=False).encode('utf-8')
    hd = {
        'User-Agent': UA,
        'Accept': 'application/json',
        'Content-Type': 'application/json',
        'Referer': _referer(),
        'Accept-Language': 'en-US,en;q=0.9',
    }
    if route != '/config':
        c = cfg()
        if c and c.get('http_sign_flag') and c.get('http_sign_id') and c.get('http_sign_key'):
            hd.update(http_sign(body, route, c['http_sign_id'], c['http_sign_key']))
    for h in _api_hosts():
        try:
            raw = _post(h, body, hd)
            j = json.loads(raw.decode('utf-8'))
            if j.get('code') != 200:
                continue
            d = j.get('data') or ''
            if len(d) < 8:
                continue
            out = aes_decrypt(d)
            if out:
                return out
        except Exception:
            continue
    return ''


def cfg():
    """站点配置（分类表 / 签名密钥 / 签名有效期），成功一次常驻 30 分钟"""
    if _CFG['data'] is not None and time.time() - _CFG['at'] < 1800:
        return _CFG['data']
    js = api('/config', {})
    if len(js) < 20:
        return _CFG['data']
    try:
        o = json.loads(js)
    except Exception:
        return _CFG['data']
    _CFG['data'] = o
    _CFG['at'] = time.time()
    return o


def expire():
    c = cfg()
    if not c:
        return str(int(time.time()) + 86400)
    e = c.get('video_sign_expire') or 0
    try:
        e = int(e)
    except Exception:
        e = 0
    return str(e if e > 0 else int(time.time()) + 86400)


# ================================================================ MPD 解析
P_ADAPT = re.compile(r'<AdaptationSet\b([^>]*)>(.*?)</AdaptationSet>', re.S)
P_REP = re.compile(r'<Representation\b([^>]*?)/?>', re.S)
P_TMPL = re.compile(r'<SegmentTemplate\b([^>]*?)/?>', re.S)
P_TL = re.compile(r'<SegmentTimeline>(.*?)</SegmentTimeline>', re.S)
P_S = re.compile(r'<S\b([^>]*?)/?>', re.S)
P_BASE = re.compile(r'<BaseURL>([^<]+)</BaseURL>')


def _attr(attrs, name):
    m = re.search(r'(?:^|[\s/])' + name + r'\s*=\s*"([^"]*)"', attrs or '')
    return m.group(1) if m else ''


def _fill(tpl, rep_id, number):
    s = (tpl or '').replace('$RepresentationID$', rep_id)

    def _rep(m):
        w = m.group(1)
        v = str(number)
        if w:
            v = v.zfill(int(w))
        return v
    return re.sub(r'\$Number(?:%(\d+)d)?\$', _rep, s)


def _path_of(d, rel):
    r = (rel or '').strip()
    if r.startswith('http'):
        return urllib.parse.urlparse(r).path or '/'
    r = r.lstrip('/')
    if d:
        return (d if d.endswith('/') else d + '/') + r
    return '/' + r


def parse_mpd(mpd, mpd_url, exp):
    """
    把 SegmentTemplate + SegmentTimeline 展开成逐片地址并签名。
    ★ initialization / media 是**相对 MPD 所在目录**的，必须拼上 /mpd/<日期>/<hash>/ 前缀，
      否则拿到的是 https://oss.dsav.app/4000k/... → 404。
    """
    d = ''
    if mpd_url:
        u = mpd_url.split('?')[0]
        if '/' in u:
            path = urllib.parse.urlparse(u).path
            d = path.rsplit('/', 1)[0] + '/'
    mb = P_BASE.search(mpd or '')
    if mb:
        b = mb.group(1).strip()
        if b.startswith('http'):
            d = urllib.parse.urlparse(b).path
            if not d.endswith('/'):
                d += '/'
        elif d:
            d = d + (b if b.endswith('/') else b + '/')

    out = []
    for ma in P_ADAPT.finditer(mpd or ''):
        a_attrs, body = ma.group(1), ma.group(2)
        ct = _attr(a_attrs, 'contentType')
        if not ct:
            mr0 = P_REP.search(body)
            if mr0:
                mt = _attr(mr0.group(1), 'mimeType')
                ct = 'audio' if mt.startswith('audio') else 'video'
        mt = P_TMPL.search(body)
        if not mt:
            continue
        t_attrs = mt.group(1)
        init_tpl = _attr(t_attrs, 'initialization')
        media_tpl = _attr(t_attrs, 'media')
        if not media_tpl:
            continue
        try:
            timescale = int(_attr(t_attrs, 'timescale') or 1000)
        except Exception:
            timescale = 1000
        if timescale <= 0:
            timescale = 1000
        try:
            start_num = int(_attr(t_attrs, 'startNumber') or 1)
        except Exception:
            start_num = 1
        if start_num <= 0:
            start_num = 1

        ds = []
        ml = P_TL.search(body)
        if ml:
            for ms in P_S.finditer(ml.group(1)):
                sa = ms.group(1)
                try:
                    dv = int(_attr(sa, 'd') or 0)
                except Exception:
                    dv = 0
                try:
                    rv = int(_attr(sa, 'r') or 0)
                except Exception:
                    rv = 0
                if dv <= 0:
                    continue
                ds.extend([dv] * (rv + 1))
                if len(ds) > 20000:
                    break

        for mr in P_REP.finditer(body):
            r_attrs = mr.group(1)
            rep_id = _attr(r_attrs, 'id') or '0'
            tk = {
                'rep': int(rep_id) if rep_id.isdigit() else len(out),
                'kind': 'audio' if ct == 'audio' else 'video',
                'codecs': _attr(r_attrs, 'codecs'),
                'bandwidth': int(_attr(r_attrs, 'bandwidth') or 0),
                'width': int(_attr(r_attrs, 'width') or 0),
                'height': int(_attr(r_attrs, 'height') or 0),
                'init': seg_url(_path_of(d, _fill(init_tpl, rep_id, start_num)), exp) if init_tpl else '',
                'segs': [],
                'durs': [],
                'total': 0.0,
            }
            acc = 0.0
            for i, dv in enumerate(ds):
                path = _path_of(d, _fill(media_tpl, rep_id, start_num + i))
                tk['segs'].append(seg_url(path, exp))
                dur = dv / float(timescale)
                tk['durs'].append(dur)
                acc += dur
            tk['total'] = acc
            if tk['segs']:
                out.append(tk)
    return out


def build_mpd(mpd, tracks):
    """原 MPD → SegmentList（每条分片都是已签名的绝对地址）"""
    try:
        cut = mpd.find('<Period')
        if cut < 0:
            return mpd
        p_end = mpd.find('>', cut)
        sb = [mpd[:p_end + 1], '\n']
        for i, t in enumerate(tracks):
            if not t['init']:
                continue
            sb.append('\t\t<AdaptationSet id="%d" contentType="%s" segmentAlignment="true" startWithSAP="1">\n'
                      % (i, t['kind']))
            sb.append('\t\t\t<Representation id="%d" mimeType="%s"' % (t['rep'], 'audio/mp4' if t['kind'] == 'audio' else 'video/mp4'))
            if t['codecs']:
                sb.append(' codecs="%s"' % t['codecs'])
            if t['bandwidth']:
                sb.append(' bandwidth="%d"' % t['bandwidth'])
            if t['width']:
                sb.append(' width="%d" height="%d"' % (t['width'], t['height']))
            sb.append('>\n\t\t\t\t<SegmentList timescale="1000" duration="0">\n')
            sb.append('\t\t\t\t\t<Initialization sourceURL="%s"/>\n' % t['init'].replace('&', '&amp;'))
            for u in t['segs'][:4000]:
                sb.append('\t\t\t\t\t<SegmentURL media="%s"/>\n' % u.replace('&', '&amp;'))
            sb.append('\t\t\t\t</SegmentList>\n\t\t\t</Representation>\n\t\t</AdaptationSet>\n')
        sb.append('\t</Period>\n</MPD>\n')
        return ''.join(sb)
    except Exception:
        return mpd


def _pick(tracks):
    v = a = None
    for t in tracks:
        if t['kind'] == 'audio':
            if a is None:
                a = t
        elif v is None or t['bandwidth'] > v['bandwidth']:
            v = t
    return v, a


def build_master(tracks, port, u_b64=''):
    """
    HLS master。
    ★ 媒体清单地址必须带上 u=<b64(mpd 地址)> —— 这样媒体清单那一发自己就能把 MPD 拉回来解析，
      不依赖任何内存状态，也就不会出现「master 指向 A 服务、媒体清单却在 B 服务上」的串台（踩过）。
    """
    v, a = _pick(tracks)
    if not v and not a:
        return ''
    uq = ('&u=' + u_b64) if u_b64 else ''
    a_url = v_url = ''
    if a:
        a_url = 'http://127.0.0.1:%d/zs?t=dsavm3u8&rep=%d%s' % (port, a['rep'], uq)
    if v:
        v_url = 'http://127.0.0.1:%d/zs?t=dsavm3u8&rep=%d%s' % (port, v['rep'], uq)
    sb = ['#EXTM3U', '#EXT-X-VERSION:7']
    if a and a_url:
        sb.append('#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="aud",NAME="音轨",DEFAULT=YES,AUTOSELECT=YES,URI="%s"' % a_url)
    main = v or a
    line = '#EXT-X-STREAM-INF:BANDWIDTH=%d' % (main['bandwidth'] + (a['bandwidth'] if a else 0))
    if main['width']:
        line += ',RESOLUTION=%dx%d' % (main['width'], main['height'])
    if main['codecs']:
        cs = main['codecs']
        if a and a['codecs'] and a['codecs'] != cs:
            cs += ',' + a['codecs']
        line += ',CODECS="%s"' % cs
    if a and a_url:
        line += ',AUDIO="aud"'
    sb.append(line)
    sb.append(v_url if v_url else a_url)
    return '\n'.join(sb) + '\n'


def build_media(t):
    mx = max(t['durs']) if t['durs'] else 8.0
    target = int(mx) + (1 if mx > int(mx) else 0)
    sb = ['#EXTM3U', '#EXT-X-VERSION:7', '#EXT-X-PLAYLIST-TYPE:VOD',
          '#EXT-X-TARGETDURATION:%d' % max(1, target), '#EXT-X-MEDIA-SEQUENCE:0']
    if t['init']:
        sb.append('#EXT-X-MAP:URI="%s"' % t['init'])
    for i, u in enumerate(t['segs']):
        sb.append('#EXTINF:%.3f,' % t['durs'][i])
        sb.append(u)
    sb.append('#EXT-X-ENDLIST')
    return '\n'.join(sb) + '\n'


# ================================================================ 本机中转（127.0.0.1）
# 只起**一台**服务，三条路由：
#   ?t=dsavmpd  &u=<b64(mpd)>            → 改写成 SegmentList 的 DASH 清单（每片已签名）
#   ?t=dsavhls  &u=<b64(mpd)>            → HLS master（视频/音频各一条媒体清单）
#   ?t=dsavm3u8 &u=<b64(mpd)>&rep=<id>   → 某条媒体清单
# 分片本体**不走中转**（清单里就是 CDN 绝对地址），所以中转只吃几段文本。
_SRV = {'sock': None, 'port': 0, 'lock': threading.Lock()}
_MPDCACHE = {}


def _b64u(s):
    return base64.urlsafe_b64encode(s.encode('utf-8')).decode().rstrip('=')


def _b64u_dec(s):
    s = s + '=' * (-len(s) % 4)
    try:
        return base64.urlsafe_b64decode(s.encode()).decode('utf-8', 'replace')
    except Exception:
        return ''


def _mpd_of(u_b64):
    """按 mpd 地址取回并解析（URL 级缓存 20 分钟，签名有效期很长）"""
    u = _b64u_dec(u_b64) if u_b64 else ''
    if len(u) < 20:
        return '', []
    hit = _MPDCACHE.get(u)
    if hit and time.time() - hit[0] < 1200:
        return hit[1], hit[2]
    try:
        mpd = _get(u, referer=_referer()).decode('utf-8', 'replace')
    except Exception:
        return '', []
    if '<MPD' not in mpd:
        return '', []
    tracks = parse_mpd(mpd, u, expire())
    _MPDCACHE[u] = (time.time(), mpd, tracks)
    return mpd, tracks


def _handle(conn):
    try:
        conn.settimeout(20)
        buf = b''
        while (b'\r\n\r\n') not in buf:
            chunk = conn.recv(4096)
            if not chunk:
                break
            buf += chunk
            if len(buf) > 65536:
                break
        head = buf.split(b'\r\n')[0].decode('latin1', 'replace')
        parts = head.split(' ')
        if len(parts) < 2:
            return
        qs = urllib.parse.parse_qs(urllib.parse.urlparse(parts[1]).query)
        typ = (qs.get('t') or qs.get('type') or [''])[0]
        ub = (qs.get('u') or [''])[0]
        try:
            rep = int((qs.get('rep') or ['0'])[0])
        except Exception:
            rep = 0

        text = ''
        ctype = 'text/plain'
        if typ in ('dsavmpd', 'dsavhls', 'dsavm3u8'):
            mpd, tracks = _mpd_of(ub)
            if typ == 'dsavmpd' and tracks:
                text = build_mpd(mpd, tracks)
                ctype = 'application/dash+xml'
            elif typ == 'dsavhls' and tracks:
                text = build_master(tracks, _SRV['port'], ub)
                ctype = 'application/vnd.apple.mpegurl'
            elif typ == 'dsavm3u8' and tracks:
                t = None
                for x in tracks:
                    if x['rep'] == rep:
                        t = x
                        break
                text = build_media(t) if t else ''
                ctype = 'application/vnd.apple.mpegurl'
        code = '200 OK' if text else '502 Bad Gateway'
        body = (text or 'no data').encode('utf-8')
        hdr = ('HTTP/1.1 %s\r\nContent-Type: %s\r\n'
               'Access-Control-Allow-Origin: *\r\n'
               'Content-Length: %d\r\nConnection: close\r\n\r\n'
               % (code, ctype, len(body)))
        conn.sendall(hdr.encode() + body)
    except Exception:
        pass
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _serve():
    while True:
        try:
            c, _ = _SRV['sock'].accept()
        except Exception:
            return
        threading.Thread(target=_handle, args=(c,), daemon=True).start()


def ensure_server():
    """起本机中转（只监听 127.0.0.1，端口系统分配）；起不来返回 0"""
    with _SRV['lock']:
        if _SRV['port'] > 0 and _SRV['sock'] is not None:
            return _SRV['port']
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind(('127.0.0.1', 0))
            s.listen(16)
            _SRV['sock'] = s
            _SRV['port'] = s.getsockname()[1]
            threading.Thread(target=_serve, daemon=True).start()
        except Exception:
            _SRV['sock'] = None
            _SRV['port'] = 0
        return _SRV['port']


# ================================================================ 插件本体
class Spider(_Spider):

    def __init__(self):
        self.extend_host = ''

    def init(self, extend=''):
        try:
            if extend:
                o = json.loads(extend) if isinstance(extend, str) else extend
                h = (o or {}).get('host', '')
                if h:
                    if not h.startswith('http'):
                        h = 'https://' + h
                    self.extend_host = h.rstrip('/')
        except Exception:
            pass
        return self

    def getName(self):
        return '🔞' + SITE_NAME + '影视'

    # ---------------- 首页 ----------------
    def homeContent(self, filter=False):
        out = {'class': [], 'list': []}
        try:
            out['class'].append({'type_id': 'hot', 'type_name': '热门推荐'})
            out['class'].append({'type_id': 'fresh', 'type_name': '最新上架'})
            c = cfg()
            for t in (c or {}).get('video_type') or []:
                try:
                    tid = int(t.get('type_id') or 0)
                except Exception:
                    tid = 0
                if tid <= 0:
                    continue
                out['class'].append({'type_id': 'type:%d' % tid,
                                     'type_name': t.get('type_name') or ('分类%d' % tid)})
            out['list'] = self._list('video_hot_search', None, 0, PAGE)
        except Exception:
            pass
        out['filters'] = {}
        return out

    def homeVideoContent(self):
        return {'list': self._list('video_hot_search', None, 0, PAGE)}

    # ---------------- 分类 ----------------
    def categoryContent(self, tid, pg, filter=False, extend=None):
        out = {'page': 1, 'pagecount': 1, 'limit': PAGE, 'total': PAGE, 'list': []}
        try:
            page = max(1, int(pg))
        except Exception:
            page = 1
        t = (tid or '').strip()
        try:
            if t.startswith('type:'):
                ls = self._list('video_type_search', int(t[5:]), (page - 1) * PAGE, PAGE)
            else:
                ls = self._list('video_hot_search', None, (page - 1) * PAGE, PAGE)
            out['page'] = page
            out['pagecount'] = page + 1 if len(ls) >= PAGE else page
            out['total'] = page * PAGE
            out['list'] = ls
        except Exception:
            pass
        return out

    # ---------------- 搜索 ----------------
    def searchContent(self, key, quick=False, pg='1'):
        out = {'page': 1, 'pagecount': 1, 'limit': PAGE, 'total': PAGE, 'list': []}
        try:
            page = max(1, int(pg or 1))
        except Exception:
            page = 1
        try:
            ls = self._list('video_wd_search', (key or '').strip(), (page - 1) * PAGE, PAGE)
            out['page'] = page
            out['pagecount'] = page + 1 if len(ls) >= PAGE else page
            out['total'] = page * PAGE
            out['list'] = ls
        except Exception:
            pass
        return out

    # ---------------- 列表 ----------------
    def _list(self, kind, arg, offset, limit):
        cards = []
        p = {'search_type': kind, 'offset': offset, 'limit': limit}
        if kind == 'video_type_search':
            p['type_id'] = arg
        elif kind == 'video_wd_search':
            p['q'] = arg or ''
        js = api('/videos/search', p)
        if len(js) < 10:
            return cards
        try:
            ls = json.loads(js)
        except Exception:
            return cards
        if isinstance(ls, dict):
            ls = ls.get('list') or []
        for it in ls or []:
            c = _card(it)
            if c:
                cards.append(c)
        return cards

    # ---------------- 详情 ----------------
    def detailContent(self, ids):
        out = {'list': []}
        try:
            vid = (ids or [''])[0].strip()
            if not vid:
                return out
            d = self._detail(vid)
            if not d:
                return out
            name = d.get('video_title') or vid
            pic = d.get('video_cover') or ''
            mpd = d.get('video_url') or ''
            tag = d.get('video_tag') or ''
            dur = d.get('video_duration') or ''
            hits = d.get('video_hits') or 0
            lv = d.get('video_level') or 0
            thumb = d.get('video_thumbnail') or ''

            sb = []
            if tag:
                sb.append('标签：' + tag)
            if dur:
                sb.append('时长：' + dur)
            if hits:
                sb.append('播放：%s' % hits)
            if thumb:
                sb.append('预览图轴：' + thumb)
            sb.append('播放：DASH（分片带时效签名，已在本地改写成已签名清单）')

            vod = {
                'vod_id': vid,
                'vod_name': name,
                'vod_pic': pic,
                'vod_remarks': dur or ('HD%d' % lv if lv else ''),
                'vod_content': '\n'.join(sb),
            }
            if len(mpd) > 20:
                pid = _b64u(json.dumps({'i': vid, 'u': mpd}, separators=(',', ':'), ensure_ascii=False))
                vod['vod_play_from'] = 'DSAV·DASH$$$DSAV·HLS'
                vod['vod_play_url'] = '正片$%s#正片$%s' % (pid, pid)
            out['list'] = [vod]
        except Exception:
            pass
        return out

    def _detail(self, vid):
        p = {'search_type': 'video_detail_filter_id', 'video_id': vid}
        js = api('/videos/search', p)
        if len(js) < 10:
            return None
        try:
            a = json.loads(js)
            if isinstance(a, dict):
                return a
            return a[0] if a else None
        except Exception:
            return None

    # ---------------- 播放 ----------------
    def playerContent(self, flag, vid, vipFlags=None):
        out = {'parse': 0, 'jx': 0, 'url': '', 'header': {'User-Agent': UA, 'Referer': _referer()}}
        try:
            raw = (vid or '').strip()
            url = ''
            if raw.startswith('http'):
                url = raw
            else:
                dec = _b64u_dec(raw) if raw else ''
                if dec.startswith('{') and '"u"' in dec:
                    try:
                        play = json.loads(dec)
                    except Exception:
                        play = {}
                    mpd = play.get('u') or ''
                    f = flag or ''
                    if len(mpd) > 20:
                        if 'HLS' in f:
                            url = self._relay(mpd, 'dsavhls')
                        else:
                            url = self._relay(mpd, 'dsavmpd')
                            if not url:
                                url = self._relay(mpd, 'dsavhls')
                    if not url:
                        url = mpd
                elif dec.startswith('http'):
                    url = dec
                elif raw:
                    d = self._detail(raw)
                    if d:
                        mpd = d.get('video_url') or ''
                        if len(mpd) > 20:
                            url = self._relay(mpd, 'dsavmpd') or mpd
            out['url'] = url
        except Exception:
            pass
        return out

    def _relay(self, mpd_url, typ):
        try:
            p = ensure_server()
            if p <= 0:
                return ''
            return 'http://127.0.0.1:%d/zs?t=%s&u=%s' % (p, typ, _b64u(mpd_url))
        except Exception:
            return ''

    def localProxy(self, param):
        return None


def _card(it):
    try:
        i = it.get('video_id') or ''
        if not i:
            return None
        rm = []
        dur = it.get('video_duration') or ''
        if dur:
            rm.append(dur)
        hits = it.get('video_hits') or 0
        if hits:
            rm.append(('%.1f万' % (hits / 10000.0)) if hits >= 10000 else str(hits))
        lv = it.get('video_level') or 0
        if lv:
            rm.append('HD%d' % lv)
        return {
            'vod_id': i,
            'vod_name': it.get('video_title') or i,
            'vod_pic': it.get('video_cover') or '',
            'vod_remarks': ' · '.join(rm),
        }
    except Exception:
        return None


# ================================================================ 自检
if __name__ == '__main__':
    ok = [0, 0]

    def ck(label, cond, detail=''):
        ok[1] += 1
        if cond:
            ok[0] += 1
        print(('  PASS ' if cond else '  FAIL ') + label + (('  | ' + str(detail)) if detail else ''))

    print('================ DSAV py 插件自检 ================')

    # ① 加密层向量（真抓包）
    plain = '{"lang":"en","route":"/config"}'
    ck('AES 加密向量', aes_encrypt(plain) == '2S4bAoUZPTCzQGiSlJSpfefeo/s/d/mKbAuLRoPm41c=', aes_encrypt(plain))
    ck('AES 解密向量', aes_decrypt('2S4bAoUZPTCzQGiSlJSpfefeo/s/d/mKbAuLRoPm41c=') == plain, '')
    ck('apiSign 向量',
       api_sign(plain, 'ec12895c-7096-432f-81ad-909cb5aea020', 1790883970) == '39ac600d12c04f12edde34365f7006c7',
       api_sign(plain, 'ec12895c-7096-432f-81ad-909cb5aea020', 1790883970))
    ck('videoSign 向量①',
       video_sign('/mpd/20260925/ee49fa97/4000k/init-stream1.m4s', '1798732800') == 'sign=QkN2T8ub4gEg1soxDJXzcA&expire=1798732800',
       video_sign('/mpd/20260925/ee49fa97/4000k/init-stream1.m4s', '1798732800'))
    ck('videoSign 向量②',
       video_sign('/mpd/20260925/ee49fa97/4000k/chunk-stream0-00001.m4s', '1798732800') == 'sign=TvFJJrMcBm8-6TQob467Aw&expire=1798732800',
       video_sign('/mpd/20260925/ee49fa97/4000k/chunk-stream0-00001.m4s', '1798732800'))

    hs = http_sign(b'{"a":1}', '/videos/search', 'http-sign-v1', 'u0USwMNkwGoDEWudQf7YvdE6uMfteRanmJ2DDW5GZM8=')
    ck('HTTP 签名头齐备', 'Content-Digest' in hs and 'Signature-Input' in hs and 'Signature' in hs, hs.get('Signature-Input'))

    # ② 站点
    sp = Spider()
    sp.init('')
    c = cfg()
    ck('/config 有分类表', bool(c) and len(c.get('video_type') or []) > 5,
       '分类 %d / expire=%s' % (len((c or {}).get('video_type') or []), expire()))

    h = sp.homeContent()
    ck('首页分类', len(h.get('class') or []) >= 10, '%d 类' % len(h.get('class') or []))
    ck('首页列表', len(h.get('list') or []) >= 10, '%d 条' % len(h.get('list') or []))
    if h.get('list'):
        print('       首条：%s | %s' % (h['list'][0]['vod_name'], h['list'][0]['vod_remarks']))

    tid = ''
    for x in h.get('class') or []:
        if x['type_id'].startswith('type:'):
            tid = x['type_id']
            break
    c1 = sp.categoryContent(tid, '1')
    c2 = sp.categoryContent(tid, '2')
    ck('分类第1页', len(c1.get('list') or []) >= 20, '%s → %d 条' % (tid, len(c1.get('list') or [])))
    ck('分类第2页(真翻页)', len(c2.get('list') or []) >= 20
       and c2['list'][0]['vod_id'] != c1['list'][0]['vod_id'],
       '首条=%s' % (c2['list'][0]['vod_name'] if c2.get('list') else ''))

    s = sp.searchContent('人妻')
    ck('搜索', len(s.get('list') or []) >= 5, '%d 条' % len(s.get('list') or []))

    vid = (c1.get('list') or [{}])[0].get('vod_id') or (h.get('list') or [{}])[0].get('vod_id')
    d = sp.detailContent([vid])
    dv = (d.get('list') or [None])[0]
    ck('详情出得来', bool(dv) and bool(dv.get('vod_name')), (dv or {}).get('vod_name'))
    ck('详情带播放线路', bool(dv) and 'DASH' in (dv.get('vod_play_from') or ''),
       (dv or {}).get('vod_play_from'))

    # ③ 播放链：真拉 MPD → 展开 → 真拉分片
    mpd_url = ''
    if dv:
        pid = dv['vod_play_url'].split('$')[1].split('#')[0]
        mpd_url = json.loads(_b64u_dec(pid)).get('u', '')
    ck('取出原始 MPD 地址', mpd_url.startswith('http'), mpd_url)

    mpd = ''
    try:
        mpd = _get(mpd_url, referer=_referer()).decode('utf-8', 'replace')
    except Exception:
        pass
    ck('真拉 MPD', '<MPD' in mpd, '%d 字节' % len(mpd))

    tracks = parse_mpd(mpd, mpd_url, expire()) if mpd else []
    nseg = sum(len(t['segs']) for t in tracks)
    ck('MPD 展开分片表', len(tracks) >= 2 and nseg > 100, '%d 轨 / %d 片' % (len(tracks), nseg))
    for t in tracks:
        print('       轨 rep=%d %s %dx%d %d %ds %d片'
              % (t['rep'], t['kind'], t['width'], t['height'], t['bandwidth'], int(t['total']), len(t['segs'])))

    pulled = 0
    for t in tracks:
        if t['init']:
            n = len(_get(t['init'], referer=_referer()) or b'')
            ck('拉 init 分片(%s)' % t['kind'], n > 400, '%d 字节' % n)
            pulled += 1 if n > 400 else 0
        if t['segs']:
            n = len(_get(t['segs'][0], referer=_referer()) or b'')
            ck('拉首个分片(%s)' % t['kind'], n > 1000, '%d 字节' % n)
            pulled += 1 if n > 1000 else 0
    ck('分片真拉到字节', pulled >= 2, '%d 个' % pulled)

    # ④ 改写清单
    if tracks:
        rw = build_mpd(mpd, tracks)
        ck('MPD 改写为 SegmentList',
           '<SegmentList' in rw and '?sign=' in rw and '<SegmentURL' in rw, '%d 字节' % len(rw))
        p = ensure_server()
        ck('本机中转起来了', p > 0, '127.0.0.1:%d' % p)
        if p > 0:
            try:
                b = _get('http://127.0.0.1:%d/zs?t=dsavmpd&u=%s' % (p, _b64u(mpd_url)))
                ck('中转出的 MPD 是已签名清单',
                   b'<SegmentList' in b and b'?sign=' in b, '%d 字节' % len(b))
            except Exception as e:
                ck('中转出的 MPD 是已签名清单', False, str(e)[:60])
            try:
                hb = _get('http://127.0.0.1:%d/zs?t=dsavhls&u=%s' % (p, _b64u(mpd_url))).decode('utf-8', 'replace')
                ck('中转出的 HLS master 正常',
                   hb.startswith('#EXTM3U') and '#EXT-X-STREAM-INF' in hb, '%d 字节' % len(hb))
                mu = [l for l in hb.split('\n') if l.startswith('http')][-1].strip()
                media = _get(mu).decode('utf-8', 'replace')
                ck('中转出的 HLS 媒体清单正常',
                   '#EXT-X-MAP' in media and '#EXT-X-ENDLIST' in media, '%d 字节' % len(media))
                seg = [l for l in media.split('\n')
                       if l.startswith('http') and '.m4s' in l and 'EXT' not in l][0]
                n = len(_get(seg) or b'')
                ck('HLS 清单里的分片真拉到字节', n > 10000, '%d 字节' % n)
            except Exception as e:
                ck('HLS 线', False, str(e)[:80])

    pc = sp.playerContent('DSAV·DASH', dv['vod_play_url'].split('$')[1].split('#')[0])
    ck('playerContent 出地址', (pc.get('url') or '').startswith('http'), (pc.get('url') or '')[:90])

    print('================ 汇总: %d/%d ================' % (ok[0], ok[1]))
    sys.exit(0 if ok[0] == ok[1] else 1)
