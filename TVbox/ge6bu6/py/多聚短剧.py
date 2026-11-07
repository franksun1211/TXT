import sys
import os
import re
import json
import time
import struct
import base64
import random
import hashlib
import binascii
import socket
import threading
import tempfile
import zlib
from pathlib import Path
from urllib.parse import (quote, urlencode, parse_qs, urlparse,
                          urlsplit, parse_qsl, unquote)
from datetime import datetime, timezone, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from concurrent.futures import ThreadPoolExecutor
from typing import Optional, Dict, Any, List, Tuple

sys.path.append('..')

try:
    import requests
except ImportError:
    requests = None

try:
    from Crypto.Cipher import AES
    from Crypto.Util import Counter
except ImportError:
    AES = None
    Counter = None

try:
    from base.spider import Spider
except ImportError:
    class Spider:
        pass

try:
    import warnings
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    warnings.filterwarnings('ignore', message='Unverified HTTPS request')
except Exception:
    pass

CONFIG_DEVICE_ID = '829902912863503360'
CONFIG_INSTALL_ID = '250404579274111008'
CONFIG_PLATFORM = 'android'
CONFIG_CACHE_SECONDS = 3600

_CURRENT_DOMAIN = 'http://127.0.0.1:9877'
_EMBEDDED_SERVER = None
_EMBEDDED_PORT = 0

def _pkcs7_pad(data, block_size=16):
    pad_len = block_size - (len(data) % block_size)
    return data + bytes([pad_len] * pad_len)

def _sm3_rotl(x, n, bits=32):
    n = n % bits
    return ((x << n) | (x >> (bits - n))) & ((1 << bits) - 1)

def _sm3_p0(x):
    return x ^ _sm3_rotl(x, 9) ^ _sm3_rotl(x, 17)

def _sm3_p1(x):
    return x ^ _sm3_rotl(x, 15) ^ _sm3_rotl(x, 23)

def _sm3_ff(x, y, z, j):
    if j < 16:
        return x ^ y ^ z
    return (x & y) | (x & z) | (y & z)

def _sm3_gg(x, y, z, j):
    if j < 16:
        return x ^ y ^ z
    return (x & y) | ((~x) & z)

def _sm3_hash(msg_list):
    IV = [0x7380166f, 0x4914b2b9, 0x172442d7, 0xda8a0600,
          0xa96f30bc, 0x163138aa, 0xe38dee4d, 0xb0fb0e4e]
    msg = bytes(msg_list)
    length = len(msg)
    msg += b'\x80'
    while len(msg) % 64 != 56:
        msg += b'\x00'
    msg += (length * 8).to_bytes(8, 'big')
    V = list(IV)
    for i in range(0, len(msg), 64):
        block = msg[i:i+64]
        W = [int.from_bytes(block[j*4:j*4+4], 'big') for j in range(16)]
        for j in range(16, 68):
            w = _sm3_p1(W[j-16] ^ W[j-9] ^ _sm3_rotl(W[j-3], 15)) ^ _sm3_rotl(W[j-13], 7) ^ W[j-6]
            W.append(w)
        W_prime = [W[j] ^ W[j+4] for j in range(64)]
        A, B, C, D, E, F, G, H = V
        for j in range(64):
            t_j = 0x79cc4519 if j < 16 else 0x7a879d8a
            SS1 = _sm3_rotl((_sm3_rotl(A, 12) + E + _sm3_rotl(t_j, j % 32)) & 0xFFFFFFFF, 7)
            SS2 = SS1 ^ _sm3_rotl(A, 12)
            TT1 = (_sm3_ff(A, B, C, j) + D + SS2 + W_prime[j]) & 0xFFFFFFFF
            TT2 = (_sm3_gg(E, F, G, j) + H + SS1 + W[j]) & 0xFFFFFFFF
            D = C
            C = _sm3_rotl(B, 9)
            B = A
            A = TT1
            H = G
            G = _sm3_rotl(F, 19)
            F = E
            E = _sm3_p0(TT2)
        V = [(V[k] ^ [A, B, C, D, E, F, G, H][k]) & 0xFFFFFFFF for k in range(8)]
    return ''.join('%08x' % x for x in V)

class SM3:
    def __init__(self, data=b''):
        if isinstance(data, str):
            data = data.encode("utf-8")
        self._data = bytearray(data)
    def update(self, data):
        self._data.extend(data)
    def digest(self):
        h = _sm3_hash([b for b in self._data])
        return bytes.fromhex(h)

def _enc_varint(value):
    buf = bytearray()
    while value > 0x7F:
        buf.append((value & 0x7F) | 0x80)
        value >>= 7
    buf.append(value & 0x7F)
    return bytes(buf)

def _zigzag32(n):
    return ((n << 1) ^ (n >> 31)) & 0xFFFFFFFF

def _zigzag64(n):
    return ((n << 1) ^ (n >> 63)) & 0xFFFFFFFFFFFFFFFF

def _enc_sint32(field, value):
    if value == 0:
        return b''
    return _enc_varint(field << 3) + _enc_varint(_zigzag32(value))

def _enc_sint64(field, value):
    if value == 0:
        return b''
    return _enc_varint(field << 3) + _enc_varint(_zigzag64(value))

def _enc_string(field, value):
    if not value:
        return b''
    data = value.encode('utf-8')
    return _enc_varint((field << 3) | 2) + _enc_varint(len(data)) + data

def _enc_bytes(field, value):
    if not value:
        return b''
    data = bytes(value)
    return _enc_varint((field << 3) | 2) + _enc_varint(len(data)) + data

def _enc_float(field, value):
    if value == 0.0:
        return b''
    return _enc_varint((field << 3) | 5) + struct.pack('<f', value)

def _enc_msg(field, data):
    if not data:
        return b''
    return _enc_varint((field << 3) | 2) + _enc_varint(len(data)) + data

class MedushaAlgorithmCount:
    def __init__(self, sign_count=0, report_count=0, setting_count=0, unknown4=0, unknown5=0):
        self.sign_count = sign_count
        self.report_count = report_count
        self.setting_count = setting_count
        self.unknown4 = unknown4
        self.unknown5 = unknown5
    def __bytes__(self):
        return b''.join([
            _enc_sint32(1, self.sign_count),
            _enc_sint32(2, self.report_count),
            _enc_sint32(3, self.setting_count),
            _enc_sint32(4, self.unknown4),
            _enc_sint32(5, self.unknown5),
        ])

class Report:
    def __init__(self, time=0, state=0, code=0, times=0, unknown6=0):
        self.time = time
        self.state = state
        self.code = code
        self.times = times
        self.unknown6 = unknown6
    def __bytes__(self):
        return b''.join([
            _enc_sint64(1, self.time),
            _enc_sint32(2, self.state),
            _enc_sint32(4, self.code),
            _enc_sint32(5, self.times),
            _enc_sint32(6, self.unknown6),
        ])

class Device:
    def __init__(self, **kw):
        self.__dict__.update({
            'd1': 0, 'collect_stat': 0, 'aid': '', 'device_id': '',
            'sec_device_token': '', 'app_version': '', 'battery': 0,
            'battery2': 0, 'battery_health': 0, 'battery_changed': 0,
            'network': '', 'tz': '', 'lan': '', 'cpu': 0, 'resolution': '',
            'sdcard': 0.0, 'sdcard_used': 0.0, 'memory': 0.0, 'memory2': 0.0,
            'data': 0.0, 'data_used': 0.0, 'os_version': '', 'brightness': 0,
            'volume': 0, 'ts': 0, 'ts2': 0, 'ts3': 0, 'ts4': 0, 'usb': 0,
            'hw_version': '', 'brand': '', 'board': '', 'product_name': '',
            'product_device': '', 'product_manufacturer': '', 'hardware': '',
            'unknown38': 0, 'unknown40': 0,
        })
        self.__dict__.update(kw)
    def __bytes__(self):
        return b''.join([
            _enc_sint32(1, self.d1), _enc_sint32(2, self.collect_stat),
            _enc_string(3, self.aid), _enc_string(4, self.device_id),
            _enc_string(5, self.sec_device_token), _enc_string(6, self.app_version),
            _enc_sint32(7, self.battery), _enc_sint32(8, self.battery2),
            _enc_sint32(9, self.battery_health), _enc_sint32(10, self.battery_changed),
            _enc_string(11, self.network), _enc_string(12, self.tz),
            _enc_string(13, self.lan), _enc_sint32(14, self.cpu),
            _enc_string(15, self.resolution),
            _enc_float(16, self.sdcard), _enc_float(17, self.sdcard_used),
            _enc_float(18, self.memory), _enc_float(19, self.memory2),
            _enc_float(20, self.data), _enc_float(21, self.data_used),
            _enc_string(22, self.os_version),
            _enc_sint32(23, self.brightness), _enc_sint32(24, self.volume),
            _enc_sint64(25, self.ts), _enc_sint64(26, self.ts2),
            _enc_sint64(27, self.ts3), _enc_sint64(28, self.ts4),
            _enc_sint32(29, self.usb), _enc_string(30, self.hw_version),
            _enc_string(31, self.brand), _enc_string(32, self.board),
            _enc_string(33, self.product_name), _enc_string(34, self.product_device),
            _enc_string(35, self.product_manufacturer), _enc_string(36, self.hardware),
            _enc_sint32(38, self.unknown38), _enc_sint32(40, self.unknown40),
        ])

class Env:
    def __init__(self, **kw):
        self.launch_time = kw.get('launch_time', 0)
        self.unknown2 = kw.get('unknown2', 0)
        self.unknown3 = kw.get('unknown3', 0)
        self.unknown5 = kw.get('unknown5', 0)
        self.version = kw.get('version', '')
        self.pid = kw.get('pid', 0)
        self.device = kw.get('device', None)
        self.report = kw.get('report', None)
        self.app_version = kw.get('app_version', '')
        self.unknown15 = kw.get('unknown15', 0)
        self.unknown16 = kw.get('unknown16', 0)
        self.unknown18 = kw.get('unknown18', 0)
        self.unknown19 = kw.get('unknown19', 0)
        self.unknown20 = kw.get('unknown20', 0)
        self.unknown21 = kw.get('unknown21', 0)
    def __bytes__(self):
        parts = [
            _enc_sint32(1, self.launch_time),
            _enc_sint32(2, self.unknown2),
            _enc_sint32(3, self.unknown3),
            _enc_sint32(5, self.unknown5),
            _enc_string(6, self.version),
            _enc_sint32(7, self.pid),
        ]
        if self.device is not None:
            parts.append(_enc_msg(12, bytes(self.device)))
        if self.report is not None:
            parts.append(_enc_msg(13, bytes(self.report)))
        parts.append(_enc_string(14, self.app_version))
        parts.extend([
            _enc_sint32(15, self.unknown15), _enc_sint32(16, self.unknown16),
            _enc_sint32(18, self.unknown18), _enc_sint32(19, self.unknown19),
            _enc_sint32(20, self.unknown20), _enc_sint32(21, self.unknown21),
        ])
        return b''.join(parts)

class Medusa:
    def __init__(self, **kw):
        self.magic = kw.get('magic', b'')
        self.version = kw.get('version', 0)
        self.rand = kw.get('rand', 0)
        self.ms_app_id = kw.get('ms_app_id', '')
        self.device_id = kw.get('device_id', '')
        self.license_id = kw.get('license_id', '')
        self.app_version = kw.get('app_version', '')
        self.sdk_version_str = kw.get('sdk_version_str', '')
        self.sdk_version = kw.get('sdk_version', 0)
        self.xg_seed_bytes = kw.get('xg_seed_bytes', b'')
        self.time = kw.get('time', 0)
        self.query_body_ts_hash = kw.get('query_body_ts_hash', b'')
        self.query_sm3 = kw.get('query_sm3', b'')
        self.request = kw.get('request', None)
        self.sec_device_token = kw.get('sec_device_token', '')
        self.time2 = kw.get('time2', 0)
        self.lanusk_hash = kw.get('lanusk_hash', b'')
        self.query_body_hash_sm3 = kw.get('query_body_hash_sm3', b'')
        self.psk_version = kw.get('psk_version', '')
        self.call_type = kw.get('call_type', 0)
        self.env = kw.get('env', None)
        self.unknown24 = kw.get('unknown24', '')
        self.original = kw.get('original', '')
    def __bytes__(self):
        parts = [
            _enc_bytes(1, self.magic),
            _enc_sint32(2, self.version),
            _enc_sint32(3, self.rand),
            _enc_string(4, self.ms_app_id),
            _enc_string(5, self.device_id),
            _enc_string(6, self.license_id),
            _enc_string(7, self.app_version),
            _enc_string(8, self.sdk_version_str),
            _enc_sint32(9, self.sdk_version),
            _enc_bytes(10, self.xg_seed_bytes),
            _enc_sint32(12, self.time),
            _enc_bytes(13, self.query_body_ts_hash),
            _enc_bytes(14, self.query_sm3),
        ]
        if self.request is not None:
            parts.append(_enc_msg(15, bytes(self.request)))
        parts.extend([
            _enc_string(16, self.sec_device_token),
            _enc_sint32(17, self.time2),
            _enc_bytes(18, self.lanusk_hash),
            _enc_bytes(19, self.query_body_hash_sm3),
            _enc_string(20, self.psk_version),
            _enc_sint32(21, self.call_type),
        ])
        if self.env is not None:
            parts.append(_enc_msg(23, bytes(self.env)))
        parts.append(_enc_string(24, self.unknown24))
        parts.append(_enc_string(26, self.original))
        return b''.join(parts)

class EecryptParams:
    def __init__(self):
        self.khronos = ''
        self.argus = ''
        self.medusa = ''
        self.gorgon = ''
        self.ladon = ''
        self.helios = ''

_BRANCH_ONE_B64 = (
        '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'
        'DfozaEg6sMIrVto1fQki/5PgVwSRS45gD4EVeorz0O7SCvz60tbyCIx76i/lWDeBcWIwgQ2JkBNMZgfs8ojTetL+CmVwMuEwX4cpCtTSjvvy6HqSxBuYWl/eCijtapgMuD4JCJi8bjjpKg1aH8BblpwK2txansILGliXCWjoui6dDDgPmloe2MKTXD25nuwKaAgo4xhB'
        'dfFVSmZP+W2tlvNbBQlH3AbF8XV49kUdUeJwYUtIVnIZYUP15YljZy8R9pjNZXNRQk0e4fDOKfFgn2WPE7RAXxGN1c3k1RrwYlfbRXp0/4XrmthDDoxdQde1EOXMyNQVRedcibXelIWaQv+Jn+UU1V3JRLeIn576gYqX3URToHOMqwyUA/G87Vn1w3uEg1nF63m89fSr'
        'dKQLVIgDk5ALjKMDc6xUgOftVVhYDnIEiwt0lKNTqHBQWO4N4oVf5T59Y9D5Ww0EI6jDcHx3VC2doalkNiIs1Xk7/eNmCF1e/WM4DX3T4YJhLHQ80ZWZCWX4e1s95daZctQtlGGEOQCI9b9uJNkQOFGJ4zFI8ZNLvkhW5bkC2uyVw0ftS2L02CC5jhXwbwSpisQIOJX4'
        'm2VMQpXzxO/rYcqoTWwjNnP/arsD0vuozBBqo1DARDvY1XRMiKLRBX9aKjP83tyGvar8AGrLv9P7NrqWVFf7wAyiZHDLSl3YhJdsfrn4bUM2RXqgWDa61stRB60qrUOBlsgQrtJbIFKQIvjM8Qf87qnXxIsKzM+Tk9puQBZsSTt1g90v78yGQ7G+VdOuyKiNLBLMFN1c'
        'h4kGYtIqRaiP9xPi5vsedSuhzyvhdqMfzfrmLN52wopd/UKOY6r3RxAq0c0Eil4XEdyEziod+aLnd+MlyMAkjv7Vgl3qbjDdIN7hgJO/nAn1ba/3gQttn628kJGvbAu9gJ/1gP0lwtTKjCbJgNWOwiQk/q/0nb1skwuBPhgr3dPF3bbHPdDd3rcqGZaW6OWpQJFWQJWq'
        'kOZV6pWVVJKq6JXmQbTG3SvdGtM9Gbfe0ik93MdWQuXpkZaolLdNysrVDh9fDrfVHstdy057Wf1OFXgrOXl7FShNOf9ak7Xh1QBfa19dD8vJHk/Wtk9eH9fKtskNOXtP/SpZFXjNa5eBZisTFivPZxOCFpVpZbgms9Gz4L6yZNDjsr0lu7i+4dInZ7CxFymDlhFqZ89r'
        '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'
    )

def _get_branch_one_table():
    raw = zlib.decompress(base64.b64decode(_BRANCH_ONE_B64))
    return [list(raw[i:i+24]) for i in range(0, len(raw), 24)]

import hashlib
import json
import random
import time
from urllib.parse import urlencode, unquote

def extract_url_params(url) -> dict:
    params = {}
    parsed_url_list = url.split("?")[1].split("&") if "?" in url else url.split("&")
    for param in parsed_url_list:
        splited_param = param.split("=")
        params[splited_param[0]] = unquote(splited_param[1] if len(splited_param) == 2 else "")
    return params

def url_encode(data):
    param = urlencode(data)
    param = param.replace("+", "%20")
    param = param.replace("%2A", "*")
    return param

def get_params_encrypturl(url, params:dict=None, devices={}, common=None, rticket_override=None, ts_override=None):
    x_common = {}
    if params:
        x_params = params.copy()
        if common:
            x_params.update(extract_url_params(common))
            x_common = extract_url_params(common)
        if devices:
            for k, v in devices.items():
                if k in x_params:
                    x_params[k] = v
                if k in x_common:
                    x_common[k] = v
                elif "did" in x_params and k == "device_id":
                    x_params["did"] = v
    else:
        x_params: dict = extract_url_params(url) if ("?" in url and len(url.split("?")) == 2 and url.split("?")[1]) else params
        if common:
            x_common = extract_url_params(common)
            x_params.update(x_common)
        if devices:
            for k, v in devices.items():
                if k in x_params:
                    x_params[k] = v
                if k in x_common:
                    x_common[k] = v
                elif "did" in x_params and k == "device_id":
                    x_params["did"] = v

    x_params["ts"] = ts_override if ts_override is not None else int(time.time())
    x_params["_rticket"] = rticket_override if rticket_override is not None else int(time.time() * 1e3)
    if x_common:
        x_common["ts"] = int(time.time())
        x_common["_rticket"] = int(time.time() * 1e3)
    eurl = url.split("?")[0] + "?" + url_encode(x_params) if "?" in url else url + "?" + url_encode(x_params)
    url_params: dict = x_params

    return eurl, url_params, url_encode(x_common)

def xssstub_hash_md5_hex(data, dataType:str=None):
    if not data:
        return str()
    if dataType == "md5":
        return data
    md5 = hashlib.md5()
    if isinstance(data, str):
        md5.update(data.encode('utf-8'))
    elif isinstance(data, bytes):
        md5.update(data)
    else:
        if dataType == "application/json; charset=UTF-8":
            md5.update(json.dumps(data, ensure_ascii=False, separators=(',', ':')).encode('utf-8'))
        else:
            md5.update(urlencode(data).encode('utf-8'))
    xt = md5.hexdigest()
    return xt.upper()

import binascii

xtime = lambda a: (((a << 1) ^ 0x1B) & 0xFF) if (a & 0x80) else (a << 1)

def rol(num, shift):
    shift %= 32
    return ((num << shift) | (num >> (32 - shift))) & 0xFFFFFFFF

def rl8(x: int, k: int) -> int:
    n = 8
    s = k & (n - 1)
    return ((x << s) | (x >> (n - s))) & 0xff

def ror32(value, count):
    count %= 32
    low = value << (32 - count)
    value >>= count
    value |= low
    value &= 0xFFFFFFFF
    return value

def ror(value, count):
    count %= 64
    low = value << (64 - count)
    value >>= count
    value |= low
    value &= 0xFFFFFFFFFFFFFFFF
    return value

def get_key_hash(key, rand):
    to_hash = bytearray(68)
    to_hash[:32] = key
    to_hash[32:36] = rand.to_bytes(4, byteorder='little')
    to_hash[36:] = key

    d1 = (rand >> 16) & 0x000000ff
    d2 = (d1 << 11) | (rand >> 24)
    d2 ^= (d1 >> 5) ^ d1
    d2 = ~d2 & 0xffffffff

    return SM3(to_hash).digest(), d2.to_bytes(4, "little")

def split_blocks(message, block_size=16, require_padding=True):
    assert len(message) % block_size == 0 or not require_padding
    return [message[i:i + 16] for i in range(0, len(message), block_size)]

def add_round_key(s, k):
    for i in range(4):
        for j in range(4):
            s[i][j] ^= k[i][j]

def add_round_key_con(s, k, con):
    for i in range(4):
        for j in range(4):
            s[i][j] ^= k[i][con[j]]

def bytes2matrix(text):
    return [list(text[i:i + 4]) for i in range(0, len(text), 4)]

def xor_bytes(a, b):
    return bytearray(i ^ j for i, j in zip(a, b))

def matrix2bytes(matrix):
    return bytearray(sum(matrix, []))

def mix_single_column(a, i):
    t = a[0][i] ^ a[1][i] ^ a[2][i] ^ a[3][i]
    u = a[0][i]
    a[0][i] ^= t ^ xtime(a[0][i] ^ a[1][i])
    a[1][i] ^= t ^ xtime(a[1][i] ^ a[2][i])
    a[2][i] ^= t ^ xtime(a[2][i] ^ a[3][i])
    a[3][i] ^= t ^ xtime(a[3][i] ^ u)

def mix_columns(s):
    for i in range(4):
        mix_single_column(s, i)

def inv_mix_columns(s):
    for i in range(0, 4):
        inv_mix_single_column(s, i)

def inv_mix_single_column(a, i):
    u = xtime(xtime(a[0][i] ^ a[2][i]))
    v = xtime(xtime(a[1][i] ^ a[3][i]))
    a[0][i] ^= u
    a[1][i] ^= v
    a[2][i] ^= u
    a[3][i] ^= v

    mix_single_column(a, i)

s_box = b''.join([
    b'\xFA\x7D\x08\x6B\x9C\x59\xB3\x4B\x04\x5F\x39\xD0\x38\x4A\x91\x99',
    b'\x00\x67\xA6\x20\x9F\xF5\x4D\x82\x73\x26\xEE\xDF\x18\x66\x83\x33',
    b'\x80\x03\x19\xFB\xD9\xFE\xAE\xAA\xA9\xB0\x52\xC6\x0B\xF3\x79\x25',
    b'\x4E\x78\xB4\x36\xAC\x5D\x1A\x27\x9E\x88\xDB\xBD\x3C\x63\xEC\x49',
    b'\x15\xC1\x30\x1F\xDC\xB8\x56\xD4\x6C\xCD\xCA\x09\x43\xC8\x35\xA3',
    b'\xEF\x1E\xF4\x96\xD2\xFC\x0E\x72\x7B\x94\x84\xD1\xEA\x45\x5A\x62',
    b'\x02\x3F\xD3\x12\x81\x34\x2B\xDD\x7E\xE6\x28\xF2\xA5\x46\x13\x01',
    b'\x3B\x21\xF6\x61\x37\x29\x2A\x0D\xED\x8C\xAF\xBF\x9D\x5C\xBB\x24',
    b'\x76\x0F\x75\xE4\x53\x89\xE1\x98\x8D\xB1\x9A\x65\x70\x4F\x54\x4C',
    b'\x58\xAB\x6E\x6F\x8B\x23\xC4\x07\x11\x0C\xBA\xCF\xA0\xA4\x8E\xD8',
    b'\x05\x3D\x14\xB2\xDA\x74\xC3\xD7\xE7\xBE\xD6\x7F\xDE\x48\x16\x3E',
    b'\x85\x90\xA1\x55\xB7\x77\x42\x22\xC9\x86\x50\x2E\x17\xF9\x64\x31',
    b'\x2C\x9B\xF1\x6D\x1C\x44\x68\xE3\xE9\xA8\x93\x97\xCB\x32\x57\xEB',
    b'\xE5\x71\x6A\xAD\xC0\xCC\xC7\xC5\xFD\x60\x1D\xA2\x2D\x47\xA7\xE2',
    b'\x51\x69\x5E\x7A\xCE\x0A\x41\xB6\x95\x8F\xF7\xB9\x87\xE0\x3A\x06',
    b'\x10\x8A\xB5\xF8\x5B\xD5\xF0\xBC\x92\xFF\x7C\x2F\xC2\xE8\x1B\x40',
    b'\xEC\x1B\xDA\xBD\xBA\x98\x91\x0C\xB2\x2B\x83\x41\x34\x67\xFB\x0A',
    b'\xD8\x76\xB5\x46\x05\x59\x61\x23\x75\x90\x87\x2A\xE3\x50\x15\x4C',
    b'\xAC\xB1\x79\xEB\xAE\xE5\x95\x47\x04\x68\xF0\x86\x3D\x51\x8B\x0F',
    b'\xCA\x8E\xE4\xB9\x4E\xF2\x12\x82\xBC\x0E\xD5\xF7\xEF\x28\x25\xCF',
    b'\x5B\x5D\xE9\x6A\x55\x02\xE1\x33\xBE\x93\xE7\xF5\xAD\x9D\x3E\x39',
    b'\x24\xA8\xE2\xFA\x17\x57\xD0\x7A\x0D\x08\x30\xD6\xB8\xA3\x8D\xFD',
    b'\x07\x9A\xC4\x1E\x6E\x22\x64\x97\xD2\x1D\xB0\xBF\x45\x66\x3F\x6C',
    b'\xDD\xDB\x27\x80\xA7\x11\xDC\xA6\xC5\x52\xF8\xC0\xB6\xC8\x5C\x00',
    b'\x73\x60\x7B\xA0\x19\x13\xAA\xC9\x35\x48\x4B\xD3\xA4\xCD\x9F\x99',
    b'\xF3\x10\x44\x40\x54\x7E\x29\xF4\x06\x1F\xA2\xAB\xA1\x2F\x3C\xF6',
    b'\xAF\x85\x62\x36\x21\x7F\x5E\xDF\x20\x1A\xB3\xB4\xE6\xFF\x72\x84',
    b'\x8F\x65\x26\x94\x5A\x77\xEA\x43\x78\xC7\x4A\xCC\x2C\x14\x6B\xC6',
    b'\xE8\x74\x53\xFC\xD4\x1C\xCE\x31\x70\x03\x18\x8C\x96\x38\x32\x89',
    b'\xF1\x3A\x5F\xD7\xF9\xA9\x69\xB7\x63\x37\x58\xC2\x3B\xC3\x71\xCB',
    b'\x9E\x92\x01\x8A\x0B\x4D\x88\x9B\xBB\x4F\x6D\x6F\xE0\xFE\xA5\x49',
    b'\xDE\x56\x16\x09\xED\x9C\xC1\x2D\xEE\x81\x7D\xD9\x7C\xD1\x2E\x42',
    b'\x5B\x4D\xC1\xA6\x5D\xEA\x44\xFD\x45\x4E\x1B\xA1\x3F\xD1\x89\xE1',
    b'\x7D\x2F\xAA\xDB\xAB\xAD\x59\xCB\xB1\xCE\x9A\x28\xC9\xE0\xF6\x70',
    b'\x39\x4A\xD7\xFF\x30\xF5\xDD\xBC\x57\x3B\x11\x8D\xB2\xEE\x00\xB6',
    b'\xE6\x1A\x5A\x7C\xF9\xDE\xC4\xCD\x2E\x80\xBB\xB9\x4C\xA5\x9F\x84',
    b'\x08\xC6\x6F\x42\x6C\xF0\x27\xE7\x8B\x3A\x9C\x51\xFB\x67\x21\x75',
    b'\x41\x31\xA7\xCA\x20\x43\x2A\xB7\xBF\xD9\x7A\xF2\xB5\xF8\x8C\x2C',
    b'\x23\x83\x4F\x8F\x60\xA0\x04\x13\x37\x14\xE3\x01\xC5\x63\x66\x5C',
    b'\x74\x81\xDF\x58\xBD\x68\x90\x3D\xD2\xB3\x34\xF4\x19\x93\x32\x29',
    b'\xD6\x49\xAE\x0D\x4B\xD8\x07\x9E\xAC\x1E\x2D\x0B\x40\xB8\x72\xBA',
    b'\x76\x10\x71\xA8\xE4\x56\x1D\x48\xFE\xE5\xC2\x47\x91\xDA\x87\x26',
    b'\x9D\x1F\x88\x6B\xC0\x98\xBE\x25\x09\x97\x33\xA3\x85\x16\x5E\x7F',
    b'\xDC\x6E\x54\xE9\xF7\xA9\xC8\xE8\xC3\x77\xD0\x82\x2B\xEC\x02\x62',
    b'\x8A\x92\x0E\x3E\xB0\x0F\x05\xF3\xF1\x96\x78\x38\x86\x36\x18\x3C',
    b'\x24\xCF\x0A\xB4\x53\xCC\x61\x65\xA4\xC7\x94\xD5\x15\x7E\x6D\xEF',
    b'\x79\x22\x35\x12\x6A\x8E\x52\x06\x55\x7B\x46\x64\x50\x95\xE2\x0C',
    b'\xED\xD3\x17\x03\xA2\x9B\x99\xEB\x1C\xFC\xAF\xD4\x73\x69\xFA\x5F',
    b'\xF7\x2C\x1E\xBF\xC8\xE1\xF3\x9F\x76\x80\x71\x48\xAA\x94\xAD\x64',
    b'\xFB\x89\xC6\x60\xC3\x32\xB3\x4D\xD2\xE0\x44\xDD\x5F\xA8\xB1\xC7',
    b'\x68\x23\x34\xC9\x6D\x12\x7F\xB7\xEB\x15\xBE\xA9\xD1\x78\x93\xA0',
    b'\x0C\x92\xA4\xD7\x47\xE3\x8A\xC2\x70\xAB\x26\x41\x9A\x79\xA7\xD8',
    b'\x14\x85\x8F\xC0\x6F\x56\xD0\x8C\x11\xB9\x2E\x3C\xE2\x9D\xCF\x0E',
    b'\xDE\x03\x5D\x46\x3E\xCD\x38\x43\x0F\x33\x5A\xD9\x1A\x65\x6C\x22',
    b'\x3B\xFC\x30\xA6\x88\xEA\x37\xA2\xB4\x8D\x8E\x51\x9C\xD6\x40\xEE',
    b'\xF9\xF8\x84\xF4\xAE\x97\xE9\xCA\x0A\x45\x67\x57\x04\x2F\x83\x5C',
    b'\xD5\xC5\xC4\x82\xB6\xA3\x91\x98\x1F\x4A\xAC\x96\x81\x6E\xCB\x1B',
    b'\x09\x08\xAF\x18\x95\x49\x7D\x54\xED\xFA\x16\x31\x3A\xDA\xB8\x66',
    b'\xF5\xA5\xF1\xFE\x10\x01\x06\x74\xCC\x63\xDF\x7C\x28\x25\xF6\xCE',
    b'\xB2\x4F\x8B\xE5\xBC\x87\x69\xBB\x86\x21\x07\x00\x36\xE7\x0B\x50',
    b'\x59\x9B\x1C\xE8\x62\x58\x19\x61\xF2\xBD\x27\x5E\xBA\x1D\xE6\x99',
    b'\x42\x3D\x0D\x2A\xB5\xDC\x5B\x29\xF0\x2D\x4C\x53\x7B\x6A\x73\x4E',
    b'\x3F\x75\xFF\x4B\xA1\x35\x17\x55\x72\x39\x20\xD3\xB0\xFD\xEF\x02',
    b'\xEC\x77\x7E\xE4\x2B\xDB\x90\xC1\x05\x9E\x7A\xD4\x52\x6B\x24\x13'])

inv_s_box = bytearray(1024)

for i in range(4):
    idx = i * 256
    for j in range(256):
        inv_s_box[idx + s_box[idx + j]] = j

class AES_V3():
    r_con = [[1, 0, 2, 3], [1, 3, 0, 2], [0, 1, 3, 2], [1, 0, 2, 3]]
    r_con2 = [[1, 0, 2, 3], [2, 0, 3, 1], [0, 1, 3, 2], [1, 0, 2, 3]]
    r_orders = [[0, 9, 14, 11, 4, 13, 2, 7, 8, 1, 6, 15, 12, 5, 10, 3],
                [0, 9, 14, 15, 4, 13, 2, 7, 8, 1, 6, 3, 12, 5, 10, 11],
                [0, 9, 14, 7, 4, 13, 2, 11, 8, 1, 6, 3, 12, 5, 10, 15],
                [0, 9, 14, 11, 4, 13, 2, 7, 8, 1, 6, 15, 12, 5, 10, 3]]

    def __init__(self, aes_key, khronos):
        self.word_size = khronos & 3
        self.aes_key = aes_key

        self.s_box = s_box[self.word_size << 8:]
        self.inv_s_box = inv_s_box[self.word_size << 8:]

        self.master_key = self._expand_key()
        self._key_matrices = bytes2matrix(self.master_key)
        self.con = self.r_con[self.word_size]
        self.con2 = self.r_con2[self.word_size]

        self.order = self.r_orders[self.word_size]

    def _expand_key(self):
        init_values = [0xca025ddc, 0x823dc546, 0xc9420583, 0xc298225f]
        init_value = init_values[self.word_size]
        mk = bytearray(init_value.to_bytes(4, "little"))
        mk = mk * 4

        mk = xor_bytes(mk, self.aes_key)
        mk += bytearray(32)

        rounds = 8

        for i in range(4, 12):
            idx = 4 * (i - 1)
            k0, k1, k2, k3 = mk[idx], mk[idx + 1], mk[idx + 2], mk[idx + 3]

            if i & 3 == 0:
                k00 = (init_value >> (rounds & 24)) ^ self.s_box[k1]
                k1 = self.s_box[k2]
                k2 = self.s_box[k3]
                k3 = self.s_box[k0]
                k0 = k00 & 0xff
            rounds += 2

            mk[idx + 4] = k0 ^ mk[idx - 12]
            mk[idx + 5] = k1 ^ mk[idx - 11]
            mk[idx + 6] = k2 ^ mk[idx - 10]
            mk[idx + 7] = k3 ^ mk[idx - 9]
        return mk

    @staticmethod
    def sum_data(data):
        key = bytearray(32)
        for i in range(31):
            idx = i * 8
            n0 = (data[idx] >> 4) & 2
            n1 = n0 | data[idx + 1] & 64
            n2 = n1 | (data[idx + 2] >> 2) & 1
            n3 = n2 | (data[idx + 3] << 3) & -128
            n4 = n3 | (data[idx + 4] >> 1) & 4
            n5 = n4 | (data[idx + 5] << 3) & 16
            n6 = n5 | (data[idx + 6] << 5) & 32
            n7 = n6 | (data[idx + 7] >> 4) & 8
            key[i] = (n7 & 0xff)

        key[31] = 1
        return key

    @staticmethod
    def mix_columns(data, key):
        data = bytearray(data)

        for i in range(31):
            kk = key[i]
            idx = i * 8
            data[idx + 0] = (data[idx + 0] & -33) | ((kk << 4) & 0xff & 32)
            data[idx + 1] = (data[idx + 1] & -65) | (kk & 64)
            data[idx + 2] = (data[idx + 2] & -5) | ((kk * 4) & 4)
            data[idx + 3] = (data[idx + 3] & -17) | ((kk >> 3) & 16)
            data[idx + 4] = (data[idx + 4] & -9) | ((kk + kk) & 8)
            data[idx + 5] = (data[idx + 5] & -3) | ((kk >> 3) & 2)
            data[idx + 6] = (data[idx + 6] & -2) | ((kk >> 5) & 1)
            data[idx + 7] = (data[idx + 7] & 127) | ((kk << 4) & 0xff & -128)

        return data

    def encrypt(self, data, iv):
        plaintext = self.sum_data(data)
        blocks = []
        previous = iv
        for plaintext_block in split_blocks(plaintext):
            x = xor_bytes(plaintext_block, previous)
            block = self.encrypt_block(x)
            blocks.append(block)
            previous = block

        key = b''.join(blocks)
        data = self.mix_columns(data, key)
        return key[-1:] + data

    def encrypt_block(self, plaintext):
        plain_state = bytes2matrix(plaintext)

        add_round_key_con(plain_state, self._key_matrices[0:4], self.con2)

        for i in range(1, 3):
            self.sub_bytes(plain_state)
            self.shift_rows(plain_state)
            if i == 1:
                self.shift_rows_con(plain_state, self.con2)
                mix_columns(plain_state)

            add_round_key_con(plain_state, self._key_matrices[i * 4:], self.con2)

        add_round_key(plain_state, self._key_matrices[4:])

        return matrix2bytes(plain_state)

    def decrypt(self, ciphertext, iv, data):
        assert len(iv) == 16

        blocks = []
        previous = iv

        for ciphertext_block in split_blocks(ciphertext):
            dc = xor_bytes(previous, self.decrypt_block(ciphertext_block))
            blocks.append(dc)
            previous = ciphertext_block

        key = b''.join(blocks)
        data = self.mix_columns(data, key)
        return data

    def decrypt_block(self, ciphertext):
        assert len(ciphertext) == 16
        cipher_state = bytes2matrix(ciphertext)

        add_round_key(cipher_state, self._key_matrices[4:])

        for i in range(2, 0, -1):
            add_round_key_con(cipher_state, self._key_matrices[i * 4:], self.con2)

            if i == 1:
                inv_mix_columns(cipher_state)
                self.shift_rows_con(cipher_state, self.con)

            self.inv_shift_rows(cipher_state)
            self.inv_sub_bytes(cipher_state)

        add_round_key_con(cipher_state, self._key_matrices[0:4], self.con2)

        return matrix2bytes(cipher_state)

    def shift_rows_con(self, s, c):
        for i in range(4):
            s[i][0], s[i][1], s[i][2], s[i][3] = s[i][c[0]], s[i][c[1]], s[i][c[2]], s[i][c[3]]

    def shift_rows(self, s):
        bs = matrix2bytes(s)
        for i in range(4):
            for j in range(4):
                s[i][j] = bs[self.order[i * 4 + j]]

    def inv_shift_rows(self, s):
        order = bytearray(16)
        for i in range(16):
            order[self.order[i]] = i

        bs = matrix2bytes(s)
        for i in range(4):
            for j in range(4):
                s[i][j] = bs[order[i * 4 + j]]

    def sub_bytes(self, s):
        for i in range(4):
            for j in range(4):
                s[i][j] = self.s_box[s[i][j]]

        s[0], s[1], s[2], s[3] = s[self.con2[0]], s[self.con2[1]], s[self.con2[2]], s[self.con2[3]]

    def inv_sub_bytes(self, s):
        for i in range(4):
            for j in range(4):
                s[i][j] = self.inv_s_box[s[i][j]]
        s[0], s[1], s[2], s[3] = s[self.con[0]], s[self.con[1]], s[self.con[2]], s[self.con[3]]

SV = [0xa7aefe20, 0x7149f1d6, 0x47e4ca07, 0xe9b58f67, 0x93b924de, 0xc614d0f5, 0x38afe0ef, 0xb2bbad73,
      0xe24444c3, 0x9d3aec9b, 0xdf7b37e4, 0xd8b16d40, 0xf8ac31b8, 0x76b9a90b, 0x31d833ee, 0x953fce64,
      0x6a6b2b48, 0x8c138276, 0x6d24a010, 0x18da124b, 0xbbeb82ee, 0x39f7ea56, 0x149f4fe1, 0x229946bc,
      0x327f309, 0xf4f50e66, 0x62d569aa, 0x78419f92, 0x4db088a7, 0x7430a018, 0xf31d88f4, 0x2f4ed651,
      0x9b13fe59, 0x45c5910d, 0x374bf0ec, 0xd5a5b69e, 0xefaceb16, 0x714f4811, 0x4c98233b, 0xc9e6e7b1,
      0xd0623b3f, 0xdf5e4f6f, 0xccb31244, 0xaddab856, 0x6faf9c9e, 0x62804e10, 0x6fa7e29d, 0x7e429d51,
      0xcdb31e01, 0xb339e419, 0xe497a8a3, 0xebeca6bc, 0x1746cd57, 0xfbf4f54a, 0xd5ea2a8b, 0xb4f02ed2,
      0x512b192, 0xefad4d29, 0xd59bec05, 0x2a1436e2, 0xfaea73c1, 0x1ebdbae8, 0x8ff257bd, 0x2d59351d]

def leftCircularShift(k, bits):
    bits = bits % 32
    k = k % (2 ** 32)
    upper = (k << bits) % (2 ** 32)
    result = upper | (k >> (32 - (bits)))
    return (result)

def blockDivide(block, chunks):
    result = []
    size = len(block) // chunks
    for i in range(0, chunks):
        result.append(int.from_bytes(block[i * size:(i + 1) * size], byteorder="little"))
    return (result)

def F(X, Y, Z):
    return ((X & Y) | ((~X) & Z))

def G(X, Y, Z):
    return ((X & Z) | (Y & (~Z)))

def H(X, Y, Z):
    return (X ^ Y ^ Z)

def I(X, Y, Z):
    return (Y ^ (X | (~Z))) & 0xffffffff

def FF(a, b, c, d, M, s, t):
    result = b + leftCircularShift((a + F(b, c, d) + M + t), s)

    return (result)

def GG(a, b, c, d, M, s, t):
    result = b + leftCircularShift((a + G(b, c, d) + M + t), s)
    return (result)

def HH(a, b, c, d, M, s, t):
    result = b + leftCircularShift((a + H(b, c, d) + M + t), s)
    return (result)

def II(a, b, c, d, M, s, t):
    result = b + leftCircularShift((a + I(b, c, d) + M + t), s)
    return (result)

def md5sum(msg, n=0):
    A = 0x79e0f2fb
    B = 0xc8b52570
    C = 0xebc2f8cd
    D = 0x7c104d93

    a = A
    b = B
    c = C
    d = D
    block = msg[:64]
    M = blockDivide(block, 16)
    a = FF(a, b, c, d, M[0], 7, SV[0])
    d = FF(d, a, b, c, M[13], 12, SV[1])
    c = FF(c, d, a, b, M[14], 17, SV[2])
    b = FF(b, c, d, a, M[12], 22, SV[3])
    a = FF(a, b, c, d, M[11], 7, SV[4])
    d = FF(d, a, b, c, M[10], 12, SV[5])
    c = FF(c, d, a, b, M[9], 17, SV[6])
    b = FF(b, c, d, a, M[7], 22, SV[7])
    a = FF(a, b, c, d, M[8], 7, SV[8])
    d = FF(d, a, b, c, M[6], 12, SV[9])
    c = FF(c, d, a, b, M[5], 17, SV[10])
    b = FF(b, c, d, a, M[4], 22, SV[11])
    a = FF(a, b, c, d, M[3], 7, SV[12])
    d = FF(d, a, b, c, M[2], 12, SV[13])
    c = FF(c, d, a, b, M[1], 17, SV[14])
    b = FF(b, c, d, a, M[15], 22, SV[15])

    a = GG(a, b, c, d, M[0], 5, SV[16])
    d = GG(d, a, b, c, M[5], 9, SV[17])
    c = GG(c, d, a, b, M[10], 14, SV[18])
    b = GG(b, c, d, a, M[1], 20, SV[19])
    a = GG(a, b, c, d, M[12], 5, SV[20])
    d = GG(d, a, b, c, M[11], 9, SV[21])
    c = GG(c, d, a, b, M[15], 14, SV[22])
    b = GG(b, c, d, a, M[2], 20, SV[23])
    a = GG(a, b, c, d, M[9], 5, SV[24])
    d = GG(d, a, b, c, M[13], 9, SV[25])
    c = GG(c, d, a, b, M[3], 14, SV[26])
    b = GG(b, c, d, a, M[8], 20, SV[27])
    a = GG(a, b, c, d, M[6], 5, SV[28])
    d = GG(d, a, b, c, M[7], 9, SV[29])
    c = GG(c, d, a, b, M[4], 14, SV[30])
    b = GG(b, c, d, a, M[14], 20, SV[31])

    a = HH(a, b, c, d, M[8], 4, SV[32])
    d = HH(d, a, b, c, M[5], 11, SV[33])
    c = HH(c, d, a, b, M[12], 16, SV[34])
    b = HH(b, c, d, a, M[13], 23, SV[35])
    a = HH(a, b, c, d, M[1], 4, SV[36])
    d = HH(d, a, b, c, M[12], 11, SV[37])
    c = HH(c, d, a, b, M[7], 16, SV[38])
    b = HH(b, c, d, a, M[14], 23, SV[39])
    a = HH(a, b, c, d, M[10], 4, SV[40])
    d = HH(d, a, b, c, M[0], 11, SV[41])
    c = HH(c, d, a, b, M[2], 16, SV[42])
    b = HH(b, c, d, a, M[6], 23, SV[43])
    a = HH(a, b, c, d, M[4], 4, SV[44])
    d = HH(d, a, b, c, M[11], 11, SV[45])
    c = HH(c, d, a, b, M[15], 16, SV[46])
    b = HH(b, c, d, a, M[3], 23, SV[47])

    a = II(a, b, c, d, M[8], 6, SV[48])
    d = II(d, a, b, c, M[6], 10, SV[49])
    c = II(c, d, a, b, M[15], 15, SV[50])
    b = II(b, c, d, a, M[5], 21, SV[51])
    a = II(a, b, c, d, M[14], 6, SV[52])
    d = II(d, a, b, c, M[9], 10, SV[53])
    c = II(c, d, a, b, M[10], 15, SV[54])
    b = II(b, c, d, a, M[2], 21, SV[55])
    a = II(a, b, c, d, M[2], 6, SV[56])
    d = II(d, a, b, c, M[13], 10, SV[57])
    c = II(c, d, a, b, M[7], 15, SV[58])
    b = II(b, c, d, a, M[12], 21, SV[59])
    a = II(a, b, c, d, M[4], 6, SV[60])
    d = II(d, a, b, c, M[1], 10, SV[61])
    c = II(c, d, a, b, M[11], 15, SV[62])
    b = II(b, c, d, a, M[3], 21, SV[63])
    A = (A + a) % (2 ** 32) ^ 0x19be4866
    B = (B + b) % (2 ** 32) ^ 0xe85986b4
    C = (C + c) % (2 ** 32) ^ 0xe19b326e
    D = (D + d) % (2 ** 32) ^ 0x71d1d7d4

    result = bytearray(
        A.to_bytes(4, "little") + B.to_bytes(4, "little") + C.to_bytes(4, "little") + D.to_bytes(4, "little"))

    result += sum_md5(result).to_bytes(4, "little")
    return result

def sum_md5(data):
    check_sum = 0x20220420
    for i in range(12):
        if i % 2 == 0:
            temp = (check_sum >> 3) ^ check_sum
            check_sum = data[i] ^ (check_sum << 7)
        else:
            temp = (check_sum >> 5) ^ check_sum
            check_sum = data[i] | (check_sum << 11)
            check_sum ^= 0xffffffff

        check_sum ^= temp
        check_sum &= 0xffffffff

    check_sum |= 4
    check_sum ^= 0x1000000
    return check_sum

SV2 = [0xa7aefe20, 0x7149f1d6, 0x47e4ca07, 0xe9b58f67, 0x93b924de, 0xc614d0f5, 0x38afe0ef, 0xb2bbad73,
       0xe24444c3, 0x9d3aec9b, 0xdf7b37e4, 0xd8b16d40, 0xf8ac31b8, 0x76b9a90b, 0x31d833ee, 0x953fce64,
       0x353595a4, 0x4609c13b, 0x36925008, 0x8c6d0925, 0x5df5c177, 0x1cfbf52b, 0x8a4fa7f0, 0x114ca35e,
       0x8193f984, 0x7a7a8733, 0x316ab4d5, 0x3c20cfc9, 0xa6d84453, 0x3a18500c, 0x798ec47a, 0x97a76b28,
       0x66c4ff96, 0x51716443, 0xdd2fc3b, 0xb5696da7, 0xbbeb3ac5, 0x5c53d204, 0xd32608ce, 0x7279b9ec,
       0xf4188ecf, 0xf7d793db, 0x332cc491, 0xab76ae15, 0x9bebe727, 0x18a01384, 0x5be9f8a7, 0x5f90a754,
       0x39b663c0, 0x36673c83, 0x7c92f514, 0x9d7d94d7, 0xe2e8d9aa, 0x5f7e9ea9, 0x7abd4551, 0x569e05da,
       0x40a25632, 0x3df5a9a5, 0xbab37d80, 0x454286dc, 0x3f5d4e78, 0x3d7b75d, 0xb1fe4af7, 0xa5ab26a3]

def md5sum_v3(msg, count_v2, orders, count_v1, n=0):
    count = count_v2 & 0xff

    sv = [0] * 64
    for i in range(64):
        sv[i] = ror32(SV2[i], count_v1)

    start = [
        ror32(0x79e0f2fb, count),
        ror32(0xc8b52570, count),
        ror32(0xebc2f8cd, count),
        ror32(0x7c104d93, count)
    ]

    count = (count_v2 + 6) & 0xff
    end = [
        ror32(0x19be4866, count),
        ror32(0xe85986b4, count),
        ror32(0xe19b326e, count),
        ror32(0x71d1d7d4, count)
    ]
    A = start[0]
    B = start[1]
    C = start[2]
    D = start[3]

    a = A
    b = B
    c = C
    d = D
    block = msg[:64]
    M = blockDivide(block, 16)

    order1 = orders[:16]
    order2 = orders[16:32]
    order3 = orders[32:48]
    order4 = orders[48:]
    a = FF(a, b, c, d, M[order1[0]], 7, sv[0])
    d = FF(d, a, b, c, M[order1[1]], 12, sv[1])
    c = FF(c, d, a, b, M[order1[2]], 17, sv[2])
    b = FF(b, c, d, a, M[order1[3]], 22, sv[3])
    a = FF(a, b, c, d, M[order1[4]], 7, sv[4])
    d = FF(d, a, b, c, M[order1[5]], 12, sv[5])
    c = FF(c, d, a, b, M[order1[6]], 17, sv[6])
    b = FF(b, c, d, a, M[order1[7]], 22, sv[7])
    a = FF(a, b, c, d, M[order1[8]], 7, sv[8])
    d = FF(d, a, b, c, M[order1[9]], 12, sv[9])
    c = FF(c, d, a, b, M[order1[10]], 17, sv[10])
    b = FF(b, c, d, a, M[order1[11]], 22, sv[11])
    a = FF(a, b, c, d, M[order1[12]], 7, sv[12])
    d = FF(d, a, b, c, M[order1[13]], 12, sv[13])
    c = FF(c, d, a, b, M[order1[14]], 17, sv[14])
    b = FF(b, c, d, a, M[order1[15]], 22, sv[15])

    a = GG(a, b, c, d, M[order2[0]], 5, sv[16])
    d = GG(d, a, b, c, M[order2[1]], 9, sv[17])
    c = GG(c, d, a, b, M[order2[2]], 14, sv[18])
    b = GG(b, c, d, a, M[order2[3]], 20, sv[19])
    a = GG(a, b, c, d, M[order2[4]], 5, sv[20])
    d = GG(d, a, b, c, M[order2[5]], 9, sv[21])
    c = GG(c, d, a, b, M[order2[6]], 14, sv[22])
    b = GG(b, c, d, a, M[order2[7]], 20, sv[23])
    a = GG(a, b, c, d, M[order2[8]], 5, sv[24])
    d = GG(d, a, b, c, M[order2[9]], 9, sv[25])
    c = GG(c, d, a, b, M[order2[10]], 14, sv[26])
    b = GG(b, c, d, a, M[order2[11]], 20, sv[27])
    a = GG(a, b, c, d, M[order2[12]], 5, sv[28])
    d = GG(d, a, b, c, M[order2[13]], 9, sv[29])
    c = GG(c, d, a, b, M[order2[14]], 14, sv[30])
    b = GG(b, c, d, a, M[order2[15]], 20, sv[31])

    a = HH(a, b, c, d, M[order3[0]], 4, sv[32])
    d = HH(d, a, b, c, M[order3[1]], 11, sv[33])
    c = HH(c, d, a, b, M[order3[2]], 16, sv[34])
    b = HH(b, c, d, a, M[order3[3]], 23, sv[35])
    a = HH(a, b, c, d, M[order3[4]], 4, sv[36])
    d = HH(d, a, b, c, M[order3[5]], 11, sv[37])
    c = HH(c, d, a, b, M[order3[6]], 16, sv[38])
    b = HH(b, c, d, a, M[order3[7]], 23, sv[39])
    a = HH(a, b, c, d, M[order3[8]], 4, sv[40])
    d = HH(d, a, b, c, M[order3[9]], 11, sv[41])
    c = HH(c, d, a, b, M[order3[10]], 16, sv[42])
    b = HH(b, c, d, a, M[order3[11]], 23, sv[43])
    a = HH(a, b, c, d, M[order3[12]], 4, sv[44])
    d = HH(d, a, b, c, M[order3[13]], 11, sv[45])
    c = HH(c, d, a, b, M[order3[14]], 16, sv[46])
    b = HH(b, c, d, a, M[order3[15]], 23, sv[47])

    a = II(a, b, c, d, M[order4[0]], 6, sv[48])
    d = II(d, a, b, c, M[order4[1]], 10, sv[49])
    c = II(c, d, a, b, M[order4[2]], 15, sv[50])
    b = II(b, c, d, a, M[order4[3]], 21, sv[51])
    a = II(a, b, c, d, M[order4[4]], 6, sv[52])
    d = II(d, a, b, c, M[order4[5]], 10, sv[53])
    c = II(c, d, a, b, M[order4[6]], 15, sv[54])
    b = II(b, c, d, a, M[order4[7]], 21, sv[55])
    a = II(a, b, c, d, M[order4[8]], 6, sv[56])
    d = II(d, a, b, c, M[order4[9]], 10, sv[57])
    c = II(c, d, a, b, M[order4[10]], 15, sv[58])
    b = II(b, c, d, a, M[order4[11]], 21, sv[59])
    a = II(a, b, c, d, M[order4[12]], 6, sv[60])
    d = II(d, a, b, c, M[order4[13]], 10, sv[61])
    c = II(c, d, a, b, M[order4[14]], 15, sv[62])
    b = II(b, c, d, a, M[order4[15]], 21, sv[63])

    A = (A + a) % (2 ** 32) ^ end[0]
    B = (B + b) % (2 ** 32) ^ end[1]
    C = (C + c) % (2 ** 32) ^ end[2]
    D = (D + d) % (2 ** 32) ^ end[3]

    result = bytearray(
        A.to_bytes(4, "little") + B.to_bytes(4, "little") + C.to_bytes(4, "little") + D.to_bytes(4, "little"))

    result += sum_md5(result).to_bytes(4, "little")
    return result

def bxor(b1, b2):
    b3 = bytearray(len(b1))
    for i in range(len(b1)):
        b3[i] = b1[i] ^ b2[i]
    return b3

def get_iv(iv, data):
    for i in range(len(data)):
        if i & 1 == 0:
            iv = (iv >> 4) ^ iv ^ (iv << 6) ^ data[i]
        else:
            iv = ~((iv >> 7) ^ iv ^ (data[i] | iv << 12))
        iv = iv & 0xffffffff
    return iv

def hash_f13(query_sm3, body_md5_bytes, ts_bytes, khronos):
    iv = get_iv(0x20230928, query_sm3)
    iv = get_iv(iv, body_md5_bytes)
    iv = get_iv(iv, ts_bytes)

    iv_v0 = ((iv & 15) * 171) >> 9
    branch = (iv & 15) - ((iv_v0 * 3) & 0xff)
    if branch == 0:
        return branch_0(iv_v0, khronos, query_sm3, body_md5_bytes, ts_bytes)
    elif branch == 1:
        return branch_1(iv_v0, khronos, query_sm3, body_md5_bytes, ts_bytes)
    elif branch == 2:
        return branch_2(iv, khronos, query_sm3, body_md5_bytes, ts_bytes)
    else:
        raise Exception("no branch: " + str(branch))

def branch_0(iv_v0, khronos, query_sm3, body_md5_bytes, ts_bytes):
    tt01 = [0xc4a78580, 0xb3c0fd39, 0xc58c5686, 0xc9aa3ba7, 0xf5a7adf2, 0x963c2ed1]
    iv_v1 = tt01[iv_v0]

    count_v1 = (iv_v1 + khronos + 1) & 0xff
    count_v2 = (iv_v1 + khronos) & 0xffffffff

    tt02 = [0xebb64faf, 0x7aadcc2, 0xcf3187bf, 0xe01138ff, 0x6d0bfcff, 0x5a30a3be, 0xb41ad638, 0x34180eb8, 0xf233eb6f,
            0xb1a584cc, 0xccc30dc7, 0x47d1db51, 0xd55653de, 0x70a84fa1, 0x57473c12, 0xf76f0288, 0x2c077f0a, 0xda0dcad0,
            0xfbb86f6c, 0xfdc4cf00, 0x688a020d, 0xe676c6a6, 0x8cd6338b, 0x1a3c8d0e, 0xcce8b06b, 0x6ad0ed0b, 0xa0522717,
            0xdc71ac83, 0x2285db71, 0xd5b4dda6, 0x736f8650, 0x6560306c, 0x617ce2a6, 0xe423417e, 0xa40e143, 0x544e4032,
            0x88dffb2a, 0x716c1ae0, 0x4c467a88, 0x5b23bb3, 0xe1d0b866, 0xbaa3dcb8, 0xae3374d3, 0xc3381a50, 0x1702f75b,
            0xfe6da368, 0xf0b4cf48, 0x4e0ffbb8, 0x72aad10d, 0x26c53a3d, 0xf2bce0f6, 0xb4557581, 0x4a257fdd, 0x8c3182a2,
            0xab0b3b86, 0x3d5dfb14, 0x4f103634, 0xd37b52d7, 0x444eff16, 0xeb0a33d1, 0x6ca86f6e, 0x284ba7, 0x8387cfa,
            0x5fb37586]

    tt03 = [0] * 64
    for i in range(0, 64):
        tt03[i] = ror32(tt02[i], count_v1) & 0xffffffff

    n0 = (count_v2 + 2) & 7

    pad = bytearray(4)
    seed = bytes([0xfa, 0x45, 0x61, 0xd7])
    for i in range(4):
        v = int.from_bytes(bytes([seed[i], seed[i]]), 'little')
        v = v >> n0
        pad[i] = v & 0xff

    count_v2 = count_v2 & 0xff
    init_value = [
        ror32(0x7aba4fc8, count_v2), ror32(0x67166507, count_v2),
        ror32(0x6403fa00, count_v2), ror32(0x340f512f, count_v2),
        ror32(984304912, count_v2), ror32(3005047866, count_v2),
        ror32(2874125293, count_v2), ror32(2152413264, count_v2)
    ]

    data = query_sm3 + body_md5_bytes + ts_bytes + pad + bytes.fromhex(' 00 00 00 00 00 00 01 a0')
    di = [0] * (len(data) // 4)
    for i in range(len(data) // 4):
        di[i] = int.from_bytes(data[i * 4:i * 4 + 4], "big")

    di0 = di[0]
    for i in range(112):
        di1, di14 = di[i + 1], di[i + 14]
        r_di1 = rol(di1, 14) ^ rol(di1, 25) ^ (di1 >> 3)
        r_di2 = rol(di14, 13) ^ rol(di14, 15) ^ (di14 >> 10)
        di0 = di0 + di[i + 9] + r_di1 + r_di2
        di.append(di0 & 0xffffffff)
        di0 = di1

    if iv_v0 == 5:
        v_j = branch0_xor(init_value, iv_v1, di, tt03, 100, 2, 0, 3, 5, 4, 6, 7, 2, 1, 5)
    elif iv_v0 == 4:
        v_j = branch0_xor(init_value, iv_v1, di, tt03, 96, 0, 5, 6, 7, 3, 1, 2, 5, 4, 4)
    elif iv_v0 == 3:
        v_j = branch0_xor(init_value, iv_v1, di, tt03, 99, 3, 6, 2, 4, 5, 1, 0, 0, 7, 6)
    elif iv_v0 == 2:
        v_j = branch0_xor(init_value, iv_v1, di, tt03, 96, 7, 6, 2, 1, 4, 0, 5, 4, 3, 5)
    elif iv_v0 == 1:
        v_j = branch0_xor(init_value, iv_v1, di, tt03, 96, 0, 6, 7, 5, 3, 2, 1, 5, 4, 4)
    elif iv_v0 == 0:
        v_j = branch0_xor(init_value, iv_v1, di, tt03, 101, 5, 7, 6, 3, 2, 1, 0, 5, 4, 3)

    ret = bytearray(32)
    for i in range(8):
        ret[i * 4:i * 4 + 4] = ((v_j[i] + init_value[i]) & 0xffffffff).to_bytes(4, 'big')

    ret = bxor(ret[:16], ret[16:])
    sum = sum_md5(ret)
    ret += sum.to_bytes(4, "little")
    return ret

def swap_v0(src, src_xor, tt2, table_f, order1, typ=None):
    da0 = [0] * 8
    ha0 = src_xor[:]
    for i in range(8):
        da0[i] = src[order1[i]] ^ src_xor[i]

    for round in range(10):
        rr_0 = r00(ha0[0], ha0[1], ha0[2], ha0[3], ha0[4], ha0[5], ha0[6], ha0[7], 0, table_f)
        rr_1 = r00(ha0[1], ha0[2], ha0[3], ha0[4], ha0[5], ha0[6], ha0[7], ha0[0], 0, table_f)
        rr_2 = r00(ha0[2], ha0[3], ha0[4], ha0[5], ha0[6], ha0[7], ha0[0], ha0[1], 0, table_f)
        rr_3 = r00(ha0[3], ha0[4], ha0[5], ha0[6], ha0[7], ha0[0], ha0[1], ha0[2], 0, table_f)
        rr_4 = r00(ha0[4], ha0[5], ha0[6], ha0[7], ha0[0], ha0[1], ha0[2], ha0[3], 0, table_f)
        rr_5 = r00(ha0[5], ha0[6], ha0[7], ha0[0], ha0[1], ha0[2], ha0[3], ha0[4], 0, table_f)
        rr_6 = r00(ha0[6], ha0[7], ha0[0], ha0[1], ha0[2], ha0[3], ha0[4], ha0[5], 0, table_f)
        rr_7 = r00(ha0[7], ha0[0], ha0[1], ha0[2], ha0[3], ha0[4], ha0[5], ha0[6], 0, table_f)
        rr_7 = rr_7 ^ tt2[round + 1]

        d0, d1, d2, d3, d4, d5, d6, d7 = da0[0], da0[1], da0[2], da0[3], da0[4], da0[5], da0[6], da0[7]

        da0[0] = r00(d0, d1, d2, d3, d4, d5, d6, d7, rr_0, table_f)
        da0[1] = r00(d1, d2, d3, d4, d5, d6, d7, d0, rr_1, table_f)
        da0[2] = r00(d2, d3, d4, d5, d6, d7, d0, d1, rr_2, table_f)
        da0[3] = r00(d3, d4, d5, d6, d7, d0, d1, d2, rr_3, table_f)
        da0[4] = r00(d4, d5, d6, d7, d0, d1, d2, d3, rr_4, table_f)
        da0[5] = r00(d5, d6, d7, d0, d1, d2, d3, d4, rr_5, table_f)
        da0[6] = r00(d6, d7, d0, d1, d2, d3, d4, d5, rr_6, table_f)
        da0[7] = r00(d7, d0, d1, d2, d3, d4, d5, d6, rr_7, table_f)

        ha0[0], ha0[1], ha0[2], ha0[3], ha0[4], ha0[5], ha0[6], ha0[7] = rr_0, rr_1, rr_2, rr_3, rr_4, rr_5, rr_6, rr_7

    if typ is None:
        src[0] = da0[0] ^ src_xor[0] ^ src[0]
        src[1] = da0[7] ^ src_xor[1] ^ src[1]
        src[2] = da0[6] ^ src_xor[2] ^ src[2]
        src[3] = da0[5] ^ src_xor[3] ^ src[3]
        src[4] = da0[4] ^ src_xor[4] ^ src[4]
        src[5] = da0[3] ^ src_xor[5] ^ src[5]
        src[6] = da0[2] ^ src_xor[6] ^ src[6]
        src[7] = da0[1] ^ src_xor[7] ^ src[7]
    else:
        src[0] = da0[7] ^ typ[1]
        src[1] = da0[6] ^ typ[2]
        src[2] = da0[5] ^ typ[3]
        src[3] = da0[4] ^ typ[4]
        src[4] = da0[3] ^ typ[5]
        src[5] = da0[2] ^ typ[6]
        src[6] = da0[1] ^ typ[7] ^ src[7]
        src[7] = da0[0] ^ typ[0]
    return src

def branch_1(iv_v0, khronos, query_sm3, body_md5_bytes, ts_bytes):
    tt1 = [0x808a9c79, 0xf079807e, 0xbadf79c5, 0xa785d3ff, 0x82d8438c]
    iv_v1 = tt1[iv_v0]

    c_v1 = (iv_v1 + khronos) & 0xff
    c_v1 = ror(khronos, c_v1)

    orders = bytes.fromhex('''05 07 01 02 04 00 06 03 00 05 02 04 01 03 07 06
    05 07 02 04 01 06 03 00 03 00 02 04 06 07 01 05
    04 05 00 03 06 02 01 07 00 00 00 00 00 00 00 00''')

    order1 = orders[iv_v0 * 8:iv_v0 * 8 + 8]

    iv_v1 = (iv_v1 + khronos + 1) & 63
    tt1 = [0x87aeea5dab37cd6b, 0x7ff48becb4f54087, 0xb0724c06706bbd5d, 0x1fe5dfb1143e328d,
           0x1a2331d00af4f1f2, 0xcaff7131bb1e71ba, 0x33385e1042752218, 0xff01ed65d4a441fb,
           0xadb1ec8828c80e8, 0x62475d12f4e06fe7, 0xbd0b238da4fe72]

    tt2 = [0] * 11
    for i in range(0, 11):
        tt2[i] = ror(tt1[i], iv_v1)

    to_sign = bytearray(query_sm3 + body_md5_bytes + ts_bytes + bytes([0x80, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0]))

    src = [0] * (len(to_sign) // 8)
    for i in range(len(to_sign) // 8):
        src[i] = int.from_bytes(to_sign[i * 8:i * 8 + 8], 'big')

    src_xor = [c_v1, c_v1, c_v1, c_v1, c_v1, c_v1, c_v1, c_v1]

    table_f = get_branch_1_table_f(iv_v0)
    swap = swap_v0(src, src_xor, tt2, table_f, order1)
    data = [0] * 8
    data[7] = 416

    src_xor = [swap[4], swap[3], swap[2], swap[1], swap[0], swap[7], swap[6], swap[5]]
    swap = swap_v0(data, src_xor, tt2, table_f, order1, swap)

    ret = bytearray(32)
    n = 0
    for i in range(7, -1, -1):
        pp = swap[i].to_bytes(8, "little")
        ret[n + 0], ret[n + 1], ret[n + 2], ret[n + 3] = pp[1], pp[3], pp[5], pp[6]
        n += 4

    for i in range(16):
        ret[i] ^= ret[i + 16]
    ret = ret[:16]
    sum = sum_md5(ret)
    ret += sum.to_bytes(4, "little")
    return ret

def r00(r0, r1, r2, r3, r4, r5, r6, r7, tt, table_f, v=0):
    x = table_f(8 * (r0 >> 56))
    r1 = (r1 >> 45) & 2040

    if tt > 0:
        x = x ^ tt
    x = x ^ table_f(r1 + 2048)

    r2 = (r2 >> 37) & 2040
    x = x ^ table_f(r2 + 4096)

    r3 = (r3 >> 29) & 2040
    x = x ^ table_f(r3 + 6144)

    r4 = (r4 >> 21) & 2040
    x = x ^ table_f(r4 + 8192)

    r5 = (r5 >> 13) & 2040
    x = x ^ table_f(r5 + 10240)

    r6 = (r6 >> 8) & 255
    x = x ^ table_f(8 * r6 + 12288)

    r7 = r7 & 255
    x = x ^ table_f(8 * r7 + 14336)

    return x

branch_2_orders = bytes.fromhex('''
    0f 07 04 00 09 08 03 0a 06 0b 05 0d 0e 01 0c 02 0f 05 08 0c 00 09 02 01 03 07 0e 06 0b 0a 0d 04 06 05 00 07 0c 00 0a 04 08 0f 01 0b 0d 09 02 0e 06 0b 02 05 04 03 08 01 01 07 0a 00 0d 0c 09 0e
    0d 07 0e 0f 0b 02 08 03 0c 05 09 01 00 04 06 0a 0d 09 02 06 0f 0b 0a 04 08 07 00 0c 05 03 01 0e 0c 09 0f 07 06 0f 03 0e 02 0d 04 05 01 0b 0a 00 0c 05 0a 09 0e 08 02 04 04 07 03 0f 01 06 0b 00
    0b 0f 04 08 02 0a 07 00 09 0d 06 01 0e 03 05 0c 0b 06 0a 05 08 02 0c 03 07 0f 0e 09 0d 00 01 04 09 06 08 0f 05 08 00 04 0a 0b 03 0d 01 02 0c 0e 09 0d 0c 06 04 07 0a 03 03 0f 00 08 01 05 02 0e
    01 00 0d 0f 09 0a 0b 0e 04 02 08 07 03 06 0c 05 01 08 0a 0c 0f 09 05 06 0b 00 03 04 02 0e 07 0d 04 08 0f 00 0c 0f 0e 0d 0a 01 06 02 07 09 05 03 04 02 05 08 0d 0b 0a 06 06 00 0e 0f 07 0c 09 03
    0a 08 04 0f 00 0b 01 06 0d 0c 07 09 03 0e 05 02 0a 07 0b 05 0f 00 02 0e 01 08 03 0d 0c 06 09 04 0d 07 0f 08 05 0f 06 04 0b 0a 0e 0c 09 00 02 03 0d 0c 02 07 04 01 0b 0e 0e 08 06 0f 09 05 00 03
    ''')

def branch_2(iv, khronos, query_sm3, body_md5_bytes, ts_bytes):
    n0 = (iv & 15 - 2) * 86
    iv_v0 = (n0 >> 15 & 0xff) + (n0 >> 8 & 0xff)

    t001 = [0x8980f29b, 0xeb549c7f, 0xb08726db, 0xd40cb5e6, 0xe8f559e4]

    n1 = t001[iv_v0]
    count_v1 = (n1 + khronos + 1) & 0xff
    count_v2 = (n1 + khronos) & 0xffffffff

    n0 = (count_v2 + 5) & 7

    pad = bytearray(8)
    seed = bytes([0x84, 0x96, 0x77, 0x9d, 0xd4, 0x15, 0x0b, 0xf8])
    for i in range(8):
        v = int.from_bytes(bytes([seed[i], seed[i]]), 'little')
        v = v >> n0
        pad[i] = v & 0xff

    data = query_sm3 + body_md5_bytes + ts_bytes + pad + bytes.fromhex('a0 01 00 00')

    idx = iv_v0 << 6
    ret = md5sum_v3(data, count_v2, branch_2_orders[idx:idx + 64], count_v1)

    return ret

def branch0_xor(data, base, di, tt03, round, x1, x2, x3, x4, x5, x6, x7, x8, x9, x10):
    d = data[:]

    for i in range(round):
        offset = base + i
        n0 = di[offset & 127]

        n1 = ((d[x3] ^ d[x4]) & d[x1]) ^ d[x3]

        n2 = rol(d[x1], 26) ^ rol(d[x1], 21) ^ rol(d[x1], 7)
        offset = offset & 63
        n3 = tt03[offset]

        n4 = (n0 + n1 + n2 + n3 + d[x5]) & 0xffffffff

        n5 = rol(d[x2], 30) ^ rol(d[x2], 19) ^ rol(d[x2], 10)
        n6 = (d[x2] & d[x6]) | ((d[x2] | d[x6]) & d[x7])
        n7 = n5 + n6

        o = d[x9]
        d[0], d[1], d[2], d[3], d[4], d[5], d[6], d[7] = d[7], d[0], d[1], d[2], d[3], d[4], d[5], d[6]
        d[x10] = (n7 + n4) & 0xffffffff
        d[x8] = (o + n4) & 0xffffffff
    return d

_BR1_RAW = None

def get_branch_1_table_f(iv_v0):
    global _BR1_RAW
    if _BR1_RAW is None:
        _BR1_RAW = bytes([item for sublist in _get_branch_one_table() for item in sublist])

    table = _BR1_RAW[iv_v0 << 14:]
    def table_f(x):
        return int.from_bytes(table[x:x + 8], 'little')
    return table_f

import hashlib

def rc4_xg(data, key):
    S = list(range(256))
    j = 0
    for i in range(256):
        j = (j + S[i] + key[i % len(key)]) % 256
        S[i] = S[j]
    i = j = 0

    result = bytearray(len(data))
    for k, v in enumerate(data):
        i += 1
        x = S[i]
        j += x
        y = S[j & 0xff]
        S[i] = y
        result[k] = v ^ S[(y + y) & 0xff]

    return result

def reverse_bits(num):
    bin_num = bin(num)[2:].zfill(8)
    rev_bin_num = bin_num[::-1]
    rev_num = int(rev_bin_num, 2)

    return rev_num

gorgon_84 = bytes([0x4a, 0x16, 0x47, 0x6c, 0x84, 0x04])

def encrypt_gorgon(body, query, khronos, xg_rand, dataType):
    xg_seed = 320
    body_md5 = xssstub_hash_md5_hex(data=body, dataType=dataType).lower() if body else ""

    data = hashlib.md5(query.encode()).digest()[:4]

    if len(body_md5) > 0:
        body_md5 = bytes.fromhex(body_md5)
        data += body_md5[:4]
    else:
        data += bytearray([0, 0, 0, 0])
    data += bytearray([0, 0, 0, 0])
    mssdkVersionInt = 67503104
    data += mssdkVersionInt.to_bytes(4, "little")
    data += khronos.to_bytes(4, "big")
    key = bytes([
        gorgon_84[0],
        320 & 0xff,
        gorgon_84[1],
        (xg_rand >> 8) & 0xff,
        gorgon_84[2],
        gorgon_84[3],
        (320 >> 8) & 0xff,
        xg_rand & 0xff,
    ])

    out = rc4_xg(data, key)
    for i in range(len(out)):
        a = out[i]
        out[i] = (a >> 4 | (a << 4)) & 0xFF
        a = out[0]
        if i + 1 < len(out):
            a = out[i + 1]
        a ^= out[i]
        a = reverse_bits(a)
        out[i] = (~(a ^ 20)) & 0xFF

    ret = gorgon_84[-2:]
    ret += xg_rand.to_bytes(2, "little")
    ret += xg_seed.to_bytes(2, "little")
    ret += out

    return ret.hex()

import base64
import hashlib
import random

def ror(value, count):
    count %= 64
    low = value << (64 - count)
    value >>= count
    value |= low
    value &= 0xFFFFFFFFFFFFFFFF
    return value

HexTable = b"0123456789abcdef"

def _helios_bytes2matrix(text):
    return [int.from_bytes(list(text[i:i + 8]), 'little') for i in range(0, len(text), 8)]

def encrypt_helios_input(hash_table, in_data):
    data0 = int.from_bytes(in_data[:8], 'little')
    data1 = int.from_bytes(in_data[8:], 'little')
    for i in range(0, 0x22):
        hash1 = hash_table[i]
        data1 = hash1 ^ (data0 + ror(data1, 8))
        data1 &= 0xFFFFFFFFFFFFFFFF
        data0 = data1 ^ ror(data0, 61)
        data0 &= 0xFFFFFFFFFFFFFFFF

    return data0.to_bytes(8, 'little') + data1.to_bytes(8, 'little')

def encrypt_helios(khronos, rand=0):
    rand = rand or int(random.randint(0, 0xFFFFFFFF))
    data = rand.to_bytes(4, "little")
    data += str(8662).encode()
    key_sum = hashlib.md5(data).digest()

    keys = bytearray(32)
    for i in range(16):
        v1 = key_sum[i]
        keys[2 * i] = HexTable[v1 >> 4]
        keys[2 * i + 1] = HexTable[v1 & 15]

    hash_table = []
    hash_table.append(int.from_bytes(keys[:8], "little"))

    keys = _helios_bytes2matrix(keys)
    buffer_b0 = keys[0]
    buffer_b8 = keys[1]
    keys.pop(0)
    keys.pop(0)

    for i in range(0, 0x22):
        x9 = buffer_b0
        x8 = buffer_b8

        x8 = ror(x8, 8)
        x8 = x8 + x9
        x8 = (x8 ^ i) & 0xFFFFFFFFFFFFFFFF
        keys.append(x8)

        x8 = x8 ^ ror(x9, 61)
        x8 &= 0xFFFFFFFFFFFFFFFF

        hash_table.append(x8)
        buffer_b0 = x8
        buffer_b8 = keys[0]
        keys.pop(0)

    in_data = _pkcs7_pad(bytes(f"{khronos}-1588093228-8662", 'utf-8'), 16)
    out = bytearray()
    for i in range(len(in_data) // 16):
        out += encrypt_helios_input(hash_table, in_data[i * 16:i * 16 + 16])

    out = data[:4] + out
    return base64.b64encode(out).decode()

import base64
import random
import time

def xmxor(data, key):
    data = xmxor_two(data, key)

    last_flag = data[-1] ^ data[-2]
    data0 = data[0]
    data[0] = (~last_flag + data[0]) & 0xff
    data[1] = ((data[0] ^ data[-1] ^ 254) + data[1]) & 0xff
    data[2] = (data[2] + ((last_flag - data0) ^ rl8(data[1], 3) ^ 2)) & 0xff

    for i in range(len(data) - 4):
        temp = (rl8(data[i + 2], 3)) ^ data[i + 1] ^ (i + 3)
        data[i + 3] = (~temp + data[i + 3]) & 0xff

    data[-1] ^= data[-2]

    sum = 0
    for i in range(len(data) - 1):
        sum += int(data[i + 1])

    data[0] = ((data[0] ^ data[1]) + sum) & 0xff
    return data

def xmxor_two(data, key):
    enc_data = bytearray(len(data))
    for i in range(len(data)):
        index = (i * 4) & 28
        d0 = key[index]
        d1 = key[index + 1]

        d2 = (rl8(data[i], 4) + d0) ^ d1
        d2 = ~d2
        d2 = (rl8(d2 & 0xff, 3)) & 0xff
        d2 += d1
        d2 = (d2 & 0xff) ^ d0
        enc_data[-i - 1] = ~d2 & 0xff
    return enc_data

def gen_medusa(url, url_params, devices, data:dict, khronos:int=None, lanusk=None, hash_rand=0, xm_rand=0, dataType=None):
    config = {
        'fix': bytes([0x35]),
        'aes_key': b'\xf1Y3vvn\xa9\x8d4\xf3\x1b\x05z\x9d[\xe4',
        'aes_iv': b'\x1f\xe1\t\xa4\x12R\x83\xf4\x18\xde\x9e\x05\x1a\x96\x9e\x12',
        'signKey': b'\x8e\xbd\xfa8\x06\xec\xc5\xce\xe7\x94#\xe6\x02\x9e\xd8%@\xbc"\x18\xbb~\xae\xf7\x1c\xb6\x91\xf7\xaa\x8a\xa2\xf5',
    }
    data, query_sm3, body_sm3 = gen_medusa_proto(url, url_params, devices, data, khronos, lanusk, dataType)

    hash_rand = hash_rand or int(random.randint(0, 0xFFFFFFFF))
    xm_rand = xm_rand or int(random.randint(0, 0xFFFFFFFF))
    key, seed = get_key_hash(config['signKey'], hash_rand)
    data = xmxor(data, key)
    xg_seed = 320
    data = xg_seed.to_bytes(8, "little") + data
    data = bytearray(data[::-1])
    for i in range(0, len(data)):
        data[i] = data[i] ^ seed[~i & 3]

    hash_rand_bytes = hash_rand.to_bytes(4, "little")
    check_bit = (query_sm3[0] & 63) << 14
    check_bit |= 0x18000001
    check_bit |= (body_sm3[0] & 63) << 8
    data = config['fix'] + xm_rand.to_bytes(4, 'little') + check_bit.to_bytes(4, 'little') + data + hash_rand_bytes[2:]
    data = AES_V3(config['aes_key'], khronos).encrypt(data, config['aes_iv'])
    version_or = bytes()
    version = bytes.fromhex("03 00 00 00 f7 e8 5f fa d7 d7 dc 3b d6 2a c8 70 57 cf 61 18 ")
    for i in range(0, 20, 4):
        d = int.from_bytes(version[i:i + 4], "little")
        d ^= khronos
        version_or += d.to_bytes(4, "little")

    data = version_or + hash_rand_bytes[:2] + int(256).to_bytes(2, "little") + data

    return base64.b64encode(data).decode()

def gen_medusa_proto(url, url_params, devices, data:dict, khronos:int=None, lanusk=None, dataType=None):
    xg_seed = 320
    body_md5 = xssstub_hash_md5_hex(data=data, dataType=dataType).lower() if data else ""
    if body_md5 != '':
        body_md5_bytes = bytes.fromhex(body_md5)
    else:
        body_md5_bytes = bytes(16)
    ts_bytes = khronos.to_bytes(4, "little")
    query_sm3 = SM3(url.split('?')[1]).digest()
    if len(lanusk) > 0:
        lanusk_sm3 = SM3(bytes.fromhex(lanusk) + ts_bytes).digest()
        lanusk_hash = md5sum(
            lanusk_sm3 + body_md5_bytes + bytes.fromhex("84 96 77 9d db 6d bc b6 d4 15 0b f8 80 01 00 00"))

        lanusk_hash = lanusk_hash
        psk_version = "1"
        query_body_hash_sm3 = SM3(url.split('?')[1].encode() + body_md5_bytes + b"1").digest()
    else:
        lanusk_hash = b''
        psk_version = 'none'
        query_body_hash_sm3 = SM3(url.split('?')[1].encode() + body_md5_bytes + b"none").digest()

    proto_data = Medusa(
        magic=bytearray(b'\xf7\xe8_\xfa\xd7\xd7\xdc;\xd6*\xc8pW\xcfa\x18'),
        version=3,
        rand=int(random.randint(0, 0xFFFFFFFF)),
        ms_app_id='8662',
        device_id=str(devices.get("device_id", url_params.get("device_id", url_params.get("did", "")))),
        license_id='1588093228',
        app_version=devices.get("version_name", url_params.get("version_name")),
        sdk_version_str='v04.06.04-ml-android',
        sdk_version=67503104,
        xg_seed_bytes=xg_seed.to_bytes(8, "little"),
        time=khronos,
        query_body_ts_hash=hash_f13(query_sm3, body_md5_bytes, ts_bytes, khronos),
        query_sm3=bytearray(query_sm3[:6]),
        request=MedushaAlgorithmCount(sign_count=111, report_count=10, setting_count=694367, unknown4=0, unknown5=586952199),
        sec_device_token='AXYQOS6n2m60x1fVZHIrH3iol',
        time2=khronos,
        lanusk_hash=lanusk_hash,
        query_body_hash_sm3=query_body_hash_sm3,
        psk_version=psk_version,
        call_type=312,
        env=Env(
            launch_time=random.randint(100, 120),
            unknown2=146331399,
            unknown3=146331396,
            unknown5=7,
            version='v04.06.04.03-bugfix',
            pid=random.randint(10001, 12000),
            device=Device(
                d1=1,
                collect_stat=2,
                aid='8662',
                device_id=str(devices.get("device_id", url_params.get("device_id", url_params.get("did", "")))),
                sec_device_token='Ai6svO3PyrwDOUSmO6ZcResxu',
                app_version='!noperm!',
                battery=-888888,
                battery2=-888888,
                battery_health=3,
                battery_changed=-888888,
                network='!notset!',
                tz='Asia/Shanghai,8',
                lan='zh_CN',
                cpu=4,
                sdcard=255.24993896484375,
                sdcard_used=35.58599090576172,
                memory=3.467449188232422,
                memory2=3.467449188232422,
                data=255.1754913330078,
                data_used=42.17544174194336,
                os_version=devices.get("os_version", url_params.get("os_version")),
                brightness=41,
                volume=36,
                ts=1728388016635,
                ts2=1728388016635,
                ts3=1728388016635,
                ts4=1728388016637,
                usb=-1,
                hw_version=devices.get("device_model", url_params.get("device_type")),
                brand=devices.get("device_brand", url_params.get("device_brand")),
                board=devices.get("device_model", url_params.get("device_type")),
                product_name=devices.get("device_model", url_params.get("device_type")),
                product_device=devices.get("device_manufacturer", devices.get("device_brand", url_params.get("device_brand"))),
                product_manufacturer=devices.get("device_brand", url_params.get("device_brand")),
                hardware=devices.get("device_brand", url_params.get("device_brand")),
                unknown38=31
            ),
            report=Report(
                time=devices.get("report_time", int(time.time())),
                state=-2,
                code=200,
                times=0,
                unknown6=0
            ),
            app_version=devices.get("version_name", url_params.get("version_name"))
        ),
        unknown24='{"cmr":16777216,"cmr2":16777216,"un_h":1879194040,"vpn":0,"kd":0,"fkd":3672518972,"pd":-1872573247,"dyn":"","do":0,"tk":true}'
    )

    return bytes(proto_data), query_sm3, proto_data.query_body_ts_hash

import base64
import json
import random
import time

def lowerHeader(header: dict):
    new_header = {}
    for key, value in header.items():
        new_header[str(key).lower()] = value
    header.clear()
    header.update(new_header)
    return new_header

def core_sixgod(surl, params, devices, data:dict={},  common=None, header=None, lanusk="", log=False, rticket_override=None, ts_override=None, khronos_override=None):
    if devices:
        for k, v in devices.items():
            if k in data:
                data[k] = v
    dataType = header.get("content-type", header.get("Content-Type"))
    xg_rand = int(random.randint(0, 0xFFFF))
    url, url_params, x_common = get_params_encrypturl(surl, params=params, devices=devices, common=common, rticket_override=rticket_override, ts_override=ts_override)

    khronos = khronos_override if khronos_override is not None else int(time.time())
    params = EecryptParams()
    params.khronos = str(khronos)
    params.ladon = base64.b64encode(khronos.to_bytes(4, 'big')).decode()
    params.argus = base64.b64encode(khronos.to_bytes(4, 'little')).decode()
    params.gorgon = encrypt_gorgon(data, url_encode(url_params), khronos, xg_rand, dataType)
    params.helios = encrypt_helios(khronos, rand=0)
    params.medusa = gen_medusa(
        url,
        url_params,
        devices,
        data,
        khronos,
        lanusk,
        dataType=dataType
    )
    six = result(params, data, url,  common=x_common, dataType=dataType, log=log)

    headers = header.copy()
    lowerHeader(header=headers)
    headers.update(six.get("sign_header"))
    if devices:
        headers["x-tt-dt"] = devices.get("x_tt_dt", "")
        headers["user-agent"] = devices.get("ua", "")
    return headers, six.get("sign_url")

def result(params: EecryptParams, data, eurl,  common=None, sell=False, dataType=None, log=False):

    six = {
        'x-ladon': params.ladon,
        'x-khronos': params.khronos,
        'x-argus': params.argus,
        'x-gorgon': params.gorgon,
        'x-helios': params.helios,
        'x-medusa': params.medusa
    }
    if data:
        six["x-ss-stub"] = xssstub_hash_md5_hex(data=data, dataType=dataType)
    if sell:
        datas: dict = dict()
        datas["api"] = eurl
        datas["sign"] = six
        if common:
            datas["sign"]["x-common-params-v2"] = common
        return datas
    datas: dict = dict()
    datas["sign_url"] = eurl
    datas["sign_header"] = six
    if common:
        datas["sign_header"]["x-common-params-v2"] = common
    if log:
        print(json.dumps(datas, indent=4, ensure_ascii=False))
    return datas

USER_AGENT = (
    "com.phoenix.read/71332 (Linux; U; Android 16; zh_CN; 25053RT47C; "
    "Build/BP2A.250605.031.A3; Cronet/TTNetVersion:04657795 2026-01-23 "
    "QuicVersion:c67e9834 2025-09-08)"
)

VIDEO_MODEL_URL_TEMPLATE = (
    "https://api5-normal-sinfonlineb.fqnovel.com/novel/player/multi_video_model/v1/"
    "?iid={install_id}&device_id={device_id}&ac=wifi&channel=update_64&aid=8662"
    "&app_name=novelread&version_code=71332&version_name=7.1.3.32"
    "&device_platform=android&os=android&ssmix=a&device_type=25053RT47C"
    "&device_brand=Redmi&language=zh&os_api=36&os_version=16"
    "&manifest_version_code=71332&resolution=1280*2772&dpi=520"
    "&update_version_code=71332&host_abi=arm64-v8a&dragon_device_type=phone"
    "&pv_player=71332&compliance_status=0&need_personal_recommend=1"
    "&player_so_load=1&is_android_pad_screen=0"
)

def load_local_config() -> Dict[str, Any]:
    return {"device_id": CONFIG_DEVICE_ID, "install_id": CONFIG_INSTALL_ID, "platform": CONFIG_PLATFORM, "cache_seconds": CONFIG_CACHE_SECONDS}

def get_device_keys() -> Dict[str, str]:
    config = load_local_config()

    device_id = str(config.get("device_id") or "").strip()
    install_id = str(config.get("install_id") or "").strip()
    platform = str(config.get("platform") or "android").strip() or "android"

    if not device_id or not install_id:
        raise RuntimeError(
            "Missing device configuration. Set DUANJU_DEVICE_ID / "
            "DUANJU_INSTALL_ID, or open the web UI and save local config."
        )

    return {
        "device_id": device_id,
        "install_id": install_id,
        "platform": platform,
    }

def build_liushen_device(device_keys: Dict[str, str]) -> Dict[str, str]:
    return {
        "device_id": device_keys.get("device_id", ""),
        "iid": device_keys.get("install_id", ""),
        "install_id": device_keys.get("install_id", ""),
        "device_brand": "Redmi",
        "device_model": "25053RT47C",
        "device_type": "25053RT47C",
        "device_manufacturer": "Xiaomi",
        "os_version": "16",
        "version_name": "7.1.3.32",
        "ua": USER_AGENT,
    }

def _compute_branch(query_string, body_bytes, khronos):
    query_sm3 = SM3(query_string).digest()
    body_md5 = hashlib.md5(body_bytes).digest() if body_bytes else bytes(16)
    ts_bytes = khronos.to_bytes(4, "little")
    iv = get_iv(0x20230928, query_sm3)
    iv = get_iv(iv, body_md5)
    iv = get_iv(iv, ts_bytes)
    low = iv & 15
    iv_v0 = (low * 171) >> 9
    return low - (iv_v0 * 3)

def sign_json_request_with_liushen(
    url: str,
    body_obj: Dict[str, Any],
    device_keys: Dict[str, str],
) -> Tuple[str, Dict[str, str], bytes]:
    body_text = json.dumps(body_obj, ensure_ascii=False, separators=(",", ":"))
    body_data = json.loads(body_text)
    body_bytes = body_text.encode("utf-8")

    ts = str(int(time.time() * 1000))
    base_headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/json; charset=utf-8,application/x-protobuf",
        "Content-Type": "application/json; charset=UTF-8",
        "x-xs-from-web": "0",
        "x-ss-req-ticket": ts,
        "x-tt-request-tag": "t=0;n=0",
        "sdk-version": "2",
        "passport-sdk-version": "50561",
        "x-vc-bdturing-sdk-version": "3.7.2.cn",
    }

    url_parts = urlsplit(url)
    base_url = f"{url_parts.scheme}://{url_parts.netloc}{url_parts.path}"
    params = dict(parse_qsl(url_parts.query, keep_blank_values=True))
    devices = build_liushen_device(device_keys)

    khronos = int(time.time())
    base_rticket = int(time.time() * 1000)
    safe_rticket = base_rticket
    safe_khronos = khronos

    for offset in range(32):
        rticket = base_rticket + offset
        eurl, _, _ = get_params_encrypturl(
            base_url, params=params, devices=devices,
            rticket_override=rticket, ts_override=khronos,
        )
        query_string = eurl.split("?", 1)[1] if "?" in eurl else ""
        branch = _compute_branch(query_string, body_bytes, khronos)
        if branch != 1:
            safe_rticket = rticket
            safe_khronos = khronos
            break

    sign_headers, sign_url = core_sixgod(
        surl=base_url,
        params=params,
        data=body_data,
        devices=devices,
        header=base_headers,
        log=False,
        rticket_override=safe_rticket,
        ts_override=safe_khronos,
        khronos_override=safe_khronos,
    )
    return sign_url, sign_headers, body_bytes

def _quality_number(value: str) -> int:
    text = str(value or "")
    m = re.match(r".*?(2160|1440|1080|720|576|540|480|360)", text)
    if m:
        return int(m.group(1))
    mapping = {"1920": 1080, "1280": 720, "1024": 576, "854": 480, "640": 360}
    if text in mapping:
        return mapping[text]
    if re.match(r"^\d{3,4}$", text):
        return int(text)
    return 0

def _quality_label(value: str) -> str:
    n = _quality_number(value)
    if n:
        return "%dP" % n
    labels = {
        "low": "\u6d41\u7545",
        "smooth": "\u6d41\u7545",
        "medium": "\u6807\u6e05",
        "normal": "\u9ad8\u6e05",
        "high": "\u8d85\u6e05",
        "original": "\u539f\u753b",
        "uhd": "\u539f\u753b",
        "super_high": "\u539f\u753b",
    }
    return labels.get(str(value or "").lower(), str(value or "\u81ea\u52a8"))

def _quality_rows(video_list: Dict[str, Any]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    if not video_list or not isinstance(video_list, dict):
        return rows
    for key, value in video_list.items():
        item = value if isinstance(value, dict) else {}
        quality = str(
            item.get("quality_desc")
            or item.get("height")
            or item.get("vheight")
            or item.get("quality")
            or key
        )
        if item.get("main_url") and not any(
            r["key"] == key for r in rows
        ):
            rows.append({"key": key, "quality": quality, "item": item})
    rows.sort(
        key=lambda r: _quality_number(r["quality"] or r["key"]),
        reverse=True,
    )
    return rows

def fetch_quality_rows(vid: str) -> List[Dict[str, Any]]:
    cache_key = str(vid)
    cached = _quality_cache.get(cache_key)
    if cached and time.time() - cached[0] < 300:
        return cached[1]

    rows: List[Dict[str, Any]] = []
    try:
        device_keys = get_device_keys()
        target_url = build_video_model_url(
            device_keys["device_id"], device_keys["install_id"]
        )
        post_payload = {
            "biz_param": {
                "detail_page_version": 0,
                "device_level": 3,
                "disable_digg_stat": False,
                "need_all_video_definition": True,
                "need_mp4_align": False,
                "use_os_player": False,
                "use_server_dns": False,
                "video_platform": 1024,
            },
            "mixed_video_id_map": {"1004": [str(vid)]},
        }
        signed_url, headers, post_body = sign_json_request_with_liushen(
            target_url, post_payload, device_keys
        )
        resp = curl_request(signed_url, headers, post_body, 20)
        data = json.loads(resp)
        fallback_api, video_model = extract_fallback_api(data, str(vid))

        resp2 = curl_request(fallback_api, {"User-Agent": USER_AGENT}, None, 20)
        outer = json.loads(resp2)
        video_data = outer.get("video_info", {}).get("data", {})
        if not isinstance(video_data, dict):
            video_data = {}
        video_list = video_data.get("video_list", {})
        if isinstance(video_list, dict):
            rows = _quality_rows(video_list)
    except Exception:
        pass

    _quality_cache[cache_key] = (time.time(), rows)
    return rows

_quality_cache: Dict[str, Tuple[float, List]] = {}

def replace_failed_device(device_id: str, platform: str) -> None:
    pass

def get_current_domain(request=None) -> str:
    return _CURRENT_DOMAIN

_RUNTIME_BASE_DIR = Path(os.path.dirname(os.path.abspath(__file__))) if "__file__" in dir() else Path.cwd()

def get_runtime_base_dir() -> Path:
    return _RUNTIME_BASE_DIR

DEFAULT_TIMEOUT = 30
VIDEO_WORKER_POOL = ThreadPoolExecutor(max_workers=4)
FFMPEG_BIN = "ffmpeg"
VIDEO_TTL_SECONDS = 300

def schedule_video_cleanup(filepath: Path, delay_seconds: int = VIDEO_TTL_SECONDS) -> None:

    def _delete_file() -> None:
        try:
            filepath.unlink(missing_ok=True)
            print(f"[cleanup] deleted_expired_video={filepath.name}")
        except Exception as exc:
            print(f"[cleanup] delete_failed file={filepath.name} error={exc}")

    timer = threading.Timer(delay_seconds, _delete_file)
    timer.daemon = True
    timer.start()

def curl_request(
    url: str,
    headers: Dict[str, str],
    post_body: Optional[bytes] = None,
    timeout: int = DEFAULT_TIMEOUT,
) -> bytes:
    session = requests.Session()
    session.trust_env = False
    if post_body is not None:
        resp = session.post(url, headers=headers, data=post_body, timeout=timeout)
    else:
        resp = session.get(url, headers=headers, timeout=timeout)
    resp.raise_for_status()
    return resp.content

def handle_video_request(
    video_id: str,
    request=None,
    max_retries: int = 3,
    stream_mode: bool = False,
    quality_key: Optional[str] = None,
) -> Dict[str, Any]:
    return resolve_video_url(
        video_id, request, max_retries,
        stream_mode=stream_mode, quality_key=quality_key,
    )

def resolve_video_url(
    video_id: str,
    request=None,
    max_retries: int = 3,
    stream_mode: bool = False,
    quality_key: Optional[str] = None,
) -> Dict[str, Any]:
    last_err: Optional[Exception] = None

    for attempt in range(max_retries):
        device_keys = get_device_keys()
        target_url = build_video_model_url(
            device_keys["device_id"], device_keys["install_id"]
        )

        post_payload = {
            "biz_param": {
                "detail_page_version": 0,
                "device_level": 3,
                "disable_digg_stat": False,
                "need_all_video_definition": True,
                "need_mp4_align": False,
                "use_os_player": False,
                "use_server_dns": False,
                "video_platform": 1024,
            },
            "mixed_video_id_map": {
                "1004": [video_id],
            },
        }
        signed_url, headers, post_body = sign_json_request_with_liushen(
            target_url, post_payload, device_keys
        )

        try:
            resp = curl_request(signed_url, headers, post_body, 30)
        except Exception as exc:
            last_err = Exception(f"video_model request failed: {exc}")
            time.sleep(0.1)
            continue

        try:
            data = json.loads(resp)
        except Exception as exc:
            last_err = Exception(f"video_model JSON parse failed: {exc}")
            continue

        if not isinstance(data, dict) or "data" not in data:
            print("video_model raw response:")
            print(json.dumps(data, ensure_ascii=False, indent=2))

        try:
            fallback_api, video_model = extract_fallback_api(data, video_id)
        except Exception as exc:
            last_err = exc
            continue

        try:
            result = download_and_decrypt_video(
                request,
                fallback_api,
                video_model,
                device_keys,
                video_id,
                max_retries=3,
                stream_mode=stream_mode,
                quality_key=quality_key,
            )
            return result
        except Exception as exc:
            last_err = exc
            time.sleep(0.1)
            continue

    raise Exception(f"Video request failed, retried {max_retries} times: {last_err}")

def build_video_model_url(device_id: str, install_id: str) -> str:
    from urllib.parse import quote

    return VIDEO_MODEL_URL_TEMPLATE.format(
        install_id=quote(install_id, safe=""),
        device_id=quote(device_id, safe=""),
    )

def extract_fallback_api(
    data: Dict[str, Any], video_id: str
) -> Tuple[str, Dict[str, Any]]:
    data_map = data.get("data")
    if not isinstance(data_map, dict):
        raise ValueError("Response missing data field")

    video_entry: Optional[Dict[str, Any]] = None

    if video_id in data_map and isinstance(data_map[video_id], dict):
        video_entry = data_map[video_id]

    if video_entry is None:
        for v in data_map.values():
            if isinstance(v, dict):
                video_entry = v
                break
            if isinstance(v, list) and len(v) > 0 and isinstance(v[0], dict):
                video_entry = v[0]
                break

    if video_entry is None:
        keys = list(data_map.keys())
        raise ValueError(
            f"video entry not found (looked for {video_id}, available keys: {keys})"
        )

    video_model: Optional[Dict[str, Any]] = None
    vm = video_entry.get("video_model")
    if isinstance(vm, str):
        video_model = json.loads(vm)
    elif isinstance(vm, dict):
        video_model = vm
    else:
        raise ValueError("video_model is empty or unknown format")

    fallback_raw = video_model.get("fallback_api")
    fallback_str = parse_fallback_api(fallback_raw)
    if not fallback_str:
        raise ValueError(f"fallback_api cannot be parsed: {type(fallback_raw)} => {fallback_raw}")

    return fallback_str, video_model

def parse_fallback_api(raw: Any) -> str:
    if isinstance(raw, str):
        if raw.startswith("{"):
            try:
                decoded = json.loads(raw)
                if isinstance(decoded, dict) and "fallback_api" in decoded:
                    return str(decoded["fallback_api"])
            except (json.JSONDecodeError, TypeError):
                pass
        if len(raw) > 10:
            return raw
    elif isinstance(raw, list) and len(raw) > 0:
        if isinstance(raw[0], str):
            return raw[0]
    elif isinstance(raw, dict):
        if "fallback_api" in raw:
            return str(raw["fallback_api"])
    return ""

def download_and_decrypt_video(
    request,
    fallback_api: str,
    video_model: Dict[str, Any],
    device_keys: Dict[str, str],
    video_id: str,
    max_retries: int = 3,
    stream_mode: bool = False,
    quality_key: Optional[str] = None,
) -> Dict[str, Any]:
    current_url = fallback_api
    current_device_keys = device_keys
    last_err: Optional[Exception] = None

    for attempt in range(max_retries):
        headers = {"User-Agent": USER_AGENT}

        try:
            resp = curl_request(current_url, headers, None, 30)
        except Exception as exc:
            last_err = Exception(f"fallback_api request failed: {exc}")
        else:
            try:
                data = json.loads(resp)
            except Exception as exc:
                last_err = Exception(f"fallback_api JSON parse failed: {exc}")
            else:
                video_info = data.get("video_info", {})
                if not isinstance(video_info, dict):
                    video_info = {}
                video_data = video_info.get("data", {})
                if not isinstance(video_data, dict):
                    video_data = {}

                if not video_data:
                    last_err = Exception("fallback_api response structure abnormal")
                else:
                    key_seed_b64 = video_data.get("key_seed", "")
                    key_seed_raw = b64_decode_padded(key_seed_b64)

                    video_list = video_data.get("video_list", {})
                    if isinstance(video_list, dict):
                        best_key, best_item = select_best_quality(video_list, preferred_key=quality_key)
                        if best_key and best_item:
                            spade_a = best_item.get("spade_a", "")
                            content_key = None
                            if spade_a:
                                try:
                                    content_key = derive_content_key(spade_a)
                                except Exception:
                                    pass

                            raw_main_url = best_item.get("main_url", "")
                            if raw_main_url:
                                real_main_url = raw_main_url
                                if key_seed_raw and len(raw_main_url) > 10:
                                    try:
                                        dec = decrypt_spade_url(raw_main_url, key_seed_raw)
                                        if dec:
                                            real_main_url = dec
                                    except Exception as exc:
                                        print(f"[debug] spade_url_decrypt_failed={exc}")

                                backup_urls = []
                                for bk_key in ("backup_url_1", "backup_url_2", "url", "play_addr"):
                                    bk_raw = best_item.get(bk_key, "")
                                    if not bk_raw or not isinstance(bk_raw, str):
                                        continue
                                    bk_real = bk_raw
                                    if key_seed_raw and len(bk_raw) > 10:
                                        try:
                                            bk_dec = decrypt_spade_url(bk_raw, key_seed_raw)
                                            if bk_dec:
                                                bk_real = bk_dec
                                        except Exception:
                                            pass
                                    if bk_real and bk_real != real_main_url:
                                        backup_urls.append(bk_real)

                                if stream_mode:
                                    stream_url = build_stream_url(
                                        request, real_main_url, content_key, backup_urls)
                                    if stream_url:
                                        best_item["main_url"] = stream_url
                                else:
                                    local_url = download_decrypt_and_serve(
                                        request, real_main_url, content_key, backup_urls)
                                    if local_url:
                                        best_item["main_url"] = local_url

                            video_data["video_list"] = {best_key: best_item}

                    video_info["data"] = video_data
                    data["video_info"] = video_info
                    return build_response_payload(video_id, video_model, video_data, best_item)

        if attempt < max_retries - 1 and video_id:
            if current_device_keys:
                replace_failed_device(
                    current_device_keys.get("device_id", ""),
                    current_device_keys.get("platform", ""),
                )
            try:
                new_url, new_keys = refresh_fallback_url(video_id)
                if new_url:
                    current_url = new_url
                    current_device_keys = new_keys
            except Exception:
                pass

    raise Exception(
        f"fallback_api request failed, retried {max_retries} times: {last_err}"
    )

def download_decrypt_and_serve(
    request,
    video_url: str,
    content_key: Optional[bytes],
    backup_urls=None,
) -> Optional[str]:
    pipeline_start = time.perf_counter()
    encrypted_data = download_video_bytes(video_url, backup_urls)
    decrypted_data = decrypt_video_bytes(encrypted_data, content_key)
    local_url = save_video_bytes(request, decrypted_data)
    pipeline_seconds = time.perf_counter() - pipeline_start
    print(f"[timing] video_pipeline_seconds={pipeline_seconds:.3f}")
    return local_url

def download_video_bytes(video_url: str, backup_urls=None) -> bytes:
    download_start = time.perf_counter()
    session = requests.Session()
    session.trust_env = False
    dl_headers = {
        "User-Agent": "com.phoenix.read/71332",
        "Referer": "https://novel.snssdk.com/",
    }
    candidate_urls = [video_url] + (backup_urls or [])
    last_exc = None
    for url in candidate_urls:
        try:
            resp = session.get(url, headers=dl_headers, timeout=(8, 120))
            resp.raise_for_status()
            download_seconds = time.perf_counter() - download_start
            print(f"[timing] download_seconds={download_seconds:.3f}")
            return resp.content
        except Exception as exc:
            print(f"[download] cdn_failed url={url[:80]} error={exc}")
            last_exc = exc
            continue
    raise Exception(f"Failed to download video: all CDN nodes failed: {last_exc}")

def decrypt_video_bytes(encrypted_data: bytes, content_key: Optional[bytes]) -> bytes:
    if content_key is None:
        print("[timing] decrypt_seconds=0.000 content_key_missing=true")
        return encrypted_data

    decrypt_start = time.perf_counter()
    try:
        decrypted_data = decrypt_mp4_cenc(encrypted_data, content_key)
        decrypt_seconds = time.perf_counter() - decrypt_start
        print(f"[timing] decrypt_seconds={decrypt_seconds:.3f}")
        return decrypted_data
    except Exception as exc:
        raise Exception(f"Failed to decrypt video: {exc}")

def save_video_bytes(request, decrypted_data: bytes) -> str:
    save_start = time.perf_counter()
    src_dir = get_runtime_base_dir() / "src"
    src_dir.mkdir(parents=True, exist_ok=True)

    filename = f"video_{time.time_ns()}.mp4"
    filepath = src_dir / filename
    filepath.write_bytes(decrypted_data)
    schedule_video_cleanup(filepath)

    current_domain = get_current_domain(request)
    save_seconds = time.perf_counter() - save_start
    print(f"[timing] save_seconds={save_seconds:.3f}")
    return f"{current_domain}/src/{filename}"

def build_stream_url(request, encrypted_url: str, content_key: Optional[bytes],
                     backup_urls=None) -> str:
    current_domain = get_current_domain(request)
    key_b64 = base64.b64encode(content_key).decode('ascii') if content_key else ''
    params_list = [('url', encrypted_url), ('key', key_b64)]
    for bk in (backup_urls or []):
        params_list.append(('bk', bk))
    params = urlencode(params_list)
    return f"{current_domain}/stream?{params}"

def stream_cenc_decrypt_chunk(chunk: bytearray, chunk_start: int,
                              decrypt_map: Dict[int, Tuple[int, bytes]],
                              content_key: bytes) -> bytes:
    chunk_end = chunk_start + len(chunk)

    for sample_off in list(decrypt_map.keys()):
        sample_sz, iv = decrypt_map[sample_off]
        sample_end = sample_off + sample_sz

        if sample_off >= chunk_end or sample_end <= chunk_start:
            continue

        overlap_start = max(sample_off, chunk_start)
        overlap_end = min(sample_end, chunk_end)
        overlap_size = overlap_end - overlap_start
        offset_in_sample = overlap_start - sample_off

        start_block = offset_in_sample // 16
        ctr_int = int.from_bytes(bytes(iv) + b'\x00' * 8, 'big') + start_block
        ctr_bytes = ctr_int.to_bytes(16, 'big')

        cipher = AES.new(content_key, AES.MODE_CTR, nonce=b"", initial_value=ctr_bytes)

        block_offset = offset_in_sample % 16
        if block_offset > 0:
            cipher.decrypt(b'\x00' * block_offset)

        start_in_chunk = overlap_start - chunk_start
        decrypted = cipher.decrypt(bytes(chunk[start_in_chunk:start_in_chunk + overlap_size]))
        chunk[start_in_chunk:start_in_chunk + overlap_size] = decrypted

    return bytes(chunk)

def refresh_fallback_url(video_id: str) -> Tuple[str, Dict[str, str]]:
    device_keys = get_device_keys()
    target_url = build_video_model_url(
        device_keys["device_id"], device_keys["install_id"]
    )

    post_payload = {
        "biz_param": {
            "detail_page_version": 0,
            "device_level": 3,
            "disable_digg_stat": False,
            "need_all_video_definition": True,
            "need_mp4_align": False,
            "use_os_player": False,
            "use_server_dns": False,
            "video_platform": 1024,
        },
        "mixed_video_id_map": {"1004": [video_id]},
    }
    signed_url, headers, post_body = sign_json_request_with_liushen(
        target_url, post_payload, device_keys
    )

    resp = curl_request(signed_url, headers, post_body, 30)
    data = json.loads(resp)

    fallback_api, _ = extract_fallback_api(data, video_id)
    return fallback_api, device_keys

def derive_content_key(spade_b64: str) -> bytes:
    s = spade_b64.strip()
    m = 4 - len(s) % 4
    if m != 4:
        s += "=" * m

    raw = base64.b64decode(s)

    if len(raw) < 3:
        raise ValueError(f"spade_a too short: {len(raw)} bytes")

    v6 = raw[0] ^ raw[1] ^ raw[2]
    v8 = len(raw) - v6 + 47

    if v8 <= 0 or v8 > len(raw) * 2:
        raise ValueError(f"spade_a: computed v8={v8} out of range")
    if 1 + v8 > len(raw):
        v8 = len(raw) - 1
    if v8 < 33:
        raise ValueError(f"spade_a: v8={v8} too small (need >=33)")

    v13 = bytearray(raw[1 : 1 + v8])

    vA, vB = 85, 246
    for i in range(v8):
        popcnt = bin(i).count("1")
        if i & 1:
            v24 = vA
            vA = v13[i]
        else:
            v24 = vB
            vB = v13[i]
        v25 = v24 ^ v13[i]
        v26 = -21 - popcnt
        v13[i] = (v26 + v25) & 0xFF

    hex_str = bytes(v13[1:33]).decode("ascii")
    key = binascii.unhexlify(hex_str)
    return key

def decrypt_mp4_cenc(data: bytes, content_key: bytes) -> bytes:
    data = bytearray(data)

    ftyp_end = struct.unpack(">I", data[0:4])[0]
    if ftyp_end + 8 >= len(data):
        raise ValueError("invalid MP4: ftyp too large")

    moov_size = struct.unpack(">I", data[ftyp_end : ftyp_end + 4])[0]
    if ftyp_end + 8 + moov_size > len(data):
        raise ValueError("invalid MP4: moov out of range")
    moov = data[ftyp_end + 8 : ftyp_end + moov_size]

    t1_off, t1_sz = find_box(moov, "trak", 0)
    t2_off, _ = find_box(moov, "trak", t1_off + t1_sz)

    for t_off in (t1_off, t2_off):
        if t_off < 0:
            continue
        result = parse_track(moov, t_off)
        if result is None:
            continue
        sizes, offsets, cns, aux_off, aux_sz, ns = result
        if ns == 0:
            continue
        if aux_off + aux_sz > len(data):
            continue
        aux = data[aux_off : aux_off + aux_sz]

        si, ap = 0, 0
        for ci, off in enumerate(offsets):
            for k in range(cns[ci]):
                if si >= ns:
                    break
                sz = sizes[si]
                if off + sz > len(data):
                    break

                iv = bytearray(8)
                if ap + 8 <= len(aux):
                    iv[:] = aux[ap : ap + 8]
                ctr_bytes = bytes(iv) + b"\x00" * 8

                cipher = AES.new(content_key, AES.MODE_CTR, nonce=b"", initial_value=ctr_bytes)
                decrypted = cipher.decrypt(bytes(data[off : off + sz]))
                data[off : off + sz] = decrypted

                off += sz
                si += 1
                ap += 8

    for old, new in ((b"encv", b"hvc1"), (b"enca", b"mp4a")):
        _replace_fourcc(data, old, new)

    _replace_sinf(data)

    return bytes(data)

def find_box(data: bytearray, fourcc: str, start: int) -> Tuple[int, int]:
    b = fourcc.encode("ascii")
    for i in range(start, len(data) - 8):
        if data[i : i + 4] == b and i >= 4:
            sz = struct.unpack(">I", data[i - 4 : i])[0]
            if 0 < sz < 5000000:
                return i - 4, sz
    return -1, 0

def get_box(data: bytearray, fourcc: str, stbl_off: int):
    o, sz = find_box(data, fourcc, stbl_off)
    if o >= 0:
        return data[o + 8 : o + sz]
    return None

def parse_track(
    moov: bytearray, t_off: int
) -> Optional[Tuple[List[int], List[int], List[int], int, int, int]]:
    stbl_off, _ = find_box(moov, "stbl", t_off + 8)

    stsz = get_box(moov, "stsz", stbl_off)
    if stsz is None:
        return None
    ds = struct.unpack(">I", stsz[4:8])[0]
    ns = struct.unpack(">I", stsz[8:12])[0]
    sizes: List[int] = []
    if ds == 0:
        for i in range(ns):
            sizes.append(struct.unpack(">I", stsz[12 + i * 4 : 16 + i * 4])[0])
    else:
        sizes = [ds] * ns

    stco = get_box(moov, "stco", stbl_off)
    if stco is None:
        return None
    nc = struct.unpack(">I", stco[4:8])[0]
    offsets = []
    for i in range(nc):
        offsets.append(struct.unpack(">I", stco[8 + i * 4 : 12 + i * 4])[0])

    stsc = get_box(moov, "stsc", stbl_off)
    if stsc is None:
        return None
    nsc = struct.unpack(">I", stsc[4:8])[0]
    entries = []
    for i in range(nsc):
        entries.append((
            struct.unpack(">I", stsc[8 + i * 12 : 12 + i * 12])[0],
            struct.unpack(">I", stsc[12 + i * 12 : 16 + i * 12])[0],
            struct.unpack(">I", stsc[16 + i * 12 : 20 + i * 12])[0],
        ))

    cns = [0] * nc
    for i in range(nsc):
        fc = entries[i][0]
        spc = entries[i][1]
        end = nc
        if i + 1 < nsc:
            end = entries[i + 1][0] - 1
        for c in range(fc - 1, min(end, nc)):
            cns[c] = spc

    saiz = get_box(moov, "saiz", stbl_off)
    if saiz is None:
        return None
    da = saiz[4]
    na = struct.unpack(">I", saiz[5:9])[0]

    saio = get_box(moov, "saio", stbl_off)
    if saio is None:
        return None
    aux_off = struct.unpack(">I", saio[8:12])[0]
    aux_sz = na * max(da, 8)

    return sizes, offsets, cns, aux_off, aux_sz, ns

def select_best_quality(
    video_list: Dict[str, Any], preferred_key: Optional[str] = None
) -> Tuple[str, Dict[str, Any]]:
    if preferred_key and preferred_key in video_list and isinstance(
        video_list[preferred_key], dict
    ):
        return preferred_key, video_list[preferred_key]
    best_key = ""
    best_item: Dict[str, Any] = {}
    best_height = 0
    for k, item in video_list.items():
        if not isinstance(item, dict):
            continue
        h = int(item.get("vheight", 0))
        if h > best_height:
            best_height = h
            best_key = k
            best_item = item
        elif h == best_height and best_item:
            cur_br = int(item.get("bitrate", 0))
            best_br = int(best_item.get("bitrate", 0))
            if cur_br > best_br:
                best_key = k
                best_item = item
    return best_key, best_item

def build_response_payload(
    video_id: str,
    video_model: Dict[str, Any],
    video_data: Dict[str, Any],
    best_item: Dict[str, Any],
) -> Dict[str, Any]:
    pic = first_non_empty(
        best_item.get("cover"),
        best_item.get("poster"),
        video_model.get("origin_cover"),
        video_model.get("cover_url"),
        video_model.get("dynamic_cover"),
        video_model.get("cover"),
        video_data.get("cover"),
        video_data.get("poster"),
    )
    url = first_non_empty(
        best_item.get("main_url"),
        best_item.get("play_addr"),
        best_item.get("backup_url_1"),
        best_item.get("url"),
    )
    height = stringify_int(first_non_empty(best_item.get("vheight"), best_item.get("height")))
    width = stringify_int(first_non_empty(best_item.get("vwidth"), best_item.get("width")))

    return {
        "vid": video_id,
        "pic": normalize_media_url(pic),
        "url": normalize_media_url(url),
        "quality": format_quality(best_item, height),
        "duration": format_duration(first_non_empty(video_model.get("duration"), video_data.get("duration"))),
        "size": format_size(first_non_empty(best_item.get("size"), best_item.get("data_size"), best_item.get("file_size"))),
        "height": height,
        "width": width,
        "create_time": format_create_time(
            first_non_empty(
                video_model.get("create_time"),
                video_model.get("publish_time"),
                video_data.get("create_time"),
                video_data.get("publish_time"),
            )
        ),
    }

def first_non_empty(*values: Any) -> str:
    for value in values:
        normalized = unwrap_media_value(value)
        if normalized:
            return normalized
    return ""

def unwrap_media_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, list):
        for item in value:
            normalized = unwrap_media_value(item)
            if normalized:
                return normalized
        return ""
    if isinstance(value, dict):
        for key in ("url", "uri", "src", "download_url"):
            normalized = unwrap_media_value(value.get(key))
            if normalized:
                return normalized
        for key in ("url_list", "urls"):
            normalized = unwrap_media_value(value.get(key))
            if normalized:
                return normalized
    return ""

def normalize_media_url(value: str) -> str:
    if value.startswith("//"):
        return f"https:{value}"
    return value

def stringify_int(value: Any) -> str:
    if value in (None, ""):
        return ""
    try:
        return str(int(float(value)))
    except (TypeError, ValueError):
        return str(value)

def format_quality(best_item: Dict[str, Any], height: str) -> str:
    label = first_non_empty(
        best_item.get("quality"),
        best_item.get("definition"),
        best_item.get("gear_name"),
        best_item.get("quality_desc"),
    )
    if label:
        return label
    if height:
        return f"{height}p"
    return ""

def format_duration(value: Any) -> str:
    if value in (None, ""):
        return ""
    try:
        total_seconds = int(float(value))
    except (TypeError, ValueError):
        return str(value)

    minutes, seconds = divmod(total_seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours > 0:
        return f"{hours}小时{minutes}分钟{seconds}秒"
    return f"{minutes}分钟{seconds}秒"

def format_size(value: Any) -> str:
    if value in (None, ""):
        return ""
    try:
        size_bytes = float(value)
    except (TypeError, ValueError):
        return str(value)

    units = ["B", "KB", "MB", "GB", "TB"]
    unit_index = 0
    while size_bytes >= 1024 and unit_index < len(units) - 1:
        size_bytes /= 1024
        unit_index += 1
    return f"{size_bytes:.2f}{units[unit_index]}"

def format_create_time(value: Any) -> str:
    if value in (None, ""):
        return ""
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return ""
        if text.endswith("Z"):
            return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(
                timezone(timedelta(hours=8))
            ).isoformat()
        try:
            numeric = float(text)
        except ValueError:
            return text
        value = numeric

    if isinstance(value, (int, float)):
        timestamp = float(value)
        if timestamp > 1e12:
            timestamp /= 1000
        dt = datetime.fromtimestamp(timestamp, tz=timezone(timedelta(hours=8)))
        return dt.isoformat()
    return str(value)

def decrypt_spade_url(b64_str: str, key_seed: bytes) -> str:
    if not b64_str:
        return ""

    raw = b64_decode_padded(b64_str)
    if len(raw) < 5:
        raise ValueError("Ciphertext too short")
    if raw[0] != 0xA8 or raw[2] != 0x01 or raw[3] != 0x00:
        raise ValueError("Ciphertext header format error")

    cipher_data = raw[4:]
    cipher_len = (len(cipher_data) // 16) * 16
    cipher_data = cipher_data[:cipher_len]

    constants = bytes([
        0x4D, 0xD4, 0xC2, 0xE6, 0xB8, 0x31, 0x62, 0x09, 0x0E, 0x52, 0xB3, 0xC7, 0xA6, 0x73, 0x3B, 0xA4,
        0x1C, 0xB2, 0x46, 0x2B, 0x82, 0x9A, 0xB5, 0x8A, 0x19, 0x6B, 0x39, 0xDB, 0x57, 0x17, 0x75, 0x24,
        0xF4, 0x9B, 0xAF, 0x7F, 0x08, 0xE8, 0xD6, 0x8D, 0x26, 0xA7, 0x2E, 0x37, 0xC1, 0xA9, 0x5A, 0x2F,
        0x1F, 0x05, 0xA5, 0x18, 0x92, 0xAE, 0xF2, 0x94, 0x97, 0x32, 0xB6, 0x2A, 0x38, 0xAA, 0xDD, 0x58,
    ])

    h1 = hashlib.sha512(key_seed).digest()
    h2 = hashlib.sha512(h1 + constants).digest()
    aes_key = h2[:16]
    iv = h2[16:32]

    cipher = AES.new(aes_key, AES.MODE_CBC, iv=iv)
    plaintext = cipher.decrypt(cipher_data)

    if plaintext:
        pad = plaintext[-1]
        if 1 <= pad <= 16 and pad <= len(plaintext):
            plaintext = plaintext[:-pad]

    return plaintext.rstrip(b"\x00").decode("utf-8", errors="replace")

def b64_decode_padded(s: str) -> bytes:
    s = s.strip()
    pad = len(s) % 4
    if pad:
        s += "=" * (4 - pad)
    try:
        return base64.b64decode(s)
    except Exception:
        return base64.urlsafe_b64decode(s)

def _replace_fourcc(data: bytearray, old: bytes, new: bytes) -> None:
    old_len = len(old)
    for i in range(len(data) - old_len):
        if data[i : i + old_len] == old:
            data[i : i + len(new)] = new

def _replace_sinf(data: bytearray) -> None:
    i = 0
    while i < len(data) - 4:
        if data[i : i + 4] == b"sinf":
            if i >= 4:
                sz = struct.unpack(">I", data[i - 4 : i])[0]
                if 0 < sz < 50000:
                    data[i : i + 4] = b"free"
                    end = min(i - 4 + sz, len(data))
                    for j in range(i + 4, end):
                        data[j] = 0
                    i = end
                    continue
        i += 1

_NINE_IMG_KEY = b'f5d965df75336270'
_NINE_IMG_IV = b'97b60394abc2fbe1'
_LEGACY_COVER_XOR = b'2019ysapp7'

def _is_plain_image(data):
    return (data[:2] == b'\xff\xd8'
            or data[:8] == b'\x89PNG\r\n\x1a\n'
            or (data[:4] == b'RIFF' and len(data) >= 12
                and data[8:12] == b'WEBP')
            or data[:5] in (b'GIF87a', b'GIF89a'))

def _image_content_type(data):
    if data[:8] == b'\x89PNG\r\n\x1a\n':
        return 'image/png'
    if data[:4] == b'RIFF' and len(data) >= 12 and data[8:12] == b'WEBP':
        return 'image/webp'
    if data[:5] in (b'GIF87a', b'GIF89a'):
        return 'image/gif'
    return 'image/jpeg'

def _nine_cbc_decrypt(data):
    if AES is not None:
        try:
            return AES.new(_NINE_IMG_KEY, AES.MODE_CBC, iv=_NINE_IMG_IV).decrypt(data)
        except Exception:
            pass
    cipher = StdAES(_NINE_IMG_KEY)
    out = bytearray()
    prev = _NINE_IMG_IV
    for offset in range(0, len(data), 16):
        block = data[offset:offset + 16]
        plain = cipher.decrypt_block(block)
        out += bytes(a ^ b for a, b in zip(plain, prev))
        prev = block
    return bytes(out)

def _decrypt_cover_image(raw):
    data = bytes(raw)
    if data and len(data) % 16 == 0:
        try:
            plain = _nine_cbc_decrypt(data)
            if _is_plain_image(plain):
                pad = plain[-1]
                if 0 < pad <= 16 and all(b == pad for b in plain[-pad:]):
                    plain = plain[:-pad]
                if plain[:2] == b'\xff\xd8':
                    end = plain.rfind(b'\xff\xd9')
                    if end >= 0:
                        plain = plain[:end + 2]
                elif plain[:8] == b'\x89PNG\r\n\x1a\n':
                    end = plain.rfind(b'IEND')
                    if end >= 0:
                        plain = plain[:end + 8]
                return plain
        except Exception:
            pass
    if data:
        plain = bytearray(data)
        limit = min(len(plain), 100)
        for i in range(limit):
            plain[i] ^= _LEGACY_COVER_XOR[i % len(_LEGACY_COVER_XOR)]
        if _is_plain_image(bytes(plain)):
            return bytes(plain)
    return data

def _local_proxy_base():
    ports = []
    if _EMBEDDED_PORT > 0:
        ports.append(_EMBEDDED_PORT)
    try:
        ports.append(int((Path(tempfile.gettempdir())
                          / '.hongguo_embedded_port').read_text().strip()))
    except Exception:
        pass
    match = re.search(r':(\d+)$', _CURRENT_DOMAIN)
    if match:
        ports.append(int(match.group(1)))
    for port in ports:
        if port <= 0:
            continue
        try:
            with socket.create_connection(('127.0.0.1', port), timeout=0.5):
                return 'http://127.0.0.1:%d' % port
        except OSError:
            continue
    return ''

def _img_proxy_url(url, ref=''):
    url = str(url or '').strip()
    if not url:
        return ''
    base = _local_proxy_base()
    if not base:
        return url
    params = {'url': url}
    if ref:
        params['ref'] = ref
    return base + '/img?' + urlencode(params)

class _PlayHandler(BaseHTTPRequestHandler):
    parser = None
    src_dir = None
    base_url = ''
    _resolve_cache = {}
    _vid_locks = {}
    _guard = threading.Lock()
    _download_locks = {}
    _download_guard = threading.Lock()
    _RESOLVE_TTL = 300
    _img_cache = {}
    _img_guard = threading.Lock()
    _IMG_CACHE_MAX = 600

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        params = parse_qs(parsed.query)
        if path == '/play':
            self._handle_play(params)
        elif path == '/stream':
            self._handle_stream(params)
        elif path.startswith('/src/'):
            self._handle_src(path[5:])
        elif path == '/img':
            self._handle_img(params)
        elif path == '/health':
            self._handle_health()
        else:
            self.send_error(404)

    def do_HEAD(self):
        parsed = urlparse(self.path)
        path = parsed.path
        if path == '/stream':
            params = parse_qs(parsed.query)
            self._handle_stream(params, head_only=True)
        elif path.startswith('/src/'):
            self._handle_src(path[5:], head_only=True)
        else:
            self.send_error(404)

    def _handle_play(self, params):
        vid = params.get('vid', [''])[0].strip()
        if not vid.isdigit():
            self._json(400, {'error': 'vid invalid'})
            return
        try:
            result = self._resolve_cached(vid)
            media_url = str(result.get('url') or '')
            if not media_url:
                self._json(502, {'error': 'no media url'})
                return
            self.send_response(302)
            self.send_header('Location', media_url)
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
        except Exception as exc:
            self._json(502, {'error': str(exc)})

    def _handle_img(self, params):
        target = params.get('url', [''])[0].strip()
        ref = params.get('ref', [''])[0].strip()
        if not (target.startswith('http://') or target.startswith('https://')):
            self._json(400, {'error': 'url invalid'})
            return
        try:
            data, ctype = self._img_cached(target, ref)
        except Exception as exc:
            self._json(502, {'error': str(exc)})
            return
        self.send_response(200)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'public, max-age=86400')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(data)

    def _img_cached(self, target, ref):
        with self._img_guard:
            hit = self._img_cache.get(target)
        if hit:
            return hit
        headers = {'User-Agent': UA_DESKTOP}
        if ref:
            headers['Referer'] = ref
        raw = http_bytes(target, headers=headers, timeout=25)
        if not raw:
            raise ValueError('图片抓取失败: %s' % target[:80])
        if _is_plain_image(raw):
            data, ctype = raw, _image_content_type(raw)
        else:
            data = _decrypt_cover_image(raw)
            ctype = _image_content_type(data)
        with self._img_guard:
            if len(self._img_cache) >= self._IMG_CACHE_MAX:
                self._img_cache.clear()
            self._img_cache[target] = (data, ctype)
        return data, ctype

    def _resolve_cached(self, vid):
        with self._guard:
            if vid not in self._vid_locks:
                self._vid_locks[vid] = threading.Lock()
            if len(self._resolve_cache) > 1000:
                now = time.time()
                for k in [k for k, v in self._resolve_cache.items()
                          if now - v[0] > self._RESOLVE_TTL]:
                    self._resolve_cache.pop(k, None)
        lock = self._vid_locks[vid]
        with lock:
            hit = self._resolve_cache.get(vid)
            if hit and time.time() - hit[0] < self._RESOLVE_TTL:
                return hit[1]
            result = handle_video_request(vid, None, max_retries=3, stream_mode=True)
            self._resolve_cache[vid] = (time.time(), result)
            return result

    def _handle_src(self, filename, head_only=False):
        if not self.src_dir:
            self.send_error(503, 'src_dir not configured')
            return
        safe_name = Path(filename).name
        filepath = self.src_dir / safe_name
        if not filepath.exists() or not filepath.is_file():
            self.send_error(404)
            return
        file_size = filepath.stat().st_size
        range_header = self.headers.get('Range', '')
        start = 0
        end = file_size - 1
        is_partial = False
        if range_header:
            m = re.match(r'bytes=(\d+)-(\d*)', range_header)
            if m:
                start = int(m.group(1))
                if m.group(2):
                    end = int(m.group(2))
                is_partial = True
        if start > end or start >= file_size:
            self.send_error(416)
            return
        content_length = end - start + 1
        if is_partial:
            self.send_response(206)
            self.send_header('Content-Range',
                             'bytes %d-%d/%d' % (start, end, file_size))
        else:
            self.send_response(200)
        self.send_header('Content-Type', 'video/mp4')
        self.send_header('Content-Length', str(content_length))
        self.send_header('Accept-Ranges', 'bytes')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Expose-Headers',
                         'Content-Length, Content-Range, Accept-Ranges')
        self.end_headers()
        if head_only:
            return
        with open(filepath, 'rb') as f:
            f.seek(start)
            remaining = content_length
            while remaining > 0:
                chunk = f.read(min(65536, remaining))
                if not chunk:
                    break
                self.wfile.write(chunk)
                remaining -= len(chunk)

    def _handle_health(self):
        self._json(200, {
            'ok': True,
            'service': 'hongguo-embedded',
            'parser': self.parser is not None,
            'src_dir': str(self.src_dir) if self.src_dir else '',
        })

    def _respond_probe_size(self, raw_url, backup_urls):
        total = 0
        try:
            session = requests.Session()
            session.trust_env = False
            headers = {
                'User-Agent': 'com.phoenix.read/71332',
                'Referer': 'https://novel.snssdk.com/',
            }
            for cand in [raw_url] + list(backup_urls or []):
                try:
                    resp = session.head(cand, headers=headers, timeout=(8, 10))
                    total = int(resp.headers.get('Content-Length', 0) or 0)
                    if total:
                        break
                except Exception:
                    continue
        except Exception:
            total = 0
        self.send_response(200)
        self.send_header('Content-Type', 'video/mp4')
        if total:
            self.send_header('Content-Length', str(total))
        self.send_header('Accept-Ranges', 'bytes')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Expose-Headers',
                         'Content-Length, Content-Range, Accept-Ranges')
        self.end_headers()

    def _handle_stream(self, params, head_only=False):
        raw_url = params.get('url', [''])[0]
        key_b64 = params.get('key', [''])[0]
        if not raw_url:
            self.send_error(400, 'missing url param')
            return

        backup_urls = [u for u in params.get('bk', []) if u]
        candidate_urls = [raw_url] + backup_urls

        try:
            content_key = base64.b64decode(key_b64) if key_b64 else None
        except Exception:
            content_key = None

        if content_key is not None:
            url_hash = hashlib.md5(raw_url.encode()).hexdigest()[:16]
            filename = 'video_%s.mp4' % url_hash
            if not self.src_dir:
                self.send_error(503, 'src_dir not configured')
                return
            filepath = self.src_dir / filename

            if filepath.exists() and filepath.is_file():
                self._handle_src(filename, head_only)
                return

            if head_only:
                self._respond_probe_size(raw_url, backup_urls)
                return

            range_header = self.headers.get('Range', '')
            if not head_only:
                try:
                    with self._download_guard:
                        if filename not in self._download_locks:
                            self._download_locks[filename] = threading.Lock()
                        s_lock = self._download_locks[filename]
                    if s_lock.acquire(blocking=False):
                        try:
                            if filepath.exists() and filepath.is_file():
                                self._handle_src(filename, head_only)
                                return
                            self._stream_cenc_direct(
                                raw_url, backup_urls, content_key,
                                total_size_hint=0, filename=filename,
                                filepath=filepath)
                            return
                        finally:
                            s_lock.release()
                    else:
                        with s_lock:
                            pass
                        if filepath.exists() and filepath.is_file():
                            self._handle_src(filename, head_only)
                            return
                        print('[stream] streaming peer failed, fallback to full download')
                except Exception as exc:
                    print('[stream] streaming failed, fallback to full download: %s' % exc)

            try:
                with self._download_guard:
                    if filename not in self._download_locks:
                        self._download_locks[filename] = threading.Lock()
                    dl_lock = self._download_locks[filename]
                with dl_lock:
                    if filepath.exists() and filepath.is_file():
                        self._handle_src(filename, head_only)
                        return
                    print('[stream] full download + decrypt...')
                    encrypted_data = download_video_bytes(raw_url, backup_urls)
                    decrypted_data = decrypt_video_bytes(encrypted_data, content_key)
                    filepath.write_bytes(decrypted_data)
                    schedule_video_cleanup(filepath)
                    print('[stream] cached %s (%d bytes)' % (filename, len(decrypted_data)))
                self._handle_src(filename, head_only)
            except Exception as exc:
                print('[stream] download_decrypt error: %s' % exc)
                try:
                    self.send_error(502, 'stream failed: %s' % exc)
                except Exception:
                    pass
            return

        session = requests.Session()
        session.trust_env = False
        dl_headers = {
            'User-Agent': 'com.phoenix.read/71332',
            'Referer': 'https://novel.snssdk.com/',
        }

        total_size = 0
        active_url = candidate_urls[0]
        for cand in candidate_urls:
            try:
                head_resp = session.head(cand, headers=dl_headers, timeout=(8, 10))
                total_size = int(head_resp.headers.get('Content-Length', 0))
                active_url = cand
                break
            except Exception:
                continue
        if total_size == 0:
            for cand in candidate_urls:
                try:
                    probe = session.get(cand, headers=dict(dl_headers, Range='bytes=0-0'),
                                        timeout=(8, 15), stream=True)
                    cr = probe.headers.get('Content-Range', '')
                    if '/' in cr:
                        total_size = int(cr.rsplit('/', 1)[1])
                    probe.close()
                    active_url = cand
                    break
                except Exception:
                    continue

        range_header = self.headers.get('Range', '')
        start = 0
        end = total_size - 1 if total_size > 0 else 0
        is_partial = False
        if range_header and total_size > 0:
            m = re.match(r'bytes=(\d+)-(\d*)', range_header)
            if m:
                start = int(m.group(1))
                if m.group(2):
                    end = int(m.group(2))
                is_partial = True

        if content_key is None:
            try:
                range_headers = dict(dl_headers)
                if is_partial:
                    range_headers['Range'] = 'bytes=%d-%d' % (start, end)
                resp = None
                last_exc = None
                for cand in candidate_urls:
                    try:
                        resp = session.get(cand, headers=range_headers,
                                           stream=True, timeout=(8, 90))
                        resp.raise_for_status()
                        break
                    except Exception as exc:
                        last_exc = exc
                        resp = None
                        continue
                if resp is None:
                    raise last_exc or RuntimeError('all cdn nodes failed')
                content_length = int(resp.headers.get('Content-Length', 0))
                if is_partial:
                    self.send_response(206)
                    self.send_header('Content-Range',
                        resp.headers.get('Content-Range',
                            'bytes %d-%d/%d' % (start, end, total_size)))
                else:
                    self.send_response(200)
                self.send_header('Content-Type', 'video/mp4')
                if content_length:
                    self.send_header('Content-Length', str(content_length))
                self.send_header('Accept-Ranges', 'bytes')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.send_header('Access-Control-Expose-Headers',
                    'Content-Length, Content-Range, Accept-Ranges')
                self.end_headers()
                if head_only:
                    return
                for chunk in resp.iter_content(65536):
                    self.wfile.write(chunk)
            except Exception as exc:
                try:
                    self.send_error(502, 'proxy failed: %s' % exc)
                except Exception:
                    pass
            return

    def _stream_cenc_direct(self, raw_url, backup_urls, content_key,
                            total_size_hint=0, filename='', filepath=None):

        session = requests.Session()
        session.trust_env = False
        dl_headers = {
            'User-Agent': 'com.phoenix.read/71332',
            'Referer': 'https://novel.snssdk.com/',
        }
        candidate_urls = [raw_url] + (backup_urls or [])

        resp = None
        last_exc = None
        for cand in candidate_urls:
            try:
                resp = session.get(cand, headers=dl_headers,
                                    stream=True, timeout=(8, 90))
                resp.raise_for_status()
                break
            except Exception as exc:
                last_exc = exc
                resp = None
                continue
        if resp is None:
            raise last_exc or RuntimeError('all cdn nodes failed')

        total_size = int(resp.headers.get('Content-Length', 0)) or total_size_hint
        chunk_iter = resp.iter_content(256 * 1024)

        buf = bytearray()
        ftyp_size = 0
        moov_end = 0
        for chunk in chunk_iter:
            buf.extend(chunk)
            if len(buf) >= 8 and ftyp_size == 0:
                ftyp_size = struct.unpack('>I', buf[0:4])[0]
            if ftyp_size > 0 and len(buf) >= ftyp_size + 8 and moov_end == 0:
                moov_size = struct.unpack('>I', buf[ftyp_size:ftyp_size + 4])[0]
                moov_end = ftyp_size + moov_size
            if moov_end > 0 and len(buf) >= moov_end:
                break

        if moov_end == 0 or len(buf) < moov_end:
            raise RuntimeError('moov not found in first chunk')

        moov_data = bytearray(buf[ftyp_size + 8:moov_end])
        for old, new in ((b'encv', b'hvc1'), (b'enca', b'mp4a')):
            _replace_fourcc(moov_data, old, new)
        _replace_sinf(moov_data)

        decrypt_map = {}
        t1_off, t1_sz = find_box(moov_data, 'trak', 0)
        t2_off, _ = find_box(moov_data, 'trak', t1_off + t1_sz)
        for t_off in (t1_off, t2_off):
            if t_off < 0:
                continue
            result = parse_track(moov_data, t_off)
            if result is None:
                continue
            sizes, offsets, cns, aux_off, aux_sz, ns = result
            if ns == 0:
                continue

            aux_data = None
            if aux_off + aux_sz <= len(buf):
                aux_data = bytes(buf[aux_off:aux_off + aux_sz])
            else:
                try:
                    aux_resp = session.get(cand, headers={
                        **dl_headers,
                        'Range': 'bytes=%d-%d' % (aux_off, aux_off + aux_sz - 1),
                    }, timeout=10)
                    aux_data = aux_resp.content
                except Exception:
                    aux_data = None
            if not aux_data:
                continue

            ivs = []
            for i in range(0, len(aux_data), 8):
                if i + 8 <= len(aux_data):
                    ivs.append(aux_data[i:i + 8])

            si = 0
            for ci, chunk_off in enumerate(offsets):
                off = chunk_off
                for _ in range(cns[ci]):
                    if si >= ns:
                        break
                    sz = sizes[si]
                    iv = ivs[si] if si < len(ivs) else b'\x00' * 8
                    decrypt_map[off] = (sz, iv)
                    off += sz
                    si += 1

        self.send_response(200)
        if total_size > 0:
            self.send_header('Content-Length', str(total_size))
        self.send_header('Content-Type', 'video/mp4')
        self.send_header('Accept-Ranges', 'bytes')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Expose-Headers',
                         'Content-Length, Content-Range, Accept-Ranges')
        self.end_headers()

        header_bytes = bytes(buf[0:ftyp_size]) + buf[ftyp_size:ftyp_size + 8] + bytes(moov_data)

        part_path = filepath.with_suffix('.part') if filepath else None
        file_handle = None
        try:
            if part_path:
                file_handle = open(part_path, 'wb')
        except Exception:
            file_handle = None

        try:
            self.wfile.write(header_bytes)
            if file_handle:
                file_handle.write(header_bytes)
            current_pos = moov_end

            if len(buf) > moov_end:
                remaining = bytearray(buf[moov_end:])
                decrypted = stream_cenc_decrypt_chunk(
                    remaining, current_pos, decrypt_map, content_key)
                self.wfile.write(decrypted)
                self.wfile.flush()
                if file_handle:
                    file_handle.write(decrypted)
                current_pos += len(remaining)

            buf = None

            for chunk in chunk_iter:
                chunk = bytearray(chunk)
                decrypted = stream_cenc_decrypt_chunk(
                    chunk, current_pos, decrypt_map, content_key)
                self.wfile.write(bytes(decrypted))
                self.wfile.flush()
                if file_handle:
                    file_handle.write(decrypted)
                current_pos += len(chunk)

            if file_handle:
                file_handle.close()
                file_handle = None
            if part_path and part_path.exists() and part_path.stat().st_size > 0:
                part_path.replace(filepath)
                schedule_video_cleanup(filepath)
                print('[stream] streaming complete, cached %s' % filename)
        except BrokenPipeError:
            print('[stream] player disconnected mid-stream')
        except Exception as exc:
            print('[stream] write error: %s' % exc)
        finally:
            if file_handle:
                file_handle.close()
            if part_path and part_path.exists():
                try:
                    part_path.unlink()
                except Exception:
                    pass

    def _stream_cenc(self, session, candidate_urls, dl_headers, content_key,
                     total_size, req_start, req_end, is_partial, head_only):

        resp = None
        last_exc = None
        for cand in candidate_urls:
            try:
                resp = session.get(cand, headers=dl_headers, stream=True, timeout=(8, 90))
                resp.raise_for_status()
                break
            except Exception as exc:
                last_exc = exc
                resp = None
                continue
        if resp is None:
            raise last_exc or RuntimeError('all cdn nodes failed')
        chunk_iter = resp.iter_content(256 * 1024)

        buf = bytearray()
        ftyp_size = 0
        moov_end = 0

        for chunk in chunk_iter:
            buf.extend(chunk)
            if len(buf) >= 8 and ftyp_size == 0:
                ftyp_size = struct.unpack('>I', buf[0:4])[0]
            if ftyp_size > 0 and len(buf) >= ftyp_size + 8 and moov_end == 0:
                moov_size = struct.unpack('>I', buf[ftyp_size:ftyp_size + 4])[0]
                moov_end = ftyp_size + moov_size
            if moov_end > 0 and len(buf) >= moov_end:
                break

        if moov_end == 0 or len(buf) < moov_end:
            print('[stream] moov parse failed, falling back to full download')
            for chunk in chunk_iter:
                buf.extend(chunk)
            decrypted = decrypt_mp4_cenc(buf, content_key)
            self._send_cenc_response(decrypted, total_size,
                                      req_start, req_end, is_partial, head_only)
            return

        moov_data = bytearray(buf[ftyp_size + 8:moov_end])

        for old, new in ((b'encv', b'hvc1'), (b'enca', b'mp4a')):
            _replace_fourcc(moov_data, old, new)
        _replace_sinf(moov_data)

        decrypt_map = {}
        t1_off, t1_sz = find_box(moov_data, 'trak', 0)
        t2_off, _ = find_box(moov_data, 'trak', t1_off + t1_sz)

        for t_off in (t1_off, t2_off):
            if t_off < 0:
                continue
            result = parse_track(moov_data, t_off)
            if result is None:
                continue
            sizes, offsets, cns, aux_off, aux_sz, ns = result
            if ns == 0:
                continue

            aux_data = None
            if aux_off + aux_sz <= len(buf):
                aux_data = bytes(buf[aux_off:aux_off + aux_sz])
            else:
                try:
                    aux_resp = session.get(cand, headers={
                        **dl_headers,
                        'Range': 'bytes=%d-%d' % (aux_off, aux_off + aux_sz - 1),
                    }, timeout=10)
                    aux_data = aux_resp.content
                except Exception as exc:
                    print('[stream] aux fetch failed: %s' % exc)
                    aux_data = None

            if not aux_data:
                continue

            ivs = []
            for i in range(0, len(aux_data), 8):
                if i + 8 <= len(aux_data):
                    ivs.append(aux_data[i:i + 8])

            si = 0
            for ci, chunk_off in enumerate(offsets):
                off = chunk_off
                for _ in range(cns[ci]):
                    if si >= ns:
                        break
                    sz = sizes[si]
                    iv = ivs[si] if si < len(ivs) else b'\x00' * 8
                    decrypt_map[off] = (sz, iv)
                    off += sz
                    si += 1

        if total_size > 0:
            content_length = req_end - req_start + 1
            if is_partial:
                self.send_response(206)
                self.send_header('Content-Range',
                    'bytes %d-%d/%d' % (req_start, req_end, total_size))
            else:
                self.send_response(200)
            self.send_header('Content-Length', str(content_length))
        else:
            self.send_response(200)
        self.send_header('Content-Type', 'video/mp4')
        self.send_header('Accept-Ranges', 'bytes')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Expose-Headers',
            'Content-Length, Content-Range, Accept-Ranges')
        self.end_headers()

        if head_only:
            return

        header_bytes = bytes(buf[0:ftyp_size]) + buf[ftyp_size:ftyp_size + 8] + bytes(moov_data)

        if req_start < moov_end:
            write_end = min(req_end + 1, moov_end)
            self.wfile.write(header_bytes[req_start:write_end])

        current_pos = moov_end

        if len(buf) > moov_end:
            remaining = bytearray(buf[moov_end:])
            decrypted_remaining = stream_cenc_decrypt_chunk(
                remaining, current_pos, decrypt_map, content_key)
            self._write_range_data(decrypted_remaining, current_pos,
                                   req_start, req_end)
            current_pos += len(remaining)

        buf = None

        for chunk in chunk_iter:
            chunk = bytearray(chunk)
            decrypted = stream_cenc_decrypt_chunk(
                chunk, current_pos, decrypt_map, content_key)
            if not self._write_range_data(decrypted, current_pos,
                                          req_start, req_end):
                break
            current_pos += len(chunk)

    def _write_range_data(self, data, data_start, req_start, req_end):
        data_end = data_start + len(data)
        if data_end <= req_start:
            return True
        if data_start > req_end:
            return False
        write_start = max(0, req_start - data_start)
        write_end = min(len(data), req_end - data_start + 1)
        if write_start < write_end:
            self.wfile.write(data[write_start:write_end])
        return True

    def _send_cenc_response(self, data, total_size, req_start, req_end, is_partial, head_only):
        content_length = len(data)
        if is_partial:
            self.send_response(206)
            self.send_header('Content-Range',
                'bytes %d-%d/%d' % (req_start, req_end, total_size or content_length))
            self.send_header('Content-Length', str(req_end - req_start + 1))
        else:
            self.send_response(200)
            self.send_header('Content-Length', str(content_length))
        self.send_header('Content-Type', 'video/mp4')
        self.send_header('Accept-Ranges', 'bytes')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Expose-Headers',
            'Content-Length, Content-Range, Accept-Ranges')
        self.end_headers()
        if head_only:
            return
        if is_partial:
            self.wfile.write(data[req_start:req_end + 1])
        else:
            self.wfile.write(data)

    def _json(self, code, data):
        body = json.dumps(data, ensure_ascii=False).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass

def _find_free_port(preferred):
    for port in range(preferred, preferred + 50):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind(('0.0.0.0', port))
                return port
        except OSError:
            continue
    return 0

import sys
import os
import re
import json
import time
import base64
import hashlib
import struct
import binascii
import random
import hmac
import threading
from urllib.parse import (quote, unquote, urlencode, urlparse, urlsplit,
                          parse_qs, parse_qsl, urljoin)

try:
    import requests
except ImportError:
    requests = None

try:
    import urllib.request as _urlrequest
    import ssl as _ssl
except ImportError:
    _urlrequest = None
    _ssl = None

try:
    import gzip as _gzip
except ImportError:
    _gzip = None

class CompatResponse(object):

    def __init__(self, raw):
        self._raw = raw
        self._body = None
        self.status_code = int(getattr(raw, 'status', 0) or 0)
        self.headers = getattr(raw, 'headers', {}) or {}
        self.encoding = 'utf-8'
        self.url = getattr(raw, 'url', '')

    def _read_all(self):
        data = self._raw.read()
        enc = str(self.headers.get('Content-Encoding') or '').lower()
        if 'gzip' in enc and _gzip is not None and data[:2] == b'\x1f\x8b':
            try:
                data = _gzip.decompress(data)
            except Exception:
                pass
        return data

    @property
    def content(self):
        if self._body is None:
            self._body = self._read_all()
            self.close()
        return self._body

    @property
    def text(self):
        return self.content.decode(self.encoding or 'utf-8', 'replace')

    def iter_content(self, chunk_size=65536):
        size = int(chunk_size or 65536)
        if self._body is not None:
            for offset in range(0, len(self._body), size):
                yield self._body[offset:offset + size]
            return
        try:
            while True:
                chunk = self._raw.read(size)
                if not chunk:
                    break
                yield chunk
        finally:
            self.close()

    def raise_for_status(self):
        if self.status_code >= 400:
            raise Exception('HTTP %s' % self.status_code)
        return self

    def close(self):
        try:
            self._raw.close()
        except Exception:
            pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False

class CompatSession(object):

    def __init__(self):
        self.trust_env = False
        self.headers = {}

    def request(self, method, url, headers=None, data=None, timeout=None,
                stream=False, verify=None, **kwargs):
        if isinstance(timeout, (tuple, list)):
            timeout = timeout[0] if timeout else None
        req = _urlrequest.Request(url, data=data, method=str(method).upper())
        for key, value in dict(headers or {}).items():
            req.add_header(str(key), str(value))
        handlers = [_urlrequest.HTTPSHandler(context=_ssl_context())]
        if self.trust_env is False:
            handlers.insert(0, _urlrequest.ProxyHandler({}))
        opener = _urlrequest.build_opener(*handlers)
        return CompatResponse(opener.open(req, timeout=timeout))

    def get(self, url, **kwargs):
        return self.request('GET', url, **kwargs)

    def post(self, url, **kwargs):
        return self.request('POST', url, **kwargs)

    def head(self, url, **kwargs):
        return self.request('HEAD', url, **kwargs)

    def close(self):
        return

class CompatRequests(object):

    def _session(self):
        session = CompatSession()
        session.trust_env = True
        return session

    def get(self, url, **kwargs):
        return self._session().request('GET', url, **kwargs)

    def post(self, url, **kwargs):
        return self._session().request('POST', url, **kwargs)

    def head(self, url, **kwargs):
        return self._session().request('HEAD', url, **kwargs)

    def request(self, method, url, **kwargs):
        return self._session().request(method, url, **kwargs)

    def Session(self):
        return CompatSession()

if requests is None:
    requests = CompatRequests()

try:
    from base.spider import Spider as _BaseSpider
except ImportError:
    class _BaseSpider(object):
        pass

try:
    import warnings
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    warnings.filterwarnings('ignore', message='Unverified HTTPS request')
except Exception:
    pass

UA_ANDROID = ('Mozilla/5.0 (Linux; Android 14; Pixel 8 Pro) AppleWebKit/537.36 '
              '(KHTML, like Gecko) Chrome/150.0.0.0 Mobile Safari/537.36')
UA_DESKTOP = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
              '(KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36')
UA_TV = ('Mozilla/5.0 (Linux; Android 12; TV) AppleWebKit/537.36 '
         '(KHTML, like Gecko) Chrome/126.0 Safari/537.36')

_DEFAULT_HEADERS = {
    'User-Agent': UA_ANDROID,
    'Accept-Language': 'zh-CN,zh;q=0.9',
}

def _ssl_context():
    try:
        ctx = _ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = _ssl.CERT_NONE
        return ctx
    except Exception:
        return None

def browser_headers(referer='', ua=None, mobile=True):
    if mobile:
        agent = UA_ANDROID
        platform = '"Android"'
    else:
        agent = UA_DESKTOP
        platform = '"Windows"'
    headers = {
        'sec-ch-ua': ('"Chromium";v="150", "Google Chrome";v="150", '
                      'Not_A Brand";v="24"'),
        'sec-ch-ua-mobile': '?0' if not mobile else '?1',
        'sec-ch-ua-platform': platform,
        'upgrade-insecure-requests': '1',
        'User-Agent': ua or agent,
        'Accept': ('text/html,application/xhtml+xml,application/xml;q=0.9,'
                   'image/avif,image/webp,image/apng,*/*;q=0.8'),
        'sec-fetch-site': 'same-origin' if referer else 'none',
        'sec-fetch-mode': 'navigate',
        'sec-fetch-user': '?1',
        'sec-fetch-dest': 'document',
        'Accept-Language': 'zh-CN,zh;q=0.9',
    }
    if referer:
        headers['Referer'] = referer
    return headers

RETRY_STATUS = (0, 403, 429, 500, 502, 503, 504)

def _http_request_once(url, method='GET', headers=None, data=None,
                       json_body=None, timeout=20, encoding='utf-8'):
    method = (method or 'GET').upper()
    hdr = {}
    lowered = {}
    for key, value in _DEFAULT_HEADERS.items():
        hdr[key] = value
        lowered[key.lower()] = key
    if headers:
        for key, value in headers.items():
            if value is None:
                continue
            name = str(key)
            previous = lowered.get(name.lower())
            if previous is not None and previous in hdr:
                del hdr[previous]
            hdr[name] = str(value)
            lowered[name.lower()] = name

    body = None
    if json_body is not None:
        body = json.dumps(json_body, ensure_ascii=False).encode('utf-8')
        hdr['Content-Type'] = 'application/json; charset=utf-8'
    elif data is not None:
        if isinstance(data, dict):
            body = urlencode(data).encode('utf-8')
        elif isinstance(data, (bytes, bytearray)):
            body = bytes(data)
        else:
            body = str(data).encode('utf-8')
        if 'content-type' not in lowered:
            hdr['Content-Type'] = 'application/x-www-form-urlencoded'

    if requests is not None:
        try:
            resp = requests.request(method, url, headers=hdr, data=body,
                                    timeout=timeout, verify=False)
            if encoding:
                resp.encoding = encoding
                return resp.status_code, resp.text
            return resp.status_code, resp.content
        except Exception:
            return 0, ''

    if _urlrequest is None:
        return 0, ''
    try:
        req = _urlrequest.Request(url, data=body, method=method)
        for key, value in hdr.items():
            req.add_header(key, value)
        ctx = _ssl_context()
        resp = _urlrequest.urlopen(req, timeout=timeout, context=ctx)
        raw = resp.read()
        status = getattr(resp, 'status', None) or resp.getcode()
        if encoding:
            try:
                return status, raw.decode(encoding, 'ignore')
            except Exception:
                return status, raw.decode('utf-8', 'ignore')
        return status, raw
    except Exception as exc:
        try:
            status = int(getattr(exc, 'code', 0) or 0)
            raw = exc.read()
            if encoding:
                return status, raw.decode(encoding, 'ignore')
            return status, raw
        except Exception:
            return 0, ''

def http_request(url, method='GET', headers=None, data=None, json_body=None,
                 timeout=20, encoding='utf-8', retries=0):
    status, text = _http_request_once(url, method, headers, data, json_body,
                                      timeout, encoding)
    attempt = 0
    while status in RETRY_STATUS and attempt < max(0, int(retries or 0)):
        attempt += 1
        time.sleep(1.5 * attempt)
        status, text = _http_request_once(url, method, headers, data,
                                          json_body, timeout, encoding)
    return status, text

def _headers_to_dict(msg):
    out = {}
    if not msg:
        return out
    get_all = getattr(msg, 'get_all', None)
    if get_all is not None:
        try:
            seen = []
            for name in msg.keys():
                if name in seen:
                    continue
                seen.append(name)
                values = get_all(name) or []
                out[str(name)] = ', '.join(str(v) for v in values)
            return out
        except Exception:
            pass
    try:
        out = dict(msg)
    except Exception:
        out = {}
    return out

def http_response(url, method='GET', headers=None, body=None, timeout=20,
                  encoding='utf-8'):
    hdr = dict(_DEFAULT_HEADERS)
    if headers:
        for key, value in headers.items():
            if value is None:
                continue
            hdr[str(key)] = str(value)
    if isinstance(body, str):
        body = body.encode('utf-8')

    if requests is not None:
        try:
            resp = requests.request((method or 'GET').upper(), url, headers=hdr,
                                    data=body, timeout=timeout, verify=False)
            heads = _headers_to_dict(getattr(resp, 'headers', None))
            text = resp.text if encoding else resp.content
            if encoding:
                resp.encoding = encoding
                text = resp.text
            return resp.status_code, text, heads
        except Exception:
            return 0, '', {}

    try:
        req = _urlrequest.Request(url, data=body, method=(method or 'GET').upper())
        for key, value in hdr.items():
            req.add_header(key, value)
        resp = _urlrequest.urlopen(req, timeout=timeout, context=_ssl_context())
        raw = resp.read()
        heads = _headers_to_dict(getattr(resp, 'headers', None))
        text = raw.decode(encoding, 'ignore') if encoding else raw
        return (getattr(resp, 'status', 0) or resp.getcode()), text, heads
    except Exception as exc:
        heads = {}
        raw = b''
        status = 0
        try:
            status = int(getattr(exc, 'code', 0) or 0)
            heads = _headers_to_dict(getattr(exc, 'headers', None))
            raw = exc.read()
        except Exception:
            pass
        return status, (raw.decode(encoding, 'ignore') if (encoding and raw)
                        else (raw if raw else '')), heads

def http_get(url, headers=None, timeout=20, encoding='utf-8', retries=0):
    return http_request(url, 'GET', headers=headers, timeout=timeout,
                        encoding=encoding, retries=retries)

def http_post(url, data=None, json_body=None, headers=None, timeout=20,
              encoding='utf-8', retries=0):
    return http_request(url, 'POST', headers=headers, data=data,
                        json_body=json_body, timeout=timeout,
                        encoding=encoding, retries=retries)

def http_bytes(url, headers=None, timeout=30, method='GET', data=None):
    status, raw = http_request(url, method, headers=headers, data=data,
                               timeout=timeout, encoding=None)
    if raw and isinstance(raw, (bytes, bytearray)):
        return bytes(raw)
    if isinstance(raw, str):
        return raw.encode('utf-8', 'ignore')
    return b''

def clean_text(value):
    if value is None:
        return ''
    return ' '.join(str(value).split())

def map_str(data, *keys):
    if not isinstance(data, dict):
        return ''
    for key in keys:
        if key not in data:
            continue
        value = data[key]
        if isinstance(value, str):
            return value
        if isinstance(value, bool):
            return 'true' if value else 'false'
        if isinstance(value, (int, float)):
            if isinstance(value, float) and value == int(value):
                return str(int(value))
            return str(value)
        if isinstance(value, dict):
            for inner in ('url', 'name', 'text', 'value', 'content'):
                got = map_str(value, inner)
                if got:
                    return got
        if isinstance(value, list) and value:
            got = ''
            for item in value:
                if isinstance(item, str) and item.strip():
                    got = item
                    break
                if isinstance(item, dict):
                    got = map_str(item, 'url', 'name', 'text')
                    if got:
                        break
            if got:
                return got
    return ''

def first_non_empty(*values):
    for value in values:
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return ''

def atoi(value, default=0):
    try:
        number = int(str(value).strip())
    except (TypeError, ValueError):
        return default
    if number < 1:
        return default
    return number

def to_int(value, default=0):
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default

def truncate(text, limit):
    text = text or ''
    if len(text) <= limit:
        return text
    return text[:limit] + '...'

def human_count(raw):
    try:
        number = int(str(raw).strip())
    except (TypeError, ValueError):
        return str(raw or '0')
    if number >= 10000:
        text = '%.1f' % (number / 10000.0)
        if text.endswith('.0'):
            text = text[:-2]
        return text + '万'
    return str(number)

def resolve_url(page_url, ref):
    if not ref:
        return ''
    ref = str(ref).strip()
    if ref.startswith('http://') or ref.startswith('https://'):
        return ref
    if ref.startswith('//'):
        return 'https:' + ref
    try:
        return urljoin(page_url or '', ref)
    except Exception:
        return ref

def is_http_media(value):
    if not value:
        return False
    text = str(value).strip()
    try:
        parsed = urlparse(text)
    except Exception:
        return False
    if not parsed.hostname:
        return False
    return parsed.scheme.lower() in ('http', 'https')

def json_loads(text):
    try:
        return json.loads(text)
    except Exception:
        return None

def as_list(value):
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        return [value]
    return []

def as_dict(value):
    return value if isinstance(value, dict) else {}

def unwrap_data(payload, *keys):
    data = as_dict(payload)
    for key in keys:
        if key in data:
            return data[key]
    for key in ('data', 'result', 'results', 'list', 'items', 'records',
                'rows', 'content', 'resultList'):
        if key in data:
            return data[key]
    return None

def sort_episodes(episodes):
    try:
        return sorted(episodes, key=lambda item: item.get('no', 0) or 0)
    except Exception:
        return episodes

def html_unescape(text):
    if not text:
        return ''
    text = str(text)
    for raw, rep in (('&quot;', '"'), ('&#x2F;', '/'), ('&#47;', '/'),
                     ('&#x27;', "'"), ('&#39;', "'"), ('&lt;', '<'),
                     ('&gt;', '>'), ('&nbsp;', ' '), ('&amp;', '&')):
        text = text.replace(raw, rep)
    return text

def strip_tags(text):
    if not text:
        return ''
    return clean_text(re.sub(r'<[^>]+>', ' ', str(text)))

def md5_hex(text):
    if isinstance(text, str):
        text = text.encode('utf-8')
    return hashlib.md5(text or b'').hexdigest()

def sha256_hex(text):
    if isinstance(text, str):
        text = text.encode('utf-8')
    return hashlib.sha256(text or b'').hexdigest()

def sha1_hex(text):
    if isinstance(text, str):
        text = text.encode('utf-8')
    return hashlib.sha1(text or b'').hexdigest()

def b64_encode(raw):
    if isinstance(raw, str):
        raw = raw.encode('utf-8')
    return base64.b64encode(raw or b'').decode('ascii')

def b64_decode(text, padding=True):
    try:
        if not padding:
            text = str(text).rstrip('=')
            text += '=' * (-len(text) % 4)
        return base64.b64decode(str(text))
    except Exception:
        return b''

def _aes_tables():
    sbox = [0] * 256
    p = 1
    q = 1
    while True:
        p = p ^ ((p << 1) & 0xFF) ^ (0x1B if p & 0x80 else 0)
        q ^= (q << 1) & 0xFF
        q ^= (q << 2) & 0xFF
        q ^= (q << 4) & 0xFF
        if q & 0x80:
            q ^= 0x09
        x = q ^ ((q << 1) | (q >> 7)) ^ ((q << 2) | (q >> 6)) \
              ^ ((q << 3) | (q >> 5)) ^ ((q << 4) | (q >> 4))
        sbox[p] = (x ^ 0x63) & 0xFF
        if p == 1:
            break
    sbox[0] = 0x63
    inv = [0] * 256
    for index, value in enumerate(sbox):
        inv[value] = index
    return sbox, inv

_AES_SBOX, _AES_INV_SBOX = _aes_tables()
_AES_RCON = [0x01, 0x02, 0x04, 0x08, 0x10, 0x20, 0x40, 0x80, 0x1B, 0x36,
             0x6C, 0xD8, 0xAB, 0x4D]

def _xtime(a):
    a <<= 1
    if a & 0x100:
        a = (a ^ 0x1B) & 0xFF
    return a

def _mul(a, b):
    result = 0
    for _ in range(8):
        if b & 1:
            result ^= a
        b >>= 1
        a = _xtime(a)
    return result & 0xFF

class StdAES(object):

    def __init__(self, key):
        if isinstance(key, str):
            key = key.encode('utf-8')
        self.key = bytes(key)
        self.rounds = len(self.key) // 4 + 6
        self._round_keys = self._expand()

    def _expand(self):
        nk = len(self.key) // 4
        words = [list(self.key[i * 4:i * 4 + 4]) for i in range(nk)]
        total = 4 * (self.rounds + 1)
        for index in range(nk, total):
            temp = list(words[index - 1])
            if index % nk == 0:
                temp = temp[1:] + temp[:1]
                temp = [_AES_SBOX[b] for b in temp]
                temp[0] ^= _AES_RCON[index // nk - 1]
            elif nk > 6 and index % nk == 4:
                temp = [_AES_SBOX[b] for b in temp]
            words.append([words[index - nk][i] ^ temp[i] for i in range(4)])
        return [bytes(sum(words[i:i + 4], [])) for i in range(0, total, 4)]

    def encrypt_block(self, block):
        state = list(block[:16])
        state = [state[i] ^ self._round_keys[0][i] for i in range(16)]
        for rnd in range(1, self.rounds + 1):
            state = [_AES_SBOX[b] for b in state]
            state = self._shift_rows(state)
            if rnd != self.rounds:
                state = self._mix_columns(state)
            key = self._round_keys[rnd]
            state = [state[i] ^ key[i] for i in range(16)]
        return bytes(state)

    def decrypt_block(self, block):
        state = list(block[:16])
        key = self._round_keys[self.rounds]
        state = [state[i] ^ key[i] for i in range(16)]
        for rnd in range(self.rounds - 1, -1, -1):
            state = self._inv_shift_rows(state)
            state = [_AES_INV_SBOX[b] for b in state]
            key = self._round_keys[rnd]
            state = [state[i] ^ key[i] for i in range(16)]
            if rnd != 0:
                state = self._inv_mix_columns(state)
        return bytes(state)

    @staticmethod
    def _shift_rows(state):
        out = [0] * 16
        for col in range(4):
            for row in range(4):
                out[col * 4 + row] = state[((col + row) % 4) * 4 + row]
        return out

    @staticmethod
    def _inv_shift_rows(state):
        out = [0] * 16
        for col in range(4):
            for row in range(4):
                out[col * 4 + row] = state[((col - row) % 4) * 4 + row]
        return out

    @staticmethod
    def _mix_columns(state):
        out = [0] * 16
        for col in range(4):
            base = col * 4
            a = state[base:base + 4]
            out[base + 0] = _mul(a[0], 2) ^ _mul(a[1], 3) ^ a[2] ^ a[3]
            out[base + 1] = a[0] ^ _mul(a[1], 2) ^ _mul(a[2], 3) ^ a[3]
            out[base + 2] = a[0] ^ a[1] ^ _mul(a[2], 2) ^ _mul(a[3], 3)
            out[base + 3] = _mul(a[0], 3) ^ a[1] ^ a[2] ^ _mul(a[3], 2)
        return out

    @staticmethod
    def _inv_mix_columns(state):
        out = [0] * 16
        for col in range(4):
            base = col * 4
            a = state[base:base + 4]
            out[base + 0] = (_mul(a[0], 14) ^ _mul(a[1], 11)
                             ^ _mul(a[2], 13) ^ _mul(a[3], 9))
            out[base + 1] = (_mul(a[0], 9) ^ _mul(a[1], 14)
                             ^ _mul(a[2], 11) ^ _mul(a[3], 13))
            out[base + 2] = (_mul(a[0], 13) ^ _mul(a[1], 9)
                             ^ _mul(a[2], 14) ^ _mul(a[3], 11))
            out[base + 3] = (_mul(a[0], 11) ^ _mul(a[1], 13)
                             ^ _mul(a[2], 9) ^ _mul(a[3], 14))
        return out

def pkcs7_pad(data, block_size=16):
    if isinstance(data, str):
        data = data.encode('utf-8')
    pad_len = block_size - (len(data) % block_size)
    return bytes(data) + bytes([pad_len] * pad_len)

def pkcs7_unpad(data):
    if not data:
        return b''
    pad_len = data[-1]
    if pad_len < 1 or pad_len > 16 or pad_len > len(data):
        return data
    return data[:-pad_len]

def aes_ecb_encrypt(key, data):
    if isinstance(data, str):
        data = data.encode('utf-8')
    data = pkcs7_pad(data)
    cipher = StdAES(key)
    return b''.join(cipher.encrypt_block(data[i:i + 16])
                    for i in range(0, len(data), 16))

def aes_ecb_decrypt(key, data):
    cipher = StdAES(key)
    out = b''.join(cipher.decrypt_block(data[i:i + 16])
                   for i in range(0, len(data) - len(data) % 16, 16))
    return pkcs7_unpad(out)

def aes_cbc_encrypt(key, iv, data):
    if isinstance(data, str):
        data = data.encode('utf-8')
    data = pkcs7_pad(data)
    if isinstance(iv, str):
        iv = iv.encode('utf-8')
    cipher = StdAES(key)
    out = b''
    prev = bytes(iv[:16])
    for i in range(0, len(data), 16):
        block = bytes(a ^ b for a, b in zip(data[i:i + 16], prev))
        prev = cipher.encrypt_block(block)
        out += prev
    return out

def aes_cbc_decrypt(key, iv, data):
    if isinstance(iv, str):
        iv = iv.encode('utf-8')
    cipher = StdAES(key)
    out = b''
    prev = bytes(iv[:16])
    for i in range(0, len(data) - len(data) % 16, 16):
        block = data[i:i + 16]
        plain = cipher.decrypt_block(block)
        out += bytes(a ^ b for a, b in zip(plain, prev))
        prev = block
    return pkcs7_unpad(out)

def aes_ctr_crypt(key, nonce, data, counter_start=0):
    if isinstance(nonce, str):
        nonce = nonce.encode('utf-8')
    if isinstance(data, str):
        data = data.encode('utf-8')
    cipher = StdAES(key)
    out = bytearray(len(data))
    base = int.from_bytes(nonce[:16].ljust(16, b'\x00'), 'big')
    block_index = 0
    for offset in range(0, len(data), 16):
        value = (base + counter_start + block_index) % (1 << 128)
        keystream = cipher.encrypt_block(value.to_bytes(16, 'big'))
        chunk = data[offset:offset + 16]
        for i in range(len(chunk)):
            out[offset + i] = chunk[i] ^ keystream[i]
        block_index += 1
    return bytes(out)

def _ghash_multiply(x, y):
    result = 0
    for i in range(128):
        if (y >> (127 - i)) & 1:
            result ^= x
        if x & 1:
            x = (x >> 1) ^ 0xE1000000000000000000000000000000
        else:
            x >>= 1
    return result

def _ghash(h, data):
    length = len(data)
    blocks = length // 16
    if length % 16:
        blocks += 1
        data = data + b'\x00' * (16 - length % 16)
    y = 0
    for i in range(blocks):
        block = int.from_bytes(data[i * 16:i * 16 + 16], 'big')
        y = _ghash_multiply(y ^ block, h)
    return y

def _gcm_j0(nonce):
    if isinstance(nonce, str):
        nonce = nonce.encode('utf-8')
    nonce = bytes(nonce)
    if len(nonce) == 12:
        return nonce + b'\x00\x00\x00\x01'
    padded = nonce + b'\x00' * (16 - len(nonce) % 16 if len(nonce) % 16 else 0)
    h = int.from_bytes(StdAES(b'\x00' * 16).encrypt_block(b'\x00' * 16), 'big')
    s = _ghash(h, padded + struct.pack('>QQ', 0, len(nonce) * 8))
    return s.to_bytes(16, 'big')

def _ghash_aad(h, aad, payload):
    def pad16(chunk):
        return chunk + b'\x00' * ((-len(chunk)) % 16)
    return _ghash(h, pad16(aad) + pad16(payload)
                  + struct.pack('>QQ', len(aad) * 8, len(payload) * 8))

def aes_gcm_encrypt(key, nonce, plaintext, aad=b''):
    if isinstance(nonce, str):
        nonce = nonce.encode('utf-8')
    cipher = StdAES(key)
    h = int.from_bytes(cipher.encrypt_block(b'\x00' * 16), 'big')
    j0 = _gcm_j0(nonce)
    enc_j0 = cipher.encrypt_block(j0)
    out = bytearray(len(plaintext))
    counter = int.from_bytes(j0, 'big')
    for offset in range(0, len(plaintext), 16):
        chunk = plaintext[offset:offset + 16]
        counter += 1
        block = counter.to_bytes(16, 'big')
        keystream = cipher.encrypt_block(block)
        for i in range(len(chunk)):
            out[offset + i] = chunk[i] ^ keystream[i]
    s = _ghash_aad(h, aad, bytes(out))
    tag = (s ^ int.from_bytes(enc_j0, 'big')).to_bytes(16, 'big')
    return bytes(out), tag

def aes_gcm_decrypt(key, nonce, ciphertext, tag=b'', aad=b''):
    if isinstance(nonce, str):
        nonce = nonce.encode('utf-8')
    if isinstance(ciphertext, str):
        ciphertext = ciphertext.encode('utf-8')
    if not tag and len(ciphertext) >= 16:
        tag = ciphertext[-16:]
        ciphertext = ciphertext[:-16]
    cipher = StdAES(key)
    h = int.from_bytes(cipher.encrypt_block(b'\x00' * 16), 'big')
    j0 = _gcm_j0(nonce)
    enc_j0 = cipher.encrypt_block(j0)
    out = bytearray(len(ciphertext))
    counter = int.from_bytes(bytes(j0), 'big')
    for offset in range(0, len(ciphertext), 16):
        chunk = ciphertext[offset:offset + 16]
        counter += 1
        block = counter.to_bytes(16, 'big')
        keystream = cipher.encrypt_block(block)
        for i in range(len(chunk)):
            out[offset + i] = chunk[i] ^ keystream[i]
    s = _ghash_aad(h, aad, ciphertext)
    expect = (s ^ int.from_bytes(enc_j0, 'big')).to_bytes(16, 'big')
    if tag and tag != expect:
        raise ValueError('GCM 校验失败')
    return bytes(out)

def _der_read(data, offset):
    tag = data[offset]
    offset += 1
    length = data[offset]
    offset += 1
    if length & 0x80:
        count = length & 0x7F
        length = int.from_bytes(data[offset:offset + count], 'big')
        offset += count
    return tag, data[offset:offset + length], offset + length

def _der_int(value):
    return int.from_bytes(value, 'big') if value else 0

def _der_sequence(data):
    tag, content, _ = _der_read(data, 0)
    items = []
    offset = 0
    while offset < len(content):
        _, value, offset = _der_read(content, offset)
        items.append(value)
    return items

def rsa_key_from_pem(pem_text):
    text = str(pem_text)
    text = re.sub(r'-----(BEGIN|END)[^-]+-----', '', text)
    text = re.sub(r'\s+', '', text)
    raw = b64_decode(text)
    if not raw:
        raise ValueError('PEM 解析失败')
    items = _der_sequence(raw)
    if len(items) == 3 and len(items[0]) <= 1:
        try:
            items = _der_sequence(items[2])
        except Exception:
            pass
    if len(items) < 4:
        raise ValueError('私钥结构异常')
    return _der_int(items[1]), _der_int(items[3])

_SHA256_DIGEST_INFO_PREFIX = bytes.fromhex(
    '3031300d060960864801650304020105000420')

def rsa_sign_pkcs1_sha256(pem_text, message):
    n, d = rsa_key_from_pem(pem_text)
    if isinstance(message, str):
        message = message.encode('utf-8')
    digest = hashlib.sha256(message).digest()
    t = _SHA256_DIGEST_INFO_PREFIX + digest
    size = (n.bit_length() + 7) // 8
    if len(t) + 11 > size:
        raise ValueError('密钥长度不足')
    em = b'\x00\x01' + b'\xff' * (size - len(t) - 3) + b'\x00' + t
    signature = pow(int.from_bytes(em, 'big'), d, n)
    return b64_encode(signature.to_bytes(size, 'big'))
class HongguoLegacySpider(Spider):
    SITE = 'https://hongguoduanju.com'
    UA = ('Mozilla/5.0 (Linux; Android 12; TV) AppleWebKit/537.36 '
          '(KHTML, like Gecko) Chrome/126.0 Safari/537.36')
    HEADERS = {
        'User-Agent': UA,
        'Accept-Language': 'zh-CN,zh;q=0.9',
    }

    DEFAULT_PORT = 9877

    CATEGORY_CONFIG = {
        'home':       {'type_name': '首页推荐', 'kind': 'home'},
        'real-drama': {'type_name': '真人剧',   'kind': 'category', 'cat': 'real-drama'},
        'comic-drama': {'type_name': '漫剧',    'kind': 'category', 'cat': 'comic-drama'},
        'ai-drama':   {'type_name': 'AI剧',     'kind': 'category', 'cat': 'ai-drama'},
    }

    FILTER_KEY = 'topic'
    FILTER_NAME = '\u9898\u6750'
    TOPIC_TTL = 3600

    FALLBACK_TOPICS = {
        'real-drama': [
            ('爱情', 'romance'), ('年代', 'period'), ('逆袭', 'comeback'),
            ('传奇', 'legend'), ('成长', 'growth'), ('家庭', 'family'),
            ('家族', 'clan'), ('萌宝', 'cute-kids'), ('悬疑', 'suspense'),
            ('惊悚', 'thriller'), ('恐怖', 'horror'), ('志怪', 'supernatural'),
            ('古装', 'costume'), ('玄幻', 'fantasy'), ('奇幻', 'wonder'),
            ('都市', 'urban'), ('青春', 'youth'), ('喜剧', 'comedy'),
            ('科幻', 'sci-fi'), ('灾难', 'disaster'), ('动作冒险', 'action-adventure'),
            ('战争', 'war'), ('综艺', 'variety'), ('剧情', 'drama'),
        ],
        'comic-drama': [
            ('脑洞', 'creative'), ('玄幻', 'fantasy'), ('剧情', 'drama'),
            ('末世', 'apocalypse'), ('豪门', 'wealthy-family'),
            ('奇幻', 'wonder'), ('科幻', 'sci-fi'), ('冒险', 'adventure'),
        ],
        'ai-drama': [
            ('脑洞', 'creative'), ('玄幻', 'fantasy'), ('剧情', 'drama'),
            ('末世', 'apocalypse'), ('豪门', 'wealthy-family'),
            ('奇幻', 'wonder'), ('科幻', 'sci-fi'), ('冒险', 'adventure'),
        ],
    }

    DEFAULT_TYPE_ID = 'home'

    CATEGORIES = [
        {'type_id': k, 'type_name': v['type_name']}
        for k, v in CATEGORY_CONFIG.items()
    ]

    SELECTOR_GROUPS = []

    PAGE_SIZE = 24

    def __init__(self):
        self._cache = {}
        self._topic_cache = {}
        self._server = None
        self._server_port = self.DEFAULT_PORT
        self._server_started = False
        self._parser = None

    def getName(self):
        return '\u7ea2\u679c\u679c[\u77ed]'

    def init(self, extend=''):
        if not self._server_started:
            try:
                self._start_embedded_server(self.DEFAULT_PORT)
            except Exception as exc:
                print('[红果果] 内嵌服务启动失败: %s' % exc)

    def _port_file(self):
        try:
            base = Path(tempfile.gettempdir())
        except Exception:
            base = Path(os.path.dirname(os.path.abspath(__file__)))
        return base / '.hongguo_embedded_port'

    def _read_registered_port(self):
        for path in (self._port_file(),
                     Path(os.path.dirname(os.path.abspath(__file__))) / '.hongguo_embedded_port'):
            try:
                value = int(path.read_text().strip())
            except Exception:
                continue
            if value:
                return value
        return 0

    def _ensure_server(self):
        if self._server_started:
            try:
                with socket.create_connection(('127.0.0.1', self._server_port), timeout=1):
                    return
            except OSError:
                self._server_started = False
                self._server = None

        reg_port = self._read_registered_port()
        if reg_port:
            try:
                with socket.create_connection(('127.0.0.1', reg_port), timeout=1):
                    self._server_port = reg_port
                    self._server_started = True
                    global _CURRENT_DOMAIN
                    _CURRENT_DOMAIN = 'http://127.0.0.1:%d' % reg_port
                    return
            except OSError:
                pass

        try:
            self._start_embedded_server(self.DEFAULT_PORT)
        except Exception as exc:
            print('[红果果] 内嵌服务重启失败: %s' % exc)

    def _start_embedded_server(self, port):
        global _CURRENT_DOMAIN, _EMBEDDED_SERVER, _EMBEDDED_PORT

        if _EMBEDDED_SERVER is not None:
            try:
                with socket.create_connection(('127.0.0.1', _EMBEDDED_PORT), timeout=1):
                    self._server = _EMBEDDED_SERVER
                    self._server_port = _EMBEDDED_PORT
                    self._server_started = True
                    _CURRENT_DOMAIN = 'http://127.0.0.1:%d' % _EMBEDDED_PORT
                    return
            except OSError:
                pass

        reg_port = self._read_registered_port()
        if reg_port and reg_port != port:
            try:
                with socket.create_connection(('127.0.0.1', reg_port), timeout=1):
                    self._server_port = reg_port
                    self._server_started = True
                    _CURRENT_DOMAIN = 'http://127.0.0.1:%d' % reg_port
                    return
            except OSError:
                pass

        actual_port = _find_free_port(port)
        if actual_port == 0:
            raise RuntimeError('no free port near %d' % port)

        public_url = 'http://127.0.0.1:%d' % actual_port
        _CURRENT_DOMAIN = public_url

        src_dir = Path(os.path.dirname(os.path.abspath(__file__))) / 'src'
        src_dir.mkdir(parents=True, exist_ok=True)

        _PlayHandler.src_dir = src_dir
        _PlayHandler.base_url = public_url

        os.environ['APP_PORT'] = str(actual_port)

        server = ThreadingHTTPServer(('0.0.0.0', actual_port), _PlayHandler)
        server_thread = threading.Thread(target=server.serve_forever, daemon=True)
        server_thread.start()

        _EMBEDDED_SERVER = server
        _EMBEDDED_PORT = actual_port

        try:
            self._port_file().write_text(str(actual_port))
        except Exception:
            pass

        self._server = server
        self._server_port = actual_port
        self._server_started = True

        print('[\u7ea2\u679c\u679c] \u5185\u5d4c\u670d\u52a1\u5df2\u542f\u52a8: %s' % public_url)

    def isVideoFormat(self, url):
        return False

    def manualVideoCheck(self):
        return False

    def destroy(self):
        return

    def _get(self, url):
        try:
            resp = requests.get(url, headers=self.HEADERS, timeout=25, verify=False)
            resp.encoding = 'utf-8'
            return resp.text
        except Exception:
            return ''

    def _router_data(self, url):
        cached = self._cache.get(url)
        if cached and time.time() - cached[0] < 180:
            return cached[1]
        html = self._get(url)
        if not html:
            raise RuntimeError('\u9875\u9762\u83b7\u53d6\u5931\u8d25: ' + url)
        matched = re.search(r'_ROUTER_DATA\s*=\s*\{', html)
        if matched:
            brace_start = matched.end() - 1
        else:
            idx = html.find('_ROUTER_DATA')
            if idx < 0:
                raise RuntimeError('\u9875\u9762\u6ca1\u6709\u8def\u7531\u6570\u636e')
            eq_idx = html.find('=', idx)
            if eq_idx < 0:
                raise RuntimeError('\u9875\u9762\u6ca1\u6709\u8def\u7531\u6570\u636e')
            brace_start = html.find('{', eq_idx)
            if brace_start < 0:
                raise RuntimeError('\u9875\u9762\u6ca1\u6709\u8def\u7531\u6570\u636e')
        script_end = html.find('</script>', brace_start)
        if script_end < 0:
            script_end = len(html)
        depth = 0
        i = brace_start
        in_str = False
        escape = False
        while i < script_end:
            ch = html[i]
            if in_str:
                if escape:
                    escape = False
                elif ch == '\\':
                    escape = True
                elif ch == '"':
                    in_str = False
            else:
                if ch == '"':
                    in_str = True
                elif ch == '{':
                    depth += 1
                elif ch == '}':
                    depth -= 1
                    if depth == 0:
                        break
            i += 1
        json_str = html[brace_start:i + 1]
        data = json.loads(json_str)
        self._cache[url] = (time.time(), data)
        return data

    @staticmethod
    def _router_page(data, *names):
        if not isinstance(data, dict):
            return {}
        loader = data.get('loaderData')
        if not isinstance(loader, dict):
            return {}
        for name in names:
            page = loader.get(name)
            if isinstance(page, dict) and page:
                return page
        for key, value in loader.items():
            if not isinstance(value, dict) or not value:
                continue
            for name in names:
                prefix = name.rstrip('$')
                if prefix and key.startswith(prefix):
                    return value
        return {}

    @staticmethod
    def _clean_url(value):
        if not value:
            return ''
        return str(value).replace('\\/', '/').replace('\\u0026', '&').replace('&amp;', '&')

    @staticmethod
    def _decode_html(value):
        if not value:
            return ''
        return (str(value)
                .replace('&quot;', '"')
                .replace('&#x2F;', '/').replace('&#47;', '/')
                .replace('&#x27;', "'").replace('&#39;', "'")
                .replace('&lt;', '<').replace('&gt;', '>')
                .replace('&amp;', '&'))

    def _clean_name(self, name):
        if not name:
            return name
        clean = re.sub(r'\s*第[一二三四五六七八九十\d]+[季部].*$', '', name).strip()
        return clean if clean else name

    @staticmethod
    def _source(item):
        if not isinstance(item, dict):
            return {}
        vd = item.get('video_data')
        return vd if isinstance(vd, dict) and vd else item

    @staticmethod
    def _tag_text(value):
        if isinstance(value, str):
            return value.strip()
        if not isinstance(value, list):
            return ''
        names = []
        for one in value[:5]:
            if isinstance(one, dict):
                text = one.get('name') or one.get('show_name') or one.get('tag_name') or ''
            else:
                text = one
            text = str(text or '').strip()
            if text:
                names.append(text)
        return ' / '.join(names)

    def _vod(self, item):
        source = self._source(item)
        sid = str(source.get('series_id') or source.get('series_id_str') or '')
        tags = self._tag_text(source.get('tags')) or \
            self._tag_text(source.get('category_list')) or \
            self._tag_text(source.get('genre'))
        info = source.get('series_episode_info')
        info = info if isinstance(info, dict) else {}
        count = source.get('episode_cnt') or info.get('episode_cnt') or 0
        remark = str(source.get('episode_right_text') or '')
        if not remark and count:
            remark = '\u5168%s\u96c6' % count
        raw_name = str(source.get('series_name') or source.get('series_title')
                       or source.get('title') or '')
        return {
            'vod_id': sid,
            'vod_name': self._clean_name(raw_name) or sid,
            'vod_pic': self._clean_url(source.get('series_cover')),
            'vod_remarks': remark,
            'vod_tag': str(tags or ''),
            'vod_content': str(source.get('series_intro') or source.get('video_desc') or ''),
        }

    def _topic_options(self, cat):
        cached = self._topic_cache.get(cat)
        if cached and time.time() - cached[0] < self.TOPIC_TTL:
            return cached[1]

        options = []
        try:
            data = self._router_data('%s/category/%s?page=1' % (self.SITE, quote(str(cat))))
            page_data = self._router_page(data, 'category_page', 'category_')
            for group in page_data.get('selectorList') or []:
                if not isinstance(group, dict):
                    continue
                picked = []
                for one in group.get('items') or []:
                    if not isinstance(one, dict):
                        continue
                    topic_id = str(one.get('selector_item_id') or '').strip()
                    if not topic_id:
                        continue
                    picked.append((str(one.get('show_name') or topic_id), topic_id))
                if picked:
                    options = picked
        except Exception as exc:
            print('[\u7ea2\u679c\u679c] \u9898\u6750\u6e05\u5355\u8bfb\u53d6\u5931\u8d25:', exc)

        if not options:
            options = [tuple(x) for x in self.FALLBACK_TOPICS.get(cat, [])]

        self._topic_cache[cat] = (time.time(), options)
        return options

    def _topic_id(self, cat, value):
        wanted = str(value or '').strip()
        if not wanted:
            return ''
        for _name, topic_id in self._topic_options(cat):
            if topic_id == wanted:
                return topic_id
        return ''

    def _build_filters(self):
        filters = {}
        for type_id, cfg in self.CATEGORY_CONFIG.items():
            if cfg.get('kind') != 'category':
                filters[type_id] = []
                continue
            options = self._topic_options(cfg['cat'])
            if not options:
                filters[type_id] = []
                continue
            filters[type_id] = [{
                'key': self.FILTER_KEY,
                'name': self.FILTER_NAME,
                'value': [{'n': '\u5168\u90e8', 'v': ''}] +
                         [{'n': n, 'v': v} for n, v in options],
            }]
        return filters

    def _category_items(self, cat, page=1, topic=''):
        path = '/category/%s' % quote(str(cat))
        if topic:
            path += '/%s' % quote(str(topic))
        url = '%s%s?page=%d' % (self.SITE, path, max(1, int(page or 1)))
        data = self._router_data(url)
        page_data = self._router_page(data, 'category_page', 'category_')
        items = page_data.get('recommendList') or []
        if not items:
            nested = page_data.get('categoryData')
            if isinstance(nested, dict):
                items = nested.get('recommendList') or []
        pagination = page_data.get('pagination')
        pagination = pagination if isinstance(pagination, dict) else {}
        seen = set()
        result = []
        for item in items:
            source = self._source(item)
            sid = str(source.get('series_id') or source.get('series_id_str') or '')
            if sid and sid not in seen:
                seen.add(sid)
                result.append(item)
        return result, pagination

    def _home_items(self):
        data = self._router_data(self.SITE + '/')
        page_data = self._router_page(data, 'page')
        seen = set()
        result = []
        for section in page_data.get('homeSections') or []:
            if not isinstance(section, dict):
                continue
            for item in section.get('video_list') or []:
                source = self._source(item)
                sid = str(source.get('series_id') or source.get('series_id_str') or '')
                if sid and sid not in seen:
                    seen.add(sid)
                    result.append(item)
        if not result:
            result, _ = self._category_items('real-drama', 1)
        return result

    def _category_type(self, value):
        raw = str(value or '').strip()
        if raw in self.CATEGORY_CONFIG:
            return raw
        return self.DEFAULT_TYPE_ID

    def homeContent(self, filter):
        result = {'class': [dict(c) for c in self.CATEGORIES]}
        if filter:
            result['filters'] = self._build_filters()
        result['list'] = self.homeVideoContent().get('list', [])
        return result

    def homeVideoContent(self):
        try:
            items = self._home_items()
            return {'list': [self._vod(x) for x in items]}
        except Exception as exc:
            print('[\u7ea2\u679c\u679c] \u9996\u9875\u8bfb\u53d6\u5931\u8d25:', exc)
            return {'list': []}

    def categoryContent(self, tid, pg, filter, extend):
        page = max(1, int(pg or 1))
        type_id = self._category_type(tid)
        config = self.CATEGORY_CONFIG[type_id]
        try:
            if config['kind'] == 'home':
                vods = [self._vod(x) for x in self._home_items()]
                return {'list': vods, 'page': 1, 'pagecount': 1,
                        'limit': len(vods), 'total': len(vods)}

            topic = ''
            if isinstance(extend, dict):
                topic = self._topic_id(config['cat'], extend.get(self.FILTER_KEY))
            cat = config['cat']

            items, pagination = ([], {})
            if topic:
                try:
                    items, pagination = self._category_items(cat, page, topic)
                except Exception as exc:
                    print('[\u7ea2\u679c\u679c] \u9898\u6750\u5217\u8868\u8bfb\u53d6\u5931\u8d25:', exc)
                if not items and page == 1:
                    topic = ''
            if not items:
                items, pagination = self._category_items(cat, page)
            vods = [self._vod(x) for x in items]
            try:
                total_pages = int(pagination.get('totalPages') or 0)
            except (TypeError, ValueError):
                total_pages = 0
            try:
                total = int(pagination.get('total') or 0)
            except (TypeError, ValueError):
                total = 0
            if len(vods) < self.PAGE_SIZE:
                total_pages = page
            elif total_pages <= page:
                total_pages = page + 1
            if total <= 0:
                total = total_pages * self.PAGE_SIZE
            return {'list': vods, 'page': page, 'pagecount': total_pages,
                    'limit': self.PAGE_SIZE, 'total': total}
        except Exception as exc:
            print('[\u7ea2\u679c\u679c] \u5206\u7c7b\u8bfb\u53d6\u5931\u8d25:', exc)
            return {'list': [], 'page': page, 'pagecount': 1, 'limit': 0, 'total': 0}

    def detailContent(self, ids):
        series_id = str(ids[0])
        url = self.SITE + '/detail?series_id=' + quote(series_id)
        try:
            data = self._router_data(url)
            detail = self._router_page(data, 'detail_page', 'detail_')
            series = detail.get('seriesDetail')
            series = series if isinstance(series, dict) else {}
            vids = series.get('vid_list') or []
            vids = [str(v) for v in vids if str(v)]
            if not vids:
                return {'list': []}

            try:
                accessible = int(series.get('accessible_episode_cnt') or 0)
            except (TypeError, ValueError):
                accessible = 0
            if accessible <= 0:
                accessible = len(vids)

            rows = []
            try:
                rows = fetch_quality_rows(vids[0])
            except Exception as exc:
                print('[红果果] 清晰度列表获取失败:', exc)
            if not rows:
                rows = [{'key': 'auto', 'quality': 'auto'}]

            sources = []
            for row in rows:
                qkey = row.get('key', 'auto')
                qname = _quality_label(row.get('quality', qkey))
                episodes = []
                for idx, vid in enumerate(vids):
                    ep = idx + 1
                    label = '%d' % ep
                    episodes.append('%s$%s_%s|%s' % (label, vid, series_id, qkey))
                sources.append({'name': qname, 'episodes': '#'.join(episodes)})

            tags = self._tag_text(series.get('tags'))
            count = series.get('episode_cnt') or len(vids)
            remark = str(series.get('episode_right_text') or '')
            if not remark and count:
                remark = '全%s集' % count

            vod = {
                'vod_id': series_id,
                'vod_name': self._clean_name(str(series.get('series_name') or series.get('series_title') or '红果短剧')),
                'vod_pic': self._clean_url(series.get('series_cover')),
                'type_name': str(tags or ''),
                'vod_remarks': remark,
                'vod_content': str(series.get('series_intro') or ''),
                'vod_play_from': '$$$'.join(s['name'] for s in sources),
                'vod_play_url': '$$$'.join(s['episodes'] for s in sources),
            }
            return {'list': [vod]}
        except Exception as exc:
            print('[红果果] 详情读取失败:', exc)
            return {'list': []}

    def searchContent(self, key, quick, pg=1):
        page = max(1, int(pg or 1))
        word = str(key).strip()
        if not word:
            return {'list': [], 'page': page}

        try:
            url = self.SITE + '/search/' + quote(word)
            data = self._router_data(url)
            page_data = self._router_page(data, 'search_(keyword)/page', 'search_')
            search_list = page_data.get('searchList') or []
            result = []
            for item in search_list:
                vod = self._vod(item)
                if vod['vod_id'] and vod['vod_name']:
                    result.append(vod)
            if result:
                return {'list': result, 'page': page}
        except Exception as exc:
            print('[红果果] 官网搜索失败:', exc)

        try:
            items, _ = self._category_items('real-drama', 1)
            keyword = word.lower()
            matches = []
            for x in items:
                source = self._source(x)
                hay = '%s %s' % (source.get('series_name') or source.get('series_title') or '',
                                 source.get('series_intro') or '')
                if keyword in hay.lower():
                    matches.append(x)
            per_page = 20
            start = (page - 1) * per_page
            return {
                'list': [self._vod(x) for x in matches[start:start + per_page]],
                'page': page,
            }
        except Exception as exc:
            print('[红果果] 搜索回退失败:', exc)
            return {'list': [], 'page': page}

    def searchContentPage(self, key, quick, pg=1):
        return self.searchContent(key, quick, pg)

    def playerContent(self, flag, pid, vipFlags):
        raw = str(pid).split('#')[0]
        quality_key = 'auto'
        if '|' in raw:
            vid_part, quality_key = raw.split('|', 1)
            quality_key = quality_key or 'auto'
        else:
            vid_part = raw

        if '_' in vid_part:
            vid, sid = vid_part.split('_', 1)
        else:
            vid, sid = vid_part, ''

        self._ensure_server()

        result = {
            'parse': 0,
            'playUrl': '',
            'url': '',
            'header': {
                'User-Agent': self.UA,
                'Referer': self.SITE + '/',
            },
        }

        if self._server_started:
            try:
                resolved = handle_video_request(str(vid), None, max_retries=3,
                                                 stream_mode=True, quality_key=quality_key)
                stream_url = str(resolved.get('url') or '')
                if stream_url and stream_url.startswith('http'):
                    result['url'] = stream_url
                    result['header'] = {'User-Agent': self.UA}
                    return result
            except Exception as exc:
                print('[playerContent] resolve_direct_failed: %s' % exc)

        if sid:
            try:
                player_url = self.SITE + '/player/%s/%s' % (sid, vid)
                data = self._router_data(player_url)
                page = self._router_page(data, 'player_(series_id)/(vid)/page', 'player_')
                vpi = page.get('video_player_info') or {}
                play_url = vpi.get('main_url') or ''
                if play_url:
                    play_url = self._clean_url(play_url)
                    result['url'] = play_url
                    result['header'] = {'User-Agent': self.UA}
                    return result
            except Exception as exc:
                print('[playerContent] web_player_fallback_failed: %s' % exc)

        play_url = 'http://127.0.0.1:%d/play?%s' % (
            self._server_port, urlencode({'vid': vid}))
        result['url'] = play_url
        return result

    def localProxy(self, params):
        return None

class HongguoSite(object):

    key = 'hongguo'
    name = '\u7ea2\u679c'

    def __init__(self):
        self.impl = HongguoLegacySpider()

    def ensure(self):
        try:
            self.impl.init('')
        except Exception as exc:
            print('[\u7ea2\u679c] \u5185\u5d4c\u670d\u52a1\u542f\u52a8\u5931\u8d25: %s' % exc)

    def cats(self):
        return [{'type_id': k, 'type_name': v['type_name']}
                for k, v in HongguoLegacySpider.CATEGORY_CONFIG.items()]

    def extra_filters(self):
        seen = {}
        for cat in ('real-drama', 'comic-drama', 'ai-drama'):
            try:
                options = self.impl._topic_options(cat) or []
            except Exception:
                options = []
            for name, topic_id in options:
                topic_id = str(topic_id or '')
                if topic_id and topic_id not in seen:
                    seen[topic_id] = str(name or topic_id)
        if not seen:
            return []
        return [{
            'key': HongguoLegacySpider.FILTER_KEY,
            'name': HongguoLegacySpider.FILTER_NAME,
            'value': [{'n': '\u5168\u90e8', 'v': ''}] +
                     [{'n': name, 'v': tid} for tid, name in seen.items()],
        }]

    def listing(self, cat, page, extend=None):
        impl = self.impl
        page = page if page and page > 0 else 1
        config = HongguoLegacySpider.CATEGORY_CONFIG.get(cat) or HongguoLegacySpider.CATEGORY_CONFIG[HongguoLegacySpider.DEFAULT_TYPE_ID]
        if config.get('kind') == 'home':
            return [impl._vod(x) for x in impl._home_items()]

        topic = ''
        if isinstance(extend, dict):
            topic = impl._topic_id(config['cat'], extend.get(HongguoLegacySpider.FILTER_KEY))
        items = []
        if topic:
            try:
                items, _pagination = impl._category_items(config['cat'], page, topic)
            except Exception as exc:
                print('[\u7ea2\u679c] \u9898\u6750\u5217\u8868\u8bfb\u53d6\u5931\u8d25:', exc)
                items = []
            if not items and page == 1:
                topic = ''
        if not items:
            items, _pagination = impl._category_items(config['cat'], page)
        return [impl._vod(x) for x in items]

    def search(self, wd, page):
        page = page if page and page > 0 else 1
        result = self.impl.searchContent(wd, False, page) or {}
        return result.get('list') or []

    def detail(self, vid):
        impl = self.impl
        vid = str(vid or '').strip()
        if not vid:
            return {}
        url = impl.SITE + '/detail?series_id=' + quote(vid)
        try:
            data = impl._router_data(url)
            page = impl._router_page(data, 'detail_page', 'detail_')
            series = page.get('seriesDetail')
            series = series if isinstance(series, dict) else {}
            vids = [str(v) for v in (series.get('vid_list') or []) if str(v)]
            if not vids:
                return {}
            rows = []
            try:
                rows = fetch_quality_rows(vids[0])
            except Exception as exc:
                print('[\u7ea2\u679c] \u6e05\u6670\u5ea6\u5217\u8868\u83b7\u53d6\u5931\u8d25:', exc)
            if not rows:
                rows = [{'key': 'auto', 'quality': 'auto'}]

            sources = []
            for row in rows:
                qkey = row.get('key', 'auto')
                qname = _quality_label(row.get('quality', qkey))
                episodes = []
                for index, one_vid in enumerate(vids):
                    episodes.append({'no': index + 1,
                                     'name': '%d' % (index + 1),
                                     'url': '%s_%s|%s' % (one_vid, vid, qkey)})
                sources.append({'name': qname, 'episodes': episodes})

            tags = impl._tag_text(series.get('tags'))
            count = series.get('episode_cnt') or len(vids)
            remark = str(series.get('episode_right_text') or '')
            if not remark and count:
                remark = '\u5168%s\u96c6' % count
            return {
                'vod_id': vid,
                'vod_name': impl._clean_name(str(
                    series.get('series_name') or series.get('series_title')
                    or '\u7ea2\u679c\u77ed\u5267')),
                'vod_pic': impl._clean_url(series.get('series_cover')),
                'vod_class': str(tags or ''),
                'vod_remarks': remark,
                'vod_content': str(series.get('series_intro') or ''),
                'sources': sources,
            }
        except Exception as exc:
            print('[\u7ea2\u679c] \u8be6\u60c5\u8bfb\u53d6\u5931\u8d25:', exc)
            return {}

    def play(self, url):
        impl = self.impl
        raw = str(url or '').split('#')[0]
        quality_key = 'auto'
        if '|' in raw:
            vid_part, quality_key = raw.split('|', 1)
            quality_key = quality_key or 'auto'
        else:
            vid_part = raw
        if '_' in vid_part:
            vid, sid = vid_part.split('_', 1)
        else:
            vid, sid = vid_part, ''

        impl._ensure_server()

        if impl._server_started:
            try:
                resolved = handle_video_request(str(vid), None, max_retries=3,
                                                stream_mode=True,
                                                quality_key=quality_key)
                stream_url = str(resolved.get('url') or '')
                if stream_url.startswith('http'):
                    return {'url': stream_url,
                            'header': {'User-Agent': impl.UA}, 'parse': 0}
            except Exception as exc:
                print('[playerContent] resolve_direct_failed: %s' % exc)

        if sid:
            try:
                player_url = impl.SITE + '/player/%s/%s' % (sid, vid)
                data = impl._router_data(player_url)
                page = impl._router_page(
                    data, 'player_(series_id)/(vid)/page', 'player_')
                info = page.get('video_player_info') or {}
                play_url = info.get('main_url') or ''
                if play_url:
                    return {'url': impl._clean_url(play_url),
                            'header': {'User-Agent': impl.UA}, 'parse': 0}
            except Exception as exc:
                print('[playerContent] web_player_fallback_failed: %s' % exc)

        return {
            'url': 'http://127.0.0.1:%d/play?%s'
                   % (impl._server_port, urlencode({'vid': vid})),
            'header': {},
            'parse': 0,
        }

class WeiguanSite(object):

    key = 'weiguan'
    name = '围观'
    site = 'https://api.drama.9ddm.com'

    CATS = [{'type_id': 'all', 'type_name': '全部'}]

    def __init__(self):
        self._detail_cache = {}

    def cats(self):
        return [dict(item) for item in self.CATS]

    def _headers(self, with_body=False):
        headers = {
            'Accept': 'application/json, text/plain, */*',
            'Origin': self.site,
            'Referer': self.site + '/',
            'User-Agent': 'okhttp/4.10.0',
        }
        if with_body:
            headers['Content-Type'] = 'application/json; charset=utf-8'
        return headers

    def _call(self, method, path, payload=None):
        target = self.site + path
        try:
            if payload is None:
                status, text = http_request(target, method,
                                            headers=self._headers(False), timeout=20)
            else:
                status, text = http_request(target, method,
                                            headers=self._headers(True),
                                            json_body=payload, timeout=20)
        except Exception as exc:
            print('[围观] 请求失败: %s' % exc)
            return None
        if status >= 400 or not text:
            print('[围观] 接口 HTTP %s' % status)
            return None
        envelope = json_loads(text)
        if not isinstance(envelope, dict):
            return None
        data = envelope.get('data')
        if data is None or data == 'null':
            if envelope.get('msg'):
                print('[围观] 接口返回错误: %s' % envelope.get('msg'))
            return None
        return data

    def _items(self, wd, page):
        page = page if page and page > 0 else 1
        payload = {
            'audience': '全部受众',
            'page': page,
            'pageSize': 30,
            'searchWord': wd or '',
            'subject': '全部主题',
        }
        data = self._call('POST', '/drama/home/search?version_code=1500&os_type=1',
                          payload)
        return as_list(data)

    def _card(self, item):
        item = as_dict(item)
        vid = map_str(item, 'oneId')
        title = clean_text(map_str(item, 'title'))
        if not vid or not title:
            return None
        card = {
            'vod_id': vid,
            'vod_name': title,
            'vod_pic': first_non_empty(map_str(item, 'vertPoster'),
                                       map_str(item, 'horizonPoster')),
            'vod_remarks': '',
            'vod_class': '全部',
            'vod_content': '',
        }
        parts = []
        count = atoi(map_str(item, 'episodeCount'), 0)
        if count > 0:
            parts.append('共%d集' % count)
        views = map_str(item, 'viewCount')
        if views and views != '0':
            parts.append(human_count(views) + '播放')
        card['vod_remarks'] = ' · '.join(parts)
        return card

    def _cards(self, items):
        cards = []
        seen = set()
        for item in items:
            card = self._card(item)
            if not card or card['vod_id'] in seen:
                continue
            seen.add(card['vod_id'])
            cards.append(card)
        return cards

    def listing(self, cat, page):
        try:
            return self._cards(self._items('', page))
        except Exception as exc:
            print('[围观] 列表读取失败: %s' % exc)
            return []

    def search(self, wd, page):
        wd = (wd or '').strip()
        if not wd:
            return []
        try:
            return self._cards(self._items(wd, page))
        except Exception as exc:
            print('[围观] 搜索失败: %s' % exc)
            return []

    def _episodes(self, one_id):
        cached = self._detail_cache.get(one_id)
        if cached and time.time() - cached[0] < 300:
            return cached[1]
        path = ('/drama/home/shortVideoDetail?version_code=1000&os_type=1'
                '&oneId=%s&page=1&pageSize=1000' % quote(str(one_id), safe=''))
        data = self._call('GET', path)
        items = as_list(data)
        self._detail_cache[one_id] = (time.time(), items)
        return items

    @staticmethod
    def _ep_order(ep, index):
        order = atoi(map_str(ep, 'playOrder'), 0)
        if order < 1:
            order = atoi(map_str(ep, 'episode'), 0)
        if order < 1:
            order = index + 1
        return order

    @staticmethod
    def _play_url(ep):
        raw = ep.get('playSetting')
        if raw is None:
            raw = ep.get('videoClarityList')
        if isinstance(raw, str):
            text = raw.strip()
            if not text:
                return ''
            parsed = json_loads(text)
            if parsed is None:
                return first_non_empty(map_str(ep, 'playUrl'), text)
            raw = parsed
        if isinstance(raw, dict):
            return first_non_empty(map_str(raw, 'super'), map_str(raw, 'high'),
                                   map_str(raw, 'normal'), map_str(raw, 'url'),
                                   map_str(raw, 'playUrl'))
        if isinstance(raw, list):
            best = None
            for item in raw:
                item = as_dict(item)
                if not item:
                    continue
                if best is None:
                    best = item
                if map_str(item, 'clarity') in ('1080P', '1080p', 'super', '超清'):
                    best = item
            if best is not None:
                return first_non_empty(map_str(best, 'url'),
                                       map_str(best, 'playUrl'))
        return first_non_empty(map_str(ep, 'playUrl'), '')

    def detail(self, vid):
        vid = str(vid or '').strip()
        if not vid:
            return {}
        try:
            items = self._episodes(vid)
            if not items:
                return {}
            first = as_dict(items[0])
            episodes = []
            seen = set()
            for index, ep in enumerate(items):
                ep = as_dict(ep)
                order = self._ep_order(ep, index)
                if order in seen:
                    continue
                seen.add(order)
                episodes.append({
                    'no': order,
                    'name': '第%d集' % order,
                    'url': '%s://%s/%d' % (self.key, vid, order),
                })
            episodes = sort_episodes(episodes)
            if not episodes:
                return {}
            return {
                'vod_id': vid,
                'vod_name': clean_text(map_str(first, 'title')) or ('围观短剧 ' + vid),
                'vod_pic': first_non_empty(map_str(first, 'vertPoster'),
                                           map_str(first, 'horizonPoster')),
                'vod_class': '围观',
                'vod_content': truncate(clean_text(
                    map_str(first, 'description', 'desc')), 400),
                'vod_remarks': '共%d集' % len(episodes),
                'episodes': episodes,
            }
        except Exception as exc:
            print('[围观] 详情读取失败: %s' % exc)
            return {}

    def play(self, url):
        raw = str(url or '')
        prefix = self.key + '://'
        if not raw.startswith(prefix):
            return {'url': raw, 'header': {'User-Agent': 'okhttp/4.10.0'},
                    'parse': 0} if is_http_media(raw) else {}
        rest = raw[len(prefix):]
        if '/' not in rest:
            return {}
        vid, extra = rest.split('/', 1)
        try:
            seq = int(extra)
        except (TypeError, ValueError):
            seq = 1
        if not vid:
            return {}
        try:
            items = self._episodes(vid)
        except Exception as exc:
            print('[围观] 播放读取失败: %s' % exc)
            return {}
        for index, ep in enumerate(items):
            ep = as_dict(ep)
            if self._ep_order(ep, index) != seq:
                continue
            src = self._play_url(ep)
            if not is_http_media(src):
                print('[围观] 第%d集暂无播放地址' % seq)
                return {}
            return {
                'url': src,
                'header': {'User-Agent': 'okhttp/4.10.0',
                           'Referer': self.site + '/'},
                'parse': 0,
            }
        print('[围观] 详情缺第%d集' % seq)
        return {}

class HemaSite(object):

    key = 'hema'
    name = '河马'
    site = 'https://freevideo.zqqds.cn'

    DATAS = ('e5f22c6e2c82fe001738cb9ce4696eab0556d064a55aef402e0fbe6b29a083f6538e4567de38'
             'e67de2071a49d9751526bfba45314e1fd4702b11c76ab9a3b5f873262854ba66e6715ed51364'
             'dbc6ee62c7180e047fcbcdbfd49874fc8f28674b16d90ca71a02de76c70598e0b75e647c37c'
             '2c19287e49be5f2a259d727dfc4df3d28802388bf3c356576b342e17e30a2ab74859263dba4'
             'd1c8eba79990d22d60d60927fdacb2addf2f0eaadd8887585ca2eb87f603faf0c207dda18cf'
             '67dc25b2199d303baff9e6605b3314a7d2631f62864f48619daceb9452f2b7b066777355374'
             '1856df030cca68af3c57810f983d452bb428ef5fc32206aef4865ae06c629bee7f513554730'
             '4acc7ef4e7c6df887308f2e79c493fd2ee03488722861b5bb51b09cb8911dfc92c288d94e60'
             '1c066d2f9d612ad2c8d4eeb4920b1d44aff3e13fd75229b857f64925df1cf12f75a00d438c4'
             '22ec1726462b915903f1dd1f4bb7cdf82cc15a6d507f80c789903e710f39a62aef073f3f93a'
             '6c681e75d295428aa290d7e98f82e7e9ad6e2b23d9086dfe8c63c5d8550b13fd61a77291473'
             'a8bdd43c7c2639f264be69d9d07f0585de4342a399275a64e7d1d4400b8ed4421a2f289f622'
             'e40cdd1cfc916a0b9ce747c924ac33e32d24b91ed5d64772d6ad6896412f52724006eabf12a'
             'aecfd6e81dad432c7b3800bbf793a1c375e3e7b4fb3b097724b5fc88a8c9bcf3dbc10cbdb25'
             '2965')

    KEY_HEX = '647a6b6a67667978677368796c677a6d'
    IV_HEX = '6170697570646f776e65646372797074'

    CODE_LIST = '1125'
    CODE_DETAIL = '1131'
    CODE_CHAPTERS = '1132'
    CODE_PLAY = '1133'
    CODE_SEARCH = '1803'

    CAT_LIST = [
        {'id': 'jingxuan', 'name': '精选', 'channel': 53},
        {'id': 'guzhuang', 'name': '古装', 'channel': 54},
        {'id': 'chongsheng', 'name': '重生', 'channel': 55},
        {'id': 'jiating', 'name': '家庭', 'channel': 56},
        {'id': 'lianai', 'name': '恋爱', 'channel': 57},
    ]

    def __init__(self):
        self._key = binascii.unhexlify(self.KEY_HEX)
        self._iv = binascii.unhexlify(self.IV_HEX)

    def cats(self):
        return [{'type_id': item['id'], 'type_name': item['name']}
                for item in self.CAT_LIST]

    @classmethod
    def _channel(cls, cat):
        for item in cls.CAT_LIST:
            if item['id'] == cat:
                return item['channel'], item['name']
        fallback = cls.CAT_LIST[0]
        return fallback['channel'], fallback['name']

    def _encrypt_body(self, payload):
        raw = json.dumps(payload, ensure_ascii=False).encode('utf-8')
        sealed = aes_cbc_encrypt(self._key, self._iv, raw)
        return binascii.hexlify(sealed).decode('ascii').upper()

    def _decrypt_body(self, text):
        blob = binascii.unhexlify(str(text or '').strip())
        plain = aes_cbc_decrypt(self._key, self._iv, blob)
        if isinstance(plain, bytes):
            plain = plain.decode('utf-8', 'ignore')
        data = json_loads(plain)
        if not isinstance(data, dict):
            raise ValueError('河马接口响应无法解析')
        return data

    def _headers(self):
        return {
            'datas': self.DATAS,
            'content-type': 'text/plain',
            'user-agent': 'okhttp/4.10.0',
            'Referer': self.site,
        }

    def _call(self, code, payload):
        body = self._encrypt_body(payload)
        status, text = http_request(
            self.site + '/free-video-portal/portal/' + code, 'POST',
            headers=self._headers(), data=body, timeout=20)
        if status >= 400:
            raise ValueError('河马接口 HTTP %s' % status)
        envelope = as_dict(json_loads(text))
        sealed = map_str(envelope, 'data')
        if not sealed.strip():
            raise ValueError('河马接口无数据')
        return self._decrypt_body(sealed)

    @staticmethod
    def _video_items(data):
        columns = as_list(data.get('columnData'))
        if not columns:
            return []
        first = as_dict(columns[0])
        return [as_dict(item) for item in as_list(first.get('videoData'))]

    @staticmethod
    def _card(item, cat):
        item = as_dict(item)
        vid = map_str(item, 'bookId')
        title = clean_text(map_str(item, 'bookName'))
        if not vid or not title:
            return None
        card = {
            'vod_id': vid,
            'vod_name': title,
            'vod_pic': map_str(item, 'coverWap'),
            'vod_remarks': '',
            'vod_class': cat or '',
            'vod_content': '',
        }
        count = atoi(map_str(item, 'updateNum'), 0)
        if count > 0:
            card['vod_remarks'] = '更新%d集' % count
        return card

    @staticmethod
    def _cards(items, cat):
        cards = []
        seen = set()
        for item in items:
            card = HemaSite._card(item, cat)
            if not card or card['vod_id'] in seen:
                continue
            seen.add(card['vod_id'])
            cards.append(card)
        return cards

    def listing(self, cat, page):
        page = page if page and page > 0 else 1
        channel_id, channel_name = self._channel(cat)
        try:
            data = self._call(self.CODE_LIST, {
                'recSwitch': True,
                'storePageId': 10002,
                'channelGroupId': '10',
                'channelId': channel_id,
                'channelName': channel_name,
                'lastColumnStyle': 3,
                'fromColumnId': '1',
                'pageFlag': str(page),
                'theaterSubscriptSwitch': True,
            })
            items = self._video_items(data)
            if page > 1 and not items:
                return []
            cards = self._cards(items, channel_name)
            if not cards:
                print('[河马] 列表无内容')
                return []
            return cards
        except Exception as exc:
            print('[河马] 列表读取失败: %s' % exc)
            return []

    def search(self, wd, page):
        wd = (wd or '').strip()
        if not wd:
            return []
        page = page if page and page > 0 else 1
        try:
            data = self._call(self.CODE_SEARCH, {
                'keyword': wd,
                'page': page,
                'size': 15,
            })
            items = [as_dict(item) for item in as_list(data.get('searchVos'))]
            cards = self._cards(items, '')
            if not cards:
                print('[河马] 搜索无结果: %s' % wd)
                return []
            return cards
        except Exception as exc:
            print('[河马] 搜索失败: %s' % exc)
            return []

    def detail(self, vid):
        vid = str(vid or '').strip()
        if not vid:
            return {}
        try:
            info_res = self._call(self.CODE_DETAIL, {'bookId': vid})
            info = as_dict(info_res.get('videoInfo'))
            if not info:
                print('[河马] 详情无数据: %s' % vid)
                return {}
            chapter_res = self._call(self.CODE_CHAPTERS, {
                'bookId': vid,
                'chapterMin': 1,
                'chapterMax': 10000,
            })
            episodes = []
            seen = set()
            for index, raw in enumerate(as_list(chapter_res.get('chapterList'))):
                chapter = as_dict(raw)
                chapter_id = map_str(chapter, 'chapterId')
                if not chapter_id:
                    continue
                order = atoi(map_str(chapter, 'chapterIndex'), 0)
                if order < 1:
                    order = index + 1
                if order in seen:
                    continue
                seen.add(order)
                episodes.append({
                    'no': order,
                    'name': first_non_empty(clean_text(map_str(chapter, 'chapterName')),
                                            '第%d集' % order),
                    'url': 'hema://%s/%s' % (vid, chapter_id),
                })
            episodes = sort_episodes(episodes)
            if not episodes:
                print('[河马] 详情无剧集: %s' % vid)
                return {}
            return {
                'vod_id': vid,
                'vod_name': clean_text(map_str(info, 'bookName')) or ('河马短剧 ' + vid),
                'vod_pic': map_str(info, 'coverWap'),
                'vod_class': clean_text(map_str(info, 'finishStatusCn')),
                'vod_content': truncate(clean_text(map_str(info, 'introduction')), 400),
                'vod_remarks': '共%d集' % len(episodes),
                'episodes': episodes,
            }
        except Exception as exc:
            print('[河马] 详情读取失败: %s' % exc)
            return {}

    def play(self, url):
        raw = str(url or '')
        prefix = self.key + '://'
        if not raw.startswith(prefix):
            return {'url': raw, 'header': {'User-Agent': 'okhttp/4.10.0'},
                    'parse': 0} if is_http_media(raw) else {}
        rest = raw[len(prefix):]
        if '/' not in rest:
            print('[河马] 播放参数无效')
            return {}
        book_id, chapter_id = rest.split('/', 1)
        if not book_id or not chapter_id:
            print('[河马] 播放参数无效')
            return {}
        try:
            data = self._call(self.CODE_PLAY, {
                'bookId': book_id,
                'chapterId': chapter_id,
                'unClockType': 'pay',
                'confirmPay': 2,
                'autoPayFlag': True,
                'omap': {
                    'channelName': '精选',
                    'logId': '17a6500357709bb2547e1e122b438cfc',
                    'originName': '书城',
                    'recId': 'bigdata_rec',
                    'scene': 'nsc_727',
                    'sceneId': 'dzmf_video_sc_reco',
                    'strategyId': 'g6y6b5sq',
                },
            })
            if map_str(data, 'chaptersPayType') != '免费':
                print('[河马] 章节 %s 需付费' % chapter_id)
                return {}
            infos = as_list(data.get('chapterInfo'))
            if not infos:
                print('[河马] 章节 %s 暂无播放地址' % chapter_id)
                return {}
            content = as_dict(as_dict(infos[0]).get('content'))
            if not content:
                print('[河马] 章节 %s 暂无播放地址' % chapter_id)
                return {}
            src = map_str(content, 'm3u8720p')
            if not src:
                for item in as_list(content.get('mp4SwitchUrl')):
                    if str(item or '').strip():
                        src = str(item).strip()
                        break
            if not is_http_media(src):
                print('[河马] 章节 %s 暂无播放地址' % chapter_id)
                return {}
            return {
                'url': src,
                'header': {'User-Agent': 'okhttp/4.10.0',
                           'Referer': self.site + '/'},
                'parse': 0,
            }
        except Exception as exc:
            print('[河马] 播放读取失败: %s' % exc)
            return {}

class ShanhaiSite(object):

    key = 'shanhai'
    name = '山海'
    site = 'https://api.app.gxshxy.com'

    LOGIN_URL = 'https://u.app.gxshxy.com/user/v3/account/login'

    LOGIN_KEY = 'B@ecf920Od8A4df7'
    GCM_KEY = 'xxxxxxwhwqedqder'

    PAGE_SIZE = 24

    CAT_LIST = [
        {'id': 'jingxuan', 'name': '精选', 'class': '0'},
        {'id': 'renqi', 'name': '人气', 'class': '1'},
        {'id': 'xinju', 'name': '新剧', 'class': '51'},
        {'id': 'tianchong', 'name': '甜宠', 'class': '46'},
        {'id': 'nixi', 'name': '逆袭', 'class': '6'},
        {'id': 'qiangzhe', 'name': '强者回归', 'class': '50'},
        {'id': 'qihuan', 'name': '奇幻', 'class': '44'},
        {'id': 'fuchou', 'name': '复仇', 'class': '49'},
        {'id': 'zhongguomeng', 'name': '中国梦', 'class': '41'},
        {'id': 'jiating', 'name': '家庭', 'class': '9'},
    ]

    def __init__(self):
        self._token = ''

    def cats(self):
        return [{'type_id': item['id'], 'type_name': item['name']}
                for item in self.CAT_LIST]

    @classmethod
    def _class_of(cls, cat):
        for item in cls.CAT_LIST:
            if item['id'] == cat:
                return item['class'], item['name']
        fallback = cls.CAT_LIST[0]
        return fallback['class'], fallback['name']

    @classmethod
    def _detail_path(cls, vid):
        return ('/shanhai-theater/v2/theater_parent/detail?theater_parent_id='
                + quote(str(vid), safe=''))

    def _login(self):
        payload = {
            'device': '22ebfeec0a5ad3c0397bae448b8658cc3',
            'install_first_open': True,
            'first_install_time': 1751687627754,
            'last_update_time': 1751687627754,
            'report_link_url': '',
            'android_id': '8f7db6f23d745890',
            'package_name': 'com.shanhai.duanju',
            'authorization': '',
            'timestamp': int(time.time() * 1000),
        }
        raw = json.dumps(payload, ensure_ascii=False)
        sealed = aes_ecb_encrypt(self.LOGIN_KEY, raw)
        body = b64_encode(sealed)
        status, text = http_request(
            self.LOGIN_URL, 'POST',
            headers={'content-type': 'application/json; charset=utf-8'},
            data=body, timeout=20)
        if status >= 400:
            raise ValueError('山海登录接口 HTTP %s' % status)
        envelope = as_dict(json_loads(text))
        token = map_str(as_dict(envelope.get('data')), 'token')
        if not token.strip():
            raise ValueError('山海登录未返回 token')
        return token

    def _auth_token(self):
        if self._token:
            return self._token
        self._token = self._login()
        return self._token

    def _reset_token(self):
        self._token = ''

    def _api_once(self, path):
        token = self._auth_token()
        status, text = http_request(self.site + path, 'GET',
                                    headers={'authorization': token}, timeout=20)
        if status >= 400:
            raise ValueError('山海接口 HTTP %s' % status)
        envelope = as_dict(json_loads(text))
        if not envelope:
            raise ValueError('山海接口响应无法解析')
        payload = as_dict(envelope.get('data'))
        sealed = map_str(payload, 'data').strip()
        if not sealed:
            code = map_str(envelope, 'code').strip()
            if code and code != 'ok':
                raise ValueError('山海接口返回错误: %s' % code)
            raise ValueError('山海接口无数据')
        nonce = binascii.unhexlify(map_str(payload, 'nonce').strip())
        blob = binascii.unhexlify(sealed)
        plain = aes_gcm_decrypt(self.GCM_KEY, nonce, blob)
        if isinstance(plain, bytes):
            plain = plain.decode('utf-8', 'ignore')
        data = json_loads(plain)
        if not isinstance(data, dict):
            raise ValueError('山海接口响应无法解析')
        return data

    def _api(self, path):
        try:
            return self._api_once(path)
        except Exception as first:
            self._reset_token()
            try:
                return self._api_once(path)
            except Exception as second:
                raise second if str(second) else first

    @staticmethod
    def _card(theater, cat):
        theater = as_dict(theater)
        vid = map_str(theater, 'id')
        title = clean_text(map_str(theater, 'title'))
        if not vid or not title:
            return None
        card = {
            'vod_id': vid,
            'vod_name': title,
            'vod_pic': map_str(theater, 'cover_url'),
            'vod_remarks': '',
            'vod_class': cat or '',
            'vod_content': '',
        }
        total = atoi(map_str(theater, 'total'), 0)
        if total > 0:
            card['vod_remarks'] = '共%d集' % total
        return card

    @staticmethod
    def _cards(items, cat):
        cards = []
        seen = set()
        for item in items:
            card = ShanhaiSite._card(item, cat)
            if not card or card['vod_id'] in seen:
                continue
            seen.add(card['vod_id'])
            cards.append(card)
        return cards

    def listing(self, cat, page):
        page = page if page and page > 0 else 1
        class_id, cat_name = self._class_of(cat)
        path = ('/shanhai-theater/v2/theater_parent/cloud/v2/theater/home_page'
                '?theater_class_id=1&type=1&class2_ids=%s&page_num=%d&page_size=%d'
                % (quote(class_id, safe=''), page, self.PAGE_SIZE))
        try:
            data = self._api(path)
            items = as_list(data.get('items'))
            if page > 1 and not items:
                return []
            cards = self._cards([as_dict(as_dict(entry).get('theater'))
                                 for entry in items], cat_name)
            if not cards:
                print('[山海] 列表无内容')
                return []
            return cards
        except Exception as exc:
            print('[山海] 列表读取失败: %s' % exc)
            return []

    def search(self, wd, page):
        wd = (wd or '').strip()
        if not wd:
            return []
        try:
            token = self._auth_token()
            status, text = http_request(
                self.site + '/v3/search', 'POST',
                headers={'authorization': token,
                         'Content-Type': 'application/json',
                         'Accept': 'application/json',
                         'User-Agent': 'okhttp/4.10.0'},
                json_body={'text': wd}, timeout=20)
            if status >= 400:
                print('[山海] 搜索 HTTP %s' % status)
                return []
            envelope = as_dict(json_loads(text))
            theater = as_dict(as_dict(envelope.get('data')).get('theater'))
            items = [as_dict(item) for item in as_list(theater.get('search_data'))]
            cards = self._cards(items, '')
            if not cards:
                print('[山海] 搜索无结果: %s' % wd)
                return []
            return cards
        except Exception as exc:
            print('[山海] 搜索失败: %s' % exc)
            return []

    @staticmethod
    def _tags(data):
        tags = []
        for item in as_list(data.get('desc_tags')):
            text = clean_text(item)
            if text:
                tags.append(text)
        return ' '.join(tags)

    def _detail(self, vid):
        data = self._api(self._detail_path(vid))
        theaters = [as_dict(item) for item in as_list(data.get('theaters'))]
        episodes = []
        for index, theater in enumerate(theaters):
            no = index + 1
            episodes.append({
                'no': no,
                'name': first_non_empty(clean_text(map_str(theater, 'son_title')),
                                        '第%d集' % no),
                'url': 'shanhai://%s/%d' % (vid, no),
            })
        episodes = sort_episodes(episodes)
        if not episodes:
            print('[山海] 详情无剧集: %s' % vid)
            return {}
        return {
            'vod_id': vid,
            'vod_name': clean_text(map_str(data, 'title')) or ('山海短剧 ' + vid),
            'vod_pic': map_str(data, 'cover_url'),
            'vod_class': self._tags(data),
            'vod_content': truncate(clean_text(map_str(data, 'introduction')), 400),
            'vod_remarks': '共%d集' % len(episodes),
            'episodes': episodes,
        }

    def detail(self, vid):
        vid = str(vid or '').strip()
        if not vid:
            return {}
        try:
            return self._detail(vid)
        except Exception as exc:
            print('[山海] 详情读取失败: %s' % exc)
            return {}

    def play(self, url):
        raw = str(url or '')
        prefix = self.key + '://'
        if not raw.startswith(prefix):
            return {'url': raw, 'header': {'User-Agent': 'okhttp/4.10.0'},
                    'parse': 0} if is_http_media(raw) else {}
        rest = raw[len(prefix):]
        if '/' not in rest:
            print('[山海] 播放参数无效')
            return {}
        vid, extra = rest.split('/', 1)
        seq = atoi(extra, 0)
        if not vid or seq < 1:
            print('[山海] 播放参数无效')
            return {}
        try:
            data = self._api(self._detail_path(vid))
            theaters = as_list(data.get('theaters'))
            if seq > len(theaters):
                print('[山海] 详情缺第%d集' % seq)
                return {}
            theater = as_dict(theaters[seq - 1])
            if not theater:
                print('[山海] 详情缺第%d集' % seq)
                return {}
            src = map_str(theater, 'son_video_url')
            if not is_http_media(src):
                print('[山海] 第%d集暂无播放地址' % seq)
                return {}
            return {
                'url': src,
                'header': {'User-Agent': 'okhttp/4.10.0',
                           'Referer': self.site + '/'},
                'parse': 0,
            }
        except Exception as exc:
            print('[山海] 播放读取失败: %s' % exc)
            return {}

class HaokanSite(object):

    key = 'haokan'
    name = '好看'
    site = 'https://sv.baidu.com'

    QUERY = 'log=vhk&tn=1020970b&ctn=1008350n&blur=1'

    USER_AGENT = ('Mozilla/5.0 (Linux; Android 11; Pixel 5) AppleWebKit/537.36 '
                  '(KHTML, like Gecko) Chrome/90.0.4430.91 Mobile Safari/537.36')

    CATS = [
        {'type_id': 'reboduanju', 'type_name': '热播剧'},
        {'type_id': 'xinju', 'type_name': '新剧'},
        {'type_id': 'zhanshen', 'type_name': '战神'},
        {'type_id': 'shenhao', 'type_name': '神豪'},
        {'type_id': 'shenyi', 'type_name': '神医'},
        {'type_id': 'tianchong', 'type_name': '甜宠'},
        {'type_id': 'zhuixu', 'type_name': '赘婿'},
        {'type_id': 'chuanyue', 'type_name': '穿越重生'},
        {'type_id': 'yineng', 'type_name': '异能'},
        {'type_id': 'nuelian', 'type_name': '虐恋'},
        {'type_id': 'gongdou', 'type_name': '宫斗宅斗'},
        {'type_id': 'xuanhuan', 'type_name': '玄幻'},
    ]

    TAG_IDS = {
        'reboduanju': '1',
        'xinju': '2',
        'zhanshen': '1001',
        'shenhao': '2001',
        'shenyi': '1002',
        'tianchong': '1007',
        'zhuixu': '1003',
        'chuanyue': '2004',
        'yineng': '2005',
        'nuelian': '1006',
        'gongdou': '2006',
        'xuanhuan': '2009',
    }

    def cats(self):
        return [dict(item) for item in self.CATS]

    def _tag_id(self, cat):
        return self.TAG_IDS.get(str(cat or '').strip(), self.TAG_IDS['reboduanju'])

    def _cat_name(self, cat):
        cat = str(cat or '').strip()
        for item in self.CATS:
            if item['type_id'] == cat:
                return item['type_name']
        return self.CATS[0]['type_name']

    def _headers(self):
        return {
            'Content-Type': 'application/x-www-form-urlencoded',
            'User-Agent': self.USER_AGENT,
        }

    def _post(self, path, form):
        target = self.site + path
        try:
            status, text = http_request(target, 'POST', headers=self._headers(),
                                        data=form, timeout=20)
        except Exception as exc:
            print('[好看] 请求失败: %s' % exc)
            return None
        if status >= 400 or not text:
            print('[好看] 接口 HTTP %s' % status)
            return None
        payload = json_loads(text)
        if payload is None:
            print('[好看] 接口返回非 JSON')
            return None
        return payload

    def _items(self, cat, page):
        if not page or page < 1:
            page = 1
        form = {
            'tag_id': self._tag_id(cat),
            'pn': str(page),
            'rn': '12',
        }
        payload = self._post('/haokan/ui-feed/playletTagsFeed?' + self.QUERY, form)
        inner = as_dict(as_dict(payload).get('data'))
        if not inner:
            print('[好看] 列表解析失败')
            return []
        return as_list(inner.get('list'))

    def _card(self, item, cat_name):
        item = as_dict(item)
        vid = map_str(item, 'playlet_id')
        title = clean_text(map_str(item, 'playlet_title'))
        if not vid or not title:
            return None
        card = {
            'vod_id': vid,
            'vod_name': title,
            'vod_pic': map_str(item, 'playlet_poster'),
            'vod_remarks': '',
            'vod_class': cat_name,
            'vod_content': '',
        }
        num = atoi(map_str(item, 'episodes_num'), 0)
        if num > 0:
            card['vod_remarks'] = '更新%d集' % num
        return card

    def _cards(self, items, cat_name):
        cards = []
        seen = set()
        for item in items:
            card = self._card(item, cat_name)
            if not card or card['vod_id'] in seen:
                continue
            seen.add(card['vod_id'])
            cards.append(card)
        return cards

    def listing(self, cat, page):
        try:
            return self._cards(self._items(cat, page), self._cat_name(cat))
        except Exception as exc:
            print('[好看] 列表读取失败: %s' % exc)
            return []

    def search(self, wd, page):
        if (wd or '').strip():
            print('[好看] 接口未提供搜索')
        return []

    @staticmethod
    def _split_id(vid):
        parts = str(vid or '').strip().split('@', 1)
        playlet_id = parts[0].strip()
        progress_vid = parts[1].strip() if len(parts) == 2 else ''
        return playlet_id, progress_vid

    def _detail_data(self, playlet_id, progress_vid):
        playlet_id = str(playlet_id or '').strip()
        if not playlet_id:
            print('[好看] 无效的剧集 ID')
            return None
        if not progress_vid:
            progress_vid = 'undefined'
        payload = self._post('/haokan/ui-video/playlet/rec/detail?' + self.QUERY,
                             {'playlet_id': playlet_id, 'vid': progress_vid})
        data = as_dict(payload).get('data')
        data = as_dict(data)
        if not data:
            print('[好看] 详情解析失败')
            return None
        return data

    @staticmethod
    def _str_list(raw):
        out = []
        for item in as_list(raw):
            if isinstance(item, (list, dict)):
                continue
            text = str(item or '').strip()
            if text:
                out.append(text)
        return out

    def detail(self, vid):
        playlet_id, progress_vid = self._split_id(vid)
        if not playlet_id:
            return {}
        try:
            data = self._detail_data(playlet_id, progress_vid)
            if not data:
                return {}
            vids = self._str_list(data.get('vid_list'))
            if not vids:
                print('[好看] 详情无剧集: %s' % playlet_id)
                return {}
            episodes = []
            for index, item in enumerate(vids):
                no = index + 1
                episodes.append({
                    'no': no,
                    'name': '第%d集' % no,
                    'url': '%s://%s' % (self.key, item),
                })
            episodes = sort_episodes(episodes)
            return {
                'vod_id': playlet_id,
                'vod_name': clean_text(map_str(data, 'playlet_title'))
                            or ('好看短剧 ' + playlet_id),
                'vod_pic': map_str(data, 'playlet_poster'),
                'vod_class': '好看',
                'vod_content': truncate(clean_text(map_str(data, 'description')), 400),
                'vod_remarks': '共%d集' % len(episodes),
                'episodes': episodes,
            }
        except Exception as exc:
            print('[好看] 详情读取失败: %s' % exc)
            return {}

    @staticmethod
    def _best_clarity_url(raw):
        best = ''
        best_quality = -1
        for item in as_list(raw):
            entry = as_dict(item)
            src = map_str(entry, 'url')
            if not src:
                continue
            quality = atoi(map_str(entry, 'video_quality'), 0)
            if quality > best_quality:
                best, best_quality = src, quality
        return best

    def _relate(self, vid):
        vid = str(vid or '').strip()
        if not vid:
            print('[好看] 播放 vid 为空')
            return ''
        payload = self._post('/appui/api?cmd=video/relate&' + self.QUERY,
                             {'method': 'post', 'vid': vid})
        top = as_dict(payload)
        if 'video/relate' not in top:
            print('[好看] 播放接口无 video/relate 字段')
            return ''
        inner = as_dict(as_dict(top.get('video/relate')).get('data'))
        src = self._best_clarity_url(as_dict(inner.get('cur_video')).get('clarityUrl'))
        if not src:
            print('[好看] 剧集无播放地址: %s' % vid)
        return src

    def play(self, url):
        raw = str(url or '').strip()
        prefix = self.key + '://'
        if not raw.startswith(prefix):
            return {'url': raw, 'header': {'User-Agent': self.USER_AGENT},
                    'parse': 0} if is_http_media(raw) else {}
        vid = raw[len(prefix):].strip()
        if not vid or '/' in vid:
            print('[好看] 播放参数无效')
            return {}
        try:
            src = self._relate(vid)
        except Exception as exc:
            print('[好看] 播放读取失败: %s' % exc)
            return {}
        if not is_http_media(src):
            print('[好看] 剧集无播放地址: %s' % vid)
            return {}
        return {
            'url': src,
            'header': {'User-Agent': self.USER_AGENT,
                       'Referer': self.site + '/'},
            'parse': 0,
        }

class BaiduSite(object):

    key = 'baidu'
    name = '百度'
    site = 'https://mbd.baidu.com'

    FEED_BASE = 'https://mbd.baidu.com'
    VIDEO_BASE = 'https://sv.baidu.com'

    VIDEO_QUERY = 'log=vhk&tn=1020970b&ctn=1008350n&blur=1'

    APP_USER_AGENT = ('Dalvik/2.1.0 (Linux; U; Android 9; 22081212C Build/PQ3B.190801.002) '
                      'Talos/1.8.13 SP-engine/3.47.0 bd_dvt/1 baiduboxapp/15.21.0.10 '
                      '(Baidu; P1 9)')
    APP_COOKIE = 'BAIDUCUID=0Ovgu0uAvu0_828wj8Hma_8i28joaSuIlOvOigiAH88ikS8f0k35aD6mA'

    CATS = [
        {'type_id': 'quanbu', 'type_name': '全部'},
        {'type_id': 'shenyi', 'type_name': '神医'},
        {'type_id': 'dushi', 'type_name': '都市'},
        {'type_id': 'xiandaiyanqing', 'type_name': '现代言情'},
        {'type_id': 'yineng', 'type_name': '异能'},
        {'type_id': 'nixi', 'type_name': '逆袭'},
        {'type_id': 'tianchong', 'type_name': '甜宠'},
        {'type_id': 'zongcai', 'type_name': '总裁'},
        {'type_id': 'mengbao', 'type_name': '萌宝'},
        {'type_id': 'zhanshen', 'type_name': '战神'},
        {'type_id': 'gongdouzhaidou', 'type_name': '宫斗宅斗'},
        {'type_id': 'shenhao', 'type_name': '神豪'},
        {'type_id': 'nuelian', 'type_name': '虐恋'},
        {'type_id': 'shanhun', 'type_name': '闪婚'},
        {'type_id': 'xuanhuan', 'type_name': '玄幻'},
        {'type_id': 'chuanyuechongsheng', 'type_name': '穿越重生'},
        {'type_id': 'niandai', 'type_name': '年代'},
        {'type_id': 'jiatinglunli', 'type_name': '家庭伦理'},
        {'type_id': 'gudaiyanqing', 'type_name': '古代言情'},
        {'type_id': 'wuxiawuda', 'type_name': '武侠武打'},
        {'type_id': 'zhuixu', 'type_name': '赘婿'},
        {'type_id': 'qingchunxiaoyuan', 'type_name': '青春校园'},
    ]

    def cats(self):
        return [dict(item) for item in self.CATS]

    def _theme(self, cat):
        cat = str(cat or '').strip()
        for item in self.CATS:
            if item['type_id'] == cat and item['type_id'] != 'quanbu':
                return item['type_name']
        return ''

    def _cat_name(self, cat):
        cat = str(cat or '').strip()
        for item in self.CATS:
            if item['type_id'] == cat:
                return item['type_name']
        return self.CATS[0]['type_name']

    def _headers(self):
        return {
            'Content-Type': 'application/x-www-form-urlencoded',
            'User-Agent': self.APP_USER_AGENT,
            'Cookie': self.APP_COOKIE,
        }

    def _post(self, target, form):
        try:
            status, text = http_request(target, 'POST', headers=self._headers(),
                                        data=form, timeout=20)
        except Exception as exc:
            print('[百度] 请求失败: %s' % exc)
            return None
        if status >= 400 or not text:
            print('[百度] 接口 HTTP %s' % status)
            return None
        payload = json_loads(text)
        if payload is None:
            print('[百度] 接口返回非 JSON')
            return None
        return payload

    def _feed_version(self, stamp):
        return md5_hex(str(stamp) + 'v2')

    def _items(self, cat, page):
        if not page or page < 1:
            page = 1
        stamp = int(time.time())
        body = {
            'data': {
                'refreshIndex': page,
                'timestamp': stamp,
                'version': self._feed_version(stamp),
                'themes': [
                    {'kind': '综合', 'names': ['新剧']},
                    {'kind': '题材', 'names': [self._theme(cat)]},
                ],
                'extRequest': {'flow_tabid': '13'},
                'from': 'feed',
                'page': 'channel_video_landing',
                'pd': 'feed',
                'theme': '',
            },
        }
        target = self.FEED_BASE + '/feedapi/v1/videoserver/playlets/list?service=bdbox'
        payload = self._post(target, {'data': json.dumps(body, ensure_ascii=False)})
        inner = as_dict(as_dict(payload).get('data'))
        if not inner:
            print('[百度] 列表解析失败')
            return []
        return as_list(inner.get('items'))

    def _card(self, item, cat_name):
        item = as_dict(item)
        vid = map_str(item, 'collId')
        title = clean_text(map_str(item, 'title'))
        if not vid or not title:
            return None
        return {
            'vod_id': vid,
            'vod_name': title,
            'vod_pic': map_str(item, 'img'),
            'vod_remarks': first_non_empty(clean_text(map_str(item, 'updateStatus')),
                                           '全集'),
            'vod_class': cat_name,
            'vod_content': '',
        }

    def _cards(self, items, cat_name):
        cards = []
        seen = set()
        for item in items:
            card = self._card(item, cat_name)
            if not card or card['vod_id'] in seen:
                continue
            seen.add(card['vod_id'])
            cards.append(card)
        return cards

    def listing(self, cat, page):
        try:
            return self._cards(self._items(cat, page), self._cat_name(cat))
        except Exception as exc:
            print('[百度] 列表读取失败: %s' % exc)
            return []

    def search(self, wd, page):
        if (wd or '').strip():
            print('[百度] 接口未提供搜索')
        return []

    def _detail_data(self, coll_id):
        coll_id = str(coll_id or '').strip()
        if not coll_id:
            print('[百度] 无效的剧集 ID')
            return None
        target = self.VIDEO_BASE + '/haokan/ui-video/playlet/rec/detail?' + self.VIDEO_QUERY
        payload = self._post(target, {'playlet_id': coll_id, 'vid': 'undefined'})
        data = as_dict(as_dict(payload).get('data'))
        if not data:
            print('[百度] 详情解析失败')
            return None
        return data

    @staticmethod
    def _str_list(raw):
        out = []
        for item in as_list(raw):
            if isinstance(item, (list, dict)):
                continue
            text = str(item or '').strip()
            if text:
                out.append(text)
        return out

    def detail(self, vid):
        coll_id = str(vid or '').strip()
        if not coll_id:
            return {}
        try:
            data = self._detail_data(coll_id)
            if not data:
                return {}
            vids = self._str_list(data.get('vid_list'))
            if not vids:
                print('[百度] 详情无剧集: %s' % coll_id)
                return {}
            episodes = []
            for index, item in enumerate(vids):
                no = index + 1
                episodes.append({
                    'no': no,
                    'name': '第%d集' % no,
                    'url': '%s://%s' % (self.key, item),
                })
            episodes = sort_episodes(episodes)
            return {
                'vod_id': coll_id,
                'vod_name': clean_text(map_str(data, 'playlet_title'))
                            or ('百度短剧 ' + coll_id),
                'vod_pic': map_str(data, 'playlet_poster'),
                'vod_class': '百度',
                'vod_content': truncate(clean_text(map_str(data, 'description')), 400),
                'vod_remarks': '共%d集' % len(episodes),
                'episodes': episodes,
            }
        except Exception as exc:
            print('[百度] 详情读取失败: %s' % exc)
            return {}

    @staticmethod
    def _best_clarity_url(raw):
        best = ''
        best_quality = -1
        for item in as_list(raw):
            entry = as_dict(item)
            src = map_str(entry, 'url')
            if not src:
                continue
            quality = atoi(map_str(entry, 'video_quality'), 0)
            if quality > best_quality:
                best, best_quality = src, quality
        return best

    def _relate(self, vid):
        vid = str(vid or '').strip()
        if not vid:
            print('[百度] 播放 vid 为空')
            return ''
        target = self.VIDEO_BASE + '/appui/api?cmd=video/relate&' + self.VIDEO_QUERY
        payload = self._post(target, {'method': 'post', 'vid': vid})
        top = as_dict(payload)
        if 'video/relate' not in top:
            print('[百度] 播放接口无 video/relate 字段')
            return ''
        inner = as_dict(as_dict(top.get('video/relate')).get('data'))
        src = self._best_clarity_url(as_dict(inner.get('cur_video')).get('clarityUrl'))
        if not src:
            print('[百度] 剧集无播放地址: %s' % vid)
        return src

    def play(self, url):
        raw = str(url or '').strip()
        prefix = self.key + '://'
        if not raw.startswith(prefix):
            return {'url': raw,
                    'header': {'User-Agent': self.APP_USER_AGENT,
                               'Cookie': self.APP_COOKIE},
                    'parse': 0} if is_http_media(raw) else {}
        vid = raw[len(prefix):].strip()
        if not vid or '/' in vid:
            print('[百度] 播放参数无效')
            return {}
        try:
            src = self._relate(vid)
        except Exception as exc:
            print('[百度] 播放读取失败: %s' % exc)
            return {}
        if not is_http_media(src):
            print('[百度] 剧集无播放地址: %s' % vid)
            return {}
        return {
            'url': src,
            'header': {'User-Agent': self.APP_USER_AGENT,
                       'Cookie': self.APP_COOKIE,
                       'Referer': self.VIDEO_BASE + '/'},
            'parse': 0,
        }

class XingyaSite(object):

    key = 'xingya'
    name = '星芽'
    site = 'https://app.whjzjx.cn'

    LOGIN_URL = 'https://u.shytkjgs.com/user/v1/account/login'
    DEVICE_ID = '24250683a3bdb3f118dff25ba4b1cba1a'
    UUID = 'randomUUID_914e7a9b-deac-4f80-9247-db56669187df'
    DEV_TOKEN = ('Bnf9uRPIcKTIOZasAUgUJAUDXhIOlB4lpDKsyLD4yvKm708G4J8PN1Z-gMaFgPwkRvqJrEje81VRxh41IaSj0lN'
                 'WuoRLUg1r08Z1f0IqSv2q1lvfxrYckKavmnAPYLdkT9P1fPFNRt0RuN3DLLFZqApRDdPD3mM2jB6d79CF2LtM*')
    APP_UA = ('Mozilla/5.0 (Linux; Android 9; RMX1931 Build/PQ3A.190605.05081124; wv) '
              'AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/91.0.4472.114 Mobile Safari/537.36')
    LOGIN_BODY = ('device=' + DEVICE_ID + '&install_first_open=false'
                  '&first_install_time=1723214205125&last_update_time=1723214205125&report_link_url=')

    CATS = [
        {'type_id': 'juchang', 'type_name': '剧场'},
        {'type_id': 'reboduanju', 'type_name': '热播剧'},
        {'type_id': 'huiyuan', 'type_name': '会员专享'},
        {'type_id': 'xingxuan', 'type_name': '星选好剧'},
        {'type_id': 'xinju', 'type_name': '新剧'},
        {'type_id': 'yangguang', 'type_name': '阳光剧场'},
    ]

    CLASS_IDS = {
        'juchang': '1',
        'reboduanju': '2',
        'huiyuan': '8',
        'xingxuan': '7',
        'xinju': '3',
        'yangguang': '5',
    }

    def __init__(self):
        self._auth_headers = None

    def cats(self):
        return [dict(item) for item in self.CATS]

    @staticmethod
    def _login_headers():
        return {
            'User-Agent': 'okhttp/4.10.0',
            'Content-Type': 'application/x-www-form-urlencoded',
            'x-app-id': '7',
            'platform': '1',
            'version_name': '3.3.1',
            'app_version': '3.3.1',
            'device_platform': 'android',
            'device_id': '24250683a3bdb3f118dff25ba4b1cba1a',
            'device_type': 'RMX1931',
            'device_brand': 'realme',
            'os_version': '9',
            'channel': 'default',
            'uuid': 'randomUUID_914e7a9b-deac-4f80-9247-db56669187df',
        }

    def login(self):
        try:
            status, text = http_request(self.LOGIN_URL, 'POST',
                                        headers=self._login_headers(),
                                        data=self.LOGIN_BODY, timeout=20)
        except Exception as exc:
            print('[星芽] 登录失败: %s' % exc)
            return None
        if status >= 400 or not text:
            print('[星芽] 登录 HTTP %s' % status)
            return None
        envelope = json_loads(text)
        if not isinstance(envelope, dict):
            print('[星芽] 登录响应无法解析')
            return None
        token = map_str(as_dict(envelope.get('data')), 'token').strip()
        if not token:
            print('[星芽] 登录失败: 未取到 token')
            return None
        headers = self._login_headers()
        headers['authorization'] = token
        headers['dev_token'] = self.DEV_TOKEN
        headers['user_agent'] = self.APP_UA
        headers['support_h265'] = '1'
        return headers

    def ensure_headers(self):
        if self._auth_headers is not None:
            return self._auth_headers
        headers = self.login()
        if headers is None:
            return None
        self._auth_headers = headers
        return headers

    def _call(self, method, target, json_body=None):
        headers = self.ensure_headers()
        if headers is None:
            return None
        try:
            status, text = http_request(target, method, headers=headers,
                                        json_body=json_body, timeout=20)
        except Exception as exc:
            print('[星芽] 请求失败: %s' % exc)
            return None
        if status >= 400 or not text:
            print('[星芽] 接口 HTTP %s' % status)
            return None
        raw = json_loads(text)
        if raw is None:
            print('[星芽] 接口响应无法解析')
            return None
        return raw

    def _get_data(self, target):
        raw = self._call('GET', target)
        if not isinstance(raw, dict):
            print('[星芽] 接口响应无法解析')
            return None
        data = raw.get('data')
        if data is None or data == 'null' or data == '' or data == {}:
            msg = map_str(raw, 'msg', 'message')
            if msg:
                print('[星芽] 接口返回错误: %s' % msg)
            else:
                print('[星芽] 接口无数据')
            return None
        return data

    @classmethod
    def _class_id(cls, cat):
        return cls.CLASS_IDS.get(str(cat or '').strip(), cls.CLASS_IDS['juchang'])

    @classmethod
    def _cat_name(cls, cat):
        cat = str(cat or '').strip()
        for item in cls.CATS:
            if item['type_id'] == cat:
                return item['type_name']
        return cls.CATS[0]['type_name']

    def _list_items(self, cat, page):
        if page < 1:
            page = 1
        target = ('%s/cloud/v2/theater/home_page?theater_class_id=%s&type=1'
                  '&class2_ids=0&page_num=%d&page_size=24'
                  % (self.site, self._class_id(cat), page))
        data = self._get_data(target)
        if data is None:
            return []
        items = []
        for entry in as_list(as_dict(data).get('list')):
            entry = as_dict(entry)
            theater = as_dict(entry.get('theater'))
            if theater:
                items.append(theater)
        return items

    def _card(self, item, cat_name):
        item = as_dict(item)
        vid = map_str(item, 'id')
        title = clean_text(map_str(item, 'title'))
        if not vid or not title:
            return None
        remarks = ''
        total = atoi(map_str(item, 'total'), 0)
        if total > 0:
            remarks = '共%d集' % total
        return {
            'vod_id': vid,
            'vod_name': title,
            'vod_pic': map_str(item, 'cover_url'),
            'vod_remarks': remarks,
            'vod_class': cat_name,
            'vod_content': '',
        }

    @staticmethod
    def _cards(items, cat_name, build):
        cards = []
        seen = set()
        for item in items:
            card = build(item, cat_name)
            if not card or card['vod_id'] in seen:
                continue
            seen.add(card['vod_id'])
            cards.append(card)
        return cards

    def listing(self, cat, page):
        try:
            page = page if page and page > 0 else 1
            items = self._list_items(cat, page)
            if page > 1 and not items:
                return []
            return self._cards(items, self._cat_name(cat), self._card)
        except Exception as exc:
            print('[星芽] 列表读取失败: %s' % exc)
            return []

    def search(self, wd, page):
        wd = str(wd or '').strip()
        if not wd:
            return []
        try:
            headers = self.ensure_headers()
            if headers is None:
                return []
            search_headers = dict(headers)
            search_headers['Content-Type'] = 'application/json'
            target = self.site + '/v3/search'
            try:
                status, text = http_request(target, 'POST', headers=search_headers,
                                            json_body={'text': wd}, timeout=20)
            except Exception as exc:
                print('[星芽] 搜索请求失败: %s' % exc)
                return []
            if status >= 400 or not text:
                print('[星芽] 搜索 HTTP %s' % status)
                return []
            raw = json_loads(text)
            if not isinstance(raw, dict):
                print('[星芽] 搜索解析失败')
                return []
            data = as_dict(raw.get('data'))
            theater = as_dict(data.get('theater'))
            items = as_list(theater.get('search_data'))
            return self._cards(items, '搜索', self._card)
        except Exception as exc:
            print('[星芽] 搜索失败: %s' % exc)
            return []

    def _detail(self, vid):
        vid = str(vid or '').strip()
        if not vid:
            print('[星芽] 无效的星芽剧集 ID')
            return None
        target = (self.site + '/v2/theater_parent/detail?theater_parent_id='
                  + quote(vid, safe=''))
        data = self._get_data(target)
        if data is None:
            return None
        node = as_dict(data)
        if not node:
            print('[星芽] 详情解析失败')
            return None
        return node

    @staticmethod
    def _ep_no(entry, index):
        order = atoi(map_str(entry, 'num'), 0)
        if order < 1:
            order = index + 1
        return order

    def detail(self, vid):
        vid = str(vid or '').strip()
        if not vid:
            return {}
        try:
            data = self._detail(vid)
            if not data:
                return {}
            theaters = as_list(data.get('theaters'))
            if not theaters:
                print('[星芽] 详情无剧集: %s' % vid)
                return {}
            episodes = []
            seen = set()
            drama_title = clean_text(map_str(data, 'title'))
            for index, raw in enumerate(theaters):
                entry = as_dict(raw)
                if not entry:
                    continue
                order = self._ep_no(entry, index)
                if order in seen:
                    continue
                seen.add(order)
                son = clean_text(map_str(entry, 'son_title'))
                if son and son != drama_title:
                    name = '第%d集 %s' % (order, son)
                else:
                    name = '第%d集' % order
                episodes.append({
                    'no': order,
                    'name': name,
                    'url': '%s://%s/%d' % (self.key, vid, order),
                })
            episodes = sort_episodes(episodes)
            if not episodes:
                print('[星芽] 详情无剧集: %s' % vid)
                return {}
            title = clean_text(map_str(data, 'title'))
            return {
                'vod_id': vid,
                'vod_name': title or ('星芽短剧 ' + vid),
                'vod_pic': map_str(data, 'cover_url'),
                'vod_content': truncate(clean_text(map_str(data, 'introduction')), 400),
                'vod_class': '星芽',
                'vod_remarks': '共%d集' % len(episodes),
                'episodes': episodes,
            }
        except Exception as exc:
            print('[星芽] 详情读取失败: %s' % exc)
            return {}

    def play(self, url):
        raw = str(url or '')
        prefix = self.key + '://'
        if not raw.startswith(prefix):
            if is_http_media(raw):
                return {'url': raw, 'header': {'User-Agent': 'okhttp/4.10.0'}, 'parse': 0}
            return {}
        rest = raw[len(prefix):]
        if '/' not in rest:
            print('[星芽] 播放参数无效')
            return {}
        vid, extra = rest.split('/', 1)
        vid = vid.strip()
        extra = extra.strip()
        if not vid or not extra:
            print('[星芽] 播放参数无效')
            return {}
        seq = atoi(extra, 0)
        if seq < 1:
            print('[星芽] 播放集号无效')
            return {}
        try:
            data = self._detail(vid)
        except Exception as exc:
            print('[星芽] 播放读取失败: %s' % exc)
            return {}
        if not data:
            return {}
        for raw_ep in as_list(data.get('theaters')):
            entry = as_dict(raw_ep)
            if not entry:
                continue
            if atoi(map_str(entry, 'num'), 0) != seq:
                continue
            src = map_str(entry, 'son_video_url')
            if not is_http_media(src):
                print('[星芽] 第%d集暂无播放地址' % seq)
                return {}
            return {
                'url': src,
                'header': {'Referer': self.site + '/', 'User-Agent': UA_DESKTOP},
                'parse': 0,
            }
        print('[星芽] 详情缺第%d集' % seq)
        return {}

class XifanSite(object):

    key = 'xifan'
    name = '西饭'
    site = 'https://xifan-api-cn.youlishipin.com'

    CATS = [
        {'type_id': 'all', 'type_name': '全部', 'area': '68@都市'},
        {'type_id': 'dushi', 'type_name': '都市', 'area': '68@都市'},
        {'type_id': 'qingchun', 'type_name': '青春', 'area': '68@青春'},
        {'type_id': 'xiandaiyanqing', 'type_name': '现代言情', 'area': '81@现代言情'},
        {'type_id': 'haomen', 'type_name': '豪门', 'area': '81@豪门'},
        {'type_id': 'danvzhu', 'type_name': '大女主', 'area': '80@大女主'},
        {'type_id': 'nixi', 'type_name': '逆袭', 'area': '79@逆袭'},
        {'type_id': 'dalianuecha', 'type_name': '打脸虐渣', 'area': '79@打脸虐渣'},
        {'type_id': 'chuanyue', 'type_name': '穿越', 'area': '81@穿越'},
    ]

    def cats(self):
        return [{'type_id': item['type_id'], 'type_name': item['type_name']}
                for item in self.CATS]

    @staticmethod
    def _headers():
        return {
            'Accept': 'application/json, text/plain, */*',
            'User-Agent': 'okhttp/4.10.0',
        }

    def _call(self, path):
        target = self.site + path
        try:
            status, text = http_get(target, headers=self._headers(), timeout=20)
        except Exception as exc:
            print('[西饭] 请求失败: %s' % exc)
            return None
        if status >= 400 or not text:
            print('[西饭] 接口 HTTP %s' % status)
            return None
        envelope = json_loads(text)
        if not isinstance(envelope, dict):
            print('[西饭] 接口响应无法解析')
            return None
        result = envelope.get('result')
        if result is None or result == 'null' or result == '' or result == {} or result == []:
            msg = map_str(envelope, 'message', 'msg')
            if msg:
                print('[西饭] 接口返回错误: %s' % msg)
            else:
                print('[西饭] 接口无数据')
            return None
        return result

    @classmethod
    def _area(cls, cat):
        hit = cls.CATS[0]
        for item in cls.CATS:
            if item['type_id'] == str(cat or '').strip():
                hit = item
                break
        parts = hit['area'].split('@', 1)
        cat_id = parts[0]
        cat_name = hit['type_name']
        if len(parts) > 1:
            cat_name = parts[1]
        return cat_id, cat_name, hit['type_name']

    def _card(self, dj, cat_name):
        dj = as_dict(dj)
        vid = map_str(dj, 'duanjuId', 'duanjuID')
        title = clean_text(map_str(dj, 'title'))
        if not vid or not title:
            return None
        source = map_str(dj, 'source')
        if source:
            vid = vid + '@' + source
        remarks = ''
        total = atoi(map_str(dj, 'total'), 0)
        if total > 0:
            remarks = '共%d集' % total
        return {
            'vod_id': vid,
            'vod_name': title,
            'vod_pic': map_str(dj, 'coverImageUrl'),
            'vod_remarks': remarks,
            'vod_class': cat_name,
            'vod_content': '',
        }

    def _list_items(self, cat, page):
        if page < 1:
            page = 1
        cat_id, cat_name, _display = self._area(cat)
        path = ('/xifan/drama/portalPage?reqType=aggregationPage&offset=%d'
                '&categoryId=%s&categoryNames=%s&pageID=page_theater&appId=drama'
                % ((page - 1) * 30, quote(cat_id, safe=''), quote(cat_name, safe='')))
        result = self._call(path)
        if result is None:
            return []
        items = []
        for element in as_list(as_dict(result).get('elements')):
            for content in as_list(as_dict(element).get('contents')):
                dj = as_dict(as_dict(content).get('duanjuVo'))
                if dj:
                    items.append(dj)
        return items

    @staticmethod
    def _cards(items, cat_name, build):
        cards = []
        seen = set()
        for item in items:
            card = build(item, cat_name)
            if not card or card['vod_id'] in seen:
                continue
            seen.add(card['vod_id'])
            cards.append(card)
        return cards

    def listing(self, cat, page):
        try:
            _cat_id, _cat_name, display = self._area(cat)
            page = page if page and page > 0 else 1
            items = self._list_items(cat, page)
            if page > 1 and not items:
                return []
            return self._cards(items, display, self._card)
        except Exception as exc:
            print('[西饭] 列表读取失败: %s' % exc)
            return []

    def search(self, wd, page):
        wd = str(wd or '').strip()
        if not wd:
            return []
        try:
            page = page if page and page > 0 else 1
            request_id = '%daa498144140ef297' % int(time.time() * 1000)
            path = ('/xifan/search/getSearchList?reqType=search&offset=%d&keyword=%s'
                    '&requestId=%s&appId=drama'
                    % ((page - 1) * 30, quote(wd, safe=''), request_id))
            result = self._call(path)
            if result is None:
                return []
            items = []
            for element in as_list(as_dict(result).get('elements')):
                dj = as_dict(as_dict(element).get('duanjuVo'))
                if dj:
                    items.append(dj)
            return self._cards(items, '西饭', self._card)
        except Exception as exc:
            print('[西饭] 搜索失败: %s' % exc)
            return []

    def _detail(self, duanju_id, source):
        path = ('/xifan/drama/getDuanjuInfo?duanjuId=%s&source=%s&appId=drama'
                % (quote(str(duanju_id or ''), safe=''),
                   quote(str(source or ''), safe='')))
        result = self._call(path)
        if result is None:
            return None
        node = as_dict(result)
        if not node:
            print('[西饭] 详情解析失败')
            return None
        return node

    @staticmethod
    def _split_id(vid):
        parts = str(vid or '').split('@', 1)
        if len(parts) < 2:
            return parts[0], ''
        return parts[0], parts[1]

    @staticmethod
    def _ep_order(ep, index):
        order = atoi(map_str(ep, 'index'), 0)
        if order < 1:
            order = index + 1
        return order

    def detail(self, vid):
        vid = str(vid or '').strip()
        if not vid:
            return {}
        try:
            duanju_id, source = self._split_id(vid)
            node = self._detail(duanju_id, source)
            if not node:
                return {}
            episodes = []
            seen = set()
            for index, raw in enumerate(as_list(node.get('episodeList'))):
                ep = as_dict(raw)
                if not ep:
                    continue
                order = self._ep_order(ep, index)
                if order in seen:
                    continue
                seen.add(order)
                episodes.append({
                    'no': order,
                    'name': '第%d集' % order,
                    'url': '%s://%s/%d' % (self.key, vid, order),
                })
            episodes = sort_episodes(episodes)
            if not episodes:
                print('[西饭] 详情无剧集: %s' % vid)
                return {}
            title = clean_text(map_str(node, 'title'))
            return {
                'vod_id': vid,
                'vod_name': title or ('西饭短剧 ' + vid),
                'vod_pic': map_str(node, 'coverImageUrl'),
                'vod_content': truncate(clean_text(
                    map_str(node, 'desc', 'description')), 400),
                'vod_class': '西饭',
                'vod_remarks': '共%d集' % len(episodes),
                'episodes': episodes,
            }
        except Exception as exc:
            print('[西饭] 详情读取失败: %s' % exc)
            return {}

    def play(self, url):
        raw = str(url or '')
        prefix = self.key + '://'
        if not raw.startswith(prefix):
            if is_http_media(raw):
                return {'url': raw, 'header': {'User-Agent': 'okhttp/4.10.0'}, 'parse': 0}
            return {}
        rest = raw[len(prefix):]
        if '/' not in rest:
            print('[西饭] 播放参数无效')
            return {}
        vid, extra = rest.split('/', 1)
        vid = vid.strip()
        extra = extra.strip()
        if not vid or not extra:
            print('[西饭] 播放参数无效')
            return {}
        seq = atoi(extra, 1)
        if seq < 1:
            seq = 1
        duanju_id, source = self._split_id(vid)
        try:
            node = self._detail(duanju_id, source)
        except Exception as exc:
            print('[西饭] 播放读取失败: %s' % exc)
            return {}
        if not node:
            return {}
        for index, raw_ep in enumerate(as_list(node.get('episodeList'))):
            ep = as_dict(raw_ep)
            if not ep:
                continue
            if self._ep_order(ep, index) != seq:
                continue
            src = map_str(ep, 'playUrl')
            if not is_http_media(src):
                print('[西饭] 第%d集暂无播放地址' % seq)
                return {}
            return {
                'url': src,
                'header': {'Referer': self.site + '/', 'User-Agent': UA_DESKTOP},
                'parse': 0,
            }
        print('[西饭] 详情缺第%d集' % seq)
        return {}

class XingxingSite(object):

    key = 'xingxing'
    name = '星星'
    site = 'http://read.api.duodutek.com'

    COMMON_QUERY = ('productId=2a8c14d1-72e7-498b-af23-381028eb47c0'
                    '&vestId=2be070e0-c824-4d0e-a67a-8f688890cadb'
                    '&channel=oppo19'
                    '&osType=android'
                    '&version=20'
                    '&token=202509271001001446030204698626')

    CATS = [
        ('jingxuan', '精选', '1287'),
        ('remen', '热门', '1288'),
        ('xinju', '新剧', '1289'),
    ]

    UA = ('Mozilla/5.0 (Windows NT 6.1; WOW64) AppleWebKit/537.36 '
          '(KHTML, like Gecko) Chrome/50.0.2661.87 Safari/537.36')

    def __init__(self):
        self._chapter_cache = {}

    def cats(self):
        return [{'type_id': item[0], 'type_name': item[1]} for item in self.CATS]

    @classmethod
    def _resource(cls, cat):
        hit = cls.CATS[0]
        for item in cls.CATS:
            if item[0] == cat:
                hit = item
                break
        return hit[2], hit[1]

    def _headers(self):
        return {
            'Accept': 'application/json, text/plain, */*',
            'User-Agent': self.UA,
        }

    def _call(self, path):
        try:
            status, text = http_get(self.site + path, headers=self._headers(),
                                    timeout=20)
        except Exception as exc:
            print('[星星] 请求失败: %s' % exc)
            return None
        if status >= 400:
            print('[星星] 接口 HTTP %s' % status)
            return None
        if not text or not text.strip():
            print('[星星] 接口空响应')
            return None
        parsed = json_loads(text)
        if not isinstance(parsed, dict):
            print('[星星] 接口响应无法解析')
            return None
        return parsed

    def _card(self, item, cat_name):
        item = as_dict(item)
        vid = map_str(item, 'id')
        title = clean_text(map_str(item, 'name'))
        if not vid or not title:
            return None
        intro = clean_text(map_str(item, 'introduction'))
        return {
            'vod_id': '%s@%s@%s' % (vid, quote(title, safe=''),
                                    quote(intro, safe='')),
            'vod_name': title,
            'vod_pic': map_str(item, 'icon'),
            'vod_remarks': (map_str(item, 'heat') or '0') + '万播放',
            'vod_class': cat_name,
            'vod_content': truncate(intro, 400),
        }

    def _cards(self, items, cat_name):
        cards = []
        seen = set()
        for item in items:
            card = self._card(item, cat_name)
            if not card or card['vod_id'] in seen:
                continue
            seen.add(card['vod_id'])
            cards.append(card)
        return cards

    def listing(self, cat, page):
        try:
            page = page if page and page > 0 else 1
            resource_id, display = self._resource(cat)
            path = ('/novel-api/app/pageModel/getResourceById?%s'
                    '&resourceId=%s&pageNum=%d&pageSize=20'
                    % (self.COMMON_QUERY, quote(resource_id, safe=''), page))
            envelope = self._call(path)
            if envelope is None:
                return []
            items = as_list(as_dict(envelope.get('data')).get('datalist'))
            if page > 1 and not items:
                return []
            cards = self._cards(items, display)
            if not cards:
                print('[星星] 列表无内容')
                return []
            return cards
        except Exception as exc:
            print('[星星] 列表读取失败: %s' % exc)
            return []

    def search(self, wd, page):
        return []

    def _chapters(self, book_id):
        cached = self._chapter_cache.get(book_id)
        if cached and time.time() - cached[0] < 300:
            return cached[1]
        path = ('/novel-api/basedata/book/getChapterList?%s&bookId=%s'
                % (self.COMMON_QUERY, quote(str(book_id), safe='')))
        envelope = self._call(path)
        items = as_list(envelope.get('data')) if envelope else []
        if not items:
            print('[星星] 详情无剧集: %s' % book_id)
        self._chapter_cache[book_id] = (time.time(), items)
        return items

    @staticmethod
    def _short_play_url(item):
        item = as_dict(item)
        groups = as_list(item.get('shortPlayList'))
        if not groups:
            return ''
        group = as_dict(groups[0])
        chapters = as_list(group.get('chapterShortPlayVoList'))
        if not chapters:
            return ''
        return map_str(as_dict(chapters[0]), 'shortPlayUrl')

    def detail(self, vid):
        vid = str(vid or '').strip()
        if not vid:
            return {}
        try:
            parts = vid.split('@')
            book_id = parts[0]
            title = ''
            if len(parts) > 1:
                title = clean_text(unquote(parts[1].replace('+', ' ')))
            desc = ''
            if len(parts) > 2:
                desc = clean_text(unquote(parts[2].replace('+', ' ')))
            if not book_id:
                return {}
            items = self._chapters(book_id)
            if not items:
                return {}
            episodes = []
            for index, item in enumerate(items):
                seq = index + 1
                if not self._short_play_url(item):
                    continue
                episodes.append({
                    'no': seq,
                    'name': '第%d集' % seq,
                    'url': '%s://%s/%d' % (self.key, book_id, seq),
                })
            episodes = sort_episodes(episodes)
            if not episodes:
                print('[星星] 详情无剧集: %s' % book_id)
                return {}
            return {
                'vod_id': vid,
                'vod_name': title or ('星星短剧 ' + book_id),
                'vod_pic': '',
                'vod_class': '星星',
                'vod_content': truncate(desc, 400),
                'vod_remarks': '共%d集' % len(episodes),
                'episodes': episodes,
            }
        except Exception as exc:
            print('[星星] 详情读取失败: %s' % exc)
            return {}

    def play(self, url):
        raw = str(url or '')
        prefix = self.key + '://'
        if not raw.startswith(prefix):
            return {'url': raw, 'header': {'User-Agent': self.UA},
                    'parse': 0} if is_http_media(raw) else {}
        rest = raw[len(prefix):]
        if '/' not in rest:
            print('[星星] 播放参数无效')
            return {}
        book_id, extra = rest.split('/', 1)
        if not book_id or not extra:
            print('[星星] 播放参数无效')
            return {}
        seq = atoi(extra, 1)
        if seq < 1:
            seq = 1
        try:
            items = self._chapters(book_id)
        except Exception as exc:
            print('[星星] 播放读取失败: %s' % exc)
            return {}
        if seq > len(items):
            print('[星星] 详情缺第%d集' % seq)
            return {}
        src = self._short_play_url(items[seq - 1])
        if not is_http_media(src):
            print('[星星] 第%d集暂无播放地址' % seq)
            return {}
        return {
            'url': src,
            'header': {'User-Agent': self.UA, 'Referer': self.site + '/'},
            'parse': 0,
        }

class YimiSite(object):

    key = 'yimi'
    name = '薏米'
    site = 'https://yimi-api.zhangyue.com'

    COMMON_QUERY = ('p1=1750574688516674369&p16=22081212C&p2=341201&p21=10&p22=15&p24=0&p25=21200'
                    '&p28=cca83346da195d11&p29=zy9351ae&p3=102120009&p31=29d1af74b128f29f'
                    '&p33=com.zhangyue.app.shortplay'
                    '&p34=force_fsg_nav_bar'
                    '&p35=BUZGFVakskazFWG2XwZ/LNs4fOnQczc4iivy1qLFvZqmerp2Abe2hv5Tu1jOHQJO5PGANizg3JbzgaTOon0qkmQ=='
                    '&p4=501609&p5=16&p7=cca83346da195d11&p9=3')

    LIST_SEC = 'AAF4IWZnITkqeX4hJCB5eio4IWc4IH4='
    DETAIL_SEC = 'AAFzKmZkKjIqenUqJCNycSo7Kmw4I3U='

    UA = 'Dalvik/2.1.0 (Linux; U; Android 15; 22081212C Build/AQ3A.241006.001)'

    CATS = [
        ('jingxuan', '精选', 'channel_c6f50cd9'),
        ('nixi', '逆袭', 'channel_a8e10abc'),
        ('fuchou', '复仇', 'channel_d26dd434'),
        ('lianai', '恋爱', 'channel_75afe84a'),
        ('chongsheng', '重生', 'channel_2272aac5'),
        ('gufeng', '古风', 'channel_73190d4f'),
        ('shenyi', '神医', 'channel_2d7eae6b'),
        ('yanqing', '言情', 'channel_614820bd'),
        ('dushi', '都市', 'channel_13dfce8b'),
        ('xuanyi', '悬疑', 'channel_861b9642'),
        ('lishi', '历史', 'channel_18157927'),
    ]

    LIST_PATH = '/bookstore/local/visual/channel/list'
    DETAIL_PATH = '/video/client/short_play/episode_list'
    USR = 'usr=tj1290623468&zyeid=4fc4c6737a87b603e1b8ce9210032bae'

    def __init__(self):
        self._detail_cache = {}

    def cats(self):
        return [{'type_id': item[0], 'type_name': item[1]} for item in self.CATS]

    @classmethod
    def _channel(cls, cat):
        hit = cls.CATS[0]
        for item in cls.CATS:
            if item[0] == cat:
                hit = item
                break
        return hit[2], hit[1]

    def _headers(self, query, path, sec):
        ts = int(time.time() * 1000)
        message = '&' + query + '&' + path + '&' + str(ts) + '&' + sec
        try:
            sign = rsa_sign_pkcs1_sha256(YIMI_PRIVATE_KEY_PEM, message)
        except Exception as exc:
            print('[薏米] 签名失败: %s' % exc)
            return None
        return {
            'x-appid': 'zy9351ae',
            'x-sig-timestamp': str(ts),
            'x-sig-alg': 'RSA-SHA256',
            'x-sig-sign': sign,
            'x-sig-ver': 'v1.1',
            'x-sig-sec': sec,
            'User-Agent': self.UA,
        }

    def _call(self, path, query, sec):
        headers = self._headers(query, path, sec)
        if headers is None:
            return None
        try:
            status, text = http_get(self.site + path + '?' + query,
                                    headers=headers, timeout=20)
        except Exception as exc:
            print('[薏米] 请求失败: %s' % exc)
            return None
        if status >= 400:
            print('[薏米] 接口 HTTP %s' % status)
            return None
        envelope = json_loads(text)
        if not isinstance(envelope, dict):
            print('[薏米] 接口响应无法解析')
            return None
        body = envelope.get('body')
        if body is None or body == 'null':
            print('[薏米] 接口无数据')
            return None
        return body

    @staticmethod
    def _card(item, cat_name):
        item = as_dict(item)
        vid = map_str(item, 'id')
        title = clean_text(map_str(item, 'short_play_name'))
        if not vid or not title:
            return None
        card = {
            'vod_id': vid,
            'vod_name': title,
            'vod_pic': map_str(item, 'cover_url'),
            'vod_remarks': '',
            'vod_class': cat_name,
            'vod_content': '',
        }
        favor = map_str(item, 'favor_count_format')
        if favor:
            card['vod_remarks'] = '热度值:' + favor
        return card

    def _cards(self, items, cat_name):
        cards = []
        seen = set()
        for item in items:
            card = self._card(item, cat_name)
            if not card or card['vod_id'] in seen:
                continue
            seen.add(card['vod_id'])
            cards.append(card)
        return cards

    def listing(self, cat, page):
        try:
            page = page if page and page > 0 else 1
            channel, display = self._channel(cat)
            query = 'key=%s&%s&page=%d&pc=10&%s' % (
                quote(channel, safe=''), self.COMMON_QUERY, page, self.USR)
            body = self._call(self.LIST_PATH, query, self.LIST_SEC)
            if body is None:
                return []
            node = as_dict(body)
            groups = as_list(node.get('list'))
            items = as_list(as_dict(groups[0]).get('short_plays')) if groups else []
            if page > 1 and not items:
                return []
            cards = self._cards(items, display)
            if not cards:
                print('[薏米] 列表无内容')
                return []
            return cards
        except Exception as exc:
            print('[薏米] 列表读取失败: %s' % exc)
            return []

    def search(self, wd, page):
        return []

    def _fetch_all(self, play_id):
        cached = self._detail_cache.get(play_id)
        if cached and time.time() - cached[0] < 300:
            return cached[1]
        name = ''
        intro = ''
        episodes = []
        start = 1
        total = 999999
        page_size = 30
        while start <= total:
            end = start + page_size - 1
            query = 'end_id=%d&%s&pc=10&play_id=%s&start_id=%d&%s' % (
                end, self.COMMON_QUERY, quote(str(play_id), safe=''),
                start, self.USR)
            body = self._call(self.DETAIL_PATH, query, self.DETAIL_SEC)
            if body is None:
                break
            node = as_dict(body)
            if start == 1:
                name = map_str(node, 'name')
                intro = map_str(node, 'introduce')
                count = atoi(map_str(node, 'target_count'), 0)
                if count > 0:
                    total = count
            batch = as_list(node.get('episode_list'))
            if not batch:
                break
            episodes.extend(batch)
            start += page_size
        result = (name, intro, episodes)
        self._detail_cache[play_id] = (time.time(), result)
        return result

    @staticmethod
    def _order(ep, index):
        ep = as_dict(ep)
        order = atoi(map_str(ep, 'order'), 0)
        if order > 0:
            return order
        return index + 1

    @staticmethod
    def _fix_play_url(raw):
        raw = (raw or '').strip()
        if not raw:
            return ''
        if 'zhangyuecdn' in raw:
            parts = raw.split('com', 1)
            if len(parts) == 2:
                return 'https://mother-t.d.ireader.com' + parts[1]
        return raw

    def detail(self, vid):
        vid = str(vid or '').strip()
        if not vid:
            return {}
        try:
            name, intro, episodes = self._fetch_all(vid)
            if not episodes:
                print('[薏米] 详情无剧集: %s' % vid)
                return {}
            result_eps = []
            seen = set()
            for index, ep in enumerate(episodes):
                order = self._order(ep, index)
                if order in seen:
                    continue
                seen.add(order)
                result_eps.append({
                    'no': order,
                    'name': '第%d集' % order,
                    'url': '%s://%s/%d' % (self.key, vid, order),
                })
            result_eps = sort_episodes(result_eps)
            if not result_eps:
                return {}
            return {
                'vod_id': vid,
                'vod_name': clean_text(name) or ('薏米短剧 ' + vid),
                'vod_pic': '',
                'vod_class': '薏米',
                'vod_content': truncate(clean_text(intro), 400),
                'vod_remarks': '共%d集' % len(result_eps),
                'episodes': result_eps,
            }
        except Exception as exc:
            print('[薏米] 详情读取失败: %s' % exc)
            return {}

    def play(self, url):
        raw = str(url or '')
        prefix = self.key + '://'
        if not raw.startswith(prefix):
            return {'url': raw, 'header': {'User-Agent': self.UA},
                    'parse': 0} if is_http_media(raw) else {}
        rest = raw[len(prefix):]
        if '/' not in rest:
            print('[薏米] 播放参数无效')
            return {}
        play_id, extra = rest.split('/', 1)
        if not play_id or not extra:
            print('[薏米] 播放参数无效')
            return {}
        order = atoi(extra, 1)
        if order < 1:
            order = 1
        try:
            _, _, episodes = self._fetch_all(play_id)
        except Exception as exc:
            print('[薏米] 播放读取失败: %s' % exc)
            return {}
        for index, ep in enumerate(episodes):
            if self._order(ep, index) != order:
                continue
            src = self._fix_play_url(map_str(as_dict(ep), 'play_url', 'playUrl'))
            if not is_http_media(src):
                print('[薏米] 第%d集暂无播放地址' % order)
                return {}
            return {
                'url': src,
                'header': {'User-Agent': self.UA, 'Referer': self.site + '/'},
                'parse': 0,
            }
        print('[薏米] 详情缺第%d集' % order)
        return {}

YIMI_PRIVATE_KEY_PEM = '''-----BEGIN PRIVATE KEY-----
MIIEvgIBADANBgkqhkiG9w0BAQEFAASCBKgwggSkAgEAAoIBAQDwCPsMptVn80Im4VVfJ2uAkjs7NpJzzsyGxleK1uN9ux/KTiY2o8kiXcRIcAYVChfdX4ywUs0jrjh8iTcC91r6qgeBaDS8wWsL5bZrn7O/8sqq2hbizV4AvvsqhxVJzRUJZjbNOcMZOPJoeL5K4U4YsiOyV8a9lt5C6zEC4Qy0xjscvOTGyVTqtWeJedEedXtiKLQxAiy6OKJxyHQqdwMHUfAgAbLzAHcVpg1RSXwud+5vtTJNXOXT98FHoFDcRIEcHiiqfU9dskzAhG2nPbFujO+YFq9tZBrWmrhPaXcHfXZqtEYePM4vuvMYjhmANdG4Ehl6pN9nuEaLZ+L35/3nAgMBAAECggEBAOe+M+s2E2ll8WMqQEs6+s5J4Ee9201Vxh8E1TYlW8Ni60FdjAVKwgCc+Mla5nRfp0TCYElH1+hv5vdNXsBNYhgKGm701Z27O4dkA2gK6vcSCFtFbb0Qu4YK3OFlQ8dZ6cqGVbhz4Qmz8k2s7UPMHKM5Mb+YgTc/tlxzR4FZF/RaY8MDpv6iMcXPY27xJzZAV1jROCXjZTZNYbgjsKDAbthRDkjMyuKCIdq7rAHrEyFSx3n7/uxnZYh42bXzWyyWudbkAJoq1ZYx+NyYj5TsN/WNoYbCPcX0Ko+CNnpkC/6qtQbHrBiMprnld67qdLCVhWpmOBYukXVPwFMJjOFlYKkCgYEA+WDh3LpitYSO6hn7mhnbqEQba13cutbJW9RQa0BjGf1OGXdqXpimcWK7viZYAKhLlyGWQmoWduDq4bjSRx7ZxY8pMtpIUWoVkKItD7D2yvmYN1guHNRpHlUIAsSH3HGwQIeXy36hJcB5gC+3XgRVPz/juTMWJDC0usECNFz17bsCgYEA9miWi6JPnZ1ffQzAyE+P6vGC/Vrl7Uyr9gqxI/OkZa8bUqfZtGo5UDGSaGRUsoTsYiEJ8m5blPY0X4xr5x1kO6rfk0gHxn1OXlCP42yT2+CqQvjNO2DHOnWNryKjmAqaAITbmC2lgj0PiiPO32ZT3aXTOgwxTKbFP3LBDmwA18UCgYAQuEgsbmqz1OFoHLnbySQLEhXsiuyDsmbpu0BxEG4UjgEwf+sn0IBIVeBUjWmVEbOPvHbAmTBMZCQbYjLnBdCACGswt6Xln4E2o0j2Jl1Fmpp0C3t7/1nU6MqStO6O/yhcCztIL4NKbq82wvw+V3gHt5bjEePIJWPYqZwmOp1ahQKBgCqfHKs6gBr7RbETq6T6XiJ9c/Lu7iaFxJjicJGPazhLeaZqcjXKye8dI/36nMvkQh8XJ+lPPXgeviBo4aEwbE4F2HZZVz72HcAin0DvXwQBcHH1J0rGCrAJ9V/91d5OtySv1mwUOTS16yIx3260/HyyWj8ILN7dWfEHoG0mMV8hAoGBAI0KBzR9WfzxNKI4ZqRD9/sN+SH4oxymo2oJ+FnOW7hk1E0EyrsGzIDCrS/f7MPzLJI7F4DULsrU5RIQyouIybZra29Vqe4L8kdIae5O0R4Y2r7gt/yWo4cWnW53Q0f5o7mzV10Dc8ewuT1DyJrt25dMWsGsP8rQ/0pVUBLEf93b
-----END PRIVATE KEY-----'''

class QimaoSite(object):

    key = 'qimao'
    name = '七猫'
    site = 'https://api-store.qmplaylet.com'

    READ_BASE = 'https://api-read.qmplaylet.com'
    KEYS = 'd3dGiJc651gSQ8w1'
    SEARCH_TRACK_ID = 'ec1280db127955061754851657967'

    CHAR_MAP = {
        '+': 'P', '/': 'X', '0': 'M', '1': 'U', '2': 'l', '3': 'E', '4': 'r',
        '5': 'Y', '6': 'W', '7': 'b', '8': 'd', '9': 'J',
        'A': '9', 'B': 's', 'C': 'a', 'D': 'I', 'E': '0', 'F': 'o', 'G': 'y',
        'H': '_', 'I': 'H', 'J': 'G', 'K': 'i', 'L': 't',
        'M': 'g', 'N': 'N', 'O': 'A', 'P': '8', 'Q': 'F', 'R': 'k', 'S': '3',
        'T': 'h', 'U': 'f', 'V': 'R', 'W': 'q', 'X': 'C',
        'Y': '4', 'Z': 'p', 'a': 'm', 'b': 'B', 'c': 'O', 'd': 'u', 'e': 'c',
        'f': '6', 'g': 'K', 'h': 'x', 'i': '5', 'j': 'T',
        'k': '-', 'l': '2', 'm': 'z', 'n': 'S', 'o': 'Z', 'p': '1', 'q': 'V',
        'r': 'v', 's': 'j', 't': 'Q', 'u': '7', 'v': 'D',
        'w': 'w', 'x': 'n', 'y': 'L', 'z': 'e',
    }

    CAT_LIST = [
        ('0', '推荐'), ('-1', '新剧'), ('1273', '都市情感'), ('1272', '古装'),
        ('571', '都市'), ('1286', '玄幻仙侠'), ('570', '奇幻'), ('590', '乡村'),
        ('573', '民国'), ('572', '年代'), ('1288', '青春校园'), ('371', '武侠'),
        ('594', '科幻'), ('556', '末世'), ('1289', '二次元'), ('400', '逆袭'),
        ('373', '穿越'), ('795', '复仇'), ('787', '系统'), ('790', '权谋'),
        ('784', '重生'), ('1294', '女性成长'), ('716', '打脸虐渣'), ('480', '闪婚'),
        ('402', '强者回归'), ('715', '追妻火葬场'), ('670', '家庭'), ('558', '马甲'),
        ('724', '职场'), ('343', '宫斗'), ('1299', '高手下山'), ('1295', '娱乐明星'),
        ('727', '异能'), ('342', '宅斗'), ('712', '替身'), ('338', '穿书'),
        ('723', '商战'), ('1291', '种田经商'), ('1293', '伦理'), ('1290', '社会话题'),
        ('492', '致富'), ('1258', '偷听心声'), ('526', '脑洞'), ('624', '豪门总裁'),
        ('356', '萌宝'), ('527', '战神'), ('812', '真假千金'), ('36', '赘婿'),
        ('1269', '神医'), ('37', '神豪'), ('1296', '小人物'), ('545', '团宠'),
        ('464', '欢喜冤家'), ('617', '女帝'), ('1297', '银发'), ('28', '兵王'),
        ('16', '虐恋'), ('21', '甜宠'), ('27', '悬疑'), ('793', '搞笑'),
        ('1287', '灵异'),
    ]

    def __init__(self):
        self._char_rev = {}
        for src, dst in self.CHAR_MAP.items():
            self._char_rev[dst] = src

    def cats(self):
        return [{'type_id': item[0], 'type_name': item[1]} for item in self.CAT_LIST]

    def _tag(self, cat):
        cat = str(cat or '').strip()
        if not cat:
            return self.CAT_LIST[0][0]
        for item in self.CAT_LIST:
            if item[0] == cat:
                return item[0]
        return self.CAT_LIST[0][0]

    @classmethod
    def _obfuscate(cls, text):
        out = []
        for ch in text:
            out.append(cls.CHAR_MAP.get(ch, ch))
        return ''.join(out)

    def _deobfuscate(self, text):
        out = []
        for ch in str(text or ''):
            out.append(self._char_rev.get(ch, ch))
        raw = b64_decode(''.join(out))
        try:
            return raw.decode('utf-8')
        except Exception:
            return ''

    def _device_json(self):
        device = [
            ('static_score', '0.8'),
            ('uuid', '00000000-7fc7-08dc-0000-000000000000'),
            ('device-id', '20250220125449b9b8cac84c2dd3d035c9052a2572f7dd0122edde3cc42a70'),
            ('mac', ''),
            ('sourceuid', 'aa7de295aad621a6'),
            ('refresh-type', '0'),
            ('model', '22021211RC'),
            ('wlb-imei', ''),
            ('client-id', 'aa7de295aad621a6'),
            ('brand', 'Redmi'),
            ('oaid', ''),
            ('oaid-no-cache', ''),
            ('sys-ver', '12'),
            ('trusted-id', ''),
            ('phone-level', 'H'),
            ('imei', ''),
            ('wlb-uid', 'aa7de295aad621a6'),
            ('session-id', str(int(time.time() * 1000))),
        ]
        parts = []
        for field, value in device:
            parts.append('"%s":"%s"' % (field, value))
        return '{' + ','.join(parts) + '}'

    def _headers(self):
        qmp = self._obfuscate(b64_encode(self._device_json()))
        sign = md5_hex('AUTHORIZATION=app-version=10001application-id=com.duoduo.read' +
                       'channel=unknown' + 'is-white=net-env=5platform=android' +
                       'qm-params=' + qmp + 'reg=' + self.KEYS)
        return {
            'net-env': '5',
            'reg': '',
            'channel': 'unknown',
            'is-white': '',
            'platform': 'android',
            'application-id': 'com.duoduo.read',
            'authorization': '',
            'app-version': '10001',
            'user-agent': 'webviewversion/0',
            'qm-params': qmp,
            'sign': sign,
            'User-Agent': ('Mozilla/5.0 (Windows NT 6.1; WOW64) AppleWebKit/537.36 '
                           '(KHTML, like Gecko) Chrome/50.0.2661.87 Safari/537.36'),
        }

    def _get(self, target):
        try:
            status, text = http_request(target, 'GET', headers=self._headers(), timeout=20)
        except Exception as exc:
            print('[七猫短剧] 请求失败: %s' % exc)
            return None
        if status >= 400:
            print('[七猫短剧] 接口 HTTP %s' % status)
            return None
        envelope = json_loads(text)
        if not isinstance(envelope, dict):
            print('[七猫短剧] 接口响应无法解析')
            return None
        data = envelope.get('data')
        if isinstance(data, dict):
            return data
        return envelope

    def _card(self, item):
        item = as_dict(item)
        vid = map_str(item, 'playlet_id', 'id')
        title = clean_text(html_unescape(strip_tags(map_str(item, 'title'))))
        if not vid or not title:
            return None
        card = {
            'vod_id': vid,
            'vod_name': title,
            'vod_pic': '',
            'vod_remarks': '',
            'vod_class': clean_text(map_str(item, 'tags')),
            'vod_content': '',
        }
        cover = map_str(item, 'image_link')
        if cover:
            card['vod_pic'] = resolve_url(self.site + '/', cover)
        parts = []
        tags = clean_text(map_str(item, 'tags'))
        if tags:
            parts.append(tags)
        count = atoi(map_str(item, 'total_episode_num', 'total_num'), 0)
        if count > 0:
            parts.append('%d集' % count)
        hot = clean_text(map_str(item, 'hot_value'))
        if hot and hot != '0':
            parts.append(hot)
        card['vod_remarks'] = ' · '.join(parts)
        return card

    def _cards(self, items):
        cards = []
        seen = set()
        for raw_item in as_list(items):
            card = self._card(raw_item)
            if not card or card['vod_id'] in seen:
                continue
            seen.add(card['vod_id'])
            cards.append(card)
        return cards

    def listing(self, cat, page):
        page = page if page and page > 0 else 1
        tag = self._tag(cat)
        try:
            if page == 1:
                sign = md5_hex('operation=1playlet_privacy=1tag_id=' + tag + self.KEYS)
                target = ('%s/api/v1/playlet/index?tag_id=%s&playlet_privacy=1'
                          '&operation=1&sign=%s' % (self.site, quote(tag, safe=''), sign))
            else:
                next_id = str(page)
                sign = md5_hex('next_id=' + next_id + 'operation=1playlet_privacy=1'
                               'tag_id=' + tag + self.KEYS)
                target = ('%s/api/v1/playlet/index?tag_id=%s&next_id=%s'
                          '&playlet_privacy=1&operation=1&sign=%s'
                          % (self.site, quote(tag, safe=''), next_id, sign))
            data = self._get(target)
            if not data:
                if page > 1:
                    return []
                print('[七猫短剧] 列表无内容')
                return []
            cards = self._cards(data.get('list'))
            if not cards and page > 1:
                return []
            return cards
        except Exception as exc:
            print('[七猫短剧] 列表读取失败: %s' % exc)
            return []

    def search(self, wd, page):
        wd = str(wd or '').strip()
        if not wd:
            return []
        page = page if page and page > 0 else 1
        page_str = str(page)
        try:
            sign = md5_hex('extend=page=' + page_str + 'read_preference=0track_id=' +
                           self.SEARCH_TRACK_ID + 'wd=' + wd + self.KEYS)
            target = ('%s/api/v1/playlet/search?extend=&page=%s&wd=%s'
                      '&read_preference=0&track_id=%s&sign=%s'
                      % (self.site, page_str, quote(wd, safe=''),
                         self.SEARCH_TRACK_ID, sign))
            data = self._get(target)
            if not data:
                print('[七猫短剧] 搜索无结果: %s' % wd)
                return []
            return self._cards(data.get('list'))
        except Exception as exc:
            print('[七猫短剧] 搜索失败: %s' % exc)
            return []

    def _info(self, vid):
        sign = md5_hex('playlet_id=' + vid + self.KEYS)
        target = ('%s/player/api/v1/playlet/info?playlet_id=%s&sign=%s'
                  % (self.READ_BASE, quote(vid, safe=''), sign))
        data = self._get(target)
        if not data:
            print('[七猫短剧] 详情无数据: %s' % vid)
            return {}
        return data

    def detail(self, vid):
        vid = str(vid or '').strip()
        if not vid:
            return {}
        try:
            data = self._info(vid)
            if not data:
                return {}
            episodes = []
            seen = set()
            for raw_item in as_list(data.get('play_list')):
                item = as_dict(raw_item)
                if not item:
                    continue
                seq = atoi(map_str(item, 'sort'), 0)
                if seq < 1 or seq in seen:
                    continue
                seen.add(seq)
                episodes.append({
                    'no': seq,
                    'name': '第%d集' % seq,
                    'url': '%s://%s/%d' % (self.key, vid, seq),
                })
            episodes = sort_episodes(episodes)
            if not episodes:
                print('[七猫短剧] 详情无分集: %s' % vid)
                return {}
            cover = map_str(data, 'image_link')
            return {
                'vod_id': vid,
                'vod_name': clean_text(map_str(data, 'title')) or ('七猫短剧 ' + vid),
                'vod_pic': resolve_url(self.site + '/', cover) if cover else '',
                'vod_content': truncate(clean_text(map_str(data, 'intro')), 400),
                'vod_class': clean_text(map_str(data, 'tags')),
                'vod_remarks': '共%d集' % len(episodes),
                'episodes': episodes,
            }
        except Exception as exc:
            print('[七猫短剧] 详情读取失败: %s' % exc)
            return {}

    def play(self, url):
        raw = str(url or '')
        prefix = self.key + '://'
        if not raw.startswith(prefix):
            if is_http_media(raw):
                return {'url': raw, 'header': {}, 'parse': 0}
            return {}
        rest = raw[len(prefix):]
        if '/' not in rest:
            return {}
        vid, extra = rest.split('/', 1)
        if not vid:
            return {}
        seq = atoi(extra, 1)
        try:
            data = self._info(vid)
        except Exception as exc:
            print('[七猫短剧] 播放读取失败: %s' % exc)
            return {}
        for raw_item in as_list(data.get('play_list')):
            item = as_dict(raw_item)
            if not item:
                continue
            if atoi(map_str(item, 'sort'), -1) != seq:
                continue
            src = map_str(item, 'video_url')
            if not is_http_media(src):
                print('[七猫短剧] 第%d集暂无播放地址' % seq)
                return {}
            return {'url': src, 'header': {'Referer': self.site + '/'}, 'parse': 0}
        print('[七猫短剧] 详情缺第%d集' % seq)
        return {}

class DamangSite(object):

    key = 'damang'
    name = '大芒'
    site = 'https://damang.api.mgtv.com'

    PLAY_API_BASE = 'http://mobile.api.mgtv.com'
    DEVICE_ID = '2ebfeec0a5ad5c0357bae448b8658cc3'
    PAGE_SIZE = 12
    PLAY_RETRY = 5
    PLAY_RETRY_WAIT = 1.2
    MEDIA_UA = 'libmpv'

    CAT_LIST = [
        ('aiqing', '爱情', '爱情', '1273154'),
        ('dushi', '都市', '都市', '1279863'),
        ('guzhuang', '古装', '古装', '1273153'),
        ('yanqing', '言情', '言情', '1273190'),
        ('qihuan', '奇幻', '奇幻', '2924543'),
        ('zhenrenxiu', '真人秀', '真人秀', '1657120'),
        ('xuanyi', '悬疑', '悬疑', '1310272'),
        ('wangju', '网剧', '网剧', '2844459'),
        ('qingchun', '青春', '青春', '2836677'),
    ]

    def cats(self):
        return [{'type_id': item[0], 'type_name': item[1]} for item in self.CAT_LIST]

    def _class(self, cat):
        cat = str(cat or '').strip()
        for item in self.CAT_LIST:
            if item[0] == cat:
                return item[2], item[3]
        fallback = self.CAT_LIST[0]
        return fallback[2], fallback[3]

    def _get_json(self, target):
        try:
            status, text = http_request(target, 'GET', headers={
                'Accept': 'application/json, text/plain, */*',
                'Referer': self.site + '/',
            }, timeout=20)
        except Exception as exc:
            print('[大芒短剧] 请求失败: %s' % exc)
            return None
        if status >= 400:
            print('[大芒短剧] 接口 HTTP %s' % status)
            return None
        envelope = json_loads(text)
        if not isinstance(envelope, dict):
            print('[大芒短剧] 接口响应无法解析')
            return None
        if 'code' in envelope:
            if atoi(map_str(envelope, 'code'), 0) != 200:
                print('[大芒短剧] 接口返回错误: %s' % map_str(envelope, 'msg'))
                return None
        return envelope

    def _card(self, item, cat_name):
        item = as_dict(item)
        vid = map_str(item, 'albumId').strip()
        title = clean_text(map_str(item, 'albumTitle', 'title'))
        if not vid or not title:
            return None
        card = {
            'vod_id': vid,
            'vod_name': title,
            'vod_pic': '',
            'vod_remarks': clean_text(map_str(item, 'albumUpdateSerialNoDesc')),
            'vod_class': cat_name or '',
            'vod_content': '',
        }
        cover = map_str(item, 'verticalImg')
        if cover:
            card['vod_pic'] = resolve_url(self.site + '/', cover)
        return card

    def _cards(self, items, cat_name):
        cards = []
        seen = set()
        for raw_item in as_list(items):
            card = self._card(raw_item, cat_name)
            if not card or card['vod_id'] in seen:
                continue
            seen.add(card['vod_id'])
            cards.append(card)
        return cards

    def listing(self, cat, page):
        page = page if page and page > 0 else 1
        tag_name, tag_id = self._class(cat)
        try:
            target = ('%s/newbee/manage/page/film/library/v3?did=%s&pageSize=%d'
                      '&pageNum=%d&tagName=%s&tagId=%s'
                      % (self.site, self.DEVICE_ID, self.PAGE_SIZE, page,
                         quote(tag_name, safe=''), quote(tag_id, safe='')))
            envelope = self._get_json(target)
            if not envelope:
                return []
            data = as_dict(envelope.get('data'))
            cards = self._cards(data.get('list'), tag_name)
            if not cards and page <= 1:
                print('[大芒短剧] 列表无内容')
            return cards
        except Exception as exc:
            print('[大芒短剧] 列表读取失败: %s' % exc)
            return []

    def search(self, wd, page):
        wd = str(wd or '').strip()
        if not wd:
            return []
        page = page if page and page > 0 else 1
        try:
            target = ('%s/newbee/search/key/words?keyWords=%s&screenType=1'
                      '&pageNum=%d&pageSize=%d'
                      % (self.site, quote(wd, safe=''), page, self.PAGE_SIZE))
            envelope = self._get_json(target)
            if not envelope:
                return []
            data = as_dict(envelope.get('data'))
            cards = self._cards(data.get('videos'), '')
            if not cards:
                print('[大芒短剧] 搜索无结果: %s' % wd)
            return cards
        except Exception as exc:
            print('[大芒短剧] 搜索失败: %s' % exc)
            return []

    def _album(self, vid):
        target = ('%s/newbee/play/page/detail?did=%s&albumId=%s&vid='
                  % (self.site, self.DEVICE_ID, quote(vid, safe='')))
        envelope = self._get_json(target)
        if not envelope:
            return {}
        album = as_dict(as_dict(envelope.get('data')).get('album'))
        if not album:
            print('[大芒短剧] 详情无数据: %s' % vid)
            return {}
        return album

    def detail(self, vid):
        vid = str(vid or '').strip()
        if not vid:
            return {}
        try:
            album = self._album(vid)
            if not album:
                return {}
            tags = []
            for raw_tag in as_list(album.get('tagList')):
                name = clean_text(map_str(as_dict(raw_tag), 'tagName'))
                if name:
                    tags.append(name)
            episodes = []
            seen = set()
            for index, raw_ep in enumerate(as_list(album.get('anthologies'))):
                ep = as_dict(raw_ep)
                ep_vid = map_str(ep, 'vid').strip()
                if not ep_vid:
                    continue
                seq = atoi(map_str(ep, 'serialno'), 0)
                if seq < 1:
                    seq = index + 1
                if seq in seen:
                    continue
                seen.add(seq)
                episodes.append({
                    'no': seq,
                    'name': '第%d集' % seq,
                    'url': '%s://%s' % (self.key, ep_vid),
                })
            episodes = sort_episodes(episodes)
            if not episodes:
                print('[大芒短剧] 详情无剧集: %s' % vid)
                return {}
            cover = map_str(album, 'verticalImg')
            return {
                'vod_id': vid,
                'vod_name': clean_text(map_str(album, 'albumTitle')) or ('大芒短剧 ' + vid),
                'vod_pic': resolve_url(self.site + '/', cover) if cover else '',
                'vod_content': truncate(clean_text(map_str(album, 'albumBrief')), 400),
                'vod_class': ' · '.join(tags),
                'vod_remarks': '共%d集' % len(episodes),
                'episodes': episodes,
            }
        except Exception as exc:
            print('[大芒短剧] 详情读取失败: %s' % exc)
            return {}

    @staticmethod
    def _play_src(data):
        for raw_source in as_list(data.get('videoSources')):
            source = as_dict(raw_source)
            if not source:
                continue
            info = map_str(as_dict(source.get('disp')), 'info')
            if is_http_media(info):
                return info
        return ''

    def play(self, url):
        raw = str(url or '').strip()
        prefix = self.key + '://'
        if not raw.startswith(prefix):
            if is_http_media(raw):
                return {'url': raw, 'header': {'User-Agent': self.MEDIA_UA,
                                               'Referer': ''}, 'parse': 0}
            return {}
        rest = raw[len(prefix):]
        if '/' in rest:
            vid = rest.split('/', 1)[0]
        else:
            vid = rest
        if not vid:
            return {}
        target = ('%s/v8/video/getSource?playType=28&fileSourceType=2&seekSourceType=2'
                  '&definition=3&_support=10100001&osVersion=10&videoId=%s'
                  % (self.PLAY_API_BASE, quote(vid, safe='')))
        try:
            for attempt in range(self.PLAY_RETRY):
                envelope = self._get_json(target)
                if envelope:
                    src = self._play_src(as_dict(envelope.get('data')))
                    if src:
                        return {
                            'url': src,
                            'header': {'User-Agent': self.MEDIA_UA, 'Referer': ''},
                            'parse': 0,
                        }
                    print('[大芒短剧] 暂无播放地址: %s' % vid)
                if attempt + 1 < self.PLAY_RETRY:
                    time.sleep(self.PLAY_RETRY_WAIT)
        except Exception as exc:
            print('[大芒短剧] 播放解析失败: %s' % exc)
        return {}

try:
    import requests
except ImportError:
    requests = None

try:
    import urllib.request as _urlrequest
    import ssl as _ssl
except ImportError:
    _urlrequest = None
    _ssl = None

try:
    import gzip as _gzip
except ImportError:
    _gzip = None

if requests is None:
    requests = CompatRequests()

try:
    from base.spider import Spider as _BaseSpider
except ImportError:
    class _BaseSpider(object):
        pass

try:
    import warnings
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    warnings.filterwarnings('ignore', message='Unverified HTTPS request')
except Exception:
    pass

_AES_SBOX, _AES_INV_SBOX = _aes_tables()

class Dj51Site(object):

    key = 'dj51'
    name = '51短剧'
    site = 'https://arab.bqdikpcrx.cc'

    CATS = [
        {'type_id': 'rank', 'type_name': '总榜', 'path': '/rank/'},
        {'type_id': 'new', 'type_name': '最新剧场', 'path': '/new-theater-category/'},
        {'type_id': 'tag:51原创', 'type_name': '51原创', 'path': '/tag/51原创/'},
        {'type_id': 'tag:AI短剧', 'type_name': 'AI短剧', 'path': '/tag/AI短剧/'},
        {'type_id': 'tag:成人', 'type_name': '成人', 'path': '/tag/成人/'},
        {'type_id': 'tag:原创', 'type_name': '原创', 'path': '/tag/原创/'},
    ]

    _RE_NUXT = re.compile(r'(?is)<script[^>]*id="__NUXT_DATA__"[^>]*>')
    _RE_PLAY = re.compile(r'(?i)<a\b[^>]*href="/play/(\d+)/1/"[^>]*>')
    _RE_LABEL = re.compile(r'(?i)aria-label="([^"]*)"')
    _RE_ALT = re.compile(r'(?i)\balt="([^"]*)"')
    _RE_IMG = re.compile(r'(?i)(?:data-src|src)="(/_img/[^"]+|https?://[^"]*pic[^"]*)"')
    _RE_M3U8 = re.compile(r'(?i)https?://[^\s"\'<>\\]{10,200}\.m3u8[^\s"\'<>\\]{0,200}')

    _WRAPPER_TAGS = ('Reactive', 'ShallowReactive', 'Ref', 'ShallowRef',
                     'EmptyRef', 'EmptyShallowRef')

    def cats(self):
        return [{'type_id': item['type_id'], 'type_name': item['type_name']}
                for item in self.CATS]

    @classmethod
    def _path_of(cls, cat):
        for item in cls.CATS:
            if item['type_id'] == cat:
                return item['path'], item['type_name']
        return cls.CATS[0]['path'], cls.CATS[0]['type_name']

    @staticmethod
    def _page_headers():
        return {
            'User-Agent': UA_ANDROID,
            'Accept': ('text/html,application/xhtml+xml,application/xml;q=0.9,'
                       'image/avif,image/webp,image/apng,*/*;q=0.8'),
            'Accept-Language': 'zh-CN,zh;q=0.9',
            'Referer': 'https://arab.bqdikpcrx.cc/',
        }

    @staticmethod
    def _media_headers():
        return {
            'User-Agent': UA_DESKTOP,
            'Accept': '*/*',
            'Accept-Language': 'zh-CN,zh;q=0.9',
            'Referer': 'https://arab.bqdikpcrx.cc/',
        }

    def _fetch(self, path):
        status, text = http_get(self.site + path, headers=self._page_headers(),
                                timeout=20)
        if status >= 400 or not text:
            print('[51短剧] 页面请求失败 %s: HTTP %s' % (path, status))
            return ''
        return text

    @staticmethod
    def _pic(cover):
        cover = (cover or '').strip()
        if not cover:
            return ''
        if cover.startswith('http://') or cover.startswith('https://'):
            return _img_proxy_url(cover)
        return _img_proxy_url('https://arab.bqdikpcrx.cc/' + cover.lstrip('/'))

    @staticmethod
    def _nuxt_rows(page):
        if not page:
            return None
        loc = Dj51Site._RE_NUXT.search(page)
        if not loc:
            return None
        try:
            rows, _ = json.JSONDecoder().raw_decode(page, loc.end())
        except Exception:
            return None
        if not isinstance(rows, list) or not rows:
            return None
        return rows

    @staticmethod
    def _ref_index(v):
        if isinstance(v, bool) or not isinstance(v, int):
            return None
        return v

    @classmethod
    def _wrapper_index(cls, v):
        if not isinstance(v, list) or len(v) != 2:
            return None
        tag = v[0]
        if not isinstance(tag, str) or tag not in cls._WRAPPER_TAGS:
            return None
        idx = v[1]
        if isinstance(idx, bool) or not isinstance(idx, int):
            return None
        return idx

    @classmethod
    def _resolve(cls, rows, v, depth=0):
        if depth > 24:
            return None
        n = cls._ref_index(v)
        if n is not None:
            if n < 0 or n >= len(rows):
                return v
            target = rows[n]
            if isinstance(target, (dict, list)):
                return cls._resolve(rows, target, depth + 1)
            return target
        if isinstance(v, dict):
            return dict((k, cls._resolve(rows, val, depth + 1))
                        for k, val in v.items())
        if isinstance(v, list):
            idx = cls._wrapper_index(v)
            if idx is not None:
                if idx < 0 or idx >= len(rows):
                    return None
                return cls._resolve(rows, rows[idx], depth + 1)
            return [cls._resolve(rows, item, depth + 1) for item in v]
        return v

    @classmethod
    def _deref(cls, rows, v):
        for _ in range(8):
            n = cls._ref_index(v)
            if n is not None:
                if n < 0 or n >= len(rows):
                    return v
                target = rows[n]
                if isinstance(target, list):
                    idx = cls._wrapper_index(target)
                    if idx is not None:
                        if idx < 0 or idx >= len(rows):
                            return None
                        v = rows[idx]
                        continue
                return target
            if isinstance(v, list):
                idx = cls._wrapper_index(v)
                if idx is None:
                    return v
                if idx < 0 or idx >= len(rows):
                    return None
                v = rows[idx]
                continue
            return v
        return None

    @classmethod
    def _deref_map(cls, rows, v):
        m = cls._deref(rows, v)
        return m if isinstance(m, dict) else None

    @classmethod
    def _deref_list(cls, rows, v):
        got = cls._deref(rows, v)
        return got if isinstance(got, list) else []

    @classmethod
    def _nstr(cls, rows, m, *keys):
        if not isinstance(m, dict):
            return ''
        for key in keys:
            if key not in m:
                continue
            got = cls._resolve(rows, m[key])
            if isinstance(got, str) and got.strip():
                return got.strip()
        return ''

    @classmethod
    def _nnum(cls, rows, m, key):
        if not isinstance(m, dict) or key not in m:
            return 0
        got = cls._resolve(rows, m[key])
        if isinstance(got, bool):
            return 0
        if isinstance(got, int):
            return got
        if isinstance(got, float):
            return int(got) if got == int(got) else 0
        if isinstance(got, str):
            return to_int(got, 0)
        return 0

    @classmethod
    def _business(cls, rows):
        root = None
        for row in rows:
            if not isinstance(row, dict):
                continue
            if 'data' not in row or 'state' not in row:
                continue
            root = row
            break
        if root is None:
            return None
        wrapper = cls._deref_map(rows, root.get('data'))
        if wrapper is None:
            return None
        for v in wrapper.values():
            payload = cls._deref_map(rows, v)
            if payload is None:
                continue
            inner = cls._deref_map(rows, payload.get('data'))
            if inner is not None:
                return inner
            return payload
        return None

    @classmethod
    def _covers(cls, rows):
        if not rows:
            return {}
        biz = cls._business(rows)
        if biz is None:
            return {}
        out = {}
        for item in cls._deref_list(rows, biz.get('list')):
            m = cls._deref_map(rows, item)
            if m is None:
                continue
            num = cls._nnum(rows, m, 'video_id')
            vid = str(num) if num > 0 else cls._nstr(rows, m, 'id')
            cover = cls._nstr(rows, m, 'cover', 'cover_img')
            if vid and cover:
                out[vid] = cover
        return out

    def _cards(self, page, rows, class_name):
        cards = []
        seen = set()
        covers = self._covers(rows) if rows else {}
        for m in self._RE_PLAY.finditer(page):
            vid = m.group(1)
            if vid in seen:
                continue
            tag = m.group(0)
            title = ''
            label = self._RE_LABEL.search(tag)
            if label:
                cand = clean_text(label.group(1))
                if cand and '播放' not in cand and cand != '立即看剧':
                    title = cand
            start = m.start() - 1200
            if start < 0:
                start = 0
            end = m.end() + 1200
            if end > len(page):
                end = len(page)
            block = page[start:end]
            if not title:
                for alt in self._RE_ALT.finditer(block):
                    cand = clean_text(alt.group(1))
                    if cand and '封面' not in cand and cand != 'logo':
                        title = cand
                        break
            if not title:
                continue
            seen.add(vid)
            pic = ''
            cover = covers.get(vid)
            if cover:
                pic = self._pic(cover)
            else:
                img = self._RE_IMG.search(block)
                if img:
                    pic = self._pic(img.group(1))
            cards.append({
                'vod_id': vid,
                'vod_name': title,
                'vod_pic': pic,
                'vod_remarks': '',
                'vod_class': class_name,
                'vod_content': '',
            })
        return cards

    def _page(self, cat, page):
        path, name = self._path_of(cat)
        if page > 1:
            path = path.rstrip('/') + '/page/%d/' % page
        return self.site + quote(path, safe='/%'), name

    def listing(self, cat, page):
        page = page if page and page > 0 else 1
        cat = cat or self.CATS[0]['type_id']
        try:
            target, name = self._page(cat, page)
            status, body = http_get(target, headers=self._page_headers(),
                                    timeout=20)
            if status >= 400 or not body:
                if status == 404 and page > 1:
                    return []
                print('[51短剧] 列表请求失败: HTTP %s' % status)
                return []
            cards = self._cards(body, self._nuxt_rows(body), name)
            if not cards:
                if page > 1:
                    return []
                print('[51短剧] 列表无内容: %s' % target)
                return []
            return cards
        except Exception as exc:
            print('[51短剧] 列表读取失败: %s' % exc)
            return []

    def search(self, wd, page):
        wd = (wd or '').strip()
        if not wd:
            return []
        page = page if page and page > 0 else 1
        try:
            path = '/search/?keyword=' + quote(wd, safe='')
            if page > 1:
                path += '&page=%d' % page
            body = self._fetch(path)
            if not body:
                return []
            cards = self._cards(body, self._nuxt_rows(body), self.name)
            lower = wd.lower()
            hits = [c for c in cards if lower in c['vod_name'].lower()]
            if not hits:
                print('[51短剧] 搜索无结果（该站搜索为客户端渲染）')
                return []
            return hits
        except Exception as exc:
            print('[51短剧] 搜索失败: %s' % exc)
            return []

    def detail(self, vid):
        vid = str(vid or '').strip()
        if '/' in vid:
            vid = vid.rsplit('/', 1)[-1]
        try:
            int(vid)
        except (TypeError, ValueError):
            print('[51短剧] 无效的 ID: %s' % vid)
            return {}
        try:
            body = self._fetch('/play/%s/1/' % quote(vid, safe=''))
            rows = self._nuxt_rows(body)
            if not rows:
                print('[51短剧] 详情页无 SSR 数据: %s' % vid)
                return {}
            name = ''
            pic = ''
            desc = ''
            tag = ''
            for row in rows:
                if not isinstance(row, dict) or 'drama_name' not in row:
                    continue
                name = self._nstr(rows, row, 'drama_name', 'video_title')
                pic = self._pic(self._nstr(rows, row, 'cover_img', 'cover'))
                desc = self._nstr(rows, row, 'description')
                tag = self._nstr(rows, row, 'tags')
                break
            if not tag:
                for row in rows:
                    if not isinstance(row, dict) or 'drama_name' not in row:
                        continue
                    got = self._resolve(rows, row.get('tags'))
                    if isinstance(got, list):
                        tag = ','.join([x for x in got if isinstance(x, str)])
                    break
            by_sort = {}
            for row in rows:
                if not isinstance(row, dict) or 'episode_title' not in row:
                    continue
                seq = self._nnum(rows, row, 'sort')
                if seq < 1:
                    continue
                old = by_sort.get(seq)
                if old is not None and old[1]:
                    continue
                by_sort[seq] = (seq, self._nstr(rows, row, 'episode_title'))
            if not by_sort:
                print('[51短剧] 详情页无剧集: %s' % vid)
                return {}
            episodes = []
            for seq in sorted(by_sort.keys()):
                label = by_sort[seq][1] or ('第%d集' % seq)
                episodes.append({
                    'no': seq,
                    'name': label,
                    'url': '%s://%s/%d' % (self.key, vid, seq),
                })
            episodes = sort_episodes(episodes)
            return {
                'vod_id': vid,
                'vod_name': name or ('51短剧 ' + vid),
                'vod_pic': pic,
                'vod_content': clean_text(desc),
                'vod_class': first_non_empty(tag, self.name),
                'vod_remarks': '共%d集' % len(episodes),
                'episodes': episodes,
            }
        except Exception as exc:
            print('[51短剧] 详情读取失败: %s' % exc)
            return {}

    def play(self, url):
        raw = str(url or '')
        prefix = self.key + '://'
        vid = ''
        extra = ''
        if raw.startswith(prefix):
            rest = raw[len(prefix):]
            if '/' in rest:
                vid, extra = rest.split('/', 1)
        elif '/play/' in raw:
            rest = raw[raw.index('/play/') + len('/play/'):].strip('/')
            parts = rest.split('/')
            if parts and parts[0]:
                vid = parts[0]
                if len(parts) >= 2:
                    extra = parts[1]
        if not vid:
            print('[51短剧] 播放参数无效: %s' % raw)
            return {}
        seq = atoi(extra, 1)
        if seq < 1:
            seq = 1
        try:
            body = self._fetch('/play/%s/%d/' % (quote(vid, safe=''), seq))
            if not body:
                return {}
            media = ''
            rows = self._nuxt_rows(body)
            if rows:
                for row in rows:
                    if not isinstance(row, dict) or 'episode_title' not in row:
                        continue
                    if self._nnum(rows, row, 'sort') != seq:
                        continue
                    got = self._nstr(rows, row, 'video_url')
                    if '.m3u8' in got:
                        media = got
                        break
            if not media:
                normalized = body.replace('\\u0026', '&').replace('\\/', '/')
                found = self._RE_M3U8.search(normalized)
                if found:
                    media = found.group(0)
            if not media or not is_http_media(media):
                print('[51短剧] 播放页无媒体地址: 第%d集' % seq)
                return {}
            return {'url': media, 'header': self._media_headers(), 'parse': 0}
        except Exception as exc:
            print('[51短剧] 播放读取失败: %s' % exc)
            return {}

try:
    import requests
except ImportError:
    requests = None

try:
    import urllib.request as _urlrequest
    import ssl as _ssl
except ImportError:
    _urlrequest = None
    _ssl = None

try:
    import gzip as _gzip
except ImportError:
    _gzip = None

if requests is None:
    requests = CompatRequests()

try:
    from base.spider import Spider as _BaseSpider
except ImportError:
    class _BaseSpider(object):
        pass

try:
    import warnings
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    warnings.filterwarnings('ignore', message='Unverified HTTPS request')
except Exception:
    pass

_AES_SBOX, _AES_INV_SBOX = _aes_tables()

class _Md2048Switch(Exception):
    pass

class Md2048Site(object):

    key = 'md2048'
    name = '2048'

    SITE_DOMAINS = ('https://2048ai.vip',
                    'https://mdcmai4.xyz',
                    'https://2048ai.xyz',
                    'https://mdcmai5.xyz')

    PICK_TTL = 600
    PROBE_TIMEOUT = 20

    CATS = [
        {'type_id': 'cat-29', 'type_name': 'AI短剧'},
        {'type_id': 'cat-6', 'type_name': '麻豆原创AI'},
        {'type_id': 'cat-9', 'type_name': '麻豆传媒'},
        {'type_id': 'cat-13', 'type_name': '清纯少女'},
        {'type_id': 'cat-2', 'type_name': 'AV中文字幕'},
        {'type_id': 'cat-1', 'type_name': '国产自拍'},
        {'type_id': 'cat-4', 'type_name': '探花大神'},
        {'type_id': 'cat-7', 'type_name': '91大神'},
        {'type_id': 'cat-14', 'type_name': '重口调教'},
        {'type_id': 'cat-22', 'type_name': '白虎嫩妹'},
        {'type_id': 'cat-23', 'type_name': '家庭乱伦'},
        {'type_id': 'cat-19', 'type_name': '黑料吃瓜'},
        {'type_id': 'posts', 'type_name': '吃瓜图文'},
        {'type_id': 'all', 'type_name': '全部视频'},
    ]

    PROBE_PATH = '/videos?page=1&size=1'

    RE_DIGITS = re.compile(r'^\d+$')

    def __init__(self):
        self._domain = ''
        self._picked_at = 0.0

    def _site(self):
        return (self._domain or self.SITE_DOMAINS[0]).rstrip('/')

    def _api_base(self):
        return self._site() + '/api/v1'

    def _order(self):
        current = self._domain or self.SITE_DOMAINS[0]
        rest = [item for item in self.SITE_DOMAINS if item != current]
        return [current] + rest

    def _probe(self, domain):
        root = str(domain or '').rstrip('/')
        if not root:
            return False
        try:
            status, text = http_get(root + '/api/v1' + self.PROBE_PATH,
                                    headers=self._headers(root),
                                    timeout=self.PROBE_TIMEOUT)
        except Exception:
            return False
        if status != 200 or not text:
            return False
        envelope = json_loads(text)
        return (isinstance(envelope, dict)
                and to_int(envelope.get('code'), 0) == 200)

    def _pick(self):
        order = self._order()
        if len(order) == 1:
            return order[0]
        done = []
        lock = threading.Lock()

        def worker(domain):
            try:
                if not self._probe(domain):
                    return
            except Exception:
                return
            with lock:
                done.append((time.time(), domain))

        threads = []
        try:
            for domain in order:
                thread = threading.Thread(target=worker, args=(domain,))
                thread.daemon = True
                thread.start()
                threads.append(thread)
        except Exception:
            threads = []
        if not threads:
            for domain in order:
                try:
                    if self._probe(domain):
                        return domain
                except Exception:
                    continue
            return order[0]
        deadline = time.time() + self.PROBE_TIMEOUT
        while time.time() < deadline:
            with lock:
                if done:
                    done.sort(key=lambda item: item[0])
                    return done[0][1]
            if not any(thread.is_alive() for thread in threads):
                break
            time.sleep(0.05)
        return order[0]

    def _ensure_domain(self):
        if self._domain and time.time() - self._picked_at < self.PICK_TTL:
            return
        self._domain = self._pick()
        self._picked_at = time.time()

    def _invoke(self, runner):
        self._ensure_domain()
        last = None
        for domain in self._order():
            self._domain = domain
            try:
                return runner()
            except _Md2048Switch as exc:
                last = exc
        self._picked_at = 0.0
        raise last if last is not None else ValueError('2048 域名均不可用')

    def _headers(self, root=''):
        base = (root or self._site()).rstrip('/')
        return {
            'Accept': 'application/json, text/plain, */*',
            'Accept-Language': 'zh-CN,zh;q=0.9',
            'Origin': base,
            'Referer': base + '/',
            'User-Agent': UA_DESKTOP,
        }

    def _api(self, path):
        try:
            status, text = http_get(self._api_base() + path,
                                    headers=self._headers(), timeout=20)
        except Exception as exc:
            print('[2048] 请求失败: %s' % exc)
            raise _Md2048Switch('2048 请求失败')
        if status == 0 or status >= 500:
            print('[2048] 接口 HTTP %s' % status)
            raise _Md2048Switch('2048 接口 HTTP %s' % status)
        envelope = json_loads(text)
        if not isinstance(envelope, dict):
            print('[2048] 接口非 JSON 响应: %s' % path)
            raise _Md2048Switch('2048 接口非 JSON 响应')
        data = as_dict(envelope.get('data'))
        if to_int(envelope.get('code'), 0) != 200 or not data:
            print('[2048] 接口返回异常: %s'
                  % (map_str(envelope, 'message') or '无数据'))
            return None
        return data

    def _abs(self, raw):
        raw = (raw or '').strip()
        if not raw:
            return ''
        if raw.startswith('http://') or raw.startswith('https://'):
            return raw
        return self._site() + '/' + raw.lstrip('/')

    @staticmethod
    def _referer_of(url):
        for domain in Md2048Site.SITE_DOMAINS:
            root = domain.rstrip('/')
            if url.startswith(root + '/') or url == root:
                return root
        return ''

    def _card(self, kind, item):
        item = as_dict(item)
        num = map_str(item, 'id')
        if not num or not self.RE_DIGITS.match(num):
            return None
        title = clean_text(map_str(item, 'title'))
        if not title:
            return None
        cover = self._abs(first_non_empty(map_str(item, 'coverUrl'),
                                         map_str(item, 'cover')))
        parts = []
        secs = to_int(map_str(item, 'durationSec'), 0)
        if secs > 0:
            parts.append('%d分%d秒' % (secs // 60, secs % 60))
        views = to_int(map_str(item, 'viewCount'), 0)
        if views > 0:
            parts.append('%d次播放' % views)
        author = map_str(item, 'authorName')
        if author:
            parts.append(author)
        return {
            'vod_id': '%s-%s' % (kind, num),
            'vod_name': title,
            'vod_pic': cover,
            'vod_remarks': ' · '.join(parts),
            'vod_class': map_str(item, 'categoryName'),
            'vod_content': truncate(clean_text(first_non_empty(
                map_str(item, 'description'), map_str(item, 'content'))), 200),
        }

    def _items(self, path):
        data = self._api(path)
        if data is None:
            return []
        kind = 'posts' if path.startswith('/posts') else 'videos'
        cards = []
        seen = set()
        for raw in as_list(data.get('items')):
            card = self._card(kind, raw)
            if not card or card['vod_id'] in seen:
                continue
            seen.add(card['vod_id'])
            cards.append(card)
        if not cards:
            print('[2048] 列表无内容: %s' % path)
        return cards

    def _list_by_cat(self, cat, query):
        if cat == 'posts':
            return self._items('/posts?' + query)
        if cat == 'all':
            return self._items('/videos?' + query)
        if cat.startswith('cat-'):
            return self._items('/videos?categoryId=%s&%s'
                               % (cat[len('cat-'):], query))
        return self._items('/videos?' + query)

    def cats(self):
        return [dict(item) for item in self.CATS]

    def listing(self, cat, page):
        try:
            page = page if page and page > 0 else 1
            cat = (cat or '').strip() or self.CATS[0]['type_id']
            query = 'page=%d&size=%d' % (page, 24)

            def runner():
                return self._list_by_cat(cat, query)

            return self._invoke(runner)
        except Exception as exc:
            print('[2048] 列表读取失败: %s' % exc)
            return []

    def search(self, wd, page):
        print('[2048] 站点搜索需登录，暂不支持关键词搜索')
        return []

    def _item(self, vid):
        vid = str(vid or '').strip()
        if '-' not in vid:
            print('[2048] 无效的 ID: %s' % vid)
            return None
        kind, num = vid.split('-', 1)
        if not num or not self.RE_DIGITS.match(num):
            print('[2048] 无效的 ID: %s' % vid)
            return None
        path = '/posts/' + num if kind == 'posts' else '/videos/' + num
        return self._api(path)

    def detail(self, vid):
        vid = str(vid or '').strip()
        if not vid:
            return {}

        def runner():
            item = self._item(vid)
            if item is None:
                return {}
            card = self._card(vid.split('-', 1)[0], item)
            if not card:
                print('[2048] 详情数据不完整: %s' % vid)
                return {}
            name = first_non_empty(card['vod_remarks'], '播放')
            return {
                'vod_id': vid,
                'vod_name': card['vod_name'],
                'vod_pic': card['vod_pic'],
                'vod_content': card['vod_content'],
                'vod_class': card['vod_class'],
                'vod_remarks': card['vod_remarks'],
                'episodes': [{'no': 1, 'name': name,
                              'url': '%s://%s/1' % (self.key, vid)}],
            }

        try:
            return self._invoke(runner)
        except Exception as exc:
            print('[2048] 详情读取失败: %s' % exc)
            return {}

    def play(self, url):
        raw = str(url or '')
        prefix = self.key + '://'
        if not raw.startswith(prefix):
            if is_http_media(raw):
                root = self._referer_of(raw)
                return {'url': raw,
                        'header': {'User-Agent': UA_DESKTOP,
                                   'Referer': (root or self._site()) + '/'},
                        'parse': 0}
            print('[2048] 播放参数无效')
            return {}
        rest = raw[len(prefix):]
        if '/' not in rest:
            print('[2048] 播放参数无效')
            return {}
        vid = rest.split('/', 1)[0]
        if not vid:
            print('[2048] 播放参数无效')
            return {}

        def runner():
            item = self._item(vid)
            if item is None:
                return {}
            src = map_str(item, 'videoUrl', 'playUrl', 'url').strip()
            if not src:
                print('[2048] 该内容没有视频源')
                return {}
            return {
                'url': '%s/m3u8/proxy?path=%s'
                       % (self._api_base(), quote(src, safe='')),
                'header': {'User-Agent': UA_DESKTOP,
                           'Referer': self._site() + '/'},
                'parse': 0,
            }

        try:
            return self._invoke(runner)
        except Exception as exc:
            print('[2048] 播放读取失败: %s' % exc)
            return {}

try:
    import requests
except ImportError:
    requests = None

try:
    import urllib.request as _urlrequest
    import ssl as _ssl
except ImportError:
    _urlrequest = None
    _ssl = None

try:
    import gzip as _gzip
except ImportError:
    _gzip = None

if requests is None:
    requests = CompatRequests()

try:
    from base.spider import Spider as _BaseSpider
except ImportError:
    class _BaseSpider(object):
        pass

try:
    import warnings
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    warnings.filterwarnings('ignore', message='Unverified HTTPS request')
except Exception:
    pass

_AES_SBOX, _AES_INV_SBOX = _aes_tables()

class _Dj91Switch(Exception):
    pass

class Dj91Site(object):

    key = 'dj91'
    name = '91'

    SITE_DOMAINS = ('https://91crdj.com',
                    'https://0303.lwbcbync.cc',
                    'https://j6bmio.lwbcbync.cc')

    PICK_TTL = 600
    PROBE_TIMEOUT = 20

    CATS = [
        {'type_id': 'duanju', 'type_name': '短剧'},
        {'type_id': 'manju', 'type_name': '漫剧'},
        {'type_id': 'zhenrenju', 'type_name': '真人剧'},
        {'type_id': 'shipin', 'type_name': '视频'},
        {'type_id': 'paihang', 'type_name': '排行'},
        {'type_id': 'biaoqian:aiduanju', 'type_name': 'AI短剧'},
    ]

    def __init__(self):
        self._domain = ''
        self._picked_at = 0.0

    def cats(self):
        return [dict(item) for item in self.CATS]

    def _site(self):
        return (self._domain or self.SITE_DOMAINS[0]).rstrip('/')

    def _order(self):
        current = self._domain or self.SITE_DOMAINS[0]
        rest = [item for item in self.SITE_DOMAINS if item != current]
        return [current] + rest

    def _probe(self, domain):
        root = str(domain or '').rstrip('/')
        if not root:
            return False
        status, text = http_get(root + '/', headers=self._headers(root),
                                timeout=self.PROBE_TIMEOUT)
        return status == 200 and bool(text)

    def _pick(self):
        order = self._order()
        if len(order) == 1:
            return order[0]
        done = []
        lock = threading.Lock()

        def worker(domain):
            try:
                if not self._probe(domain):
                    return
            except Exception:
                return
            with lock:
                done.append((time.time(), domain))

        threads = []
        try:
            for domain in order:
                thread = threading.Thread(target=worker, args=(domain,))
                thread.daemon = True
                thread.start()
                threads.append(thread)
        except Exception:
            threads = []
        if not threads:
            for domain in order:
                try:
                    if self._probe(domain):
                        return domain
                except Exception:
                    continue
            return order[0]
        deadline = time.time() + self.PROBE_TIMEOUT
        while time.time() < deadline:
            with lock:
                if done:
                    done.sort(key=lambda item: item[0])
                    return done[0][1]
            if not any(thread.is_alive() for thread in threads):
                break
            time.sleep(0.05)
        return order[0]

    def _ensure_domain(self):
        if self._domain and time.time() - self._picked_at < self.PICK_TTL:
            return
        self._domain = self._pick()
        self._picked_at = time.time()

    def _invoke(self, runner):
        self._ensure_domain()
        last = None
        for position, domain in enumerate(self._order()):
            self._domain = domain
            try:
                return runner()
            except _Dj91Switch as exc:
                last = exc
        self._picked_at = 0.0
        raise last if last is not None else ValueError('91 域名均不可用')

    def _headers(self, root=''):
        base = (root or self._site()).rstrip('/')
        return {
            'User-Agent': UA_ANDROID,
            'Accept': ('text/html,application/xhtml+xml,application/xml;q=0.9,'
                       'image/avif,image/webp,image/apng,*/*;q=0.8'),
            'Accept-Language': 'zh-CN,zh;q=0.9',
            'Referer': base + '/',
            'Upgrade-Insecure-Requests': '1',
        }

    def _fetch(self, path):
        target = self._site() + path
        try:
            status, text = http_get(target, headers=self._headers(), timeout=20)
        except Exception as exc:
            print('[91] 请求失败: %s' % exc)
            raise _Dj91Switch('91 请求失败')
        if status == 0 or status >= 500:
            print('[91] 页面 HTTP %s: %s' % (status, path))
            raise _Dj91Switch('91 页面 HTTP %s' % status)
        if status >= 400 or not text:
            print('[91] 页面 HTTP %s: %s' % (status, path))
            return status, ''
        return status, text

    def _abs(self, raw):
        raw = (raw or '').strip()
        if not raw:
            return ''
        if raw.startswith('http://') or raw.startswith('https://'):
            return raw
        return self._site() + '/' + raw.lstrip('/')

    @staticmethod
    def _strip(text):
        return clean_text(re.sub(r'<[^>]*>', ' ', text or ''))

    @staticmethod
    def _attr(block, name):
        match = re.search(r'(?i)\b' + re.escape(name) + r'="([^"]*)"', block or '')
        if match:
            return match.group(1).strip()
        return ''

    @staticmethod
    def _first_image(block):
        for match in re.finditer(
                r'(?i)\b(?:data-src|data-original|data-lazy-src|src)="([^"]+)"',
                block or ''):
            raw = match.group(1).strip()
            low = raw.lower()
            if not raw or low.startswith('data:') or 'placeholder' in low \
                    or 'loading' in low:
                continue
            return raw
        return ''

    @staticmethod
    def _clean_alt(alt):
        alt = (alt or '').strip()
        if alt.startswith('《'):
            pos = alt.find('》')
            if pos > 0:
                return alt[1:pos].strip()
        return alt[:-2].strip() if alt.endswith('封面') else alt

    def _anchor_title(self, block, detail_path):
        best = ''
        pattern = (r'(?is)<a\b[^>]*href="[^"]*' + re.escape(detail_path)
                   + r'"[^>]*>([\s\S]*?)</a>')
        for match in re.finditer(pattern, block or ''):
            text = self._strip(match.group(1))
            if not text or re.match(r'^\d+$', text):
                continue
            if len(text) > len(best):
                best = text
        return best

    def _cat_path(self, cat):
        cat = cat or self.CATS[0]['type_id']
        if cat.startswith('biaoqian:'):
            return '/biaoqian/' + quote(cat[len('biaoqian:'):], safe='') + '/'
        if cat == 'home':
            return '/'
        return '/' + cat + '/'

    def _cat_name(self, cat):
        for item in self.CATS:
            if item['type_id'] == cat:
                return item['type_name']
        return ''

    def _list_path(self, cat, page):
        base = self._cat_path(cat)
        if page <= 1:
            return base
        return base.rstrip('/') + '/page/%d/' % page

    def _blocks(self, page_html):
        starts = []
        for match in re.finditer(r'(?i)<a\s+class="[^"]*\bcard\b[^"]*"',
                                 page_html or ''):
            starts.append(match.start())
        starts.sort()
        blocks = []
        for index, begin in enumerate(starts):
            end = starts[index + 1] if index + 1 < len(starts) else len(page_html)
            blocks.append(page_html[begin:end])
        return blocks

    def _card(self, block, cat_name):
        detail = re.search(
            r'(?i)href="(?:https?://[^/"]+)?'
            r'(/(?:duanju|manju|zhenrenju|shipin|paihang)/\d+-[^"/]+/)"', block)
        if not detail:
            return None
        vid = detail.group(1).strip('/')
        if not vid:
            return None
        title = self._attr(block, 'data-track-item-name')
        if not title:
            title = self._anchor_title(block, detail.group(1))
        if not title:
            alt = re.search(r'(?i)\balt="([^"]*)"', block)
            if alt:
                title = self._clean_alt(alt.group(1))
        if not title:
            heading = re.search(r'(?is)<h[23][^>]*>([\s\S]*?)</h[23]>', block)
            if heading:
                title = self._strip(heading.group(1))
        if not title:
            return None

        remarks = ''
        eps = re.search(r'(?i)class="eps-flag"[^>]*>([\s\S]*?)</span>', block)
        if eps:
            remarks = self._strip(eps.group(1))
        score = ''
        found = re.search(r'(?i)class="score"[^>]*>([^<]*)<', block)
        if found:
            score = self._strip(found.group(1))

        category = ''
        for pattern in (r'(?i)class="meta"[^>]*>([\s\S]*?)</div>',
                        r'(?i)class="rank-(?:row|runner)-(?:meta|metric)"'
                        r'[^>]*>([^<]*)<',
                        r'(?is)class="rank-row-copy"[^>]*>[\s\S]*?<p[^>]*>'
                        r'([^<]*)</p>'):
            found = re.search(pattern, block)
            if found:
                category = self._strip(found.group(1))
                break
        if not category:
            found = re.search(r'(?i)class="badge[^"]*"[^>]*>([^<]*)<', block)
            if found:
                category = found.group(1).strip()

        desc = ''
        for pattern in (
                r'(?i)class="hover-panel"[^>]*>\s*<span>([\s\S]*?)</span>',
                r'(?i)class="rank-(?:row|runner)-desc"[^>]*>([^<]*)<'):
            found = re.search(pattern, block)
            if found:
                desc = truncate(self._strip(found.group(1)), 200)
                break

        cover = self._first_image(block)
        parts = []
        if score:
            parts.append('评分' + score)
        if remarks:
            parts.append(remarks)
        return {
            'vod_id': vid,
            'vod_name': title,
            'vod_pic': _img_proxy_url(self._abs(cover), self._site() + '/') if cover else '',
            'vod_remarks': ' · '.join(parts),
            'vod_class': category or cat_name,
            'vod_content': desc,
        }

    def _cards(self, page_html, cat_name):
        cards = []
        seen = set()
        for block in self._blocks(page_html):
            card = self._card(block, cat_name)
            if not card or card['vod_id'] in seen:
                continue
            seen.add(card['vod_id'])
            cards.append(card)
        return cards

    def listing(self, cat, page):
        page = page if page and page > 0 else 1
        path = self._list_path(cat, page)

        def runner():
            _, body = self._fetch(path)
            if not body:
                return []
            cards = self._cards(body, self._cat_name(cat))
            if not cards:
                print('[91] 列表无内容: %s' % path)
                return []
            return cards
        try:
            return self._invoke(runner)
        except Exception as exc:
            print('[91] 列表读取失败: %s' % exc)
            return []

    def search(self, wd, page):
        wd = (wd or '').strip()
        if not wd:
            return []
        page = page if page and page > 0 else 1
        path = '/search/?q=' + quote(wd, safe='')
        if page > 1:
            path += '&page=%d' % page

        def runner():
            _, body = self._fetch(path)
            if not body:
                return []
            cards = self._cards(body, '')
            if not cards:
                print('[91] 搜索无结果: %s' % wd)
                return []
            return cards

        try:
            return self._invoke(runner)
        except Exception as exc:
            print('[91] 搜索失败: %s' % exc)
            return []

    def _seqs(self, body, vid):
        seqs = set()
        found = re.search(r'(?i)data-total="(\d+)"', body)
        if found:
            total = atoi(found.group(1), 0)
            if 0 < total <= 2000:
                seqs = set(range(1, total + 1))
        if not seqs:
            for match in re.finditer(r'(?i)title="第\s*(\d+)\s*集"', body):
                no = atoi(match.group(1), 0)
                if no > 0:
                    seqs.add(no)
        if not seqs:
            prefix = '/' + vid + '/'
            for match in re.finditer(r'(?i)href="([^"]+)"', body):
                raw = match.group(1)
                parsed = urlparse(raw)
                if parsed.path:
                    raw = parsed.path
                raw = raw.strip('/')
                full = '/' + raw + '/'
                if not full.startswith(prefix):
                    continue
                no = atoi(full[len(prefix):].strip('/'), 0)
                if no > 0:
                    seqs.add(no)
        return sorted(seqs)

    def detail(self, vid):
        vid = str(vid or '').strip().strip('/')
        if not vid:
            return {}

        def runner():
            _, body = self._fetch('/' + vid + '/')
            if not body:
                return {}
            title = ''
            found = re.search(r'(?is)<h1[^>]*>([\s\S]*?)</h1>', body)
            if found:
                title = self._strip(found.group(1))
            desc = ''
            found = re.search(
                r'(?is)<meta[^>]*name="description"[^>]*content="([^"]*)"', body)
            if found:
                desc = clean_text(found.group(1))
            pic = ''
            found = re.search(
                r'(?i)\b(?:data-src|data-original|src)="(https?://pic\.[^"]+)"',
                body)
            if found:
                pic = found.group(1)
            category = ''
            found = re.search(r'(?i)class="badge[^"]*"[^>]*>([^<]*)<', body)
            if found:
                category = found.group(1).strip()

            episodes = []
            for no in self._seqs(body, vid):
                episodes.append({
                    'no': no,
                    'name': '第%d集' % no,
                    'url': '%s://%s/%d' % (self.key, vid, no),
                })
            episodes = sort_episodes(episodes)
            if not episodes:
                print('[91] 详情页无剧集: %s' % vid)
                return {}
            return {
                'vod_id': vid,
                'vod_name': title or ('91 ' + vid),
                'vod_pic': pic,
                'vod_content': desc,
                'vod_class': category,
                'vod_remarks': '共%d集' % len(episodes),
                'episodes': episodes,
            }

        try:
            return self._invoke(runner)
        except Exception as exc:
            print('[91] 详情读取失败: %s' % exc)
            return {}

    def _play_path(self, vid, seq):
        base = '/' + vid.strip('/')
        return '%s/%d/' % (base, seq)

    def _play_src(self, body):
        found = re.search(
            r'(?is)<script[^>]*id="playInitialData"[^>]*>([\s\S]*?)</script>',
            body)
        if not found:
            print('[91] 播放页无播放数据')
            return ''
        data = json_loads(found.group(1).strip())
        if not isinstance(data, dict):
            print('[91] 播放数据解析失败')
            return ''
        current = data.get('current')
        if not isinstance(current, dict):
            current = data.get('episode')
        if not isinstance(current, dict):
            print('[91] 播放数据缺少当前集')
            return ''
        src = map_str(current, 'src', 'videoUrl', 'playUrl', 'url')
        if src:
            return src
        hevc = map_str(current, 'srcHevc')
        if '.m3u8' in hevc:
            return hevc
        print('[91] 播放数据无媒体地址')
        return ''

    def play(self, url):
        raw = str(url or '').strip().strip('/')
        prefix = self.key + '://'
        if raw.startswith(prefix):
            rest = raw[len(prefix):].strip('/')
        else:
            hit = ''
            for domain in self.SITE_DOMAINS:
                root = domain.rstrip('/')
                if root in raw:
                    hit = root
                    break
            if not hit:
                print('[91] 播放参数无效')
                return {}
            rest = raw[raw.index(hit) + len(hit):].lstrip('/').strip('/')
        cut = rest.rfind('/')
        if cut <= 0 or cut >= len(rest) - 1:
            print('[91] 播放参数无效')
            return {}
        vid, seq_text = rest[:cut], rest[cut + 1:]
        seq = atoi(seq_text, 0)
        if seq < 1 or not vid:
            print('[91] 播放参数无效')
            return {}
        def runner():
            _, body = self._fetch(self._play_path(vid, seq))
            if not body:
                return {}
            src = self._play_src(body)
            if not is_http_media(src):
                print('[91] 播放地址无效')
                return {}
            return {
                'url': src,
                'header': {'User-Agent': UA_DESKTOP,
                           'Referer': self._site() + '/'},
                'parse': 0,
            }

        try:
            return self._invoke(runner)
        except Exception as exc:
            print('[91] 播放读取失败: %s' % exc)
            return {}

try:
    import requests
except ImportError:
    requests = None

try:
    import urllib.request as _urlrequest
    import ssl as _ssl
except ImportError:
    _urlrequest = None
    _ssl = None

try:
    import gzip as _gzip
except ImportError:
    _gzip = None

if requests is None:
    requests = CompatRequests()

try:
    from base.spider import Spider as _BaseSpider
except ImportError:
    class _BaseSpider(object):
        pass

try:
    import warnings
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    warnings.filterwarnings('ignore', message='Unverified HTTPS request')
except Exception:
    pass

_AES_SBOX, _AES_INV_SBOX = _aes_tables()

def _nuxt_is_non_image(low):
    return (low.endswith('.js') or low.endswith('.css')
            or '.js?' in low or '.css?' in low
            or 'googletagmanager' in low or '/gtag/js' in low
            or 'analytics' in low or 'hm.baidu' in low
            or '/beacon' in low or 'pixel.gif' in low)

def _nuxt_drama_data(page):
    rows = Dj51Site._nuxt_rows(page)
    if not rows:
        return None, None
    root = None
    for row in rows:
        if not isinstance(row, dict) or 'data' not in row or 'state' not in row:
            continue
        root = row
        break
    if root is None:
        return None, None
    wrapper = Dj51Site._deref_map(rows, root.get('data'))
    if wrapper is None:
        return None, None

    def descend(m, depth):
        if not isinstance(m, dict) or depth > 4:
            return None
        if 'items' in m:
            return m
        for v in m.values():
            if isinstance(v, bool) or not isinstance(v, int):
                continue
            inner = Dj51Site._deref_map(rows, v)
            if inner is not None:
                got = descend(inner, depth + 1)
                if got is not None:
                    return got
        return None

    data = descend(wrapper, 0)
    return (rows, data) if data is not None else (None, None)

def _collect_nuxt_drama_items(rows, node, out, depth=0):
    if node is None or depth > 8 or len(out) > 400:
        return
    if isinstance(node, dict):
        for k, v in node.items():
            if k == 'items':
                lst = Dj51Site._deref_list(rows, v)
                if lst:
                    for it in lst:
                        m = Dj51Site._deref_map(rows, it)
                        if m is not None and Dj51Site._nstr(rows, m, 'slug'):
                            out.append(m)
                    continue
            _collect_nuxt_drama_items(rows, Dj51Site._deref(rows, v), out,
                                      depth + 1)
    elif isinstance(node, list):
        for it in node:
            _collect_nuxt_drama_items(rows, it, out, depth + 1)

def _nuxt_drama_cards(page, cat_name, pic_cb):
    rows, data = _nuxt_drama_data(page)
    if rows is None or data is None:
        return []
    items = []
    _collect_nuxt_drama_items(rows, data, items)
    cards = []
    seen = set()
    for m in items:
        slug = Dj51Site._nstr(rows, m, 'slug')
        title = Dj51Site._nstr(rows, m, 'title')
        if not slug or not title or slug in seen:
            continue
        seen.add(slug)
        pic = ''
        cover = Dj51Site._deref_map(rows, m.get('cover'))
        if cover is not None:
            u = Dj51Site._nstr(rows, cover, 'url')
            if not u:
                u = Dj51Site._nstr(rows, cover, 'fallback_url')
            if u and not _nuxt_is_non_image(u.lower()):
                pic = _img_proxy_url(pic_cb(u))
        cat = ''
        ch = Dj51Site._deref_map(rows, m.get('channel'))
        if ch is not None:
            cat = Dj51Site._nstr(rows, ch, 'name')
        total = 0
        for k in ('latest_episode_number', 'published_episode_count',
                  'total_episode_count'):
            n = Dj51Site._nnum(rows, m, k)
            if n > 0:
                total = n
                break
        remarks = ''
        if total > 0:
            status = Dj51Site._nstr(rows, m, 'serial_status')
            remarks = ('全%d集' % total) if status.lower() == 'completed' \
                else ('更新至%d集' % total)
        cards.append({
            'vod_id': slug,
            'vod_name': title,
            'vod_pic': pic,
            'vod_remarks': remarks,
            'vod_class': cat or cat_name,
            'vod_content': '',
        })
    return cards

class ChengguoSite(object):

    key = 'chengguo'
    name = '橙果'
    site = 'https://chengguodj.com'

    CATS = [
        {'type_id': 'yuanchuang', 'type_name': '原创', 'path': '/yuanchuang'},
        {'type_id': 'mogai', 'type_name': '魔改', 'path': '/mogai'},
        {'type_id': 'manju', 'type_name': '漫剧', 'path': '/manju'},
        {'type_id': 'zhenren', 'type_name': '真人', 'path': '/zhenren'},
        {'type_id': 'aiduanju', 'type_name': 'AI短剧', 'path': '/aiduanju'},
        {'type_id': 'browse', 'type_name': '全部', 'path': '/browse'},
    ]

    RE_IMG = r'(?i)\b(?:z-image-loader-url|data-cover-fb|data-src|src)="([^"]+)"'
    RE_M3U8 = r'(?i)https?://[^\s"\'<>\\]{10,200}\.m3u8[^\s"\'<>\\]{0,200}'
    RE_DETAIL_EP = (r'(?i)<a\b[^>]*href="/play/([0-9a-zA-Z\-]+)/(\d+)"'
                    r'[^>]*>([\s\S]*?)</a>')
    RE_H1 = r'(?is)<h1[^>]*>([\s\S]*?)</h1>'
    RE_PAGE_TITLE = r'(?is)<title>([\s\S]*?)</title>'
    RE_META_DESC = r'(?is)<meta\s+name="description"\s+content="([^"]*)"'
    RE_DESC_CLASS = (r'(?is)class="[^"]*(?:synopsis|summary|desc)[^"]*"'
                     r'[^>]*>([\s\S]{10,400}?)<')

    def __init__(self):
        self._detail_cache = {}

    def _fetch(self, path):
        try:
            status, text = http_get(self.site + path,
                                    headers={'Referer': self.site + '/'},
                                    timeout=20)
        except Exception as exc:
            print('[橙果] 请求失败: %s' % exc)
            return 0, ''
        if status >= 400 or not text:
            print('[橙果] 页面 HTTP %s: %s' % (status, path))
        return status, text or ''

    @staticmethod
    def _abs_url(raw):
        raw = (raw or '').strip()
        if not raw:
            return ''
        if raw.startswith('http://') or raw.startswith('https://'):
            return raw
        if raw.startswith('//'):
            return 'https:' + raw
        if raw.startswith('/'):
            return 'https://chengguodj.com' + raw
        return 'https://chengguodj.com/' + raw

    def _path(self, cat):
        for item in self.CATS:
            if item['type_id'] == cat:
                return item['path']
        if cat and cat.startswith('/'):
            return cat
        return self.CATS[0]['path']

    @staticmethod
    def _cat_name(cat):
        for item in ChengguoSite.CATS:
            if item['type_id'] == cat:
                return item['type_name']
        return ''

    def _cards(self, page_html, cat_name):
        return _nuxt_drama_cards(page_html, cat_name, self._abs_url)

    def cats(self):
        return [{'type_id': item['type_id'], 'type_name': item['type_name']}
                for item in self.CATS]

    def listing(self, cat, page):
        try:
            page = page if page and page > 0 else 1
            base = self._path(cat if cat else self.CATS[0]['type_id'])
            path = base
            if page > 1:
                path = base.rstrip('/') + '?page=%d' % page
            status, body = self._fetch(path)
            if status >= 400 or not body:
                return []
            cards = self._cards(body, self._cat_name(cat))
            if not cards:
                if page > 1:
                    return []
                print('[橙果] 列表无内容: %s' % path)
            return cards
        except Exception as exc:
            print('[橙果] 列表读取失败: %s' % exc)
            return []

    def search(self, wd, page):
        wd = (wd or '').strip()
        if not wd:
            return []
        try:
            page = page if page and page > 0 else 1
            path = '/search?q=' + quote(wd, safe='')
            if page > 1:
                path += '&page=' + str(page)
            status, body = self._fetch(path)
            if status >= 400 or not body:
                return []
            cards = self._cards(body, '橙果')
            if not cards:
                print('[橙果] 搜索无结果: %s' % wd)
            return cards
        except Exception as exc:
            print('[橙果] 搜索失败: %s' % exc)
            return []

    def _drama(self, vid):
        cached = self._detail_cache.get(vid)
        if cached and time.time() - cached[0] < 300:
            return cached[1]
        status, body = self._fetch('/drama/' + quote(vid, safe=''))
        body = body if status < 400 else ''
        self._detail_cache[vid] = (time.time(), body)
        return body

    def detail(self, vid):
        vid = str(vid or '').strip()
        if '/' in vid:
            vid = vid.rsplit('/', 1)[-1]
        if not vid:
            print('[橙果] 无效的剧集 ID')
            return {}
        try:
            body = self._drama(vid)
            if not body:
                return {}
            title = ''
            found = re.search(self.RE_H1, body)
            if found:
                title = clean_text(strip_tags(found.group(1)))
            if not title:
                found = re.search(self.RE_PAGE_TITLE, body)
                if found:
                    title = strip_tags(found.group(1)).split('-', 1)[0].strip()
            desc = ''
            found = re.search(self.RE_META_DESC, body)
            if found:
                desc = html_unescape(found.group(1)).strip()
            if not desc:
                found = re.search(self.RE_DESC_CLASS, body)
                if found:
                    desc = clean_text(strip_tags(found.group(1)))
            pic = ''
            found = re.search(self.RE_IMG, body)
            if found:
                raw = html_unescape(found.group(1)).strip()
                low = raw.lower()
                if low and not low.startswith('data:') \
                        and not _nuxt_is_non_image(low):
                    pic = self._abs_url(raw)
            labels = {}
            for match in re.finditer(self.RE_DETAIL_EP, body):
                if match.group(1) != vid:
                    continue
                try:
                    number = int(match.group(2))
                except (TypeError, ValueError):
                    continue
                if number < 1 or number in labels:
                    continue
                labels[number] = clean_text(strip_tags(match.group(3)))
            if not labels:
                print('[橙果] 详情页无剧集: %s' % vid)
                return {}
            episodes = []
            for number in sorted(labels.keys()):
                label = labels[number] or ('第%d集' % number)
                episodes.append({
                    'no': number,
                    'name': label,
                    'url': '%s://%s/%d' % (self.key, vid, number),
                })
            return {
                'vod_id': vid,
                'vod_name': title or ('橙果剧集 ' + vid),
                'vod_pic': pic,
                'vod_content': desc,
                'vod_class': '橙果',
                'vod_remarks': '共%d集' % len(episodes),
                'episodes': episodes,
            }
        except Exception as exc:
            print('[橙果] 详情读取失败: %s' % exc)
            return {}

    def play(self, url):
        raw = str(url or '')
        vid = ''
        extra = ''
        prefix = self.key + '://'
        if raw.startswith(prefix):
            rest = raw[len(prefix):]
            if '/' in rest:
                vid, extra = rest.split('/', 1)
        elif '/play/' in raw:
            rest = raw[raw.index('/play/') + len('/play/'):].strip('/')
            parts = rest.split('/')
            if parts and parts[0]:
                vid = parts[0]
                if len(parts) >= 2:
                    extra = parts[1]
        if not vid:
            print('[橙果] 播放参数无效')
            return {}
        seq = atoi(extra, 1)
        if seq < 1:
            seq = 1
        try:
            path = '/play/%s/%d' % (quote(vid, safe=''), seq)
            status, body = self._fetch(path)
            if status >= 400 or not body:
                return {}
            normalized = (body.replace('\\u0026', '&')
                          .replace('\\u002F', '/').replace('\\/', '/'))
            media = ''
            for match in re.finditer(self.RE_M3U8, normalized):
                candidate = html_unescape(match.group(0)).strip()
                if '.m3u8' in candidate:
                    media = candidate
                    break
            if not media:
                print('[橙果] 播放页无媒体地址: %s' % path)
                return {}
            return {
                'url': media,
                'header': {'Referer': self.site + '/',
                           'User-Agent': UA_DESKTOP},
                'parse': 0,
            }
        except Exception as exc:
            print('[橙果] 播放读取失败: %s' % exc)
            return {}

try:
    import requests
except ImportError:
    requests = None

try:
    import urllib.request as _urlrequest
    import ssl as _ssl
except ImportError:
    _urlrequest = None
    _ssl = None

try:
    import gzip as _gzip
except ImportError:
    _gzip = None

if requests is None:
    requests = CompatRequests()

try:
    from base.spider import Spider as _BaseSpider
except ImportError:
    class _BaseSpider(object):
        pass

try:
    import warnings
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    warnings.filterwarnings('ignore', message='Unverified HTTPS request')
except Exception:
    pass

_AES_SBOX, _AES_INV_SBOX = _aes_tables()

class _YeguoSwitch(Exception):
    pass

class YeguoSite(object):

    key = 'yeguo'
    name = '野果'

    SITE_DOMAINS = ('https://blue.ekzhadks.cc',
                    'https://aids.ekzhadks.cc',
                    'https://adopt.ekzhadks.cc')

    HANDSHAKE_TIMEOUT = 30

    UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
          '(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36')

    CATS = [{'type_id': '', 'type_name': '推荐'}]

    FILTER_FIELDS = ('theme', 'setting', 'background', 'time', 'recommend')

    NUMERIC_ID = re.compile(r'^[1-9][0-9]{0,17}$')

    SCRIPT_TAG = re.compile(r'(?is)<script\b([^>]*)>(.*?)</script>')
    SCRIPT_ATTR = re.compile(r'([a-zA-Z_:][-\w:.]*)\s*=\s*"([^"]*)"')
    MODULE_IMPORT = re.compile(
        r'import\s*\{([^{};]+)\}\s*from\s*["\'`]([^"\'`]+)["\'`]', re.S)
    PUBLIC_FIELD = re.compile(
        r'\b(version|mode|padding|key|iv|sign_key)\s*:\s*'
        r'(?:[a-zA-Z_$][a-zA-Z0-9_$]*\(\s*)?["\'`]([^"\'`\\\r\n]{1,512})["\'`]')

    def __init__(self):
        self._access = None
        self._domain = ''

    def cats(self):
        return [dict(item) for item in self.CATS]

    def _site(self):
        return (self._domain or self.SITE_DOMAINS[0]).rstrip('/')

    def _order(self):
        current = self._domain or self.SITE_DOMAINS[0]
        rest = [item for item in self.SITE_DOMAINS if item != current]
        return [current] + rest

    def _handshake(self):
        order = self._order()
        if len(order) == 1:
            return self._discover(order[0])
        done = []
        lock = threading.Lock()

        def worker(domain):
            try:
                access = self._discover(domain)
            except Exception:
                return
            with lock:
                done.append((time.time(), domain, access))

        threads = []
        try:
            for domain in order:
                thread = threading.Thread(target=worker, args=(domain,))
                thread.daemon = True
                thread.start()
                threads.append(thread)
        except Exception:
            threads = []
        if not threads:
            last = None
            for domain in order:
                try:
                    return self._discover(domain)
                except Exception as exc:
                    last = exc
            raise last if last is not None else ValueError('野果域名均不可用')
        deadline = time.time() + self.HANDSHAKE_TIMEOUT
        while time.time() < deadline:
            with lock:
                if done:
                    done.sort(key=lambda item: item[0])
                    return done[0][2]
            if not any(thread.is_alive() for thread in threads):
                break
            time.sleep(0.05)
        raise ValueError('野果域名均不可用，请稍后重试')

    def _invoke(self, runner):
        last = None
        for position, domain in enumerate(self._order()):
            if position > 0:
                self._access = None
            self._domain = domain
            try:
                return runner()
            except _YeguoSwitch as exc:
                last = exc
        raise last if last is not None else ValueError('野果域名均不可用')

    @staticmethod
    def _origin(url):
        try:
            parts = urlsplit(url)
        except Exception:
            return ''
        scheme = (parts.scheme or '').lower()
        host = (parts.hostname or '').lower()
        try:
            port = parts.port
        except ValueError:
            return ''
        if port and not ((scheme == 'https' and port == 443)
                         or (scheme == 'http' and port == 80)):
            host = '%s:%d' % (host, port)
        return scheme + '://' + host

    def _fetch(self, url, referer):
        status, text = http_get(url, headers={'User-Agent': self.UA,
                                              'Accept': '*/*',
                                              'Referer': referer}, timeout=25)
        if status >= 400 or not text:
            raise ValueError('野果页面抓取失败 HTTP %s' % status)
        return text

    def _api_base(self, html):
        base = ''
        for attrs, body in self.SCRIPT_TAG.findall(html):
            props = dict(self.SCRIPT_ATTR.findall(attrs))
            if props.get('id') != '__NUXT_DATA__':
                continue
            table = json_loads(body)
            if not isinstance(table, list) or len(table) > 100000:
                continue
            for entry in table:
                if not isinstance(entry, dict) or 'apiBaseURL' not in entry:
                    continue
                reference = entry.get('apiBaseURL')
                if isinstance(reference, bool) or not isinstance(reference, int):
                    continue
                if reference < 0 or reference >= len(table):
                    continue
                value = table[reference]
                if isinstance(value, str) and value:
                    base = value
                    break
            if base:
                break
        if not base:
            raise ValueError('野果页面未提供有效的接口入口')
        try:
            parts = urlsplit(base)
        except Exception:
            raise ValueError('野果页面未提供有效的接口入口')
        if not is_http_media(base) or '@' in parts.netloc or parts.query or parts.fragment:
            raise ValueError('野果页面未提供有效的接口入口')
        return base.rstrip('/')

    def _script_url(self, page, reference):
        if not reference:
            return ''
        try:
            address = urljoin(page, reference)
            parts = urlsplit(address)
        except Exception:
            return ''
        if '@' in parts.netloc:
            return ''
        if self._origin(address) != self._origin(page):
            return ''
        if not parts.path.startswith('/_nuxt/') or not parts.path.endswith('.js'):
            return ''
        return parts.scheme + '://' + parts.netloc + parts.path + (
            '?' + parts.query if parts.query else '')

    def _entry_script(self, html, root):
        for attrs, _body in self.SCRIPT_TAG.findall(html):
            props = dict(self.SCRIPT_ATTR.findall(attrs))
            if props.get('type') != 'module':
                continue
            address = self._script_url(root + '/', props.get('src', ''))
            if address:
                return address
        return ''

    @staticmethod
    def _public_bytes(value):
        value = value or ''
        if '_' not in value:
            return value.encode('utf-8', 'ignore')
        out = bytearray()
        for part in value.split('_'):
            if not part.isdigit():
                return b''
            number = int(part, 10)
            if number > 255:
                return b''
            out.append(number)
        return bytes(out)

    def _parse_config(self, script):
        fields = {}
        for name, value in self.PUBLIC_FIELD.findall(script)[:32]:
            fields[name] = value
        if fields.get('version') != 'v0' or fields.get('mode') != 'CBC' \
                or fields.get('padding') != 'Pkcs7':
            return None
        access = {
            'key': self._public_bytes(fields.get('key', '')),
            'iv': self._public_bytes(fields.get('iv', '')),
            'signKey': self._public_bytes(fields.get('sign_key', '')),
        }
        if len(access['key']) not in (16, 24, 32) or len(access['iv']) != 16:
            return None
        if not access['signKey'] or len(access['signKey']) > 128:
            return None
        return access

    def _discover(self, domain):
        root = str(domain or '').rstrip('/')
        if not root:
            raise ValueError('野果域名无效')
        html = self._fetch(root + '/', root + '/')
        base = self._api_base(html)
        entry = self._entry_script(html, root)
        if not entry:
            raise ValueError('野果页面未提供接口配置脚本')
        entry_body = self._fetch(entry, root + '/')
        imports = self.MODULE_IMPORT.findall(entry_body)[:64]
        imports.sort(key=lambda item: 0 if ',' not in item[0] else 1)
        seen = set()
        for names, path in imports:
            if len(names) > 2400:
                continue
            address = self._script_url(entry, path)
            if not address or address in seen:
                continue
            if len(seen) >= 20:
                break
            seen.add(address)
            script = self._fetch(address, root + '/')
            access = self._parse_config(script)
            if not access:
                continue
            access['base'] = base
            access['domain'] = root
            access['identifier'] = binascii.hexlify(os.urandom(16)).decode('ascii')
            access['loadedAt'] = time.time()
            return access
        raise ValueError('野果接口配置已变化，暂时无法解码，请稍后重试')

    def _configuration(self):
        access = self._access
        if access and time.time() - access.get('loadedAt', 0) < 3600:
            return access
        access = self._handshake()
        self._domain = access.get('domain') or self._domain
        self._access = access
        return access

    @staticmethod
    def _signature_field(value):
        if isinstance(value, bool):
            return 'true' if value else 'false'
        if isinstance(value, str):
            return value
        if isinstance(value, float) and value == int(value):
            return str(int(value))
        if isinstance(value, (int, float)):
            return str(value)
        return None

    @staticmethod
    def _response_signature(envelope, sign_key):
        names = []
        for name, value in envelope.items():
            if name in ('sign', '_ver') or value is None:
                continue
            names.append(name)
        names.sort()
        pieces = []
        for name in names:
            field = YeguoSite._signature_field(envelope.get(name))
            if field is None:
                raise ValueError('野果接口响应校验或解码失败')
            if name == 'data':
                field = field.replace(' ', '+')
            pieces.append(name + '=' + field)
        digest = hashlib.sha256('&'.join(pieces).encode('utf-8') + sign_key).digest()
        return hashlib.md5(binascii.hexlify(digest)).hexdigest()

    def _decode(self, text, access):
        envelope = json_loads(text)
        if not isinstance(envelope, dict):
            raise ValueError('野果接口响应校验或解码失败')
        signature = map_str(envelope, 'sign').strip()
        if signature:
            expected = self._response_signature(envelope, access['signKey'])
            if expected != signature.lower():
                raise ValueError('野果接口响应校验或解码失败')
        encoded = envelope.get('data')
        if isinstance(encoded, str):
            blob = b64_decode(encoded.strip().replace(' ', '+'))
            if not blob or len(blob) % 16:
                raise ValueError('野果接口响应校验或解码失败')
            plain = aes_cbc_decrypt(access['key'], access['iv'], blob)
            if isinstance(plain, bytes):
                plain = plain.decode('utf-8', 'ignore')
            decoded = json_loads(plain)
            if not isinstance(decoded, dict):
                raise ValueError('野果接口响应校验或解码失败')
            return decoded
        return envelope

    def _headers(self):
        root = self._site()
        return {
            'User-Agent': self.UA,
            'Content-Type': 'application/x-www-form-urlencoded',
            'Accept': 'application/json, text/plain, */*',
            'Origin': root,
            'Referer': root + '/',
        }

    def _call(self, route, parameters=None):
        for attempt in range(2):
            access = self._configuration()
            values = {
                'bundleId': 'com.pwa.mater',
                'version': '1.3.2',
                'oauth_type': 'web',
                'language': 'zh',
                'via': 'pwa',
                'oauth_id': access['identifier'],
                'trace_id': access['identifier'],
                'token': '',
            }
            for name, value in (parameters or {}).items():
                values[name] = value
            body = urlencode(sorted(values.items()))
            status, text = http_request(access['base'] + route, 'POST',
                                        headers=self._headers(), data=body,
                                        timeout=25)
            if status == 0 or status >= 500:
                raise _YeguoSwitch('野果 HTTP %s' % status)
            if status < 200 or status >= 300:
                raise ValueError('野果 HTTP %s: %s' % (status, truncate(text, 200)))
            try:
                payload = self._decode(text, access)
            except ValueError:
                if attempt == 0:
                    self._access = None
                    continue
                raise
            state = map_str(payload, 'status')
            if state != '1':
                if state == '-1':
                    raise ValueError('野果当前内容需要站源授权')
                raise ValueError('野果请求未完成：%s' % first_non_empty(
                    truncate(clean_text(map_str(payload, 'msg')), 180), '请稍后重试'))
            data = payload.get('data')
            if not isinstance(data, dict):
                raise ValueError('野果返回的数据格式无效')
            return data
        raise ValueError('野果接口响应校验或解码失败')

    @staticmethod
    def _integer(value, maximum=100000):
        text = map_str({'v': value}, 'v')
        try:
            number = int(text.strip())
        except (TypeError, ValueError):
            return -1
        if number < 0 or number > maximum:
            return -1
        return number

    @staticmethod
    def _flag(value):
        if isinstance(value, bool):
            return value
        text = map_str({'v': value}, 'v').strip()
        if text == '1':
            return True
        return False

    @classmethod
    def _cover(cls, value, page):
        if isinstance(value, list):
            for entry in value:
                got = cls._cover(entry, page)
                if got:
                    return got
            return ''
        if isinstance(value, dict):
            for inner in ('url', 'contentUrl', 'thumbnailUrl'):
                got = cls._cover(value.get(inner), page)
                if got:
                    return got
            return ''
        if isinstance(value, str):
            item = value.strip()
            if not item or item.startswith('data:') or item.startswith('javascript:'):
                return ''
            if item.startswith('//'):
                item = 'https:' + item
            return _img_proxy_url(resolve_url(page, item), page)
        return ''

    @classmethod
    def _status(cls, row):
        state = map_str(row, 'serialize_status')
        if state == '1':
            return '连载中'
        if state == '2':
            return '已完结'
        return ''

    def _card(self, row, cat_name):
        row = as_dict(row)
        vid = map_str(row, 'video_id', 'id')
        title = map_str(row, 'title')
        if not self.NUMERIC_ID.match(vid) or not title.strip():
            return None
        card = {
            'vod_id': vid,
            'vod_name': truncate(title, 256),
            'vod_pic': self._cover(row.get('cover'), self._site() + '/'),
            'vod_remarks': self._status(row),
            'vod_class': cat_name,
            'vod_content': truncate(map_str(row, 'description'), 500),
        }
        episodes = self._integer(first_non_empty(map_str(row, 'episode_count'),
                                                map_str(row, 'episodes'),
                                                map_str(row, 'total_serial')))
        if episodes > 0:
            if card['vod_remarks']:
                card['vod_remarks'] += ' '
            card['vod_remarks'] += '全%d集' % episodes
        return card

    def _cards(self, rows, cat_name):
        cards = []
        seen = set()
        for row in rows:
            card = self._card(row, cat_name)
            if not card or card['vod_id'] in seen:
                continue
            seen.add(card['vod_id'])
            cards.append(card)
        return cards

    @classmethod
    def _valid_cat(cls, cat):
        field, sep, value = str(cat or '').partition(':')
        if not sep or not cls.NUMERIC_ID.match(value):
            return False
        return field in cls.FILTER_FIELDS

    def listing(self, cat, page):
        cat = str(cat or '').strip()
        if cat and not self._valid_cat(cat):
            print('[野果] 分类参数无效: %s' % cat)
            return []
        page = page if page and page > 0 else 1
        values = {'page': str(page), 'limit': '20'}
        if cat:
            field, _sep, value = cat.partition(':')
            values[field] = value

        def runner():
            data = self._call('/api/theater/exploreList', values)
            rows = [as_dict(row) for row in as_list(data.get('list'))]
            if not rows:
                print('[野果] 目录没有返回内容')
                return []
            cards = self._cards(rows, '推荐')
            if not cards:
                print('[野果] 目录中没有可识别的剧集')
                return []
            return cards

        try:
            return self._invoke(runner)
        except Exception as exc:
            print('[野果] 列表读取失败: %s' % exc)
            return []

    def search(self, wd, page):
        wd = (wd or '').strip()
        if not wd:
            return []
        page = page if page and page > 0 else 1

        def runner():
            data = self._call('/api/search/result', {
                'keyword': wd,
                'tab': 'video',
                'page': str(page),
                'limit': '20',
            })
            rows = [as_dict(row) for row in as_list(data.get('list'))]
            if not rows:
                print('[野果] 搜索没有返回内容')
                return []
            cards = self._cards(rows, '推荐')
            if not cards:
                print('[野果] 搜索中没有可识别的剧集')
                return []
            return cards

        try:
            return self._invoke(runner)
        except Exception as exc:
            print('[野果] 搜索失败: %s' % exc)
            return []

    def _detail(self, vid):
        data = self._call('/api/playlet/detail', {
            'video_id': vid,
            'id': vid,
            'episode_id': '0',
            'related_limit': '0',
        })
        if map_str(data, 'video_id', 'id') != vid:
            raise ValueError('野果详情与请求剧集不符')
        episodes = []
        seen = set()
        for entry in as_list(data.get('episodes')):
            episode = as_dict(entry)
            if self._flag(episode.get('is_adv')):
                continue
            episode_id = map_str(episode, 'id')
            number = self._integer(episode.get('sort'))
            if not self.NUMERIC_ID.match(episode_id) or number < 1 or number in seen:
                continue
            seen.add(number)
            episodes.append({
                'no': number,
                'name': '第 %d 集' % number,
                'url': '%s://%s/%s' % (self.key, vid, episode_id),
            })
        episodes = sort_episodes(episodes)
        if not episodes:
            raise ValueError('野果详情没有返回剧集列表')
        return {
            'vod_id': vid,
            'vod_name': truncate(first_non_empty(map_str(data, 'title'), vid), 256),
            'vod_pic': self._cover(data.get('cover'), self._site() + '/'),
            'vod_class': '短剧',
            'vod_content': truncate(map_str(data, 'description'), 12000),
            'vod_remarks': '共%d集' % len(episodes),
            'episodes': episodes,
        }

    def detail(self, vid):
        vid = str(vid or '').strip()
        if not self.NUMERIC_ID.match(vid):
            print('[野果] 无效的剧集 ID: %s' % vid)
            return {}
        try:
            return self._invoke(lambda: self._detail(vid))
        except Exception as exc:
            print('[野果] 详情读取失败: %s' % exc)
            return {}

    def play(self, url):
        raw = str(url or '').strip()
        prefix = self.key + '://'
        if not raw.startswith(prefix):
            return {'url': raw, 'header': {'User-Agent': self.UA},
                    'parse': 0} if is_http_media(raw) else {}
        rest = raw[len(prefix):]
        if '/' not in rest:
            print('[野果] 播放参数无效')
            return {}
        vid, extra = rest.split('/', 1)
        if not self.NUMERIC_ID.match(vid) or not self.NUMERIC_ID.match(extra):
            print('[野果] 播放参数无效')
            return {}
        def runner():
            data = self._call('/api/playlet/play', {
                'playlet_id': vid,
                'video_id': vid,
                'episode_id': extra,
            })
            if map_str(data, 'playlet_id') != vid or map_str(data, 'id') != extra:
                print('[野果] 返回的播放地址与请求分集不符')
                return {}
            if self._flag(data.get('is_adv')):
                print('[野果] 未返回该集正片，请重试')
                return {}
            number = self._integer(data.get('episode_sort'))
            if number < 1:
                print('[野果] 播放分集编号无效')
                return {}
            referer = self._site() + '/drama/video/' + vid + '/'
            if number > 1:
                referer += 'ep-%d/' % number
            candidates = []
            seen = set()
            for field in ('video_url', 'video_url_h265'):
                address = resolve_url(referer, map_str(data, field))
                if not is_http_media(address) or address in seen:
                    continue
                seen.add(address)
                candidates.append(address)
            if not candidates:
                print('[野果] 未提供该集播放地址，请重试或确认站源权限')
                return {}
            return {
                'url': candidates[0],
                'header': {'User-Agent': self.UA, 'Referer': referer},
                'parse': 0,
            }

        try:
            return self._invoke(runner)
        except Exception as exc:
            print('[野果] 播放读取失败: %s' % exc)
            return {}

try:
    import requests
except ImportError:
    requests = None

try:
    import urllib.request as _urlrequest
    import ssl as _ssl
except ImportError:
    _urlrequest = None
    _ssl = None

try:
    import gzip as _gzip
except ImportError:
    _gzip = None

if requests is None:
    requests = CompatRequests()

try:
    from base.spider import Spider as _BaseSpider
except ImportError:
    class _BaseSpider(object):
        pass

try:
    import warnings
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    warnings.filterwarnings('ignore', message='Unverified HTTPS request')
except Exception:
    pass

_AES_SBOX, _AES_INV_SBOX = _aes_tables()

class XiangjiaoSite(object):

    key = 'xiangjiao'
    name = '香蕉'
    site = 'https://xiangjiaoai.ai'

    PAGE_SIZE = 30
    MAX_LIMIT = 300
    TOKEN_TTL = 30 * 60
    CAT_TTL = 10 * 60

    CATS = [
        ('hot', '精选推荐'),
        ('theater', '全部剧场'),
        ('cat:明星换脸', '明星换脸'),
        ('cat:影视魔改', '影视魔改'),
        ('cat:动漫游戏', '动漫游戏'),
        ('cat:穿越重生', '穿越重生'),
        ('cat:系统异能', '系统异能'),
        ('cat:仙侠修真', '仙侠修真'),
        ('cat:古装权谋', '古装权谋'),
    ]

    def __init__(self):
        self.token = ''
        self.token_at = 0
        self.cat_ids = {}
        self.cat_at = 0
        self.last_status = 0
        self.device_id = self._random_device_id()

    @staticmethod
    def _random_device_id():
        try:
            return os.urandom(16).hex()
        except Exception:
            return ''.join(random.choice('0123456789abcdef') for _ in range(32))

    def cats(self):
        return [{'type_id': item[0], 'type_name': item[1]} for item in self.CATS]

    def _headers(self, token=''):
        headers = {
            'Accept': 'application/json, text/plain, */*',
            'Content-Type': 'application/json',
            'Origin': self.site,
            'User-Agent': UA_DESKTOP,
        }
        if token:
            headers['Authorization'] = 'Bearer ' + token
        return headers

    def _call(self, method, path, payload=None, token=''):
        target = self.site + path
        self.last_status = 0
        try:
            status, text = http_request(target, method, headers=self._headers(token),
                                        json_body=payload, timeout=20)
        except Exception as exc:
            print('[香蕉] 请求失败: %s' % exc)
            return None
        self.last_status = status
        if status >= 400 or not text:
            print('[香蕉] 接口 %s 返回 %s: %s' % (path, status, truncate(text, 120)))
            return None
        body = json_loads(text)
        if not isinstance(body, dict):
            print('[香蕉] 接口 %s 响应解析失败' % path)
            return None
        return body

    def _ensure_token(self):
        if self.token and time.time() - self.token_at < self.TOKEN_TTL:
            return self.token
        body = self._call('POST', '/api/guest-sessions',
                          {'device_id': self.device_id})
        if body is None:
            return ''
        token = first_non_empty(map_str(as_dict(body.get('data')), 'access_token'),
                                map_str(body, 'access_token'))
        if not token:
            print('[香蕉] 未返回访客令牌')
            return ''
        self.token = token
        self.token_at = time.time()
        return token

    def _drop_token(self):
        self.token = ''
        self.token_at = 0

    def _category_id(self, name):
        if self.cat_ids and time.time() - self.cat_at < self.CAT_TTL:
            got = self.cat_ids.get(name)
            if got:
                return got
        body = self._call('GET', '/api/categories')
        ids = {}
        if body is not None:
            for item in as_list(body.get('data')):
                item = as_dict(item)
                cid = map_str(item, 'id')
                cname = clean_text(map_str(item, 'name'))
                if cid and cname:
                    ids[cname] = cid
        if ids:
            self.cat_ids = ids
            self.cat_at = time.time()
        got = ids.get(name)
        if not got:
            print('[香蕉] 无此分类: %s' % name)
        return got or ''

    def _items(self, cat, page):
        if cat == 'hot':
            if page > 1:
                return []
            body = self._call('GET', '/api/home/hot')
            if body is None:
                return []
            return as_list(as_dict(body.get('data')).get('items'))

        params = {}
        limit = self.PAGE_SIZE * page
        if limit > self.MAX_LIMIT:
            limit = self.MAX_LIMIT
        params['limit'] = str(limit)
        if cat.startswith('cat:'):
            cid = self._category_id(cat[len('cat:'):].strip())
            if not cid:
                return []
            params['category_id'] = cid
        body = self._call('GET', '/api/theater?' + urlencode(params))
        if body is None:
            return []
        items = as_list(as_dict(body.get('data')).get('items'))
        if page > 1:
            start = (page - 1) * self.PAGE_SIZE
            if start >= len(items):
                return []
            items = items[start:]
        if len(items) > self.PAGE_SIZE:
            items = items[:self.PAGE_SIZE]
        return items

    def _card(self, item):
        item = as_dict(item)
        nested = as_dict(item.get('drama'))
        if nested:
            merged = dict(nested)
            if not map_str(merged, 'image_url'):
                cover = map_str(item, 'image_url')
                if cover:
                    merged['image_url'] = cover
            item = merged
        vid = map_str(item, 'id')
        title = clean_text(map_str(item, 'title'))
        if not vid or not title:
            return None
        total = atoi(first_non_empty(map_str(item, 'episode_count'),
                                     map_str(item, 'latest_episode_number')), 0)
        finished = map_str(item, 'serial_status').lower() == 'finished'
        remarks = ''
        if finished and total > 0:
            remarks = '全%d集' % total
        elif total > 0:
            remarks = '更新至%d集' % total
        elif finished:
            remarks = '已完结'
        tags = []
        for tag in as_list(item.get('tags')):
            if len(tags) >= 4:
                break
            tag_name = clean_text(map_str(as_dict(tag), 'name'))
            if tag_name:
                tags.append(tag_name)
        return {
            'vod_id': vid,
            'vod_name': title,
            'vod_pic': first_non_empty(map_str(item, 'cover_url'),
                                       map_str(item, 'image_url')),
            'vod_remarks': remarks,
            'vod_class': ','.join(tags),
            'vod_content': truncate(clean_text(map_str(item, 'description')), 200),
        }

    def _cards(self, items):
        cards = []
        seen = set()
        for item in items:
            card = self._card(item)
            if not card or card['vod_id'] in seen:
                continue
            seen.add(card['vod_id'])
            cards.append(card)
        return cards

    def listing(self, cat, page):
        page = page if page and page > 0 else 1
        cat = str(cat or '').strip() or self.CATS[0][0]
        try:
            cards = self._cards(self._items(cat, page))
        except Exception as exc:
            print('[香蕉] 列表读取失败: %s' % exc)
            return []
        if not cards:
            if page > 1:
                return []
            print('[香蕉] 列表无内容: %s' % cat)
        return cards

    def search(self, wd, page):
        wd = str(wd or '').strip()
        if not wd:
            return []
        page = page if page and page > 0 else 1
        path = '/api/search?q=%s&page=%d' % (quote(wd, safe=''), page)
        try:
            body = self._call('GET', path)
            cards = self._cards(as_list(as_dict(as_dict(body).get('data')).get('items')))
        except Exception as exc:
            print('[香蕉] 搜索失败: %s' % exc)
            return []
        if not cards:
            print('[香蕉] 搜索无结果: %s' % wd)
        return cards

    def detail(self, vid):
        vid = str(vid or '').strip()
        if '/' in vid:
            vid = vid[vid.rfind('/') + 1:]
        if not vid:
            return {}
        escaped = quote(vid, safe='')
        try:
            info = as_dict(as_dict(self._call('GET', '/api/dramas/' + escaped)).get('data'))
            eps = as_list(as_dict(self._call('GET', '/api/dramas/' + escaped
                                             + '/episodes')).get('data'))
        except Exception as exc:
            print('[香蕉] 详情读取失败: %s' % exc)
            return {}
        if not eps:
            print('[香蕉] 详情无剧集: %s' % vid)
            return {}
        episodes = []
        seen = set()
        for ep in eps:
            ep = as_dict(ep)
            ep_id = clean_text(map_str(ep, 'id'))
            if not ep_id:
                continue
            no = atoi(map_str(ep, 'episode_number'), len(episodes) + 1)
            if no in seen:
                continue
            seen.add(no)
            name = clean_text(map_str(ep, 'title')) or ('第%d集' % no)
            episodes.append({
                'no': no,
                'name': name,
                'url': '%s://%s/%s' % (self.key, vid, ep_id),
            })
        episodes = sort_episodes(episodes)
        if not episodes:
            print('[香蕉] 详情无有效剧集: %s' % vid)
            return {}
        tags = []
        for tag in as_list(info.get('tags')):
            if len(tags) >= 5:
                break
            tag_name = clean_text(map_str(as_dict(tag), 'name'))
            if tag_name:
                tags.append(tag_name)
        return {
            'vod_id': vid,
            'vod_name': clean_text(map_str(info, 'title')) or ('香蕉剧集 ' + vid),
            'vod_pic': map_str(info, 'cover_url'),
            'vod_content': clean_text(map_str(info, 'description')),
            'vod_class': ','.join(tags),
            'vod_remarks': '共%d集' % len(episodes),
            'episodes': episodes,
        }

    def play(self, url):
        raw = str(url or '')
        prefix = self.key + '://'
        drama_id, ep_id = '', ''
        if raw.startswith(prefix):
            rest = raw[len(prefix):]
            if '/' in rest:
                drama_id, ep_id = rest.split('/', 1)
        elif '/episodes/' in raw:
            parts = [part for part in raw.strip('/').split('/') if part]
            if len(parts) >= 2:
                drama_id, ep_id = parts[-2], parts[-1]
        if not ep_id:
            print('[香蕉] 播放参数无效: %s' % raw)
            return {}
        for _ in range(2):
            token = self._ensure_token()
            if not token:
                return {}
            body = self._call('POST', '/api/playback/sessions',
                              {'episode_id': ep_id}, token)
            if body is None:
                if self.last_status == 401:
                    self._drop_token()
                    continue
                return {}
            media = as_dict(as_dict(body.get('data')).get('media'))
            src = clean_text(map_str(media, 'manifest_url'))
            if not src:
                print('[香蕉] 播放会话未返回媒体地址')
                return {}
            if not is_http_media(src):
                print('[香蕉] 播放地址非法: %s' % truncate(src, 80))
                return {}
            return {
                'url': src,
                'header': {'User-Agent': UA_DESKTOP, 'Referer': self.site + '/'},
                'parse': 0,
            }
        print('[香蕉] 播放会话失败（令牌重试后仍无效）')
        return {}

try:
    import requests
except ImportError:
    requests = None

try:
    import urllib.request as _urlrequest
    import ssl as _ssl
except ImportError:
    _urlrequest = None
    _ssl = None

try:
    import gzip as _gzip
except ImportError:
    _gzip = None

if requests is None:
    requests = CompatRequests()

try:
    from base.spider import Spider as _BaseSpider
except ImportError:
    class _BaseSpider(object):
        pass

try:
    import warnings
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    warnings.filterwarnings('ignore', message='Unverified HTTPS request')
except Exception:
    pass

_AES_SBOX, _AES_INV_SBOX = _aes_tables()

class Huangguo2Site(object):

    key = 'huangguo2'
    name = '黄果2'
    site = 'https://huangguo.video'

    CATS = [
        {'type_id': 'all', 'type_name': '全部'},
        {'type_id': '3', 'type_name': '连续剧'},
        {'type_id': '1', 'type_name': 'MV/音乐剧'},
        {'type_id': '2', 'type_name': '短片'},
        {'type_id': '4', 'type_name': '片段'},
        {'type_id': 'updates', 'type_name': '最近更新'},
        {'type_id': 'ranking', 'type_name': '排行榜'},
    ]

    RE_CARD_START = re.compile(r'<article\s+class="[^"]*\bvideo-card\b[^"]*"[^>]*>', re.I)
    RE_CARD_HREF = re.compile(r'href="(?:https?://[^/"]+)?/(series|video)/([0-9a-zA-Z]+)', re.I)
    RE_IMG_SRC = re.compile(r'<img[^>]*\bsrc="([^"]+)"', re.I)
    RE_IMG_ALT = re.compile(r'<img[^>]*\balt="([^"]*)"', re.I)
    RE_REMARKS = re.compile(r'bg-black/(?:50|55)[^>]*>([^<]+)<', re.I)
    RE_CARD_META = re.compile(r'<p\s+class="[^"]*\bmt-2\b[^"]*font-mono-meta[^"]*"[^>]*>([^<]*)<', re.I)
    RE_TITLE = re.compile(r'<p[^>]*class="[^"]*\bmt-2\.5\b[^"]*"[^>]*>([\s\S]*?)</p>', re.I | re.S)
    RE_H1 = re.compile(r'<h1[^>]*>([\s\S]*?)</h1>', re.I | re.S)
    RE_OG_IMAGE = re.compile(r'<meta[^>]*property="og:image"[^>]*content="([^"]+)"', re.I)
    RE_META_DESC = re.compile(r'<meta[^>]*name="description"[^>]*content="([^"]*)"', re.I)
    RE_VIDEO_LINK = re.compile(r'href="(?:https?://[^/"]+)?/video/([0-9a-zA-Z]+)', re.I)
    RE_DATA_HLS = re.compile(r'data-hls="([^"]+)"', re.I)

    def _headers(self):
        return browser_headers(referer=self.site + '/', ua=UA_ANDROID,
                               mobile=True)

    def _fetch(self, path):
        try:
            status, text = http_get(self.site + path, headers=self._headers(),
                                    timeout=20, retries=2)
        except Exception as exc:
            print('[黄果2] 请求失败: %s' % exc)
            return 0, ''
        return status, text or ''

    @staticmethod
    def _abs(raw):
        raw = (raw or '').strip()
        if not raw:
            return ''
        if raw.startswith('http://') or raw.startswith('https://'):
            return raw
        return Huangguo2Site.site + '/' + raw.lstrip('/')

    @staticmethod
    def _cat_name(cat):
        for item in Huangguo2Site.CATS:
            if item['type_id'] == cat:
                return item['type_name']
        return ''

    @staticmethod
    def _split_id(vid):
        vid = str(vid or '').strip().strip('/')
        if '-' not in vid:
            return '', '', False
        kind, key = vid.split('-', 1)
        if not key or kind not in ('series', 'video'):
            return '', '', False
        return kind, key, True

    def _cards(self, page_html, cat_name):
        starts = [m.start() for m in self.RE_CARD_START.finditer(page_html)]
        cards = []
        seen = set()
        for index, begin in enumerate(starts):
            end = starts[index + 1] if index + 1 < len(starts) else len(page_html)
            block = page_html[begin:end]
            href = self.RE_CARD_HREF.search(block)
            if not href:
                continue
            vid = href.group(1) + '-' + href.group(2)
            if vid in seen:
                continue
            title = ''
            alt = self.RE_IMG_ALT.search(block)
            if alt:
                title = clean_text(html_unescape(alt.group(1)))
            if not title:
                node = self.RE_TITLE.search(block)
                if node:
                    title = clean_text(strip_tags(node.group(1)))
            if not title:
                continue
            seen.add(vid)
            card = {
                'vod_id': vid,
                'vod_name': title,
                'vod_pic': '',
                'vod_remarks': '',
                'vod_class': cat_name,
                'vod_content': '',
            }
            img = self.RE_IMG_SRC.search(block)
            if img:
                card['vod_pic'] = self._abs(img.group(1))
            marks = self.RE_REMARKS.search(block)
            if marks:
                card['vod_remarks'] = clean_text(marks.group(1))
            meta = self.RE_CARD_META.search(block)
            if meta:
                text = clean_text(meta.group(1))
                pos = text.find(' · ')
                if pos > 0:
                    card['vod_class'] = text[:pos]
                    card['vod_content'] = text
                else:
                    card['vod_content'] = text
            cards.append(card)
        return cards

    def cats(self):
        return [dict(item) for item in self.CATS]

    def listing(self, cat, page):
        try:
            page = page if page and page > 0 else 1
            cat = (cat or '').strip() or 'all'
            if cat in ('updates', 'ranking'):
                path = '/%s?page=%d' % (cat, page)
            else:
                path = '/videos?category=%s&page=%d' % (quote(cat, safe=''), page)
            status, body = self._fetch(path)
            if not body:
                if page > 1 and status == 404:
                    return []
                print('[黄果2] 列表读取失败: %s' % path)
                return []
            cards = self._cards(body, self._cat_name(cat))
            if not cards:
                if page > 1:
                    return []
                print('[黄果2] 列表无内容: %s' % path)
            return cards
        except Exception as exc:
            print('[黄果2] 列表读取失败: %s' % exc)
            return []

    def search(self, wd, page):
        wd = (wd or '').strip()
        if not wd:
            return []
        try:
            page = page if page and page > 0 else 1
            path = '/search?q=' + quote(wd, safe='')
            if page > 1:
                path += '&page=%d' % page
            status, body = self._fetch(path)
            if not body:
                print('[黄果2] 搜索失败: %s' % path)
                return []
            return self._cards(body, '')
        except Exception as exc:
            print('[黄果2] 搜索失败: %s' % exc)
            return []

    def detail(self, vid):
        kind, key, ok = self._split_id(vid)
        if not ok:
            print('[黄果2] 卡片 ID 无效: %s' % vid)
            return {}
        try:
            status, body = self._fetch('/%s/%s' % (kind, quote(key, safe='')))
            if not body:
                print('[黄果2] 详情读取失败 HTTP %s: %s' % (status, vid))
                return {}
            title = ''
            node = self.RE_H1.search(body)
            if node:
                title = clean_text(html_unescape(strip_tags(node.group(1))))
            desc = ''
            node = self.RE_META_DESC.search(body)
            if node:
                desc = clean_text(html_unescape(node.group(1)))
            pic = ''
            node = self.RE_OG_IMAGE.search(body)
            if node:
                pic = self._abs(node.group(1))
            episodes = []
            if kind == 'video':
                episodes.append({'no': 1, 'name': '播放',
                                 'url': '%s://video-%s/1' % (self.key, key)})
            else:
                seen = set()
                for link in self.RE_VIDEO_LINK.finditer(body):
                    sub = link.group(1)
                    if sub in seen:
                        continue
                    seen.add(sub)
                    order = len(episodes) + 1
                    episodes.append({
                        'no': order,
                        'name': '第%d集' % order,
                        'url': '%s://video-%s/1' % (self.key, sub),
                    })
            episodes = sort_episodes(episodes)
            if not episodes:
                print('[黄果2] 详情页无剧集: %s' % vid)
                return {}
            return {
                'vod_id': '%s-%s' % (kind, key),
                'vod_name': title or ('黄果2 ' + key),
                'vod_pic': pic,
                'vod_content': truncate(desc, 400),
                'vod_class': '黄果2',
                'vod_remarks': '共%d集' % len(episodes),
                'episodes': episodes,
            }
        except Exception as exc:
            print('[黄果2] 详情读取失败: %s' % exc)
            return {}

    def play(self, url):
        raw = str(url or '')
        prefix = self.key + '://'
        ok = False
        if raw.startswith(prefix):
            rest = raw[len(prefix):]
            if '/' in rest:
                vid, _extra = rest.split('/', 1)
                if vid and _extra:
                    ok = True
        if not ok:
            vid = raw.strip().strip('/')
            pos = vid.find('huangguo.video/')
            if pos >= 0:
                vid = vid[pos + len('huangguo.video/'):]
            vid = vid.strip('/')
            pos = vid.rfind('/')
            if pos >= 0:
                vid = vid[:pos]
            if not (vid.startswith('video-') or vid.startswith('series-')):
                print('[黄果2] 播放参数无效')
                return {}
        kind, key, valid = self._split_id(vid)
        if not valid:
            print('[黄果2] 播放参数无效')
            return {}
        if kind != 'video':
            print('[黄果2] 连续剧请选择具体分集')
            return {}
        try:
            status, body = self._fetch('/video/%s' % quote(key, safe=''))
            if not body:
                print('[黄果2] 播放页读取失败 HTTP %s: %s' % (status, key))
                return {}
            node = self.RE_DATA_HLS.search(body)
            if not node or '.m3u8' not in node.group(1):
                print('[黄果2] 播放页无媒体地址: %s' % key)
                return {}
            src = self._abs(node.group(1))
            if not is_http_media(src):
                print('[黄果2] 播放地址无效: %s' % key)
                return {}
            return {
                'url': src,
                'header': {'User-Agent': UA_DESKTOP, 'Referer': self.site + '/'},
                'parse': 0,
            }
        except Exception as exc:
            print('[黄果2] 播放读取失败: %s' % exc)
            return {}

class HuangguoSite(Huangguo2Site):

    key = 'huangguo'
    name = '黄果'

try:
    import requests
except ImportError:
    requests = None

try:
    import urllib.request as _urlrequest
    import ssl as _ssl
except ImportError:
    _urlrequest = None
    _ssl = None

try:
    import gzip as _gzip
except ImportError:
    _gzip = None

if requests is None:
    requests = CompatRequests()

try:
    from base.spider import Spider as _BaseSpider
except ImportError:
    class _BaseSpider(object):
        pass

try:
    import warnings
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    warnings.filterwarnings('ignore', message='Unverified HTTPS request')
except Exception:
    pass

_AES_SBOX, _AES_INV_SBOX = _aes_tables()

class HuangguooldSite(object):

    key = 'huangguoold'
    name = '黄果旧版'
    site = 'https://dr6skssi3nxbk.cloudfront.net'

    FRONTEND_BASE = 'https://d2pypzndaqisk.cloudfront.net'
    API_BASE = 'https://dr6skssi3nxbk.cloudfront.net'
    API_FALLBACKS = [
        'https://dr6skssi3nxbk.cloudfront.net',
        'https://d18ka9rqpfd3lo.cloudfront.net',
        'https://d37n0wjehmw08f.cloudfront.net',
    ]
    CDN_BASE = 'https://sjljsla.lkkwip.cn'
    COVER_HOST = 'https://zzzznnn.lkkwip.cn'
    COVER_HOST2 = 'https://pic.zdmhyg.cn'

    UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
          '(KHTML, like Gecko) Chrome/120 Safari/537.36')

    DEV_ID_HOLDER = '00000000000000000000000000000'
    DEFAULT_DEV_ID = '36835C92263168B91790238904835'
    X_USER_AGENT = ('BuildID=com.abc.Butterfly;SysType=ios;DevID=' + DEV_ID_HOLDER +
                    ';Ver=1.0.0;DevType=iPhone;DeviceBrand=APPLE;DeviceModel=iPhone;'
                    'SystemName=iOS;SystemVersion=18.7;Terminal=1;IsH5=1;Sid=' +
                    DEV_ID_HOLDER)

    TAB_FALLBACK_IDS = [
        '6a928dfedb2cc815f75d7213',
        '6a928de7db2cc815f75d720f',
        '6a928dccdb2cc815f75d720b',
        '6a928d54db2cc815f75d7201',
    ]

    CATS = [
        {'type_id': 'tab-0', 'type_name': '黄果原创'},
        {'type_id': 'tab-1', 'type_name': 'AI成人短剧'},
        {'type_id': 'tab-2', 'type_name': 'AI成人漫剧'},
        {'type_id': 'tab-3', 'type_name': 'AI魔改'},
    ]

    RE_SCRIPT = re.compile(r'<script\b[^>]*\bsrc=["\']([^"\']+)["\']', re.I)
    RE_QUOTE = re.compile(r'["\']([A-Za-z]+)["\']\s*[:,]\s*["\']([^"\'\\\r\n]+)["\']')

    PROTO_TTL = 24 * 3600
    API_COOLDOWN = 60
    TAB_TTL = 600
    DETAIL_TTL = 180

    class TokenExpired(Exception):
        pass

    def __init__(self):
        self._proto = {}
        self._proto_at = 0
        self._dev_id = ''
        self._token = ''
        self._api_base = ''
        self._api_fail = {}
        self._tabs = []
        self._tabs_at = 0
        self._details = {}

    @staticmethod
    def _pad(raw):
        pad_len = 16 - len(raw) % 16
        return bytes(raw) + bytes([pad_len] * pad_len)

    @staticmethod
    def _unpad(raw):
        if not raw or len(raw) % 16 != 0:
            return raw
        pad_len = raw[-1]
        if pad_len <= 0 or pad_len > 16 or pad_len > len(raw):
            return raw
        for byte in raw[len(raw) - pad_len:]:
            if byte != pad_len:
                return raw
        return raw[:len(raw) - pad_len]

    @staticmethod
    def _cbc_encrypt(key, iv, plain):
        cipher = StdAES(key)
        prev = bytes(iv[:16])
        out = b''
        for off in range(0, len(plain), 16):
            block = bytes(a ^ b for a, b in zip(plain[off:off + 16], prev))
            prev = cipher.encrypt_block(block)
            out += prev
        return out

    @staticmethod
    def _cbc_decrypt(key, iv, body):
        cipher = StdAES(key)
        prev = bytes(iv[:16])
        out = b''
        for off in range(0, len(body), 16):
            block = body[off:off + 16]
            plain = cipher.decrypt_block(block)
            out += bytes(a ^ b for a, b in zip(plain, prev))
            prev = block
        return out

    def _encrypt_params(self, params):
        param_key = self._proto.get('paramKey', '')
        param_iv = self._proto.get('paramIv', '')
        if len(param_key) != 16 or len(param_iv) != 16:
            raise ValueError('黄果旧版接口协议未就绪')
        parts = []
        for name in sorted(params.keys()):
            parts.append(json.dumps(name, ensure_ascii=False) + ':' +
                         json.dumps(params[name], ensure_ascii=False))
        plain = self._pad(('{' + ','.join(parts) + '}').encode('utf-8'))
        sealed = self._cbc_encrypt(param_key.encode('utf-8'),
                                   param_iv.encode('utf-8'), plain)
        return b64_encode(sealed)

    @staticmethod
    def _derive_key_iv(interface_key, nonce):
        joined = interface_key.encode('utf-8') + nonce
        half = len(joined) // 2
        seed = hashlib.sha256(joined).digest()
        mixed = seed[8:24]
        first = mixed + joined[:half]
        second = joined[half:] + mixed
        d = hashlib.sha256(first).digest()
        f = hashlib.sha256(second).digest()
        key = d[:8] + f[8:24] + d[24:]
        iv = f[:4] + d[12:20] + f[28:]
        return key, iv

    def _decrypt_response(self, data):
        interface_key = self._proto.get('interfaceKey', '')
        if not interface_key:
            raise ValueError('黄果旧版接口协议未就绪')
        blob = b64_decode(str(data or '').strip())
        if not blob or len(blob) < 12:
            raise ValueError('黄果旧版响应体格式异常')
        key, iv = self._derive_key_iv(interface_key, blob[:12])
        body = blob[12:]
        body = body[:len(body) - len(body) % 16]
        if not body:
            return b''
        return self._unpad(self._cbc_decrypt(key, iv, body))

    def _ensure_protocol(self):
        param_key = self._proto.get('paramKey', '')
        param_iv = self._proto.get('paramIv', '')
        ready = bool(self._proto.get('interfaceKey')) and len(param_key) == 16 and len(param_iv) == 16
        if ready and time.time() - self._proto_at < self.PROTO_TTL:
            return
        status, text = http_get(self.FRONTEND_BASE + '/',
                                headers=self._headers(), timeout=20)
        if status >= 400 or not text:
            raise ValueError('获取黄果旧版官网失败: HTTP %s' % status)
        for src in self.RE_SCRIPT.findall(text):
            src = (src or '').strip()
            if '/main-' not in src or not src.endswith('.js'):
                continue
            script_url = resolve_url(self.FRONTEND_BASE + '/', src)
            script_status, script = http_get(script_url,
                                             headers=self._headers(), timeout=20)
            if script_status >= 400 or not script:
                raise ValueError('获取黄果旧版协议脚本失败: HTTP %s' % script_status)
            values = {}
            for name, value in self.RE_QUOTE.findall(script):
                if name not in values:
                    values[name] = value
            proto = {
                'interfaceKey': values.get('interfaceKey', ''),
                'paramKey': values.get('parameterKey', ''),
                'paramIv': values.get('parameterIv', ''),
            }
            if (not proto['interfaceKey'] or len(proto['paramKey']) != 16
                    or len(proto['paramIv']) != 16):
                raise ValueError('黄果旧版官网协议已变化，无法识别接口参数')
            self._proto = proto
            self._proto_at = time.time()
            return
        raise ValueError('黄果旧版官网未提供可识别的接口脚本')

    @staticmethod
    def _valid_dev_id(value):
        value = str(value or '')
        if len(value) != 29:
            return False
        try:
            binascii.unhexlify(value[:16])
            int(value[16:])
        except (TypeError, ValueError, binascii.Error):
            return False
        return True

    @staticmethod
    def _valid_token(value):
        value = str(value or '')
        if not value or len(value) > 4096:
            return False
        for char in value:
            if char in ' \r\n\t':
                return False
        return True

    def _current_dev_id(self):
        if not self._dev_id:
            self._dev_id = self.DEFAULT_DEV_ID
        return self._dev_id

    def _rotate_dev_id(self):
        fresh = '%016X' % random.getrandbits(64) + str(int(time.time() * 1000))
        if not fresh:
            return
        self._dev_id = fresh
        self._token = ''

    def _login(self, dev_id):
        data = self._raw('POST', '/api/app/mine/login/h5', {
            'devID': dev_id,
            'sysType': 'ios',
            'isAppStore': False,
        }, '')
        token = map_str(as_dict(data), 'token')
        if not self._valid_token(token):
            raise ValueError('黄果旧版访客登录未返回令牌')
        return token

    def _ensure_token(self, force=False):
        if force:
            self._token = ''
        if self._token:
            return self._token
        dev_id = self._current_dev_id()
        try:
            token = self._login(dev_id)
        except Exception as exc:
            if dev_id != self.DEFAULT_DEV_ID:
                raise
            self._rotate_dev_id()
            token = self._login(self._current_dev_id())
        self._token = token
        return token

    def _headers(self, with_json=False):
        headers = {
            'Accept': 'application/json',
            'Temp': 'test',
            'Origin': self.FRONTEND_BASE,
            'Referer': self.FRONTEND_BASE + '/',
            'User-Agent': self.UA,
        }
        if with_json:
            headers['Content-Type'] = 'application/json'
        return headers

    def _x_user_agent(self):
        return self.X_USER_AGENT.replace('DevID=' + self.DEV_ID_HOLDER,
                                         'DevID=' + self._current_dev_id(), 1)

    def _api_base_for(self):
        if self._api_base:
            return self._api_base
        last_err = ''
        for candidate in self.API_FALLBACKS:
            if time.time() < self._api_fail.get(candidate, 0):
                continue
            status, text = http_get(candidate + '/api/app/ping/check',
                                    headers=self._headers(), timeout=6)
            if status >= 400 or not text:
                last_err = '%s 健康检查失败' % candidate
                continue
            envelope = as_dict(json_loads(text))
            if to_int(envelope.get('code'), 0) != 200:
                last_err = '%s 健康检查失败' % candidate
                continue
            self._api_base = candidate
            return candidate
        raise ValueError('黄果旧版接口域名不可用: %s' % (last_err or '候选域名均在冷却中'))

    def _invalidate_api(self, base):
        self._api_fail[base] = time.time() + self.API_COOLDOWN
        if self._api_base == base:
            self._api_base = ''

    def _raw(self, method, path, params, token):
        self._ensure_protocol()
        base = self._api_base_for()
        target = base + path
        headers = self._headers()
        headers['X-User-Agent'] = self._x_user_agent()
        if token:
            headers['Authorization'] = token
        json_body = None
        if params is not None:
            sealed = self._encrypt_params(params)
            if method == 'GET':
                target += ('&' if '?' in target else '?') + 'data=' + quote(sealed, safe='')
            else:
                json_body = {'data': sealed}
                headers['Content-Type'] = 'application/json'
        status, text = http_request(target, method, headers=headers,
                                    json_body=json_body, timeout=20)
        if status == 0 and not text:
            raise ValueError('黄果旧版接口请求失败: %s' % target)
        if status >= 500:
            self._invalidate_api(base)
            raise ValueError('黄果旧版接口 HTTP %s' % status)
        envelope = as_dict(json_loads(text))
        if not envelope:
            raise ValueError('黄果旧版接口响应无法解析')
        code = to_int(envelope.get('code'), 0)
        if code != 200:
            if code in (5005, 5009):
                raise self.TokenExpired('黄果旧版令牌失效（%s）' % code)
            raise ValueError('黄果旧版接口返回错误: %s'
                             % (map_str(envelope, 'msg') or str(code)))
        data = envelope.get('data')
        if envelope.get('hash') and isinstance(data, str) and data.strip():
            plain = self._decrypt_response(data)
            data = json_loads(plain.decode('utf-8', 'ignore'))
        return data

    def _call(self, method, path, params=None):
        token = self._ensure_token(False)
        try:
            return self._raw(method, path, params, token)
        except self.TokenExpired:
            pass
        token = self._ensure_token(True)
        return self._raw(method, path, params, token)

    def cats(self):
        return [dict(item) for item in self.CATS]

    def _cat_name(self, cat):
        for item in self.CATS:
            if item['type_id'] == cat:
                return item['type_name']
        return ''

    def _tabs(self):
        if self._tabs and time.time() - self._tabs_at < self.TAB_TTL:
            return self._tabs
        data = self._call('GET', '/api/app/playlet-tab/all')
        items = []
        for raw in as_list(as_dict(data).get('list')):
            item = as_dict(raw)
            tab_id = map_str(item, 'id').strip()
            if tab_id:
                items.append({'id': tab_id, 'name': clean_text(map_str(item, 'name'))})
        if not items:
            raise ValueError('黄果旧版接口未返回分类')
        self._tabs = items
        self._tabs_at = time.time()
        return items

    def _tab_id(self, cat):
        cat = str(cat or '').strip()
        if not cat:
            cat = self.CATS[0]['type_id']
        if cat.startswith('tab:'):
            return cat[4:]
        index = 0
        if cat.startswith('tab-'):
            parsed = atoi(cat[4:], -1)
            if parsed >= 0:
                index = parsed
        try:
            tabs = self._tabs()
        except Exception:
            tabs = []
        if index < len(tabs):
            return tabs[index]['id']
        if index < len(self.TAB_FALLBACK_IDS):
            return self.TAB_FALLBACK_IDS[index]
        raise ValueError('黄果旧版分类不存在: %s' % cat)

    @staticmethod
    def _cover_url(raw):
        raw = str(raw or '').strip()
        if not raw:
            return ''
        if raw.startswith('//'):
            raw = 'https:' + raw
        if raw.startswith('http://') or raw.startswith('https://'):
            return _img_proxy_url(raw, HuangguooldSite.FRONTEND_BASE + '/')
        raw = raw.lstrip('/')
        if '..' in raw:
            return ''
        if raw.startswith('upload/') or raw.startswith('upload_01/'):
            return _img_proxy_url(HuangguooldSite.COVER_HOST2 + '/' + raw,
                                  HuangguooldSite.FRONTEND_BASE + '/')
        return _img_proxy_url(HuangguooldSite.COVER_HOST + '/' + raw,
                              HuangguooldSite.FRONTEND_BASE + '/')

    @staticmethod
    def _card(item, cat_name):
        item = as_dict(item)
        vid = map_str(item, 'id')
        title = first_non_empty(map_str(item, 'title'), map_str(item, 'name'))
        if not vid or not title:
            return None
        card = {
            'vod_id': vid,
            'vod_name': clean_text(title),
            'vod_pic': HuangguooldSite._cover_url(
                map_str(item, 'cover', 'horizontalCover', 'coverUrl')),
            'vod_remarks': '',
            'vod_class': first_non_empty(map_str(item, 'tagNames'),
                                         map_str(item, 'anchor'), cat_name),
            'vod_content': truncate(clean_text(map_str(item, 'summary')), 200),
        }
        total = atoi(map_str(item, 'totalEpisode'), 0)
        if total > 0:
            updated = atoi(map_str(item, 'updateEpisode'), 0)
            if 0 < updated < total:
                card['vod_remarks'] = '更新至%d集/共%d集' % (updated, total)
            else:
                card['vod_remarks'] = '共%d集' % total
        return card

    def listing(self, cat, page):
        page = page if page and page > 0 else 1
        try:
            tab_id = self._tab_id(cat)
            data = self._call('GET', '/api/app/playlet/home/tab/' + quote(tab_id, safe=''), {
                'pageNumber': str(page),
                'pageSize': '24',
                'tabSortType': '1',
            })
            items = as_list(as_dict(data).get('list'))
            cat_name = self._cat_name(cat)
            cards = []
            seen = set()
            for raw in items:
                card = self._card(raw, cat_name)
                if not card or card['vod_id'] in seen:
                    continue
                seen.add(card['vod_id'])
                cards.append(card)
            if not cards:
                if page > 1:
                    return []
                print('[黄果旧版] 列表无内容: %s' % cat)
                return []
            return cards
        except Exception as exc:
            if page > 1:
                return []
            print('[黄果旧版] 列表读取失败: %s' % exc)
            return []

    def search(self, wd, page):
        print('[黄果旧版] 接口未提供搜索')
        return []

    @staticmethod
    def _parse_chapters(raw):
        items = as_list(raw)
        if items:
            return [as_dict(item) for item in items]
        wrapper = as_dict(raw)
        items = as_list(wrapper.get('list'))
        if items:
            return [as_dict(item) for item in items]
        return []

    @staticmethod
    def _tag_names(detail):
        raw = detail.get('tagNames')
        if isinstance(raw, list):
            names = []
            for item in raw:
                text = str(item or '').strip()
                if text:
                    names.append(text)
            if names:
                return ','.join(names)
        return map_str(detail, 'anchor')

    def _chapters(self, vid):
        cached = self._details.get(vid)
        if cached and time.time() - cached.get('at', 0) < self.DETAIL_TTL:
            return cached
        data = self._call('GET', '/api/app/playlet/detail/' + quote(vid, safe=''))
        detail = as_dict(data)
        if not detail:
            raise ValueError('黄果旧版详情解析失败')
        entry = {
            'title': clean_text(first_non_empty(map_str(detail, 'title'),
                                                map_str(detail, 'name'))),
            'cover': self._cover_url(
                map_str(detail, 'cover', 'horizontalCover', 'coverUrl')),
            'desc': truncate(clean_text(map_str(detail, 'summary')), 400),
            'tags': self._tag_names(detail),
            'chapters': self._parse_chapters(detail.get('chapters')),
            'at': time.time(),
        }
        if not entry['chapters']:
            try:
                entry['chapters'] = self._parse_chapters(
                    self._call('GET', '/api/app/playlet-chapter/list/' + quote(vid, safe='')))
            except Exception:
                entry['chapters'] = []
        if not entry['chapters']:
            raise ValueError('黄果旧版详情无分集: %s' % vid)
        self._details[vid] = entry
        return entry

    def detail(self, vid):
        vid = str(vid or '').strip()
        if not vid:
            return {}
        try:
            entry = self._chapters(vid)
            episodes = []
            seen = set()
            for index, chapter in enumerate(entry['chapters']):
                chapter = as_dict(chapter)
                no = to_int(map_str(chapter, 'currentEpisode'), 0)
                if no <= 0:
                    no = atoi(map_str(chapter, 'title'), 0)
                if no <= 0:
                    no = index + 1
                while no in seen:
                    no += 1
                seen.add(no)
                name = clean_text(map_str(chapter, 'title'))
                if not name:
                    name = '第%d集' % no
                episodes.append({
                    'no': no,
                    'name': name,
                    'url': 'huangguoold://%s/%d' % (vid, no),
                })
            episodes = sort_episodes(episodes)
            if not episodes:
                return {}
            return {
                'vod_id': vid,
                'vod_name': entry['title'] or ('黄果旧版 ' + vid),
                'vod_pic': entry['cover'],
                'vod_class': entry['tags'],
                'vod_content': entry['desc'],
                'vod_remarks': '共%d集' % len(episodes),
                'episodes': episodes,
            }
        except Exception as exc:
            print('[黄果旧版] 详情读取失败: %s' % exc)
            return {}

    @staticmethod
    def _find_chapter(chapters, episode):
        for index, chapter in enumerate(chapters):
            no = to_int(map_str(chapter, 'currentEpisode'), 0)
            if no <= 0:
                no = index + 1
            if no == episode:
                return as_dict(chapter)
        if 1 <= episode <= len(chapters):
            return as_dict(chapters[episode - 1])
        return {}

    def play(self, url):
        raw = str(url or '')
        prefix = self.key + '://'
        if not raw.startswith(prefix):
            return {'url': raw, 'header': {'User-Agent': self.UA},
                    'parse': 0} if is_http_media(raw) else {}
        rest = raw[len(prefix):]
        if '/' not in rest:
            print('[黄果旧版] 播放参数无效')
            return {}
        vid, extra = rest.split('/', 1)
        if not vid or not extra:
            print('[黄果旧版] 播放参数无效')
            return {}
        episode = atoi(extra, 1)
        if episode < 1:
            episode = 1
        try:
            entry = self._chapters(vid)
            chapter = self._find_chapter(entry['chapters'], episode)
            if not chapter:
                print('[黄果旧版] 第%d集不存在' % episode)
                return {}
            video_url = map_str(chapter, 'videoUrl').strip()
            if not video_url:
                print('[黄果旧版] 该分集没有播放地址')
                return {}
            token = self._ensure_token(False)
            base = self._api_base_for()
            target = (base + '/api/app/vid/h5/m3u8/' + quote(video_url.strip('/'), safe='')
                      + '?' + urlencode({'c': self.CDN_BASE, 'token': token}))
            if not is_http_media(target):
                print('[黄果旧版] 播放地址无效')
                return {}
            return {
                'url': target,
                'header': {
                    'User-Agent': self.UA,
                    'Referer': self.FRONTEND_BASE + '/',
                    'Origin': self.FRONTEND_BASE,
                    'Accept': '*/*',
                },
                'parse': 0,
            }
        except Exception as exc:
            print('[黄果旧版] 播放读取失败: %s' % exc)
            return {}

try:
    import requests
except ImportError:
    requests = None

try:
    import urllib.request as _urlrequest
    import ssl as _ssl
except ImportError:
    _urlrequest = None
    _ssl = None

try:
    import gzip as _gzip
except ImportError:
    _gzip = None

if requests is None:
    requests = CompatRequests()

try:
    from base.spider import Spider as _BaseSpider
except ImportError:
    class _BaseSpider(object):
        pass

try:
    import warnings
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    warnings.filterwarnings('ignore', message='Unverified HTTPS request')
except Exception:
    pass

_AES_SBOX, _AES_INV_SBOX = _aes_tables()

class HuangguaSite(object):

    key = 'huanggua'
    name = '黄瓜'
    SITE = 'https://hgdju4.com'

    FETCH_TIMEOUT = 12
    RETRY = 2

    CATS = [
        {'type_id': 'recommend', 'type_name': '推荐', 'path': '/'},
        {'type_id': 'yuanchuang', 'type_name': '原创', 'path': '/yuanchuang'},
        {'type_id': 'mogai', 'type_name': '魔改', 'path': '/mogai'},
        {'type_id': 'manju', 'type_name': 'AI漫剧', 'path': '/manju'},
        {'type_id': 'zhenren', 'type_name': '真人短剧', 'path': '/zhenren'},
        {'type_id': 'aiduanju', 'type_name': 'AI短剧', 'path': '/aiduanju'},
        {'type_id': 'browse', 'type_name': '全部', 'path': '/browse'},
        {'type_id': 'browse-new', 'type_name': '最新', 'path': '/browse?sort=new'},
    ]

    RE_MIRROR = re.compile(r'(?i)(https?://[a-z0-9\-]{2,24}\.[a-z0-9]{5,20}\.cc)')
    MARKER = 'data-xpch="card-drama"'
    RE_M3U8 = re.compile(r'(?i)https?://[^\s"\'<>\\]{10,200}\.m3u8[^\s"\'<>\\]{0,300}')
    RE_EP_LINK = re.compile(r'(?i)<a\b[^>]*href="/play/([0-9a-zA-Z\-]+)/(\d+)"[^>]*>')
    RE_H1 = re.compile(r'(?is)<h1[^>]*>([\s\S]*?)</h1>')
    RE_PAGE_TITLE = re.compile(r'(?is)<title>([\s\S]*?)</title>')
    RE_META_DESC = re.compile(r'(?i)<meta\s+name="description"\s+content="([^"]*)"')
    RE_IMG = re.compile(r'(?i)\b(?:z-image-loader-url|data-cover-fb|data-src|src)="([^"]+)"')
    RE_VALID_SLUG = re.compile(r'^[A-Za-z0-9_-]{1,64}$')

    def __init__(self):
        self._hosts = []
        self._host = ''

    def cats(self):
        return [{'type_id': item['type_id'], 'type_name': item['type_name']}
                for item in self.CATS]

    def _discover(self):
        seen = {self.SITE.rstrip('/')}
        candidates = [self.SITE]
        try:
            _, text = http_get(self.SITE + '/', headers={
                'User-Agent': UA_DESKTOP, 'Referer': self.SITE + '/'},
                timeout=self.FETCH_TIMEOUT)
            if text:
                for m in self.RE_MIRROR.finditer(text):
                    host = m.group(1).rstrip('/')
                    if host in seen or 'hgdju' in host:
                        continue
                    seen.add(host)
                    candidates.append(host)
        except Exception:
            pass
        valid = []
        for host in candidates:
            try:
                status, text = http_get(host + '/', headers={
                    'User-Agent': UA_DESKTOP, 'Referer': host + '/'},
                    timeout=self.FETCH_TIMEOUT)
            except Exception:
                status, text = 0, ''
            if status and status < 400 and text and self.MARKER in text:
                valid.append(host)
        self._hosts = valid
        if self._hosts:
            self._host = self._hosts[0]

    def _mirrors(self):
        if not self._hosts:
            self._discover()
        return self._hosts

    def _fetch(self, path):
        last_status = 0
        for _ in range(self.RETRY):
            for host in self._mirrors():
                try:
                    status, text = http_get(host + path, headers={
                        'User-Agent': UA_DESKTOP, 'Referer': host + '/'},
                        timeout=self.FETCH_TIMEOUT)
                except Exception:
                    status, text = 0, ''
                if not text or status >= 400:
                    if status:
                        last_status = status
                    continue
                self._host = host
                return text, ''
            self._hosts = []
        if last_status >= 400:
            return '', 'HTTP %d' % last_status
        return '', '黄瓜短剧抓取失败'

    @staticmethod
    def _cat_path(cat):
        for item in HuangguaSite.CATS:
            if item['type_id'] == cat:
                return item['path']
        return HuangguaSite.CATS[0]['path']

    @staticmethod
    def _cat_name(cat):
        for item in HuangguaSite.CATS:
            if item['type_id'] == cat:
                return item['type_name']
        return ''

    def _abs_url(self, raw):
        raw = html_unescape((raw or '').strip())
        if not raw:
            return ''
        if raw.startswith('http://') or raw.startswith('https://'):
            return raw
        if raw.startswith('//'):
            return 'https:' + raw
        base = self._host or self.SITE
        if raw.startswith('/'):
            return base + raw
        return base + '/' + raw

    def _img_proxy(self, cover):
        cover = str(cover or '').strip()
        if not cover:
            return ''
        if cover.startswith('http://') or cover.startswith('https://'):
            return cover
        return self._abs_url(cover)

    def _cards(self, page_html, cat_name):
        return _nuxt_drama_cards(page_html, cat_name, self._abs_url)

    def listing(self, cat, page):
        page = page if page and page > 0 else 1
        cat = str(cat or '').strip() or self.CATS[0]['type_id']
        base = self._cat_path(cat)
        path = base
        if page > 1:
            path = base.rstrip('/') + '?page=%d' % page
        page_html, err = self._fetch(path)
        if err:
            if page > 1 and '404' in err:
                return []
            print('[黄瓜] 列表抓取失败 %s: %s' % (path, err))
            return []
        cards = self._cards(page_html, self._cat_name(cat))
        if not cards:
            if page > 1:
                return []
            print('[黄瓜] 列表无内容: %s' % path)
        return cards

    def search(self, wd, page):
        wd = str(wd or '').strip()
        if not wd:
            return []
        page_html, err = self._fetch('/search?q=' + quote(wd, safe=''))
        if err:
            print('[黄瓜] 搜索失败: %s' % err)
            return []
        cards = self._cards(page_html, '')
        if not cards:
            print('[黄瓜] 搜索无结果: %s' % wd)
        return cards

    def detail(self, vid):
        vid = str(vid or '').strip()
        if '/' in vid:
            vid = vid[vid.rfind('/') + 1:]
        if not self.RE_VALID_SLUG.match(vid or ''):
            print('[黄瓜] 无效的剧集 ID: %s' % vid)
            return {}
        page_html, err = self._fetch('/drama/' + quote(vid, safe=''))
        if err:
            print('[黄瓜] 详情抓取失败: %s' % err)
            return {}
        name = ''
        found = self.RE_H1.search(page_html)
        if found:
            name = clean_text(html_unescape(strip_tags(found.group(1))))
        if not name:
            found = self.RE_PAGE_TITLE.search(page_html)
            if found:
                name = clean_text(html_unescape(strip_tags(found.group(1))))
                name = re.split(r'\s*-\s*', name, 1)[0].strip()
                name = re.sub(r'在线观看$', '', name).strip()
        desc = ''
        found = self.RE_META_DESC.search(page_html)
        if found:
            desc = truncate(clean_text(html_unescape(found.group(1))), 400)
        pic = ''
        found = self.RE_IMG.search(page_html)
        if found:
            raw = html_unescape(found.group(1)).strip()
            low = raw.lower()
            if low and not low.startswith('data:') \
                    and not _nuxt_is_non_image(low):
                pic = self._abs_url(raw)
        episodes = []
        seen = set()
        for match in self.RE_EP_LINK.finditer(page_html):
            if match.group(1) != vid:
                continue
            no = atoi(match.group(2), 0)
            if no < 1 or no in seen:
                continue
            seen.add(no)
            episodes.append({
                'no': no,
                'name': '第%d集' % no,
                'url': '%s://%s/%d' % (self.key, vid, no),
            })
        episodes = sort_episodes(episodes)
        if not episodes:
            print('[黄瓜] 详情页无剧集: %s' % vid)
            return {}
        return {
            'vod_id': vid,
            'vod_name': name or ('黄瓜短剧 ' + vid),
            'vod_pic': pic,
            'vod_content': desc,
            'vod_class': '黄瓜',
            'vod_remarks': '共%d集' % len(episodes),
            'episodes': episodes,
        }

    def play(self, url):
        raw = str(url or '')
        prefix = self.key + '://'
        vid, extra = '', ''
        if raw.startswith(prefix):
            rest = raw[len(prefix):]
            if '/' in rest:
                vid, extra = rest.split('/', 1)
        elif '/play/' in raw:
            parts = [part for part in raw[raw.find('/play/') + len('/play/'):].strip('/').split('/') if part]
            if parts:
                vid = parts[0]
                if len(parts) >= 2:
                    extra = parts[1]
        if not vid:
            print('[黄瓜] 播放参数无效: %s' % raw)
            return {}
        seq = atoi(extra, 1)
        if seq < 1:
            seq = 1
        if not self.RE_VALID_SLUG.match(vid):
            print('[黄瓜] 无效的剧集 ID: %s' % vid)
            return {}
        page_html, err = self._fetch('/play/%s/%d' % (quote(vid, safe=''), seq))
        if err:
            print('[黄瓜] 播放页抓取失败: %s' % err)
            return {}
        normalized = page_html.replace('\\u0026', '&').replace('\\u002F', '/') \
            .replace('\\/', '/')
        media = ''
        for m in self.RE_M3U8.findall(normalized):
            cand = html_unescape(m.strip())
            if '.m3u8' in cand:
                media = cand
                break
        if not is_http_media(media):
            print('[黄瓜] 第%d集暂无播放地址' % seq)
            return {}
        return {
            'url': media,
            'header': {'User-Agent': UA_DESKTOP, 'Referer': (self._host or self.SITE) + '/'},
            'parse': 0,
        }

try:
    import requests
except ImportError:
    requests = None
try:
    import urllib.request as _urlrequest
    import ssl as _ssl
except ImportError:
    _urlrequest = None
    _ssl = None
try:
    import gzip as _gzip
except ImportError:
    _gzip = None

if requests is None:
    requests = CompatRequests()
try:
    from base.spider import Spider as _BaseSpider
except ImportError:

    class _BaseSpider(object):
        pass
try:
    import warnings
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    warnings.filterwarnings('ignore', message='Unverified HTTPS request')
except Exception:
    pass

_AES_SBOX, _AES_INV_SBOX = _aes_tables()

class _HuangjuSwitch(Exception):
    pass

class HuangjuSite(object):
    key = 'huangju'
    name = '黄菊'
    SITE_DOMAINS = ('huangju.net', 'yanyushorttv.cc', 'yanyushorttv.top')
    UA = 'Mozilla/5.0 (Linux; Android 11; Pixel 5) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/90.0.4430.91 Mobile Safari/537.36'
    RECOMMEND_CAT = '@recommend'
    NEWEST_CAT = '@new'
    HOT_CAT = '@hot'
    AI_ADULT_CAT = 'adult'
    CATS = [{'type_id': RECOMMEND_CAT, 'type_name': '推荐'}, {'type_id': NEWEST_CAT, 'type_name': '最新'}, {'type_id': HOT_CAT, 'type_name': '热门'}, {'type_id': AI_ADULT_CAT, 'type_name': 'AI成人短剧'}]
    SIGNED_COOKIE_RE = '(?:^|[,;\\s])CloudFront-(Policy|Signature|Key-Pair-Id)\\s*=\\s*([^;,\\s"\\\']+)'
    COOKIE_NAMES = ('CloudFront-Policy', 'CloudFront-Signature', 'CloudFront-Key-Pair-Id')
    BAD_ID_CHARS = '/\\:?#%|"<>'

    def __init__(self):
        self._token = ''
        self._device_id = ''
        self._domain_index = 0

    def _domain(self):
        try:
            index = int(self._domain_index)
        except (TypeError, ValueError):
            index = 0
        if index < 0 or index >= len(self.SITE_DOMAINS):
            index = 0
        self._domain_index = index
        return self.SITE_DOMAINS[index]

    def _site_base(self):
        return 'https://' + self._domain()

    def _api_base(self):
        return 'https://api.' + self._domain()

    def _switch_domain(self):
        count = len(self.SITE_DOMAINS)
        self._domain_index = (int(self._domain_index or 0) + 1) % count
        self._token = ''
        return self._domain_index

    def _try_domains(self, operation):
        tried = 0
        last_exc = None
        count = len(self.SITE_DOMAINS)
        while tried < count:
            try:
                return operation()
            except _HuangjuSwitch as exc:
                last_exc = exc
                tried += 1
                if tried < count:
                    self._switch_domain()
        if last_exc is not None:
            print('[黄菊] 所有备用域名均不可用: %s' % last_exc)
        return None

    def _safe(self, operation, fallback):
        try:
            result = self._try_domains(operation)
        except Exception as exc:
            print('[黄菊] 请求失败: %s' % exc)
            return fallback
        return result if result else fallback

    @classmethod
    def _valid_id(cls, value):
        text = value if isinstance(value, str) else str(value or '')
        if not text or len(text) > 450 or text.strip() != text:
            return False
        if text == '.' or text == '..':
            return False
        for ch in cls.BAD_ID_CHARS:
            if ch in text:
                return False
        for ch in text:
            if ord(ch) < 32 or ord(ch) == 127:
                return False
        return True

    @staticmethod
    def _integer(value, maximum):
        text = map_str({'v': value}, 'v')
        try:
            number = int(text.strip())
        except (TypeError, ValueError):
            return (0, False)
        return (number, 0 <= number <= maximum)

    @staticmethod
    def _cat_names(row):
        names = []
        seen = set()

        def add(value):
            name = ''
            if isinstance(value, dict):
                name = map_str(value, 'name')
            elif isinstance(value, str):
                name = value
            name = name.strip()
            if name and len(name) <= 48 and (name not in seen) and (len(names) < 24):
                seen.add(name)
                names.append(name)
        row = as_dict(row)
        add(row.get('category'))
        for entry in as_list(row.get('categories')):
            add(entry)
        return names

    @staticmethod
    def _payload(value):
        if isinstance(value, dict):
            if value.get('data') is not None and value.get('id') is None and (value.get('items') is None) and (value.get('url') is None):
                return value.get('data')
        return value

    @classmethod
    def _cover(cls, value, page_url):
        if isinstance(value, list):
            for entry in value:
                got = cls._cover(entry, page_url)
                if got:
                    return got
            return ''
        if isinstance(value, dict):
            for key in ('url', 'contentUrl', 'thumbnailUrl'):
                got = cls._cover(value.get(key), page_url)
                if got:
                    return got
            return ''
        if isinstance(value, str):
            item = value.strip()
            if not item or item.startswith('data:') or item.startswith('javascript:'):
                return ''
            if item.startswith('//'):
                item = 'https:' + item
            return resolve_url(page_url, item)
        return ''

    @staticmethod
    def _uuid_like():
        raw = ''.join((random.choice('0123456789abcdef') for _ in range(32)))
        return '-'.join((raw[:8], raw[8:12], raw[12:16], raw[16:20], raw[20:]))

    @staticmethod
    def _expiration(value):
        text = map_str({'v': value}, 'v').strip()
        if not text:
            return 0
        try:
            stamp = int(text)
        except (TypeError, ValueError):
            stamp = 0
        if stamp > 0 and stamp <= 253402300799000:
            if stamp > 100000000000:
                return stamp // 1000
            return stamp
        try:
            struct_time = time.strptime(text[:19], '%Y-%m-%dT%H:%M:%S')
            return int(time.mktime(struct_time) + time.timezone)
        except (TypeError, ValueError):
            return 0

    def _headers(self, with_body=False, token=''):
        headers = {'User-Agent': self.UA, 'Accept': 'application/json, text/plain, */*', 'Accept-Language': 'zh-CN,zh;q=0.9', 'Referer': self._site_base() + '/', 'Origin': self._site_base()}
        if with_body:
            headers['Content-Type'] = 'application/json'
        if token:
            headers['Authorization'] = 'Bearer ' + token
        return headers

    def _raw(self, method, url, headers, body=None):
        try:
            status, text, heads = http_response(url, method, headers=headers, body=body, timeout=20)
        except Exception as exc:
            raise _HuangjuSwitch('请求异常: %s' % exc)
        if status >= 500 or status == 0:
            raise _HuangjuSwitch('接口 HTTP %s' % status)
        raw_cookie = ''
        for key, value in (heads or {}).items():
            if str(key).lower() == 'set-cookie':
                raw_cookie = value
                break
        return (status, text, [raw_cookie] if raw_cookie else [])

    def _clear_token(self, token):
        if self._token == token:
            self._token = ''

    def _guest_token(self):
        if self._token:
            return self._token
        if not self._device_id:
            self._device_id = self._uuid_like()
        body = json.dumps({'deviceId': self._device_id}, ensure_ascii=False).encode('utf-8')
        status, text, _ = self._raw('POST', self._api_base() + '/auth/guest', self._headers(True), body)
        if status < 200 or status >= 300 or (not text):
            raise _HuangjuSwitch('授权 HTTP %s' % status)
        row = as_dict(json_loads(text))
        token = map_str(row, 'token')
        if not token:
            token = map_str(as_dict(row.get('data')), 'token')
        if not token or len(token) > 8192 or any((ord(ch) <= 32 or ord(ch) >= 127 for ch in token)):
            raise _HuangjuSwitch('未返回有效的访客授权')
        self._token = token
        return token

    def _get(self, route, query, limit):
        for attempt in range(2):
            token = self._guest_token()
            if not token:
                return (None, [])
            address = self._api_base() + route
            if query:
                address += '?' + urlencode(sorted(query.items()))
            status, text, set_cookies = self._raw('GET', address, self._headers(False, token))
            if attempt == 0 and status in (401, 403) and text and (len(text) <= limit) and (json_loads(text) is not None):
                self._clear_token(token)
                continue
            if status < 200 or status >= 300:
                print('[黄菊] 接口 HTTP %s' % status)
                return (None, [])
            if not text or len(text) > limit:
                print('[黄菊] 返回的数据过大或为空')
                return (None, [])
            decoded = json_loads(text)
            if decoded is None:
                print('[黄菊] 返回的数据格式无效')
                return (None, [])
            return (decoded, set_cookies)
        print('[黄菊] 访客授权已失效，请重试')
        return (None, [])

    def _card(self, item):
        item = as_dict(item)
        item_id = map_str(item, 'id')
        slug = map_str(item, 'slug')
        title = map_str(item, 'title')
        if not self._valid_id(item_id) or not self._valid_id(slug) or (not title):
            return None
        source_id = slug + '-' + item_id
        remarks = ''
        status = map_str(item, 'status')
        if status == 'completed':
            remarks = '完结'
        elif status == 'ongoing':
            remarks = '连载中'
        total, ok = self._integer(item.get('totalEpisodes'), 100000)
        if ok and total > 0:
            if remarks:
                remarks += ' '
            remarks += '全%d集' % total
        return {'vod_id': source_id, 'vod_name': truncate(title.strip(), 256), 'vod_pic': self._cover(item.get('coverUrl'), self._site_base() + '/'), 'vod_remarks': remarks, 'vod_class': first_non_empty(*self._cat_names(item)), 'vod_content': truncate(map_str(item, 'description'), 500)}

    def _cards(self, value):
        row = as_dict(self._payload(value))
        items = as_list(row.get('items'))
        if not items:
            print('[黄菊] 目录没有返回内容')
            return []
        cards = []
        seen = set()
        for entry in items:
            card = self._card(entry)
            if not card or card['vod_id'] in seen:
                continue
            seen.add(card['vod_id'])
            cards.append(card)
        if not cards:
            print('[黄菊] 目录中没有可识别的剧集')
        return cards

    def cats(self):
        return [dict(item) for item in self.CATS]

    def listing(self, cat, page):

        def _run():
            return self._listing_once(cat, page)
        return self._safe(_run, [])

    def _listing_once(self, cat, page):
        page = page if page and page > 0 else 1
        query = {'page': str(page)}
        if cat == self.NEWEST_CAT:
            query['sort'] = 'new'
        elif cat == self.HOT_CAT:
            query['sort'] = 'hot'
        elif cat == self.AI_ADULT_CAT:
            query['category'] = cat
        elif cat and cat != self.RECOMMEND_CAT and (cat not in ('all',)):
            query['category'] = str(cat)
        value, _ = self._get('/dramas', query, 4 << 20)
        return self._cards(value)

    def search(self, wd, page):
        wd = (wd or '').strip()
        if not wd:
            return []

        def _run():
            page_no = page if page and page > 0 else 1
            value, _ = self._get('/dramas', {'page': str(page_no), 'q': wd}, 4 << 20)
            return self._cards(value)
        return self._safe(_run, [])

    def detail(self, vid):
        vid = str(vid or '').strip()
        if not self._valid_id(vid) or '-' not in vid:
            print('[黄菊] 无效的剧集 ID')
            return {}

        def _run():
            return self._detail_once(vid)
        return self._safe(_run, {})

    def _detail_once(self, vid):
        value, _ = self._get('/dramas/' + quote(vid, safe=''), None, 8 << 20)
        row = as_dict(self._payload(value))
        drama_id = map_str(row, 'id')
        if not self._valid_id(drama_id) or not vid.endswith('-' + drama_id):
            print('[黄菊] 详情与请求剧集不符')
            return {}
        episodes = []
        for index, entry in enumerate(as_list(row.get('episodes'))):
            episode = as_dict(entry)
            playable = episode.get('playable')
            if isinstance(playable, bool) and (not playable):
                continue
            episode_id = map_str(episode, 'id')
            if not self._valid_id(episode_id):
                continue
            number = index + 1
            value_no, ok = self._integer(episode.get('epNo'), 100000)
            if ok and value_no > 0:
                number = value_no
            episodes.append({'no': number, 'name': '第 %d 集' % number, 'url': '%s://%s' % (self.key, quote(episode_id, safe=''))})
        if not episodes:
            print('[黄菊] 详情没有返回剧集列表')
            return {}
        episodes = sort_episodes(episodes)
        return {'vod_id': vid, 'vod_name': truncate(first_non_empty(map_str(row, 'title'), vid), 256), 'vod_pic': self._cover(row.get('coverUrl'), self._site_base() + '/'), 'vod_content': truncate(map_str(row, 'description'), 12000), 'vod_class': first_non_empty(*self._cat_names(row)), 'vod_remarks': '共%d集' % len(episodes), 'episodes': episodes}

    def _media_cookie(self, set_cookies, expires_at):
        expires = self._expiration(expires_at)
        if expires > 0 and time.time() >= expires:
            print('[黄菊] 媒体凭证已过期，请检查设备时间后重试')
            return ''
        cookies = {}
        for header in set_cookies or []:
            for match in re.finditer(self.SIGNED_COOKIE_RE, header):
                cookies['CloudFront-' + match.group(1)] = match.group(2)
        values = []
        for name in self.COOKIE_NAMES:
            value = cookies.get(name, '')
            if not value or len(value) > 8192 or any((ch in ',;"\\' for ch in value)) or any((ord(ch) <= 32 or ord(ch) >= 127 for ch in value)):
                print('[黄菊] 未返回完整的媒体凭证，请重试')
                return ''
            values.append(name + '=' + value)
        return '; '.join(values)

    def play(self, url):
        raw = str(url or '')
        prefix = self.key + '://'
        episode_id = raw[len(prefix):] if raw.startswith(prefix) else raw
        try:
            episode_id = unquote(episode_id)
        except Exception:
            pass
        if not self._valid_id(episode_id):
            print('[黄菊] 播放参数无效')
            return {}

        def _run():
            return self._play_once(episode_id)
        return self._safe(_run, {})

    def _play_once(self, episode_id):
        value, set_cookies = self._get('/play/' + quote(episode_id, safe=''), None, 64 << 10)
        row = as_dict(self._payload(value))
        cookie = self._media_cookie(set_cookies, row.get('expiresAt'))
        if not cookie:
            return {}
        media = map_str(row, 'url')
        if not media:
            print('[黄菊] 未返回播放地址')
            return {}
        base = self._site_base()
        if not media.startswith('http://') and (not media.startswith('https://')):
            media = resolve_url(base + '/', media)
        if not is_http_media(media):
            print('[黄菊] 播放地址无效')
            return {}
        return {'url': media, 'header': {'Referer': base + '/', 'Cookie': cookie}, 'parse': 0}

try:
    import requests
except ImportError:
    requests = None

try:
    import urllib.request as _urlrequest
    import ssl as _ssl
except ImportError:
    _urlrequest = None
    _ssl = None

try:
    import gzip as _gzip
except ImportError:
    _gzip = None

if requests is None:
    requests = CompatRequests()

try:
    from base.spider import Spider as _BaseSpider
except ImportError:
    class _BaseSpider(object):
        pass

try:
    import warnings
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    warnings.filterwarnings('ignore', message='Unverified HTTPS request')
except Exception:
    pass

_AES_SBOX, _AES_INV_SBOX = _aes_tables()

class Huangdou2Site(object):

    key = 'huangdou2'
    name = '黄豆2'
    site = 'https://aihuangdou.com'

    CATS = [
        {'type_id': 'home', 'type_name': '首页'},
        {'type_id': 'ai-duanju', 'type_name': 'AI短剧'},
        {'type_id': 'ai-manju', 'type_name': 'AI漫剧'},
        {'type_id': 'rankings', 'type_name': '榜单'},
    ]

    def cats(self):
        return [dict(item) for item in self.CATS]

    def _headers(self):
        return {
            'User-Agent': UA_ANDROID,
            'Accept': ('text/html,application/xhtml+xml,application/xml;q=0.9,'
                       'image/avif,image/webp,image/apng,*/*;q=0.8'),
            'Accept-Language': 'zh-CN,zh;q=0.9',
            'Referer': self.site + '/',
            'Upgrade-Insecure-Requests': '1',
        }

    def _fetch(self, path):
        target = self.site + path
        try:
            status, text = http_get(target, headers=self._headers(), timeout=20)
        except Exception as exc:
            print('[黄豆2] 请求失败: %s' % exc)
            return 0, ''
        if status >= 400 or status == 0 or not text:
            print('[黄豆2] 页面 HTTP %s: %s' % (status, path))
            return status, ''
        return status, text

    def _abs(self, raw):
        raw = (raw or '').strip()
        if not raw:
            return ''
        if raw.startswith('http://') or raw.startswith('https://'):
            return raw
        return self.site + '/' + raw.lstrip('/')

    @staticmethod
    def _strip(text):
        return clean_text(re.sub(r'<[^>]*>', ' ', text or ''))

    @staticmethod
    def _attr(block, name):
        match = re.search(r'(?i)\b' + re.escape(name) + r'="([^"]*)"', block or '')
        if match:
            return match.group(1).strip()
        return ''

    @staticmethod
    def _first_image(block):
        for match in re.finditer(
                r'(?i)\b(?:data-src|data-original|data-lazy-src|src)="([^"]+)"',
                block or ''):
            raw = match.group(1).strip()
            low = raw.lower()
            if not raw or low.startswith('data:') or 'placeholder' in low \
                    or 'loading' in low:
                continue
            return raw
        return ''

    @staticmethod
    def _clean_alt(alt):
        alt = (alt or '').strip()
        if alt.startswith('《'):
            pos = alt.find('》')
            if pos > 0:
                return alt[1:pos].strip()
        return alt[:-2].strip() if alt.endswith('封面') else alt

    def _anchor_title(self, block, detail_path):
        best = ''
        pattern = (r'(?is)<a\b[^>]*href="[^"]*' + re.escape(detail_path)
                   + r'"[^>]*>([\s\S]*?)</a>')
        for match in re.finditer(pattern, block or ''):
            text = self._strip(match.group(1))
            if not text or re.match(r'^\d+$', text):
                continue
            if len(text) > len(best):
                best = text
        return best

    def _cat_path(self, cat):
        cat = cat or self.CATS[0]['type_id']
        if cat.startswith('biaoqian:'):
            return '/biaoqian/' + quote(cat[len('biaoqian:'):], safe='') + '/'
        if cat == 'home':
            return '/'
        return '/' + cat + '/'

    def _cat_name(self, cat):
        for item in self.CATS:
            if item['type_id'] == cat:
                return item['type_name']
        return ''

    def _list_path(self, cat, page):
        base = self._cat_path(cat)
        if page <= 1:
            return base
        return base.rstrip('/') + '/%d/' % page

    def _blocks(self, page_html):
        starts = []
        for pattern in (r'(?i)<article\s+class="[^"]*\bdrama-card\b[^"]*"',
                        r'(?i)<(?:article|li|div)\s+class="[^"]*'
                        r'(?:rank-runner|rank-row)(?:"|\s)'):
            for match in re.finditer(pattern, page_html or ''):
                starts.append(match.start())
        starts.sort()
        blocks = []
        for index, begin in enumerate(starts):
            end = starts[index + 1] if index + 1 < len(starts) else len(page_html)
            blocks.append(page_html[begin:end])
        return blocks

    def _card(self, block, cat_name):
        detail = re.search(r'(?i)href="(?:https?://[^/"]+)?(/drama/\d+/)"', block)
        if not detail:
            return None
        vid = detail.group(1).strip('/')
        if not vid:
            return None
        title = self._attr(block, 'data-track-item-name')
        if not title:
            title = self._anchor_title(block, detail.group(1))
        if not title:
            alt = re.search(r'(?i)\balt="([^"]*)"', block)
            if alt:
                title = self._clean_alt(alt.group(1))
        if not title:
            heading = re.search(r'(?is)<h[23][^>]*>([\s\S]*?)</h[23]>', block)
            if heading:
                title = self._strip(heading.group(1))
        if not title:
            return None

        remarks = ''
        eps = re.search(r'(?i)class="eps-flag"[^>]*>([\s\S]*?)</span>', block)
        if eps:
            remarks = self._strip(eps.group(1))
        score = ''
        found = re.search(r'(?is)class="card-score"[^>]*>([\s\S]*?)</em>', block)
        if found:
            score = self._strip(found.group(1))

        category = ''
        for pattern in (r'(?i)class="meta"[^>]*>([\s\S]*?)</div>',
                        r'(?i)class="rank-(?:row|runner)-(?:meta|metric)"'
                        r'[^>]*>([^<]*)<',
                        r'(?is)class="rank-row-copy"[^>]*>[\s\S]*?<p[^>]*>'
                        r'([^<]*)</p>'):
            found = re.search(pattern, block)
            if found:
                category = self._strip(found.group(1))
                break
        if not category:
            found = re.search(r'(?i)class="badge[^"]*"[^>]*>([^<]*)<', block)
            if found:
                category = found.group(1).strip()

        desc = ''
        for pattern in (
                r'(?i)class="hover-panel"[^>]*>\s*<span>([\s\S]*?)</span>',
                r'(?i)class="rank-(?:row|runner)-desc"[^>]*>([^<]*)<'):
            found = re.search(pattern, block)
            if found:
                desc = truncate(self._strip(found.group(1)), 200)
                break

        cover = self._first_image(block)
        parts = []
        if score:
            parts.append('评分' + score)
        if remarks:
            parts.append(remarks)
        return {
            'vod_id': vid,
            'vod_name': title,
            'vod_pic': _img_proxy_url(self._abs(cover), self.site + '/') if cover else '',
            'vod_remarks': ' · '.join(parts),
            'vod_class': category or cat_name,
            'vod_content': desc,
        }

    def _cards(self, page_html, cat_name):
        cards = []
        seen = set()
        for block in self._blocks(page_html):
            card = self._card(block, cat_name)
            if not card or card['vod_id'] in seen:
                continue
            seen.add(card['vod_id'])
            cards.append(card)
        return cards

    def listing(self, cat, page):
        page = page if page and page > 0 else 1
        path = self._list_path(cat, page)
        try:
            _, body = self._fetch(path)
            if not body:
                return []
            cards = self._cards(body, self._cat_name(cat))
            if not cards:
                print('[黄豆2] 列表无内容: %s' % path)
                return []
            return cards
        except Exception as exc:
            print('[黄豆2] 列表读取失败: %s' % exc)
            return []

    def search(self, wd, page):
        wd = (wd or '').strip()
        if not wd:
            return []
        page = page if page and page > 0 else 1
        path = '/search/?q=' + quote(wd, safe='')
        if page > 1:
            path += '&page=%d' % page
        try:
            _, body = self._fetch(path)
            if not body:
                return []
            cards = self._cards(body, '')
            if not cards:
                print('[黄豆2] 搜索无结果: %s' % wd)
                return []
            return cards
        except Exception as exc:
            print('[黄豆2] 搜索失败: %s' % exc)
            return []

    def _seqs(self, body, vid):
        seqs = set()
        found = re.search(r'(?i)data-total="(\d+)"', body)
        if found:
            total = atoi(found.group(1), 0)
            if 0 < total <= 2000:
                seqs = set(range(1, total + 1))
        if not seqs:
            for match in re.finditer(r'(?i)title="第\s*(\d+)\s*集"', body):
                no = atoi(match.group(1), 0)
                if no > 0:
                    seqs.add(no)
        if not seqs:
            prefix = '/' + vid + '/'
            for match in re.finditer(r'(?i)href="([^"]+)"', body):
                raw = match.group(1)
                parsed = urlparse(raw)
                if parsed.path:
                    raw = parsed.path
                raw = raw.strip('/')
                full = '/' + raw + '/'
                if not full.startswith(prefix):
                    continue
                no = atoi(full[len(prefix):].strip('/'), 0)
                if no > 0:
                    seqs.add(no)
        if not seqs:
            leaf = self._leaf(vid)
            if leaf and re.search(r'(?i)href="[^"]*/video/%s/"' % re.escape(leaf),
                                  body):
                seqs.add(1)
        return sorted(seqs)

    def detail(self, vid):
        vid = str(vid or '').strip().strip('/')
        if not vid:
            return {}
        try:
            _, body = self._fetch('/' + vid + '/')
            if not body:
                return {}
            title = ''
            found = re.search(r'(?is)<h1[^>]*>([\s\S]*?)</h1>', body)
            if found:
                title = self._strip(found.group(1))
            desc = ''
            found = re.search(
                r'(?is)<meta[^>]*name="description"[^>]*content="([^"]*)"', body)
            if found:
                desc = clean_text(found.group(1))
            pic = ''
            found = re.search(
                r'(?i)\b(?:data-src|data-original|src)="(https?://pic\.[^"]+)"',
                body)
            if found:
                pic = found.group(1)
            category = ''
            found = re.search(r'(?i)class="badge[^"]*"[^>]*>([^<]*)<', body)
            if found:
                category = found.group(1).strip()

            episodes = []
            for no in self._seqs(body, vid):
                episodes.append({
                    'no': no,
                    'name': '第%d集' % no,
                    'url': '%s://%s/%d' % (self.key, vid, no),
                })
            episodes = sort_episodes(episodes)
            if not episodes:
                print('[黄豆2] 详情页无剧集: %s' % vid)
                return {}
            return {
                'vod_id': vid,
                'vod_name': title or ('黄豆2 ' + vid),
                'vod_pic': pic,
                'vod_content': desc,
                'vod_class': category,
                'vod_remarks': '共%d集' % len(episodes),
                'episodes': episodes,
            }
        except Exception as exc:
            print('[黄豆2] 详情读取失败: %s' % exc)
            return {}

    @staticmethod
    def _leaf(vid):
        vid = vid.strip('/')
        pos = vid.rfind('/')
        return vid[pos + 1:] if pos >= 0 else vid

    def _play_path(self, vid, seq):
        base = '/video/' + self._leaf(vid)
        if seq > 1:
            return '%s/%d/' % (base, seq)
        return base + '/'

    def _play_src(self, body):
        found = re.search(
            r'(?is)<script[^>]*id="ninePlayData"[^>]*>([\s\S]*?)</script>', body)
        if not found:
            print('[黄豆2] 播放页无播放数据')
            return ''
        data = json_loads(found.group(1).strip())
        if not isinstance(data, dict):
            print('[黄豆2] 播放数据解析失败')
            return ''
        current = data.get('current')
        if not isinstance(current, dict):
            current = data.get('episode')
        if not isinstance(current, dict):
            print('[黄豆2] 播放数据缺少当前集')
            return ''
        src = map_str(current, 'src', 'videoUrl', 'playUrl', 'url')
        if src:
            return src
        hevc = map_str(current, 'srcHevc')
        if '.m3u8' in hevc:
            return hevc
        print('[黄豆2] 播放数据无媒体地址')
        return ''

    def play(self, url):
        raw = str(url or '').strip().strip('/')
        prefix = self.key + '://'
        if raw.startswith(prefix):
            rest = raw[len(prefix):].strip('/')
        elif self.site in raw:
            pos = raw.index(self.site)
            rest = raw[pos + len(self.site):].lstrip('/').strip('/')
        else:
            print('[黄豆2] 播放参数无效')
            return {}
        cut = rest.rfind('/')
        if cut <= 0 or cut >= len(rest) - 1:
            print('[黄豆2] 播放参数无效')
            return {}
        vid, seq_text = rest[:cut], rest[cut + 1:]
        seq = atoi(seq_text, 0)
        if seq < 1 or not vid:
            print('[黄豆2] 播放参数无效')
            return {}
        try:
            _, body = self._fetch(self._play_path(vid, seq))
            if not body:
                return {}
            src = self._play_src(body)
            if not is_http_media(src):
                print('[黄豆2] 播放地址无效')
                return {}
            return {
                'url': src,
                'header': {'User-Agent': UA_DESKTOP,
                           'Referer': self.site + '/'},
                'parse': 0,
            }
        except Exception as exc:
            print('[黄豆2] 播放读取失败: %s' % exc)
            return {}

class _MaccmsBase(object):

    KEY = ''
    NAME = ''
    BASE = ''
    DEFAULT_CAT = ''
    CATS = []
    PLACEHOLDER_HOSTS = ('img.test.com', 'huangdouhui.tv')

    def __init__(self):
        self.key = self.KEY
        self.name = self.NAME
        self.site = self.BASE

    def _host_name(self):
        try:
            host = (urlparse(self.site).hostname or '').lower()
        except Exception:
            return ''
        return host[4:] if host.startswith('www.') else host

    def _clean_pic(self, pic):
        if not pic:
            return ''
        try:
            host = (urlparse(pic).hostname or '').lower()
        except Exception:
            return ''
        if not host:
            return ''
        for placeholder in self.PLACEHOLDER_HOSTS:
            if host == placeholder or host.endswith('.' + placeholder):
                return ''
        if host.startswith('www.'):
            host = host[4:]
        if host != self._host_name():
            return ''
        return pic

    def _query(self, params):
        endpoint = self.site + '/api.php/provide/vod/?' + urlencode(params)
        status, text = http_get(endpoint, headers={'Referer': self.site + '/'},
                                timeout=20)
        if status >= 400:
            print('[%s] 接口 HTTP %d' % (self.name, status))
            return []
        if not text:
            print('[%s] 接口返回空' % self.name)
            return []
        try:
            payload = json.loads(text)
        except Exception as exc:
            print('[%s] 接口解析失败: %s' % (self.name, exc))
            return []
        if payload.get('code') != 1:
            print('[%s] 接口异常: %s' % (self.name, payload.get('msg') or ''))
            return []
        return payload.get('list') or []

    def _cards(self, items):
        cards = []
        for item in items or []:
            if not isinstance(item, dict):
                continue
            vid = str(item.get('vod_id') or '')
            if not vid:
                continue
            cards.append({
                'vod_id': vid,
                'vod_name': str(item.get('vod_name') or ''),
                'vod_pic': self._clean_pic(str(item.get('vod_pic') or '')),
                'vod_remarks': str(item.get('vod_remarks') or ''),
                'vod_class': str(item.get('vod_class') or ''),
                'vod_score': str(item.get('vod_score') or ''),
                'vod_content': clean_text(item.get('vod_content')),
            })
        return cards

    def cats(self):
        return [{'type_id': item[0], 'type_name': item[1]} for item in self.CATS]

    def listing(self, cat, page):
        cat = str(cat or '').strip() or self.DEFAULT_CAT
        page = page if page and page > 0 else 1
        return self._cards(self._query({'ac': 'detail', 't': cat, 'pg': page}))

    def search(self, wd, page):
        wd = str(wd or '').strip()
        if not wd:
            return []
        page = page if page and page > 0 else 1
        return self._cards(self._query({'ac': 'detail', 'wd': wd, 'pg': page}))

    def _play_eps(self, raw):
        best = []
        for line in str(raw or '').split('$$$'):
            eps = []
            for seg in line.split('#'):
                if '$' not in seg:
                    continue
                name, addr = seg.split('$', 1)
                addr = addr.strip()
                if not addr:
                    continue
                eps.append((name.strip(), addr))
            if len(eps) > len(best):
                best = eps
        return best

    def _detail_eps(self, item, vid):
        episodes = []
        for idx, ep in enumerate(self._play_eps(str(item.get('vod_play_url') or ''))):
            episodes.append({
                'no': idx + 1,
                'name': ep[0],
                'url': '%s://%s/%d' % (self.key, vid, idx + 1),
            })
        return episodes

    def detail(self, vid):
        vid = str(vid or '').strip()
        items = self._query({'ac': 'detail', 'ids': vid})
        if not items:
            print('[%s] 详情不存在: %s' % (self.name, vid))
            return {}
        item = items[0] if isinstance(items[0], dict) else {}
        episodes = self._detail_eps(item, str(item.get('vod_id') or vid))
        if not episodes:
            print('[%s] 详情无剧集: %s' % (self.name, vid))
            return {}
        return {
            'vod_id': str(item.get('vod_id') or vid),
            'vod_name': str(item.get('vod_name') or ''),
            'vod_pic': self._clean_pic(str(item.get('vod_pic') or '')),
            'vod_content': clean_text(item.get('vod_content')),
            'vod_class': str(item.get('vod_class') or ''),
            'vod_remarks': '共%d集' % len(episodes),
            'episodes': episodes,
        }

    def play(self, url):
        raw = str(url or '').strip().strip('/')
        prefix = self.key + '://'
        if not raw.startswith(prefix):
            print('[%s] 播放参数无效' % self.name)
            return {}
        rest = raw[len(prefix):].strip('/')
        cut = rest.rfind('/')
        if cut <= 0 or cut >= len(rest) - 1:
            print('[%s] 播放参数无效' % self.name)
            return {}
        vid, seq_text = rest[:cut], rest[cut + 1:]
        seq = atoi(seq_text, 0)
        if seq < 1 or not vid:
            print('[%s] 播放参数无效' % self.name)
            return {}
        items = self._query({'ac': 'detail', 'ids': vid})
        if not items:
            print('[%s] 播放资源不存在' % self.name)
            return {}
        item = items[0] if isinstance(items[0], dict) else {}
        eps = self._play_eps(str(item.get('vod_play_url') or ''))
        if seq > len(eps):
            print('[%s] 该剧只有 %d 集，没有第 %d 集' % (self.name, len(eps), seq))
            return {}
        src = eps[seq - 1][1]
        if not is_http_media(src):
            print('[%s] 播放地址无效' % self.name)
            return {}
        return {'url': src, 'header': {'Referer': self.site + '/'}, 'parse': 0}

class IuzvodSite(_MaccmsBase):

    KEY = 'iuzvod'
    NAME = '爱优影视'
    BASE = 'https://www.iuzvod.com'
    DEFAULT_CAT = '5'
    CATS = [
        ('5', '短剧'),
    ]

class JuwuSite(_MaccmsBase):

    KEY = 'juwu'
    NAME = '剧屋'
    BASE = 'https://www.juwu.tv'
    DEFAULT_CAT = '1'
    CATS = [
        ('1', '短剧'),
        ('2', '情色电影'),
    ]

class DuanjuoneSite(object):

    key = 'duanjuone'
    name = '短剧one'
    site = 'https://duanju.one'
    ACCESS_HOST = 'https://cnaccess.duanju.one'
    ACCEPT_URL = 'https://cnaccess.duanju.one/accept'
    UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
          '(KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36')
    PAGE_SIZE = 24
    MAX_PAGES = 20

    CATS = [
        ('all', '全部'),
        ('free', '免费'),
        ('vip', 'VIP/积分'),
    ]

    RE_CARD = re.compile(r'(?is)<a class="drama-card" href="[^"]*/drama/([A-Za-z0-9\-]+)"[^>]*>([\s\S]*?)</a>')
    RE_COVER = re.compile(r'(?is)<img[^>]+src="([^"]+)"')
    RE_TITLE = re.compile(r'(?is)<h2[^>]*>([\s\S]*?)</h2>')
    RE_ALT = re.compile(r'(?is)<img[^>]+alt="([^"]*)"')
    RE_EP_COUNT = re.compile(r'(?is)<span class="episode-count">([\s\S]*?)</span>')
    RE_TAGS = re.compile(r'(?is)<div class="card-tags">([\s\S]*?)</div>')
    RE_TAG_SPAN = re.compile(r'(?is)<span[^>]*>([\s\S]*?)</span>')
    RE_UPDATED = re.compile(r'(?is)<p[^>]*>([^<]*更新于[^<]*)</p>')
    RE_EP_LINK = re.compile(r'(?is)<a\b[^>]*href="[^"]*/drama/[A-Za-z0-9\-]+/ep/(\d+)"[^>]*>([\s\S]*?)</a>')
    RE_EP_NUM = re.compile(r'(?is)<b[^>]*>\s*(\d+)\s*</b>')
    RE_OG_IMG = re.compile(r'(?i)property="og:image"\s+content="([^"]+)"')
    RE_OG_TTL = re.compile(r'(?i)property="og:title"\s+content="([^"]+)"')
    RE_META = re.compile(r'(?i)<meta\s+name="description"\s+content="([^"]*)"')
    RE_VIDEO = re.compile(r'(?is)<video\b[^>]*src="([^"]+)"')
    RE_SOURCE = re.compile(r'(?is)<source\b[^>]*src="([^"]+)"')
    RE_TOKEN = re.compile(r'name="_token"\s+value="([^"]+)"')
    RE_NOTICE = re.compile(r'(?i)NETWORK NOTICE|cnaccess\.duanju\.one/accept|cn-access-body')
    RE_TAG_NAV = re.compile(r'(?is)class="tag-strip"[^>]*>([\s\S]*?)</nav>')
    RE_TAG_A = re.compile(r'(?is)<a\b[^>]*href="[^"]*/tag/([A-Za-z0-9_\-%]+)"[^>]*>([\s\S]*?)</a>')
    RE_NON_IMG = re.compile(r'(?i)\.(?:svg|gif|json)$')

    def __init__(self):
        self.accessed = False
        self.cookies = {}

    def _cookie_header(self):
        return '; '.join('%s=%s' % (name, value)
                         for name, value in self.cookies.items())

    def _absorb_cookies(self, heads):
        raw = (heads or {}).get('Set-Cookie') or ''
        for part in raw.split(','):
            part = part.strip()
            if '=' not in part:
                continue
            name, _, rest = part.partition('=')
            value = rest.split(';')[0].strip()
            if name:
                self.cookies[name] = value

    def _fetch_once(self, path):
        url = path if path.startswith('http') else self.site + path
        status, text, heads = http_response(url, headers={
            'User-Agent': self.UA,
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'zh-CN,zh;q=0.9',
            'Referer': self.site + '/',
        }, timeout=20)
        if heads:
            self._absorb_cookies(heads)
        if not text:
            print('[短剧one] 空响应: %s' % url)
            return ''
        return text

    def _fetch(self, path):
        body = self._fetch_once(path)
        if not self.RE_NOTICE.search(body):
            return body
        if not self._ensure_access():
            return ''
        body = self._fetch_once(path)
        if self.RE_NOTICE.search(body):
            print('[短剧one] 访问确认失败（仍返回网络提示页）')
            return ''
        return body

    def _ensure_access(self):
        if self.accessed:
            return True
        notice = self._fetch_once('/')
        found = self.RE_TOKEN.search(notice)
        if not found:
            if not self.RE_NOTICE.search(notice):
                self.accessed = True
                return True
            print('[短剧one] 提示页缺少 _token')
            return False
        status, text, heads = http_response(self.ACCEPT_URL, method='POST',
                                            body='_token=' + quote(found.group(1), safe=''),
                                            headers={
                                                'User-Agent': self.UA,
                                                'Content-Type': 'application/x-www-form-urlencoded',
                                                'Referer': self.ACCESS_HOST + '/',
                                                'Origin': self.ACCESS_HOST,
                                                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
                                                'Accept-Language': 'zh-CN,zh;q=0.9',
                                            }, timeout=20)
        if heads:
            self._absorb_cookies(heads)
        if status >= 400:
            print('[短剧one] 访问确认失败 HTTP %d' % status)
            return False
        self.accessed = True
        return True

    def _abs_url(self, raw):
        raw = html_unescape((raw or '').strip())
        if not raw:
            return ''
        if raw.startswith('http://') or raw.startswith('https://'):
            return raw
        if raw.startswith('//'):
            return 'https:' + raw
        if raw.startswith('/'):
            return self.site + raw
        return self.site + '/' + raw

    def cats(self):
        cats = [{'type_id': item[0], 'type_name': item[1]} for item in self.CATS]
        body = self._fetch('/dramas')
        seen = {'all', 'free', 'vip'}
        if body:
            nav = self.RE_TAG_NAV.search(body)
            if nav:
                for found in self.RE_TAG_A.finditer(nav.group(1)):
                    slug = found.group(1)
                    name = clean_text(strip_tags(found.group(2)))
                    name = clean_text(re.sub(r'\d+\s*$', '', name))
                    tag = 'tag:' + slug
                    if not slug or not name or tag in seen:
                        continue
                    seen.add(tag)
                    cats.append({'type_id': tag, 'type_name': name})
        return cats

    def _cards(self, page_html):
        cards = []
        seen = set()
        for found in self.RE_CARD.finditer(page_html):
            vid = found.group(1)
            if not vid or vid in seen:
                continue
            block = found.group(2)
            title = ''
            t = self.RE_TITLE.search(block)
            if t:
                title = clean_text(strip_tags(t.group(1)))
            if not title:
                a = self.RE_ALT.search(block)
                if a:
                    title = clean_text(re.sub(r'封面$', '', html_unescape(a.group(1))))
            if not title:
                continue
            seen.add(vid)
            card = {'vod_id': vid, 'vod_name': title}
            img = self.RE_COVER.search(block)
            if img:
                low = img.group(1).lower()
                if not low.startswith('data:') and not self.RE_NON_IMG.search(low):
                    card['vod_pic'] = _img_proxy_url(
                        self._abs_url(img.group(1)), self.site + '/')
            parts = []
            ep = self.RE_EP_COUNT.search(block)
            if ep:
                value = clean_text(strip_tags(ep.group(1)))
                if value:
                    parts.append(value)
            up = self.RE_UPDATED.search(block)
            if up:
                value = clean_text(strip_tags(up.group(1)))
                if value:
                    parts.append(value)
            card['vod_remarks'] = ' · '.join(parts)
            tags = self.RE_TAGS.search(block)
            if tags:
                names = []
                for s in self.RE_TAG_SPAN.finditer(tags.group(1)):
                    value = clean_text(strip_tags(s.group(1)))
                    if value:
                        names.append(value)
                if names:
                    card['vod_class'] = '/'.join(names)
            cards.append(card)
        if len(cards) > self.PAGE_SIZE * 2:
            cards = cards[:self.PAGE_SIZE * 2]
        return cards

    def _list_url(self, cat, page):
        if cat.startswith('tag:'):
            return '%s/tag/%s?page=%d' % (self.site, quote(cat[4:], safe=''), page)
        path = '/dramas'
        if cat in ('free', 'vip'):
            path += '?filter=' + cat
        if page > 1:
            path += ('&page=' if '?' in path else '?page=') + str(page)
        return self.site + path

    def listing(self, cat, page):
        page = page if page and page > 0 else 1
        if page > self.MAX_PAGES:
            return []
        cat = str(cat or '').strip() or 'all'
        body = self._fetch(self._list_url(cat, page))
        if not body:
            return []
        return self._cards(body)

    def search(self, wd, page):
        wd = str(wd or '').strip()
        if not wd:
            return []
        page = page if page and page > 0 else 1
        body = self._fetch('%s/dramas?q=%s&page=%d' % (self.site, quote(wd, safe=''), page))
        if not body:
            return []
        return self._cards(body)

    def detail(self, vid):
        vid = str(vid or '').strip()
        if '/' in vid:
            vid = vid[vid.rfind('/') + 1:]
        if not vid:
            print('[短剧one] 无效的剧集 ID')
            return {}
        body = self._fetch('/drama/' + quote(vid, safe=''))
        if not body:
            return {}
        name = ''
        m = self.RE_OG_TTL.search(body)
        if m:
            name = clean_text(html_unescape(m.group(1)))
            name = re.sub(r'(在线观看\s*-\s*短剧one|-?\s*短剧one)$', '', name).strip()
        if not name:
            t = re.search(r'(?is)<title>([\s\S]*?)</title>', body)
            if t:
                name = clean_text(html_unescape(t.group(1)).split('-', 1)[0])
        pic = ''
        m = self.RE_OG_IMG.search(body)
        if m:
            pic = self._abs_url(m.group(1))
        desc = ''
        m = self.RE_META.search(body)
        if m:
            desc = clean_text(html_unescape(m.group(1)))
        eps = {}
        for found in self.RE_EP_LINK.finditer(body):
            no = atoi(found.group(1), 0)
            if no < 1 or no in eps:
                continue
            label = ''
            b = self.RE_EP_NUM.search(found.group(2))
            if b and b.group(1) != str(no):
                label = clean_text(strip_tags(found.group(2)))
            eps[no] = label
        if not eps:
            print('[短剧one] 详情页无剧集: %s' % vid)
            return {}
        episodes = []
        for no in sorted(eps):
            episodes.append({
                'no': no,
                'name': eps[no] or ('第%d集' % no),
                'url': '%s://%s/%d' % (self.key, vid, no),
            })
        return {
            'vod_id': vid,
            'vod_name': name or ('短剧one ' + vid),
            'vod_pic': pic,
            'vod_content': desc,
            'vod_class': '',
            'vod_remarks': '共%d集' % len(episodes),
            'episodes': episodes,
        }

    def play(self, url):
        raw = str(url or '').strip()
        prefix = self.key + '://'
        vid, extra = '', ''
        if raw.startswith(prefix):
            rest = raw[len(prefix):].strip('/')
            if '/' in rest:
                vid, extra = rest.split('/', 1)
            else:
                vid = rest
        elif '/drama/' in raw:
            rest = raw[raw.find('/drama/') + len('/drama/'):].strip('/')
            parts = [part for part in rest.split('/') if part]
            if parts:
                vid = parts[0]
                if len(parts) >= 3 and parts[1] == 'ep':
                    extra = parts[2]
        if not vid:
            print('[短剧one] 播放参数无效')
            return {}
        seq = atoi(extra, 1)
        if seq < 1:
            seq = 1
        path = '/drama/' + quote(vid, safe='')
        if seq > 1:
            path += '/ep/%d' % seq
        body = self._fetch(path)
        if not body:
            return {}
        media = ''
        m = self.RE_VIDEO.search(body)
        if m:
            media = html_unescape(m.group(1).strip())
        if not media:
            m = self.RE_SOURCE.search(body)
            if m:
                media = html_unescape(m.group(1).strip())
        if not is_http_media(media):
            print('[短剧one] 播放页无媒体地址')
            return {}
        return {'url': media, 'header': {'Referer': self.site + '/'}, 'parse': 0}

import os
import re
import json
import time
import random
import base64
import hashlib
import gzip
import hmac
from urllib.parse import quote, urlencode, urlparse

def _huangdou_uuid():
    raw = ''.join(random.choice('0123456789abcdef') for _ in range(32))
    return '%s-%s-%s-%s-%s' % (raw[:8], raw[8:12], raw[12:16], raw[16:20], raw[20:])

def _huangdou_key(rid):
    clean = rid.replace('-', '')
    b = bytes.fromhex(clean)
    return hmac.new(HuangdouSite.PLATFORM_KEY.encode('utf-8'), b,
                    hashlib.sha256).digest()

def _huangdou_decode(blob, key):
    try:
        return json.loads(blob.decode('utf-8'))
    except Exception:
        pass
    if len(blob) < 32:
        raise ValueError('黄豆响应太短或格式异常')
    plain = aes_cbc_decrypt(key, blob[:16], blob[16:])
    if len(plain) >= 2 and plain[0] == 0x1f and plain[1] == 0x8b:
        plain = gzip.decompress(plain)
    return json.loads(plain.decode('utf-8'))

def _huangdou_data(decoded):
    if isinstance(decoded, dict) and isinstance(decoded.get('data'), dict):
        return decoded['data']
    return decoded if isinstance(decoded, dict) else {}

def _huangdou_episodes(data):
    x = data.get('episodes')
    if isinstance(x, list):
        return [m for m in x if isinstance(m, dict)]
    for key in ('sort', 'chapters', 'list'):
        sub = data.get(key)
        if isinstance(sub, dict):
            got = _huangdou_episodes(sub)
            if got:
                return got
        if isinstance(sub, list):
            got = [m for m in sub if isinstance(m, dict)]
            if got:
                return got
    return []

def _huangdou_preview_only(data, media, host):
    flag = map_str(data, 'is_preview', 'isPreview').lower()
    if flag in ('true', '1', 'y'):
        return True
    preview = first_non_empty(map_str(data, 'preview_m3u8'),
                              map_str(data, 'preview_url'))
    if preview and (not media or
                    resolve_url(host + '/', media) == resolve_url(host + '/', preview)):
        return True
    try:
        path = urlparse(media or '').path.lower()
    except Exception:
        return False
    return (path in ('preview.mp4', 'preview.m3u8') or
            path.endswith('/preview.mp4') or path.endswith('/preview.m3u8'))

class HuangdouSite(object):

    key = 'huangdou'
    name = '黄豆🪙'
    site = 'https://tideember.cc'

    FALLBACK = 'https://xqjurgek.top'
    DEVICE_TYPE = 'web'
    PLATFORM_KEY = '7961beb44246e3012ce228d6b5ced05a'
    VERSION = '2.0.0'
    CATS = [
        ('all', '全部'),
    ]

    def __init__(self):
        self.session_id = _huangdou_uuid()
        self.device_id = self.session_id

    def cats(self):
        return [{'type_id': item[0], 'type_name': item[1]} for item in self.CATS]

    def _call(self, path, data):
        last_err = '黄豆接口调用失败'
        for idx, host in enumerate((self.site, self.FALLBACK)):
            decoded, err = self._call_once(host, path, data)
            if err is None:
                return decoded
            last_err = err
            if err.startswith('ACCESS_ERR:'):
                break
            if idx >= 1:
                break
        print('[黄豆] ' + last_err)
        return None

    def _call_once(self, host, path, data):
        path = '/' + path.lstrip('/')
        rid = _huangdou_uuid()
        key = _huangdou_key(rid)
        iv = os.urandom(16)
        plain = json.dumps({'token': '', 'deviceId': self.device_id, 'data': data},
                           separators=(',', ':'))
        gz = gzip.compress(plain.encode('utf-8'))
        body = iv + aes_cbc_encrypt(key, iv, gz)
        target = host.rstrip('/') + '/api' + path
        timestamp = str(int(time.time()))
        sign_raw = 'Dart|%s|%s|%s|%s' % (self.session_id, rid, timestamp, path)
        sign = hashlib.sha256(sign_raw.encode('utf-8')).hexdigest() + '-' + timestamp
        headers = {
            'User-Agent': ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                           'AppleWebKit/537.36 (KHTML, like Gecko) '
                           'Chrome/120 Safari/537.36'),
            'Accept': '*/*',
            'Origin': host,
            'Referer': host + '/home',
            'Content-Type': 'application/octet-stream',
            'version': self.VERSION,
            'deviceType': self.DEVICE_TYPE,
            'requestId': rid,
            'sessionId': self.session_id,
            'time': timestamp,
            'sign': sign,
        }
        raw = http_bytes(target, headers=headers, timeout=25, method='POST', data=body)
        if not raw:
            return None, '黄豆接口网络失败'
        try:
            decoded = _huangdou_decode(raw, key)
        except Exception as exc:
            return None, '黄豆响应解码失败: %s' % exc
        if isinstance(decoded, dict):
            status = map_str(decoded, 'status')
            if status and status != 'y':
                code = map_str(decoded, 'errorCode', 'error_code', 'code')
                msg = ('黄豆接口拒绝请求: ' +
                       first_non_empty(map_str(decoded, 'msg', 'message', 'error'),
                                       status))
                if code in ('813004', '813005', '813006', '813103', 'preview'):
                    return None, 'ACCESS_ERR:' + msg
                return None, msg
        return decoded, None

    def listing(self, cat, page):
        if page < 1:
            page = 1
        cat = str(cat or '').strip() or 'all'
        decoded = self._call('/drama/rank', {'tab': cat, 'page': str(page)})
        if decoded is None:
            return []
        items = _huangdou_data(decoded).get('list')
        if not isinstance(items, list):
            print('[黄豆] 榜单没有返回内容')
            return []
        cards = []
        for row in items:
            if not isinstance(row, dict):
                continue
            raw_id = first_non_empty(map_str(row, 'id'), map_str(row, 'drama_id'))
            vid = raw_id[3:] if raw_id.startswith('rp_') else raw_id
            if not vid:
                continue
            pic = first_non_empty(map_str(row, 'img_y', 'img_x', 'img', 'cover', 'pic'))
            remark = first_non_empty(map_str(row, 'update_label'),
                                     map_str(row, 'corner'))
            if not remark:
                eps = map_str(row, 'episode_count')
                if eps:
                    remark = '全' + eps + '集'
            cards.append({
                'vod_id': vid,
                'vod_name': first_non_empty(map_str(row, 'name', 'title', 't'), vid),
                'vod_pic': resolve_url(self.site + '/', pic) if pic else '',
                'vod_remarks': remark,
                'vod_class': first_non_empty(map_str(row, 'category',
                                                     'category_name',
                                                     'categoryName')),
                'vod_score': map_str(row, 'hot_rate'),
                'vod_content': first_non_empty(map_str(row, 'description'),
                                               map_str(row, 'summary')),
            })
        return cards

    def search(self, wd, page):
        print('[黄豆] 黄豆平台暂不支持关键词搜索')
        return []

    def detail(self, vid):
        vid = str(vid or '').strip()
        vid = vid[3:] if vid.startswith('rp_') else vid
        if not vid:
            print('[黄豆] 无效剧集 ID')
            return {}
        decoded = self._call('/drama/detail', {'id': vid})
        if decoded is None:
            return {}
        data = _huangdou_data(decoded)
        back = map_str(data, 'id', 'drama_id')
        back = back[3:] if back.startswith('rp_') else back
        if back != vid:
            print('[黄豆] 详情返回了其他剧集')
            return {}
        episodes = []
        seen = set()
        for ep in _huangdou_episodes(data):
            seq = atoi(first_non_empty(map_str(ep, 'seq', 'episode', 'ep')), 0)
            if seq <= 0 or seq in seen:
                continue
            seen.add(seq)
            episodes.append({
                'no': seq,
                'name': first_non_empty(map_str(ep, 'name', 'title'), '第%d集' % seq),
                'url': 'huangdou://%s/%d' % (vid, seq),
            })
        if not episodes:
            print('[黄豆] 详情没有返回剧集列表')
            return {}
        pic = first_non_empty(map_str(data, 'img_y', 'img_x', 'img', 'cover', 'pic'))
        return {
            'vod_id': vid,
            'vod_name': first_non_empty(map_str(data, 'name', 'title', 't'), vid),
            'vod_pic': resolve_url(self.site + '/', pic) if pic else '',
            'vod_content': first_non_empty(map_str(data, 'description'),
                                           map_str(data, 'summary')),
            'vod_class': first_non_empty(map_str(data, 'category',
                                                 'category_name', 'categoryName')),
            'vod_remarks': '共%d集' % len(episodes),
            'episodes': episodes,
        }

    def play(self, url):
        raw = str(url or '').strip().strip('/')
        prefix = 'huangdou://'
        if not raw.startswith(prefix):
            print('[黄豆] 播放参数无效')
            return {}
        rest = raw[len(prefix):].strip('/')
        cut = rest.rfind('/')
        if cut <= 0 or cut >= len(rest) - 1:
            print('[黄豆] 播放参数无效')
            return {}
        vid, seq_text = rest[:cut], rest[cut + 1:]
        seq = atoi(seq_text, 0)
        if seq < 1 or not vid:
            print('[黄豆] 播放参数无效')
            return {}
        decoded = self._call('/drama/play', {'id': vid, 'seq': str(seq)})
        if decoded is None:
            return {}
        data = _huangdou_data(decoded)
        host = self.site
        media = first_non_empty(map_str(data, 'm3u8'), map_str(data, 'url'),
                                map_str(data, 'play_url'), map_str(data, 'playUrl'))
        ua = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
              '(KHTML, like Gecko) Chrome/120 Safari/537.36')
        if _huangdou_preview_only(data, media, host):
            free = (host + '/api/drama/hls/' + quote(vid, safe='-_.~') + '/' +
                    str(seq) + '/play.m3u8?line=free')
            body = http_bytes(free, headers={'User-Agent': ua,
                                             'Referer': host + '/home'},
                              timeout=25)
            if body and body.strip().startswith(b'#EXTM3U'):
                return {'url': free,
                        'header': {'User-Agent': ua, 'Referer': host + '/home'},
                        'parse': 0}
            print('[黄豆] 仅提供试看，未取得该集正片')
            return {}
        if not media:
            media = (host + '/api/drama/hls/' + quote(vid, safe='-_.~') + '/' +
                     str(seq) + '/play.m3u8?line=free')
            body = http_bytes(media, headers={'User-Agent': ua,
                                              'Referer': host + '/home'},
                              timeout=25)
            if not body or not body.strip().startswith(b'#EXTM3U'):
                print('[黄豆] 未提供有效的播放地址，备用播放列表也不可用')
                return {}
        if not (media.startswith('http://') or media.startswith('https://')):
            media = resolve_url(host + '/', media)
        if not is_http_media(media):
            print('[黄豆] 播放地址不是 HTTP/HTTPS URL')
            return {}
        return {'url': media,
                'header': {'User-Agent': ua, 'Referer': host + '/home'},
                'parse': 0}

RE_KB_VIDEO_SRC = re.compile(r'(?is)<video[^>]+src="([^"]+)"')
RE_KB_JSON_URL = re.compile(
    r'"(?:hlsUrl|playUrl|videoUrl|sourceUrl|mediaUrl|trailerUrl)"\s*:\s*'
    r'"([^"]+?\.(?:m3u8|mp4)[^"]*)"')
RE_KB_BARE = re.compile(r'https?://[^"\\\s<>]+?\.(?:mp4|m3u8)[^"\\\s<>]*')

def _kuangbiao_qescape(s):
    return quote(s, safe='').replace('%20', '+')

def _kuangbiao_adult(item):
    drama = item.get('drama')
    if not isinstance(drama, dict):
        drama = item
    for tag in as_list(drama.get('tags')):
        if not isinstance(tag, dict):
            continue
        name = map_str(tag, 'name')
        if name.startswith('adult-') or '不伦' in name or '偷情' in name:
            return True
    return False

def _kuangbiao_drama(item):
    drama = item.get('drama')
    if isinstance(drama, dict) and drama:
        return drama
    return item

def _kuangbiao_min_cover(raw):
    if not raw or '_minimize.' in raw:
        return raw
    path, query = raw, ''
    if '?' in raw:
        path, query = raw.split('?', 1)
        query = '?' + query
    if path.rfind('.') > path.rfind('/'):
        return path[:path.rfind('.')] + '_minimize.webp' + query
    return raw

def _kuangbiao_abs(raw):
    raw = str(raw or '').strip()
    if not raw:
        return ''
    if raw.startswith('http://') or raw.startswith('https://'):
        return raw
    if raw.startswith('//'):
        return 'https:' + raw
    if not raw.startswith('/'):
        raw = '/' + raw
    return KuangbiaoSite.site + raw

def _kuangbiao_unescape(s):
    return str(s or '').replace('\\u0026', '&').replace('\\/', '/')

def _kuangbiao_extract_src(page):
    page = _kuangbiao_unescape(page)
    m = RE_KB_VIDEO_SRC.search(page)
    if m:
        u = _kuangbiao_abs(m.group(1))
        if is_http_media(u):
            return u
    m = RE_KB_JSON_URL.search(page)
    if m:
        u = _kuangbiao_abs(m.group(1))
        if is_http_media(u):
            return u
    m = RE_KB_BARE.search(page)
    if m:
        u = _kuangbiao_abs(m.group(0))
        if is_http_media(u):
            return u
    return ''

def _kuangbiao_cat_name(cat):
    for c in KuangbiaoSite.CATS:
        if c[0] == cat:
            return c[1]
    return ''

class KuangbiaoSite(object):

    key = 'kuangbiao'
    name = '狂飙'
    site = 'https://ai.dramarush.tv'

    UA = ('Mozilla/5.0 (Linux; Android 12; Mobile) AppleWebKit/537.36 '
          'Chrome/131.0 Safari/537.36')
    PAGE_SIZE = 12
    MAX_EPISODES = 80
    MAX_PAGES = 6
    COVER_CDN_LSJ = 'https://cdn.shorttv.online/lsj/'
    COVER_RAW_LSJ = 'https://raw.shorttv.online/lsj/'
    PLAY_PREFIX = 'https://raw.shorttv.online/uploads/direct/'
    COVER_PLACEHOLDER = 'dc/img/828ed491f008e85d9caef01a.jpg'

    CATS = [
        ('t-5jxcit', '短剧'),
        ('normal_short', '正规短剧'),
        ('adult_short', '成人短剧'),
    ]
    SPECS = {
        't-5jxcit': ({'categorySlug': 't-5jxcit'}, 'all', 1),
        'normal_short': ({'contentKind': 'SHORT_DRAMA'}, 'normal', 6),
        'adult_short': ({'tagSlug': 'adult'}, 'adult', 6),
    }

    def __init__(self):
        self._ep_cache = {}

    def cats(self):
        return [{'type_id': item[0], 'type_name': item[1]} for item in self.CATS]

    def _trpc(self, name, payload):
        target = self.site + '/api/trpc/' + name
        if payload:
            wrapped = json.dumps({'json': payload}, separators=(',', ':'),
                                 ensure_ascii=False)
            target += '?input=' + _kuangbiao_qescape(wrapped)
        status, text = http_get(target, headers={
            'User-Agent': self.UA,
            'Referer': self.site + '/zh/',
            'Accept': 'application/json, text/plain, */*',
        }, timeout=20)
        if status >= 400 or not text:
            return {}
        try:
            envelope = json.loads(text)
        except Exception:
            return {}
        node = envelope.get('result')
        node = node.get('data') if isinstance(node, dict) else None
        out = node.get('json') if isinstance(node, dict) else None
        return out if isinstance(out, dict) else {}

    def _browse_once(self, spec, cursor):
        query, mode, rounds = spec
        data = {'limit': self.PAGE_SIZE}
        data.update(query)
        out, seen, next_cursor = [], set(), ''
        for _ in range(rounds):
            if cursor:
                data['cursor'] = cursor
            body = self._trpc('feed.browse', data)
            if not body:
                break
            for raw_item in as_list(body.get('items')):
                if not isinstance(raw_item, dict):
                    continue
                if mode == 'adult' and not _kuangbiao_adult(raw_item):
                    continue
                if mode == 'normal' and _kuangbiao_adult(raw_item):
                    continue
                drama = _kuangbiao_drama(raw_item)
                key = map_str(drama, 'id').strip() or clean_text(map_str(drama, 'title'))
                if not key or key in seen:
                    continue
                seen.add(key)
                out.append(raw_item)
            next_cursor = map_str(body, 'nextCursor').strip()
            if len(out) >= self.PAGE_SIZE or not next_cursor:
                break
            cursor = next_cursor
        return out, next_cursor

    def _cover(self, drama):
        for k in ('cover', 'poster'):
            raw = map_str(drama, k)
            if not raw or self.COVER_PLACEHOLDER in raw:
                continue
            raw = raw.replace(self.COVER_CDN_LSJ, self.COVER_RAW_LSJ, 1)
            fixed = _kuangbiao_min_cover(raw)
            if fixed:
                return resolve_url(self.site + '/zh/', fixed)
        return ''

    def _card(self, item, cat_name):
        drama = _kuangbiao_drama(item)
        vid = map_str(drama, 'id').strip()
        name = clean_text(map_str(drama, 'title'))
        if not vid or not name:
            return None
        card = {
            'vod_id': vid,
            'vod_name': name,
            'vod_pic': self._cover(drama),
            'vod_remarks': '',
            'vod_class': cat_name,
            'vod_score': '',
            'vod_content': '',
        }
        total = atoi(map_str(drama, 'totalEpisodes'), 0)
        if total > 0:
            card['vod_remarks'] = '%d集' % total
        desc = clean_text(map_str(drama, 'description'))
        if desc:
            card['vod_content'] = truncate(desc, 200)
        return card

    def listing(self, cat, page):
        if page < 1:
            page = 1
        if page > self.MAX_PAGES:
            page = self.MAX_PAGES
        cat_text = str(cat or '').strip()
        spec = self.SPECS.get(cat_text, self.SPECS['t-5jxcit'])
        cursor = ''
        target = []
        for i in range(1, page + 1):
            items, next_cursor = self._browse_once(spec, cursor)
            if i == page:
                target = items
            cursor = next_cursor
            if not next_cursor:
                break
        cards = []
        for raw_item in target:
            if not isinstance(raw_item, dict):
                continue
            card = self._card(raw_item, _kuangbiao_cat_name(cat_text))
            if card:
                cards.append(card)
        if not cards:
            if page > 1:
                return []
            print('[狂飙] 列表无内容')
        return cards

    def search(self, wd, page):
        wd = str(wd or '').strip()
        if not wd:
            return []
        if page < 1:
            page = 1
        cards = self.listing('t-5jxcit', page)
        return [c for c in cards if wd in c['vod_name']]

    def _episodes(self, drama_id, no):
        cached = self._ep_cache.get(drama_id)
        if cached:
            return cached
        body = self._trpc('episode.watch',
                          {'dramaId': drama_id, 'episodeNumber': no})
        table = {}
        for raw_ep in as_list(body.get('episodes')):
            if not isinstance(raw_ep, dict):
                continue
            idx = atoi(map_str(raw_ep, 'index'), 0)
            ep_id = map_str(raw_ep, 'id').strip()
            if idx > 0 and ep_id:
                table[idx] = ep_id
        if table:
            if len(self._ep_cache) > 512:
                self._ep_cache = {}
            self._ep_cache[drama_id] = table
        return table

    def detail(self, vid):
        vid = str(vid or '').strip()
        if not vid:
            print('[狂飙] 无效剧集 ID')
            return {}
        body = self._trpc('episode.watch', {'dramaId': vid, 'episodeNumber': 1})
        if not body:
            print('[狂飙] 详情读取失败')
            return {}
        drama = body.get('drama')
        if not isinstance(drama, dict):
            drama = {}
        table = {}
        for raw_ep in as_list(body.get('episodes')):
            if not isinstance(raw_ep, dict):
                continue
            idx = atoi(map_str(raw_ep, 'index'), 0)
            ep_id = map_str(raw_ep, 'id').strip()
            if idx > 0 and ep_id:
                table[idx] = ep_id
        if len(self._ep_cache) > 512:
            self._ep_cache = {}
        self._ep_cache[vid] = table
        total = atoi(map_str(drama, 'totalEpisodes'), 0)
        if total < len(table):
            total = len(table)
        if total < 1:
            total = 1
        if total > self.MAX_EPISODES:
            total = self.MAX_EPISODES
        episodes = [{'no': i, 'name': '第%d集' % i,
                     'url': 'kuangbiao://%s/%d' % (vid, i)}
                    for i in range(1, total + 1)]
        if not episodes:
            print('[狂飙] 详情无剧集: %s' % vid)
            return {}
        return {
            'vod_id': vid,
            'vod_name': clean_text(map_str(drama, 'title')) or ('狂飙短剧 ' + vid),
            'vod_pic': self._cover(drama),
            'vod_content': truncate(clean_text(map_str(drama, 'description')), 400),
            'vod_class': '狂飙',
            'vod_remarks': '共%d集' % len(episodes),
            'episodes': episodes,
        }

    def _watch_page_src(self, drama_id, no):
        target = '%s/zh/watch/%s/%d' % (self.site, quote(drama_id, safe='-_.~'), no)
        status, text = http_get(target, headers={
            'User-Agent': self.UA,
            'Referer': target,
            'Accept': ('text/html,application/xhtml+xml,application/xml;q=0.9,'
                       '*/*;q=0.8'),
        }, timeout=20)
        if status >= 400 or not text:
            return ''
        return _kuangbiao_extract_src(text)

    def play(self, url):
        raw = str(url or '').strip().strip('/')
        prefix = 'kuangbiao://'
        if not raw.startswith(prefix):
            print('[狂飙] 播放参数无效')
            return {}
        rest = raw[len(prefix):].strip('/')
        cut = rest.rfind('/')
        if cut <= 0 or cut >= len(rest) - 1:
            print('[狂飙] 播放参数无效')
            return {}
        drama_id, seq_text = rest[:cut], rest[cut + 1:]
        no = atoi(seq_text, 1)
        if no < 1:
            no = 1
        header = {'User-Agent': self.UA, 'Referer': self.site + '/zh/'}
        table = self._episodes(drama_id, no)
        if table:
            ep_id = table.get(no, '').strip()
            if ep_id:
                return {'url': self.PLAY_PREFIX + ep_id + '/video.mp4',
                        'header': header, 'parse': 0}
        src = self._watch_page_src(drama_id, no)
        if src:
            return {'url': src, 'header': header, 'parse': 0}
        print('[狂飙] 第%d集无播放地址' % no)
        return {}

def _wusheng_split_id(vid):
    vid = str(vid or '').strip()
    if '@' in vid:
        head, _, tail = vid.partition('@')
        return head.strip(), tail.strip()
    return vid, ''

def _wusheng_ep_url(chapter):
    for raw_list in as_list(chapter.get('shortPlayList')):
        if not isinstance(raw_list, dict):
            continue
        for raw_vo in as_list(raw_list.get('chapterShortPlayVoList')):
            if not isinstance(raw_vo, dict):
                continue
            u = map_str(raw_vo, 'shortPlayUrl').strip()
            if u:
                return u
    return ''

def _wusheng_cat_name(cat_id):
    for c in WushengSite.CATS:
        if c[0] == cat_id:
            return c[1]
    return ''

class WushengSite(object):

    key = 'wusheng'
    name = '悟圣'
    site = 'http://read.api.duodutek.com'

    TOKEN = '202509271001001446030204698626'
    UA = ('Mozilla/5.0 (Windows NT 6.1; WOW64) AppleWebKit/537.36 '
          '(KHTML, like Gecko) Chrome/50.0.2661.87 Safari/537.36')
    PRODUCT_ID = '2a8c14d1-72e7-498b-af23-381028eb47c0'
    VEST_ID = '2be070e0-c824-4d0e-a67a-8f688890cadb'
    CHANNEL = 'oppo19'
    OS_TYPE = 'android'
    VERSION = '20'
    PAGE_SIZE = 20
    DESC_MAX = 200
    MAX_EPISODES = 200

    CATS = [
        ('1287', '甜宠'),
        ('1288', '逆袭'),
        ('1289', '热血'),
        ('1290', '现代'),
        ('1291', '古代'),
    ]

    def __init__(self):
        pass

    def cats(self):
        return [{'type_id': item[0], 'type_name': item[1]} for item in self.CATS]

    def _base_query(self, extra=None):
        q = {
            'productId': self.PRODUCT_ID,
            'vestId': self.VEST_ID,
            'channel': self.CHANNEL,
            'osType': self.OS_TYPE,
            'version': self.VERSION,
            'token': self.TOKEN,
        }
        if extra:
            q.update(extra)
        return q

    def _get_json(self, path, params):
        status, text = http_get(self.site + path + '?' + urlencode(params),
                                headers={'User-Agent': self.UA,
                                         'Accept': 'application/json, text/plain, */*'},
                                timeout=20)
        if status >= 400 or not text:
            print('[悟圣] 接口 HTTP %s' % status)
            return None
        try:
            envelope = json.loads(text)
        except Exception as exc:
            print('[悟圣] 接口响应无法解析: %s' % exc)
            return None
        if 'code' in envelope:
            code = atoi(str(envelope.get('code')), 0)
            if code != 0 and code != 200:
                print('[悟圣] 接口返回错误: %s' % map_str(envelope, 'msg'))
                return None
        return envelope

    def _card(self, item, cat_name):
        vid = map_str(item, 'id').strip()
        name = clean_text(map_str(item, 'name'))
        if not vid or not name:
            return None
        cover = map_str(item, 'icon')
        card = {
            'vod_id': vid + '@' + name,
            'vod_name': name,
            'vod_pic': resolve_url(self.site + '/', cover) if cover else '',
            'vod_remarks': clean_text(map_str(item, 'heat')) + '万播放',
            'vod_class': cat_name,
            'vod_score': '',
            'vod_content': '',
        }
        desc = clean_text(map_str(item, 'introduction'))
        if desc:
            card['vod_content'] = truncate(desc, self.DESC_MAX)
        return card

    def listing(self, cat, page):
        if page < 1:
            page = 1
        cat_text = str(cat or '').strip()
        cat_id = cat_text
        if cat_id not in [c[0] for c in self.CATS]:
            cat_id = self.CATS[0][0]
        params = self._base_query({'resourceId': cat_id, 'pageNum': str(page),
                                   'pageSize': str(self.PAGE_SIZE)})
        envelope = self._get_json('/novel-api/app/pageModel/getResourceById', params)
        if envelope is None:
            return []
        data = envelope.get('data')
        items = data.get('datalist') if isinstance(data, dict) else None
        if not isinstance(items, list):
            items = []
        cards = []
        seen = set()
        for raw_item in items:
            if not isinstance(raw_item, dict):
                continue
            card = self._card(raw_item, _wusheng_cat_name(cat_id))
            if not card:
                continue
            book_id = card['vod_id'].split('@', 1)[0]
            if book_id in seen:
                continue
            seen.add(book_id)
            cards.append(card)
        if not cards:
            if page > 1:
                return []
            print('[悟圣] 列表无内容')
        return cards

    def search(self, wd, page):
        print('[悟圣] 悟圣未提供搜索')
        return []

    def detail(self, vid):
        vid = str(vid or '').strip()
        book_id, name = _wusheng_split_id(vid)
        if not book_id:
            print('[悟圣] 无效剧集 ID')
            return {}
        envelope = self._get_json('/novel-api/basedata/book/getChapterList',
                                  self._base_query({'bookId': book_id}))
        if envelope is None:
            return {}
        chapters = envelope.get('data')
        if not isinstance(chapters, list):
            chapters = []
        episodes = []
        seen = set()
        pic = ''
        for i, raw_chapter in enumerate(chapters):
            if len(episodes) >= self.MAX_EPISODES:
                break
            if not isinstance(raw_chapter, dict):
                continue
            src = _wusheng_ep_url(raw_chapter)
            if not src:
                continue
            no = atoi(map_str(raw_chapter, 'chapterIndex'), 0)
            if no < 1:
                no = i + 1
            if no in seen:
                continue
            seen.add(no)
            ep_name = clean_text(map_str(raw_chapter, 'chapterName'))
            if not ep_name:
                ep_name = '第%d集' % no
            episodes.append({'no': no, 'name': ep_name, 'url': src})
            if not pic:
                cover = map_str(raw_chapter, 'iconUrl')
                if cover.startswith('http') and cover.rstrip('/') != self.site:
                    pic = resolve_url(self.site + '/', cover)
        episodes = sort_episodes(episodes)
        if not episodes:
            print('[悟圣] 详情无剧集: %s' % book_id)
            return {}
        return {
            'vod_id': vid,
            'vod_name': name or ('悟圣短剧 ' + book_id),
            'vod_pic': pic,
            'vod_content': '',
            'vod_class': '悟圣',
            'vod_remarks': '共%d集' % len(episodes),
            'episodes': episodes,
        }

    def play(self, url):
        target = str(url or '').strip()
        if not target or not target.startswith('http'):
            print('[悟圣] 播放参数无效')
            return {}
        return {'url': target,
                'header': {'User-Agent': self.UA, 'Referer': self.site + '/'},
                'parse': 0}

def _qixing_ok(envelope):
    if 'code' not in envelope:
        return True
    v = envelope.get('code')
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return v == 0 or v == 200
    s = str(v).strip()
    if s.lower() == 'ok':
        return True
    try:
        n = int(s)
    except (TypeError, ValueError):
        return False
    return n == 0 or n == 200

def _qixing_card(item, cat_name):
    vid = map_str(item, 'id').strip()
    name = clean_text(map_str(item, 'title'))
    if not vid or not name:
        return None
    cover = map_str(item, 'cover_url')
    card = {
        'vod_id': vid,
        'vod_name': name,
        'vod_pic': resolve_url(QixingSite.site + '/', cover) if cover else '',
        'vod_remarks': first_non_empty(clean_text(map_str(item, 'play_amount_str')),
                                       clean_text(map_str(item, 'score_str'))),
        'vod_class': cat_name,
        'vod_score': '',
        'vod_content': '',
    }
    desc = clean_text(map_str(item, 'introduction'))
    if desc:
        card['vod_content'] = truncate(desc, QixingSite.DESC_MAX)
    return card

def _qixing_cat(cat):
    for c in QixingSite.CATS:
        if c[0] == cat:
            return c[0]
    return QixingSite.CATS[0][0]

def _qixing_cat_name(cat):
    for c in QixingSite.CATS:
        if c[0] == cat:
            return c[1]
    return ''

class QixingSite(object):

    key = 'qixing'
    name = '七星'
    site = 'https://app.whjzjx.cn'

    AUTH_BASE = 'https://u.shytkjgs.com'
    AES_KEY = 'B@ecf920Od8A4df7'
    DEVICE = '2a50580e69d38388c94c93605241fb306'
    ANDROID_ID = 'ec1280db12795506'
    PACKAGE = 'com.jz.xydj'
    VERSION_NAME = '3.8.3.1'
    UA = ('Mozilla/5.0 (Linux; Android 9; V1938T Build/PQ3A.190705.08211809; wv) '
          'AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 '
          'Chrome/91.0.4472.114 Safari/537.36')
    PAGE_SIZE = 24
    DESC_MAX = 200
    MAX_EPISODES = 300
    TOKEN_TTL = 30 * 60

    CATS = [
        ('1', '剧场'),
        ('3', '新剧'),
        ('2', '热播'),
        ('7', '星选'),
        ('5', '阳光'),
    ]

    def __init__(self):
        self._token = ''
        self._token_at = 0.0

    def cats(self):
        return [{'type_id': item[0], 'type_name': item[1]} for item in self.CATS]

    def _login_payload(self):
        device = {
            'device': self.DEVICE,
            'package_name': self.PACKAGE,
            'android_id': self.ANDROID_ID,
            'install_first_open': True,
            'first_install_time': 1752505243345,
            'last_update_time': 1752505243345,
            'report_link_url': '',
            'authorization': '',
            'timestamp': int(time.time() * 1000),
        }
        return json.dumps(device, separators=(',', ':'))

    def _login(self):
        encrypted = aes_ecb_encrypt(self.AES_KEY, self._login_payload())
        body = base64.b64encode(encrypted).decode('ascii')
        status, text = http_post(self.AUTH_BASE + '/user/v3/account/login',
                                 data=body, headers={
                                     'platform': '1',
                                     'user_agent': self.UA,
                                     'content-type': 'application/json; charset=utf-8',
                                 }, timeout=20)
        if status >= 400 or not text:
            print('[七星] 登录 HTTP %s' % status)
            return ''
        try:
            envelope = json.loads(text)
        except Exception:
            print('[七星] 登录响应无法解析')
            return ''
        if not _qixing_ok(envelope):
            print('[七星] 登录失败: %s' % map_str(envelope, 'msg', 'message'))
            return ''
        data = envelope.get('data')
        token = map_str(data, 'token') if isinstance(data, dict) else ''
        if not token:
            print('[七星] 登录没拿到 token')
            return ''
        return token

    def _auth(self):
        now = time.time()
        if self._token and now - self._token_at < self.TOKEN_TTL:
            return self._token
        token = self._login()
        if token:
            self._token, self._token_at = token, now
        return token

    def _get_json(self, target, retry=True):
        token = self._auth()
        if not token:
            return None
        status, text = http_get(target, headers={
            'authorization': token,
            'platform': '1',
            'version_name': self.VERSION_NAME,
            'User-Agent': self.UA,
        }, timeout=20)
        if status in (401, 403):
            if retry:
                self._token = ''
                return self._get_json(target, False)
            print('[七星] 接口鉴权失败 HTTP %d' % status)
            return None
        if status >= 400 or not text:
            print('[七星] 接口 HTTP %d' % status)
            return None
        try:
            envelope = json.loads(text)
        except Exception:
            print('[七星] 接口响应无法解析')
            return None
        if not _qixing_ok(envelope):
            msg = map_str(envelope, 'msg', 'message')
            if retry and ('token' in msg or '登录' in msg or 'auth' in msg.lower()):
                self._token = ''
                return self._get_json(target, False)
            print('[七星] 接口返回错误: %s' % msg)
            return None
        return envelope

    def listing(self, cat, page):
        if page < 1:
            page = 1
        cat_id = _qixing_cat(str(cat or '').strip())
        params = {'theater_class_id': cat_id, 'page_num': str(page),
                  'page_size': str(self.PAGE_SIZE)}
        envelope = self._get_json(self.site + '/v1/theater/home_page?' +
                                  urlencode(params))
        if envelope is None:
            return []
        data = envelope.get('data')
        items = data.get('list') if isinstance(data, dict) else None
        if not isinstance(items, list):
            items = []
        cards = []
        seen = set()
        for raw_item in items:
            if not isinstance(raw_item, dict):
                continue
            if isinstance(raw_item.get('theater'), dict):
                raw_item = raw_item['theater']
            card = _qixing_card(raw_item, _qixing_cat_name(cat_id))
            if not card or card['vod_id'] in seen:
                continue
            seen.add(card['vod_id'])
            cards.append(card)
        if not cards:
            if page > 1:
                return []
            print('[七星] 列表无内容')
        return cards

    def search(self, wd, page):
        wd = str(wd or '').strip()
        if not wd:
            return []
        if page > 1:
            return []
        token = self._auth()
        if not token:
            print('[七星] 搜索无 token')
            return []
        status, text = http_post(self.site + '/v3/search', json_body={'text': wd},
                                 headers={
                                     'authorization': token,
                                     'platform': '1',
                                     'version_name': self.VERSION_NAME,
                                     'User-Agent': self.UA,
                                     'Content-Type': 'application/json',
                                 }, timeout=20)
        if status >= 400 or not text:
            print('[七星] 搜索 HTTP %d' % status)
            return []
        try:
            envelope = json.loads(text)
        except Exception:
            print('[七星] 搜索响应无法解析')
            return []
        if not _qixing_ok(envelope):
            print('[七星] 搜索失败')
            return []
        data = envelope.get('data')
        theater = data.get('theater') if isinstance(data, dict) else None
        items = theater.get('search_data') if isinstance(theater, dict) else None
        if not isinstance(items, list):
            items = []
        cards = []
        seen = set()
        for raw_item in items:
            if not isinstance(raw_item, dict):
                continue
            if isinstance(raw_item.get('theater'), dict):
                raw_item = raw_item['theater']
            card = _qixing_card(raw_item, '')
            if not card or card['vod_id'] in seen:
                continue
            seen.add(card['vod_id'])
            cards.append(card)
        return cards

    def detail(self, vid):
        vid = str(vid or '').strip()
        if not vid:
            print('[七星] 无效剧集 ID')
            return {}
        envelope = self._get_json(self.site + '/v2/theater_parent/detail?' +
                                  urlencode({'theater_parent_id': vid}))
        if envelope is None:
            return {}
        data = envelope.get('data')
        if not isinstance(data, dict):
            data = {}
        name = clean_text(map_str(data, 'title'))
        cover = map_str(data, 'cover_url')
        desc = truncate(clean_text(first_non_empty(map_str(data, 'introduction'),
                                                   map_str(data, 'descrip'))),
                        self.DESC_MAX)
        episodes = []
        seen = set()
        for i, raw_ep in enumerate(as_list(data.get('theaters'))):
            if len(episodes) >= self.MAX_EPISODES:
                break
            if not isinstance(raw_ep, dict):
                continue
            src = map_str(raw_ep, 'son_video_url')
            if not src:
                continue
            no = atoi(map_str(raw_ep, 'num'), 0)
            if no < 1:
                no = i + 1
            if no in seen:
                continue
            seen.add(no)
            ep_name = clean_text(map_str(raw_ep, 'son_title'))
            if not ep_name or ep_name == name:
                ep_name = '第%d集' % no
            episodes.append({'no': no, 'name': ep_name, 'url': src})
        episodes = sort_episodes(episodes)
        if not episodes:
            print('[七星] 详情无剧集: %s' % vid)
            return {}
        return {
            'vod_id': vid,
            'vod_name': name or ('七星短剧 ' + vid),
            'vod_pic': resolve_url(self.site + '/', cover) if cover else '',
            'vod_content': desc,
            'vod_class': '七星',
            'vod_remarks': '共%d集' % len(episodes),
            'episodes': episodes,
        }

    def play(self, url):
        target = str(url or '').strip()
        if not target or not target.startswith('http'):
            print('[七星] 播放参数无效')
            return {}
        return {'url': target, 'header': {'User-Agent': self.UA}, 'parse': 0}

RE_KUWO_PLAY_URL = re.compile(r'(?m)^\s*url=(\S+)\s*$')

def _kuwo_ok(envelope):
    code = envelope.get('code')
    return code is None or code == 0 or code == 200

def _kuwo_card(a):
    vid = str(a.get('url') or '').strip()
    name = clean_text(a.get('title'))
    if not name:
        name = clean_text(a.get('subTitle'))
    if not vid or not name:
        return None
    pic = str(a.get('img') or '').strip()
    remarks = clean_text(a.get('currrentDesc')).strip()
    if not remarks:
        try:
            song_num = int(a.get('songNum') or 0)
        except (TypeError, ValueError):
            song_num = 0
        if song_num > 0:
            remarks = '%d集全' % song_num
    return {
        'vod_id': vid,
        'vod_name': name,
        'vod_pic': resolve_url(KuwoSite.site + '/', pic) if pic else '',
        'vod_remarks': remarks,
        'vod_class': '酷我短剧',
        'vod_score': '',
        'vod_content': '',
    }

def _kuwo_split_play(play_url):
    raw = str(play_url or '').strip()
    if not raw:
        return ''
    if raw.startswith('kuwo://'):
        rest = raw[len('kuwo://'):].strip('/')
        cut = rest.rfind('/')
        if cut <= 0 or cut >= len(rest) - 1:
            return ''
        return rest[cut + 1:].strip()
    if 'vid=' in raw:
        vid = raw[raw.find('vid=') + 4:].strip()
        for ch in '&?#':
            j = vid.find(ch)
            if j >= 0:
                vid = vid[:j]
        return vid.strip()
    if raw.isdigit():
        return raw
    return ''

class KuwoSite(object):

    key = 'kuwo'
    name = '酷我'
    site = 'http://wapi.kuwo.cn/openapi/v1/shortplay'

    PLAY_URL = 'http://nmobi.kuwo.cn/mobi.s'
    UA = 'okhttp/5.1.0'
    PAGE_SIZE = 12
    MAX_PAGES = 60
    SEARCH_PG = 2
    SEARCH_MAX = 30

    CATS = [
        ('10', '猜你想看'),
        ('11', '土味爱情'),
        ('12', '更多精彩'),
        ('13', '霸道总裁的人生'),
        ('14', '赘婿当道'),
        ('15', '漫漫追妻路'),
        ('16', '家庭情感'),
    ]

    def __init__(self):
        pass

    def cats(self):
        return [{'type_id': item[0], 'type_name': item[1]} for item in self.CATS]

    def _get_json(self, target):
        status, text = http_get(target, headers={'User-Agent': self.UA,
                                                 'Accept': 'application/json'},
                                timeout=20)
        if status >= 400 or not text:
            print('[酷我] 接口 HTTP %s' % status)
            return None
        try:
            return json.loads(text)
        except Exception:
            print('[酷我] 接口响应无法解析')
            return None

    def _module_url(self, module_id, page):
        return ('%s/moduleMore?currentPage=%d&moduleId=%s&rn=%d'
                % (self.site, page, quote(str(module_id), safe=''), self.PAGE_SIZE))

    def listing(self, cat, page):
        if page < 1:
            page = 1
        if page > self.MAX_PAGES:
            return []
        cat = str(cat or '').strip() or self.CATS[0][0]
        envelope = self._get_json(self._module_url(cat, page))
        if envelope is None:
            return []
        if not _kuwo_ok(envelope):
            print('[酷我] 接口返回 %s %s' % (envelope.get('code'), envelope.get('msg')))
            return []
        data = envelope.get('data')
        items = data.get('list') if isinstance(data, dict) else None
        if not isinstance(items, list):
            items = []
        cards = []
        seen = set()
        for a in items:
            if not isinstance(a, dict):
                continue
            card = _kuwo_card(a)
            if not card or card['vod_id'] in seen:
                continue
            seen.add(card['vod_id'])
            cards.append(card)
        if not cards:
            if page > 1:
                return []
            print('[酷我] 分类无内容: %s' % cat)
        return cards

    def search(self, wd, page):
        wd = str(wd or '').strip()
        if not wd:
            return []
        if page < 1:
            page = 1
        hits = []
        seen = set()
        wd_lower = wd.lower()
        for cat_id, _ in self.CATS:
            for pg in range(1, self.SEARCH_PG + 1):
                envelope = self._get_json(self._module_url(cat_id, pg))
                if envelope is None:
                    continue
                data = envelope.get('data')
                items = data.get('list') if isinstance(data, dict) else None
                if not isinstance(items, list):
                    continue
                for a in items:
                    if not isinstance(a, dict):
                        continue
                    card = _kuwo_card(a)
                    if not card:
                        continue
                    if wd_lower not in card['vod_name'].lower():
                        continue
                    if card['vod_id'] in seen or len(seen) >= self.SEARCH_MAX:
                        continue
                    seen.add(card['vod_id'])
                    hits.append(card)
        if not hits:
            return []
        if page > 1:
            start = (page - 1) * self.PAGE_SIZE
            if start >= len(hits):
                return []
            return hits[start:min(start + self.PAGE_SIZE, len(hits))]
        if len(hits) > self.SEARCH_MAX:
            hits = hits[:self.SEARCH_MAX]
        return hits

    def detail(self, vid):
        vid = str(vid or '').strip()
        if ':' in vid:
            vid = vid.split(':', 1)[1]
        vid = vid.strip('/')
        if not vid:
            print('[酷我] 无效剧集 ID')
            return {}
        envelope = self._get_json(self.site + '/videoList?albumId=' +
                                  quote(vid, safe=''))
        if envelope is None:
            return {}
        if not _kuwo_ok(envelope):
            print('[酷我] 详情返回 %s %s' % (envelope.get('code'), envelope.get('msg')))
            return {}
        data = envelope.get('data')
        if not isinstance(data, dict):
            data = {}
        shortinfo = data.get('shortinfo')
        if not isinstance(shortinfo, dict):
            shortinfo = {}
        name = clean_text(shortinfo.get('title'))
        cover = str(shortinfo.get('cover') or '').strip()
        items = data.get('list')
        if not isinstance(items, list):
            items = []
        episodes = []
        for i, it in enumerate(items):
            if not isinstance(it, dict):
                continue
            pay = it.get('mvpayinfo')
            vid_num = ''
            if isinstance(pay, dict):
                try:
                    pv = int(pay.get('vid') or 0)
                except (TypeError, ValueError):
                    pv = 0
                if pv > 0:
                    vid_num = str(pv)
            if not vid_num:
                continue
            ep_name = clean_text(it.get('name'))
            if not ep_name:
                ep_name = '第%d集' % (i + 1)
            episodes.append({'no': i + 1, 'name': ep_name,
                             'url': 'kuwo://%s/%s' % (vid, vid_num)})
        if not episodes:
            print('[酷我] 详情无剧集: %s' % vid)
            return {}
        return {
            'vod_id': vid,
            'vod_name': name or ('酷我短剧 ' + vid),
            'vod_pic': resolve_url(self.site + '/', cover) if cover else '',
            'vod_content': '',
            'vod_class': '酷我短剧',
            'vod_remarks': '共%d集' % len(episodes),
            'episodes': episodes,
        }

    def play(self, url):
        vid = _kuwo_split_play(url)
        if not vid:
            print('[酷我] 播放参数无效')
            return {}
        target = '%s?f=web&type=get_url_by_vid&vid=%s' % (self.PLAY_URL,
                                                          quote(vid, safe=''))
        status, text = http_get(target, headers={'User-Agent': self.UA}, timeout=20)
        if status >= 400 or not text:
            print('[酷我] 解析接口 HTTP %d' % status)
            return {}
        media = ''
        m = RE_KUWO_PLAY_URL.search(text)
        if m:
            media = m.group(1).strip()
        if not media:
            i = text.find('url=')
            if i >= 0:
                rest = text[i + 4:].strip()
                j = -1
                for ch in '\r\n \t':
                    k = rest.find(ch)
                    if k >= 0 and (j < 0 or k < j):
                        j = k
                if j >= 0:
                    rest = rest[:j]
                media = rest.strip()
        if not media:
            print('[酷我] 解析未返回播放地址')
            return {}
        if not is_http_media(media):
            print('[酷我] 播放地址无效')
            return {}
        return {'url': media, 'header': {'User-Agent': self.UA}, 'parse': 0}

import uuid
from urllib.parse import quote, quote_plus, unquote, urljoin, urlparse

_NIUNIU_API_BASE = 'https://new.tianjinzhitongdaohe.com'
_NIUNIU_CSJ_BASE = 'https://csj-sp.csjdeveloper.com'
_NIUNIU_HMAC_KEY = 'aceaa47f96b4875d446b2e1d97e03bbb'
_NIUNIU_LOGIN_AES_KEY = 'dafdb3d2a5c343d6'
_NIUNIU_LOGIN_SALT = '786774955F'
_NIUNIU_LOGIN_NONCE = 'VX1KKGtoBDCi1fB1'
_NIUNIU_BIZ_AES_KEY = 'ce49b18dd4e0a4d8'
_NIUNIU_BIZ_SALT = 'FD8188A8D5'
_NIUNIU_BIZ_NONCE = 'X9UknYKtLa3DmtjC'
_NIUNIU_SITE_ID = '5627189'
_NIUNIU_DEVICE_SERIAL = 'aa11fc54-ba9c-3980-add5-447d3fa5b939'
_NIUNIU_UA = 'okhttp/4.12.0'
_NIUNIU_CSJ_UA = 'okhttp/3.12.11'
_NIUNIU_PAGE_SIZE = 40

def _niuniu_hmac_sha256_hex(message, key):
    return hmac.new(key.encode('utf-8'), message.encode('utf-8'),
                    hashlib.sha256).hexdigest()

def _niuniu_new_device_id():
    try:
        return str(uuid.uuid4())
    except Exception:
        return _NIUNIU_DEVICE_SERIAL

def _niuniu_id_num(value):
    text = str(value or '').strip()
    if text.isdigit():
        try:
            return int(text)
        except Exception:
            return text
    return text

def _niuniu_episode_text(value):
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, bool):
        return ''
    if isinstance(value, (int, float)):
        if isinstance(value, float) and value == int(value):
            return str(int(value))
        return str(value)
    return ''

def _niuniu_score_text(value):
    return _niuniu_episode_text(value)

def _niuniu_desc_text(classify, score, intro):
    lines = []
    for label, seg in (('类型：', classify), ('评分：', score), ('简介：', intro)):
        if seg:
            lines.append(label + seg)
    return '\n'.join(lines)

def _niuniu_aes_ecb_encrypt_b64(plain, key):
    return b64_encode(aes_ecb_encrypt(key, plain))

def _niuniu_aes_ecb_decrypt_b64(cipher_b64, key):
    raw = b64_decode(cipher_b64)
    if not raw:
        return b''
    return aes_ecb_decrypt(key, raw)

def _niuniu_csj_body(shortplay_id, index):
    if index < 1:
        index = 1
    idx = str(index)
    body = ('not_include=0'
            '&lock_free=1'
            '&type=1'
            '&clientVersion=v5.2.5'
            '&uuid=6IDYUSASPQY5BBVACWQW3LLTPV4V7DE26UOCX5TZTVUGX4VUJNXQ01'
            '&resolution=1080*2320'
            '&openudid=82f4175d577a2939'
            '&dt=22021211RC'
            '&os_api=31'
            '&install_id=1496879012031075'
            '&sdk_version=1.1.3.0'
            '&siteid=' + _NIUNIU_SITE_ID +
            '&dev_log_aid=667431'
            '&oaid=abec0dfff623201b'
            '&timestamp=' + str(int(time.time())) +
            '&direction=0'
            '&ac=mobile'
            '&os=Android'
            '&vod_version=1.10.21.6-tob'
            '&os_version=12'
            '&count=1'
            '&index=' + idx +
            '&lock_index=' + idx)
    if shortplay_id:
        body += '&shortplay_id=' + shortplay_id
    body += ('&sha1=46121F77CE2FCAD3DBC3B9EC8A24908C1A8AD6D9'
             '&device_brand=Redmi'
             '&package_name=com.niuniu.ztdh.app')
    return body

def _niuniu_csj_unlock_body(shortplay_id, index):
    if index < 1:
        index = 1
    return ('ac=mobile'
            '&os=Android'
            '&vod_version=1.10.21.6-tob'
            '&os_version=12'
            '&lock_ad=1'
            '&lock_free=1'
            '&type=1'
            '&clientVersion=v5.2.5'
            '&uuid=6IDYUSASPQY5BBVACWQW3LLTPV4V7DE26UOCX5TZTVUGX4VUJNXQ01'
            '&resolution=1080*2320'
            '&openudid=82f4175d577a2939'
            '&shortplay_id=' + shortplay_id +
            '&dt=22021211RC'
            '&sha1=46121F77CE2FCAD3DBC3B9EC8A24908C1A8AD6D9'
            '&lock_index=' + str(index) +
            '&os_api=31'
            '&install_id=1496879012031075'
            '&device_brand=Redmi'
            '&sdk_version=1.1.3.0'
            '&package_name=com.niuniu.ztdh.app'
            '&siteid=' + _NIUNIU_SITE_ID +
            '&dev_log_aid=667431'
            '&oaid=abec0dfff623201b'
            '&timestamp=' + str(int(time.time())))

def _niuniu_csj_login_body():
    return ('ac=wifi'
            '&os=Android'
            '&vod_version=1.10.21.6-tob'
            '&os_version=9'
            '&type=1'
            '&clientVersion=v5.2.5'
            '&uuid=Y4WNZ3SAWK7MAJMH7CXCDHJ4VMPVFRZQTBSIA4XTYO4AWEUHIK6Q01'
            '&resolution=1280*2618'
            '&openudid=889edced38f1069b'
            '&dt=Pixel%204'
            '&sha1=46121F77CE2FCAD3DBC3B9EC8A24908C1A8AD6D9'
            '&os_api=28'
            '&install_id=1549688030634536'
            '&device_brand=google'
            '&sdk_version=1.1.3.0'
            '&package_name=com.niuniu.ztdh.app'
            '&siteid=' + _NIUNIU_SITE_ID +
            '&dev_log_aid=667431'
            '&oaid='
            '&timestamp=' + str(int(time.time())))

def _niuniu_code(payload):
    try:
        return int(as_dict(json.loads(payload)).get('code', -1) or -1)
    except Exception:
        return -1

class NiuniuSite(object):

    key = 'niuniu'
    name = '牛牛'
    site = _NIUNIU_API_BASE

    CATS = [
        ('', '全部'),
        ('现言', '现言'),
        ('古言', '古言'),
        ('历史', '历史'),
        ('都市', '都市'),
        ('亲情', '亲情'),
        ('玄幻', '玄幻'),
        ('热血', '热血'),
        ('动作', '动作'),
        ('喜剧', '喜剧'),
        ('悬疑', '悬疑'),
        ('军事', '军事'),
        ('二次元', '二次元'),
        ('其他剧情', '其他剧情'),
    ]

    def __init__(self):
        self._token = ''
        self._device_id = ''
        self._csj_token = ''
        self._csj_token_at = 0.0

    def cats(self):
        return [{'type_id': item[0], 'type_name': item[1]} for item in self.CATS]

    def _visitor_token(self, force=False):
        if not force and self._token:
            return self._token, ''
        if not self._device_id:
            self._device_id = _niuniu_new_device_id()
        status, text = http_get(self.site + '/api/v1/app/user/visitorInfo', headers={
            'deviceid': self._device_id,
            'token': '',
            'client': 'app',
            'devicetype': 'Android',
            'User-Agent': _NIUNIU_UA,
        }, timeout=20)
        if status == 0 or status >= 400 or not text:
            return '', '牛牛游客接口 HTTP %d' % status
        try:
            payload = json.loads(text)
        except Exception as exc:
            return '', '牛牛游客接口响应无法解析: %s' % exc
        token = str(as_dict(payload.get('data')).get('token') or '')
        if not token:
            return '', '牛牛游客接口没拿到 token'
        self._token = token
        return token, ''

    def _nn_do_post(self, path, body, token):
        try:
            data = json.dumps(body, ensure_ascii=False, separators=(',', ':'))
        except Exception as exc:
            return None, str(exc)
        status, text = http_post(self.site + path, data=data, headers={
            'token': token,
            'deviceid': self._device_id,
            'Content-Type': 'application/json;charset=UTF-8',
            'User-Agent': _NIUNIU_UA,
        }, timeout=20)
        if status == 0 or status >= 400 or not text:
            return None, '牛牛接口 HTTP %d' % status
        return text, ''

    def _nn_post(self, path, body):
        for attempt in range(2):
            token, err = self._visitor_token(force=attempt > 0)
            if err:
                return None, err
            payload, err = self._nn_do_post(path, body, token)
            if err:
                return None, err
            if _niuniu_code(payload) == 2006 and attempt == 0:
                continue
            return payload, ''
        return None, '牛牛接口重试后仍失败'

    def _ensure_csj_token(self, force=False):
        now = time.time()
        if not force and self._csj_token and (now - self._csj_token_at) < 30 * 60:
            return self._csj_token, ''
        ts = str(int(time.time()))
        body = _niuniu_csj_login_body()
        enc = _niuniu_aes_ecb_encrypt_b64(body, _NIUNIU_LOGIN_AES_KEY)
        status, text = http_post(
            _NIUNIU_CSJ_BASE + '/csj_sp/api/v1/user/login?siteid=' + _NIUNIU_SITE_ID,
            data=enc, headers={
                'X-Salt': _NIUNIU_LOGIN_SALT,
                'X-Nonce': _NIUNIU_LOGIN_NONCE,
                'X-Timestamp': ts,
                'X-Signature': _niuniu_hmac_sha256_hex(
                    ts + _NIUNIU_LOGIN_NONCE + body, _NIUNIU_HMAC_KEY),
                'Content-Type': 'application/x-www-form-urlencoded',
                'User-Agent': _NIUNIU_UA,
            }, timeout=20)
        if status == 0 or status >= 400 or not text:
            return '', '牛牛CSJ登录 HTTP %d' % status
        plain = _niuniu_aes_ecb_decrypt_b64(text, _NIUNIU_LOGIN_AES_KEY)
        try:
            resp = json.loads(plain.decode('utf-8', 'ignore'))
        except Exception:
            return '', '牛牛CSJ登录响应无法解析'
        token = str(as_dict(as_dict(resp).get('data')).get('access_token') or '')
        if not token:
            return '', '牛牛CSJ登录没拿到 access_token'
        self._csj_token = token
        self._csj_token_at = time.time()
        return token, ''

    def _csj_post_body(self, path, body):
        last_err = ''
        for attempt in range(2):
            token, err = self._ensure_csj_token(force=attempt > 0)
            if err:
                return None, err
            ts = str(int(time.time()))
            enc = _niuniu_aes_ecb_encrypt_b64(body, _NIUNIU_BIZ_AES_KEY)
            status, text = http_post(
                _NIUNIU_CSJ_BASE + path, data=enc, headers={
                    'X-Salt': _NIUNIU_BIZ_SALT,
                    'X-Nonce': _NIUNIU_BIZ_NONCE,
                    'X-Timestamp': ts,
                    'X-Access-Token': token,
                    'X-Signature': _niuniu_hmac_sha256_hex(
                        ts + _NIUNIU_BIZ_NONCE + body, _NIUNIU_HMAC_KEY),
                    'Content-Type': 'application/x-www-form-urlencoded',
                    'User-Agent': _NIUNIU_CSJ_UA,
                }, timeout=20)
            if status == 0 or status >= 400 or not text:
                last_err = '牛牛CSJ接口 HTTP %d' % status
                continue
            plain = _niuniu_aes_ecb_decrypt_b64(text, _NIUNIU_BIZ_AES_KEY)
            try:
                resp = json.loads(plain.decode('utf-8', 'ignore'))
            except Exception:
                return None, '牛牛CSJ响应无法解析'
            ret = resp.get('ret') or 0
            code = resp.get('code') or 0
            if (ret not in (0, 200)) or (code not in (0, 200)):
                if attempt == 0:
                    last_err = '牛牛CSJ返回 ret=%s code=%s %s' % (
                        ret, code, resp.get('msg'))
                    continue
                return None, last_err
            return as_dict(resp), ''
        if not last_err:
            last_err = '牛牛CSJ请求失败'
        return None, last_err

    def _csj_post(self, path, shortplay_id, index):
        return self._csj_post_body(path, _niuniu_csj_body(shortplay_id, index))

    def _csj_unlock(self, shortplay_id, index):
        return self._csj_post_body(
            '/csj_sp/api/v1/pay/ad_unlock?siteid=' + _NIUNIU_SITE_ID,
            _niuniu_csj_unlock_body(shortplay_id, index))

    def _cards(self, records, cat_class):
        cards = []
        seen = set()
        for record in records:
            record = as_dict(record)
            vid = str(record.get('id') or '').strip()
            if not vid or vid == '0' or vid in seen:
                continue
            name = clean_text(record.get('name'))
            if not name:
                continue
            seen.add(vid)
            remarks = str(record.get('type') or '').strip()
            total = to_int(record.get('totalEpisode'), 0)
            if total > 0:
                ep = '共%d集' % total
                remarks = (remarks + ' · ' + ep) if remarks else ep
            cards.append({
                'vod_id': vid,
                'vod_name': name,
                'vod_pic': str(record.get('cover') or '').strip(),
                'vod_remarks': remarks,
                'vod_class': cat_class,
                'vod_score': '',
                'vod_content': '',
            })
        return cards

    def listing(self, cat, page):
        page = page if page and page > 0 else 1
        cat = str(cat or '').strip()
        if cat == '全部':
            cat = ''
        payload, err = self._nn_post('/api/v1/app/screen/screenMovie', {
            'condition': {'classify': cat, 'typeId': 'S1'},
            'pageNum': page,
            'pageSize': _NIUNIU_PAGE_SIZE,
        })
        if err:
            print('[牛牛] 列表失败: %s' % err)
            return []
        try:
            resp = json.loads(payload)
        except Exception:
            print('[牛牛] 列表响应无法解析')
            return []
        resp = as_dict(resp)
        code = int(resp.get('code', -1) or -1)
        if code not in (0, 200):
            print('[牛牛] 列表返回 %s %s' % (code, resp.get('msg')))
            return []
        cards = self._cards(as_list(as_dict(resp.get('data')).get('records')),
                            '牛牛短剧')
        if not cards:
            print('[牛牛] 列表无内容')
        return cards

    def search(self, wd, page):
        wd = str(wd or '').strip()
        if not wd:
            print('[牛牛] 搜索关键词为空')
            return []
        page = page if page and page > 0 else 1
        payload, err = self._nn_post('/api/v1/app/search/searchMovie', {
            'condition': {'typeId': 'S1', 'value': wd},
            'pageNum': page,
            'pageSize': _NIUNIU_PAGE_SIZE,
        })
        if err:
            print('[牛牛] 搜索失败: %s' % err)
            return []
        try:
            resp = json.loads(payload)
        except Exception:
            print('[牛牛] 搜索响应无法解析')
            return []
        resp = as_dict(resp)
        code = int(resp.get('code', -1) or -1)
        if code not in (0, 200):
            print('[牛牛] 搜索返回 %s %s' % (code, resp.get('msg')))
            return []
        cards = self._cards(as_list(as_dict(resp.get('data')).get('records')),
                            '牛牛短剧')
        if not cards:
            print('[牛牛] 搜索无结果: %s' % wd)
        return cards

    def detail(self, vid):
        vid = str(vid or '').strip()
        if ':' in vid:
            vid = vid.split(':', 1)[1]
        if not vid:
            print('[牛牛] 无效的剧集 ID')
            return {}
        desc = {}
        desc_payload, desc_err = self._nn_post('/api/v1/app/play/movieDesc', {
            'id': _niuniu_id_num(vid), 'typeId': 'S1'})
        if not desc_err:
            try:
                desc = as_dict(json.loads(desc_payload).get('data'))
            except Exception:
                desc = {}
        det_payload, det_err = self._nn_post('/api/v1/app/play/movieDetails', {
            'id': _niuniu_id_num(vid), 'source': 0, 'typeId': 'S1',
            'userId': '546932'})
        if det_err:
            print('[牛牛] 详情失败: %s' % det_err)
            return {}
        try:
            det = json.loads(det_payload)
        except Exception:
            print('[牛牛] 详情响应无法解析')
            return {}
        det = as_dict(det)
        code = int(det.get('code', -1) or -1)
        if code not in (0, 200):
            print('[牛牛] 详情返回 %s %s' % (code, det.get('msg')))
            return {}
        data = as_dict(det.get('data'))
        name = clean_text(first_non_empty(desc.get('name'), data.get('name')))
        if not name:
            name = '牛牛短剧 ' + vid
        pic = str(first_non_empty(desc.get('cover'), data.get('cover')) or '').strip()
        content = _niuniu_desc_text(str(desc.get('classify') or ''),
                                    _niuniu_score_text(desc.get('score')),
                                    clean_text(first_non_empty(
                                        desc.get('introduce'), data.get('introduce'))))
        episodes = []
        eps = as_list(data.get('episodeList'))
        third = str(data.get('thirdPlayId') or '').strip()
        if eps:
            seq_no = 0
            for idx, item in enumerate(eps):
                item = as_dict(item)
                ep_id = str(item.get('id') or '').strip()
                if not ep_id:
                    continue
                seq_no += 1
                ep_name = _niuniu_episode_text(item.get('episode'))
                if not ep_name:
                    ep_name = '第%d集' % (idx + 1)
                elif '集' not in ep_name:
                    ep_name = '第' + ep_name + '集'
                episodes.append({'no': seq_no, 'name': ep_name,
                                 'url': 'niuniu://%s/f%s' % (vid, ep_id)})
        elif third and third != '0':
            cs, cs_err = self._csj_post(
                '/csj_sp/api/v1/shortplay/detail?siteid=' + _NIUNIU_SITE_ID,
                third, 1)
            if not cs_err:
                for item in as_list(as_dict(cs.get('data')).get('episode_right_list')):
                    item = as_dict(item)
                    index = to_int(item.get('index'), 0)
                    if index < 1:
                        continue
                    episodes.append({'no': index, 'name': '第%d集' % index,
                                     'url': 'niuniu://%s/t%d' % (third, index)})
                for item in as_list(as_dict(cs.get('data')).get('list')):
                    item = as_dict(item)
                    if not pic:
                        cover = str(item.get('cover_image') or '').strip()
                        if is_http_media(cover):
                            pic = cover
                    if not content:
                        content = clean_text(item.get('desc'))
                    break
        if not episodes:
            print('[牛牛] 详情无剧集: %s' % vid)
            return {}
        return {
            'vod_id': vid,
            'vod_name': name,
            'vod_pic': pic,
            'vod_content': content,
            'vod_class': '牛牛短剧',
            'vod_remarks': '共%d集' % len(episodes),
            'episodes': episodes,
        }

    def play(self, url):
        raw = str(url or '').strip().strip('/')
        prefix = 'niuniu://'
        if not raw.startswith(prefix):
            print('[牛牛] 播放参数无效')
            return {}
        rest = raw[len(prefix):].strip('/')
        cut = rest.rfind('/')
        if cut <= 0 or cut >= len(rest) - 1:
            print('[牛牛] 播放参数无效')
            return {}
        vid, extra = rest[:cut], rest[cut + 1:]
        if not vid or not extra:
            print('[牛牛] 播放参数无效')
            return {}
        mode = extra[0].lower()
        seq_text = extra[1:]
        if mode == 'f':
            if not seq_text:
                print('[牛牛] 播放参数无效')
                return {}
            payload, err = self._nn_post('/api/v1/app/play/movieDetails', {
                'id': _niuniu_id_num(seq_text), 'source': 0, 'typeId': 'S1',
                'userId': '546932', 'episodeId': _niuniu_id_num(vid)})
            if err:
                print('[牛牛] 播放失败: %s' % err)
                return {}
            try:
                resp = json.loads(payload)
            except Exception:
                print('[牛牛] 播放响应无法解析')
                return {}
            src = str(as_dict(as_dict(resp).get('data')).get('url') or '').strip()
            if not is_http_media(src):
                print('[牛牛] 播放未返回有效地址')
                return {}
            return {'url': src, 'header': {'User-Agent': _NIUNIU_UA}, 'parse': 0}
        if mode == 't':
            seq = atoi(seq_text, 0)
            if seq < 1:
                print('[牛牛] 播放参数无效')
                return {}
            _, _ = self._csj_unlock(vid, seq)
            cs, err = self._csj_post(
                '/csj_sp/api/v1/shortplay/detail?siteid=' + _NIUNIU_SITE_ID,
                vid, seq)
            if err:
                print('[牛牛] 播放失败: %s' % err)
                return {}
            media = ''
            for item in as_list(as_dict(cs.get('data')).get('list')):
                item = as_dict(item)
                raw_main = str(as_dict(as_dict(
                    as_dict(item.get('video_model')).get('video_list')
                ).get('video_1')).get('main_url') or '').strip()
                if not raw_main:
                    continue
                dec = b64_decode(raw_main)
                if not dec:
                    continue
                candidate = dec.decode('utf-8', 'ignore').strip()
                if is_http_media(candidate):
                    media = candidate
                    break
            if not media:
                print('[牛牛] 第三方播放未解析出地址')
                return {}
            return {'url': media, 'header': {'User-Agent': _NIUNIU_UA}, 'parse': 0}
        print('[牛牛] 播放参数无效')
        return {}

_XIFU_HOST = 'https://minidrama-api.contentchina.com'
_XIFU_ORIGIN = 'https://minidrama.contentchina.com'
_XIFU_REFERER = _XIFU_ORIGIN + '/'
_XIFU_UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
            '(KHTML, like Gecko) Chrome/120.0 Safari/537.36')
_XIFU_CAT_IDS = ('3', '6', '5', '1', '4', '23', '16')

def _xifu_percent_encode(text):
    return quote(str(text), safe='~-_.')

def _xifu_album_id(item):
    raw = item.get('albumId')
    if isinstance(raw, str):
        return raw.strip()
    if isinstance(raw, bool):
        return ''
    if isinstance(raw, (int, float)):
        if isinstance(raw, float) and raw == int(raw):
            return str(int(raw))
        return str(raw)
    return ''

def _xifu_total(item):
    raw = item.get('total')
    if isinstance(raw, bool):
        return 0
    if isinstance(raw, (int, float)):
        return int(raw)
    if isinstance(raw, str):
        return atoi(raw, 0)
    return 0

def _xifu_abs_cover(item):
    cover = str(item.get('coverUrl') or '').strip()
    if not cover:
        return ''
    return resolve_url(_XIFU_HOST, cover)

def _xifu_vod_url(vid, play_auth):
    raw = b64_decode(play_auth)
    if not raw:
        raw = b64_decode(play_auth, padding=False)
    try:
        cred = json.loads(raw.decode('utf-8', 'ignore'))
    except Exception:
        return '', '喜福凭证解析失败'
    cred = as_dict(cred)
    access_key_id = str(cred.get('AccessKeyId') or '')
    if not access_key_id:
        return '', '喜福凭证解析失败'
    region = str(cred.get('Region') or '') or 'cn-shanghai'
    params = {
        'Action': 'GetPlayInfo',
        'Version': '2017-03-21',
        'Format': 'JSON',
        'AccessKeyId': access_key_id,
        'SecurityToken': str(cred.get('SecurityToken') or ''),
        'VideoId': vid,
        'AuthInfo': str(cred.get('AuthInfo') or ''),
        'Timestamp': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
        'SignatureMethod': 'HMAC-SHA1',
        'SignatureVersion': '1.0',
        'SignatureNonce': str(time.time_ns()),
    }
    if cred.get('PlayConfig') is not None:
        params['PlayConfig'] = json.dumps(cred.get('PlayConfig'),
                                          ensure_ascii=False,
                                          separators=(',', ':'))
    canonical = '&'.join(
        _xifu_percent_encode(key) + '=' + _xifu_percent_encode(params[key])
        for key in sorted(params))
    string_to_sign = 'GET&%2F&' + _xifu_percent_encode(canonical)
    secret = (str(cred.get('AccessKeySecret') or '') + '&').encode('utf-8')
    digest = hmac.new(secret, string_to_sign.encode('utf-8'),
                      hashlib.sha1).digest()
    sig = b64_encode(digest)
    target = ('https://vod.%s.aliyuncs.com/?%s&Signature=%s'
              % (region, canonical, _xifu_percent_encode(sig)))
    return target, ''

class XifuSite(object):

    key = 'xifu'
    name = '喜福'
    site = _XIFU_HOST

    CATS = [
        ('3', '爽剧'),
        ('6', '甜宠'),
        ('5', '逆袭'),
        ('1', '现代言情'),
        ('4', '都市'),
        ('23', '玄幻'),
        ('16', '古代言情'),
    ]

    def __init__(self):
        pass

    def cats(self):
        return [{'type_id': item[0], 'type_name': item[1]} for item in self.CATS]

    def _get(self, raw_url, retries=0):
        status, text = http_get(raw_url, headers={
            'User-Agent': _XIFU_UA,
            'Origin': _XIFU_ORIGIN,
            'Referer': _XIFU_REFERER,
            'Accept': 'application/json, text/plain, */*',
        }, timeout=20, retries=retries)
        if status == 0 or status >= 400 or not text:
            return None, '喜福接口 HTTP %d' % status
        try:
            envelope = json.loads(text)
        except Exception:
            return None, '喜福接口响应无法解析'
        envelope = as_dict(envelope)
        data = envelope.get('data')
        if data is None or data == 'null':
            return None, '喜福接口无数据'
        return data, ''

    def _drama_list(self, cat, page):
        page = page if page and page > 0 else 1
        if cat and cat not in _XIFU_CAT_IDS:
            cat = ''

        def fetch(cid):
            u = '%s/web/v1/drama/list?pageSize=24&currentPage=%d' % (self.site, page)
            if cid:
                u += '&filterCategories[]=' + quote_plus(cid)
            data, err = self._get(u)
            if err:
                return [], err
            return as_list(as_dict(data).get('data')), ''

        items, err = fetch(cat)
        if (err or not items) and cat:
            fallback, ferr = fetch('')
            if not ferr and fallback:
                return fallback, ''
        return items, err

    def _card(self, item, cat):
        vid = _xifu_album_id(item)
        if not vid:
            return None
        name = clean_text(map_str(item, 'title'))
        if not name:
            return None
        total = _xifu_total(item)
        remarks = '共%d集' % total if total > 0 else ''
        return {
            'vod_id': vid,
            'vod_name': name,
            'vod_pic': _xifu_abs_cover(item),
            'vod_remarks': remarks,
            'vod_class': cat,
            'vod_score': '',
            'vod_content': '',
        }

    def listing(self, cat, page):
        cat = str(cat or '').strip()
        items, err = self._drama_list(cat, page)
        if err:
            print('[喜福] 列表失败: %s' % err)
            return []
        cards = []
        seen = set()
        for item in items:
            item = as_dict(item)
            card = self._card(item, cat)
            if not card or card['vod_id'] in seen:
                continue
            seen.add(card['vod_id'])
            cards.append(card)
        if not cards:
            print('[喜福] 列表无内容')
            return []
        return cards

    def search(self, wd, page):
        print('[喜福] 暂不支持搜索')
        return []

    def detail(self, vid):
        vid = str(vid or '').strip()
        if not vid:
            print('[喜福] 无效的剧集 ID')
            return {}
        for cid in [''] + [item[0] for item in self.CATS]:
            items, err = self._drama_list(cid, 1)
            if err:
                continue
            for item in items:
                item = as_dict(item)
                if _xifu_album_id(item) != vid:
                    continue
                total = _xifu_total(item)
                if total < 1:
                    total = 1
                name = clean_text(map_str(item, 'title'))
                if not name:
                    name = '喜福短剧 ' + vid
                episodes = [{'no': i, 'name': '第%d集' % i,
                             'url': 'xifu://%s/%d' % (vid, i)}
                            for i in range(1, total + 1)]
                return {
                    'vod_id': vid,
                    'vod_name': name,
                    'vod_pic': _xifu_abs_cover(item),
                    'vod_content': '',
                    'vod_class': '喜福',
                    'vod_remarks': '共%d集' % len(episodes),
                    'episodes': episodes,
                }
        return {
            'vod_id': vid,
            'vod_name': '喜福短剧 ' + vid,
            'vod_pic': '',
            'vod_content': '',
            'vod_class': '喜福',
            'vod_remarks': '共1集',
            'episodes': [{'no': 1, 'name': '第1集', 'url': 'xifu://%s/1' % vid}],
        }

    def play(self, url):
        raw = str(url or '').strip()
        prefix = 'xifu://'
        if not raw.startswith(prefix):
            print('[喜福] 播放参数无效')
            return {}
        rest = raw[len(prefix):].strip('/')
        cut = rest.rfind('/')
        if cut <= 0 or cut >= len(rest) - 1:
            print('[喜福] 播放参数无效')
            return {}
        vid, seq_text = rest[:cut], rest[cut + 1:]
        seq = atoi(seq_text, 0)
        if seq < 1 or not vid:
            print('[喜福] 播放参数无效')
            return {}
        data, err = self._get('%s/web/v1/drama/play_auth?albumId=%s&seq=%d'
                              % (self.site, quote(vid, safe=''), seq), retries=2)
        if err:
            print('[喜福] 播放凭证获取失败: %s' % err)
            return {}
        auth = as_dict(data)
        auth_vid = str(auth.get('vid') or '')
        play_auth = str(auth.get('playAuth') or '')
        if not auth_vid or not play_auth:
            print('[喜福] 播放凭证获取失败')
            return {}
        target, verr = _xifu_vod_url(auth_vid, play_auth)
        if verr:
            print('[喜福] %s' % verr)
            return {}
        status, text = http_get(target, headers={
            'User-Agent': _XIFU_UA,
            'Accept': '*/*',
            'Origin': _XIFU_ORIGIN,
            'Referer': _XIFU_REFERER,
        }, timeout=20, retries=2)
        if status == 0 or status >= 400 or not text:
            print('[喜福] 播放接口 HTTP %d' % status)
            return {}
        try:
            res = json.loads(text)
        except Exception:
            print('[喜福] 播放响应解析失败')
            return {}
        play_infos = res.get('PlayInfoList')
        infos = []
        if isinstance(play_infos, dict):
            infos = as_list(play_infos.get('PlayInfo'))
        elif isinstance(play_infos, list):
            infos = play_infos
        if not infos:
            infos = as_list(res.get('PlayInfo'))
        src = ''
        for info in infos:
            info = as_dict(info)
            cand = str(info.get('PlayURL') or '').strip()
            if not is_http_media(cand):
                continue
            if not src:
                src = cand
            if str(info.get('Status') or '') == 'Normal':
                src = cand
                break
        if not src:
            print('[喜福] 播放地址为空')
            return {}
        return {'url': src,
                'header': {'User-Agent': _XIFU_UA, 'Referer': _XIFU_REFERER},
                'parse': 0}

_SHUANG_HOST = 'https://djw123.com'
_SHUANG_REFERER = _SHUANG_HOST + '/'
_SHUANG_UA = ('Mozilla/5.0 (Linux; Android 14; Pixel 8 Pro) AppleWebKit/537.36 '
              '(KHTML, like Gecko) Chrome/150.0.0.0 Mobile Safari/537.36')
_SHUANG_HEADERS = {
    'User-Agent': _SHUANG_UA,
    'Accept': ('text/html,application/xhtml+xml,application/xml;q=0.9,'
               'image/avif,image/webp,image/apng,*/*;q=0.8'),
    'Accept-Language': 'zh-CN,zh;q=0.9',
    'Referer': _SHUANG_REFERER,
    'sec-ch-ua': '"Chromium";v="150", "Google Chrome";v="150", "Not_A Brand";v="24"',
    'sec-ch-ua-mobile': '?1',
    'sec-ch-ua-platform': '"Android"',
    'upgrade-insecure-requests': '1',
    'sec-fetch-site': 'same-origin',
    'sec-fetch-mode': 'navigate',
    'sec-fetch-user': '?1',
    'sec-fetch-dest': 'document',
}
_SHUANG_DEFAULT = '女频恋爱'
_SHUANG_KNOWN = ('女频恋爱', '脑洞悬疑', '年代穿越', '古装仙侠', '现代都市',
                 '反转', '爽文', '短剧')

def _shuang_fetch(raw_url):
    if _urlrequest is None:
        return http_get(raw_url, headers=dict(_SHUANG_HEADERS),
                        timeout=20, retries=2)
    try:
        req = _urlrequest.Request(raw_url, headers=dict(_SHUANG_HEADERS))
        handlers = [_urlrequest.HTTPSHandler(context=_ssl_context()),
                    _urlrequest.ProxyHandler({})]
        opener = _urlrequest.build_opener(*handlers)
        resp = opener.open(req, timeout=20)
        raw = resp.read()
        status = getattr(resp, 'status', None) or resp.getcode()
        enc = (resp.headers.get('Content-Encoding') or '').lower()
        if 'gzip' in enc and _gzip is not None:
            raw = _gzip.decompress(raw)
        return status, raw.decode('utf-8', 'ignore')
    except Exception as exc:
        return int(getattr(exc, 'code', 0) or 0), ''

_SHUANG_RE_CARD = re.compile(r'(?i)<div class="a-con-inner">')
_SHUANG_RE_A_TITLE = re.compile(r'(?is)<a[^>]*title="([^"]+)"[^>]*>')
_SHUANG_RE_A_HREF = re.compile(r'(?is)<a[^>]*href="([^"]+)"[^>]*>')
_SHUANG_RE_IMG = re.compile(r'(?is)<img[^>]*data-original="([^"]+)"')
_SHUANG_RE_REMARK = re.compile(r'(?is)<span[^>]*>([^<]+)</span>')
_SHUANG_RE_MOVBOX = re.compile(
    r'(?is)<div[^>]*class=["\']movbox["\'][^>]*>.*?<a[^>]*href=["\']([^"\']+)["\']')
_SHUANG_RE_PLAY = re.compile(
    r'(?i)<a[^>]*href=["\']([^"\']*/play/[^"\']+)["\']')
_SHUANG_RE_H1 = re.compile(r'(?is)<h1[^>]*>([^<]+)</h1>')
_SHUANG_RE_STATE = re.compile(
    r'(?is)<p[^>]*class=["\'][^"\']*zhuangtai[^"\']*["\'][^>]*>(.*?)</p>')
_SHUANG_RE_SEARCH_CON = re.compile(r'(?is)<div[^>]*class="search-con"[^>]*>.*?</div>')
_SHUANG_RE_LI = re.compile(r'(?is)<li[^>]*>.*?</li>')
_SHUANG_RE_STATE2 = re.compile(r'(?is)<span[^>]*class="state"[^>]*>([^<]+)</span>')
_SHUANG_RE_VAR = re.compile(r'(?:var\s+)?player_[a-zA-Z0-9]+=(.*?)<')
_SHUANG_RE_JSON = re.compile(r'(\{"flag":"play".*?\})')
_SHUANG_RE_VIDEO = re.compile(r'(?i)<video[^>]*src=["\']([^"\']+)["\']')
_SHUANG_RE_SOURCE = re.compile(r'(?i)<source[^>]*src=["\']([^"\']+)["\']')

def _shuang_abs(raw):
    raw = html_unescape(str(raw or '').strip())
    if not raw:
        return ''
    if raw.startswith('http://') or raw.startswith('https://'):
        return raw
    if raw.startswith('//'):
        return 'https:' + raw
    if not raw.startswith('/'):
        raw = '/' + raw
    return _SHUANG_HOST + raw

def _shuang_card_blocks(page):
    matches = list(_SHUANG_RE_CARD.finditer(page))
    blocks = []
    for index, found in enumerate(matches):
        end = len(page)
        if index + 1 < len(matches):
            end = matches[index + 1].start()
        blocks.append(page[found.end():end])
    return blocks

def _shuang_parse_items(page):
    blocks = _shuang_card_blocks(page)
    out = []
    seen = set()
    for block in blocks:
        tm = _SHUANG_RE_A_TITLE.search(block)
        um = _SHUANG_RE_A_HREF.search(block)
        if not tm or not um:
            continue
        href = html_unescape(um.group(1).strip())
        if not href or href.startswith('javascript'):
            continue
        if '/vod/' not in href:
            continue
        if href in seen:
            continue
        seen.add(href)
        item = {'id': href.lstrip('/'),
                'name': clean_text(html_unescape(tm.group(1))),
                'pic': '', 'rem': ''}
        pm = _SHUANG_RE_IMG.search(block)
        if pm:
            item['pic'] = _shuang_abs(pm.group(1))
        rm = _SHUANG_RE_REMARK.search(block)
        if rm:
            item['rem'] = clean_text(html_unescape(rm.group(1)))
        out.append(item)
    return out

def _shuang_parse_search(page):
    block = _SHUANG_RE_SEARCH_CON.search(page)
    if not block:
        return []
    out = []
    for li in _SHUANG_RE_LI.findall(block.group(0)):
        tm = _SHUANG_RE_A_TITLE.search(li)
        um = _SHUANG_RE_A_HREF.search(li)
        if not tm or not um:
            continue
        href = html_unescape(um.group(1).strip())
        if not href or href.startswith('javascript'):
            continue
        item = {'id': href.lstrip('/'),
                'name': clean_text(html_unescape(tm.group(1))),
                'pic': '', 'rem': ''}
        pm = _SHUANG_RE_IMG.search(li)
        if pm:
            item['pic'] = _shuang_abs(pm.group(1))
        rm = _SHUANG_RE_STATE2.search(li)
        if rm:
            item['rem'] = clean_text(html_unescape(rm.group(1)))
        out.append(item)
    return out

def _shuang_extract_src(page):
    raw = ''
    m = _SHUANG_RE_VAR.search(page)
    if m:
        raw = m.group(1).strip()
    else:
        m = _SHUANG_RE_JSON.search(page)
        if m:
            raw = m.group(1).strip()
    if raw:
        try:
            obj = json.loads(raw)
        except Exception:
            obj = None
        if isinstance(obj, dict):
            vu = str(obj.get('url') or '')
            enc = obj.get('encrypt')
            if isinstance(enc, float) and enc == int(enc):
                enc = int(enc)
            enc = str(enc or '')
            if enc == '1':
                vu = unquote(vu).replace('+', ' ')
            elif enc == '2':
                dec = b64_decode(vu)
                if dec:
                    vu = dec.decode('utf-8', 'ignore')
                    if '%' in vu:
                        vu = unquote(vu).replace('+', ' ')
            if is_http_media(vu):
                return vu
    m = _SHUANG_RE_VIDEO.search(page)
    if m and is_http_media(m.group(1)):
        return m.group(1)
    m = _SHUANG_RE_SOURCE.search(page)
    if m and is_http_media(m.group(1)):
        return m.group(1)
    return ''

class ShuangSite(object):

    key = 'shuang'
    name = '爽爽'
    site = _SHUANG_HOST

    CATS = [
        ('', '全部'),
        ('女频恋爱', '女频恋爱'),
        ('脑洞悬疑', '脑洞悬疑'),
        ('年代穿越', '年代穿越'),
        ('古装仙侠', '古装仙侠'),
        ('现代都市', '现代都市'),
        ('反转', '反转'),
        ('爽文', '爽文'),
        ('短剧', '短剧'),
    ]

    def __init__(self):
        self._play_pages = {}

    def cats(self):
        return [{'type_id': item[0], 'type_name': item[1]} for item in self.CATS]

    def _get(self, raw_url):
        status, text = _shuang_fetch(raw_url)
        if status == 0 or status >= 400 or not text:
            return '', '爽爽接口 HTTP %d' % status
        return text, ''

    def _card(self, item, cat):
        return {
            'vod_id': item['id'],
            'vod_name': item['name'],
            'vod_pic': item['pic'],
            'vod_remarks': item['rem'],
            'vod_class': cat,
            'vod_score': '',
            'vod_content': '',
        }

    def listing(self, cat, page):
        page = page if page and page > 0 else 1
        area = str(cat or '').strip()
        if area not in _SHUANG_KNOWN:
            area = _SHUANG_DEFAULT
        u = '%s/show/duanju---%s-----%d---.html' % (_SHUANG_HOST, quote_plus(area), page)
        body, err = self._get(u)
        if err:
            print('[爽爽] 列表失败 %s: %s' % (u, err))
            return []
        cat_name = str(cat or '全部').strip()
        cards = [self._card(item, cat_name) for item in _shuang_parse_items(body)]
        if not cards:
            print('[爽爽] 列表无内容: %s' % u)
            return []
        return cards

    def search(self, wd, page):
        wd = str(wd or '').strip()
        if not wd:
            print('[爽爽] 搜索关键词为空')
            return []
        u = '%s/search/-------------.html?wd=%s' % (_SHUANG_HOST, quote_plus(wd))
        body, err = self._get(u)
        if err:
            print('[爽爽] 搜索失败: %s' % err)
            return []
        items = _shuang_parse_items(body)
        if not items:
            items = _shuang_parse_search(body)
        cards = [self._card(item, '搜索') for item in items]
        if not cards:
            print('[爽爽] 搜索无结果: %s' % wd)
            return []
        return cards

    def detail(self, vid):
        raw_id = str(vid or '').strip()
        if not raw_id:
            print('[爽爽] 无效的剧集 ID')
            return {}
        try:
            vid = unquote(raw_id)
        except Exception:
            vid = raw_id
        vid = vid.strip()
        detail_url = vid if vid.startswith('http') else _shuang_abs(vid)
        body, err = self._get(detail_url)
        if err:
            print('[爽爽] 详情抓取失败: %s' % err)
            return {}
        play_url = ''
        m = _SHUANG_RE_MOVBOX.search(body)
        if m:
            play_url = _shuang_abs(m.group(1))
        else:
            m = _SHUANG_RE_PLAY.search(body)
            if m:
                play_url = _shuang_abs(m.group(1))
        if not play_url:
            print('[爽爽] 详情未找到播放页: %s' % vid)
            return {}
        name = ''
        m = _SHUANG_RE_H1.search(body)
        if m:
            name = clean_text(html_unescape(m.group(1)))
        pic = ''
        m = _SHUANG_RE_IMG.search(body)
        if m:
            pic = _shuang_abs(m.group(1))
        desc = ''
        m = _SHUANG_RE_STATE.search(body)
        if m:
            desc = truncate(clean_text(html_unescape(strip_tags(m.group(1)))), 200)
        self._play_pages[vid] = play_url
        return {
            'vod_id': vid,
            'vod_name': name or '爽爽短剧',
            'vod_pic': pic,
            'vod_content': desc,
            'vod_class': '爽爽',
            'vod_remarks': '共1集',
            'episodes': [{'no': 1, 'name': '全集', 'url': 'shuang://%s/1' % vid}],
        }

    def play(self, url):
        raw = str(url or '').strip()
        prefix = 'shuang://'
        play_url = ''
        if raw.startswith(prefix):
            rest = raw[len(prefix):].strip('/')
            cut = rest.rfind('/')
            if cut > 0 and cut < len(rest) - 1:
                vid = rest[:cut]
                seq = atoi(rest[cut + 1:], 1)
            else:
                vid = rest
                seq = 1
            if not vid:
                print('[爽爽] 播放参数无效')
                return {}
            if seq < 1:
                seq = 1
            play_url = self._play_pages.get(vid, '')
            if not play_url:
                body, err = self._get(_shuang_abs(vid))
                if err:
                    print('[爽爽] 播放页抓取失败: %s' % err)
                    return {}
                m = _SHUANG_RE_MOVBOX.search(body)
                if m:
                    play_url = _shuang_abs(m.group(1))
                else:
                    m = _SHUANG_RE_PLAY.search(body)
                    if m:
                        play_url = _shuang_abs(m.group(1))
                if play_url:
                    self._play_pages[vid] = play_url
            if not play_url:
                print('[爽爽] 详情未找到播放页: %s' % vid)
                return {}
        elif raw.startswith('http'):
            play_url = raw
        else:
            print('[爽爽] 播放参数无效')
            return {}
        body, err = self._get(play_url)
        if err:
            print('[爽爽] 播放页抓取失败: %s' % err)
            return {}
        src = _shuang_extract_src(body)
        if not src:
            print('[爽爽] 播放地址解析失败')
            return {}
        return {'url': src,
                'header': {'User-Agent': _SHUANG_UA, 'Referer': _SHUANG_REFERER},
                'parse': 0}

_WUWU_HOST = 'https://www.duanju55.com'
_WUWU_REFERER = _WUWU_HOST + '/'
_WUWU_UA = 'okhttp/4.10.0'
_WUWU_KNOWN = ('全部', '男频', '女频', '都市', '虐渣', '励志', '逆袭',
               '古风', '复仇', '家庭', '悬疑', '奇幻')
_WUWU_LIST_PATTERNS = (
    ('SecondList_bookName', 'SecondList_bookImage', 'SecondList_totalChapterNum'),
    ('TagBookList_bookName', 'TagBookList_bookImageBox', 'TagBookList_totalChapterNum'),
    ('BrowseList_bookName', 'BrowseList_imageBox', 'BrowseList_totalChapterNum'),
)

_WUWU_RE_TITLE = re.compile(r'(?is)<title>(.*?)</title>')
_WUWU_RE_COVER = re.compile(
    r'(?is)<img[^>]*class="[^"]*\bDramaDetail_bookCover\b[^"]*"[^>]*\bsrc="([^"]+)"')
_WUWU_RE_OG_IMAGE = re.compile(r'(?i)property="og:image"[^>]+content="([^"]+)"')
_WUWU_RE_DESC = re.compile(
    r'(?is)class="[^"]*(?:detail-content|vod-content|content|descr|intro)[^"]*"[^>]*>(.*?)</div>')
_WUWU_RE_BOX = re.compile(r'(?is)<div class="pcDrama_contentBox.*?</ul>')
_WUWU_RE_EP = re.compile(
    r'(?is)<a[^>]*class="[^"]*\bpcDrama_catalogItem\b[^"]*"[^>]*href="([^"]*vod/play/id/\d+/sid/\d+/nid/(\d+)\.html)"[^>]*>(.*?)</a>')
_WUWU_RE_PLAYER = re.compile(r'(?is)var\s+player_\w+\s*=\s*\{.*?"url"\s*:\s*"([^"]*)"')
_WUWU_RE_ANY_URL = re.compile(r'(?i)["\']url["\']\s*:\s*["\']([^"\']+)["\']')
_WUWU_RE_MEDIA = re.compile(r'(?i)(?:https?:)?//[^\s"\'<>]+\.(?:m3u8|mp4)')
_WUWU_RE_B64 = re.compile(r'^[A-Za-z0-9+/=]{16,}$')

def _wuwu_abs(raw):
    raw = html_unescape(str(raw or '').strip())
    if not raw:
        return ''
    if raw.startswith('http://') or raw.startswith('https://'):
        return raw
    if raw.startswith('//'):
        return 'https:' + raw
    if not raw.startswith('/'):
        raw = '/' + raw
    return _WUWU_HOST + raw

def _wuwu_list_url(cat, page):
    cat = str(cat or '').strip()
    if cat not in _WUWU_KNOWN:
        cat = '全部'
    base = _WUWU_HOST + '/index.php/vod/type/id/1.html'
    if cat != '全部':
        base = _WUWU_HOST + '/index.php/vod/show/class/' + quote(cat, safe='') + '/id/1.html'
    if page > 1:
        base += '?page=%d' % page
    return base

def _wuwu_parse_list(page):
    items = {}
    order = []
    for name, img, rem in _WUWU_LIST_PATTERNS:
        re_name = re.compile(
            r'(?is)<a[^>]*class="[^"]*\b' + re.escape(name) +
            r'\b[^"]*"[^>]*href="[^"]*vod/detail/id/(\d+)\.html"[^>]*>(.*?)</a>')
        for m in re_name.finditer(page):
            vid = m.group(1)
            it = items.get(vid)
            if it is None:
                it = {'id': vid, 'name': '', 'pic': '', 'rem': ''}
                items[vid] = it
                order.append(vid)
            if it['name']:
                continue
            inner = m.group(2)
            sm = re.compile(r'(?is)<span[^>]*>([^<]+)</span>').search(inner)
            if sm:
                it['name'] = clean_text(html_unescape(sm.group(1)))
            else:
                it['name'] = clean_text(html_unescape(strip_tags(inner)))
        re_img = re.compile(
            r'(?is)<a[^>]*class="[^"]*\b' + re.escape(img) +
            r'\b[^"]*"[^>]*href="[^"]*vod/detail/id/(\d+)\.html"[^>]*>(.*?)</a>')
        for m in re_img.finditer(page):
            vid = m.group(1)
            it = items.get(vid)
            if it is None or it['pic']:
                continue
            im = re.compile(r'(?is)<img[^>]*\bsrc="([^"]+)"').search(m.group(2))
            if im:
                it['pic'] = _wuwu_abs(im.group(1))
        re_rem = re.compile(
            r'(?is)<a[^>]*class="[^"]*\b' + re.escape(rem) +
            r'\b[^"]*"[^>]*href="[^"]*vod/detail/id/(\d+)\.html"[^>]*>(.*?)</a>')
        for m in re_rem.finditer(page):
            vid = m.group(1)
            it = items.get(vid)
            if it is None or it['rem']:
                continue
            it['rem'] = clean_text(html_unescape(strip_tags(m.group(2))))
    out = []
    for vid in order:
        it = items[vid]
        if not it['name']:
            continue
        out.append(it)
    return out

def _wuwu_box_episodes(box):
    seen = set()
    out = []
    for m in _WUWU_RE_EP.finditer(box):
        no = atoi(m.group(2), 0)
        if no < 1 or no in seen:
            continue
        seen.add(no)
        name = clean_text(html_unescape(strip_tags(m.group(3))))
        if not name:
            name = '第%d集' % no
        out.append({'no': no, 'name': name, 'url': _wuwu_abs(m.group(1))})
    return out

def _wuwu_extract_src(page):
    m = _WUWU_RE_PLAYER.search(page)
    if m:
        raw = m.group(1).replace('\\/', '/')
        if is_http_media(raw):
            return raw
    m = _WUWU_RE_ANY_URL.search(page)
    if m:
        cand = m.group(1).strip()
        if _WUWU_RE_B64.match(cand):
            dec = b64_decode(cand)
            if not dec:
                dec = b64_decode(cand, padding=False)
            if dec:
                cand = dec.decode('utf-8', 'ignore')
        if is_http_media(cand):
            return cand
    m = _WUWU_RE_MEDIA.search(page)
    if m:
        found = m.group(0)
        if found.startswith('//'):
            found = 'https:' + found
        if is_http_media(found):
            return found
    return ''

class WuwuSite(object):

    key = 'wuwu'
    name = '五五'
    site = _WUWU_HOST

    CATS = [
        ('全部', '全部'),
        ('男频', '男频'),
        ('女频', '女频'),
        ('都市', '都市'),
        ('虐渣', '虐渣'),
        ('励志', '励志'),
        ('逆袭', '逆袭'),
        ('古风', '古风'),
        ('复仇', '复仇'),
        ('家庭', '家庭'),
        ('悬疑', '悬疑'),
        ('奇幻', '奇幻'),
    ]

    def __init__(self):
        self._play_map = {}

    def cats(self):
        return [{'type_id': item[0], 'type_name': item[1]} for item in self.CATS]

    def _get(self, raw_url):
        status, text = http_get(raw_url, headers={
            'User-Agent': _WUWU_UA,
            'Referer': _WUWU_REFERER,
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        }, timeout=20)
        if status == 0 or status >= 400 or not text:
            return '', '五五接口 HTTP %d' % status
        return text, ''

    def _card(self, item, cat):
        return {
            'vod_id': item['id'],
            'vod_name': item['name'],
            'vod_pic': item['pic'],
            'vod_remarks': item['rem'],
            'vod_class': cat,
            'vod_score': '',
            'vod_content': '',
        }

    def listing(self, cat, page):
        page = page if page and page > 0 else 1
        body, err = self._get(_wuwu_list_url(cat, page))
        if err:
            print('[五五] 列表失败: %s' % err)
            return []
        cat_name = str(cat or '全部').strip()
        cards = [self._card(item, cat_name) for item in _wuwu_parse_list(body)]
        if not cards:
            print('[五五] 列表无内容')
            return []
        return cards

    def search(self, wd, page):
        wd = str(wd or '').strip()
        if not wd:
            print('[五五] 搜索关键词为空')
            return []
        page = page if page and page > 0 else 1
        u = _WUWU_HOST + '/index.php/vod/search/wd/' + quote(wd, safe='') + '.html'
        if page > 1:
            u += '?page=%d' % page
        body, err = self._get(u)
        if err:
            print('[五五] 搜索失败: %s' % err)
            return []
        cards = [self._card(item, '搜索') for item in _wuwu_parse_list(body)]
        if not cards:
            print('[五五] 搜索无结果: %s' % wd)
            return []
        return cards

    def detail(self, vid):
        vid = str(vid or '').strip()
        if not vid:
            print('[五五] 无效的剧集 ID')
            return {}
        page, err = self._get(_WUWU_HOST + '/index.php/vod/detail/id/'
                              + quote(vid, safe='') + '.html')
        if err:
            print('[五五] 详情抓取失败: %s' % err)
            return {}
        name = ''
        m = _WUWU_RE_TITLE.search(page)
        if m:
            title = clean_text(html_unescape(m.group(1)))
            name = title.split('-', 1)[0].strip()
        pic = ''
        m = _WUWU_RE_COVER.search(page)
        if m:
            pic = _wuwu_abs(m.group(1))
        else:
            m = _WUWU_RE_OG_IMAGE.search(page)
            if m:
                pic = _wuwu_abs(m.group(1))
        desc = ''
        m = _WUWU_RE_DESC.search(page)
        if m:
            desc = truncate(clean_text(html_unescape(strip_tags(m.group(1)))), 400)
        episodes = []
        play_map = {}
        for box in _WUWU_RE_BOX.findall(page):
            eps_raw = _wuwu_box_episodes(box)
            if not eps_raw:
                continue
            for item in eps_raw:
                no = item['no']
                play_map[no] = item['url']
                episodes.append({'no': no, 'name': item['name'],
                                 'url': 'wuwu://%s/%d' % (vid, no)})
            break
        episodes = sort_episodes(episodes)
        if not episodes:
            print('[五五] 详情无剧集: %s' % vid)
            return {}
        self._play_map[vid] = play_map
        return {
            'vod_id': vid,
            'vod_name': name or ('五五短剧 ' + vid),
            'vod_pic': pic,
            'vod_content': desc,
            'vod_class': '五五',
            'vod_remarks': '共%d集' % len(episodes),
            'episodes': episodes,
        }

    def play(self, url):
        raw = str(url or '').strip()
        prefix = 'wuwu://'
        play_url = ''
        if raw.startswith(prefix):
            rest = raw[len(prefix):].strip('/')
            cut = rest.rfind('/')
            if cut <= 0 or cut >= len(rest) - 1:
                print('[五五] 播放参数无效')
                return {}
            vid, seq_text = rest[:cut], rest[cut + 1:]
            seq = atoi(seq_text, 0)
            if seq < 1 or not vid:
                print('[五五] 播放参数无效')
                return {}
            play_url = self._play_map.get(vid, {}).get(seq, '')
            if not play_url:
                body, err = self._get(_WUWU_HOST + '/index.php/vod/detail/id/'
                                      + quote(vid, safe='') + '.html')
                if err:
                    print('[五五] 播放页抓取失败: %s' % err)
                    return {}
                for box in _WUWU_RE_BOX.findall(body):
                    pmap = {}
                    for item in _wuwu_box_episodes(box):
                        pmap[item['no']] = item['url']
                    if pmap:
                        play_url = pmap.get(seq, '')
                        if play_url:
                            self._play_map[vid] = pmap
                    if play_url:
                        break
            if not play_url:
                print('[五五] 播放资源不存在: %s' % raw)
                return {}
        elif raw.startswith('http'):
            play_url = raw
        else:
            print('[五五] 播放参数无效')
            return {}
        page, err = self._get(play_url)
        if err:
            print('[五五] 播放页抓取失败: %s' % err)
            return {}
        src = _wuwu_extract_src(page)
        if not src:
            print('[五五] 播放地址解析失败')
            return {}
        return {'url': src,
                'header': {'User-Agent': _WUWU_UA, 'Referer': _WUWU_REFERER},
                'parse': 0}

import time
import random
import hmac
import hashlib
from urllib.parse import quote, urlparse, urlencode

_SOUJU_BASE = 'https://souju.ai'
_SOUJU_SIGN_KEY = 'f39d73aa7a6426203cdee1ef17b31d3b7ea8c23f4c59c62a3a8aa0f39ee5e79d'
_SOUJU_UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
             '(KHTML, like Gecko) Chrome/124.0 Safari/537.36')
_SOUJU_CLIENT_NAME = 'movie-search-frontend'
_SOUJU_CLIENT_VER = '1.0.0'
_SOUJU_BUILD_VER = 'aimovie-v2026.09.28.4-f41bc3c82d76-web'
_SOUJU_PROTO_VER = '2026-07-05.library-v2.playback-v1'
_SOUJU_PAGE_SIZE = 24
_SOUJU_MAX_PAGES = 20
_SOUJU_EP_LIMIT = 100
_SOUJU_EP_CACHE_MAX = 240

def _souju_abs_url(raw):
    raw = (raw or '').strip()
    if not raw:
        return ''
    if raw.startswith('http://') or raw.startswith('https://'):
        return raw
    if raw.startswith('//'):
        return 'https:' + raw
    if raw.startswith('/'):
        return _SOUJU_BASE + raw
    return raw

def _souju_nonce():
    return ''.join(random.choice('0123456789abcdef') for _ in range(32))

def _souju_sign_headers(method, path):
    ts = str(int(time.time() * 1000))
    nonce = _souju_nonce()
    msg = '%s\n%s\n%s\n%s' % (method, path, ts, nonce)
    sig = hmac.new(_SOUJU_SIGN_KEY.encode('utf-8'), msg.encode('utf-8'),
                   hashlib.sha256).hexdigest()
    return {
        'User-Agent': _SOUJU_UA,
        'Accept': 'application/json',
        'x-ai-movie-client-name': _SOUJU_CLIENT_NAME,
        'x-ai-movie-client-version': _SOUJU_CLIENT_VER,
        'x-ai-movie-build-version': _SOUJU_BUILD_VER,
        'x-ai-movie-protocol-version': _SOUJU_PROTO_VER,
        'x-ai-movie-timestamp': ts,
        'x-ai-movie-nonce': nonce,
        'x-ai-movie-signature': sig,
    }

class SoujuSite(object):

    key = 'souju'
    name = '搜剧AI'
    site = _SOUJU_BASE

    CATS = [
        ('all', '全部'),
        ('脑洞悬疑', '脑洞悬疑'),
        ('古装仙侠', '古装仙侠'),
        ('现代都市', '现代都市'),
        ('女频恋爱', '女频恋爱'),
        ('言情总裁', '言情总裁'),
        ('年代穿越', '年代穿越'),
        ('现代言情', '现代言情'),
        ('反转爽剧', '反转爽剧'),
        ('重生民国', '重生民国'),
    ]

    def __init__(self):
        self._ep_cache = {}
        self._ep_seq = []

    def _api(self, method, path, payload=None):
        headers = _souju_sign_headers(method, path)
        if payload is not None:
            headers['Content-Type'] = 'application/json'
        if method == 'POST':
            status, text = http_post(_SOUJU_BASE + path, json_body=payload,
                                     headers=headers, timeout=20)
        else:
            status, text = http_get(_SOUJU_BASE + path, headers=headers,
                                    timeout=20)
        if status >= 400:
            print('[搜剧AI] 接口 HTTP %d: %s' % (status, path))
            return None
        if not text:
            print('[搜剧AI] 空响应: %s' % path)
            return None
        try:
            return json.loads(text)
        except Exception as exc:
            print('[搜剧AI] 响应解析失败: %s' % exc)
            return None

    def _get_json(self, path):
        return self._api('GET', path)

    def cats(self):
        return [{'type_id': item[0], 'type_name': item[1]} for item in self.CATS]

    def _browse_path(self, genre, wd, page):
        page = page if page and page > 0 else 1
        params = {'kind': 'short_drama', 'intent': 'latest_catalog'}
        if wd:
            params['intent'] = 'catalog_search'
            params['query'] = wd
        elif genre and genre != 'all':
            params['genre'] = genre
        params['page'] = str(page)
        params['limit'] = str(_SOUJU_PAGE_SIZE)
        return '/v1/browse/catalog?' + urlencode(params)

    def _cards(self, payload):
        cards = []
        seen = set()
        items = payload.get('cards') or payload.get('items') or []
        for c in items:
            if not isinstance(c, dict):
                continue
            cid = str(c.get('id') or '')
            title = clean_text(c.get('title'))
            if not cid or not title or cid in seen:
                continue
            avail = c.get('availability') or {}
            if avail.get('disabled'):
                continue
            seen.add(cid)
            card = {
                'vod_id': cid,
                'vod_name': title,
                'vod_pic': _souju_abs_url(c.get('poster_url')),
            }
            genres = c.get('genres') or []
            if genres:
                card['vod_class'] = '/'.join(str(g) for g in genres if g)
            parts = []
            try:
                year = int(c.get('year') or 0)
            except (TypeError, ValueError):
                year = 0
            if year > 0:
                parts.append(str(year))
            remarks = clean_text(c.get('remarks'))
            if remarks:
                parts.append(remarks)
            card['vod_remarks'] = ' · '.join(parts)
            cards.append(card)
        return cards

    def listing(self, cat, page):
        page = page if page and page > 0 else 1
        if page > _SOUJU_MAX_PAGES:
            return []
        cat = str(cat or '').strip() or 'all'
        payload = self._get_json(self._browse_path(cat, '', page))
        if not isinstance(payload, dict):
            return []
        return self._cards(payload)

    def search(self, wd, page):
        wd = str(wd or '').strip()
        if not wd:
            return []
        page = page if page and page > 0 else 1
        payload = self._get_json(self._browse_path('', wd, page))
        if not isinstance(payload, dict):
            return []
        return self._cards(payload)

    def _episodes(self, vid):
        cached = self._ep_cache.get(vid)
        if cached:
            return cached
        all_eps = []
        seen = set()
        offset = 0
        while offset < 2000:
            path = ('/v1/catalog/%s/episodes?limit=%d&offset=%d&order=asc'
                    % (quote(vid, safe=''), _SOUJU_EP_LIMIT, offset))
            payload = self._get_json(path)
            if not isinstance(payload, dict):
                if all_eps:
                    break
                return []
            eps = payload.get('episodes') or []
            for e in eps:
                if not isinstance(e, dict):
                    continue
                token = str(e.get('token') or '')
                if not token or token in seen:
                    continue
                seen.add(token)
                all_eps.append(e)
            pag = payload.get('episode_pagination') or payload.get('pagination') or {}
            if not pag.get('has_more') or not eps:
                break
            offset += _SOUJU_EP_LIMIT
        if not all_eps:
            print('[搜剧AI] 无分集: %s' % vid)
            return []

        def _ep_no(e):
            try:
                num = int(e.get('number') or 0)
            except (TypeError, ValueError):
                num = 0
            if num > 0:
                return num
            try:
                return int(str(e.get('key') or '').strip() or 0)
            except (TypeError, ValueError):
                return 0

        all_eps.sort(key=_ep_no)
        if len(self._ep_cache) >= _SOUJU_EP_CACHE_MAX:
            self._ep_cache = {}
        self._ep_cache[vid] = all_eps
        return all_eps

    def detail(self, vid):
        vid = str(vid or '').strip()
        if '/' in vid:
            vid = vid[vid.rfind('/') + 1:]
        if not vid:
            print('[搜剧AI] 无效的剧集 ID')
            return {}
        path = '/v1/catalog/%s?episodes=window&episode_limit=1' % quote(vid, safe='')
        d = self._get_json(path)
        if not isinstance(d, dict):
            return {}
        eps = self._episodes(vid)
        if not eps:
            print('[搜剧AI] 详情页无剧集: %s' % vid)
            return {}
        episodes = []
        for i, e in enumerate(eps):
            try:
                no = int(e.get('number') or 0)
            except (TypeError, ValueError):
                no = 0
            if no <= 0:
                try:
                    no = int(str(e.get('key') or '').strip() or 0)
                except (TypeError, ValueError):
                    no = 0
            if no <= 0:
                no = i + 1
            name = clean_text(e.get('title')) or ('第%d集' % no)
            episodes.append({
                'no': no,
                'name': name,
                'url': '%s://%s/%d' % (self.key, vid, no),
            })
        tag = []
        try:
            year = int(d.get('year') or 0)
        except (TypeError, ValueError):
            year = 0
        if year > 0:
            tag.append(str(year))
        area = clean_text(d.get('area'))
        if area:
            tag.append(area)
        genres = d.get('genres') or []
        for g in genres:
            if isinstance(g, str) and g:
                tag.append(g)
        desc = clean_text(d.get('overview')) or clean_text(d.get('description'))
        return {
            'vod_id': vid,
            'vod_name': clean_text(d.get('title')) or ('搜剧AI ' + vid),
            'vod_pic': _souju_abs_url(d.get('poster_url')),
            'vod_content': desc,
            'vod_class': '/'.join(tag),
            'vod_remarks': '共%d集' % len(episodes),
            'episodes': episodes,
        }

    @staticmethod
    def _pick_line(lines):
        sel_mp4, any_mp4, sel_any, any_line = None, None, None, None
        ok = [False, False, False, False]
        for line in lines:
            if not isinstance(line, dict):
                continue
            if not line.get('url'):
                continue
            kind = str(line.get('url_kind') or '')
            selected = bool(line.get('selected'))
            if kind == 'mp4':
                if selected and not ok[0]:
                    sel_mp4, ok[0] = line, True
                elif not ok[1]:
                    any_mp4, ok[1] = line, True
            if selected and not ok[2]:
                sel_any, ok[2] = line, True
            if not ok[3]:
                any_line, ok[3] = line, True
        for line, hit in ((sel_mp4, ok[0]), (any_mp4, ok[1]),
                          (sel_any, ok[2]), (any_line, ok[3])):
            if hit and line:
                return line
        return None

    def _resolve_line(self, line):
        raw = str(line.get('url') or '').strip()
        if raw.startswith('resolve://'):
            ticket = raw[len('resolve://'):]
            payload = {
                'ticket': ticket,
                'selection': {
                    'playback_source_id': line.get('playback_source_id') or '',
                    'provider_id': line.get('provider_id') or '',
                },
            }
            body = self._api('POST', '/v1/playback/resolve-line?view=compact',
                             payload)
            if not isinstance(body, dict):
                return ''
            line = body.get('line') or {}
            raw = str(line.get('url') or '').strip()
        if not raw or raw.startswith('resolve://'):
            return ''
        return raw

    def play(self, url):
        raw = str(url or '').strip()
        vid, extra = '', ''
        if raw.startswith('souju://'):
            rest = raw[len('souju://'):].strip('/')
            if '/' in rest:
                vid, extra = rest.split('/', 1)
            else:
                vid = rest
        elif '/short-drama/' in raw:
            rest = raw[raw.find('/short-drama/') + len('/short-drama/'):]
            if '?' in rest:
                head, _, query = rest.partition('?')
                for part in query.split('&'):
                    if part.startswith('episode='):
                        extra = part[len('episode='):]
                rest = head
            vid = rest.strip('/')
        if not vid:
            print('[搜剧AI] 播放参数无效')
            return {}
        try:
            seq = int(extra)
        except (TypeError, ValueError):
            seq = 1
        if seq < 1:
            seq = 1
        eps = self._episodes(vid)
        if not eps:
            return {}
        if seq > len(eps):
            seq = len(eps)
        token = eps[seq - 1].get('token') or ''
        if not token:
            print('[搜剧AI] 分集 token 缺失')
            return {}
        rr = self._get_json('/v1/playback/resolve/' + quote(token, safe=''))
        if not isinstance(rr, dict):
            return {}
        line = self._pick_line(rr.get('line_options') or [])
        if not line:
            print('[搜剧AI] 无可用播放线路')
            return {}
        media = self._resolve_line(line)
        if not is_http_media(media):
            print('[搜剧AI] 播放地址无效')
            return {}
        return {'url': media, 'header': {'Referer': self.site + '/'}, 'parse': 0}

_YIZK_UA = ('Mozilla/5.0 (Linux; Android 13) AppleWebKit/537.36 '
            '(KHTML, like Gecko) Chrome/150.0.0.0 Mobile Safari/537.36')
_YIZK_PAGE_SIZE = 24
_YIZK_MAX_PAGES = 100
_YIZK_MAX_EPS = 999
_YIZK_LANDING_HOSTS = ['https://1zk.top', 'https://1zk.me']
_YIZK_ENTRY_HOSTS = ['yizk.net', '7kan.net']
_YIZK_SITE_BASE = 'https://1zk.top'

_YIZK_RE_NON_IMAGE = re.compile(r'(?i)\.(?:svg|gif|json|webp)$')

class YizkSite(object):

    key = 'yizk'
    name = '一直看'
    site = _YIZK_SITE_BASE

    CATS = [
        ('all', '全部'),
        ('sqqsfvqirt0c', '华语'),
        ('jr4jltgb5bct', '东瀛'),
        ('967tfsdaht5i', '欧美'),
        ('ovamj5o7x0gs', '三级'),
        ('fs7hmy32jqfm', 'Ai短剧'),
    ]

    def __init__(self):
        self._hosts = list(_YIZK_LANDING_HOSTS)
        self._current = ''

    def _headers(self, host):
        return {
            'User-Agent': _YIZK_UA,
            'Accept': 'application/json',
            'Referer': host + '/',
        }

    def _probe(self, host):
        try:
            status, text = http_get(host + '/api/v1/system/public',
                                    headers=self._headers(host), timeout=15)
        except Exception:
            return False
        if status >= 400 or not text:
            return False
        try:
            return json.loads(text).get('code') == 0
        except Exception:
            return False

    def _refresh_hosts(self):
        for entry in _YIZK_ENTRY_HOSTS:
            base = 'https://' + entry.strip().rstrip('/')
            try:
                status, text = http_get(base + '/api/router/list',
                                        headers={'User-Agent': _YIZK_UA,
                                                 'Referer': base + '/'},
                                        timeout=15)
            except Exception:
                continue
            if status >= 400 or not text:
                continue
            try:
                payload = json.loads(text)
            except Exception:
                continue
            routes = (payload.get('data') or {}).get('routes') or []
            hosts = []
            for r in routes:
                domain = str(r.get('domain') or '').strip()
                if not domain:
                    continue
                hosts.append('https://' + domain.strip().rstrip('/'))
            if hosts:
                return hosts
        return []

    def _call(self, method, path, payload=None):
        hosts = list(self._hosts)
        if not hosts:
            hosts = list(_YIZK_LANDING_HOSTS)
        last_err = ''
        for host in hosts:
            try:
                if method == 'POST':
                    headers = self._headers(host)
                    headers['Content-Type'] = 'application/json'
                    status, text = http_post(host + path, json_body=payload,
                                             headers=headers, timeout=20)
                else:
                    status, text = http_get(host + path,
                                            headers=self._headers(host),
                                            timeout=20)
            except Exception as exc:
                last_err = str(exc)
                continue
            if status >= 400 or not text:
                last_err = 'HTTP %d' % status
                continue
            try:
                resp = json.loads(text)
            except Exception as exc:
                last_err = str(exc)
                continue
            if resp.get('code') != 0:
                last_err = 'code=%s %s' % (resp.get('code'), resp.get('message'))
                continue
            self._current = host
            return resp.get('data')
        fresh = self._refresh_hosts()
        if not fresh:
            if last_err:
                print('[一直看] 接口失败: %s' % last_err)
            return None
        self._hosts = fresh
        for host in fresh:
            try:
                if method == 'POST':
                    headers = self._headers(host)
                    headers['Content-Type'] = 'application/json'
                    status, text = http_post(host + path, json_body=payload,
                                             headers=headers, timeout=20)
                else:
                    status, text = http_get(host + path,
                                            headers=self._headers(host),
                                            timeout=20)
            except Exception:
                continue
            if status >= 400 or not text:
                continue
            try:
                resp = json.loads(text)
            except Exception:
                continue
            if resp.get('code') != 0:
                continue
            self._current = host
            return resp.get('data')
        print('[一直看] 线路换池后仍失败: %s' % last_err)
        return None

    def cats(self):
        cats = [{'type_id': item[0], 'type_name': item[1]} for item in self.CATS]
        data = self._call('GET', '/api/v1/categories')
        if not isinstance(data, list):
            return cats
        seen = {str(item[0]) for item in self.CATS}
        for c in data:
            if not isinstance(c, dict):
                continue
            token = str(c.get('token') or '')
            name = clean_text(c.get('name'))
            parent = c.get('parent_id')
            if not token or not name or token in seen:
                continue
            if parent is not None and int(parent or 0) != 0:
                continue
            seen.add(token)
            cats.append({'type_id': token, 'type_name': name})
        return cats

    @staticmethod
    def _pic(raw):
        raw = (raw or '').strip()
        if not raw:
            return ''
        low = raw.lower()
        if low.startswith('data:') or _YIZK_RE_NON_IMAGE.search(low):
            return ''
        return raw

    def _cards(self, data):
        cards = []
        seen = set()
        for f in data.get('list') or []:
            if not isinstance(f, dict):
                continue
            token = str(f.get('token') or '')
            title = clean_text(f.get('title'))
            if not token or not title or token in seen:
                continue
            seen.add(token)
            card = {
                'vod_id': token,
                'vod_name': title,
                'vod_pic': self._pic(f.get('cover_url')),
            }
            try:
                count = int(f.get('episode_count') or 0)
            except (TypeError, ValueError):
                count = 0
            if count > 1:
                card['vod_remarks'] = '共%d集' % count
            cat = f.get('category')
            if isinstance(cat, dict) and cat.get('name'):
                card['vod_class'] = clean_text(cat.get('name'))
            cards.append(card)
        return cards

    def _list_path(self, cat, page, wd=''):
        page = page if page and page > 0 else 1
        if wd:
            return ('/api/v1/films?keyword=%s&page=%d&pageSize=%d'
                    % (quote(wd, safe=''), page, _YIZK_PAGE_SIZE))
        cat = str(cat or '').strip()
        if cat and cat != 'all':
            return ('/api/v1/categories/%s/films?page=%d&pageSize=%d'
                    % (quote(cat, safe=''), page, _YIZK_PAGE_SIZE))
        return '/api/v1/films?page=%d&pageSize=%d' % (page, _YIZK_PAGE_SIZE)

    def listing(self, cat, page):
        page = page if page and page > 0 else 1
        if page > _YIZK_MAX_PAGES:
            return []
        data = self._call('GET', self._list_path(cat, page))
        if not isinstance(data, dict):
            return []
        return self._cards(data)

    def search(self, wd, page):
        wd = str(wd or '').strip()
        if not wd:
            return []
        page = page if page and page > 0 else 1
        data = self._call('GET', self._list_path('', page, wd))
        if not isinstance(data, dict):
            return []
        return self._cards(data)

    def detail(self, vid):
        vid = str(vid or '').strip()
        if '/' in vid:
            vid = vid[vid.rfind('/') + 1:]
        for sep in ('?', '#', '/'):
            if sep in vid:
                vid = vid.split(sep, 1)[0]
        if not vid:
            print('[一直看] 无效的剧集 ID')
            return {}
        f = self._call('GET', '/api/v1/films/' + quote(vid, safe=''))
        if not isinstance(f, dict):
            return {}
        try:
            count = int(f.get('episode_count') or 0)
        except (TypeError, ValueError):
            count = 0
        if count < 1:
            count = 1
        if count > _YIZK_MAX_EPS:
            count = _YIZK_MAX_EPS
        episodes = []
        for n in range(1, count + 1):
            episodes.append({
                'no': n,
                'name': '第%d集' % n,
                'url': '%s://%s/%d' % (self.key, vid, n),
            })
        tag = ''
        cat = f.get('category')
        if isinstance(cat, dict) and cat.get('name'):
            tag = clean_text(cat.get('name'))
        return {
            'vod_id': str(f.get('token') or vid),
            'vod_name': clean_text(f.get('title')) or ('一直看 ' + vid),
            'vod_pic': self._pic(f.get('cover_url')),
            'vod_content': clean_text(f.get('description')),
            'vod_class': tag,
            'vod_remarks': '共%d集' % len(episodes),
            'episodes': episodes,
        }

    def play(self, url):
        raw = str(url or '').strip()
        vid, extra = '', ''
        if raw.startswith('yizk://'):
            rest = raw[len('yizk://'):].strip('/')
            if '/' in rest:
                vid, extra = rest.split('/', 1)
            else:
                vid = rest
        elif '/film/' in raw:
            rest = raw[raw.find('/film/') + len('/film/'):]
            for sep in ('?', '#', '/'):
                if sep in rest:
                    rest = rest.split(sep, 1)[0]
            vid = rest.strip('/')
        if not vid:
            print('[一直看] 播放参数无效')
            return {}
        try:
            episode = int(extra)
        except (TypeError, ValueError):
            episode = 1
        if episode < 1:
            episode = 1
        data = self._call('POST', '/api/v1/films/' + quote(vid, safe='') + '/play',
                          payload={'episode': episode, 'source': 'direct'})
        if not isinstance(data, dict):
            return {}
        media = str(data.get('playUrl') or '').strip()
        if not is_http_media(media):
            print('[一直看] 播放地址无效')
            return {}
        host = self._current or self._hosts[0] if self._hosts else self.site
        return {'url': media, 'header': {'Referer': host + '/'}, 'parse': 0}

PLATFORM_CLASSES = [
    HongguoSite,
    WeiguanSite,
    HemaSite,
    ShanhaiSite,
    HaokanSite,
    BaiduSite,
    XingyaSite,
    QimaoSite,
    DamangSite,
    XifanSite,
    XingxingSite,
    YimiSite,
    WushengSite,
    QixingSite,
    KuwoSite,
    NiuniuSite,
    XifuSite,
    SoujuSite,
    WuwuSite,
    HuangdouSite,
    Huangdou2Site,
    HuangguoSite,
    Huangguo2Site,
    HuangguooldSite,
    HuangjuSite,
    YeguoSite,
    Dj51Site,
    ChengguoSite,
    XiangjiaoSite,
    HuangguaSite,
    KuangbiaoSite,
    Dj91Site,
    Md2048Site,
    DuanjuoneSite,
    YizkSite,
]

try:
    from inspect import signature as _py_signature
except Exception:
    _py_signature = None

def _listing_accepts_extend(site):
    if _py_signature is None:
        return False
    try:
        return len(_py_signature(site.listing).parameters) >= 3
    except (TypeError, ValueError):
        return False

_DUOJU_CONFIG_FILE = 'duoju_config.json'

def _duoju_config_path():
    return _RUNTIME_BASE_DIR / _DUOJU_CONFIG_FILE

def _read_last_platform():
    try:
        with open(_duoju_config_path(), 'r', encoding='utf-8') as f:
            data = json.load(f)
        value = str(data.get('last_platform') or '').strip()
        return value if value else 'hongguo'
    except Exception:
        return 'hongguo'

def _write_last_platform(tid):
    try:
        path = _duoju_config_path()
        data = {}
        try:
            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f)
        except Exception:
            data = {}
        if not isinstance(data, dict):
            data = {}
        data['last_platform'] = str(tid or '')
        tmp = path.with_suffix(path.suffix + '.tmp')
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False)
        os.replace(tmp, path)
    except Exception:
        pass

class SiteRouter(object):

    FILTER_CLASS = 'class'
    PAGE_SIZE = 24

    PROXY_KEYS = frozenset({'yeguo', 'dj51', 'duanjuone'})

    def __init__(self):
        self.sites = {}
        self.order = []
        self._cats = {}
        self._cats_at = {}
        for cls in PLATFORM_CLASSES:
            try:
                site = cls()
            except Exception as exc:
                print('[聚合短剧] 平台初始化失败 %s: %s' % (cls, exc))
                continue
            key = str(getattr(site, 'key', '') or '')
            if not key or key in self.sites:
                continue
            self.sites[key] = site
            self.order.append(key)

    def platforms(self):
        result = []
        for key in self.order:
            site = self.sites[key]
            name = str(getattr(site, 'name', key) or key)
            if key in self.PROXY_KEYS:
                name += '✈'
            result.append({'type_id': key, 'type_name': name})
        return result

    def site_of(self, tid):
        if tid in self.sites:
            return self.sites[tid]
        if self.order:
            return self.sites[self.order[0]]
        return None

    def cats_of(self, tid):
        now = time.time()
        if tid in self._cats and now - self._cats_at.get(tid, 0) < 600:
            return self._cats[tid]
        site = self.site_of(tid)
        try:
            cats = site.cats() or []
        except Exception as exc:
            print('[聚合短剧] 分类读取失败: %s' % exc)
            cats = []
        if not cats:
            cats = [{'type_id': 'all', 'type_name': '全部'}]
        self._cats[tid] = cats
        self._cats_at[tid] = now
        return cats

    def default_cat(self, tid):
        return str(self.cats_of(tid)[0].get('type_id') or 'all')

    def pick_cat(self, tid, extend):
        wanted = ''
        if isinstance(extend, dict):
            wanted = str(extend.get(self.FILTER_CLASS) or '')
        valid = {str(item.get('type_id')) for item in self.cats_of(tid)}
        return wanted if wanted in valid else self.default_cat(tid)

    def filters(self, tid):
        site = self.site_of(tid)
        cats = self.cats_of(tid)
        if len(cats) <= 1:
            entries = []
        else:
            entries = [{
                'key': self.FILTER_CLASS,
                'name': '\u5206\u7c7b',
                'value': [{'n': str(item.get('type_name') or ''),
                           'v': str(item.get('type_id') or '')}
                          for item in cats],
            }]
        extra = getattr(site, 'extra_filters', None)
        if callable(extra):
            try:
                for one in extra() or []:
                    entries.append(one)
            except Exception as exc:
                print('[聚合短剧] 附加筛选读取失败: %s' % exc)
        return entries

    @staticmethod
    def encode_id(site_key, vid):
        return '%s:%s' % (site_key, vid)

    def decode_id(self, vid):
        raw = str(vid or '')
        if '://' in raw:
            scheme = raw.split('://', 1)[0].lower()
            if scheme in self.sites:
                return self.sites[scheme], raw
        if ':' in raw:
            head, tail = raw.split(':', 1)
            if head in self.sites:
                return self.sites[head], tail
        return self.site_of('hongguo'), raw

class Spider(_BaseSpider):

    def __init__(self):
        self.router = SiteRouter()

    def getName(self):
        return '\u805a\u5408\u77ed\u5267'

    def init(self, extend=''):
        for key in self.router.order:
            ensure = getattr(self.router.sites[key], 'ensure', None)
            if callable(ensure):
                try:
                    ensure()
                except Exception as exc:
                    print('[聚合短剧] %s 初始化失败: %s'
                          % (self.router.sites[key].name, exc))

    def isVideoFormat(self, url):
        return False

    def manualVideoCheck(self):
        return False

    def destroy(self):
        return

    def localProxy(self, params):
        return None

    @staticmethod
    def _page(value):
        try:
            page = int(value)
        except (TypeError, ValueError):
            page = 1
        return page if page > 0 else 1

    @staticmethod
    def _vod(site_key, item):
        item = item if isinstance(item, dict) else {}
        vid = str(item.get('vod_id') or '')
        return {
            'vod_id': SiteRouter.encode_id(site_key, vid),
            'vod_name': str(item.get('vod_name') or ''),
            'vod_pic': str(item.get('vod_pic') or ''),
            'vod_remarks': str(item.get('vod_remarks') or ''),
            'type_name': str(item.get('vod_class') or ''),
            'vod_tag': str(item.get('vod_class') or ''),
            'vod_content': str(item.get('vod_content') or ''),
        }

    def _listing(self, tid, page, extend=None):
        router = self.router
        site = router.site_of(tid)
        cat = router.pick_cat(tid, extend)
        key = str(getattr(site, 'key', '') or '')
        if not site:
            return []
        try:
            if extend is not None and _listing_accepts_extend(site):
                items = site.listing(cat, page, extend) or []
            else:
                items = site.listing(cat, page) or []
        except Exception as exc:
            print('[聚合短剧] %s 列表读取失败: %s'
                  % (getattr(site, 'name', key), exc))
            items = []
        return [self._vod(key, item) for item in items]

    def homeContent(self, filter):
        result = {'class': self.router.platforms()}
        if filter:
            entries = {}
            lock = threading.Lock()

            def _build(tid):
                try:
                    flt = self.router.filters(tid)
                except Exception as exc:
                    print('[聚合短剧] 筛选构建失败 %s: %s' % (tid, exc))
                    flt = []
                with lock:
                    entries[tid] = flt

            threads = []
            for item in result['class']:
                tid = item['type_id']
                t = threading.Thread(target=_build, args=(tid,), daemon=True)
                t.start()
                threads.append(t)
            for t in threads:
                t.join(timeout=15)
            result['filters'] = entries
        try:
            result['list'] = self.homeVideoContent().get('list', [])
        except Exception:
            result['list'] = []
        return result

    def _random_home_vods(self):
        order = list(self.router.order)
        if not order:
            return []
        random.shuffle(order)
        for site_key in order:
            items = self._listing(site_key, 1)
            if items:
                return items
        return []

    def homeVideoContent(self):
        return {'list': self._random_home_vods()}

    def categoryContent(self, tid, pg, filter, extend):
        page = self._page(pg)
        extend = extend if isinstance(extend, dict) else {}
        tid = str(tid)
        if tid in self.router.sites:
            _write_last_platform(tid)
        vods = self._listing(tid, page, extend)
        pagecount = page if len(vods) < SiteRouter.PAGE_SIZE else page + 1
        return {'list': vods, 'page': page, 'pagecount': pagecount,
                'limit': SiteRouter.PAGE_SIZE,
                'total': pagecount * SiteRouter.PAGE_SIZE}

    def detailContent(self, ids):
        try:
            vid = str(ids[0])
        except (TypeError, IndexError):
            return {'list': []}
        site, pure = self.router.decode_id(vid)
        if site is None:
            return {'list': []}
        try:
            detail = site.detail(pure) or {}
        except Exception as exc:
            print('[聚合短剧] %s 详情读取失败: %s'
                  % (getattr(site, 'name', '?'), exc))
            detail = {}
        sources = detail.get('sources')
        if not sources:
            episodes = detail.get('episodes') or []
            if not episodes:
                return {'list': []}
            sources = [{'name': str(getattr(site, 'name', '') or ''),
                        'episodes': episodes}]
        vod = {
            'vod_id': vid,
            'vod_name': str(detail.get('vod_name') or ''),
            'vod_pic': str(detail.get('vod_pic') or ''),
            'type_name': str(detail.get('vod_class') or ''),
            'vod_remarks': str(detail.get('vod_remarks') or ''),
            'vod_content': str(detail.get('vod_content') or ''),
            'vod_play_from': '$$$'.join(str(s.get('name') or '') for s in sources),
            'vod_play_url': '$$$'.join(
                '#'.join('%s$%s' % (ep.get('name') or ep.get('no'), ep.get('url'))
                         for ep in sorted(s.get('episodes') or [],
                                          key=lambda item: item.get('no') or 0))
                for s in sources),
        }
        return {'list': [vod]}

    def searchContent(self, key, quick, pg=1):
        page = self._page(pg)
        word = str(key or '').strip()
        if not word:
            return {'list': [], 'page': page}
        target = _read_last_platform()
        if target not in self.router.sites:
            target = 'hongguo'
        site = self.router.sites[target]
        try:
            items = site.search(word, page) or []
        except Exception as exc:
            print('[聚合短剧] %s 搜索失败: %s'
                  % (getattr(site, 'name', target), exc))
            items = []
        pname = str(getattr(site, 'name', target) or target)
        collected = []
        for item in items:
            vod = self._vod(target, item)
            if vod.get('vod_remarks'):
                vod['vod_remarks'] = '%s · %s' % (pname, vod['vod_remarks'])
            else:
                vod['vod_remarks'] = pname
            vod['type_name'] = pname
            vod['vod_tag'] = pname
            collected.append(vod)
        return {'list': collected, 'page': page}

    def searchContentPage(self, key, quick, pg=1):
        return self.searchContent(key, quick, pg)

    def playerContent(self, flag, pid, vipFlags):
        raw = str(pid or '').split('#')[0]
        result = {'parse': 0, 'playUrl': '', 'url': '', 'header': {}}
        if is_http_media(raw):
            result['url'] = raw
            return result
        site, pure = self.router.decode_id(raw)
        if site is None:
            return result
        try:
            resolved = site.play(pure) or {}
        except Exception as exc:
            print('[聚合短剧] %s 播放解析失败: %s'
                  % (getattr(site, 'name', '?'), exc))
            resolved = {}
        result['url'] = str(resolved.get('url') or '')
        result['header'] = resolved.get('header') or {}
        result['parse'] = int(resolved.get('parse') or 0)
        return result
