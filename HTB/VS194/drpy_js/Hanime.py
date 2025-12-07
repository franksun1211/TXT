# -*- coding: utf-8 -*-
# by @嗷呜 & Perplexity (Updated 2025-12-03 修复时间时长筛选)
import json
import sys
import threading
import requests
import re
import time
import random
import html as html_parser
from urllib.parse import quote, unquote

sys.path.append('..')
from base.spider import Spider

class Spider(Spider):

    def init(self, extend=""):
        self.host = "https://down.nigx.cn/hanime1.me"
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Referer': f'{self.host}/',
            'Origin': self.host
        }

    def getName(self):
        return "Hanime"

    def isVideoFormat(self, url):
        return any(ext in (url or '') for ext in ['.m3u8', '.mp4', '.ts'])

    def manualVideoCheck(self):
        return False

    def destroy(self):
        pass

    def homeContent(self, filter):
        classes = [
            {'type_name': '最新上市', 'type_id': 'latest'},
            {'type_name': '裏番', 'type_id': '裏番'},
            {'type_name': '泡麵番', 'type_id': '泡麵番'},
            {'type_name': 'Motion Anime', 'type_id': 'Motion Anime'},
            {'type_name': '3D動畫', 'type_id': '3D動畫'},
            {'type_name': '同人作品', 'type_id': '同人作品'},
            {'type_name': 'MMD', 'type_id': 'MMD'},
            {'type_name': 'Cosplay', 'type_id': 'Cosplay'},
            {'type_name': '本日排行', 'type_id': 'daily_rank'},
            {'type_name': '本週排行', 'type_id': 'weekly_rank'},
            {'type_name': '本月排行', 'type_id': 'monthly_rank'}
        ]
        sort_filters = [
            {"n": "最新上市", "v": "最新上市"},
            {"n": "本日排行", "v": "本日排行"},
            {"n": "本週排行", "v": "本週排行"},
            {"n": "本月排行", "v": "本月排行"},
            {"n": "人氣爆棚", "v": "人氣爆棚"}
        ]
        date_filters = [
            {"n": "全部時間", "v": ""},
            {"n": "24小時", "v": "24"},
            {"n": "2天", "v": "2"},
            {"n": "1週", "v": "7"},
            {"n": "1月", "v": "30"},
            {"n": "3月", "v": "90"}
        ]
        duration_filters = [
            {"n": "全部時長", "v": ""},
            {"n": "1分鐘", "v": "1"},
            {"n": "5分鐘", "v": "5"},
            {"n": "10分鐘", "v": "10"},
            {"n": "20分鐘", "v": "20"},
            {"n": "30分鐘", "v": "30"},
            {"n": "60+分鐘", "v": "60"},
            {"n": "0-10分鐘", "v": "0-10"},
            {"n": "0-20分鐘", "v": "0-20"}
        ]
        filters = {}
        for item in classes:
            filters[item['type_id']] = [
                {"key": "sort", "name": "排序", "value": sort_filters},
                {"key": "date", "name": "時間", "value": date_filters},
                {"key": "duration", "name": "時長", "value": duration_filters}
            ]
        return {'class': classes, 'filters': filters}

    def homeVideoContent(self):
        try:
            url = f"{self.host}/search?sort=最新上市"
            content = self.fetch(url, headers=self.getheaders()).text
            vods = self.parse_vod_list(content)
            return {'list': vods}
        except Exception:
            return {'list': []}

    def categoryContent(self, tid, pg, filter, extend):
        page = int(pg)
        sort = extend.get('sort', '')
        date = extend.get('date', '')
        duration = extend.get('duration', '')

        # 参数映射，严格对应网页支持值
        valid_dates = {"": "", "24": "24", "2": "2", "7": "7", "30": "30", "90": "90"}
        valid_durations = {"": "", "1": "1", "5": "5", "10": "10", "20": "20", "30": "30", "60": "60", "0-10": "0-10", "0-20": "0-20"}

        date = valid_dates.get(date, "")
        duration = valid_durations.get(duration, "")

        if tid == 'latest':
            url = f"{self.host}/search?sort=最新上市&page={page}"
        elif tid == 'daily_rank':
            url = f"{self.host}/search?sort=本日排行&page={page}"
        elif tid == 'weekly_rank':
            url = f"{self.host}/search?sort=本週排行&page={page}"
        elif tid == 'monthly_rank':
            url = f"{self.host}/search?sort=本月排行&page={page}"
        else:
            param_list = [f"query={quote(tid)}", f"page={page}"]
            if sort:
                param_list.append(f"sort={quote(sort)}")
            if date:
                param_list.append(f"date={quote(date)}")
            if duration:
                param_list.append(f"duration={quote(duration)}")
            url = f"{self.host}/search?" + "&".join(param_list)

        try:
            content = self.fetch(url, headers=self.getheaders()).text
            vods = self.parse_vod_list(content)
            return {
                'list': vods,
                'page': page,
                'pagecount': page + 1 if len(vods) > 0 else page,
                'limit': 30,
                'total': 9999
            }
        except Exception:
            return {'list': []}

    def detailContent(self, ids):
        vid = ids[0]
        url = f"{self.host}/watch?v={vid}"

        try:
            html = self.fetch(url, headers=self.getheaders()).text

            title_match = re.search(r'<meta property="og:title" content="(.*?)"', html)
            title = title_match.group(1) if title_match else vid

            pic_match = re.search(r'<meta property="og:image" content="(.*?)"', html)
            pic = pic_match.group(1) if pic_match else ""

            desc_match = re.search(r'<meta property="og:description" content="(.*?)"', html)
            desc = desc_match.group(1) if desc_match else ""

            vod_tag_list = []
            rich_tags = []

            tag_matches = re.findall(r'<div class="single-video-tag"[^>]*>\s*<a[^>]*>(.*?)</a>', html, re.S)
            seen_tags = set()
            for inner_html in tag_matches:
                name = re.sub(r'<[^>]+>', '', inner_html).strip()
                name = re.sub(r'\([^)]*\)', '', name)
                name = re.sub(r'\s*\d+$', '', name).strip()
                if '#' in name:
                    name = name.split('#')[0].strip()
                name = html_parser.unescape(name).replace('&nbsp;', '').strip()
                if name and name not in seen_tags:
                    seen_tags.add(name)
                    vod_tag_list.append(name)
                    target = json.dumps({'id': name, 'name': name}, ensure_ascii=False)
                    rich_tags.append(f'[a=cr:{target}/]{name}[/a]')

            vod_tag_str = ",".join(vod_tag_list)
            vod_content = f"{desc}\n\n🏷️ 标签: {' '.join(rich_tags)}" if rich_tags else desc

            sources = re.findall(r'<source[^>]+src="([^"]+)"', html)
            if not sources:
                sources = re.findall(r'src="([^"]+\.mp4[^"]*)"', html)

            decoded_sources = [html_parser.unescape(s).replace('&amp;', '&') for s in sources]

            quality_map = {}
            for s in decoded_sources:
                if '4k' in s.lower() or '2160' in s:
                    quality_map.setdefault('4K', []).append(s)
                elif '2k' in s.lower() or '1440' in s:
                    quality_map.setdefault('2K', []).append(s)
                elif '1080' in s:
                    quality_map.setdefault('1080p', []).append(s)
                elif '720' in s:
                    quality_map.setdefault('720p', []).append(s)
                elif '480' in s:
                    quality_map.setdefault('480p', []).append(s)
                else:
                    quality_map.setdefault('标清', []).append(s)

            quality_order = ['4K', '2K', '1080p', '720p', '480p', '标清']
            play_parts = []
            for q in quality_order:
                if q in quality_map and quality_map[q]:
                    best_url = quality_map[q][0]
                    play_url_with_dm = f"{vid}_dm_{best_url}"
                    play_parts.append(f"{q}${play_url_with_dm}")

            if not play_parts:
                m3u8_match = re.search(r'source\s*=\s*[\'"](https?://[^\'"]+\.m3u8[^\'"]*)[\'"]', html)
                if m3u8_match:
                    best_url = m3u8_match.group(1).replace('&amp;', '&')
                    play_url_with_dm = f"{vid}_dm_{best_url}"
                    play_parts.append(f"自动${play_url_with_dm}")

            if not play_parts:
                play_parts.append(f"网页播放${url}")

            line_name = "书生玩剣ⁱ·*₁＇"
            vod_play_url = f"{line_name}$" + "#".join(play_parts)

            vod = {
                "vod_id": vid,
                "vod_name": title,
                "vod_pic": pic,
                "type_name": "",
                "vod_year": "",
                "vod_area": "",
                "vod_remarks": "",
                "vod_actor": "",
                "vod_director": "",
                "vod_content": vod_content,
                "vod_tag": vod_tag_str,
                "vod_play_from": line_name,
                "vod_play_url": vod_play_url
            }
            return {'list': [vod]}
        except Exception:
            return {'list': []}

    def searchContent(self, key, quick, pg="1", extend=None):
        page = int(pg)
        base_url = f"{self.host}/search?"
        param_list = [f"page={page}"]
        if key:
            param_list.append(f"query={quote(key)}")

        if extend:
            tags = extend.get("tags", [])
            if isinstance(tags, list) and tags:
                for t in tags:
                    param_list.append(f"tags[]={quote(t)}")
            sort = extend.get("sort", "")
            if sort:
                param_list.append(f"sort={quote(sort)}")
            date = extend.get("date", "")
            date_map = {"": "", "24": "24", "2": "2", "7": "7", "30": "30", "90": "90"}
            if date and date in date_map:
                param_list.append(f"date={date_map[date]}")
            duration = extend.get("duration", "")
            duration_map = {"": "", "1": "1", "5": "5", "10": "10", "20": "20", "30": "30", "60": "60"}
            if duration and duration in duration_map:
                param_list.append(f"duration={duration_map[duration]}")
            genre = extend.get("genre", "")
            if genre:
                param_list.append(f"genre={quote(genre)}")

        url = base_url + "&".join(param_list)
        try:
            html = self.fetch(url, headers=self.getheaders()).text
            vods = self.parse_vod_list(html)
            return {'list': vods, 'page': page}
        except Exception:
            return {'list': [], 'page': page}

    def playerContent(self, flag, id, vipFlags):
        header = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Referer': 'https://hanime1.me/',
            'Origin': 'https://hanime1.me'
        }
        if '_dm_' in id:
            vid, url = id.split('_dm_', 1)
            threading.Thread(target=self._preload_danmaku, args=(vid, url)).start()
        else:
            url = id

        if '.mp4' in url or '.m3u8' in url:
            return {'parse': 0, 'url': url, 'header': header}
        return {'parse': 1, 'url': url, 'header': header}

    def parse_vod_list(self, html):
        vods = []
        seen = set()
        parts = re.split(r'class="[^"]*search-doujin-videos[^"]*"', html)
        for i in range(1, len(parts)):
            block = parts[i][:4000]
            try:
                url_match = re.search(r'<a[^>]+href="([^"]+)"', block)
                if not url_match: continue
                url = url_match.group(1)
                id_match = re.search(r'v=(\d+)', url)
                if not id_match: continue
                vid = id_match.group(1)
                if vid in seen: continue
                seen.add(vid)

                title = vid
                title_match = re.search(r'class="[^"]*card-mobile-title[^"]*"[^>]*>(.*?)</div>', block)
                if title_match:
                    title = title_match.group(1).strip()

                pic = ""
                img_matches = re.findall(r'<img[^>]+src="([^"]+)"', block)
                found_thumb = False
                for img_src in img_matches:
                    if 'thumbnail' in img_src:
                        pic = img_src
                        found_thumb = True
                        break
                if not found_thumb and len(img_matches) > 1:
                    pic = img_matches[1]
                elif not found_thumb and len(img_matches) > 0:
                    pic = img_matches[0]

                remarks = ""
                dur_match = re.search(r'class="[^"]*card-mobile-duration[^"]*"[^>]*>(.*?)</div>', block)
                if dur_match:
                    remarks = dur_match.group(1).strip()

                vods.append({
                    "vod_id": vid,
                    "vod_name": title,
                    "vod_pic": pic,
                    "vod_remarks": remarks
                })
            except Exception:
                pass
        return vods

    # 弹幕处理保持不变
    def localProxy(self, param):
        try:
            xtype = param.get('type', '')
            if xtype == 'hlxdm':
                vid = param.get('path', '')
                times = int(param.get('times', 0))
                comments = self._fetch_comments(vid)
                return self._generate_danmaku_xml(comments, times)
            return [404, 'text/plain', b'']
        except Exception:
            return [500, 'text/plain', b'']

    def _fetch_comments(self, vid):
        comments = []
        try:
            url = f"{self.host}/loadComment?id={vid}&type=video&content=comment-tablink"
            res = requests.get(url, headers=self.getheaders(), timeout=5)
            data = res.json()
            comments_html = data.get('comments', '')
            if not comments_html:
                return ["欢迎观看", "Hanime1"]

            comment_blocks = re.findall(
                r'<div[^>]*class="comment-index-text"[^>]*>(?:[^<]|<[^>]*>)*?</div>\s*<div[^>]*class="comment-index-text"[^>]*>(.*?)</div>',
                comments_html, re.S
            )
            for block in comment_blocks:
                text = re.sub(r'<[^>]+>', '', block).strip()
                if len(text) > 2 and len(text) < 100 and not any(x in text for x in ['加载中', '查看', '回复', '登录', '发表']):
                    comments.append(text)
            seen = set()
            clean_comments = []
            for c in comments:
                if c not in seen and len(clean_comments) < 60:
                    seen.add(c)
                    clean_comments.append(c)
            return clean_comments if clean_comments else ["欢迎观看", "精彩内容"]
        except Exception:
            return ["欢迎观看", "Hanime1"]

    def _generate_danmaku_xml(self, comments, duration):
        if duration <= 0:
            duration = 600
        xml = ['<?xml version="1.0" encoding="UTF-8"?>', '<i>']
        xml.append('<d p="0,5,25,16711680,0">弹幕加载成功</d>')
        if not comments:
            xml.extend([
                '<d p="5,1,25,16777215,0">暂无评论，欢迎补充~</d>',
                '<d p="15,1,25,16777215,0">Hanime1 高清无码</d>'
            ])
        else:
            for i, c in enumerate(comments):
                progress = i / len(comments)
                base_time = progress * duration
                t = round(max(1, min(base_time + random.uniform(-5, 5), duration - 1)), 1)
                color = 16777215
                if random.random() < 0.15:
                    color = random.randint(0x666666, 0xFFFFFF)
                safe_text = html_parser.escape(c)
                xml.append(f'<d p="{t},1,25,{color},0">{safe_text}</d>')
        xml.append('</i>')
        return [200, 'text/xml', '\n'.join(xml)]

    def _preload_danmaku(self, vid, url):
        try:
            time.sleep(1)
            dm_url = f"{self.getProxyUrl()}&path={vid}&times=600&type=hlxdm"
            requests.get(f"http://127.0.0.1:9978/action?do=refresh&type=danmaku&path={quote(dm_url)}", timeout=2)
        except:
            pass

    def getheaders(self, param=None):
        return self.headers
