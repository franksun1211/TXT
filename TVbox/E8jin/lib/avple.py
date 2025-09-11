#!/usr/bin/python
# -*- coding: utf-8 -*-
import requests
import re
import json
import urllib.parse


class Spider:
    def __init__(self):
        self.host = "https://avple.tv"
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }

    def log(self, msg):
        print(msg)

    def fetch(self, url, headers=None):
        return requests.get(url, headers=headers or self.headers, timeout=10)

    def homeContent(self):
        """抓首頁影片"""
        try:
            rsp = self.fetch(self.host)
            html = rsp.text
            videos = self._getVideos(html)
            return videos
        except Exception as e:
            self.log(f"首頁錯誤: {e}")
            return []

    def searchContent(self, key, pg="1"):
        """搜尋影片"""
        try:
            search_url = f"{self.host}/search/{urllib.parse.quote(key)}/{pg}"
            self.log(f"搜尋URL: {search_url}")
            rsp = self.fetch(search_url)
            html = rsp.text
            videos = self._getVideos(html)
            return videos
        except Exception as e:
            self.log(f"搜尋錯誤: {e}")
            return []

    def detailContent(self, vid):
        """抓影片詳情"""
        try:
            detail_url = f"{self.host}/video/{vid}"
            self.log(f"詳情URL: {detail_url}")
            rsp = self.fetch(detail_url)
            html = rsp.text
            title = self._regStr(r'<h1 class="text-white text-lg font-bold">([^<]+)</h1>', html)
            desc = self._regStr(r'<div id="detail-desc"[^>]*>([\s\S]*?)</div>', html)
            return {
                "vod_id": vid,
                "vod_name": title or f"影片 {vid}",
                "vod_content": desc or "暫無簡介"
            }
        except Exception as e:
            self.log(f"詳情錯誤: {e}")
            return {}

    def _getVideos(self, html):
        """解析影片清單"""
        videos = []
        try:
            # 先試 JSON-LD
            json_ld_match = re.search(r'<script type="application/ld\+json"[^>]*>(.*?)</script>', html, re.DOTALL)
            if json_ld_match:
                try:
                    data = json.loads(json_ld_match.group(1).strip())
                    if '@graph' in data:
                        for item in data['@graph']:
                            if item.get('@type') == 'ListItem':
                                vid = item['item'].split("/")[-1]
                                title = item.get('name', '未知')
                                videos.append({"vod_id": vid, "vod_name": title})
                except Exception as e:
                    self.log(f"JSON-LD 解析錯誤: {e}")

            # fallback 正則
            if not videos:
                self.log("⚠️ JSON-LD 沒抓到，改用正則解析...")
                item_pattern = r'<a[^>]+href="/video/(\d+)"[^>]*>.*?<h2[^>]*?>(.*?)</h2>'
                items = re.findall(item_pattern, html, re.DOTALL)
                for vid, title in items:
                    videos.append({"vod_id": vid, "vod_name": title.strip()})
        except Exception as e:
            self.log(f"_getVideos 出錯: {e}")
        return videos

    def _regStr(self, pattern, string):
        """正則取第一個匹配"""
        try:
            match = re.search(pattern, string, re.DOTALL)
            return match.group(1).strip() if match else ""
        except Exception:
            return ""


