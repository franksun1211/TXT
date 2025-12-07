# -*- coding: utf-8 -*-
# by @嗷呜
import html
import json
import random
import re
import sys
from urllib.parse import urlparse

import execjs
from Crypto.Cipher import AES
from Crypto.Util.Padding import unpad
from pyquery import PyQuery as pq

sys.path.append('..')
from base.spider import Spider
from curl_cffi import requests


class Spider(Spider):

    def init(self, extend='{}'):
        pass

    def getName(self):
        pass

    def isVideoFormat(self, url):
        pass

    def manualVideoCheck(self):
        pass

    def destroy(self):
        pass

    host = 'https://netflav5.com'

    headers = {
        'accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7',
        'accept-language': 'zh-CN,zh;q=0.9,en;q=0.8',
        'sec-ch-ua': '"Not/A)Brand";v="8", "Chromium";v="141", "Google Chrome";v="141"',
        'sec-ch-ua-platform': '"Windows"',
        'referer': f"{host}/",
        'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.7390.55 Safari/537.36',
    }

    def getheader(self):
        header = self.headers.copy()
        header['accept'] = "application/json, text/plain, */*"
        return header

    def homeContent(self, filter):
        result = {}
        cate = {
            "無碼": "genre",
            "有碼": "genre",
            "中文字幕": "genre",
            "女優": "actress",
            "類別": "tags",
            "隨機片單":"sheet"
        }
        classes = []
        filters = {}
        for k, j in cate.items():
            classes.append({
                'type_name': k,
                'type_id': f"{j}/{k}"
            })
        result['class'] = classes
        result['filters'] = filters
        return result

    def homeVideoContent(self):
        resp=requests.get(f"{self.host}/api98/page/v2/getIndex", headers=self.getheader(), impersonate="chrome110").json()
        data=self.unpack(resp['result'])
        return {'list': self.getlist(data['editorChoice']['docs'])}

    def categoryContent(self, tid, pg, filter, extend):
        # /api98/video/v2/getVideo?page=2&category=%E6%9C%89%E7%A2%BC
        params = {
            "page": pg,
            "category": tid
        }
        if tid == "actress/女優":
            return self.getact(pg, extend)
        elif "censored/" in  tid:
            # /api98/video/v2/getVideo?page=1&actor=MINAMO
            censore={"page": pg, "actor": tid.split('/', 1)[-1]}
            resp=requests.get(f"{self.host}/api98/video/v2/getVideo", params=censore, headers=self.getheader(),impersonate="chrome110").json()
            data = self.unpack(resp['result'])
        elif tid == "tags/類別":
            return self.gettags()
        elif tid == "sheet/隨機片單":
            return self.getsheet()
        elif 'browse/' in tid:
            browse={"page": pg, "shareCode": tid.split('/', 1)[-1]}
            resp=requests.post(f"{self.host}/api98/bookmark/getBookmarkWithCode", json=browse, headers=self.getheader(),impersonate="chrome110").json()
            data=resp['result']
        else:
            if "genre/" in tid:
                params["category"]=tid.split('/', 1)[-1]
            resp = requests.get(f"{self.host}/api98/video/v2/getVideo", params=params, headers=self.getheader(),impersonate="chrome110").json()
            data = self.unpack(resp['result'])
        return {'list': self.getlist(data['docs']), 'page': pg, 'pagecount': data['pages'], 'limit': data['limit'],'total': data['total']}

    def detailContent(self, ids):
        resp=requests.get(f"{self.host}/api98/video/v3/retrieveVideo/{ids[0]}", headers=self.getheader(),impersonate="chrome110").json()
        v=self.unpack(resp['result'])
        vod = {
            'type_name': v.get('category'),
            'vod_year': v.get('createdAt').split('T', 1)[0] if v.get('createdAt') else "",
            'vod_area': v.get('code'),
            'vod_remarks': v.get('duration')
        }
        vod['vod_actor'] = ' '.join([
            self.build_link(f"censored/{i}", f'#{i}')
            for i in v.get('actors', [])
            if i and ":" not in i
        ])
        vod['vod_content'] = ' '.join([
            self.build_link(f"genre/{i}", f'#{i}')
            for i in v.get('tags', [])
            if i and ":" not in i
        ])
        vod['vod_content'] = '\n'.join(['点击展开↓↓↓', vod['vod_content'], v.get('description')])
        ajmmm = {"NAV": [f"线路{i + 1}${self.e64(v)}" for i, v in enumerate(v.get('srcs', []))],
                 "Magt": [f"[{i.get('fileSize')}]{i.get('title')}${i.get('src')}" for i in
                          v.get('magnets', [])]}
        n, p = [], []
        for i, v in ajmmm.items():
            if len(v):
                n.append(i)
                p.append('#'.join(v))
        vod['vod_play_from'] = '$$$'.join(n)
        vod['vod_play_url'] = '$$$'.join(p)

        return {'list': [vod]}

    def searchContent(self, key, quick, pg="1"):
        params = {
            "type": "title",
            "page": pg,
            "keyword": key
        }
        resp = requests.get(f"{self.host}/api98/video/advanceSearchVideo", params=params, headers=self.getheader(),
                            impersonate="chrome110").json()
        data = resp['result']
        return {'list': self.getlist(data['docs']), 'page': pg, 'pagecount': data['pages'], 'limit': data['limit'],
                'total': data['total']}

    def playerContent(self, flag, id, vipFlags):
        if 'Magt' in flag:
            header = self.headers.copy()
            del header['accept']
            del header['sec-ch-ua-platform']
            return {'parse': 0, 'url': id}
        id = self.d64(id)
        parsed = urlparse(id)
        prefix = f"{parsed.scheme}://{parsed.netloc}"
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.7390.55 Safari/537.36',
            'sec-ch-ua-platform': '"Windows"',
            'sec-ch-ua': '"Not/A)Brand";v="8", "Chromium";v="141", "Google Chrome";v="141"',
            'referer': f"{prefix}{parsed.path}",
            'accept-language': 'zh-CN,zh;q=0.9,en;q=0.8'
        }
        # doc = pq(requests.get(id, headers=self.headers, impersonate="chrome110").content.decode())
        # stxt = doc('script')
        jscode, key, playurl, p, missp = '', '', '', 0, ''
        # try:
        #     if '#' in id:
        #         vid = id.split('#', 1)[-1]
        #         params = {
        #             "id": vid,
        #             "w": random.randint(1200, 1920),
        #             "h": random.randint(600, 1080),
        #             "r": ""
        #         }
        #         resp = requests.get(f"{prefix}/api/v1/video", params=params, headers=headers, impersonate="chrome110")
        #         adata = json.loads(self.aes(resp.text))
        #         path = adata.get('hls')
        #         if path: playurl = f"{prefix}{'' if path.startswith('/') else '/'}{path}"
        #     else:
        #         for i in stxt.items():
        #             if "eval(function" in i.text():
        #                 jscode = i.text()
        #                 break
        #             elif 'missav' in id and 'jwplayer.key' in i.text():
        #                 jscode = i.html()
        #                 missp = parsed.path[:-1] if parsed.path.endswith('/') else parsed.path
        #                 break
        #         pattern = r'jwplayer\.key\s*=\s*"([^"]+)"'
        #         match = re.search(pattern, doc.html())
        #         if match: key = match.group(1)
        #         if jscode and key:
        #             data = self.p_qjs(jscode, key)
        #             path = data.get('sources', [])[0].get('file')
        #             if path: playurl = f"{prefix}{missp}{'' if path.startswith('/') else '/'}{path}"
        # except Exception as e:
        #     self.log(f"执行失败: {e}")
        #     playurl, p = id, 1
        if not playurl:
            playurl, p = id, 1
        return {'parse': p, 'url': playurl, 'header': headers}

    def localProxy(self, param):
        pass

    def getact(self, pg, ext):
        params = {k: v for k, v in {
            "page": pg,
            "type": ext.get("type", ""),
            "age": ext.get("age"),
            "height": ext.get("height"),
            "breast": ext.get("breast"),
            "waist": ext.get("waist"),
            "hip": ext.get("hip"),
            "cup": ext.get("cup")
        }.items() if v or k == "type"}
        resp = requests.get(f"{self.host}/api98/actress/getActress", params=params, headers=self.getheader(),impersonate="chrome110").json()
        data = resp['result']
        videos = [{
            'vod_id': f"{i.get('type')}/{i.get('name')}",
            'vod_name': i.get('name'),
            'vod_pic': f"{i.get('icon')}@Referer={self.host}/",
            'vod_remarks': i.get('videoCount'),
            'vod_tag': 'folder',
            'style': {"type": "oval"}
        } for i in data['docs']]
        return {'list': videos, 'page': pg, 'pagecount': data['pages'], 'limit': data['limit'], 'total': data['total']}

    def gettags(self):
        doc=pq(requests.get(f"{self.host}/genre", headers=self.headers,impersonate="chrome110").content)
        videos=[
            {
                'vod_id': f"genre/{j.text()}",
                'vod_name': j.text(),
                'vod_pic': '',
                'vod_tag': 'folder',
                'style': {"type": "rect", "ratio": 2}
            }
            for i in doc('.page_root_2.genre_container .genre_item_container').items()
            for j in i('a').items()
        ]
        return {'list': videos, 'page': 1, 'pagecount': 1, 'limit': len(videos), 'total': 1}

    def getsheet(self):
        doc=pq(requests.get(f"{self.host}/browse", headers=self.headers,impersonate="chrome110").content)
        videos=[
            {
                'vod_id': f"browse/{href.split('=',1)[1]}",
                'vod_name': href.split('=',1)[1],
                'vod_pic': i('img').eq(1).attr('src'),
                'vod_tag': 'folder',
                'style': {"type": "rect", "ratio": 2}
            }
            for i in doc('#browse_grid_container_2 > a').items()
            if(href:=i.attr('href'))
        ]
        return {'list': videos, 'page': 1, 'pagecount': 1, 'limit': len(videos), 'total': 1}

    def getlist(self, data):
        videos = []
        for i in data:
            if (not i.get('title') or not i.get('sourceDate')) and i.get('video'):i=i['video']
            videos.append({
                'vod_id': i.get('videoId'),
                'vod_name': i.get('title'),
                'vod_pic': i.get('preview') or i.get('preview_hp'),
                'vod_year': i.get('sourceDate').split('T', 1)[0] if i.get('sourceDate') else "",
                'vod_remarks': i.get('code'),
                'style': {"type": "rect", "ratio": 1.33}
            })
        return videos

    def p_qjs(self, js_code, key):
        try:
            text = f'''
            var videoConfig = "";
            global.jwplayer = function () {{
                return {{
                    setup: function (config) {{
                        videoConfig = JSON.stringify(config);
                        return this;
                    }},
                    on: function (event) {{
                        return this;
                    }},
                    addButton: function(icon, tooltip) {{
                        return this;
                    }}
                }};
            }};
            jwplayer.key = "{key}";
            global.$ = function () {{
                return {{}}
            }};
            
            $.cookie = () => null;
            $.ajaxSetup = () => null;
            global.localStorage = {{
                getItem: (key) => {{
                    return 'mock-value';
                }}
            }};
            {js_code}
            '''
            # print(text)
            ctx = execjs.compile(text)
            result = ctx.eval("videoConfig")
            return json.loads(result)
        except Exception as e:
            print("js执行失败：",e)
            return {}

    def build_link(self, id, iname, name=''):
        return '[a=cr:' + json.dumps({'id': id, 'name': iname}) + '/]' + (name or iname) + '[/a]'

    def aes(self, data):
        try:
            key = b"kiemtienmua911ca"
            iv = b"1234567890oiuytr"
            cipher = AES.new(key, AES.MODE_CBC, iv)
            pt = unpad(cipher.decrypt(bytes.fromhex(data)), AES.block_size)
            return pt.decode("utf-8")
        except Exception as e:
            self.log(f"aes执行失败: {e}")
            return ''

    def decode_string(self, s):
        if not isinstance(s, str):
            return s
        replacements = {
            "+": " ",
            "%2B": "+",
            "%7C": "|",
            "%5E": "^",
            "%25": "%"
        }
        for pattern, replacement in replacements.items():
            s = s.replace(pattern, replacement)
        return s

    def unpack(self, compressed_str):
        parts = compressed_str.replace("$", "|", 1).split("^")
        dictionary = []
        if parts[0] != "":
            strings = parts[0].split("|")
            for s in strings:
                dictionary.append(self.decode_string(s))
        if len(parts) > 1 and parts[1] != "":
            integers = parts[1].split("|")
            for i in integers:
                dictionary.append(int(i, 36))
        if len(parts) > 2 and parts[2] != "":
            floats = parts[2].split("|")
            for f in floats:
                dictionary.append(float(f))
        structure_str = parts[3] if len(parts) > 3 else ""
        tokens = []
        current_num = ""

        for char in structure_str:
            if char in ["|", "$", "@", "]"]:
                if current_num:
                    tokens.append(int(current_num, 36))
                    current_num = ""
                if char != "|":
                    tokens.append(char)
            else:
                current_num += char
        if current_num:
            tokens.append(int(current_num, 36))

        token_index = [0]

        def parse_collection():
            token_type = tokens[token_index[0]]
            token_index[0] += 1
            if token_type == "@":
                result = []
                while token_index[0] < len(tokens):
                    token = tokens[token_index[0]]

                    if token == "]":
                        token_index[0] += 1
                        return result

                    if token == "@" or token == "$":
                        result.append(parse_collection())
                    else:
                        token_index[0] += 1
                        if token == -1:
                            result.append(True)
                        elif token == -2:
                            result.append(False)
                        elif token == -3:
                            result.append(None)
                        elif token == -4:
                            result.append("")
                        elif token == -5:
                            result.append(None)
                        else:
                            result.append(dictionary[token])
                return result
            elif token_type == "$":
                result = {}
                while token_index[0] < len(tokens):
                    key_token = tokens[token_index[0]]

                    if key_token == "]":
                        token_index[0] += 1
                        return result
                    if key_token == -4:
                        key = ""
                    else:
                        key = dictionary[key_token]

                    token_index[0] += 1
                    value_token = tokens[token_index[0]]
                    if value_token == "@" or value_token == "$":
                        result[key] = parse_collection()
                    else:
                        token_index[0] += 1
                        if value_token == -1:
                            result[key] = True
                        elif value_token == -2:
                            result[key] = False
                        elif value_token == -3:
                            result[key] = None
                        elif value_token == -4:
                            result[key] = ""
                        elif value_token == -5:
                            result[key] = None
                        else:
                            result[key] = dictionary[value_token]
                return result

            else:
                raise TypeError(f"Bad token {token_type} isn't a type")

        return parse_collection()