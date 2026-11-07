# -*- coding: utf-8 -*-
"""
321AV https://321av.net
列表 /vodtype/{id}.html  播放 player_aaaa → /private-getvideo/{code}
入口用 /index.php 或 /enter（根路径 / 会 500）
"""
import re
import json
import base64
from urllib.parse import quote, unquote

try:
    import urllib3
    urllib3.disable_warnings()
except Exception:
    pass

try:
    import requests as req_lib
except Exception:
    req_lib = None

try:
    from base.spider import Spider as BaseSpider
except ImportError:
    class BaseSpider(object):
        def fetch(self, url, headers=None, timeout=15, **kw):
            import requests as rq
            r = rq.get(url, headers=headers or {}, timeout=timeout, verify=False, **kw)
            class R:
                pass
            o = R()
            o.text = r.text
            o.content = r.content
            o.status_code = r.status_code
            o.url = r.url
            return o


HOST = 'https://321av.net'
UA = (
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
    '(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36'
)

CLASS_LIST = [
    {'type_id': '20', 'type_name': '有码影片'},
    {'type_id': '21', 'type_name': '无码影片'},
    {'type_id': '22', 'type_name': '中文字幕'},
    {'type_id': 'home', 'type_name': '首页推荐'},
]


class Spider(BaseSpider):

    def __init__(self):
        try:
            super(Spider, self).__init__()
        except Exception:
            pass
        self.host = HOST
        self._ua = UA

    def init(self, extend=''):
        if extend and str(extend).startswith('http'):
            self.host = str(extend).rstrip('/')
        return True

    def getName(self):
        return '321AV'

    def isVideoFormat(self, url):
        low = (url or '').lower()
        return any(k in low for k in ('.m3u8', '.mp4', '.flv', '.ts'))

    def manualVideoCheck(self):
        return False

    def destroy(self):
        pass

    def _fix_txt(self, s):
        if not s:
            return ''
        s = str(s)
        if re.search(r'\\u[0-9a-fA-F]{4}', s):
            try:
                s = s.encode('utf-8').decode('unicode_escape')
            except Exception:
                pass
        s = re.sub(r'&#(\d+);', lambda m: chr(int(m.group(1))), s)
        return s.strip()

    def _headers(self, referer=None):
        return {
            'User-Agent': self._ua,
            'Referer': referer or (self.host + '/index.php'),
            'Accept': 'text/html,application/xhtml+xml,application/json,*/*;q=0.8',
            'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
            'Cache-Control': 'no-cache',
        }

    def _normalize_html(self, html):
        """处理被 JSON/反斜杠转义的 HTML"""
        if not html:
            return ''
        # 若整段像 JSON 字符串
        if html.startswith('"') and '\\n' in html[:200]:
            try:
                html = json.loads(html)
            except Exception:
                pass
        if '\\/vodplay' in html or 'href=\\"' in html:
            html = (
                html.replace('\\/', '/')
                .replace('\\"', '"')
                .replace("\\'", "'")
                .replace('\\n', '\n')
                .replace('\\t', '\t')
                .replace('\\u003c', '<')
                .replace('\\u003e', '>')
            )
            if re.search(r'\\u[0-9a-fA-F]{4}', html):
                try:
                    html = html.encode('utf-8').decode('unicode_escape')
                except Exception:
                    pass
        return html

    def _fetch(self, url, timeout=25):
        if url.startswith('/'):
            url = self.host + url
        # 禁止打根路径（会 500）
        if url.rstrip('/').endswith('321av.net'):
            url = self.host + '/index.php'
        text = ''
        headers = self._headers()
        # 1) requests 直连（更稳）
        if req_lib is not None:
            for t in (timeout, 35):
                try:
                    r = req_lib.get(url, headers=headers, timeout=t, verify=False)
                    if r.status_code == 200 and r.text and len(r.text) > 500:
                        text = r.text
                        break
                except Exception:
                    continue
        # 2) BaseSpider.fetch 兜底
        if not text:
            try:
                r = self.fetch(url, headers=headers, timeout=timeout)
                text = getattr(r, 'text', '') or ''
            except Exception:
                text = ''
        return self._normalize_html(text)

    def _parse_list(self, html):
        results, seen = [], set()
        html = self._normalize_html(html or '')
        if not html:
            return results

        # 策略1: 完整卡片（带图+标题）
        for m in re.finditer(
            r'href=["\']/vodplay/(\d+)-\d+-\d+\.html["\'][\s\S]{0,900}?'
            r'(?:data-src|src)=["\']([^"\']+)["\'][\s\S]{0,300}?alt=["\']([^"\']*)["\']',
            html, re.I
        ):
            vid = m.group(1)
            if vid in seen:
                continue
            seen.add(vid)
            pic = m.group(2)
            if pic.startswith('//'):
                pic = 'https:' + pic
            elif pic.startswith('/'):
                pic = self.host + pic
            title = self._fix_txt(re.sub(r'\s+', ' ', m.group(3) or ''))
            if 'loading' in pic:
                pic = ''
            results.append({
                'vod_id': vid,
                'vod_name': title or vid,
                'vod_pic': pic,
                'vod_remarks': '',
            })

        # 策略2: 链接 + 附近文字标题
        if len(results) < 8:
            for m in re.finditer(
                r'href=["\']/vodplay/(\d+)-\d+-\d+\.html["\'][^>]*>\s*([^<]{2,80})\s*<',
                html, re.I
            ):
                vid = m.group(1)
                if vid in seen:
                    continue
                title = self._fix_txt(m.group(2))
                if not title or title.isdigit():
                    continue
                seen.add(vid)
                results.append({
                    'vod_id': vid,
                    'vod_name': title,
                    'vod_pic': '',
                    'vod_remarks': '',
                })

        # 策略3: 仅 id（保证不空）
        if len(results) < 5:
            for m in re.finditer(r'/vodplay/(\d+)-\d+-\d+\.html', html):
                vid = m.group(1)
                if vid in seen:
                    continue
                seen.add(vid)
                # 尝试附近 alt / 文本
                block = html[max(0, m.start() - 50): m.start() + 500]
                title = vid
                am = re.search(r'alt=["\']([^"\']{2,100})["\']', block)
                if am:
                    title = self._fix_txt(am.group(1))
                pic = ''
                pm = re.search(r'data-src=["\']([^"\']+)["\']', block)
                if pm:
                    pic = pm.group(1).replace('\\/', '/')
                    if pic.startswith('//'):
                        pic = 'https:' + pic
                results.append({
                    'vod_id': vid,
                    'vod_name': title or vid,
                    'vod_pic': pic,
                    'vod_remarks': '',
                })
        return results

    def _parse_player_aaaa(self, html):
        html = self._normalize_html(html or '')
        if not html:
            return None
        candidates = []
        m = re.search(r'player_aaaa\s*=\s*(\{.*?\})\s*;?\s*<', html, re.S)
        if m:
            candidates.append(m.group(1))
        m = re.search(r'player_aaaa\s*=\s*(\{.*?\})', html, re.S)
        if m:
            candidates.append(m.group(1))
        m = re.search(r'player_aaaa\s*=\s*\{', html)
        if m:
            start = m.end() - 1
            depth = 0
            for i, c in enumerate(html[start:start + 8000]):
                if c == '{':
                    depth += 1
                elif c == '}':
                    depth -= 1
                    if depth == 0:
                        candidates.append(html[start:start + i + 1])
                        break
        for raw in candidates:
            for attempt in (raw, raw.replace('\\"', '"').replace("\\'", "'").replace('\\\\', '\\')):
                try:
                    data = json.loads(attempt)
                    if isinstance(data, dict) and ('url' in data or 'encrypt' in data):
                        return data
                except Exception:
                    continue
        return None

    def _decode_player_url(self, raw):
        if not raw:
            return ''
        s = unquote(str(raw).strip())
        try:
            pad = '=' * (-len(s) % 4)
            data = json.loads(base64.b64decode(s + pad).decode('utf-8', 'ignore'))
        except Exception:
            return ''
        code = ''
        ss = data.get('ss') if isinstance(data, dict) else None
        if isinstance(ss, list):
            for row in ss:
                if isinstance(row, list) and len(row) >= 2:
                    path = str(row[1])
                    m = re.search(r'/cn/([^/?#]+)', path, re.I)
                    if m:
                        code = m.group(1)
                        break
                elif isinstance(row, str):
                    m = re.search(r'/cn/([^/?#]+)', row, re.I)
                    if m:
                        code = m.group(1)
                        break
        return code

    def _play_from_code(self, code):
        plays = []
        if not code:
            return plays
        url = '%s/private-getvideo/%s' % (self.host, quote(code))
        try:
            text = self._fetch(url)
            data = json.loads(text)
        except Exception:
            # 再用 requests
            if req_lib is not None:
                try:
                    r = req_lib.get(url, headers=self._headers(), timeout=20, verify=False)
                    data = r.json()
                except Exception:
                    return plays
            else:
                return plays
        playlist = data.get('playlist') if isinstance(data, dict) else None
        if not isinstance(playlist, list):
            return plays
        seen = set()
        for item in playlist:
            if not isinstance(item, dict):
                continue
            u = item.get('url') or ''
            if not u.startswith('http') or u in seen:
                continue
            if not re.search(r'\.(m3u8|mp4)(\?|$)', u, re.I):
                continue
            seen.add(u)
            name = 'HLS' if '.m3u8' in u else 'MP4'
            qm = re.search(r'(\d{3,4})p', u, re.I)
            if qm:
                name = qm.group(1) + 'P'
            if item.get('playMode') == 'streampipe':
                name = '自适应'
            plays.append((name, u))
        return plays

    def homeContent(self, filter=False):
        return {'class': list(CLASS_LIST), 'list': [], 'filters': {}}

    def homeVideoContent(self):
        html = self._fetch(self.host + '/index.php')
        return {'list': self._parse_list(html)[:24]}

    def categoryContent(self, tid, pg=1, filter=False, extend=None):
        try:
            page = max(1, int(str(pg) or 1))
        except Exception:
            page = 1
        tid = str(tid or 'home').strip()

        urls = []
        if tid == 'home':
            urls.append(self.host + '/index.php' + (('?page=%d' % page) if page > 1 else ''))
            urls.append(self.host + '/enter')
        else:
            if page <= 1:
                urls.append('%s/vodtype/%s.html' % (self.host, tid))
            else:
                urls.append('%s/vodtype/%s-%d.html' % (self.host, tid, page))
                urls.append('%s/vodtype/%s.html?page=%d' % (self.host, tid, page))
                urls.append('%s/vodtype/%s.html' % (self.host, tid))

        vods = []
        for url in urls:
            html = self._fetch(url)
            vods = self._parse_list(html)
            if len(vods) >= 5:
                break
        # 仍空：回首页列表兜底，避免「找不到数据」
        if not vods and tid != 'home':
            html = self._fetch(self.host + '/index.php')
            vods = self._parse_list(html)

        return {
            'list': vods,
            'page': page,
            'pagecount': page + 1 if len(vods) >= 10 else page,
            'limit': 24,
            'total': 9999 if vods else 0,
        }

    def searchContent(self, key, quick=False, pg='1'):
        try:
            page = max(1, int(str(pg) or 1))
        except Exception:
            page = 1
        q = quote(str(key or '').strip())
        url = '%s/index.php/vod/search.html?wd=%s' % (self.host, q)
        if page > 1:
            url += '&page=%d' % page
        html = self._fetch(url)
        vods = self._parse_list(html)
        if not vods:
            # 备用搜索
            url2 = '%s/vodsearch/-------------.html?wd=%s' % (self.host, q)
            vods = self._parse_list(self._fetch(url2))
        return {
            'list': vods,
            'page': page,
            'pagecount': page + 1 if len(vods) >= 10 else page,
            'limit': 24,
            'total': 9999 if vods else 0,
        }

    def detailContent(self, ids):
        raw = ids[0] if isinstance(ids, (list, tuple)) else ids
        vid = re.search(r'(\d+)', str(raw or ''))
        vid = vid.group(1) if vid else ''
        if not vid:
            return {'list': []}
        page = '%s/vodplay/%s-1-1.html' % (self.host, vid)
        html = self._fetch(page)
        name = ''
        m = re.search(r'<h1[^>]*>([^<]+)</h1>', html, re.I)
        if not m:
            m = re.search(r'<h1[^>]*>([^<]+)', html, re.I)
        if m:
            name = self._fix_txt(m.group(1))
        if not name:
            m = re.search(r'<title>([^<]+)', html, re.I)
            if m:
                name = self._fix_txt(m.group(1))
                name = re.sub(r'\s*[-|].*$', '', name).strip()
                name = re.sub(r'^在线播放', '', name).strip()
        pic = ''
        m = re.search(r'og:image["\']\s+content=["\']([^"\']+)', html, re.I)
        if m:
            pic = m.group(1).replace('\\/', '/')

        code = ''
        data = self._parse_player_aaaa(html)
        if data:
            code = self._decode_player_url(data.get('url') or '')
        if not code:
            tm = re.search(r'([A-Z]{2,10}-?\d{2,5})', html or '', re.I)
            if tm:
                code = tm.group(1).lower().replace('_', '-')
        plays = self._play_from_code(code)
        if plays:
            play_from = '$$$'.join([n for n, _ in plays])
            play_url = '$$$'.join(['正片$%s' % u for _, u in plays])
        else:
            play_from = '321AV'
            play_url = '正片$%s' % page
        return {'list': [{
            'vod_id': vid,
            'vod_name': name or vid,
            'vod_pic': pic,
            'vod_play_from': play_from,
            'vod_play_url': play_url,
        }]}

    def playerContent(self, flag, id, vipFlags=None):
        url = str(id or '').strip()
        header = {
            'User-Agent': self._ua,
            'Referer': self.host + '/',
            'Origin': self.host,
        }
        if re.search(r'\.(m3u8|mp4)(\?|$)', url, re.I):
            return {'parse': 0, 'jx': 0, 'url': url, 'header': header}
        vid = ''
        m = re.search(r'/vodplay/(\d+)', url)
        if m:
            vid = m.group(1)
        elif re.match(r'^\d+$', url):
            vid = url
        if vid:
            d = self.detailContent([vid])
            item = (d.get('list') or [{}])[0]
            pu = item.get('vod_play_url') or ''
            for part in pu.split('$$$'):
                if '$' in part:
                    u = part.split('$', 1)[1]
                    if re.search(r'\.(m3u8|mp4)', u, re.I):
                        return {'parse': 0, 'jx': 0, 'url': u, 'header': header}
        return {'parse': 0, 'jx': 0, 'url': '', 'header': header}


if __name__ == '__main__':
    sp = Spider()
    sp.init()
    for tid in ['20', '21', '22', 'home']:
        r = sp.categoryContent(tid, 1)
        print(tid, len(r.get('list') or []), (r.get('list') or [{}])[0].get('vod_name', '')[:30])
