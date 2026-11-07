#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
央视频直播 - 双源统一版 (Script 1 native + Script 2 YSPTP)
- 文件日志已禁用 (不写 /storage/emulated/0/Download/ysp-live.log)
- 双源: 央视频源1 (JCE/bk) + 央视频源2 (YSPTP)
"""

import base64, gzip, hashlib, json, os, random, re, struct, threading, time
import urllib.error, urllib.parse, urllib.request, uuid
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

try:
    from base.spider import Spider as SpiderBase
except ImportError:
    class SpiderBase(object):
        def getCache(self, key): return None
        def setCache(self, key, value): return "fail"
        def delCache(self, key): return "fail"


# ================================================================ 日志 (已禁用文件写入)
# 只 print 到 stdout, 不写文件. TVBox 一般看不到 stdout, 等于静默.

def _log(msg):
    try:
        print('[ysp] ' + msg, flush=True)
    except Exception:
        pass


def format_remarks(brand="央视频", meta=""):
    clean_meta = str(meta or "").strip()
    clean_meta = re.sub(r"[\r\n\t]+", " ", clean_meta).strip()
    return ("%s | %s" % (brand, clean_meta)) if clean_meta else brand


UA = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36'
WINDOW = 300
REFRESH_INTERVAL = 2
IDLE_TIMEOUT = 300
MAX_SEGS = 400
PLAYLIST_WINDOW = 8
LOCAL_PORT_PREFERRED = 19876
LOCAL_PORT_RANGE = 50


# ================================================================ JCE 协议

class W:
    def __init__(self): self.b = bytearray()
    def head(self, typ, tag):
        if tag < 15: self.b.append(((tag & 0xf) << 4) | (typ & 0xf))
        else: self.b.append(0xf0 | (typ & 0xf)); self.b.append(tag)
    def byte(self, v, tag):
        v = int(v)
        if v == 0: self.head(12, tag)
        else: self.head(0, tag); self.b += struct.pack('>b', v)
    def short(self, v, tag):
        v = int(v)
        if -128 <= v <= 127: self.byte(v, tag)
        else: self.head(1, tag); self.b += struct.pack('>h', v)
    def int(self, v, tag):
        v = int(v)
        if -32768 <= v <= 32767: self.short(v, tag)
        else: self.head(2, tag); self.b += struct.pack('>i', v)
    def long(self, v, tag):
        v = int(v)
        if -2147483648 <= v <= 2147483647: self.int(v, tag)
        else: self.head(3, tag); self.b += struct.pack('>q', v)
    def float(self, v, tag):
        self.head(4, tag); self.b += struct.pack('>f', float(v))
    def double(self, v, tag):
        self.head(5, tag); self.b += struct.pack('>d', float(v))
    def string(self, s, tag):
        if s is None: return
        data = str(s).encode('utf-8')
        if len(data) > 255: self.head(7, tag); self.b += struct.pack('>i', len(data)); self.b += data
        else: self.head(6, tag); self.b.append(len(data)); self.b += data
    def bytes(self, data, tag):
        data = bytes(data); self.head(13, tag); self.head(0, 0); self.int(len(data), 0); self.b += data
    def struct(self, fn, tag): self.head(10, tag); fn(self); self.head(11, 0)
    def list(self, items, tag, wf=None): self.head(9, tag); self.int(len(items), 0)
    def out(self): return bytes(self.b)


class R:
    def __init__(self, data): self.d = memoryview(data); self.p = 0
    def rem(self): return len(self.d) - self.p
    def get(self, n):
        if self.p + n > len(self.d): raise EOFError
        b = self.d[self.p:self.p + n].tobytes(); self.p += n; return b
    def u8(self): return self.get(1)[0]
    def head(self):
        b = self.u8(); typ = b & 0xf; tag = (b & 0xf0) >> 4
        if tag == 15: tag = self.u8()
        return typ, tag
    def value(self, typ):
        if typ == 0: return struct.unpack('>b', self.get(1))[0]
        if typ == 1: return struct.unpack('>h', self.get(2))[0]
        if typ == 2: return struct.unpack('>i', self.get(4))[0]
        if typ == 3: return struct.unpack('>q', self.get(8))[0]
        if typ == 4: return struct.unpack('>f', self.get(4))[0]
        if typ == 5: return struct.unpack('>d', self.get(8))[0]
        if typ == 6: n = self.u8(); return self.get(n).decode('utf-8', 'replace')
        if typ == 7: n = struct.unpack('>i', self.get(4))[0]; return self.get(n).decode('utf-8', 'replace')
        if typ == 8: n = self._int(); return {self._fv(): self._fv() for _ in range(n)}
        if typ == 9: n = self._int(); return [self._fv() for _ in range(n)]
        if typ == 10: return self.struct()
        if typ == 11: return None
        if typ == 12: return 0
        if typ == 13: t, _ = self.head(); n = self._int(); return self.get(n)
        raise ValueError('type %d' % typ)
    def _fv(self): t, _ = self.head(); return self.value(t)
    def _int(self): t, _ = self.head(); return int(self.value(t))
    def struct(self):
        m = {}
        while self.rem() > 0:
            t, tag = self.head()
            if t == 11: break
            m[tag] = self.value(t)
        return m


VER_NAME, VER_CODE = '3.2.7.26212', '302070'
APP_ID, QMF_APP_ID, QMF_PLATFORM, BIZ_ID = '1200013', 10012, 1, 0
CHAN_ID = '10070'
GUID = ''.join(random.choice('0123456789abcdef') for _ in range(32))


def _qua(w):
    w.string(VER_NAME, 0); w.string(VER_CODE, 1)
    w.int(1080, 2); w.int(2400, 3); w.int(3, 4); w.string('12', 5)
    w.int(1, 6); w.int(1, 7); w.int(420, 8); w.string(CHAN_ID, 9)
    for i in range(10, 15): w.string('', i)
    w.struct(lambda ww: (ww.int(0, 0), ww.byte(0, 1), ww.string('', 2)), 15)
    w.string('', 16); w.string('', 17); w.string('', 18)
    w.struct(lambda ww: (ww.int(0, 0), ww.float(0, 1), ww.float(0, 2), ww.double(0, 3)), 19)
    w.string(GUID[:16], 20); w.string('Pixel 6', 21)
    w.int(1, 22)
    for i in range(23, 27): w.int(0, i)
    w.string('', 27); w.string('', 28); w.string(GUID, 29)


def _head(w, cmd, reqid):
    w.int(reqid, 0); w.int(cmd, 1)
    w.struct(lambda ww: _qua(ww), 2)
    w.string(APP_ID, 3); w.string(GUID, 4)
    w.list([], 5); w.struct(lambda ww: None, 6)
    w.list([], 7)
    w.int(0, 8); w.int(0, 9); w.int(0, 10)


def _wrap(cmd, body, reqid):
    w = W()
    w.struct(lambda ww: _head(ww, cmd, reqid), 0)
    w.bytes(body, 1)
    reqcmd = w.out()
    inner = bytearray([38]) + struct.pack('>i', len(reqcmd) + 17) + bytes([1]) + b'\x00' * 10 + reqcmd + bytes([40])
    comp = gzip.compress(bytes(inner))
    out = bytearray([19]) + struct.pack('>i', 0) + struct.pack('>H', 2) + struct.pack('>H', 65281)
    out += struct.pack('>H', cmd) + struct.pack('>H', 0) + struct.pack('>q', reqid)
    out += struct.pack('>i', 531) + struct.pack('>i', QMF_APP_ID) + struct.pack('>q', BIZ_ID)
    g = GUID.encode()[:32]; out += g + b'\x00' * (32 - len(g))
    out += struct.pack('>b', QMF_PLATFORM) + struct.pack('>i', int(VER_CODE)) + b'\x00' * 6
    out += bytes([0]) + struct.pack('>H', 0) + struct.pack('>H', 0)
    out += struct.pack('>i', len(inner)) + comp + bytes([3])
    struct.pack_into('>i', out, 1, len(out))
    return bytes(out)


def _unwrap(data):
    if data[:1] != b'\x13' or len(data) < 90: return None
    flags = struct.unpack('>i', data[21:25])[0]
    payload = data[89:-1]
    if flags & 2: payload = gzip.decompress(payload)
    if payload[:1] != b'&' or payload[-1:] != b'(': return None
    rc = R(payload[16:-1]).struct()
    return rc.get(1) or b''


class DeadHostError(RuntimeError):
    pass


def jce_timeshift_url(pid, sid, start, end, stream='fhd'):
    w = W()
    w.string(pid, 0); w.string(sid, 1); w.long(start, 2); w.long(end, 3); w.string(stream, 4)
    body = w.out()
    CMD = 25312
    reqid = int(time.time() * 1000) & 0x7fffffff
    packet = _wrap(CMD, body, reqid)
    req = urllib.request.Request('https://jacc.ysp.cctv.cn', data=packet, method='POST')
    req.add_header('Content-Type', 'application/octet-stream')
    with urllib.request.urlopen(req, timeout=15) as resp:
        raw = resp.read()
    resp_body = _unwrap(raw)
    if not resp_body: raise RuntimeError('bad response')
    m = R(resp_body).struct()
    err = m.get(0, 0)
    if err != 0: raise RuntimeError(m.get(1, 'errCode=%s' % err))
    url = m.get(2, '')
    if not url: raise RuntimeError('empty m3u8')
    if 'liverecord.video.cloud.cctv.com' in url:
        raise DeadHostError('dead cdn host')
    return url


# ================================================================ cKey + bkliveinfo

_CK_PLATFORM = 4330403
_CK_APPVER = 'V8.22.1035.3031'
_CK_TEA = bytes.fromhex('59b2f7cf725ef43c34fdd7c123411ed3')
_CK_GTEA = bytes.fromhex('110DBEC10C23E7D2E56A1CAD6914EF1B')
_CK_XOR = bytes([0x84, 0x2e, 0xed, 0x08, 0xf0, 0x66, 0xe6, 0xea, 0x48, 0xb4, 0xca, 0xa9, 0x91, 0xed, 0x6f, 0xf3])
_CK_GXOR = bytes([0xb3, 0xc9, 0x53, 0xa0, 0x69, 0x13, 0xad, 0x4d])


def _u32(v): return v & 0xFFFFFFFF


def _tea_blk(blk, key):
    y, z = struct.unpack('>2I', blk)
    k = struct.unpack('>4I', key)
    s = 0
    for _ in range(16):
        s = _u32(s + 0x9e3779b9)
        y = _u32(y + _u32(_u32(_u32(z << 4) + k[0]) ^ _u32(z + s) ^ _u32((z >> 5) + k[1])))
        z = _u32(z + _u32(_u32(_u32(y << 4) + k[2]) ^ _u32(y + s) ^ _u32((y >> 5) + k[3])))
    return struct.pack('>2I', y, z)


def _cksum(buf):
    v = 0
    for b in buf: v = (0x83 * v + b) & 0x7fffffff
    return v


def _tea_pkt(data, key):
    pad = (8 - ((len(data) + 10) % 8)) % 8
    plain = bytes([(os.urandom(1)[0] & 0xf8) | pad]) + os.urandom(pad) + os.urandom(2) + data + bytes(7)
    out, pp, pc = b'', bytes(8), bytes(8)
    for off in range(0, len(plain), 8):
        mixed = bytes(a ^ b for a, b in zip(plain[off:off + 8], pc))
        enc = _tea_blk(mixed, key)
        cipher = bytes(a ^ b for a, b in zip(enc, pp))
        out += cipher
        pp, pc = mixed, cipher
    return out


def _lp(s):
    d = s.encode() if isinstance(s, str) else s
    return struct.pack('>H', len(d)) + d


def _ck_guard(ts, guid):
    def tail(v):
        t = str(v); return t[-5:] if len(t) >= 5 else ''
    body = struct.pack('>I', ts) + _lp(tail(guid)) + _lp(tail('null')) + _lp(tail('null')) + _lp('-1')
    plain = _lp(body)
    enc = _tea_pkt(plain, _CK_GTEA) + struct.pack('>I', _cksum(plain))
    enc = bytes(a ^ _CK_GXOR[i & 7] for i, a in enumerate(enc))
    return enc.hex().upper()


def _ckey(channel_id):
    ts = int(time.time())
    guid = os.urandom(16).hex()
    guard = _ck_guard(ts, guid)
    uid = os.urandom(4).hex().upper()
    body = (bytes.fromhex('0000004200000004000004d2') + struct.pack('>I', _CK_PLATFORM)
            + struct.pack('>I', 0) + struct.pack('>I', ts) + _lp('dcgh')
            + _lp('_zj1A5Gh6QYcxWjIUGos2w==') + _lp(_CK_APPVER) + _lp(str(channel_id))
            + _lp(guid) + struct.pack('>I', 1) + struct.pack('>I', 1) + _lp(uid) + _lp('nil')
            + _lp('57eab0c4-2c58-44c6-8ae9-dd2757525dc5') + _lp('nil') + _lp('v0.1.000')
            + _lp('com.cctv.yangshipin.app.iphone') + _lp(str(_CK_PLATFORM))
            + _lp('ex_json_bus') + _lp('ex_json_vs') + _lp(guard))
    pkt = bytearray(struct.pack('>H', len(body)) + body)
    pkt[18:22] = struct.pack('>I', _cksum(bytes(pkt)))
    pkt = bytes(pkt)
    enc = _tea_pkt(pkt, _CK_TEA) + struct.pack('>I', _cksum(pkt))
    enc = bytes(a ^ _CK_XOR[i & 15] for i, a in enumerate(enc))
    b64 = base64.b64encode(enc).decode().replace('+', '_').replace('/', '-').rstrip('=')
    return {'cKey': '--01' + b64, 'guid': guid, 'ts': ts,
            'flowId': '%s_%d' % (uuid.uuid4().hex.upper(), _CK_PLATFORM)}


_BK_H264 = base64.b64encode(b'H(30:1080,60:1080|30:1080,60:1080)').decode()


def bk_playurls(channel_id, live_pid, defn='fhd'):
    t = _ckey(channel_id)
    q = urllib.parse.urlencode({
        'atime': '120', 'livepid': live_pid, 'cnlid': channel_id,
        'appVer': _CK_APPVER, 'app_version': '300090', 'caplv': '1', 'cmd': '2',
        'defn': defn, 'device': 'iPhone', 'encryptVer': '4.2', 'getpreviewinfo': '0',
        'hevclv': '0', 'lang': 'zh-Hans_CN', 'livequeue': '0', 'logintype': '1',
        'nettype': '1', 'newnettype': '1', 'newplatform': str(_CK_PLATFORM),
        'platform': str(_CK_PLATFORM), 'sdtfrom': 'v3021', 'spacode': '23',
        'spaudio': '1', 'spdemuxer': '6', 'spdrm': '2', 'spdynamicrange': '1',
        'spflv': '1', 'spflvaudio': '1', 'sphdrfps': '60', 'sphttps': '1',
        'spvcode': _BK_H264, 'spvideo': '4', 'stream': '1', 'system': '1',
        'sysver': 'ios18.2.1', 'uhd_flag': '0', 'cKey': t['cKey'], 'guid': t['guid'],
        'fntick': str(t['ts']), 'flowid': t['flowId'], 'playbacktime': '0',
    })
    req = urllib.request.Request('https://bkliveinfo.ysp.cctv.cn/?' + q,
                                 headers={'User-Agent': 'qqlive', 'Accept': 'application/json'})
    with urllib.request.urlopen(req, timeout=15) as r:
        p = json.loads(r.read().decode())
    if int(p.get('iretcode', -1)) != 0:
        raise RuntimeError('iretcode=%s %s' % (p.get('iretcode'), p.get('errinfo', '')))
    urls = []
    if p.get('playurl'): urls.append(p['playurl'])
    bu = p.get('backurl_list') or p.get('backurlList') or p.get('backurl')
    if isinstance(bu, list):
        for it in bu: urls.append(it if isinstance(it, str) else (it.get('url') or it.get('playurl') or ''))
    elif isinstance(bu, str):
        urls += [x for x in re.split(r'[;,]', bu) if x.strip()]
    urls = [u for u in dict.fromkeys(urls) if u and '.cctv.' in u]
    if not urls: raise RuntimeError('no playurl')
    urls.sort(key=lambda u: (0 if 'bklive-' in u else 1, u))
    return urls


# ================================================================ YSPTP 协议

AK = '9f5c54c4ed0e50109b800f7e28fec205'
YSPTP_APP_START = 'https://ytpaddr.cctv.cn/gsnw/api/app/start/v1/01'
YSPTP_DICTIONARY = 'https://ytpaddr.cctv.cn/gsnw/player/dictionary/obtain/v1'
YSPTP_LIVE_01 = 'https://ytpaddr.cctv.cn/gsnw/api/live/v1/01'
YSPTP_LIVE_02 = 'https://ytpaddr.cctv.cn/gsnw/api/live/v1/02'
YSPTP_VDN = 'https://ytpvdn.cctv.cn/cctvmobileinf/rest/cctv/videoliveUrl/getstream'
YSPTP_VERSION = '1.4.1'
YSPTP_APP_CHANNEL = 'dangbei'
YSPTP_VDN_APP = '央视频电视投屏助手'
YSPTP_UA = 'cctv_app_tv'

_YSPTP_STATE_FILE = '/tmp/ysptp-state.json'
_YSPTP_CACHE_TTL = 600
_YSPTP_REFRESH_MARGIN = 45


def _ysptp_now_ms(): return int(time.time() * 1000)
def _ysptp_now_s(): return time.time()


def _java_hashcode(s):
    h = 0
    for c in s:
        h = ((h * 31) + ord(c)) & 0xFFFFFFFF
        if h >= 0x80000000: h -= 0x100000000
    return h


def _java_uuid_hash(msb, lsb):
    def to_u64(v): return v + (1 << 64) if v < 0 else v
    return '%016x%016x' % (to_u64(msb), to_u64(lsb))


def _sha1_upper(s): return hashlib.sha1(s.encode()).hexdigest().upper()
def _sha256_hex(s): return hashlib.sha256(s.encode()).hexdigest()
def _md5_hex(s): return hashlib.md5(s.encode()).hexdigest()


# ================================================================
# Opsi A: device identity 持久化 — 每次返回同一个设备
# ================================================================

_DEVICE_ANDROID_ID = 'a1b2c3d4e5f6a7b8'
_DEVICE_MAC = 'aa:bb:cc:dd:ee:01'
_DEVICE_XUID = None


def _ysptp_profile():
    return {
        'android_id': _DEVICE_ANDROID_ID,
        'mac': _DEVICE_MAC,
        'hardware': 'mt5895',
        'board': 'mt5895',
        'brand': 'Sony',
        'device': 'sony_xr_85z9k',
        'manufacturer': 'Sony',
        'model': 'XR-85Z9K',
        'product': 'sony_xr_85z9k',
        'tags': 'release-keys',
        'build_type': 'user',
        'user': 'build',
        'resolution': '7680*4320',
        'display': 'XR-85Z9K-user 13 SONYTV.2022.XR_85Z9K 2024 release-keys',
        'version_id': 'SONYTV.2022.XR_85Z9K',
        'host': 'sony-tv-build',
        'fingerprint': 'Sony/sony_xr_85z9k/sony_xr_85z9k:13/SONYTV.2022.XR_85Z9K/2024:user/release-keys',
        'report_model': 'XR85Z9K',
        'screen_param': '7680-4320-280',
        'cast_model': 'XR-85Z9K',
    }


def _ysptp_xuid(p):
    build = ('1698' + p['hardware'] + p['board'] + p['brand'] + p['device']
             + p['manufacturer'] + p['model'] + p['product'] + p['tags']
             + p['build_type'] + p['user'] + p['resolution'] + p['mac'])
    uuid_hex = _java_uuid_hash(_java_hashcode(build), _java_hashcode(p['model']))
    return _sha1_upper(p['android_id'] + '|' + uuid_hex)


def _ysptp_fingerprint(xuid, ms):
    day0 = 86400000 * ((ms // 1000 + 28800) // 86400) - 28800000
    return _sha256_hex(_sha256_hex(AK + xuid + str(ms) + str(day0)))


def _ysptp_load_state():
    global _DEVICE_XUID
    p = _ysptp_profile()
    if _DEVICE_XUID is None:
        _DEVICE_XUID = _ysptp_xuid(p)
    return {'schema_version': 1, 'profile': p, 'screen_param': p['screen_param'],
            'cast_model': p['cast_model'], 'x_uid': _DEVICE_XUID, 'created_at': _ysptp_now_s()}


def _ysptp_headers(ident, ctype='application/json; charset=utf-8', accept='application/json', ts=None):
    return {
        'Accept': accept or '*/*', 'Accept-Language': 'zh-CN,zh;q=0.8',
        'Referer': 'api.cctv.cn', 'User-Agent': YSPTP_UA,
        'UID': ident.get('android_id', ''),
        'appChannel': YSPTP_APP_CHANNEL,
        'X-Uid': ident['x_uid'],
        'X-Fingerprint': ident['x_fingerprint'],
        'X-Version': YSPTP_VERSION,
        'X-Timestamp': str(ts if ts is not None else _ysptp_now_ms()),
        'X-Nonce': str(uuid.uuid4()),
        'Content-Type': ctype,
        'Connection': 'Keep-Alive',
        'Accept-Encoding': 'gzip',
        'Cache-Control': 'no-cache',
    }


def _ysptp_build_identity(p):
    xu = _ysptp_xuid(p)
    xf = _ysptp_fingerprint(xu, _ysptp_now_ms())
    return {'android_id': p['android_id'], 'x_uid': xu, 'x_fingerprint': xf}


def _ysptp_http(url, headers, body=None, method='POST', timeout=15):
    data = body.encode('utf-8') if isinstance(body, str) else body
    req = urllib.request.Request(url, data=data, method=method)
    for k, v in headers.items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read(), r.headers.get('Content-Type', '')
    except urllib.error.HTTPError as e:
        return e.code, e.read(), e.headers.get('Content-Type', '') if e.headers else ''


_AES_BACKEND = None


def _detect_aes_backend():
    global _AES_BACKEND
    if _AES_BACKEND is not None:
        return _AES_BACKEND
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM  # noqa
        _AES_BACKEND = 'cryptography'
        return _AES_BACKEND
    except Exception:
        pass
    try:
        from Crypto.Cipher import AES  # noqa
        _AES_BACKEND = 'pycryptodome'
        return _AES_BACKEND
    except Exception:
        pass
    _AES_BACKEND = 'none'
    return _AES_BACKEND


def _ysptp_aes_gcm_decrypt_b64(value, key):
    raw = base64.b64decode(value)
    if len(raw) <= 12:
        raise RuntimeError('AES-GCM payload too short')
    k = key.encode('utf-8')[:32].ljust(32, b'\x00')
    nonce, ct = raw[:12], raw[12:]
    backend = _detect_aes_backend()
    if backend == 'cryptography':
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        return AESGCM(k).decrypt(nonce, ct, None).decode('utf-8')
    if backend == 'pycryptodome':
        from Crypto.Cipher import AES
        tag, real_ct = ct[-16:], ct[:-16]
        cipher = AES.new(k, AES.MODE_GCM, nonce=nonce)
        return cipher.decrypt_and_verify(real_ct, tag).decode('utf-8')
    raise RuntimeError('AES-GCM unavailable: install "cryptography" or "pycryptodome"')


def _ysptp_aes_gcm_encrypt_b64(value, key):
    k = key.encode('utf-8')[:32].ljust(32, b'\x00')
    nonce = os.urandom(12)
    backend = _detect_aes_backend()
    if backend == 'cryptography':
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        ct = AESGCM(k).encrypt(nonce, value.encode('utf-8'), None)
        return base64.b64encode(nonce + ct).decode()
    if backend == 'pycryptodome':
        from Crypto.Cipher import AES
        cipher = AES.new(k, AES.MODE_GCM, nonce=nonce)
        ct, tag = cipher.encrypt_and_digest(value.encode('utf-8'))
        return base64.b64encode(nonce + ct + tag).decode()
    raise RuntimeError('AES-GCM unavailable: install "cryptography" or "pycryptodome"')


def _ysptp_collect_report(p, xuid):
    value = {
        'cctv_id': xuid[:64], 'device_id': p['android_id'], 'idfa': '', 'idfv': '', 'user_id': '',
        'app_key': '1178c84d-4818-44ff-b415-02106e87e144', 'imei': '', 'android_id': p['android_id'], 'mac': p['mac'],
        'device_builder_type': p['build_type'], 'device_hardware': p['hardware'], 'device_board': p['board'],
        'device_brand': p['brand'], 'device_params': p['device'], 'device_display': p['display'],
        'device_version_id': p['version_id'], 'device_host': p['host'], 'device_product': p['product'],
        'device_tags': p['tags'], 'device_user': p['user'], 'device_fingerprint': p['fingerprint'],
        'device_manufacturer': p['manufacturer'], 'device_model': p['report_model'],
        'device_resolution': p['resolution'], 'system_type': 'Android', 'device_type': 'TV',
        'app_language': 'CHINESE', 'app_version': YSPTP_VERSION, 'sdk_version': '1.0.0',
        'os_version': '13', 'app_channel': YSPTP_APP_CHANNEL, 'data_time': str(_ysptp_now_ms()),
    }
    info = json.dumps({'key': 'app_start_d1', 'value': value}, ensure_ascii=False, separators=(',', ':'))
    headers = {
        'Content-Type': 'application/x-www-form-urlencoded', 'Charset': 'UTF-8',
        'User-Agent': 'Dalvik/2.1.0 (Linux; U; Android 13; %s Build/%s)' % (p['model'], p['version_id']),
        'X-Uid': xuid, 'X-Fingerprint': xuid, 'X-Version': YSPTP_VERSION,
        'Connection': 'Keep-Alive', 'Accept-Encoding': 'gzip',
    }
    st, txt, _ = _ysptp_http('https://collect.cctv.cn/cctvmobileinf/rest/cctv/receive/new/app',
                              headers, urllib.parse.urlencode({'info': info}))
    if st < 200 or st >= 300:
        raise RuntimeError('collect HTTP %d: %s' % (st, txt[:200]))


def _ysptp_dictionary():
    headers = {
        'X-Uid': 'ROOT', 'X-Fingerprint': 'ROOT', 'X-Nonce': str(uuid.uuid4()),
        'X-Timestamp': str(_ysptp_now_ms()), 'X-Version': YSPTP_VERSION, 'UID': 'ROOT',
        'Referer': 'api.cctv.cn', 'User-Agent': YSPTP_UA, 'appChannel': 'ROOT',
        'Connection': 'Keep-Alive', 'Accept-Encoding': 'gzip',
    }
    st, _, _ = _ysptp_http(YSPTP_DICTIONARY, headers)
    if st < 200 or st >= 300:
        raise RuntimeError('dictionary HTTP %d' % st)


def _ysptp_app_start(ident, p):
    ts = _ysptp_now_ms()
    ident['x_fingerprint'] = _ysptp_fingerprint(ident['x_uid'], ts)
    body = {'key': 'app_start_d1', 'value': {
        'cctv_id': ident['x_uid'][:64], 'device_id': p['android_id'], 'idfa': '', 'idfv': '',
        'user_id': '', 'app_key': '1178c84d-4818-44ff-b415-02106e87e144', 'imei': '',
        'android_id': p['android_id'], 'mac': p['mac'], 'device_builder_type': p['build_type'],
        'device_hardware': p['hardware'], 'device_board': p['board'], 'device_brand': p['brand'],
        'device_params': p['device'], 'device_display': p['display'],
        'device_version_id': p['version_id'], 'device_host': p['host'], 'device_product': p['product'],
        'device_tags': p['tags'], 'device_user': p['user'], 'device_fingerprint': p['fingerprint'],
        'device_manufacturer': p['manufacturer'], 'device_model': p['report_model'],
        'device_resolution': p['resolution'], 'system_type': 'Android', 'device_type': 'TV',
        'app_language': 'CHINESE', 'app_version': YSPTP_VERSION, 'sdk_version': '',
        'os_version': '13', 'app_channel': YSPTP_APP_CHANNEL, 'data_time': str(_ysptp_now_ms()),
    }}
    headers = _ysptp_headers(ident, 'application/json; charset=utf-8', 'application/json', ts)
    headers['UID'] = ''
    st, txt, _ = _ysptp_http(YSPTP_APP_START, headers,
                             json.dumps(body, ensure_ascii=False, separators=(',', ':')))
    if st < 200 or st >= 300:
        raise RuntimeError('app/start HTTP %d: %s' % (st, txt[:200]))
    j = json.loads(txt.decode('utf-8', 'replace'))
    d = j.get('data')
    if isinstance(d, dict): enc = d.get('key', '')
    elif isinstance(d, str): enc = d
    else: enc = ''
    if not enc: raise RuntimeError('app/start missing key')
    return _ysptp_aes_gcm_decrypt_b64(enc, ident['x_fingerprint'][:32])


def _ysptp_report_single(ident, body, uid_override=None):
    headers = _ysptp_headers(ident)
    if uid_override is not None: headers['UID'] = uid_override
    st, _, _ = _ysptp_http('https://ytpdata.cctv.cn/das/app/data/message/single', headers,
                            json.dumps(body, ensure_ascii=False, separators=(',', ':')))
    if st < 200 or st >= 300:
        raise RuntimeError('report HTTP %d' % st)


def _ysptp_app_event(ident, p):
    t = _ysptp_now_ms()
    v = {
        'cctv_id': ident['x_uid'][:64], 'device_id': p['android_id'], 'idfa': '', 'idfv': '',
        'user_id': '', 'app_key': '1178c84d-4818-44ff-b415-02106e87e144', 'imei': '',
        'android_id': p['android_id'], 'mac': p['mac'], 'device_builder_type': p['build_type'],
        'device_hardware': p['hardware'], 'device_board': p['board'], 'device_brand': p['brand'],
        'device_params': p['device'], 'device_display': p['display'],
        'device_version_id': p['version_id'], 'device_host': p['host'], 'device_product': p['product'],
        'device_tags': p['tags'], 'device_user': p['user'], 'device_fingerprint': p['fingerprint'],
        'device_manufacturer': p['manufacturer'], 'device_model': p['report_model'],
        'device_resolution': p['resolution'], 'system_type': 'Android', 'device_type': 'TV',
        'app_language': 'CHINESE', 'app_version': YSPTP_VERSION, 'sdk_version': '',
        'os_version': '13', 'app_channel': YSPTP_APP_CHANNEL, 'data_time': str(t),
        'event_id': 'app_start', 'event_name': '应用启动', 'event_time': str(t),
        'network_type': 'WIFI', 'cur_version': YSPTP_VERSION, 'channel': YSPTP_APP_CHANNEL,
        'pre_version': YSPTP_VERSION,
    }
    _ysptp_report_single(ident, {'key': 'event', 'value': v}, uid_override='')


def _ysptp_page_event(ident, p):
    end = _ysptp_now_ms(); start = end - 1000
    v = {
        'cctv_id': ident['x_uid'][:64], 'device_id': p['android_id'], 'idfa': '', 'idfv': '',
        'user_id': '', 'app_key': '1178c84d-4818-44ff-b415-02106e87e144', 'imei': '',
        'android_id': p['android_id'], 'mac': p['mac'], 'device_builder_type': p['build_type'],
        'device_hardware': p['hardware'], 'device_board': p['board'], 'device_brand': p['brand'],
        'device_params': p['device'], 'device_display': p['display'],
        'device_version_id': p['version_id'], 'device_host': p['host'], 'device_product': p['product'],
        'device_tags': p['tags'], 'device_user': p['user'], 'device_fingerprint': p['fingerprint'],
        'device_manufacturer': p['manufacturer'], 'device_model': p['report_model'],
        'device_resolution': p['resolution'], 'system_type': 'Android', 'device_type': 'TV',
        'app_language': 'CHINESE', 'app_version': YSPTP_VERSION, 'sdk_version': '',
        'os_version': '13', 'app_channel': YSPTP_APP_CHANNEL, 'data_time': str(end + 2),
        'start_time': str(start), 'end_time': str(end), 'duration': '1000',
        'page_name': 'com.cctv.tv.mvp.ui.activity.MainActivity', 'session_id': str(uuid.uuid4()),
        'network_type': 'WIFI',
    }
    _ysptp_report_single(ident, {'key': 'page_d1', 'value': v})


def _ysptp_heartbeat(ident, p):
    t = _ysptp_now_ms()
    v = {
        'cctv_id': ident['x_uid'][:64], 'device_id': p['android_id'], 'idfa': '', 'idfv': '',
        'user_id': '', 'app_key': '1178c84d-4818-44ff-b415-02106e87e144', 'imei': '',
        'android_id': p['android_id'], 'mac': p['mac'], 'device_builder_type': p['build_type'],
        'device_hardware': p['hardware'], 'device_board': p['board'], 'device_brand': p['brand'],
        'device_params': p['device'], 'device_display': p['display'],
        'device_version_id': p['version_id'], 'device_host': p['host'], 'device_product': p['product'],
        'device_tags': p['tags'], 'device_user': p['user'], 'device_fingerprint': p['fingerprint'],
        'device_manufacturer': p['manufacturer'], 'device_model': p['report_model'],
        'device_resolution': p['resolution'], 'system_type': 'Android', 'device_type': 'TV',
        'app_language': 'CHINESE', 'app_version': YSPTP_VERSION, 'sdk_version': '',
        'os_version': '13', 'app_channel': YSPTP_APP_CHANNEL, 'data_time': str(t),
        'network_type': 'WiFi', 'guid': '', 'other': '',
    }
    _ysptp_report_single(ident, {'key': 'app_heartbeat', 'value': v})


def _ysptp_index_flow(ident):
    st, _, _ = _ysptp_http('https://ytpaddr.cctv.cn/gsnw/api/index/v1/01', _ysptp_headers(ident),
                            json.dumps({'channel': YSPTP_APP_CHANNEL, 'source': 'application'}, ensure_ascii=False))
    if st < 200 or st >= 300: raise RuntimeError('index HTTP %d' % st)


def _ysptp_warmup(ident):
    appcommon = json.dumps({'adid': '', 'av': YSPTP_VERSION, 'an': YSPTP_VDN_APP, 'ap': 'cctv_app_tv'},
                           ensure_ascii=False, separators=(',', ':'))
    st, _, _ = _ysptp_http('https://ytpaddr.cctv.cn/gsnw/drm/config/obtain/v1',
                           _ysptp_headers(ident, 'application/x-www-form-urlencoded', None),
                           urllib.parse.urlencode({'appcommon': appcommon}))
    if st < 200 or st >= 300: raise RuntimeError('drm HTTP %d' % st)
    url = 'https://ytpaddr.cctv.cn/gsnw/version/config/obtain/v1?' + urllib.parse.urlencode({'appcommon': appcommon})
    st, _, _ = _ysptp_http(url, _ysptp_headers(ident, '', None), method='GET')
    if st < 200 or st >= 300: raise RuntimeError('version HTTP %d' % st)


def _ysptp_live01(ident, state, session_key, live_id):
    body = {
        'screenParam': state['screen_param'], 'rate': '', 'systemType': 'ios',
        'model': state['cast_model'], 'id': live_id,
        'userId': 'BAEBFF2B-C516-4F34-ABC0-A824A6461CBD', 'clientSign': 'cctvVideo',
        'deviceId': {'serial': '', 'imei': '', 'android_id': ''},
    }
    st, txt, _ = _ysptp_http(YSPTP_LIVE_01, _ysptp_headers(ident),
                              json.dumps(body, ensure_ascii=False, separators=(',', ':')))
    if st < 200 or st >= 300: raise RuntimeError('live/01 HTTP %d' % st)
    j = json.loads(txt.decode('utf-8', 'replace'))
    videos = j.get('data', {}).get('videoList') or j.get('data', {}).get('videos') or []
    pick = None; fallback = None
    for v in videos:
        if not v.get('url'): continue
        if fallback is None: fallback = v
        if v.get('rate') == '36p': pick = v; break
    v = pick or fallback
    if not v: raise RuntimeError('live/01 no url')
    url = v['url']
    if not re.match(r'^https?://', url, re.I):
        url = _ysptp_aes_gcm_decrypt_b64(url, session_key)
    return {'url': url, 'rate': v.get('rate', ''), 'rateName': v.get('rateName', '')}


def _ysptp_live02(ident, session_key):
    enc = _ysptp_aes_gcm_encrypt_b64('', session_key)
    st, txt, _ = _ysptp_http(YSPTP_LIVE_02, _ysptp_headers(ident), json.dumps({'guid': enc}))
    if st < 200 or st >= 300: raise RuntimeError('live/02 HTTP %d' % st)
    j = json.loads(txt.decode('utf-8', 'replace'))
    d = j.get('data', {})
    enc = d.get('appSecret') or d.get('app_secret') or (d if isinstance(d, str) else '')
    if not enc: raise RuntimeError('live/02 no appSecret')
    return _ysptp_aes_gcm_decrypt_b64(enc, session_key)


def _ysptp_vdn(ident, live_url, secret):
    r = '%08x-0000-%04x-0000-00000000%04x' % (
        random.randint(0, 0xffffffff), random.randint(0, 0xffff), random.randint(0, 0xffff))
    sign = _md5_hex(AK + secret + r)
    appcommon = json.dumps({'adid': '', 'av': YSPTP_VERSION, 'an': YSPTP_VDN_APP, 'ap': 'cctv_app_tv'},
                           ensure_ascii=False, separators=(',', ':'))
    headers = _ysptp_headers(ident, 'application/x-www-form-urlencoded', None)
    headers['APPID'] = AK; headers['APPSIGN'] = sign; headers['APPRANDOMSTR'] = r
    st, txt, _ = _ysptp_http(YSPTP_VDN, headers,
                              urllib.parse.urlencode({'appcommon': appcommon, 'url': live_url}))
    if st < 200 or st >= 300: raise RuntimeError('VDN HTTP %d' % st)
    j = json.loads(txt.decode('utf-8', 'replace'))
    if str(j.get('succeed', '')) != '1' or not j.get('url'):
        raise RuntimeError('VDN no url')
    return {'url': j['url'], 'sign': sign, 'random': r}


_YSPTP_ENTRY_CACHE = {}
_YSPTP_ENTRY_LOCK = threading.Lock()


def ysptp_resolve(live_id, slug=''):
    with _YSPTP_ENTRY_LOCK:
        now = _ysptp_now_s()
        if slug and slug in _YSPTP_ENTRY_CACHE:
            e = _YSPTP_ENTRY_CACHE[slug]
            if now < e['expires_at'] - _YSPTP_REFRESH_MARGIN:
                return e
        state = _ysptp_load_state()
        p = state['profile']
        ident = _ysptp_build_identity(p)
        try: _ysptp_collect_report(p, ident['x_uid'])
        except Exception as e: _log('ysptp collect skip: %s' % e)
        try: _ysptp_dictionary()
        except Exception as e: _log('ysptp dict skip: %s' % e)
        session = _ysptp_app_start(ident, p)
        try:
            _ysptp_app_event(ident, p); _ysptp_page_event(ident, p)
            _ysptp_heartbeat(ident, p); _ysptp_index_flow(ident); _ysptp_warmup(ident)
        except Exception as e:
            _log('ysptp warmup skip: %s' % e)
        l1 = _ysptp_live01(ident, state, session, live_id)
        secret = _ysptp_live02(ident, session)
        v = _ysptp_vdn(ident, l1['url'], secret)
        entry = {
            'final_url': v['url'],
            'headers': {'UID': p['android_id'], 'APPID': AK, 'Referer': 'api.cctv.cn',
                        'User-Agent': YSPTP_UA, 'APPRANDOMSTR': v['random'], 'APPSIGN': v['sign']},
            'rate': l1['rate'], 'rate_name': l1['rateName'],
            'expires_at': now + _YSPTP_CACHE_TTL,
        }
        if slug: _YSPTP_ENTRY_CACHE[slug] = entry
        return entry


# ================================================================ 频道表

CHANNELS = [
    ('cctv1',    'CCTV-1 综合',      '2024078201', '600001859', 'fhd', 'Live1717729995180256'),
    ('cctv2',    'CCTV-2 财经',      '2024075401', '600001800', 'fhd', 'Live1718261577870260'),
    ('cctv3',    'CCTV-3 综艺',      '2024068501', '600001801', 'fhd', 'Live1718261955077261'),
    ('cctv4',    'CCTV-4 中文国际',  '2029797101', '600001814', 'fhd', 'Live1718276148119264'),
    ('cctv5',    'CCTV-5 体育',      '2024078401', '600001818', 'fhd', 'Live1719474204987287'),
    ('cctv5p',   'CCTV-5+ 体育赛事', '2024078001', '600001817', 'fhd', 'Live1719473996025286'),
    ('cctv6',    'CCTV-6 电影',      '2013693901', '600108442', 'fhd', None),
    ('cctv7',    'CCTV-7 国防军事',  '2024072001', '600004092', 'fhd', 'Live1718276412224269'),
    ('cctv8',    'CCTV-8 电视剧',    '2029793001', '600001803', 'fhd', 'Live1718276458899270'),
    ('cctv9',    'CCTV-9 纪录',      '2024078601', '600004078', 'fhd', 'Live1718276503187272'),
    ('cctv10',   'CCTV-10 科教',     '2024078701', '600001805', 'fhd', 'Live1718276550002273'),
    ('cctv11',   'CCTV-11 戏曲',     '2027248701', '600001806', 'fhd', 'Live1718276603690275'),
    ('cctv12',   'CCTV-12 社会与法', '2027248801', '600001807', 'fhd', 'Live1718276623932276'),
    ('cctv13',   'CCTV-13 新闻',     '2029797201', '600001811', 'fhd', 'Live1718276575708274'),
    ('cctv14',   'CCTV-14 少儿',     '2027248901', '600001809', 'fhd', 'Live1718276498748271'),
    ('cctv15',   'CCTV-15 音乐',     '2027249001', '600001815', 'fhd', 'Live1718276319614267'),
    ('cctv16',   'CCTV-16 奥林匹克', '2027249101', '600098637', 'fhd', 'Live1718276256572265'),
    ('cctv17',   'CCTV-17 农业农村', '2027249401', '600001810', 'fhd', 'Live1718276138318263'),
    ('cctv4k',   'CCTV-4K 超高清',   '2029810301', '600002264', 'fhd', 'Live1767871224782105'),
    ('cctv8k',   'CCTV-8K 超高清',   '2026774101', '600156816', 'fhd', 'Live1688400593818102'),
    ('cctv164k', 'CCTV-16 4K',       '2027249301', '600099502', 'fhd', 'Live1704966749996185'),
    ('cgtn',     'CGTN 英语',        '2024181701', '600014550', 'fhd', 'Live1719392219423280'),
    ('cgtnfr',   'CGTN 法语',        '2024181801', '600084704', 'fhd', 'Live1719392670442283'),
    ('cgtnru',   'CGTN 俄语',        '2024181901', '600084758', 'fhd', 'Live1719392779653284'),
    ('cgtnar',   'CGTN 阿拉伯语',    '2024182001', '600084782', 'fhd', 'Live1719392885692285'),
    ('cgtnes',   'CGTN 西班牙语',    '2024182101', '600084744', 'fhd', 'Live1719392560433282'),
    ('cgtndoc',  'CGTN 纪录',        '2024182301', '600084781', 'fhd', 'Live1719392360336281'),
    ('cctvfyjc', 'CCTV 风云剧场',    '2025637103', '600099658', 'shd', None),
    ('cctvdyjc', 'CCTV 第一剧场',    '2026874203', '600099655', 'shd', None),
    ('cctvhjjc', 'CCTV 怀旧剧场',    '2026874303', '600099620', 'shd', None),
    ('bjws',     '北京卫视',  '2024052703', '600002309', 'fhd', None),
    ('jsws',     '江苏卫视',  '2024171103', '600002521', 'fhd', None),
    ('dfws',     '东方卫视',  '2024054503', '600002483', 'fhd', None),
    ('zjws',     '浙江卫视',  '2024054703', '600002520', 'fhd', None),
    ('hnws',     '湖南卫视',  '2024054803', '600002475', 'fhd', None),
    ('hbws',     '湖北卫视',  '2024171203', '600002508', 'fhd', None),
    ('gdws',     '广东卫视',  '2024060903', '600002485', 'fhd', None),
    ('gxws',     '广西卫视',  '2024060703', '600002509', 'fhd', None),
    ('hljws',    '黑龙江卫视','2029797003', '600002498', 'fhd', None),
    ('hainanws', '海南卫视',  '2024055603', '600002506', 'fhd', None),
    ('cqws',     '重庆卫视',  '2024061103', '600002531', 'fhd', None),
    ('szws',     '深圳卫视',  '2024061303', '600002481', 'fhd', None),
    ('scws',     '四川卫视',  '2024061403', '600002516', 'fhd', None),
    ('henanws',  '河南卫视',  '2029797303', '600002525', 'fhd', None),
    ('dnws',     '东南卫视',  '2024061503', '600002484', 'fhd', None),
    ('gzws',     '贵州卫视',  '2024061603', '600002490', 'fhd', None),
    ('jxws',     '江西卫视',  '2024061703', '600002503', 'fhd', None),
    ('lnws',     '辽宁卫视',  '2024171303', '600002505', 'fhd', None),
    ('ahws',     '安徽卫视',  '2024171403', '600002532', 'fhd', None),
    ('hebws',    '河北卫视',  '2024171503', '600002493', 'fhd', None),
    ('sdws',     '山东卫视',  '2029787903', '600002513', 'fhd', None),
    ('tjws',     '天津卫视',  '2019927003', '600152137', 'fhd', None),
    ('jlws',     '吉林卫视',  '2025561503', '600190405', 'fhd', None),
    ('saxws',    '陕西卫视',  '2029795103', '600190400', 'fhd', None),
    ('nxws',     '宁夏卫视',  '2025608503', '600190737', 'fhd', None),
    ('nmgws',    '内蒙古卫视','2025561203', '600190401', 'fhd', None),
    ('ynws',     '云南卫视',  '2025561303', '600190402', 'fhd', None),
    ('shanxiws', '山西卫视',  '2025560803', '600190407', 'fhd', None),
    ('qhws',     '青海卫视',  '2025559103', '600190406', 'fhd', None),
    ('xizangws', '西藏卫视',  '2025558003', '600190403', 'fhd', None),
    ('xjws',     '新疆卫视',  '2019927403', '600152138', 'fhd', None),
    ('cetv1',    'CETV-1',    '2022823801', '600171827', 'fhd', None),
    ('guoxue',   '国学频道',  '2029360403', '600213139', 'fhd', None),
    ('cctv4kb',  'CCTV-4K 备用', '', '', '', 'Live1704872878572161'),
    ('cgtnen',   'CGTN 英语备用', '', '', '', 'Live1719392219423280'),
]

CHANNEL_MAP = {c[0]: {'slug': c[0], 'name': c[1], 'sid': c[2], 'pid': c[3], 'defn': c[4], 'ysptp': c[5]} for c in CHANNELS}

FORCE_BK = {'cctv11', 'cctv12', 'cctv14', 'cctv15', 'cctv16', 'cctv164k',
            'cctv17', 'cctv4k', 'cctvfyjc', 'cctvdyjc', 'cctvhjjc'}

BACKEND_CHANNELS = {
    'cctv1', 'cctv2', 'cctv3', 'cctv4', 'cctv5', 'cctv5p',
    'cctv7', 'cctv8', 'cctv9', 'cctv10', 'cctv11', 'cctv12',
    'cctv13', 'cctv14', 'cctv15', 'cctv16', 'cctv17',
    'cctv4k', 'cctv8k', 'cctv164k',
    'cgtn', 'cgtnfr', 'cgtnru', 'cgtnar', 'cgtnes', 'cgtndoc',
}

TRUE_4K_CHANNELS = {'cctv4k', 'cctv8k', 'cctv164k'}


def _has_native(slug):
    info = CHANNEL_MAP.get(slug, {})
    return bool(info.get('sid')) and bool(info.get('pid'))


def _has_ysptp(slug):
    return bool(CHANNEL_MAP.get(slug, {}).get('ysptp'))


YSPTP_SLUG_SUFFIX = '_ys'


# ================================================================ 台标

LOGO_MIRRORS = [
    'https://cdn.jsdmirror.com/gh/fanmingming/live@main/tv/',
    'https://jsd.onmicrosoft.cn/gh/fanmingming/live@main/tv/',
    'https://gcore.jsdelivr.net/gh/fanmingming/live@main/tv/',
    'https://cdn.jsdelivr.net/gh/fanmingming/live@main/tv/',
    'https://ghproxy.net/https://raw.githubusercontent.com/fanmingming/live/main/tv/',
    'https://live.fanmingming.com/tv/',
]
LOGO_PROBE_FILE = 'CCTV1.png'
LOGO_TIMEOUT = 8
LOGO_MODE = 'auto'

_LOGO_BASE = LOGO_MIRRORS[0]
_LOGO_DONE = threading.Event()
_LOGO_START_LOCK = threading.Lock()
_LOGO_STARTED = False
_LOGO_CACHE = {}
_LOGO_CACHE_LOCK = threading.Lock()

_LOGO_PLACEHOLDER = base64.b64decode(
    'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII='
)

_LOGO_OVERRIDE = {
    'cctv5p': 'CCTV5+.png', 'cctv4k': 'CCTV4K.png', 'cctv8k': 'CCTV8K.png',
    'cctv164k': 'CCTV16.png', 'cctv4kb': 'CCTV4K.png',
    'cgtn': 'CGTN.png', 'cgtnen': 'CGTN.png',
    'cgtnfr': 'CGTN法语.png', 'cgtnru': 'CGTN俄语.png',
    'cgtnar': 'CGTN阿语.png', 'cgtnes': 'CGTN西语.png', 'cgtndoc': 'CGTN纪录.png',
    'cctvfyjc': '风云剧场.png', 'cctvdyjc': '第一剧场.png', 'cctvhjjc': '怀旧剧场.png',
    'cetv1': 'CETV1.png', 'guoxue': '国学.png',
}


def _logo_file(slug, name=''):
    if slug in _LOGO_OVERRIDE: return _LOGO_OVERRIDE[slug]
    m = re.match(r'^cctv(\d+)$', slug)
    if m: return 'CCTV%s.png' % m.group(1)
    if name and slug.endswith('ws'): return name + '.png'
    return ''


def _probe_logo_source():
    global _LOGO_BASE
    try:
        for base in LOGO_MIRRORS:
            try:
                req = urllib.request.Request(base + LOGO_PROBE_FILE,
                                             headers={'User-Agent': UA, 'Referer': 'https://live.cctv.cn/'})
                with urllib.request.urlopen(req, timeout=LOGO_TIMEOUT) as r:
                    head = r.read(16)
                if head[:4] == b'\x89PNG':
                    _LOGO_BASE = base; return
            except Exception: continue
        _LOGO_BASE = ''
    finally: _LOGO_DONE.set()


def _ensure_logo_source(wait=3.0):
    global _LOGO_STARTED
    with _LOGO_START_LOCK:
        if not _LOGO_STARTED:
            _LOGO_STARTED = True
            threading.Thread(target=_probe_logo_source, daemon=True).start()
    _LOGO_DONE.wait(wait)
    return _LOGO_BASE


def _local_logo_url(slug):
    return 'http://127.0.0.1:%d/logo/%s.png' % (_LOCAL_PORT, slug) if _LOCAL_PORT else ''


def _logo_url(slug, name=''):
    _ensure_logo_source(wait=0)
    if not _LOGO_DONE.is_set():
        return _local_logo_url(slug)
    fname = _logo_file(slug, name)
    base = _LOGO_BASE
    direct = (base + urllib.parse.quote(fname)) if (base and fname) else ''
    if LOGO_MODE == 'direct': return direct or _local_logo_url(slug)
    if LOGO_MODE == 'local': return _local_logo_url(slug) or direct
    return direct or _local_logo_url(slug)


def _fetch_logo_bytes(slug, name):
    fname = _logo_file(slug, name)
    if fname:
        bases = ([_LOGO_BASE] if _LOGO_BASE else []) + [b for b in LOGO_MIRRORS if b != _LOGO_BASE]
        q = urllib.parse.quote(fname)
        for base in bases:
            try:
                req = urllib.request.Request(base + q,
                                             headers={'User-Agent': UA, 'Referer': 'https://live.cctv.cn/'})
                with urllib.request.urlopen(req, timeout=LOGO_TIMEOUT) as r:
                    data = r.read()
                if data[:4] == b'\x89PNG': return data
            except Exception: continue
    return _LOGO_PLACEHOLDER


# ================================================================ 活流状态

class _ChannelState:
    def __init__(self, slug, name, sid, pid, defn, ysptp_live, mode):
        self.slug = slug; self.name = name
        self.sid = sid; self.pid = pid; self.defn = defn
        self.ysptp_live = ysptp_live
        self.lock = threading.Lock()
        self.segments = {}; self.order = deque(); self.seq = 0
        self.last_access = 0.0; self.thread = None
        self.last_error = ''
        self.mode = mode
        self._starting = False


CHANNEL_STATE = {}

for c in CHANNELS:
    slug, name, sid, pid, defn, ys = c[0], c[1], c[2], c[3], c[4], c[5]
    if sid and pid:
        native_mode = 'bk' if slug in FORCE_BK else 'jce'
        CHANNEL_STATE[slug] = _ChannelState(slug, name, sid, pid, defn, None, native_mode)
    if ys:
        ys_slug = slug + YSPTP_SLUG_SUFFIX
        CHANNEL_STATE[ys_slug] = _ChannelState(ys_slug, name + ' (YSPTP)', '', '', '', ys, 'ysptp')


def _seg_key(url, pdt):
    if pdt: return 'pdt:' + pdt
    p = urllib.parse.urlsplit(url)
    return p.scheme + '://' + p.netloc + p.path


def _append_segments(ch, segs):
    with ch.lock:
        for dur, pdt, url in segs:
            key = _seg_key(url, pdt)
            if key in ch.segments:
                ch.segments[key][3] = url
                continue
            ch.seq += 1
            ch.segments[key] = [ch.seq, dur, pdt, url]
            ch.order.append(key)
        while len(ch.order) > MAX_SEGS:
            ch.segments.pop(ch.order.popleft(), None)
        ch.last_error = ''


def _parse_m3u8(text, base_url):
    segs, dur, pdt = [], 6.0, ''
    for line in text.splitlines():
        line = line.strip()
        if line.startswith('#EXTINF:'):
            try: dur = float(line[len('#EXTINF:'):].split(',')[0])
            except ValueError: dur = 6.0
        elif line.startswith('#EXT-X-PROGRAM-DATE-TIME:'):
            pdt = line[len('#EXT-X-PROGRAM-DATE-TIME:'):]
        elif line and not line.startswith('#'):
            segs.append((dur, pdt, urllib.parse.urljoin(base_url, line)))
            pdt = ''
    return segs


def _jce_refresh(ch):
    now = int(time.time())
    m3u8_url = jce_timeshift_url(ch.pid, ch.sid, now - WINDOW, now, ch.defn)
    req = urllib.request.Request(m3u8_url, headers={'User-Agent': UA})
    with urllib.request.urlopen(req, timeout=20) as r:
        text = r.read().decode('utf-8', 'replace')
    segs = _parse_m3u8(text, m3u8_url)
    if not segs: raise RuntimeError('empty playlist')
    _append_segments(ch, segs)
    return True


def _bk_refresh(ch):
    urls = bk_playurls(ch.sid, ch.pid, ch.defn)
    last_err = ''
    for u in urls:
        try:
            req = urllib.request.Request(u, headers={
                'User-Agent': UA, 'Referer': 'https://live.cctv.cn/',
                'Accept': 'application/vnd.apple.mpegurl,application/json,*/*'})
            with urllib.request.urlopen(req, timeout=20) as r:
                text = r.read().decode('utf-8', 'replace'); final = r.geturl()
            lines = text.splitlines()
            for i, ln in enumerate(lines):
                if ln.strip().startswith('#EXT-X-STREAM-INF'):
                    for j in range(i + 1, len(lines)):
                        s = lines[j].strip()
                        if s and not s.startswith('#'):
                            sub = urllib.parse.urljoin(final, s)
                            req2 = urllib.request.Request(sub, headers={'User-Agent': UA})
                            with urllib.request.urlopen(req2, timeout=20) as r2:
                                text = r2.read().decode('utf-8', 'replace'); final = r2.geturl()
                            break
                    break
            segs = _parse_m3u8(text, final)
            if segs:
                _append_segments(ch, segs)
                return True
        except Exception as e:
            last_err = '%s: %s' % (type(e).__name__, e); continue
    raise RuntimeError(last_err or 'bk playlist failed')


def _ysptp_refresh(ch):
    e = ysptp_resolve(ch.ysptp_live, ch.slug)
    req = urllib.request.Request(e['final_url'], headers=e['headers'])
    with urllib.request.urlopen(req, timeout=20) as r:
        text = r.read().decode('utf-8', 'replace'); final = r.geturl()
    lines = text.splitlines()
    for i, ln in enumerate(lines):
        if ln.strip().startswith('#EXT-X-STREAM-INF'):
            for j in range(i + 1, len(lines)):
                s = lines[j].strip()
                if s and not s.startswith('#'):
                    sub = urllib.parse.urljoin(final, s)
                    req2 = urllib.request.Request(sub, headers=e['headers'])
                    with urllib.request.urlopen(req2, timeout=20) as r2:
                        text = r2.read().decode('utf-8', 'replace'); final = r2.geturl()
                    break
            break
    segs = _parse_m3u8(text, final)
    if not segs: raise RuntimeError('ysptp empty playlist')
    _append_segments(ch, segs)
    return True


def _refresh_native(ch):
    if ch.mode == 'bk':
        try: return _bk_refresh(ch)
        except Exception as e:
            _log('%s bk failed (%s), fallback jce' % (ch.slug, e))
            ch.mode = 'jce'
            return _jce_refresh(ch)
    try:
        return _jce_refresh(ch)
    except DeadHostError:
        ch.mode = 'bk'
        return _bk_refresh(ch)


def _refresh_once(ch):
    t0 = time.time()
    try:
        if ch.mode == 'ysptp':
            ok = _ysptp_refresh(ch)
        else:
            ok = _refresh_native(ch)
        _log('refresh %s: ok=%s %.2fs segs=%d'
             % (ch.slug, ok, time.time() - t0, len(ch.order)))
        return ok
    except Exception as e:
        ch.last_error = ('%s: %s' % (type(e).__name__, e))[:120]
        _log('refresh %s: FAIL %.2fs %s'
             % (ch.slug, time.time() - t0, ch.last_error))
        return False


def _refresh_loop(ch):
    fails = 0
    while time.time() - ch.last_access < IDLE_TIMEOUT:
        ok = _refresh_once(ch)
        fails = 0 if ok else fails + 1
        time.sleep(REFRESH_INTERVAL if fails < 3 else 15)


def _ensure_channel(ch):
    ch.last_access = time.time()
    with ch.lock:
        if ch._starting: return
        need_fetch = not ch.segments
        need_thread = ch.thread is None or not ch.thread.is_alive()
        if need_fetch or need_thread: ch._starting = True
        else: return
    try:
        if need_fetch: _refresh_once(ch)
        if need_thread:
            ch.thread = threading.Thread(target=_refresh_loop, args=(ch,), daemon=True)
            ch.thread.start()
    finally:
        with ch.lock: ch._starting = False


# ================================================================ 本地 HTTP 服务

_LOCAL_PORT = None
_LOCAL_LOCK = threading.Lock()


class _LocalHandler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'
    server_version = 'ysp-live-spider'

    def log_message(self, fmt, *args): pass

    def _send(self, code, body, ctype='text/plain; charset=utf-8', extra=None, cache=False):
        data = body.encode('utf-8') if isinstance(body, str) else body
        try:
            self.send_response(code)
            self.send_header('Content-Type', ctype)
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Access-Control-Allow-Origin', '*')
            self.send_header('Cache-Control', 'public, max-age=86400' if cache else 'no-cache, no-store')
            if extra:
                for k, v in extra.items(): self.send_header(k, v)
            self.end_headers()
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError): pass

    def do_HEAD(self):
        path = urllib.parse.urlparse(self.path).path
        for pat, ct in [(r'^/[\w]+\.m3u8$', 'application/vnd.apple.mpegurl'),
                        (r'^/logo/[\w]+\.png$', 'image/png')]:
            if re.match(pat, path):
                self.send_response(200); self.send_header('Content-Type', ct); self.end_headers(); return
        self.send_response(404); self.end_headers()

    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path
        if path == '/health':
            self._send(200, 'ok\n'); return
        if path == '/diag':
            lines = []
            for slug, ch in CHANNEL_STATE.items():
                with ch.lock: n = len(ch.order)
                lines.append('%s mode=%s segs=%d err=%s' % (slug, ch.mode, n, ch.last_error))
            lines.append('logo_source=%s' % (_LOGO_BASE or '(none)'))
            lines.append('aes_backend=%s' % (_AES_BACKEND or 'unknown'))
            self._send(200, '\n'.join(lines) + '\n'); return

        m = re.match(r'^/logo/([\w]+)\.png$', path)
        if m: self._serve_logo(m.group(1)); return
        m = re.match(r'^/([\w]+)\.m3u8$', path)
        if m: self._serve_m3u8(m.group(1)); return
        m = re.match(r'^/chunk/([\w]+)/(\d+)\.ts$', path)
        if m: self._serve_chunk(m.group(1), int(m.group(2))); return
        self._send(404, 'not found\n')

    def _serve_logo(self, slug):
        base = slug[:-len(YSPTP_SLUG_SUFFIX)] if slug.endswith(YSPTP_SLUG_SUFFIX) else slug
        info = CHANNEL_MAP.get(base)
        name = info['name'] if info else ''
        with _LOGO_CACHE_LOCK: data = _LOGO_CACHE.get(base)
        if data is None:
            data = _fetch_logo_bytes(base, name)
            with _LOGO_CACHE_LOCK: _LOGO_CACHE[base] = data
        self._send(200, data, 'image/png', cache=True)

    def _serve_m3u8(self, slug):
        ch = CHANNEL_STATE.get(slug)
        if not ch:
            self._send(404, 'unknown\n'); return
        _ensure_channel(ch)
        for _ in range(60):
            with ch.lock:
                if ch.order: break
            time.sleep(0.2)
        with ch.lock:
            keys = list(ch.order)
            segs = [ch.segments[k] for k in keys if k in ch.segments]
            window = segs[-PLAYLIST_WINDOW:] if segs else []
        if not window:
            self._send(503, 'no data: %s\n' % (ch.last_error or 'fetching')); return
        ch.last_access = time.time()
        port = _LOCAL_PORT
        target = max(6, int(max(s[1] for s in window) + 0.5))
        out = [
            '#EXTM3U', '#EXT-X-VERSION:3',
            '#EXT-X-TARGETDURATION:%d' % target,
            '#EXT-X-MEDIA-SEQUENCE:%d' % window[0][0],
            '#EXT-X-DISCONTINUITY-SEQUENCE:0',
            '#EXT-X-START:TIME-OFFSET=-15.0',
        ]
        for seq, dur, pdt, _url in window:
            if pdt: out.append('#EXT-X-PROGRAM-DATE-TIME:' + pdt)
            out.append('#EXTINF:%.3f,' % dur)
            out.append('http://127.0.0.1:%d/chunk/%s/%d.ts' % (port, slug, seq))
        self._send(200, '\n'.join(out) + '\n', 'application/vnd.apple.mpegurl')

    def _serve_chunk(self, slug, seq):
        ch = CHANNEL_STATE.get(slug)
        if not ch:
            self._send(404, 'unknown\n'); return
        ch.last_access = time.time()
        with ch.lock:
            url = None
            for k in ch.order:
                s = ch.segments.get(k)
                if s and s[0] == seq:
                    url = s[3]; break
        if not url:
            self._send(404, 'chunk expired\n'); return

        try:
            req = urllib.request.Request(url, headers={
                'User-Agent': UA, 'Referer': 'https://live.cctv.cn/'})
            with urllib.request.urlopen(req, timeout=20) as r:
                self.send_response(200)
                self.send_header('Content-Type', 'video/mp2t')
                self.send_header('Transfer-Encoding', 'chunked')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.send_header('Cache-Control', 'no-cache, no-store')
                self.end_headers()
                while True:
                    buf = r.read(64 * 1024)
                    if not buf: break
                    self.wfile.write(('%x\r\n' % len(buf)).encode('ascii'))
                    self.wfile.write(buf)
                    self.wfile.write(b'\r\n')
                self.wfile.write(b'0\r\n\r\n')
        except urllib.error.HTTPError as e:
            try: self._send(e.code, 'chunk HTTP %d\n' % e.code)
            except Exception: pass
        except Exception as e:
            try: self._send(502, 'chunk error: %s\n' % e)
            except Exception: pass


def _ensure_local_server():
    global _LOCAL_PORT
    with _LOCAL_LOCK:
        if _LOCAL_PORT is not None: return _LOCAL_PORT
        ports = list(range(LOCAL_PORT_PREFERRED, LOCAL_PORT_PREFERRED + LOCAL_PORT_RANGE)) + [0]
        last_err = None
        for port in ports:
            try:
                srv = ThreadingHTTPServer(('127.0.0.1', port), _LocalHandler)
                srv.daemon_threads = True
                _LOCAL_PORT = srv.server_address[1]
                threading.Thread(target=srv.serve_forever, daemon=True).start()
                try: _detect_aes_backend()
                except Exception: pass
                return _LOCAL_PORT
            except OSError as e:
                last_err = e; continue
        raise RuntimeError('无法绑定端口: %s' % last_err)


# ================================================================ Spider

class Spider(SpiderBase):
    def __init__(self):
        super(Spider, self).__init__()
        self.brandActor = "📺 央视频直播"
        self.brandDirector = "ysp-live-multi"

    def init(self, extend=""):
        try: _ensure_local_server()
        except Exception as e: _log('本地服务失败: %s' % e)
        try: _ensure_logo_source(wait=0)
        except Exception: pass
        return True

    def getName(self): return "央视频直播"
    def isVideoFormat(self, url): return True
    def manualVideoCheck(self): return False
    def destroy(self): pass

    def homeContent(self, filter):
        return {"class": [
            {"type_name": "央视频道", "type_id": "cctv"},
            {"type_name": "卫视频道", "type_id": "satellite"},
            {"type_name": "CGTN",     "type_id": "cgtn"},
            {"type_name": "4K超清",    "type_id": "4k"},
            {"type_name": "付费剧场",  "type_id": "premium"},
            {"type_name": "其他",      "type_id": "other"},
        ], "filters": {}}

    def homeVideoContent(self): return {"list": []}

    def _classify(self, base_slug):
        cats = []
        if (re.match(r'^cctv\d+$', base_slug) or base_slug == 'cctv5p') and base_slug not in TRUE_4K_CHANNELS:
            cats.append('cctv')
        if base_slug in TRUE_4K_CHANNELS or base_slug == 'cctv4kb':
            cats.append('4k')
        if base_slug in ('cctvfyjc', 'cctvdyjc', 'cctvhjjc'):
            cats.append('premium')
        if base_slug.endswith('ws') or base_slug == 'cetv1':
            cats.append('satellite')
        if base_slug.startswith('cgtn'):
            cats.append('cgtn')
        if base_slug == 'guoxue':
            cats.append('other')
        return cats

    def _make_card(self, slug, name):
        tag = ''
        if slug in TRUE_4K_CHANNELS or slug == 'cctv4kb':
            tag = '真4K'
        elif slug in BACKEND_CHANNELS:
            tag = '高码率'
        return {"vod_id": slug, "vod_name": name, "vod_pic": _logo_url(slug, name),
                "vod_remarks": format_remarks("央视频", tag),
                "style": {"type": "rect", "ratio": 1.78}}

    def categoryContent(self, tid, pg, filter, extend):
        cards = [self._make_card(s, n) for s, n, _si, _p, _d, _y in CHANNELS if tid in self._classify(s)]
        return {"page": 1, "pagecount": 1, "limit": len(cards), "total": len(cards), "list": cards}

    def detailContent(self, ids):
        slug = ids[0] if isinstance(ids, (list, tuple)) else str(ids)
        info = CHANNEL_MAP.get(slug)
        if not info: return {"list": []}
        try:
            _ensure_local_server()
            if _has_native(slug):
                ch = CHANNEL_STATE.get(slug)
                if ch:
                    _ensure_channel(ch)
                    t0 = time.time()
                    while time.time() - t0 < 3.0:
                        with ch.lock:
                            if ch.order: break
                        time.sleep(0.1)
            if _has_ysptp(slug):
                ch_ys = CHANNEL_STATE.get(slug + YSPTP_SLUG_SUFFIX)
                if ch_ys:
                    threading.Thread(
                        target=lambda c: _ensure_channel(c),
                        args=(ch_ys,), daemon=True).start()
        except Exception: pass

        lines_1 = []
        lines_2 = []
        if _has_native(slug):
            lines_1.append("超清$%s" % slug)
        if _has_ysptp(slug):
            lines_2.append("超清(YSPTP)$%s%s" % (slug, YSPTP_SLUG_SUFFIX))
        if not lines_1 and not lines_2:
            lines_1.append("超清$%s" % slug)

        play_from_parts = []
        play_url_parts = []
        if lines_1:
            play_from_parts.append("央视频源1")
            play_url_parts.append("#".join(lines_1))
        if lines_2:
            play_from_parts.append("央视频源2")
            play_url_parts.append("#".join(lines_2))

        full_desc = "【📺 央视频直播】\n频道: %s\n源1: Script 1 (JCE/bk)\n源2: Script 2 (YSPTP)" % info['name']
        escaped = (full_desc.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))

        vod = {
            "vod_id": slug, "vod_name": info['name'],
            "vod_pic": _logo_url(slug, info['name']),
            "vod_actor": self.brandActor, "vod_director": self.brandDirector,
            "vod_remarks": format_remarks("央视频", "直播"),
            "vod_content": escaped,
            "vod_play_from": "$$$".join(play_from_parts),
            "vod_play_url": "$$$".join(play_url_parts),
        }
        return {"list": [vod]}

    def playerContent(self, flag, id, vipFlags):
        del flag, vipFlags
        slug = str(id).strip()
        if slug.startswith('http://') or slug.startswith('https://'):
            return {"parse": 0, "playUrl": "", "url": slug,
                    "header": {"User-Agent": UA, "Referer": "https://live.cctv.cn/"}}
        if slug not in CHANNEL_STATE:
            return {"parse": 0, "playUrl": "", "url": "", "header": {}}
        try:
            port = _ensure_local_server()
        except Exception:
            return {"parse": 0, "playUrl": "", "url": "", "header": {}}
        ch = CHANNEL_STATE[slug]
        _ensure_channel(ch)
        for _ in range(40):
            with ch.lock:
                if ch.order: break
            time.sleep(0.5)
        return {
            "parse": 0, "playUrl": "",
            "url": "http://127.0.0.1:%d/%s.m3u8" % (port, slug),
            "header": {"User-Agent": UA, "Referer": "https://live.cctv.cn/"},
        }

    def searchContent(self, key, quick, pg="1"):
        del quick, pg
        key = (key or "").strip().lower()
        cards = []
        if key:
            for s, n, _si, _p, _d, _y in CHANNELS:
                if key in n.lower() or key in s.lower():
                    cards.append(self._make_card(s, n))
        return {"page": 1, "pagecount": 1, "limit": len(cards), "total": len(cards), "list": cards}

    def action(self, action): return {"msg": "ok"}
    def liveContent(self): return ""
    def localProxy(self, params): return [404, "text/plain; charset=utf-8", "Proxy not configured"]


if __name__ == '__main__':
    import argparse, sys
    ap = argparse.ArgumentParser()
    ap.add_argument('--port', type=int, default=LOCAL_PORT_PREFERRED)
    ap.add_argument('--once', action='store_true')
    ap.add_argument('--test-slug', default='cctv1')
    args = ap.parse_args()
    LOCAL_PORT_PREFERRED = args.port

    sp = Spider(); sp.init()
    print("homeContent:", json.dumps(sp.homeContent({}), ensure_ascii=False))
    cc = sp.categoryContent('cctv', 1, {}, {})
    print("cctv: %d 个频道" % cc['total'])
    dc = sp.detailContent([args.test_slug])
    if dc['list']:
        v = dc['list'][0]
        print("detail:", v['vod_name'])
        print("  play_from:", v['vod_play_from'])
        print("  play_url :", v['vod_play_url'])
    pc1 = sp.playerContent('', args.test_slug, [])
    print("playerContent native:", json.dumps(pc1, ensure_ascii=False))
    pc2 = sp.playerContent('', args.test_slug + YSPTP_SLUG_SUFFIX, [])
    print("playerContent ysptp :", json.dumps(pc2, ensure_ascii=False))
    if args.once:
        time.sleep(4)
        for label, pc in [('native', pc1), ('ysptp', pc2)]:
            if not pc['url']:
                print("%s: no url" % label); continue
            try:
                with urllib.request.urlopen(pc['url'], timeout=15) as r:
                    body = r.read().decode('utf-8', 'replace')
                print("%s OK: %s" % (label, body[:200].replace('\n', ' ')))
            except Exception as e:
                print("%s 错误: %s" % (label, e))
        sys.exit(0)
    port = _LOCAL_PORT
    print("\n常驻: http://127.0.0.1:%d/%s.m3u8" % (port, args.test_slug))
    try:
        while True: time.sleep(3600)
    except KeyboardInterrupt: pass
