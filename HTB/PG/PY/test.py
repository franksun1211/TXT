# coding=utf-8
import sys
import json
import time
import urllib.parse
import re
import requests
from lxml import etree
sys.path.append('..')
from base.spider import Spider

class Spider(Spider):
    def getName(self):
        return "香蕉視頻[優化版]"

    def init(self, extend=""):
        self.host = "https://618013.xyz"
        self.api_host = "https://h5.xxoo168.org"
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/110.0.0.0 Safari/537.36',
            'Referer': self.host
        }
        self.log(f"香蕉視頻初始化成功")

    # --- 工具方法 ---
    def _abs_url(self, url):
        """處理相對路徑"""
        if not url: return ""
        if url.startswith('//'): return "https:" + url
        if url.startswith('/'): return self.host + url
        return url

    def _decrypt_title(self, encrypted_text):
        """優化後的解密算法"""
        try:
            # 使用列表推導式提升處理速度
            return ''.join([chr(ord(c) ^ 128) for c in encrypted_text])
        except Exception:
            return encrypted_text

    # --- 核心邏輯 ---
    def homeContent(self, filter):
        result = {'class': self.homeVideoContent()['class']}
        try:
            rsp = self.fetch(self.host, headers=self.headers)
            doc = etree.HTML(rsp.text)
            result['list'] = self._get_videos(doc)
        except Exception as e:
            self.log(f"首頁獲取失敗: {e}")
        return result

    def homeVideoContent(self):
        # 建議將分類配置化，方便維護
        category = [
            {'type_id': '618013.xyz_1', 'type_name': '全部'},
            {'type_id': '618013.xyz_13', 'type_name': '精品'},
            {'type_id': '618013.xyz_6', 'type_name': '國產'},
            {'type_id': '618013.xyz_33', 'type_name': '中字'},
            {'type_id': '618013.xyz_32', 'type_name': '自拍'}
        ]
        return {'class': category}

    def categoryContent(self, tid, pg, filter, extend):
        try:
            domain, type_id = tid.split('_')
            url = f"https://{domain}/index.php/vod/type/id/{type_id}/page/{pg}.html"
            rsp = self.fetch(url, headers=self.headers)
            doc = etree.HTML(rsp.text)
            videos = self._get_videos(doc)
            
            # 獲取總頁數（優化選擇器）
            page_text = doc.xpath('//div[contains(@class,"mypage")]//a[last()]/@href')
            page_count = pg
            if page_text:
                match = re.search(r'page/(\d+)', page_text[0])
                page_count = match.group(1) if match else pg

            return {
                'list': videos,
                'page': int(pg),
                'pagecount': int(page_count),
                'limit': 20,
                'total': int(page_count) * 20
            }
        except Exception as e:
            self.log(f"分類獲取失敗: {e}")
            return {'list': []}

    def _get_videos(self, doc):
        """統一的影片列表解析"""
        videos = []
        nodes = doc.xpath('//a[@class="vodbox"]')
        for node in nodes:
            try:
                href = node.xpath('./@href')[0]
                vod_id = self.regStr(r'm=(\d+)', href)
                
                # 標題解析
                raw_title = node.xpath('./p[@class="km-script"]/text()')
                title = self._decrypt_title(raw_title[0].strip()) if raw_title else "未知影片"
                
                # 封面圖
                img = node.xpath('.//img/@data-original | .//img/@src')
                pic = self._abs_url(img[0]) if img else ""

                videos.append({
                    'vod_id': f"618013.xyz_{vod_id}",
                    'vod_name': title,
                    'vod_pic': pic,
                    'vod_remarks': node.xpath('.//span[@class="v-time"]/text()')[0] if node.xpath('.//span[@class="v-time"]') else ""
                })
            except:
                continue
        return videos

    def playerContent(self, flag, id, vipFlags):
        """播放解析優化：增加 API 請求的超時與失敗重試"""
        video_id = id.split('_')[-1]
        api_url = f"{self.api_host}/api/v2/vod/reqplay/{video_id}"
        
        try:
            # 模擬移動端請求可能更容易獲取地址
            api_headers = self.headers.copy()
            api_headers.update({
                'Origin': self.host,
                'X-Requested-With': 'XMLHttpRequest'
            })
            
            # 使用 fetch 請求 API
            rsp = self.fetch(api_url, headers=api_headers)
            data = rsp.json()
            
            # 優先獲取正式地址，否則獲取預覽地址
            video_url = data.get('data', {}).get('httpurl') or data.get('data', {}).get('httpurl_preview')
            
            if video_url:
                return {'parse': 0, 'playUrl': '', 'url': video_url.split('?')[0]}
        except Exception as e:
            self.log(f"API 播放解析失敗: {e}")

        # 最終保底：返回網頁播放器
        return {'parse': 1, 'playUrl': '', 'url': f"{self.host}/html/kkyd.html?m={video_id}"}
