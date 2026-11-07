# coding=utf-8

import sys
sys.path.append('..')

from base.spider import Spider
import re
import json
import html as html_lib
from urllib.parse import quote, unquote, parse_qs, urlparse

class Spider(Spider):

    def getName(self):
        return "黑料论坛"

    def init(self, extend=""):
        self.host = "https://put.hllt2.life"
        self.tgGroup = ""
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36',
            'Referer': self.host + '/hllt/'
        }
        self.cateManual = {
            "亚洲情色": "20",
            "强奸乱伦": "21",
            "偷拍自拍": "22",
            "风骚寡妇": "23",
            "制服师生": "24",
            "欧美性爱": "25",
            "JAV高清": "26",
            "VR虚拟": "27",
            "无码视频": "28",
            "有码视频": "29",
            "国产视频": "30",
            "女同": "31",
            "动漫": "32",
            "三级伦理": "33"
        }

    # ===== 弱清洗 HTML 实体 =====
    def _unesc(self, s):
        try:
            return html_lib.unescape(s or "").strip()
        except Exception:
            return (s or "").strip()

    # ===== 正则取第一组 =====
    def _get_matched(self, pattern, text, default=""):
        try:
            m = re.search(pattern, text, re.DOTALL)
            if m:
                return m.group(1)
        except Exception:
            pass
        return default

    # ===== 清洗标题（站点条目存在"标题 title 标题"脏数据） =====
    def _clean_title(self, s):
        s = self._unesc(s)
        idx = s.find(' title')
        if idx > 0:
            s = s[:idx].strip()
        return s

    # ===== 构造分类/搜索 URL =====
    def get_cate_url(self, tid, pg=1, wd=None):
        if wd:
            return "%s/cn/home/web/index.php/vod/search.html?wd=%s" % (self.host, quote(wd))
        else:
            return "%s/cn/home/web/index.php/vod/type/id/%s/page/%d.html" % (self.host, tid, pg)

    # ===== 解析视频列表页（li 卡片块：a.cover 链接 + h3 标题 + img 图片） =====
    def parse_list(self, html):
        videos = []
        total_pages = 1

        # 分类分页 /vod/type/id/{tid}/page/{n}.html 与搜索分页 /vod/search/page/{n}/wd/xxx.html
        page_nums = [int(p) for p in re.findall(r'/vod/type/id/\d+/page/(\d+)\.html', html)]
        page_nums += [int(p) for p in re.findall(r'/vod/search/page/(\d+)/wd/', html)]
        if page_nums:
            total_pages = max(page_nums)

        # 每个条目是一个 <li> 块（分类页里是 <li><article>...，搜索页是 <li><a class="cover">...）
        blocks = re.findall(r'<li>(.*?)</li>', html, re.DOTALL)

        for block in blocks:
            try:
                href_m = re.search(r'<a[^>]*class="cover"[^>]*href="([^"]*\/vod\/play\/[^"]+)"', block)
                if not href_m:
                    continue
                href = href_m.group(1).replace('&amp;', '&')

                # 图片：懒加载 data-src 优先，其次 src
                pic = ""
                img_m = re.search(r'<img[^>]*?(?:data-src|src)="(https?://[^"]+)"', block)
                if img_m:
                    pic = img_m.group(1)

                # 标题：img[title] 属性优先，其次 h3 内文本
                title = self._get_matched(r'<img[^>]*title="([^"]*)"', block, "")
                if not title:
                    h3_text = self._get_matched(r'<h3>(.*?)</h3>', block, "")
                    if h3_text:
                        title = re.sub(r'<[^>]+>', '', h3_text)
                title = self._clean_title(title)
                if not title:
                    title = "高清精彩正片"

                vod_id = href if href.startswith('http') else self.host + href
                videos.append({
                    "vod_id": vod_id,
                    "vod_name": title,
                    "vod_pic": pic if pic else "https://img.meituan.net/video/ea1bb086d18160e6465a4ade60212d6b1150.ico",
                    "vod_remarks": "高清"
                })
            except Exception:
                continue

        return videos, total_pages

    # ===== 从播放页 player_data 提取真实 m3u8 =====
    def get_m3u8_by_html(self, html):
        m = re.search(r'player_data=(\{.*?\})\s*</script>', html, re.DOTALL)
        if not m:
            return ""
        try:
            data = json.loads(m.group(1))
            vod_url = data.get('url', '') or ''
            if vod_url.endswith('?300'):
                vod_url = vod_url[:-4]
            return vod_url
        except Exception:
            return ""

    # ===== 首页内容 =====
    def homeContent(self, filter=False):
        result = {}
        classes = []
        for k in self.cateManual:
            classes.append({"type_name": k, "type_id": self.cateManual[k]})
        result['class'] = classes
        result['filters'] = {}

        try:
            url = self.get_cate_url("20", 1)
            rsp = self.fetch(url, headers=self.headers)
            videos, _ = self.parse_list(rsp.text)
            result['list'] = videos
        except Exception:
            result['list'] = []
        return result

    def homeVideoContent(self):
        try:
            url = self.get_cate_url("20", 1)
            rsp = self.fetch(url, headers=self.headers)
            videos, _ = self.parse_list(rsp.text)
            return {'list': videos}
        except Exception:
            return {'list': []}

    # ===== 分类内容 =====
    def categoryContent(self, tid, pg, filter=False, extend=""):
        result = {}
        page = int(pg) if pg else 1

        try:
            url = self.get_cate_url(tid, page)
            rsp = self.fetch(url, headers=self.headers)
            videos, total_pages = self.parse_list(rsp.text)

            result['list'] = videos
            result['page'] = page
            result['pagecount'] = total_pages
            result['limit'] = len(videos)
            result['total'] = total_pages * max(len(videos), 1)
        except Exception:
            result['list'] = []
            result['page'] = page
            result['pagecount'] = 1
            result['limit'] = 0
            result['total'] = 0
        return result

    # ===== 详情内容：取播放页标题 + player_data 真实 m3u8 =====
    def detailContent(self, array):
        vod_id = array[0] if array else ""
        result = {}
        try:
            target_url = vod_id if vod_id.startswith('http') else self.host + vod_id
            rsp = self.fetch(target_url, headers=self.headers)
            html = rsp.text

            m3u8_url = self.get_m3u8_by_html(html)

            title = self._get_matched(r'<h1[^>]*>([^<]+)</h1>', html, "")
            title = self._clean_title(title)
            if not title:
                title = self._get_matched(r'<title>([^<]+)</title>', html, "")
                if title:
                    title = re.split(r'\s*[-_]\s*', title)[0].strip()
                    title = self._clean_title(title)
                else:
                    title = "高清精彩视频"

            vod_content = "【🔞 资源来自网络，仅供个人学习交流，请勿用于商业用途。】"

            play_from = "在线播放"
            play_url = title + "$" + m3u8_url

            vod = {
                "vod_id": vod_id,
                "vod_name": title,
                "vod_pic": "",
                "type_name": "高清专区",
                "vod_year": "2026",
                "vod_area": "内部",
                "vod_remarks": "正片",
                "vod_actor": "",
                "vod_director": "",
                "vod_content": vod_content,
                "vod_play_from": play_from,
                "vod_play_url": play_url
            }
            result['list'] = [vod]
        except Exception:
            result['list'] = []
        return result

    # ===== 搜索内容 =====
    def searchContent(self, key, quick, pg="1"):
        result = {}
        try:
            page = int(pg) if pg else 1
            if page > 1:
                url = "%s/cn/home/web/index.php/vod/search/page/%d/wd/%s.html" % (self.host, page, quote(key))
            else:
                url = "%s/cn/home/web/index.php/vod/search.html?wd=%s" % (self.host, quote(key))
            rsp = self.fetch(url, headers=self.headers)
            videos, total_pages = self.parse_list(rsp.text)
            result['list'] = videos
            result['page'] = page
            result['pagecount'] = total_pages
        except Exception:
            result['list'] = []
        return result

    # ===== 播放内容 =====
    def playerContent(self, flag, id, vipFlags):
        return {
            "parse": 0,
            "playUrl": "",
            "url": id,
            "header": {
                "User-Agent": self.headers['User-Agent'],
                "Referer": self.host + "/hllt/"
            }
        }

    def isVideoFormat(self, url):
        if re.search(r'\.(m3u8|mp4|flv|avi|mkv|rmvb|wmv)(\?|#|$)', url, re.IGNORECASE):
            return True
        return False

    def manualVideoCheck(self):
        return False

    def verifyLiveUrl(self, url):
        return url