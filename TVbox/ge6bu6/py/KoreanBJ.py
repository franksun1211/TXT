# -*- coding: utf-8 -*-
"""
KoreanBJ https://ww1.koreanbj.club
列表: loop-video /video/slug/
播放: VOE 系 embed → 六层混淆解包 → m3u8/mp4 直出
风格参考 SupJav (voe_decode + 直链优先)
"""
import re
import json
import time
import base64
import codecs
import sys
from urllib.parse import quote

sys.path.append('..')
try:
    from base.spider import Spider as BaseSpider
except Exception:
    class BaseSpider(object):
        def init(self, extend=''):
            pass

try:
    import requests
except Exception:
    requests = None

import urllib.request
import ssl

UA = (
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
    '(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
)


class Spider(BaseSpider):
    def __init__(self):
        self.host = 'https://ww1.koreanbj.club'
        self.name = 'KoreanBJ'
        self.ua = UA
        self.channels = {
            'home': {'name': '最新', 'path': '/'},
            'korean-bj': {'name': 'Korean BJ', 'path': '/category/korean-bj/'},
            'pandatv': {'name': 'PandaTV', 'path': '/category/pandatv/'},
            'onlyfans': {'name': 'OnlyFans', 'path': '/category/onlyfans/'},
            'amateur': {'name': 'Amateur', 'path': '/category/amateur-porn/'},
            'kpop': {'name': 'Kpop Deepfake', 'path': '/category/kpop-deepfake/'},
            'twitch': {'name': 'Twitch', 'path': '/category/twitch/'},
            'premium': {'name': 'Premium', 'path': '/category/premium/'},
            'stripchat': {'name': 'Stripchat', 'path': '/category/stripchat/'},
            'fantrie': {'name': 'Fantrie', 'path': '/category/fantrie/'},
            'nude': {'name': 'Nude', 'path': '/category/nude/'},
            'pov': {'name': 'POV', 'path': '/category/pov/'},
        }
        self._play_cache = {}
        self._play_ttl = 90

    def getName(self):
        return self.name

    def init(self, extend=''):
        if extend and str(extend).startswith('http'):
            self.host = str(extend).rstrip('/')

    def destroy(self):
        pass

    def _headers(self, referer=None):
        return {
            'User-Agent': self.ua,
            'Accept': 'text/html,application/xhtml+xml,*/*;q=0.8',
            'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
            'Referer': referer or (self.host + '/'),
        }

    def _get(self, url, referer=None, timeout=22):
        if not url.startswith('http'):
            url = self.host + (url if url.startswith('/') else '/' + url)
        headers = self._headers(referer)
        try:
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
                return resp.read().decode('utf-8', 'ignore')
        except Exception as e:
            print('urllib GET', e)
        if requests is not None:
            try:
                r = requests.get(url, headers=headers, timeout=timeout, verify=False)
                if r.status_code == 200:
                    return r.text
            except Exception as e:
                print('requests GET', e)
        return ''

    def _abs(self, u):
        if not u:
            return ''
        u = str(u).strip().replace('\\/', '/').replace('&amp;', '&')
        if u.startswith('//'):
            return 'https:' + u
        if u.startswith('/'):
            return self.host + u
        if not u.startswith('http'):
            return self.host + '/' + u
        return u

    def _parse_list(self, html):
        videos, seen = [], set()
        if not html:
            return videos
        blocks = re.findall(
            r'(?:<article[^>]*)?class="[^"]*loop-video[^"]*"[\s\S]{0,1200}',
            html, re.I
        )
        for block in blocks:
            lm = re.search(
                r'href="(https?://[^"]+/video/[^"]+|/video/[^"]+)"[^>]*(?:title="([^"]*)")?',
                block, re.I
            )
            if not lm:
                continue
            vod_id = self._abs(lm.group(1).split('?')[0])
            if vod_id in seen:
                continue
            seen.add(vod_id)
            name = (lm.group(2) or '').strip()
            if not name:
                tm = re.search(r'title="([^"]+)"', block)
                name = tm.group(1).strip() if tm else vod_id.rstrip('/').rsplit('/', 1)[-1]
            name = re.sub(r'\s*[–\-]\s*KoreanBJ.*$', '', name, flags=re.I)
            name = name.replace('&#8211;', '-').replace('&amp;', '&').strip()
            pic = ''
            for pm in re.finditer(r'(?:data-src|src)="([^"]+)"', block, re.I):
                p = pm.group(1)
                if 'placeholder' in p:
                    continue
                if re.search(r'\.(jpg|jpeg|png|webp)', p, re.I) or 'upload' in p:
                    pic = self._abs(p)
                    break
            videos.append({
                'vod_id': vod_id,
                'vod_name': re.sub(r'\s+', ' ', name)[:120],
                'vod_pic': pic,
                'vod_remarks': 'KoreanBJ',
            })
        return videos

    def homeContent(self, filter=False):
        classes = [{'type_id': k, 'type_name': v['name']} for k, v in self.channels.items()]
        return {'class': classes, 'filters': {}}

    def homeVideoContent(self):
        return {'list': self._parse_list(self._get(self.host + '/'))[:24]}

    def categoryContent(self, tid, pg, filter=False, extend=None):
        pg = max(1, int(pg or 1))
        tid = str(tid or 'home').strip()
        info = self.channels.get(tid) or self.channels['home']
        path = info['path']
        if path == '/':
            url = self.host + '/' if pg == 1 else self.host + '/page/%d/' % pg
        else:
            url = self.host + path
            if pg > 1:
                url = url.rstrip('/') + '/page/%d/' % pg
        html = self._get(url)
        if not html:
            html = self._get(url)
        videos = self._parse_list(html)
        pages = [int(x) for x in re.findall(r'/page/(\d+)/', html or '')]
        pagecount = max(pages + [pg]) if videos else pg
        return {
            'list': videos, 'page': pg, 'pagecount': pagecount,
            'limit': 24, 'total': pagecount * max(len(videos), 1),
        }

    def searchContent(self, key, quick, pg='1'):
        pg = max(1, int(pg or 1))
        kw = quote(str(key or '').strip())
        if not kw:
            return {'list': []}
        url = '%s/?s=%s' % (self.host, kw)
        if pg > 1:
            url = '%s/page/%d/?s=%s' % (self.host, pg, kw)
        return {
            'list': self._parse_list(self._get(url)),
            'page': pg, 'pagecount': pg + 1, 'limit': 24, 'total': 9999,
        }

    # ---------- VOE 解包 (对齐 SupJav) ----------
    @staticmethod
    def _voe_decode(html):
        m = re.search(
            r'<script[^>]*type=["\']application/json["\'][^>]*>(.*?)</script>',
            html or '', re.S
        )
        if not m:
            return {}
        blk = m.group(1).strip()
        try:
            j = json.loads(blk)
            raw = j[0] if isinstance(j, list) and j else blk
        except Exception:
            raw = blk
        s = str(raw)
        for k in ('@$', '^^', '~@', '%?', '*~', '!!', '#&'):
            s = s.replace(k, '_')
        s = s.replace('_', '')

        def b64d(x):
            x = re.sub(r'[^A-Za-z0-9+/=]', '', x)
            x += '=' * (-len(x) % 4)
            return base64.b64decode(x).decode('utf-8', 'replace')

        try:
            t = b64d(codecs.encode(s, 'rot13'))
            t = ''.join(chr(ord(c) - 3) for c in t)
            t = b64d(t[::-1])
            cfg = json.loads(t)
            return cfg if isinstance(cfg, dict) else {}
        except Exception:
            return {}

    def _extract_embed_urls(self, html):
        urls, seen = [], set()

        def add(u):
            u = self._abs(u)
            if not u or u in seen or not u.startswith('http'):
                return
            if 'koreanbj.club' in u and '/video/' in u and '/e/' not in u:
                return
            if any(x in u for x in ['googletag', 'ads', 'placeholder', '/trailers/', 'test-videos']):
                return
            if u.endswith('.webm'):
                return
            seen.add(u)
            urls.append(u)

        for m in re.finditer(r'"contentURL"\s*:\s*"([^"]+)"', html or ''):
            add(m.group(1).replace('\\/', '/'))
        for m in re.finditer(
            r"iframe\.setAttribute\(\s*['\"]src['\"]\s*,\s*['\"]([^'\"]+)['\"]",
            html or ''
        ):
            add(m.group(1))
        for m in re.finditer(
            r'<iframe[^>]+(?:src|data-src)=["\']([^"\']+)', html or '', re.I
        ):
            add(m.group(1))
        for m in re.finditer(
            r'https?://(?:[a-z0-9\-]+\.)?(?:voe|io\d+storege|streamtape|filemoon)[^"\'\s<>]+',
            html or '', re.I
        ):
            add(m.group(0))
        return urls

    def _resolve_voe(self, embed_url, referer=None):
        """打开 VOE embed，解包得到 m3u8 / mp4"""
        html = self._get(embed_url, referer=referer or self.host + '/')
        if not html:
            return '', ''
        # 明文
        hits = re.findall(r'https?://[^\s"\'<>\\]+\.m3u8[^\s"\'<>\\]*', html)
        if hits and 'test-videos' not in hits[0]:
            return hits[0], ''
        cfg = self._voe_decode(html)
        src = str(cfg.get('source') or '')
        if '.m3u8' in src or src.startswith('http'):
            if '.m3u8' in src:
                return src, ''
        dau = str(cfg.get('direct_access_url') or '')
        if dau.startswith('http'):
            return '', dau
        if src.startswith('http'):
            return '', src
        return '', ''

    def _cache_get(self, key):
        v = self._play_cache.get(key)
        if not v:
            return None
        if time.time() - v[0] > self._play_ttl:
            self._play_cache.pop(key, None)
            return None
        return v[1]

    def _cache_put(self, key, val):
        self._play_cache[key] = (time.time(), val)

    def detailContent(self, ids):
        raw = ids[0] if isinstance(ids, (list, tuple)) else ids
        url = str(raw or '').strip()
        if not url.startswith('http'):
            url = self._abs(url)
        html = self._get(url)
        if not html:
            return {'list': []}
        name = ''
        m = re.search(r'<title>([^<]+)', html, re.I)
        if m:
            name = re.sub(r'\s*[|\-–].*$', '', m.group(1)).strip()
            name = name.replace('&#8211;', '-').strip()
        pic = ''
        m = re.search(r'property=["\']og:image["\'][^>]*content=["\']([^"\']+)', html, re.I)
        if m:
            pic = self._abs(m.group(1))

        embeds = self._extract_embed_urls(html)
        plays = []
        for i, emb in enumerate(embeds):
            # 详情阶段就解 VOE，给壳子真实 http 地址
            cached = self._cache_get(emb)
            if cached:
                m3u8, direct = cached
            else:
                m3u8, direct = self._resolve_voe(emb, referer=url)
                if m3u8 or direct:
                    self._cache_put(emb, (m3u8, direct))
            if m3u8:
                plays.append(('HLS', m3u8))
            if direct:
                plays.append(('MP4', direct))
            if not m3u8 and not direct:
                plays.append(('Embed', emb))

        if not plays:
            plays = [('原页', url)]

        # 去重
        seen, uniq = set(), []
        for n, u in plays:
            if u in seen:
                continue
            seen.add(u)
            uniq.append('%s$%s' % (n, u))

        return {
            'list': [{
                'vod_id': url,
                'vod_name': name or 'KoreanBJ',
                'vod_pic': pic,
                'vod_remarks': '',
                'vod_content': '',
                'vod_play_from': 'KoreanBJ',
                'vod_play_url': '#'.join(uniq),
            }]
        }

    def playerContent(self, flag, id, vipFlags=None):
        header = {
            'User-Agent': self.ua,
            'Referer': self.host + '/',
        }
        play = str(id or '').strip()
        if play.startswith('//'):
            play = 'https:' + play

        if re.search(r'\.(m3u8|mp4)(\?|$)', play, re.I):
            # m3u8 可能需要 embed 域 Referer
            if 'cloudwindow-route' in play or 'ugc-cdn' in play:
                header['Referer'] = 'https://jeremyparticipantanything.com/'
            return {'parse': 0, 'jx': 0, 'url': play, 'header': header}

        # 仍是 embed：再解一次
        if play.startswith('http') and ('/e/' in play or 'embed' in play.lower()):
            cached = self._cache_get(play)
            if cached:
                m3u8, direct = cached
            else:
                m3u8, direct = self._resolve_voe(play, referer=self.host + '/')
                if m3u8 or direct:
                    self._cache_put(play, (m3u8, direct))
            if m3u8:
                header['Referer'] = play
                return {'parse': 0, 'jx': 0, 'url': m3u8, 'header': header}
            if direct:
                header['Referer'] = play
                return {'parse': 0, 'jx': 0, 'url': direct, 'header': header}
            return {
                'parse': 1, 'jx': 0, 'url': play, 'header': header,
                'sniff': 1, 'sniff_include': ['.m3u8', '.mp4'],
            }

        return {
            'parse': 1, 'jx': 0, 'url': play, 'header': header,
            'sniff': 1, 'sniff_include': ['.m3u8', '.mp4'],
        }

    def isVideoFormat(self, url):
        return bool(url and re.search(r'\.(m3u8|mp4|ts)(\?|$)', str(url), re.I))

    def manualVideoCheck(self):
        return False

    def localProxy(self, param):
        return None