if __name__ == "__main__":
    spider = Spider()

    print("=== 測試首頁 ===")
    home = spider.homeContent()
    print("抓到首頁影片數:", len(home))
    print(home[:5])

    print("\n=== 測試搜尋 (麻豆) ===")
    search = spider.searchContent("麻豆")
    print("抓到搜尋影片數:", len(search))
    print(search[:5])

    if home:
        print("\n=== 測試詳情 (取首頁第一個影片) ===")
        vid = home[0]["vod_id"]
        detail = spider.detailContent(vid)
        print(detail)            limit = 24
            total = 999999

            if not videos and pg_int == 1:
                self.log(f"警告: 分类ID {tid}, 页码 {pg} 未找到任何视频。URL: {url}")
            elif not videos:
                self.log(f"信息: 分类ID {tid}, 页码 {pg} 没有更多视频。URL: {url}")

            return {
                'list': videos,
                'page': pg_int,
                'pagecount': pagecount,
                'limit': limit,
                'total': total
            }
        except Exception as e:
            self.log(f"分类内容获取出错 (tid={tid}, pg={pg}): {str(e)}")
            return {'list': []}

    def searchContent(self, key, quick, pg="1"):
        """搜索功能"""
        try:
            search_url = f"{self.host}/search/{urllib.parse.quote(key)}/{pg}"
            self.log(f"搜索URL: {search_url}")

            rsp = self.fetch(search_url, headers=self.headers)
            html = rsp.text
            videos = self._getVideos(html)

            return {'list': videos}
        except Exception as e:
            self.log(f"搜索出错: {str(e)}")
            return {'list': []}

    def detailContent(self, ids):
        """详情页面"""
        try:
            vid = ids[0]
            detail_url = f"{self.host}/video/{vid}"
            self.log(f"详情URL: {detail_url}")
            rsp = self.fetch(detail_url, headers=self.headers)
            html = rsp.text
            video_info = self._getDetail(html, vid)
            return {'list': [video_info]} if video_info else {'list': []}
        except Exception as e:
            self.log(f"详情获取出错 (vid: {ids[0]}): {str(e)}")
            return {'list': []}

    def playerContent(self, flag, id, vipFlags):
        """播放链接解析"""
        try:
            video_url = f"https://s4.avple.tv/video/{id}"
            self.log(f"✅ 找到视频直链: {video_url}")
            return {
                'parse': 0,
                'playUrl': '',
                'url': video_url,
                'header': json.dumps(self.headers)
            }
        except Exception as e:
            self.log(f"播放链接获取出错 (id: {id}): {str(e)}")
            return {'parse': 1, 'playUrl': '', 'url': f"{self.host}/video/{id}"}

    def _getCategories(self):
        """从首页提取分类"""
        categories = []
        try:
            categories.append({'type_id': 'trending', 'type_name': '近期热门'})
            categories.append({'type_id': 'latest', 'type_name': '最新影片'})

            rsp = self.fetch(self.host, headers=self.headers)
            html = rsp.text

            category_pattern = r'<a\s+[^>]*?href="(/tags/(\d+)/1/date)"[^>]*>([^<]+?)</a>'
            matches = re.findall(category_pattern, html, re.IGNORECASE)

            for _, cat_id, cat_name in matches:
                if not any(c['type_id'] == cat_id for c in categories):
                    categories.append({'type_id': cat_id, 'type_name': cat_name.strip()})
            
            if len(categories) < 5:
                self.log("动态获取分类失败或分类过少，使用硬编码分类。")
                hardcoded_categories = [
                    {'type_id': '121', 'type_name': '麻豆傳媒'},
                    {'type_id': '123', 'type_name': '果凍傳媒'},
                    {'type_id': '124', 'type_name': '皇家華人'},
                    {'type_id': '125', 'type_name': '精東影業'},
                    {'type_id': '126', 'type_name': '天美傳媒'},
                    {'type_id': '127', 'type_name': '星空無限傳媒'},
                    {'type_id': '128', 'type_name': '樂播傳媒'},
                    {'type_id': '129', 'type_name': '蜜桃傳媒'},
                    {'type_id': '130', 'type_name': '烏鴉傳媒'},
                    {'type_id': '131', 'type_name': '國產自拍'},
                    {'type_id': '122', 'type_name': 'SWAG'},
                    {'type_id': '135', 'type_name': 'FC2PPV'},
                    {'type_id': '15', 'type_name': '黑絲'},
                    {'type_id': '113', 'type_name': '旗袍'},
                    {'type_id': '49', 'type_name': '校服'},
                    {'type_id': '13', 'type_name': '絲襪'},
                    {'type_id': '97', 'type_name': '女僕'},
                    {'type_id': '76', 'type_name': '吊帶襪'},
                    {'type_id': '87', 'type_name': '兔女郎'},
                    {'type_id': '1', 'type_name': '巨乳'},
                    {'type_id': '63', 'type_name': '貧乳'},
                    {'type_id': '79', 'type_name': '露出'},
                    {'type_id': '2', 'type_name': '中出'},
                    {'type_id': '32', 'type_name': '顏射'},
                    {'type_id': '18', 'type_name': '潮吹'},
                    {'type_id': '53', 'type_name': '綑綁'},
                    {'type_id': '33', 'type_name': '多P'}
                ]
                for hc in hardcoded_categories:
                    if not any(c['type_id'] == hc['type_id'] for c in categories):
                        categories.append(hc)

            return categories

        except Exception as e:
            self.log(f"获取分类出错: {str(e)}")
            return [{'type_id': 'trending', 'type_name': '近期热门'}, {'type_id': 'latest', 'type_name': '最新影片'}]

    def _getVideos(self, html):
        """从HTML中提取视频列表"""
        videos = []
        try:
            # 尝试匹配首页的JSON-LD结构（最稳定）
            json_ld_match = re.search(r'<script type="application/ld\+json" data-next-head="">(.*?)<\/script>', html, re.DOTALL)
            if json_ld_match:
                json_content = json_ld_match.group(1).strip()
                data = json.loads(json_content)
                if '@graph' in data:
                    for item in data['@graph']:
                        if item.get('@type') == 'ListItem':
                            video_id = item.get('item', '').split('/')[-1]
                            video_name = item.get('name', '未知视频')
                            thumb_url = f"https://avple-poster.b-cdn.net/video/{video_id}.png"
                            video = {
                                'vod_id': video_id,
                                'vod_name': video_name.strip(),
                                'vod_pic': thumb_url,
                                'vod_remarks': video_name.split('] ')[0].replace('[', '') if ']' in video_name else ''
                            }
                            videos.append(video)
            
            # 如果JSON-LD解析失败或没有找到数据，则尝试匹配分类页的HTML结构
            if not videos:
                self.log("未从JSON-LD中解析到视频，尝试备用HTML匹配...")
                # 匹配新的HTML结构：每个视频项在一个<a>标签里
                item_pattern = r'<a class="group[^>]*href="/video/(\d+)"[^>]*>.*?data-src="([^"]+)".*?<h2[^>]*?>(.*?)</h2>.*?<p[^>]*?class="text-xs[^"]*?">(.*?)</p>'
                items = re.findall(item_pattern, html, re.DOTALL | re.IGNORECASE)

                for vid, img_url, title, remark in items:
                    # 清理标题和备注中的HTML标签
                    title = re.sub(r'<[^>]+>', '', title).strip()
                    remark = re.sub(r'<[^>]+>', '', remark).strip()
                    img_url = img_url.strip()

                    # 确保图片URL是完整的
                    if not img_url.startswith('http'):
                        img_url = self.host + img_url

                    video = {
                        'vod_id': vid,
                        'vod_name': title if title else f"视频 {vid}",
                        'vod_pic': img_url,
                        'vod_remarks': remark
                    }
                    videos.append(video)
        
        except Exception as e:
            self.log(f"解析视频列表出错: {str(e)}")
        
        return videos

    def _getDetail(self, html, vid):
        """获取详情信息"""
        try:
            title = self.regStr(r'<h1 class="text-white text-lg font-bold">([^<]+)</h1>', html)
            pic = self.regStr(r'data-src="(https://avple-poster\.b-cdn\.net/video/[^"]+\.png)"', html)
            desc_element = re.search(r'<div id="detail-desc" class="break-words[^>]*">([\s\S]*?)</div>', html)
            desc = ""
            if desc_element:
                desc = desc_element.group(1).strip().replace('<br>', '\n').replace('</br>', '')
            else:
                desc = title if title else "暂无简介"
            actor = self.regStr(r'<span>演員:</span>\s*([^<]+)', html)
            director = ""
            play_from = ["默认源"]
            play_url_list = [f"第1集${vid}"]
            type_name_match = re.search(r'<div class="text-white">[^>]*<a href="/tags/(\d+)/\d+/date">([^<]+)</a>', html)
            type_name = type_name_match.group(2).strip() if type_name_match else "未知"
            return {
                'vod_id': vid,
                'vod_name': title if title else f"视频ID: {vid}",
                'vod_pic': pic if pic else "",
                'type_name': type_name,
                'vod_year': "未知",
                'vod_area': "未知",
                'vod_remarks': "高清",
                'vod_actor': actor if actor else "未知",
                'vod_director': director,
                'vod_content': desc,
                'vod_play_from': '$$$'.join(play_from),
                'vod_play_url': '$$$'.join(play_url_list)
            }
        except Exception as e:
            self.log(f"获取详情失败 (vid={vid}): {str(e)}")
            return {
                'vod_id': vid,
                'vod_name': "加载失败",
                'vod_pic': "",
                'type_name': "未知",
                'vod_year': "",
                'vod_area': "",
                'vod_remarks': "",
                'vod_actor': "",
                'vod_director': "",
                'vod_content': "详情加载失败",
                'vod_play_from': "默认源",
                'vod_play_url': f"第1集${vid}"
            }
    def regStr(self, pattern, string):
        """正则提取第一个匹配组，并处理空值"""
        try:
            match = re.search(pattern, string, re.DOTALL)
            return match.group(1).strip() if match and match.group(1) else ""
        except Exception as e:
            self.log(f"正则提取出错 (pattern: {pattern}): {str(e)}")
            return ""

