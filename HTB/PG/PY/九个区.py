# coding=utf-8
#!/usr/bin/python
import sys
sys.path.append('..')
from base.spider import Spider
import json
import time
import urllib.parse
import re
import requests
from lxml import etree

class Spider(Spider):
    
    def getName(self):
        return "香蕉视频"
    
    def init(self, extend=""):
        self.host = "https://618013.xyz"
        self.api_host = "https://h5.xxoo168.org"
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
            'Referer': self.host
        }
        self.log(f"香蕉视频爬蟲初始化完成")

    def html(self, content):
        try:
            return etree.HTML(content)
        except:
            return None

    def regStr(self, pattern, string, index=1):
        try:
            match = re.search(pattern, string, re.IGNORECASE)
            if match and len(match.groups()) >= index:
                return match.group(index)
        except:
            pass
        return ""

    def homeContent(self, filter):
        result = {}
        classes = [
            {'type_id': '618013.xyz_1', 'type_name': '全部视频'},
            {'type_id': '618013.xyz_13', 'type_name': '香蕉精品'},
            {'type_id': '618013.xyz_6', 'type_name': '国产视频'},
            {'type_id': '618013.xyz_33', 'type_name': '中文字幕'},
            {'type_id': '618013.xyz_32', 'type_name': '国产自拍'}
        ]
        result['class'] = classes
        try:
            rsp = self.fetch(self.host, headers=self.headers)
            doc = self.html(rsp.text)
            result['list'] = self._get_videos(doc, limit=20)
        except:
            result['list'] = []
        return result

    def categoryContent(self, tid, pg, filter, extend):
        try:
            domain, type_id = tid.split('_')
            url = f"https://{domain}/index.php/vod/type/id/{type_id}.html"
            if pg and pg != '1':
                url = url.replace('.html', f'/page/{pg}.html')
            
            rsp = self.fetch(url, headers=self.headers)
            doc = self.html(rsp.text)
            videos = self._get_videos(doc)
            
            # 微調：更穩定的分頁獲取方式
            pagecount = pg
            pages = doc.xpath('//div[@class="mypage"]//a[contains(@href, "page")]/@href')
            if pages:
                last_pg = self.regStr(r'page/(\d+)', pages[-1])
                if last_pg: pagecount = last_pg
            
            return {
                'list': videos,
                'page': int(pg),
                'pagecount': int(pagecount),
                'limit': 20,
                'total': int(pagecount) * 20
            }
        except:
            return {'list': []}

    def detailContent(self, ids):
        try:
            vid = ids[0]
            video_id = vid.split('_')[-1] if '_' in vid else vid
            detail_url = f"{self.host}/index.php/vod/detail/id/{video_id}.html"
            
            rsp = self.fetch(detail_url, headers=self.headers)
            doc = self.html(rsp.text)
            
            # 使用原本的解析邏輯
            title = doc.xpath('//h1/text()')[0].strip() if doc.xpath('//h1/text()') else "未知"
            pic = doc.xpath('//div[@class="dyimg"]//img/@src')[0] if doc.xpath('//div[@class="dyimg"]//img/@src') else ""
            if pic.startswith('/'): pic = self.host + pic

            video_info = {
                'vod_id': vid,
                'vod_name': title,
                'vod_pic': pic,
                'vod_play_from': '香蕉雲',
                'vod_play_url': f"立即播放${vid}"
            }
            return {'list': [video_info]}
        except:
            return {'list': []}

    def playerContent(self, flag, id, vipFlags):
        """核心復原：確保播放連結正確返回"""
        try:
            video_id = id.split('_')[-1] if '_' in id else id
            api_url = f"{self.api_host}/api/v2/vod/reqplay/{video_id}"
            
            # OK 影視通常需要特定的 Referer
            api_headers = self.headers.copy()
            api_headers.update({'Origin': self.host})
            
            api_response = self.fetch(api_url, headers=api_headers)
            if api_response:
                data = api_response.json()
                # 同時檢查 httpurl 和 httpurl_preview (重要：很多是試看版)
                video_url = data.get('data', {}).get('httpurl') or data.get('data', {}).get('httpurl_preview')
                
                if video_url:
                    # 去掉 URL 中的多餘參數（?300），這常導致 OK 影視無法識別格式
                    video_url = video_url.split('?')[0] if '.m3u8' in video_url else video_url
                    return {'parse': 0, 'playUrl': '', 'url': video_url, 'header': ''}
            
            # 萬一 API 沒給地址，回退到原始播放頁嘗試
            return {'parse': 1, 'url': f"{self.host}/index.php/vod/play/id/{video_id}.html"}
        except:
            return {'parse': 1, 'url': id}

    def _get_videos(self, doc, limit=None):
        videos = []
        if doc is None: return videos
        elements = doc.xpath('//a[@class="vodbox"]')
        for elem in elements:
            try:
                href = elem.xpath('./@href')[0]
                vod_id = self.regStr(r'id/(\d+)', href)
                
                # 關鍵：標題解密（保持你原本的邏輯）
                title_elem = elem.xpath('./p[@class="km-script"]/text()')
                name = self._decrypt_title(title_elem[0]) if title_elem else "未知"
                
                pic = elem.xpath('.//img/@data-original')[0] if elem.xpath('.//img/@data-original') else ""
                if pic.startswith('/'): pic = self.host + pic

                videos.append({
                    'vod_id': f"618013.xyz_{vod_id}",
                    'vod_name': name,
                    'vod_pic': pic,
                    'vod_remarks': ''
                })
            except:
                continue
        return videos[:limit] if limit else videos

    def _decrypt_title(self, encrypted_text):
        try:
            return ''.join([chr(ord(char) ^ 128) for char in encrypted_text])
        except:
            return encrypted_text            return None

    def regStr(self, pattern, string, index=1):
        """正则表达式提取字符串"""
        try:
            match = re.search(pattern, string, re.IGNORECASE)
            if match and len(match.groups()) >= index:
                return match.group(index)
        except:
            pass
        return ""

    def isVideoFormat(self, url):
        pass

    def manualVideoCheck(self):
        pass

    def homeContent(self, filter):
        """获取首页内容和分类"""
        result = {}
        # 只保留指定的分类
        classes = [
            {'type_id': '618013.xyz_1', 'type_name': '全部视频'},
            {'type_id': '618013.xyz_13', 'type_name': '香蕉精品'},
            {'type_id': '618013.xyz_22', 'type_name': '制服诱惑'},
            {'type_id': '618013.xyz_6', 'type_name': '国产视频'},
            {'type_id': '618013.xyz_8', 'type_name': '清纯少女'},
            {'type_id': '618013.xyz_9', 'type_name': '辣妹大奶'},
            {'type_id': '618013.xyz_10', 'type_name': '女同专属'},
            {'type_id': '618013.xyz_11', 'type_name': '素人出演'},
            {'type_id': '618013.xyz_12', 'type_name': '角色扮演'},
            {'type_id': '618013.xyz_20', 'type_name': '人妻熟女'},
            {'type_id': '618013.xyz_23', 'type_name': '日韩剧情'},
            {'type_id': '618013.xyz_21', 'type_name': '经典伦理'},
            {'type_id': '618013.xyz_7', 'type_name': '成人动漫'},
            {'type_id': '618013.xyz_14', 'type_name': '精品二区'},
            {'type_id': '618013.xyz_40', 'type_name': '精品三区'},
            {'type_id': '618013.xyz_53', 'type_name': '动漫中字'},
            {'type_id': '618013.xyz_52', 'type_name': '日本无码'},
            {'type_id': '618013.xyz_33', 'type_name': '中文字幕'},
            {'type_id': '618013.xyz_44', 'type_name': '国产传媒'},
            {'type_id': '618013.xyz_32', 'type_name': '国产自拍'}
        ]
        result['class'] = classes
        try:
            rsp = self.fetch(self.host, headers=self.headers)
            doc = self.html(rsp.text)
            videos = self._get_videos(doc, limit=20)
            result['list'] = videos
        except Exception as e:
            self.log(f"首页获取出错: {str(e)}")
            result['list'] = []
        return result

    def homeVideoContent(self):
        """分类定义 - 兼容性方法"""
        return {
            'class': [
                {'type_id': '618013.xyz_1', 'type_name': '全部视频'},
                {'type_id': '618013.xyz_13', 'type_name': '香蕉精品'},
                {'type_id': '618013.xyz_22', 'type_name': '制服诱惑'},
                {'type_id': '618013.xyz_6', 'type_name': '国产视频'},
                {'type_id': '618013.xyz_8', 'type_name': '清纯少女'},
                {'type_id': '618013.xyz_9', 'type_name': '辣妹大奶'},
                {'type_id': '618013.xyz_10', 'type_name': '女同专属'},
                {'type_id': '618013.xyz_11', 'type_name': '素人出演'},
                {'type_id': '618013.xyz_12', 'type_name': '角色扮演'},
                {'type_id': '618013.xyz_20', 'type_name': '人妻熟女'},
                {'type_id': '618013.xyz_23', 'type_name': '日韩剧情'},
                {'type_id': '618013.xyz_21', 'type_name': '经典伦理'},
                {'type_id': '618013.xyz_7', 'type_name': '成人动漫'},
                {'type_id': '618013.xyz_14', 'type_name': '精品二区'},
                {'type_id': '618013.xyz_40', 'type_name': '精品三区'},
                {'type_id': '618013.xyz_53', 'type_name': '动漫中字'},
                {'type_id': '618013.xyz_52', 'type_name': '日本无码'},
                {'type_id': '618013.xyz_33', 'type_name': '中文字幕'},
                {'type_id': '618013.xyz_44', 'type_name': '国产传媒'},
                {'type_id': '618013.xyz_32', 'type_name': '国产自拍'}
            ]
        }

    def categoryContent(self, tid, pg, filter, extend):
        """分类内容"""
        try:
            domain, type_id = tid.split('_')
            url = f"https://{domain}/index.php/vod/type/id/{type_id}.html"
            if pg and pg != '1':
                url = url.replace('.html', f'/page/{pg}.html')
            self.log(f"访问分类URL: {url}")
            rsp = self.fetch(url, headers=self.headers)
            doc = self.html(rsp.text)
            videos = self._get_videos(doc, limit=20)
            
            # 获取总页数
            pagecount = 1
            page_elements = doc.xpath('//div[@class="mypage"]//a')
            if page_elements:
                try:
                    last_page = page_elements[-2].xpath('./text()')[0]  # 获取"尾页"前一个元素
                    if last_page.isdigit():
                        pagecount = int(last_page)
                except:
                    pass
            
            return {
                'list': videos,
                'page': int(pg),
                'pagecount': pagecount,
                'limit': 20,
                'total': pagecount * 20
            }
        except Exception as e:
            self.log(f"分类内容获取出错: {str(e)}")
            return {'list': []}

    def searchContent(self, key, quick, pg="1"):
        """搜索功能"""
        try:
            search_url = f"{self.host}/index.php/vod/search.html?wd={urllib.parse.quote(key)}&page={pg}"
            self.log(f"搜索URL: {search_url}")
            rsp = self.fetch(search_url, headers=self.headers)
            if not rsp or rsp.status_code != 200:
                return {'list': []}
            doc = self.html(rsp.text)
            videos = self._get_videos(doc)
            return {'list': videos}
        except Exception as e:
            self.log(f"搜索出错: {str(e)}")
            return {'list': []}

    def detailContent(self, ids):
        """详情页面"""
        try:
            vid = ids[0]
            if '_' in vid:
                domain, video_id = vid.split('_')
                detail_url = f"https://{domain}/index.php/vod/detail/id/{video_id}.html"
            else:
                detail_url = f"{self.host}/index.php/vod/detail/id/{vid}.html"
            self.log(f"访问详情URL: {detail_url}")
            rsp = self.fetch(detail_url, headers=self.headers)
            doc = self.html(rsp.text)
            video_info = self._get_detail(doc, vid)
            return {'list': [video_info]} if video_info else {'list': []}
        except Exception as e:
            self.log(f"详情获取出错: {str(e)}")
            return {'list': []}

    def playerContent(self, flag, id, vipFlags):
        """播放链接 - 直接使用API获取视频地址"""
        try:
            self.log(f"获取播放链接: flag={flag}, id={id}")
            
            # 提取视频ID
            if '_' in id:
                _, video_id = id.split('_')
            else:
                video_id = id
                
            self.log(f"视频ID: {video_id}")
            
            # 直接调用API获取视频地址
            api_url = f"{self.api_host}/api/v2/vod/reqplay/{video_id}"
            self.log(f"请求API获取视频地址: {api_url}")
            
            api_headers = self.headers.copy()
            api_headers.update({
                'Referer': f"{self.host}/",
                'Origin': self.host,
                'X-Requested-With': 'XMLHttpRequest'
            })
            
            api_response = self.fetch(api_url, headers=api_headers)
            if api_response and api_response.status_code == 200:
                data = api_response.json()
                self.log(f"API响应: {data}")
                
                if data.get('retcode') == 3:
                    video_url = data.get('data', {}).get('httpurl_preview', '')
                else:
                    video_url = data.get('data', {}).get('httpurl', '')
                
                if video_url:
                    # 移除可能的参数
                    video_url = video_url.replace('?300', '')
                    self.log(f"从API获取到视频地址: {video_url}")
                    return {'parse': 0, 'playUrl': '', 'url': video_url}
                else:
                    self.log("API响应中没有找到视频地址")
            else:
                self.log(f"API请求失败，状态码: {api_response.status_code if api_response else '无响应'}")
                
            # 如果API请求失败，回退到原来的方法
            if '_' in id:
                domain, play_id = id.split('_')
                play_url = f"https://{domain}/html/kkyd.html?m={play_id}"
            else:
                play_url = f"{self.host}/html/kkyd.html?m={id}"
                
            self.log(f"回退到播放页面: {play_url}")
            return {'parse': 1, 'playUrl': '', 'url': play_url}
            
        except Exception as e:
            self.log(f"播放链接获取出错: {str(e)}")
            # 出错时也返回播放页面URL
            if '_' in id:
                domain, play_id = id.split('_')
                play_url = f"https://{domain}/html/kkyd.html?m={play_id}"
            else:
                play_url = f"{self.host}/html/kkyd.html?m={id}"
            return {'parse': 1, 'playUrl': '', 'url': play_url}

    # ========== 辅助方法 ==========
    
    def _get_videos(self, doc, limit=None):
        """获取影片列表 - 根据实际网站结构"""
        try:
            videos = []
            elements = doc.xpath('//a[@class="vodbox"]')
            self.log(f"找到 {len(elements)} 个vodbox元素")
            for elem in elements:
                video = self._extract_video(elem)
                if video:
                    videos.append(video)
            return videos[:limit] if limit and videos else videos
        except Exception as e:
            self.log(f"获取影片列表出错: {str(e)}")
            return []

    def _extract_video(self, element):
        """提取影片信息 - 修复标题乱码问题，正确读取km-script标签文本"""
        try:
            # 1. 提取影片链接（获取vod_id的来源）
            link = element.xpath('./@href')[0]  # 获取a标签的href属性
            if link.startswith('/'):
                link = self.host + link  # 补全相对路径为完整URL
            
            # 2. 提取vod_id（从URL的m参数获取，而非hash，更准确）
            vod_id = self.regStr(r'm=(\d+)', link)  # 匹配 ?m=123 中的数字
            if not vod_id:
                vod_id = str(hash(link) % 1000000)  # 兜底：hash生成唯一ID
            
            # 3. 提取标题（关键修复：读取<p class="km-script">内的文本并解密）
            title_elem = element.xpath('./p[@class="km-script"]/text()')  # 定位km-script标签
            if not title_elem:
                # 尝试其他可能的标题选择器
                title_elem = element.xpath('.//p[contains(@class, "script")]/text()')
                if not title_elem:
                    title_elem = element.xpath('.//p/text()')
                    if not title_elem:
                        title_elem = element.xpath('.//h3/text()')
                        if not title_elem:
                            title_elem = element.xpath('.//h4/text()')
                            if not title_elem:
                                self.log(f"未找到标题元素，跳过该视频")
                                return None
            
            title_encrypted = title_elem[0].strip()  # 获取加密的标题文本
            
            # 4. 解密标题 - 使用网站的解密算法
            title = self._decrypt_title(title_encrypted)
            
            # 5. 提取封面图（逻辑不变，兼容data-original和src）
            pic_elem = element.xpath('.//img/@data-original')  # 优先懒加载地址
            if not pic_elem:
                pic_elem = element.xpath('.//img/@src')  # 兜底：直接src地址
            pic = pic_elem[0] if pic_elem else ''
            
            # 6. 补全图片URL（处理相对路径或无协议的情况）
            if pic:
                if pic.startswith('//'):
                    pic = 'https:' + pic  # 补全https协议
                elif pic.startswith('/'):
                    pic = self.host + pic  # 补全主域名
            
            # 7. 返回正确的视频信息
            return {
                'vod_id': f"618013.xyz_{vod_id}",
                'vod_name': title,  # 此时title已为正确文本
                'vod_pic': pic,
                'vod_remarks': '',
                'vod_year': ''
            }
        except Exception as e:
            self.log(f"提取影片信息出错: {str(e)}")
            return None

    def _decrypt_title(self, encrypted_text):
        """解密标题 - 使用网站的解密算法"""
        try:
            # 网站使用的解密算法：每个字符与128进行异或操作
            decrypted_chars = []
            for char in encrypted_text:
                # 将字符转换为Unicode码点
                code_point = ord(char)
                # 与128进行异或操作
                decrypted_code = code_point ^ 128
                # 转换回字符
                decrypted_char = chr(decrypted_code)
                decrypted_chars.append(decrypted_char)
            
            # 拼接解密后的字符
            decrypted_text = ''.join(decrypted_chars)
            return decrypted_text
        except Exception as e:
            self.log(f"标题解密失败: {str(e)}")
            return encrypted_text  # 如果解密失败，返回原文本

    def _get_detail(self, doc, vid):
        """获取详情信息 (优化版) - 修复播放源提取问题"""
        try:
            title = self._get_text(doc, ['//h1/text()', '//title/text()'])
            pic = self._get_text(doc, ['//div[@class="dyimg"]//img/@src', '//img[@class="poster"]/@src'])
            if pic and pic.startswith('/'):
                pic = self.host + pic
            desc = self._get_text(doc, ['//div[@class="yp_context"]/text()', '//div[@class="introduction"]//text()'])
            actor = self._get_text(doc, ['//span[contains(text(),"主演")]/following-sibling::*/text()'])
            director = self._get_text(doc, ['//span[contains(text(),"导演")]/following-sibling::*/text()'])

            play_from = []
            play_urls = []
            
            # 尝试查找播放源
            play_links = doc.xpath('//a[contains(@href, "m=")]')
            if play_links:
                episodes = []
                for link in play_links:
                    ep_title = link.xpath('./text()')
                    ep_href = link.xpath('./@href')[0]
                    if ep_title:
                        ep_title = ep_title[0].strip()
                        play_id = self.regStr(r'm=(\d+)', ep_href)
                        if play_id:
                            episodes.append(f"{ep_title}${play_id}")
                
                if episodes:
                    play_from.append("默认播放源")
                    play_urls.append('#'.join(episodes))

            if not play_from:
                self.log("未找到播放源元素，无法定位播放源列表")
                # 即使没有播放源，也返回基本信息
                return {
                    'vod_id': vid,
                    'vod_name': title,
                    'vod_pic': pic,
                    'type_name': '',
                    'vod_year': '',
                    'vod_area': '',
                    'vod_remarks': '',
                    'vod_actor': actor,
                    'vod_director': director,
                    'vod_content': desc,
                    'vod_play_from': '默认播放源',
                    'vod_play_url': f"第1集${vid}"
                }

            return {
                'vod_id': vid,
                'vod_name': title,
                'vod_pic': pic,
                'type_name': '',
                'vod_year': '',
                'vod_area': '',
                'vod_remarks': '',
                'vod_actor': actor,
                'vod_director': director,
                'vod_content': desc,
                'vod_play_from': '$$$'.join(play_from),
                'vod_play_url': '$$$'.join(play_urls)
            }
        except Exception as e:
            self.log(f"获取详情出错: {str(e)}")
            return None

    def _get_text(self, doc, selectors):
        """通用文本提取"""
        for selector in selectors:
            texts = doc.xpath(selector)
            for text in texts:
                if text and text.strip():
                    return text.strip()
        return ''
