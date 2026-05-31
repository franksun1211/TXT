# -*- coding: utf-8 -*-
import re
import json
import html
from base.spider import Spider


class Spider(Spider):

    def __init__(self):
        self.name = 'avbebe'
        self.host = 'https://avbebe.com'
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 '
                         '(KHTML, like Gecko) Chrome/122.0.0.0 Mobile Safari/537.36',
            'Referer': f'{self.host}/',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
            'Accept-Language': 'zh-CN,zh;q=0.9',
            'Connection': 'keep-alive'
        }

        # 预编译正则
        self.re_post = re.compile(r'<article\s+class="jeg_post[^"]*".*?>(.*?)</article>', re.S | re.I)
        self.re_title_link = re.compile(
            r'<h3[^>]*class="jeg_post_title"[^>]*>\s*<a[^>]+href="([^"]+)"[^>]*>([^<]+)</a>', re.S | re.I)
        self.re_img = re.compile(r'<img[^>]+(?:data-src|data-lazy-src|src)="([^"]+)"', re.I)
        self.re_pagecount = re.compile(r'Page\s+\d+\s+of\s+(\d+)', re.I)
        self.re_title = re.compile(r'<title>(.*?)</title>', re.I)

    def getName(self):
        return self.name

    def init(self, extend=""):
        pass

    # =========================
    # 首页分类
    # =========================
    def homeContent(self, filter):
        return {
            'class': [
                {'type_id': '/archives/category/new', 'type_name': '新番'},
                {'type_id': '/archives/category/h動畫影片', 'type_name': '動畫卡通'},
                {'type_id': '/archives/category/3d動畫', 'type_name': '3D動畫'},
                {'type_id': '/archives/category/泡麵番', 'type_name': '泡麵番'},
                {'type_id': '/archives/category/高清中字', 'type_name': '高清中字'},
                {'type_id': '/archives/category/馬賽克破解', 'type_name': '無馬賽克'},
                {'type_id': '/archives/category/高清素人', 'type_name': '高清素人'},
                {'type_id': '/archives/category/綜合av', 'type_name': '綜合AV'},
                {'type_id': '/archives/category/華語av/華語av-素人', 'type_name': '華語素人'},
                {'type_id': '/archives/category/華語av/華語av-片商', 'type_name': '華語片商'}
            ]
        }

    def parse_list(self, html_str: str):
        videos = []
        JUR705_PIC = "https://pics.dmm.co.jp/digital/video/jur00705/jur00705pl.jpg"

        blocks = re.findall(
            r'<article class="jeg_post[^>]*>.*?</article>', 
            html_str, 
            re.S | re.I
        )

        for block in blocks:
            # 提取标题和链接
            link_match = re.search(
                r'<h3[^>]*class="jeg_post_title"[^>]*>\s*<a[^>]+href="([^"]+)"[^>]*>([^<]+)</a>',
                block, re.S | re.I
            )
            if not link_match:
                continue

            vod_id = link_match.group(1)
            if not vod_id.startswith('http'):
                vod_id = self.host + vod_id

            vod_name = re.sub(r'\s+', ' ', link_match.group(2)).strip()

            # 提取图片
            pic = ''
            pic_match = self.re_img.search(block)

            if pic_match:
                pic = pic_match.group(1).split('?')[0]
                if pic.startswith('data:'):
                    pic = ''
                if pic.startswith('//'):
                    pic = 'https:' + pic
                elif pic.startswith('/'):
                    pic = self.host + pic
            if re.search(r'\bJUR[-]?705\b', vod_name, re.I):
                pic = JUR705_PIC

            videos.append({
                'vod_id': vod_id,
                'vod_name': vod_name,
                'vod_pic': pic,
                'vod_remarks': ''
            })

        return videos

    def categoryContent(self, tid, pg, filter, extend):
        pg = int(pg)
        url = f"{self.host}{tid}"
        if pg > 1:
            url += f'/page/{pg}'

        html_text = self.fetch(url, headers=self.headers).text
        videos = self.parse_list(html_text)

        # 总页数
        pagecount = 1
        m = self.re_pagecount.search(html_text)
        if m:
            pagecount = int(m.group(1))

        return {'list': videos, 'page': pg, 'pagecount': pagecount}

    def parse_hglink(self, iframe_url: str) -> str:
        headers = {
            'User-Agent': self.headers['User-Agent'],
            'Referer': iframe_url
        }

        try:
            html_text = self.fetch(iframe_url, headers=headers).text
        except:
            return ''

        patterns = [
            r'(https?://[^"\']+\.m3u8[^"\']*)',
            r'file\s*:\s*"([^"]+\.m3u8[^"]*)"',
            r'file\s*=\s*"([^"]+\.m3u8[^"]*)"'
        ]

        for p in patterns:
            m = re.search(p, html_text, re.I)
            if m:
                url = m.group(1)
                if url.startswith('//'):
                    url = 'https:' + url
                return url
        return ''

    def detailContent(self, ids):
        url = ids[0]
        if not url.startswith('http'):
            url = self.host + url

        try:
            html_text = self.fetch(url, headers=self.headers).text
        except:
            return {'list': []}

        # 标题清理
        vod_name = ''
        t = self.re_title.search(html_text)
        if t:
            vod_name = re.sub(r'[-–|│·【】\[\]].*', '', t.group(1)).strip()

        # 1. aiovg 直链
        m = re.search(r'<source[^>]+type=["\']application/x-mpegurl["\'][^>]+src=["\']([^"\']+)', 
                     html_text, re.I)
        if m:
            m3u8 = m.group(1)
            if m3u8.startswith('//'):
                m3u8 = 'https:' + m3u8
            return {
                'list': [{
                    'vod_id': url,
                    'vod_name': vod_name,
                    'vod_play_from': 'aiovg',
                    'vod_play_url': f'直连${m3u8}'
                }]
            }

        # 2. flow 多集
        flow_items = re.findall(r'data-item=[\'"]({.*?})[\'"]', html_text, re.S)
        if flow_items:
            urls = []
            for i, item_str in enumerate(flow_items):
                try:
                    data = json.loads(html.unescape(item_str))
                    src = data.get('sources', [{}])[0].get('src')
                    if src:
                        episode_name = f"第{i+1:02d}集"
                        urls.append(f"{episode_name}${src}")
                except:
                    continue

            if urls:
                return {
                    'list': [{
                        'vod_id': url,
                        'vod_name': vod_name,
                        'vod_play_from': 'flow',
                        'vod_play_url': '#'.join(urls)
                    }]
                }

        # 3. mp4
        mp4 = re.search(r'<source[^>]+src=["\']([^"\']+\.mp4[^"\']*)["\']', html_text, re.I | re.S)
        if mp4:
            mp4_url = mp4.group(1)
            if mp4_url.startswith('//'):
                mp4_url = 'https:' + mp4_url
            return {
                'list': [{
                    'vod_id': url,
                    'vod_name': vod_name,
                    'vod_play_from': '直连mp4',
                    'vod_play_url': f'直连${mp4_url}'
                }]
            }

        # 4. iframe
        for src in re.findall(r'<iframe[^>]+(?:src|data-src)=["\']([^"\']+)', html_text, re.I):
            if 'hgcloud' in src or '/e/' in src:
                return {
                    'list': [{
                        'vod_id': url,
                        'vod_name': vod_name,
                        'vod_play_from': 'iframe',
                        'vod_play_url': f'播放${src}'
                    }]
                }
        return {'list': []}

    def playerContent(self, flag, id, vipFlags):
        headers = {
            'User-Agent': self.headers['User-Agent'],
            'Referer': f'{self.host}/',
            'Origin': self.host
        }

        if flag in ['aiovg', 'flow', '直连mp4']:
            return {'parse': 0, 'url': id, 'header': headers}

        # iframe 解析
        return {
            'parse': 1,
            'url': id,
            'header': headers,
            'sniff': 1,
            'sniff_include': ['.m3u8', '.mp4', '.ts']
        }

    def searchContent(self, key, quick, pg="1"):
        pg = int(pg)
        url = f'{self.host}/?s={key.replace(" ", "+")}'
        if pg > 1:
            url += f'&paged={pg}'

        html_text = self.fetch(url, headers=self.headers).text
        return {'list': self.parse_list(html_text)}