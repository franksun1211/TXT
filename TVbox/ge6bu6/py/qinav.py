# 🌿尼🐴出品仅供参考

# -*- coding: utf-8 -*-
# 适配站点: https://www.qinav.com
# 分类：动态解析分类页面，一级分类 + 二级筛选下拉（模仿蜜桃）
# 播放：直链提取 + iframe 二次解析 + m3u8 广告清洗（无 AES 解密）
import sys
import re
import json
import base64
import requests
import urllib3
import time
import random
from urllib.parse import unquote, quote, urljoin, urlparse

urllib3.disable_warnings()
sys.path.append('..')
from base.spider import Spider as BaseSpider


class Spider(BaseSpider):
    session = requests.Session()
    host = 'https://www.qinav.com'
    _debug = True
    _category_cache = None

    def _log(self, msg):
        if self._debug:
            print(f'[qinav] {msg}')

    def getName(self):
        return 'qinav'

    def isVideoFormat(self, url):
        if not url:
            return False
        return '.m3u8' in url or '.mp4' in url or '.ts' in url

    def manualVideoCheck(self):
        return False

    def destroy(self):
        pass

    # ---------- 本地代理：清洗 m3u8 ----------
    def localProxy(self, param):
        """处理 type=m3u8 的代理请求，返回清洗后的 m3u8"""
        try:
            if not isinstance(param, dict):
                param = {}
            ptype = param.get('type') or param.get('action') or param.get('do')
            url = param.get('url', '')
            if ptype != 'm3u8' or not url:
                return [404, "text/plain", "not found"]
            referer = param.get('referer', '') or self.host
            if isinstance(url, list):
                url = url[0]
            if isinstance(referer, list):
                referer = referer[0]
            url = unquote(url)
            referer = unquote(referer)
            raw_m3u8 = self._get_m3u8_content(url, referer)
            if not raw_m3u8:
                return [404, "text/plain", "m3u8 download failed"]
            cleaned = self._clean_m3u8(raw_m3u8, url, referer)
            return [200, "application/vnd.apple.mpegurl", cleaned]
        except Exception as e:
            self._log(f'localProxy error: {e}')
            return [404, "text/plain", "proxy error"]

    # ---------- 初始化 ----------
    def init(self, extend=''):
        self.session.verify = False
        self.session.headers.update(self._get_headers())
        try:
            self.session.get(self.host, timeout=10)
        except:
            pass

    def _get_headers(self, referer=None):
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'zh-CN,zh;q=0.9',
            'Accept-Encoding': 'gzip, deflate',
            'Connection': 'keep-alive',
            'Referer': referer or self.host + '/',
        }
        return headers

    def _fetch(self, url, referer=None, retries=3):
        for i in range(retries):
            try:
                if referer is None:
                    referer = self.host + '/'
                headers = self._get_headers(referer)
                if i > 0:
                    time.sleep(random.uniform(1.0, 2.5))
                r = self.session.get(url, headers=headers, timeout=30, verify=False, allow_redirects=True)
                r.encoding = 'utf-8'
                if r.status_code == 200:
                    return r.text
                elif r.status_code in [403, 429, 503]:
                    self._log(f'请求被拦截 [{r.status_code}]，重试 {i+1}/{retries}')
                else:
                    self._log(f'状态码 {r.status_code}，可能无内容')
                    return ''
            except Exception as e:
                self._log(f'请求异常 [{e}]，重试 {i+1}/{retries}')
        return ''

    @staticmethod
    def _decode_b64(encoded_str):
        try:
            raw = base64.b64decode(encoded_str)
            return unquote(raw.decode('utf-8'))
        except:
            return encoded_str

    # ==================== 分类相关（动态解析，同前） ====================
    def _fetch_category_data(self):
        if self._category_cache is not None:
            return self._category_cache
        url = f'{self.host}/site.html'
        html = self._fetch(url)
        if not html:
            self._log('获取分类页面失败，使用硬编码备用')
            return self._get_fallback_categories()
        categories = []
        pattern = r'<h3>(.*?)</h3>\s*<div class="word">(.*?)</div>'
        matches = re.findall(pattern, html, re.S)
        if not matches:
            self._log('未找到分类结构，使用备用')
            return self._get_fallback_categories()
        for site_name, word_html in matches:
            site_name = site_name.strip()
            sub_links = re.findall(r'<a href="/site/(\d+)/(\d+)\.html">([^<]+)</a>', word_html)
            if not sub_links:
                continue
            sub_list = []
            for sid, cid, cname in sub_links:
                cname = cname.strip()
                if not cname or cname == '0':
                    continue
                sub_list.append((sid, cid, cname))
            if sub_list:
                categories.append({
                    'site_name': site_name,
                    'site_id': sub_list[0][0],
                    'subs': [(cid, cname) for _, cid, cname in sub_list]
                })
        if not categories:
            self._log('解析分类失败，使用备用')
            return self._get_fallback_categories()
        self._category_cache = categories
        self._log(f'动态解析到 {len(categories)} 个一级分类')
        return categories

    def _get_fallback_categories(self):
        fallback = [
            {'site_name': '视频1站', 'site_id': '1', 'subs': [
                ('42', '大秀视频'), ('4', '国产精品'), ('27', '自拍偷拍'), ('28', 'AV明星'),
                ('20', '动漫精品'), ('23', '日韩精品'), ('3', '欧美精品'), ('41', '教师学生'),
                ('18', '中文字幕'), ('36', '巨乳系列'), ('32', '3P合辑'), ('16', '人妻系列'),
                ('25', '制服诱惑'), ('2', '强奸乱伦'), ('40', 'SM重味'), ('1', '日韩无码'),
                ('22', '伦理影片')
            ]},
            {'site_name': '视频2站', 'site_id': '2', 'subs': [
                ('3', '日韩无码'), ('4', 'AV明星'), ('34', '童颜巨乳'), ('1', '国产自拍'),
                ('26', '强奸乱伦'), ('2', '欧美极品'), ('35', '高潮喷吹'), ('24', '重咸口味'),
                ('21', '动漫精品'), ('22', '极骚萝莉'), ('20', '中文字幕'), ('37', '绝美少女'),
                ('36', '激情口交')
            ]},
            {'site_name': '视频3站', 'site_id': '3', 'subs': [
                ('74', '少妇人妻'), ('52', '网曝黑料'), ('47', '国产视频'), ('48', '国产传媒'),
                ('67', '明星爆料'), ('71', '日本无码'), ('63', '制服诱惑'), ('82', '精品短视频'),
                ('49', '国产探花'), ('53', '日本有码'), ('61', '偷拍自拍'), ('64', '欧美精品'),
                ('75', '角色扮演'), ('56', '校园春色'), ('54', '主播大秀'), ('68', '三级伦理'),
                ('80', '男同性恋'), ('51', '野战户外'), ('70', '成人动漫'), ('78', '中文字幕'),
                ('57', 'SM虐待'), ('81', '反差系列'), ('72', '重口猎奇'), ('66', 'AV女优'),
                ('59', '巨乳系列'), ('76', '女同性恋'), ('69', 'AV解说'), ('77', '黑人专区'),
                ('73', 'VR视频'), ('62', '强奸乱伦'), ('50', '极品学生')
            ]},
            {'site_name': '视频4站', 'site_id': '4', 'subs': [
                ('2', '亚洲有码'), ('24', '国产自拍'), ('20', '美女主播'), ('49', 'AV解说'),
                ('1', '亚洲无码'), ('3', '欧美情色'), ('5', '动漫卡通')
            ]},
            {'site_name': '视频5站', 'site_id': '5', 'subs': [
                ('2', '国产主播'), ('9', '中文字幕'), ('5', '欧美性爱'), ('4', '无码专区'),
                ('1', '亚洲情色'), ('12', '卡通动画'), ('14', '少女萝莉'), ('3', '国产自拍'),
                ('15', '重口色情'), ('10', '制服诱惑'), ('7', '强奸乱伦'), ('13', '视频伦理'),
                ('8', '巨乳美乳'), ('33', '福利姬'), ('11', '女同性恋'), ('6', '熟女人妻')
            ]},
            {'site_name': '视频6站', 'site_id': '6', 'subs': [
                ('1', '国产情色'), ('20', '中文字幕'), ('23', '欧美情色'), ('31', '精品推荐'),
                ('2', '日本无码'), ('22', '成人动漫'), ('25', '长腿丝袜'), ('21', '网红主播'),
                ('27', '韩国伦理'), ('28', '香港伦理'), ('26', '邻家人妻'), ('3', 'AV明星'),
                ('24', '国模私拍'), ('35', 'AV明星1')
            ]},
            {'site_name': '视频7站', 'site_id': '7', 'subs': [
                ('25', '亚洲有码'), ('27', '巨乳美乳'), ('28', '人妻熟女'), ('51', '女优系列'),
                ('63', '恋腿狂魔'), ('31', '萝莉少女'), ('37', '日本精品'), ('36', '口交颜射'),
                ('35', '制服丝袜'), ('29', '强奸乱伦'), ('53', '风情旗袍'), ('44', '欺辱凌辱'),
                ('32', '伦理三级'), ('39', '素人自拍'), ('47', '91探花'), ('23', '主播直播'),
                ('58', '网曝门'), ('34', '自拍偷拍'), ('38', 'Cosplay'), ('41', '韩国御姐'),
                ('50', '古装扮演'), ('48', '网红流出'), ('46', '多人多P'), ('26', '中文字幕'),
                ('55', '瑜伽裤'), ('49', '野外露出'), ('22', '国产色情'), ('20', '精品推荐'),
                ('33', '成人动漫'), ('30', '欧美精品'), ('52', '可爱学生'), ('54', '兽耳系列'),
                ('40', '台湾辣妹'), ('45', '剧情介绍'), ('24', '亚洲无码'), ('42', '唯美港姐'),
                ('56', '闷骚护士'), ('43', '东南亚AV'), ('60', '女同性恋'), ('61', '男同性恋')
            ]},
            {'site_name': '视频8站', 'site_id': '8', 'subs': [
                ('16', '强奸乱伦'), ('6', '中文字幕'), ('38', '女优明星'), ('36', '网爆黑料'),
                ('3', '制服诱惑'), ('35', '网红头条'), ('40', 'AV解说'), ('5', 'AI换脸'),
                ('7', '卡通动漫'), ('9', '美女主播'), ('10', '国产自拍'), ('33', '抖音视频'),
                ('37', '欧美无码'), ('12', '萝莉少女'), ('1', '无码专区'), ('2', '麻豆传媒'),
                ('8', '欧美系列'), ('14', '多人群交'), ('11', '熟女人妻'), ('15', '美乳巨乳'),
                ('39', 'SM调教'), ('4', '三级伦理'), ('34', '韩国主播'), ('13', '女同性爱')
            ]}
        ]
        result = []
        for item in fallback:
            result.append({
                'site_name': item['site_name'],
                'site_id': item['site_id'],
                'subs': item['subs']
            })
        return result

    # ==================== 首页 ====================
    def homeContent(self, filter):
        try:
            categories = self._fetch_category_data()
            classes = []
            filters = {}
            for cat in categories:
                cid = f'site_{cat["site_id"]}'
                classes.append({'type_id': cid, 'type_name': cat['site_name']})
                sub_values = [{'n': '全部', 'v': ''}]
                for sub_id, sub_name in cat['subs']:
                    sub_values.append({'n': sub_name, 'v': f'{cat["site_id"]}_{sub_id}'})
                filters[cid] = [{'key': 'sub', 'name': '分类', 'value': sub_values}]

            home_list = []
            if categories:
                first_cat = categories[0]
                if first_cat['subs']:
                    first_sub = first_cat['subs'][0]
                    home_list = self._get_video_list(first_cat['site_id'], first_sub[0], 1)
            self._log(f'首页返回 {len(classes)} 个一级分类, 推荐视频 {len(home_list)} 个')
            return {
                'class': classes,
                'filters': filters,
                'type': '影视',
                'list': home_list,
                'page': 1,
                'pagecount': 1,
                'limit': len(home_list),
                'total': len(home_list)
            }
        except Exception as e:
            self._log(f'homeContent 异常: {e}')
            return {'class': [], 'filters': {}, 'type': '影视', 'list': [], 'page': 1, 'pagecount': 1, 'limit': 0, 'total': 0}

    def homeVideoContent(self):
        return {'list': []}

    def categoryContent(self, tid, pg, filter, extend):
        try:
            page = int(pg) if pg else 1
            if isinstance(extend, str):
                try:
                    extend = json.loads(extend)
                except:
                    extend = {}
            if not isinstance(extend, dict):
                extend = {}
            if str(tid).startswith('site_'):
                site_id = str(tid).replace('site_', '')
                sub_val = extend.get('sub', filter.get('sub', '')) if isinstance(filter, dict) else extend.get('sub', '')
                if sub_val and '_' not in str(sub_val):
                    cats = self._fetch_category_data()
                    for cat in cats:
                        if str(cat['site_id']) == str(sub_val) and cat['subs']:
                            sub_val = f"{sub_val}_{cat['subs'][0][0]}"
                            break
                if not sub_val:
                    cats = self._fetch_category_data()
                    for cat in cats:
                        if cat['site_id'] == site_id and cat['subs']:
                            sub_val = f"{site_id}_{cat['subs'][0][0]}"
                            break
                if sub_val and '_' in sub_val:
                    sid, catid = sub_val.split('_')
                    return self._load_sub_videos(sid, catid, page)
                else:
                    return {'list': [], 'page': page, 'pagecount': 1, 'limit': 0, 'total': 0}
            if '_' in str(tid) and not str(tid).startswith('site_'):
                parts = str(tid).split('_')
                if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
                    return self._load_sub_videos(parts[0], parts[1], page)
            return {'list': [], 'page': page, 'pagecount': 1, 'limit': 0, 'total': 0}
        except Exception as e:
            self._log(f'categoryContent 异常: {e}')
            return {'list': [], 'page': int(pg) if pg else 1, 'pagecount': 1, 'limit': 0, 'total': 0}

    def _load_sub_videos(self, site_id, cat_id, page):
        items = self._get_video_list(site_id, cat_id, page)
        total_page = page + 1
        url = f'{self.host}/site/{site_id}/{cat_id}.html'
        html = self._fetch(url)
        if html:
            pages = re.findall(r'[?&]page=(\d+)', html)
            if pages:
                total_page = max(int(p) for p in pages) + 1
        return {
            'list': items,
            'page': page,
            'pagecount': total_page,
            'limit': len(items),
            'total': total_page * len(items)
        }

    def _parse_list(self, html):
        items = []
        for a in re.finditer(r'<a[^>]+title="[^"]*"\s+href="(/video/(\d+)\.html)"[^>]*>.*?<li class="title">(.*?)</li>', html, re.S):
            vid = a.group(2)
            title = re.sub(r'<[^>]+>', '', a.group(3)).strip()
            img = re.search(r'img="([^"]+)"', a.group(0)) or re.search(r'data-src="([^"]+)"', a.group(0)) or re.search(r'src="([^"]+)"', a.group(0))
            pic = img.group(1) if img else ''
            items.append({'vod_id': vid, 'vod_name': title, 'vod_pic': pic, 'vod_remarks': ''})
        return items

    def _get_video_list(self, site_id, cat_id, page):
        urls = [
            f'{self.host}/site/{site_id}/{cat_id}.html',
            f'{self.host}/list/{site_id}_{cat_id}.html',
            f'{self.host}/category/{cat_id}.html?site_id={site_id}',
            f'{self.host}/index.php?c=site&a=index&site_id={site_id}&cat_id={cat_id}',
            f'{self.host}/videolist/{site_id}/{cat_id}.html',
        ]
        for url in urls:
            if page > 1:
                sep = '&' if '?' in url else '?'
                url += f'{sep}page={page}'
            html = self._fetch(url)
            if html:
                items = self._parse_list(html)
                if items:
                    return items
        return []

    # ==================== 详情解析（同前，无 AES） ====================
    def _fetch_detail(self, vid):
        for url in [f'{self.host}/video/{vid}.html', f'{self.host}/v/{vid}.html']:
            html = self._fetch(url, referer=self.host)
            if html and ('video' in html or 'play' in html or 'm3u8' in html or 'mp4' in html or 'iframe' in html):
                detail = self._parse_detail(html, vid, url)
                if detail and detail.get('vod_play_url'):
                    return detail
        return None

    def _parse_detail(self, html, vid, base_url):
        title = ''
        m = re.search(r'<h1[^>]*>(.*?)</h1>', html, re.S)
        if m: title = re.sub(r'<[^>]+>', '', m.group(1)).strip()
        if not title:
            m = re.search(r'<title>([^<]+)</title>', html)
            if m: title = m.group(1).strip()
        cover = ''
        m = re.search(r'<meta[^>]*property="og:image"[^>]*content="([^"]+)"', html)
        if m: cover = m.group(1)
        if not cover:
            m = re.search(r'<img[^>]+class="[^"]*cover[^"]*"[^>]+src="([^"]+)"', html, re.S)
            if m: cover = m.group(1)
        if not cover:
            m = re.search(r'data-original="([^"]+)"', html)
            if m: cover = m.group(1)

        play_urls = []
        seen = set()
        def add(label, url):
            if url in seen: return
            seen.add(url)
            play_urls.append(f'{label}${url}')

        # iframe 二次解析
        for src in set(re.findall(r'<iframe[^>]+(?:src|data-src)=["\']([^"\']+)["\']', html)):
            full = src if src.startswith('http') else urljoin(base_url, src)
            try:
                embed_html = self._fetch(full, referer=self.host)
                if embed_html:
                    m3u8_m = re.search(r'''url\s*=\s*['"](https?://[^'"]+\.m3u8[^'"]*)['"]''', embed_html)
                    if m3u8_m:
                        add('直链', m3u8_m.group(1))
                        continue
                    for vm in re.finditer(r'https?://[^\s"\'<>]+\.(?:m3u8|mp4)[^\s"\'<>]*', embed_html):
                        add('直链', vm.group())
                        break
            except: pass
            add('外链', full)

        for media in set(re.findall(r'https?://[^\s"\'<>]+\.(?:m3u8|mp4|flv|mkv|ts)(?:\?[^\s"\'<>]*)?', html)):
            add('直链', media)
        for media in set(re.findall(r'<(?:video|source)[^>]+src=["\']([^"\']+)["\']', html)):
            if any(ext in media for ext in ['.m3u8', '.mp4', '.flv', '.ts']):
                full = media if media.startswith('http') else urljoin(base_url, media)
                add('HTML5', full)
        for script in re.findall(r'<script[^>]*>(.*?)</script>', html, re.S):
            for m in re.finditer(r'(?:url|src|file)\s*[:=]\s*["\']([^"\']+)["\']', script):
                url = m.group(1)
                if url.startswith('http') and any(ext in url for ext in ['.m3u8', '.mp4', '.flv']):
                    add('JS提取', url)
        if not play_urls:
            site_id = ''
            source_id = ''
            m_sid = re.search(r'site_id[=:](\d+)', html)
            if m_sid: site_id = m_sid.group(1)
            m_src = re.search(r'source_id[=:](\d+)', html)
            if m_src: source_id = m_src.group(1)
            if site_id and source_id:
                add('默认线路', f'{self.host}/play.php?site_id={site_id}&source_id={source_id}')
            else:
                add('默认线路', f'{self.host}/play.php?vid={vid}')
        if not play_urls:
            return None
        sources, urls = [], []
        for pu in play_urls:
            sn, url = pu.split('$', 1)
            sources.append(sn)
            urls.append(f'{sn}${url}')
        return {
            'vod_id': vid,
            'vod_name': title or vid,
            'vod_pic': cover or '',
            'vod_play_from': '$$$'.join(sources),
            'vod_play_url': '#'.join(urls),
            'vod_content': title or '',
        }

    def detailContent(self, ids):
        try:
            vid = str(ids[0] if isinstance(ids, list) else ids)
            detail = self._fetch_detail(vid)
            if not detail: return {'list': []}
            return {'list': [detail]}
        except Exception as e:
            self._log(f'detailContent 异常: {e}')
            return {'list': []}

    # ==================== 播放器（集成去广告代理） ====================
    def playerContent(self, flag, id, vipFlags=None):
        try:
            # 处理 vid|pid|res 格式 (即使没 AES，这里也可能有)
            if id and not id.startswith('http'):
                # 如果有 play.php 相关逻辑，可保留；当前仅处理 http 链接
                pass
            # 直接 http 链接
            if id.startswith('http'):
                # 如果是 m3u8，走本地代理清洗
                if '.m3u8' in id:
                    proxy_url = self._proxy_m3u8_url(id, self.host)
                    return {'parse': 0, 'url': proxy_url, 'header': json.dumps({'Referer': self.host})}
                else:
                    return {'parse': 0, 'url': id, 'header': json.dumps({'Referer': self.host})}
            # 兜底：尝试从详情获取
            return {'parse': 0, 'url': '', 'header': {}}
        except Exception as e:
            self._log(f'playerContent 异常: {e}')
            return {'parse': 0, 'url': '', 'header': {}}

    # ==================== m3u8 广告清洗（移植自撸一天） ====================
    def _proxy_m3u8_url(self, url, referer=''):
        try:
            if hasattr(self, 'getProxyUrl'):
                base = self.getProxyUrl()
                # 确保 base 以 & 或 ? 结尾，追加参数
                if '?' not in base:
                    base += '?do=py'
                return base + '&type=m3u8&url=' + quote(url, safe='') + '&referer=' + quote(referer or self.host, safe='')
        except:
            pass
        # 降级：直接返回原始 URL（不做清洗）
        return url

    def _get_m3u8_content(self, url, referer):
        try:
            headers = self.session.headers.copy()
            headers['Referer'] = referer
            resp = requests.get(url, headers=headers, timeout=15)
            if resp.status_code == 200:
                resp.encoding = 'utf-8'
                return resp.text
        except Exception as e:
            self._log(f'下载 m3u8 失败: {e}')
        return None

    def _clean_m3u8(self, m3u8_text, m3u8_url='', referer='', skip_seconds=25):
        """清洗 m3u8：去除广告分片，保留 KEY/MAP/DISCONTINUITY，URI 绝对化"""
        text = (m3u8_text or '').replace('\r', '')
        if '#EXT-X-STREAM-INF' in text:
            # master m3u8，将子 m3u8 的 URL 也替换为代理链接
            out = []
            for raw in text.splitlines():
                line = raw.strip()
                if not line:
                    continue
                if line.startswith('#'):
                    out.append(line)
                else:
                    abs_url = urljoin(m3u8_url, line)
                    if '.m3u8' in line.lower():
                        out.append(self._proxy_m3u8_url(abs_url, referer))
                    else:
                        out.append(abs_url)
            return '\n'.join(out) + '\n'

        header, segments, tail, media_sequence, target_duration = self._parse_m3u8_segments(text)
        if not segments:
            return text

        marker = self._main_path_marker(m3u8_url)
        stat = {}
        for seg in segments:
            key = self._segment_host_key(seg['uri'], m3u8_url)
            stat[key] = stat.get(key, 0.0) + float(seg.get('dur') or 0)
        main_key = max(stat.items(), key=lambda x: x[1])[0] if stat else ('', '')
        total_dur = sum(stat.values()) or 0
        main_dur = stat.get(main_key, 0)

        cleaned = []
        removed = 0
        for idx, seg in enumerate(segments):
            key = self._segment_host_key(seg['uri'], m3u8_url)
            is_front = idx < 12
            abs_uri = urljoin(m3u8_url, seg.get('uri', ''))
            is_ad = self._is_ad_segment(seg['uri'], seg.get('dur'), seg.get('tags'))
            if marker and marker not in urlparse(abs_uri).path.lower():
                is_ad = True
            tags_text = '\n'.join(seg.get('tags') or []).upper()
            if is_front and 'METHOD=NONE' in tags_text and marker and marker not in urlparse(abs_uri).path.lower():
                is_ad = True
            if (not is_ad) and is_front and total_dur > 0 and main_dur >= total_dur * 0.6:
                if key != main_key and stat.get(key, 0) <= 90:
                    is_ad = True
            if is_ad:
                removed += 1
                continue
            seg['_idx'] = idx
            cleaned.append(seg)

        # 若未检测到广告，尝试按累积秒数跳过前置广告段
        if removed == 0 and len(segments) > 4:
            acc = 0.0
            cut = 0
            for idx, seg in enumerate(segments[:12]):
                key = self._segment_host_key(seg['uri'], m3u8_url)
                if key == main_key and acc >= 3:
                    break
                acc += float(seg.get('dur') or target_duration or 3)
                cut = idx + 1
                if acc >= skip_seconds:
                    break
            if cut > 0 and cut < len(segments):
                first_key = self._segment_host_key(segments[0]['uri'], m3u8_url)
                if first_key != main_key:
                    cleaned = segments[cut:]
                    removed = cut

        if not cleaned:
            cleaned = segments
            removed = 0

        new_lines = []
        has_m3u = False
        for line in header:
            if line.startswith('#EXTM3U'): has_m3u = True
            if line.startswith('#EXT-X-MEDIA-SEQUENCE') or line.startswith('#EXT-X-START'):
                continue
            if line.startswith('#EXT-X-KEY') and 'METHOD=NONE' in line.upper() and removed > 0:
                continue
            new_lines.append(line)
        if not has_m3u:
            new_lines.insert(0, '#EXTM3U')
        first_idx = cleaned[0].get('_idx', removed) if cleaned else removed
        new_lines.append(f'#EXT-X-MEDIA-SEQUENCE:{media_sequence + first_idx}')
        for seg in cleaned:
            for tag in seg.get('tags') or []:
                if tag.startswith('#EXT-X-KEY') or tag.startswith('#EXT-X-MAP'):
                    def _fix_uri(m):
                        return 'URI="' + urljoin(m3u8_url, m.group(1)) + '"'
                    tag = re.sub(r'URI="([^"]+)"', _fix_uri, tag)
                new_lines.append(tag)
            new_lines.append(urljoin(m3u8_url, seg.get('uri', '')))
        if tail:
            for line in tail:
                if line.startswith('#EXT-X-ENDLIST'):
                    new_lines.append(line)
        elif '#EXT-X-ENDLIST' in text:
            new_lines.append('#EXT-X-ENDLIST')
        self._log(f'm3u8清洗: 原{len(segments)}片 → 删除{removed}片广告，保留{len(cleaned)}片')
        return '\n'.join(new_lines) + '\n'

    def _parse_m3u8_segments(self, text):
        lines = [x.strip() for x in (text or '').replace('\r', '').split('\n') if x.strip()]
        header, segments, tail = [], [], []
        pending_tags = []
        media_sequence = 0
        target_duration = 0
        started = False
        i = 0
        while i < len(lines):
            line = lines[i]
            if line.startswith('#EXT-X-MEDIA-SEQUENCE'):
                try:
                    media_sequence = int(line.split(':', 1)[1])
                except:
                    pass
                if not started:
                    header.append(line)
                else:
                    pending_tags.append(line)
            elif line.startswith('#EXT-X-TARGETDURATION'):
                try:
                    target_duration = float(line.split(':', 1)[1])
                except:
                    pass
                if not started:
                    header.append(line)
                else:
                    pending_tags.append(line)
            elif line.startswith('#EXTINF'):
                started = True
                dur = target_duration or 3.0
                m = re.search(r'#EXTINF:\s*([\d.]+)', line)
                if m:
                    try:
                        dur = float(m.group(1))
                    except:
                        pass
                tags = pending_tags + [line]
                pending_tags = []
                uri = ''
                j = i + 1
                while j < len(lines):
                    if lines[j].startswith('#'):
                        tags.append(lines[j])
                        j += 1
                        continue
                    uri = lines[j]
                    break
                if uri:
                    segments.append({'tags': tags, 'uri': uri, 'dur': dur})
                    i = j
                else:
                    tail.extend(tags)
            elif line.startswith('#EXT-X-ENDLIST'):
                tail.append(line)
            elif line.startswith('#'):
                if started:
                    pending_tags.append(line)
                else:
                    header.append(line)
            else:
                started = True
                dur = target_duration or 3.0
                segments.append({'tags': pending_tags, 'uri': line, 'dur': dur})
                pending_tags = []
            i += 1
        return header, segments, tail, media_sequence, target_duration

    def _is_ad_segment(self, uri, dur=0, prev_tags=None):
        u = (uri or '').strip().lower()
        if not u:
            return False
        ad_words = [
            'ad', 'ads', 'advert', 'advertise', 'advertisement', 'sponsor',
            'pre', 'preroll', '片头', '广告', '/gg/', '_gg', 'gg_', '/adv/',
            '/ad/', '/ads/', 'banner', 'promo', 'commercial'
        ]
        if any(w in u for w in ad_words):
            return True
        try:
            if 0 < float(dur) <= 1.2:
                return True
        except:
            pass
        return False

    def _segment_host_key(self, uri, base_url):
        try:
            full = urljoin(base_url, uri)
            p = urlparse(full)
            path = re.sub(r'/[^/]*$', '/', p.path or '/')
            return (p.netloc.lower(), path.lower())
        except:
            return ('', '')

    def _main_path_marker(self, m3u8_url):
        try:
            p = urlparse(m3u8_url).path
            m = re.search(r'(/\d{8}/[^/]+/\d+kb/hls/)', p)
            if m:
                return m.group(1).lower()
            m = re.search(r'(/\d{8}/[^/]+/)', p)
            if m:
                return m.group(1).lower()
        except:
            pass
        return ''

    def searchContent(self, key, quick, pg='1'):
        try:
            page = int(pg) if pg else 1
            url = f'{self.host}/search.php?keyword={quote(key)}&page={page}'
            html = self._fetch(url, referer=self.host)
            items = self._parse_list(html) if html else []
            return {'list': items, 'page': page, 'pagecount': page + 1, 'limit': len(items), 'total': page * len(items)}
        except Exception as e:
            self._log(f'searchContent 异常: {e}')
            return {'list': [], 'page': 1, 'pagecount': 1, 'limit': 0, 'total': 0}