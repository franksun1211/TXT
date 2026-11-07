# coding=utf-8
# 黄果剧场 (huangguo.video) — TVBox drpy Python Spider
# 遮天法体系 · 道宫境界 · CF Managed Challenge 已突破
# 版本: v1.0 | 日期: 2026-09-16

import sys
sys.path.append('..')

from base.spider import Spider
import re
import json
import html as html_lib
from urllib.parse import quote

class Spider(Spider):

    def getName(self):
        return "黄果剧场"

    def init(self, extend=""):
        self.host = "https://huangguo.video"
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
            'Referer': self.host + '/'
        }

        self.cateManual = {
            "全部视频": "all",
            "MV/音乐剧": "1",
            "短片": "2",
            "连续剧": "3",
            "片段": "4"
        }

        # 尝试导入 curl_cffi 用于过 Cloudflare
        self.has_curl_cffi = False
        self.curl_session = None
        try:
            from curl_cffi import requests
            self.curl_session = requests.Session()
            self.has_curl_cffi = True
        except Exception:
            pass

    def fetch(self, url, headers=None, timeout=10):
        """
        重载基类 fetch：优先使用 curl_cffi chrome131 指纹过 CF，
        否则回退到基类 requests（依赖 TVBox 环境 UA 指纹）。
        """
        if headers is None:
            headers = self.headers

        if self.has_curl_cffi and self.curl_session:
            try:
                resp = self.curl_session.get(url, headers=headers, impersonate="chrome131", timeout=timeout)
                if resp.status_code == 200:
                    return resp
            except Exception:
                pass

        return super().fetch(url, headers=headers, timeout=timeout)

    # ===== 通用正则提取 =====
    def _get_matched(self, pattern, text, default=""):
        try:
            m = re.search(pattern, text, re.DOTALL)
            if m:
                return m.group(1)
        except Exception:
            pass
        return default

    def _unesc(self, s):
        try:
            return html_lib.unescape(s or "").strip()
        except Exception:
            return (s or "").strip()

    # ===== 获取分类页URL =====
    def get_cate_url(self, tid, pg=1):
        if tid == "all":
            return "%s/videos?page=%d" % (self.host, pg)
        return "%s/videos?category=%s&page=%d" % (self.host, tid, pg)

    def get_search_url(self, key, pg=1):
        return "%s/search?q=%s&page=%d" % (self.host, quote(key), pg)

    # ===== 解析视频卡片列表（首页/分类页通用） =====
    def _parse_cards(self, html):
        videos = []
        matches = re.findall(
            r'<a[^>]*href="(/(?:series|video)/[a-z0-9]+)"[^>]*>(.*?)</a>',
            html, re.DOTALL
        )

        seen = set()
        for href, body in matches:
            if href in seen:
                continue
            seen.add(href)

            try:
                # 标题: 优先 img alt
                title = ""
                alt_m = re.search(r'alt="([^"]*)"', body)
                if alt_m:
                    title = alt_m.group(1).strip()
                # 其次 vcard-title
                if not title:
                    title_m = re.search(r'class="vcard-title"[^>]*>([^<]+)', body)
                    if title_m:
                        title = title_m.group(1).strip()
                # 最后 p.truncate
                if not title:
                    title_m = re.search(r'class="[^"]*truncate[^"]*"[^>]*>([^<]+)', body)
                    if title_m:
                        title = title_m.group(1).strip()
                if not title:
                    title = "未命名"

                # 封面
                pic = ""
                pic_m = re.search(r'src="(/uploads/[^"]+)"', body)
                if pic_m:
                    pic = self.host + pic_m.group(1)
                if not pic:
                    pic_m = re.search(r'background-image:url\((/uploads/[^)]+)\)', body)
                    if pic_m:
                        pic = self.host + pic_m.group(1)

                # 备注 (评级 + 时长/集数)
                remarks = ""
                rating_m = re.search(r'rating-(safe|nude|explicit)[^>]*>([^<]+)</span>', body)
                if rating_m:
                    remarks = rating_m.group(2).strip()
                dur_m = re.search(r'>([^<]*(?:更新至第\d+集|\d+:\d+|分钟|小时)[^<]*)</span>', body)
                if dur_m:
                    dur = dur_m.group(1).strip()
                    if dur and dur not in ("限制级", "安全", "裸露"):
                        remarks = remarks + " · " + dur if remarks else dur

                vid = self.host + href

                videos.append({
                    "vod_id": vid,
                    "vod_name": title,
                    "vod_pic": pic,
                    "vod_remarks": remarks
                })
            except Exception:
                continue

        return videos

    # ===== 解析搜索页 =====
    def _parse_search(self, html):
        videos = []
        # 搜索页: <article class="search-content-card">...</article>
        articles = re.findall(
            r'<article class="search-content-card[^"]*"[^>]*>(.*?)</article>',
            html, re.DOTALL
        )
        for article in articles:
            try:
                a_m = re.search(
                    r'<a[^>]*href="(/(?:series|video)/[a-z0-9]+)"[^>]*>(.*?)</a>',
                    article, re.DOTALL
                )
                if not a_m:
                    continue
                href = a_m.group(1)
                body = a_m.group(2)

                # 标题: 优先 article 内 img alt
                title = ""
                img_alt_m = re.search(r'alt="([^"]*)"', article)
                if img_alt_m:
                    title = img_alt_m.group(1).strip()
                # 其次 a 内 p.truncate
                if not title:
                    title_m = re.search(r'class="truncate"[^>]*>([^<]+)', article)
                    if title_m:
                        title = title_m.group(1).strip()
                if not title:
                    title = "未命名"

                # 封面
                pic = ""
                pic_m = re.search(r'src="(/uploads/[^"]+)"', article)
                if pic_m:
                    pic = self.host + pic_m.group(1)

                # 备注
                remarks = ""
                rating_m = re.search(r'rating-(safe|nude|explicit)[^>]*>([^<]+)</span>', article)
                if rating_m:
                    remarks = rating_m.group(2).strip()
                dur_m = re.search(r'>([^<]*(?:更新至第\d+集|\d+:\d+)[^<]*)</span>', article)
                if dur_m:
                    dur = dur_m.group(1).strip()
                    if dur and dur not in ("限制级", "安全", "裸露"):
                        remarks = remarks + " · " + dur if remarks else dur

                vid = self.host + href

                videos.append({
                    "vod_id": vid,
                    "vod_name": title,
                    "vod_pic": pic,
                    "vod_remarks": remarks
                })
            except Exception:
                continue

        return videos

    # ===== 首页内容 =====
    def homeContent(self, filter=False):
        result = {}
        classes = []
        for k, v in self.cateManual.items():
            classes.append({"type_name": k, "type_id": v})
        result['class'] = classes

        try:
            rsp = self.fetch(self.host + "/", headers=self.headers)
            videos = self._parse_cards(rsp.text)
            result['list'] = videos
        except Exception:
            result['list'] = []

        return result

    def homeVideoContent(self):
        return self.homeContent(False)

    # ===== 分类内容 =====
    def categoryContent(self, tid, pg, filter=False, extend=""):
        result = {}
        page = int(pg) if pg else 1

        try:
            url = self.get_cate_url(tid, page)
            rsp = self.fetch(url, headers=self.headers)
            videos = self._parse_cards(rsp.text)

            # 计算总页数
            total_pages = 1
            m = re.search(r'page=(\d+)[^>]*class="[^"]*pager-page[^"]*is-edge', rsp.text)
            if m:
                total_pages = int(m.group(1))
            else:
                m = re.search(r'href="/videos\?[^"]*page=(\d+)[^"]*"[^>]*>\s*\d+\s*</a>\s*<a[^>]*>\s*下一页', rsp.text)
                if m:
                    total_pages = int(m.group(1))

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

    # ===== 详情内容 =====
    def detailContent(self, array):
        vod_id = array[0] if array else ""
        result = {}

        try:
            rsp = self.fetch(vod_id, headers=self.headers)
            html_text = rsp.text

            # 标题
            title = self._get_matched(r'<title>(.*?)\s*·\s*黄果剧场</title>', html_text, "未命名")

            # 封面
            pic = self._get_matched(r'<meta[^>]*property="og:image"[^>]*content="([^"]+)"', html_text, "")

            # 简介
            content = self._get_matched(r'<meta[^>]*property="og:description"[^>]*content="([^"]*)"', html_text, "")
            content = self._unesc(content)

            # 判断内容类型: 连续剧 / 单视频
            if '/series/' in vod_id:
                # 连续剧: 提取选集
                episodes = []
                ep_html = self._get_matched(
                    r'<div[^>]*data-episode-list[^>]*>(.*?)</div>\s*</section>',
                    html_text, ""
                )
                if ep_html:
                    ep_links = re.findall(
                        r'<a[^>]*href="(/video/[a-z0-9]+)"[^>]*>(.*?)</a>',
                        ep_html, re.DOTALL
                    )
                    for href, body in ep_links:
                        # 集名
                        ep_name = ""
                        alt_m = re.search(r'alt="([^"]*)"', body)
                        if alt_m:
                            ep_name = alt_m.group(1).strip()
                        if not ep_name:
                            p_m = re.search(r'<p[^>]*>([^<]+)</p>', body)
                            if p_m:
                                ep_name = p_m.group(1).strip()
                        if not ep_name:
                            ep_name = href.replace("/video/", "")

                        # 时长
                        dur_m = re.search(r'>([^<]*(?:\d+:\d+)[^<]*)</span>', body)
                        if dur_m:
                            dur = dur_m.group(1).strip()
                            ep_name = ep_name + " [" + dur + "]"

                        # 存储完整URL
                        full_url = self.host + href
                        episodes.append(ep_name + "$" + full_url)

                play_url = "#".join(episodes)
            else:
                # 单视频: 直接提取 m3u8
                m3u8 = self._get_matched(r'data-hls="([^"]+)"', html_text, "")
                if m3u8 and m3u8.startswith('/'):
                    m3u8 = self.host + m3u8

                play_url = title + "$" + m3u8 if m3u8 else ""

            vod = {
                "vod_id": vod_id,
                "vod_name": title,
                "vod_pic": pic,
                "vod_content": content,
                "vod_play_from": "黄果剧场",
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
            url = self.get_search_url(key, page)
            rsp = self.fetch(url, headers=self.headers)
            videos = self._parse_search(rsp.text)

            # 计算总页数
            total_pages = 1
            m = re.search(r'page=(\d+)[^>]*class="[^"]*pager-page[^"]*is-edge', rsp.text)
            if m:
                total_pages = int(m.group(1))

            result['list'] = videos
            result['page'] = page
            result['pagecount'] = total_pages
        except Exception:
            result['list'] = []

        return result

    def searchContentPage(self, key, quick, pg):
        return self.searchContent(key, quick, pg)

    # ===== 播放内容 =====
    def playerContent(self, flag, id, vipFlags):
        result = {"parse": 0, "playUrl": "", "url": id, "header": {}}

        # 如果 id 是 /video/xxx 的完整URL，需要二次请求获取 m3u8
        if id.startswith(self.host + "/video/"):
            try:
                rsp = self.fetch(id, headers=self.headers)
                m3u8 = self._get_matched(r'data-hls="([^"]+)"', rsp.text, "")
                if m3u8:
                    if m3u8.startswith('/'):
                        m3u8 = self.host + m3u8
                    result['url'] = m3u8
            except Exception:
                pass

        result['header'] = {
            "User-Agent": self.headers['User-Agent'],
            "Referer": self.host + "/"
        }
        return result

    # ===== 本地代理 =====
    def localProxy(self, param):
        return [200, "application/json", b'{"msg":"proxy not needed"}']

    def isVideoFormat(self, url):
        if re.search(r'\.(m3u8\d?)(\?|$)', url, re.IGNORECASE):
            return True
        return False

    def manualVideoCheck(self):
        return False
