# -*- coding: utf-8 -*-
#author 🍑
import json
import re
import os
import sys
import requests
from requests.exceptions import RequestException
try:
    from pyquery import PyQuery as pq
except Exception:
    pq = None
from base.spider import Spider

class Spider(Spider):
    name = 'Javbobo'
    host = 'https://javbobo.com'
    def init(self, extend=""):
        try:
            self.extend = json.loads(extend) if extend else {}
        except Exception:
            self.extend = {}
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:142.0) Gecko/20100101 Firefox/142.0',
            'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
            'Referer': f'{self.host}/',
            'Origin': self.host,
            'Connection': 'keep-alive',
        }
        self.session = requests.Session()
        self.session.headers.update(self.headers)
    def getName(self):
        return self.name
    def isVideoFormat(self, url):
        return any(ext in (url or '') for ext in ['.m3u8', '.mp4', '.ts'])
    def manualVideoCheck(self):
        return False
    def destroy(self):
        pass
    def homeContent(self, filter):
        result = {}
        try:
            cateManual = [
                {'type_name': '日本有碼', 'type_id': '47'},
                {'type_name': '日本無碼', 'type_id': '48'},
                {'type_name': '國產AV', 'type_id': '49'},
                {'type_name': '網紅主播', 'type_id': '50'},
            ]
            result['class'] = cateManual
            result['filters'] = {}
        except Exception:
            pass
        return result
    def homeVideoContent(self):
        return self.categoryContent('', '1', False, {})
    def categoryContent(self, tid, pg, filter, extend):
        pg = str(pg)
        result = {'page': pg, 'pagecount': 9999, 'limit': 90, 'total': 999999, 'list': []}
        try:
            url = self.host
            if tid:
                if str(tid).startswith('http'):
                    url = str(tid)
                    if pg != '1': url = f"{url}{'&' if '?' in url else '?'}page={pg}"
                elif str(tid).startswith('/'):
                    url = f"{self.host}{tid}"
                    if pg != '1': url = f"{url}{'&' if '?' in url else '?'}page={pg}"
                else:
                    url = f"{self.host}/vod/index.html?type_id={tid}"
                    if pg != '1': url = f"{self.host}/vod/index.html?page={pg}&type_id={tid}"
            resp = self.session.get(url, timeout=30)
            resp.raise_for_status()
            if pq is None: raise RuntimeError('PyQuery 未安装，无法解析列表页面')
            doc = pq(resp.text)
            def _parse_list(doc):
                vlist = []
                seen = set()
                for a in doc('a[href*="/vod/player.html"]').items():
                    href = a.attr('href') or ''
                    if not href: continue
                    full = href if href.startswith('http') else f"{self.host}{href}"
                    m = re.search(r'[?&]id=(\d+)', full)
                    if not m: continue
                    vid = m.group(1)
                    if vid in seen: continue
                    seen.add(vid)
                    img_el = a('img')
                    title = img_el.attr('alt') or a.attr('title') or (a.text() or '').strip()
                    if not title:
                        li = a.parents('li').eq(0)
                        title = li.find('h1,h2,h3').text().strip() if li else ''
                        if not title: title = f"视频{vid}"
                    img = img_el.attr('src') or img_el.attr('data-src') or ''
                    if img and not img.startswith('http'): img = f"{self.host}{img}"
                    vlist.append({
                        'vod_id': full, 'vod_name': title, 'vod_pic': img, 'vod_remarks': '',
                        'style': {'ratio': 1.33, 'type': 'rect'}
                    })
                    if len(vlist) >= 90: break
                return vlist
            result['list'] = _parse_list(doc)
            page_numbers = []
            for a in doc('a[href*="/vod/index.html?page="]').items():
                t = (a.text() or '').strip()
                if t.isdigit(): page_numbers.append(int(t))
            if page_numbers: result['pagecount'] = max(page_numbers)
        except Exception:
            result['list'] = []
        return result
    def detailContent(self, ids):
        try:
            url = ids[0] if isinstance(ids, list) else str(ids)
            if not url: return {'list': []}
            if not url.startswith('http'): url = f"{self.host}/vod/player.html?id={url}"
            resp = self.session.get(url, timeout=30)
            resp.raise_for_status()
            html = resp.text
            if pq is None: raise RuntimeError('PyQuery 未安装，无法解析详情页面')
            doc = pq(html)
            title = doc('meta[property="og:title"]').attr('content') or doc('h1').text().strip() or 'Javbobo 视频'
            vod_pic = doc('meta[property="og:image"]').attr('content') or ''
            if not vod_pic:
                img_el = doc('img').eq(0)
                vod_pic = img_el.attr('src') or img_el.attr('data-src') or ''
                if vod_pic and not vod_pic.startswith('http'): vod_pic = f"{self.host}{vod_pic}"
            line_id = None
            m = re.search(r"lineId\s*=\s*Number\('?(\d+)'?\)", html)
            if m: line_id = m.group(1)
            if not line_id:
                m = re.search(r"var\s+Iyplayer\s*=\s*\{[^}]*id:(\d+)", html)
                if m: line_id = m.group(1)
            play_id = line_id or url
            vod = {
                'vod_name': title, 'vod_pic': vod_pic, 'vod_content': '',
                'vod_play_from': 'Javbobo', 'vod_play_url': f'正片${play_id}'
            }
            return {'list': [vod]}
        except Exception:
            return {'list': []}
    def searchContent(self, key, quick, pg="1"):
        try:
            params = {'wd': key}
            url = f"{self.host}/index.html"
            resp = self.session.get(url, params=params, timeout=30)
            resp.raise_for_status()
            if pq is None: raise RuntimeError('PyQuery 未安装，无法解析搜索页面')
            doc = pq(resp.text)
            vlist = []
            seen = set()
            for a in doc('a[href*="/vod/player.html"]').items():
                href = a.attr('href') or ''
                if not href: continue
                full = href if href.startswith('http') else f"{self.host}{href}"
                m = re.search(r'[?&]id=(\d+)', full)
                if not m: continue
                vid = m.group(1)
                if vid in seen: continue
                seen.add(vid)
                img_el = a('img')
                title = img_el.attr('alt') or a.attr('title') or (a.text() or '').strip()
                img = img_el.attr('src') or img_el.attr('data-src') or ''
                if img and not img.startswith('http'): img = f"{self.host}{img}"
                vlist.append({
                    'vod_id': full, 'vod_name': title or f'视频{vid}', 'vod_pic': img,
                    'vod_remarks': '', 'style': {'ratio': 1.33, 'type': 'rect'}
                })
                if len(vlist) >= 60: break
            return {'list': vlist, 'page': pg, 'pagecount': 9999, 'limit': 90, 'total': 999999}
        except Exception:
            return {'list': []}
    def playerContent(self, flag, id, vipFlags):
        try:
            line_id = None
            sid = str(id or '')
            if re.fullmatch(r'\d+', sid):
                line_id = sid
            elif sid.startswith('http'):
                if self.isVideoFormat(sid):
                    headers = {'User-Agent': self.headers['User-Agent'], 'Referer': f'{self.host}/'}
                    return {'parse': 0, 'url': sid, 'header': headers}
                html = self.session.get(sid, timeout=30).text
                m = re.search(r"lineId\s*=\s*Number\('?(\d+)'?\)", html)
                if m: line_id = m.group(1)
                if not line_id:
                    m = re.search(r"var\s+Iyplayer\s*=\s*\{[^}]*id:(\d+)", html)
                    if m: line_id = m.group(1)
            else:
                if sid.startswith('/'): page_url = f"{self.host}{sid}"
                else: page_url = f"{self.host}/vod/player.html?id={sid}"
                html = self.session.get(page_url, timeout=30).text
                m = re.search(r"lineId\s*=\s*Number\('?(\d+)'?\)", html)
                if m: line_id = m.group(1)
                if not line_id:
                    m = re.search(r"var\s+Iyplayer\s*=\s*\{[^}]*id:(\d+)", html)
                    if m: line_id = m.group(1)
            if not line_id: raise ValueError('未能获取到播放线路ID(lineId)')
            api = f"{self.host}/openapi/playline/{line_id}"
            r = self.session.get(api, timeout=30)
            txt = r.text.strip()
            j = None
            try: j = r.json()
            except Exception: j = None
            if isinstance(j, str):
                try: j = json.loads(j)
                except Exception: j = None
            if not isinstance(j, dict):
                try: j = json.loads(txt)
                except Exception: j = {}
            m3u8_url = ''
            if isinstance(j, dict): m3u8_url = j.get('info', {}).get('file') or j.get('file') or ''
            headers = {'User-Agent': self.headers['User-Agent'], 'Referer': f'{self.host}/'}
            return {'parse': 0, 'url': m3u8_url, 'header': headers}
        except Exception:
            return {'parse': 0, 'url': '', 'header': {}}