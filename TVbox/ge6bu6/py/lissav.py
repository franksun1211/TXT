# -*- coding: utf-8 -*-
# LissAV https://lissav.my/asian/zh-CN
# 播放接口: /asian/zh-CN/api/video/stream?video_uid={uid}
import re
import json
import sys
from urllib.parse import quote

sys.path.append('..')
try:
    from base.spider import Spider as BaseSpider
except ImportError:
    class BaseSpider(object):
        def init(self, extend=""):
            pass

try:
    import requests
except ImportError:
    requests = None


class Spider(BaseSpider):
    def __init__(self):
        self.host = 'https://lissav.my'
        self.base = '/asian/zh-CN'
        self.ua = (
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
            'AppleWebKit/537.36 (KHTML, like Gecko) '
            'Chrome/131.0.0.0 Safari/537.36'
        )
        self.channels = {
            'recent': {'name': '最近更新', 'path': '/asian/zh-CN/videos/recent'},
            'new_releases': {'name': '新作上市', 'path': '/asian/zh-CN/videos/new-releases'},
            'hot_today': {'name': '今日热门', 'path': '/asian/zh-CN/videos/hot/today'},
            'hot_week': {'name': '本周热门', 'path': '/asian/zh-CN/videos/hot/week'},
            'hot_month': {'name': '本月热门', 'path': '/asian/zh-CN/videos/hot/month'},
            'uncensored_leak': {'name': '无码流出', 'path': '/asian/zh-CN/videos/tag/%E6%97%A0%E7%A0%81%E6%B5%81%E5%87%BA'},
            'chinese_sub': {'name': '中文字幕', 'path': '/asian/zh-CN/videos/tag/%E4%B8%AD%E6%96%87%E5%AD%97%E5%B9%95'},
            'danti': {'name': '单体作品', 'path': '/asian/zh-CN/videos/tag/%E5%8D%95%E4%BD%93%E4%BD%9C%E5%93%81'},
        }

    def getName(self):
        return 'LissAV'

    def init(self, extend=""):
        if extend:
            try:
                conf = json.loads(extend) if isinstance(extend, str) and extend.strip().startswith('{') else {}
                if conf.get('host'):
                    self.host = conf['host'].rstrip('/')
                if conf.get('base'):
                    self.base = conf['base']
            except Exception:
                pass

    def _headers(self, referer=None, accept=None):
        return {
            'User-Agent': self.ua,
            'Accept': accept or 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'zh-CN,zh;q=0.9',
            'Referer': referer or (self.host + self.base + '/'),
        }

    def _get(self, url, referer=None, accept=None):
        try:
            headers = self._headers(referer, accept)
            if requests is None:
                import urllib.request
                req = urllib.request.Request(url, headers=headers)
                with urllib.request.urlopen(req, timeout=18) as resp:
                    return resp.read().decode('utf-8', 'ignore')
            r = requests.get(url, headers=headers, timeout=18, verify=False)
            r.encoding = 'utf-8'
            return r.text if r.status_code == 200 else ''
        except Exception as e:
            print('GET error', url, e)
            return ''

    def _abs(self, u):
        if not u:
            return ''
        u = u.strip().replace('\\/', '/')
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
        for m in re.finditer(
            r'href="((?:https?://(?:www\.)?lissav\.my)?/asian/zh-CN/video/cid/([a-zA-Z0-9._-]+))"',
            html, re.I
        ):
            href, cid = m.group(1), m.group(2)
            full = self._abs(href)
            if full in seen:
                continue
            seen.add(full)
            block = html[max(0, m.start() - 400):m.end() + 900]
            title = ''
            tm = re.search(r'(?:title|alt)=["\']([^"\']{2,300})["\']', block)
            if tm:
                title = tm.group(1).strip()
            if not title or len(title) < 2:
                tm2 = re.search(r'streamit-video-card__caption-link[^>]*title=["\']([^"\']+)["\']', block)
                if tm2:
                    title = tm2.group(1).strip()
            if not title:
                title = cid.upper()
            pic = ''
            for pat in (
                r'data-poster=["\']([^"\']+)["\']',
                r'src=["\'](https?://[^"\']+cover[^"\']*\.(?:jpg|jpeg|png|webp)[^"\']*)["\']',
                r'src=["\'](https?://[^"\']+\.(?:jpg|jpeg|png|webp)[^"\']*)["\']',
            ):
                pm = re.search(pat, block, re.I)
                if pm and 'logo' not in pm.group(1).lower() and 'loading' not in pm.group(1).lower():
                    pic = self._abs(pm.group(1))
                    break
            tips = ''
            em = re.search(r'streamit-video-card__edition[^>]*>([^<]+)<', block)
            if em:
                tips = em.group(1).strip()
            if not tips:
                tm3 = re.search(r'streamit-video-card__time[^>]*>[\s\S]*?<p[^>]*>([^<]+)</p>', block)
                if tm3:
                    tips = tm3.group(1).strip()
            videos.append({
                'vod_id': full,
                'vod_name': title[:120],
                'vod_pic': pic,
                'vod_remarks': tips or cid.upper(),
            })
        return videos

    def _extract_uid(self, html):
        if not html:
            return ''
        for pat in (
            r'data-streamit-page-video-uid=["\']([a-f0-9]+)["\']',
            r'videoUid\s*=\s*["\']([a-f0-9]+)["\']',
            r'"videoUid"\s*:\s*"([a-f0-9]+)"',
        ):
            m = re.search(pat, html, re.I)
            if m:
                return m.group(1)
        return ''

    def _fetch_playlist(self, uid, referer=None):
        if not uid:
            return []
        api = '%s%s/api/video/stream?video_uid=%s' % (self.host, self.base, uid)
        text = self._get(
            api,
            referer=referer or (self.host + self.base + '/'),
            accept='application/json, text/plain, */*',
        )
        if not text or not text.strip().startswith('{'):
            return []
        try:
            data = json.loads(text)
        except Exception:
            return []
        plays, seen = [], set()
        pl = data.get('playlist') or data.get('sources') or []
        for item in pl:
            if not isinstance(item, dict):
                continue
            u = item.get('url') or item.get('src') or ''
            if not u or u in seen or 'preview' in u.lower():
                continue
            seen.add(u)
            mode = (item.get('playMode') or '').lower()
            label = '线路%d' % (len(plays) + 1)
            if mode:
                label = '%s(%s)' % (label, mode)
            plays.append((label, u))
        return plays

    def homeContent(self, filter):
        classes = [{'type_id': k, 'type_name': v['name']} for k, v in self.channels.items()]
        return {'class': classes, 'list': []}

    def homeVideoContent(self):
        html = self._get(self.host + self.base + '/videos/recent')
        return {'list': self._parse_list(html)[:24]}

    def categoryContent(self, tid, pg, filter, extend):
        pg = int(pg or 1)
        tid = str(tid or 'recent')
        info = self.channels.get(tid) or self.channels['recent']
        path = info['path']
        url = self.host + path if pg <= 1 else self.host + path + ('&' if '?' in path else '?') + 'page=%d' % pg
        html = self._get(url)
        videos = self._parse_list(html)
        pagecount = pg + 1 if videos and (len(videos) >= 12 or re.search(r'[?&]page=%d' % (pg + 1), html) or 'pagination' in html) else pg
        return {'list': videos, 'page': pg, 'pagecount': pagecount, 'limit': 24, 'total': 9999}

    def detailContent(self, ids):
        result = {'list': []}
        if not ids:
            return result
        page_url = str(ids[0])
        if not page_url.startswith('http'):
            page_url = self.host + (page_url if page_url.startswith('/') else self.base + '/video/cid/' + page_url)
        html = self._get(page_url)
        if not html:
            return result
        name = ''
        m = re.search(r'<title>([^<]+)</title>', html, re.I)
        if m:
            name = re.sub(r'^\s*LissAV\s*[|｜]\s*', '', m.group(1)).strip()
            name = re.sub(r'\s*[-|—].*$', '', name).strip()
        if not name:
            m = re.search(r'property=["\']og:title["\'][^>]*content=["\']([^"\']+)["\']', html, re.I)
            if m:
                name = re.sub(r'^\s*LissAV\s*[|｜]\s*', '', m.group(1)).strip()
        pic = ''
        m = re.search(r'property=["\']og:image["\'][^>]*content=["\']([^"\']+)["\']', html, re.I)
        if m:
            pic = self._abs(m.group(1))
        desc = ''
        m = re.search(r'property=["\']og:description["\'][^>]*content=["\']([^"\']+)["\']', html, re.I)
        if m:
            desc = m.group(1).strip()[:500]
        uid = self._extract_uid(html)
        plays = self._fetch_playlist(uid, referer=page_url)
        if not plays:
            seen = set()
            for u in re.findall(r'https?://[^\s"\'<>]+\.(?:m3u8|mp4)[^\s"\'<>]*', html):
                u = u.replace('&amp;', '&')
                if u in seen or 'preview' in u.lower():
                    continue
                seen.add(u)
                plays.append(('直链%d' % (len(plays) + 1), u))
        if not plays and uid:
            plays = [('线路API', '%s%s/api/video/stream?video_uid=%s' % (self.host, self.base, uid))]
        if not plays:
            plays = [('正片', page_url)]
        play_url = '#'.join(['%s$%s' % (n, u) for n, u in plays])
        result['list'] = [{
            'vod_id': page_url,
            'vod_name': name or 'LissAV',
            'vod_pic': pic,
            'vod_remarks': uid,
            'vod_actor': '',
            'vod_director': '',
            'vod_content': desc,
            'vod_play_from': 'LissAV',
            'vod_play_url': play_url,
        }]
        return result

    def searchContent(self, key, quick, pg=1):
        return self.searchContentPage(key, quick, pg)

    def searchContentPage(self, key, quick, pg=1):
        pg = int(pg or 1)
        key = (key or '').strip()
        if not key:
            return {'list': [], 'page': 1, 'pagecount': 1, 'limit': 24, 'total': 0}
        url = self.host + self.base + '/videos/search/' + quote(key)
        if pg > 1:
            url += '?page=%d' % pg
        html = self._get(url)
        videos = self._parse_list(html)
        if not videos:
            url2 = self.host + self.base + '/search?q=' + quote(key)
            if pg > 1:
                url2 += '&page=%d' % pg
            html = self._get(url2)
            videos = self._parse_list(html)
        return {
            'list': videos,
            'page': pg,
            'pagecount': pg + 1 if len(videos) >= 12 else pg,
            'limit': 24,
            'total': len(videos),
        }

    def playerContent(self, flag, id, vipFlags):
        header = {
            'User-Agent': self.ua,
            'Referer': self.host + '/',
            'Origin': self.host,
        }
        play = str(id or '').strip()
        if play.startswith('http') and re.search(r'\.(m3u8|mp4)(\?|$)', play, re.I):
            return {'parse': 0, 'url': play, 'header': header}
        if '/api/video/stream' in play:
            m = re.search(r'video_uid=([a-f0-9]+)', play, re.I)
            uid = m.group(1) if m else ''
            plays = self._fetch_playlist(uid)
            if plays:
                return {'parse': 0, 'url': plays[0][1], 'header': header}
            return {'parse': 1, 'jx': '1', 'url': play, 'header': header}
        if not play.startswith('http'):
            play = self._abs(play)
        html = self._get(play, referer=self.host + self.base + '/')
        uid = self._extract_uid(html)
        plays = self._fetch_playlist(uid, referer=play)
        if plays:
            return {'parse': 0, 'url': plays[0][1], 'header': header}
        for u in re.findall(r'https?://[^\s"\'<>]+\.(?:m3u8|mp4)[^\s"\'<>]*', html or ''):
            u = u.replace('&amp;', '&')
            if 'preview' not in u.lower():
                return {'parse': 0, 'url': u, 'header': header}
        return {'parse': 1, 'jx': '1', 'url': play, 'header': header}

    def isVideoFormat(self, url):
        return bool(url and re.search(r'\.(m3u8|mp4|ts)(\?|$)', url, re.I))

    def manualVideoCheck(self):
        return False

    def localProxy(self, param):
        return None
