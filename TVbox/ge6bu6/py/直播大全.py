# -*- coding: utf-8 -*-
# 裤佬TG频道https://t.me/stymei
# 裤佬独家6合1聚合直播 - 抖音、快手、B站、虎牙、斗鱼、酷狗
import json
import re
import sys
import time
import random
import hashlib
import base64
import urllib.parse
from base64 import b64decode, b64encode
from urllib.parse import parse_qs, quote, unquote
import requests
from bs4 import BeautifulSoup
sys.path.append('..')
from base.spider import Spider
from concurrent.futures import ThreadPoolExecutor


class Spider(Spider):

    def __init__(self):
        super().__init__()
        # 快手/酷狗专用成员变量
        self.ks_mobile_host = 'https://livev.m.chenzhongtech.com'
        self.kg_api_host = "https://fxservice4.kugou.com"
        self._ks_cache = {}
        self._ks_last_reco = 0
        self.kg_room_cache = {}

        # 抖音直播 专用成员变量
        self.dy_ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36"
        self.dy_a_bogus = "a" * 188
        self.dy_ttwid = ""

    def init(self, extend=""):
        return self

    def getName(self):
        return "直播"

    def isVideoFormat(self, url):
        video_formats = ['.m3u8', '.mp4', '.flv', '.ts']
        return any(str(url).lower().endswith(fmt) for fmt in video_formats)

    def manualVideoCheck(self):
        return False

    def destroy(self):
        pass

    headers = [
        {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36 Edg/129.0.0.0"
        },
        {
            "User-Agent": "Dart/3.4 (dart:io)"
        }
    ]

    excepturl = 'https://www.baidu.com'

    hosts = {
        "huya": ["https://www.huya.com", "https://mp.huya.com"],
        "douyu": "https://www.douyu.com",
        "bili_search": "https://search.bilibili.com",
        "bili_api": "https://api.live.bilibili.com",
        "kuaishou": "https://live.kuaishou.com",
        "kugou": "https://fanxing.kugou.com",
        "douyin": "https://live.douyin.com"
    }

    referers = {
        "huya": "https://live.cdn.huya.com",
        "douyu": "https://m.douyu.com",
        "bili": "https://live.bilibili.com",
        "kuaishou": "https://live.kuaishou.com",
        "kugou": "https://fanxing.kugou.com/",
        "douyin": "https://live.douyin.com/"
    }

    playheaders = {
        "bili": {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36 Edg/129.0.0.0',
            'Referer': 'https://live.bilibili.com/'
        },
        'huya': {
            'User-Agent': 'ExoPlayer',
            'Connection': 'Keep-Alive',
            'Icy-MetaData': '1'
        },
        'douyu': {
            'User-Agent': 'libmpv',
            'Icy-MetaData': '1'
        },
        'ks': {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:115.0) Gecko/20100101 Firefox/115.0',
            'Referer': 'https://live.kuaishou.com/'
        },
        'kugou': {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36',
            'Referer': 'https://fx.kugou.com/'
        },
        'douyin': {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36',
            'Referer': 'https://live.douyin.com/'
        }
    }

    bili_categories = [
        {"type_id": "舞蹈", "type_name": "舞蹈"},
        {"type_id": "音乐", "type_name": "音乐"},
        {"type_id": "手游", "type_name": "手游"},
        {"type_id": "网游", "type_name": "网游"},
        {"type_id": "单机游戏", "type_name": "单机游戏"},
        {"type_id": "虚拟主播", "type_name": "虚拟主播"},
        {"type_id": "电台", "type_name": "电台"},
        {"type_id": "体育", "type_name": "体育"},
        {"type_id": "聊天", "type_name": "聊天"},
        {"type_id": "娱乐", "type_name": "娱乐"},
        {"type_id": "电影", "type_name": "影视"},
        {"type_id": "新闻", "type_name": "新闻"}
    ]

    ks_categories = [
        {'type_id': 'kwai_live', 'type_name': '推荐'},
        {'type_id': 'game_1001', 'type_name': '王者荣耀'},
        {'type_id': 'game_22008', 'type_name': '和平精英'},
        {'type_id': 'game_22723', 'type_name': '三角洲行动'},
        {'type_id': 'game_1', 'type_name': '英雄联盟'},
        {'type_id': 'game_2', 'type_name': '穿越火线'},
        {'type_id': 'game_1054', 'type_name': 'QQ飞车手游'},
        {'type_id': 'cat_SYXX', 'type_name': '手游休闲'},
        {'type_id': 'cat_WYJJ', 'type_name': '网游竞技'}
    ]

    kg_categories = [
        {"type_id": "0", "type_name": "推荐"},
        {"type_id": "game", "type_name": "一起玩"},
        {"type_id": "music", "type_name": "音乐"},
        {"type_id": "dance", "type_name": "舞蹈"},
        {"type_id": "face", "type_name": "颜值"},
        {"type_id": "acg", "type_name": "酷次元"},
        {"type_id": "chinese", "type_name": "新秀"}
    ]

    dy_categories = [
        {"type_id": "4_103_1_2_1_1010014", "type_name": "英雄联盟"},
        {"type_id": "4_103_1_2_1_1010045", "type_name": "王者荣耀"},
        {"type_id": "4_103_1_2_1_1010055", "type_name": "金铲铲之战"},
        {"type_id": "4_103_1_2_1_1010350", "type_name": "魔兽争霸3"},
        {"type_id": "4_103_1_1_1_1010032", "type_name": "和平精英"},
        {"type_id": "4_103_1_1_1_1011032", "type_name": "三角洲行动"},
        {"type_id": "4_103_1_6_1_1010092", "type_name": "地下城与勇士"},
        {"type_id": "4_103_1_3", "type_name": "单机游戏"},
        {"type_id": "4_103_1_1", "type_name": "射击游戏"},
        {"type_id": "4_103_1_2", "type_name": "竞技游戏"},
        {"type_id": "4_105", "type_name": "舞蹈"},
        {"type_id": "4_106", "type_name": "文化"},
        {"type_id": "4_107", "type_name": "生活"},
        {"type_id": "4_108", "type_name": "运动"},
        {"type_id": "4_102", "type_name": "音乐"},
        {"type_id": "4_104", "type_name": "二次元"}
    ]

    def _extract_middle_text(self, text, start_str, end_str):
        start_index = text.find(start_str)
        if start_index == -1:
            return ""
        end_index = text.find(end_str, start_index + len(start_str))
        if end_index == -1:
            return ""
        return text[start_index + len(start_str):end_index].replace("\\", "")

    def process_douyu(self):
        try:
            self.dyufdata = self.fetch(
                f'{self.referers["douyu"]}/api/cate/list',
                headers=self.headers[1]
            ).json()
            return ('douyu', [{'key': 'cate', 'name': '分类',
                               'value': [{'n': i['cate1Name'], 'v': str(i['cate1Id'])}
                                         for i in self.dyufdata['data']['cate1Info']]}])
        except Exception as e:
            print(f"douyu错误: {e}")
            return 'douyu', None

    def homeContent(self, filter):
        result = {}
        # 排序：抖音、快手、B站、虎牙、斗鱼、酷狗
        cateManual = {
            "抖音": "douyin",
            "快手": "ks",
            "B站": "bili",
            "虎牙": "huya",
            "斗鱼": "douyu",
            "酷狗": "kugou"
        }
        classes = []
        filters = {
            'huya': [{'key': 'cate', 'name': '分类',
                      'value': [{'n': '网游', 'v': '1'}, {'n': '单机', 'v': '2'},
                                {'n': '娱乐', 'v': '8'}, {'n': '手游', 'v': '3'}]}],
            'bili': [{'key': 'cate', 'name': '分类',
                      'value': [{'n': item['type_name'], 'v': item['type_id']}
                                for item in self.bili_categories]}],
            'ks': [{'key': 'cate', 'name': '分类',
                    'value': [{'n': item['type_name'], 'v': item['type_id']}
                              for item in self.ks_categories]}],
            'kugou': [{'key': 'cate', 'name': '分类',
                       'value': [{'n': item['type_name'], 'v': item['type_id']}
                                 for item in self.kg_categories]}],
            'douyin': [{'key': 'cate', 'name': '分类',
                        'value': [{'n': item['type_name'], 'v': item['type_id']}
                                  for item in self.dy_categories]}]
        }

        with ThreadPoolExecutor(max_workers=1) as executor:
            futures = {
                executor.submit(self.process_douyu): 'douyu'
            }

            for future in futures:
                platform, filter_data = future.result()
                if filter_data:
                    filters[platform] = filter_data

        for k in cateManual:
            classes.append({
                'type_name': k,
                'type_id': cateManual[k]
            })

        result['class'] = classes
        result['filters'] = filters
        return result

    def homeVideoContent(self):
        pass

    def categoryContent(self, tid, pg, filter, extend):
        vdata = []
        result = {}
        pagecount = 9999
        result['page'] = pg
        result['limit'] = 90
        result['total'] = 999999
        if 'bili' in tid:
            vdata, pagecount = self.biliContent(tid, pg, filter, extend, vdata)
        elif 'huya' in tid:
            vdata, pagecount = self.huyaContent(tid, pg, filter, extend, vdata)
        elif 'douyu' in tid:
            vdata, pagecount = self.douyuContent(tid, pg, filter, extend, vdata)
        elif 'ks' in tid:
            vdata, pagecount = self.ksContent(tid, pg, filter, extend, vdata)
        elif 'kugou' in tid:
            vdata, pagecount = self.kugouContent(tid, pg, filter, extend, vdata)
        elif 'douyin' in tid:
            vdata, pagecount = self.douyinContent(tid, pg, filter, extend, vdata)
        result['list'] = vdata
        result['pagecount'] = pagecount
        return result

    # ==================== 抖音直播 DouYin ====================
    def _dy_get_ttwid_cookie(self):
        if self.dy_ttwid:
            return self.dy_ttwid
        try:
            s = requests.Session()
            s.get("https://live.douyin.com/", headers={"User-Agent": self.dy_ua}, timeout=5)
            payload = {
                "region": "cn",
                "aid": 6383,
                "needFid": False,
                "service": "live.douyin.com",
                "migrate_info": {"tier": "", "from_model": "pc"},
            }
            res = s.post(
                "https://ttwid.bytedance.com/ttwid/union/register/",
                json=payload,
                headers={
                    "User-Agent": self.dy_ua,
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                },
                timeout=5
            )
            set_cookie = res.headers.get("Set-Cookie", "")
            match = re.search(r"ttwid=([^;]+)", set_cookie)
            if match:
                self.dy_ttwid = match.group(1)
                return self.dy_ttwid
        except Exception as e:
            print(f"抖音 ttwid 获取异常: {e}")
        return ""

    def _dy_split_category(self, path):
        seg = [s for s in path.split("_") if s]
        if len(seg) < 2:
            return {"partition": "105", "partitionType": "4"}
        return {"partition": seg[-1], "partitionType": seg[-2]}

    def _dy_extract_cdn_url(self, room):
        stream_url = room.get("stream_url", {}) if room else {}
        hls_map = stream_url.get("hls_pull_url_map") or {}
        flv_map = stream_url.get("flv_pull_url") or {}
        qualities = ["FULL_HD1", "HD1", "SD1", "SD2"]

        for q in qualities:
            if hls_map.get(q):
                return hls_map[q].replace("http://", "https://")

        for q in qualities:
            if flv_map.get(q):
                return flv_map[q].replace("http://", "https://")

        if stream_url.get("hls_pull_url"):
            return stream_url["hls_pull_url"].replace("http://", "https://")

        return None

    def douyinContent(self, tid, pg, filter, extend, vdata):
        try:
            cate_path = extend.get('cate', '4_103_1_2_1_1010014')
            p = max(0, int(pg or 1) - 1)
            ttwid = self._dy_get_ttwid_cookie()
            cat_info = self._dy_split_category(cate_path)
            
            params = {
                "aid": "6383",
                "app_name": "douyin_web",
                "live_id": "1",
                "device_platform": "web",
                "language": "zh-CN",
                "cookie_enabled": "true",
                "screen_width": "1280",
                "screen_height": "720",
                "browser_language": "zh-CN",
                "browser_platform": "Windows",
                "browser_name": "Chrome",
                "browser_version": "151.0.0.0",
                "os_name": "Windows",
                "os_version": "10",
                "count": "15",
                "offset": str(p * 15),
                "partition": cat_info["partition"],
                "partition_type": cat_info["partitionType"],
                "req_from": "2",
                "a_bogus": self.dy_a_bogus,
            }
            headers = {
                "User-Agent": self.dy_ua,
                "Referer": f"https://live.douyin.com/categorynew/{cate_path}",
            }
            if ttwid:
                headers["Cookie"] = f"ttwid={ttwid}"

            url = "https://live.douyin.com/webcast/web/partition/detail/room/v2/"
            resp = requests.get(url, params=params, headers=headers, timeout=8)
            if resp.status_code == 200:
                data = resp.json()
                items = data.get("data", {}).get("data", []) or []
                for it in items:
                    room = it.get("room", {}) or {}
                    owner = room.get("owner", {}) or {}
                    title = room.get("title") or owner.get("nickname") or "抖音直播"
                    cover = room.get("cover", {}).get("url_list", [""])[0] or owner.get("avatar_thumb", {}).get("url_list", [""])[0]
                    web_rid = room.get("owner_user_id") or owner.get("sec_uid") or room.get("id_str") or ""
                    user_count = room.get("user_count", 0)
                    play_url = self._dy_extract_cdn_url(room)
                    
                    if web_rid or play_url:
                        encoded_url = self.e64(play_url) if play_url else ""
                        vod_id = f"douyin@@{web_rid}@@{encoded_url}"
                        v = self.buildvod(
                            vod_id=vod_id,
                            vod_name=title,
                            vod_pic=cover,
                            vod_remarks=f"🔥{user_count}" if user_count else "抖音直播",
                            vod_actor=owner.get("nickname", ""),
                            style={"type": "rect", "ratio": 1.33}
                        )
                        vdata.append(v)
            return vdata, 9999
        except Exception as e:
            print(f"抖音分类解析失败: {e}")
            return vdata, 1

    def douyinDetail(self, ids):
        try:
            rid = ids[1] if len(ids) > 1 else ""
            direct_url = self.d64(ids[2]) if len(ids) > 2 and ids[2] else ""
            
            play_url = direct_url if direct_url else f"https://live.douyin.com/{rid}"
            
            vod = self.buildvod(
                vod_name=f"抖音直播 - {rid}",
                vod_content='欢迎观看抖音直播\n👖 关注裤佬TG频道 https://t.me/stymei',
                vod_play_from='👖裤佬独家聚合-抖音专线',
                vod_play_url=f"高清直播${play_url}"
            )
            return vod
        except Exception as e:
            return self.handle_exception(e)

    # ==================== B 站 ====================
    def biliContent(self, tid, pg, filter, extend, vdata):
        try:
            keyword = extend.get('cate', '舞蹈')
            url = f'{self.hosts["bili_search"]}/live?keyword={urllib.parse.quote(keyword)}&page={str(pg)}'
            
            detail = requests.get(url=url, headers=self.headers[0], timeout=10)
            detail.encoding = "utf-8"
            res = detail.text
            doc = BeautifulSoup(res, "lxml")

            soups = doc.find_all('div', class_="video-list-item")

            for vod in soups:
                names = vod.find('h3', class_="bili-live-card__info--tit")
                if not names:
                    continue
                name = names.text.strip().replace('直播中', '')

                room_id = names.find('a')['href']
                room_id = self._extract_middle_text(room_id, 'bilibili.com/', '?')

                pic = vod.find('img')['src'] if vod.find('img') else ''
                if pic and 'http' not in pic:
                    pic = "https:" + pic

                remarks = vod.find('a', class_="bili-live-card__info--uname")
                remark = remarks.text.strip() if remarks else ''

                v = self.buildvod(
                    vod_id=f"bili@@{room_id}",
                    vod_name=name,
                    vod_pic=pic,
                    vod_remarks=remark,
                    style={"type": "rect", "ratio": 1.33}
                )
                vdata.append(v)

            return vdata, 9999
        except Exception as e:
            print(f"B站分类内容获取错误: {e}")
            return vdata, 1

    # ==================== 虎牙 ====================
    def huyaContent(self, tid, pg, filter, extend, vdata):
        if extend.get('cate') and pg == '1' and 'click' not in tid:
            id = extend.get('cate')
            data = self.fetch(f'{self.referers[tid]}/liveconfig/game/bussLive?bussType={id}',
                              headers=self.headers[1]).json()
            for i in data['data']:
                v = self.buildvod(
                    vod_id=f"click_{tid}@@{int(i['gid'])}",
                    vod_name=i.get('gameFullName'),
                    vod_pic=f'https://huyaimg.msstatic.com/cdnimage/game/{int(i["gid"])}-MS.jpg',
                    vod_tag=1,
                    style={"type": "oval", "ratio": 1}
                )
                vdata.append(v)
            return vdata, 1
        else:
            gid = ''
            if 'click' in tid:
                ids = tid.split('_')[1].split('@@')
                tid = ids[0]
                gid = f'&gameId={ids[1]}'
            data = self.fetch(f'{self.hosts[tid][0]}/cache.php?m=LiveList&do=getLiveListByPage&tagAll=0{gid}&page={pg}',
                              headers=self.headers[1]).json()
            for i in data['data']['datas']:
                if i.get('profileRoom'):
                    v = self.buildvod(
                        f"{tid}@@{i['profileRoom']}",
                        i.get('introduction'),
                        i.get('screenshot'),
                        str(int(i.get('totalCount', '1')) / 10000) + '万',
                        0,
                        i.get('nick'),
                        style={"type": "rect", "ratio": 1.33}
                    )
                    vdata.append(v)
            return vdata, 9999

    # ==================== 斗鱼 ====================
    def douyuContent(self, tid, pg, filter, extend, vdata):
        if extend.get('cate') and pg == '1' and 'click' not in tid:
            for i in self.dyufdata['data']['cate2Info']:
                if str(i['cate1Id']) == extend['cate']:
                    v = self.buildvod(
                        vod_id=f"click_{tid}@@{i['cate2Id']}",
                        vod_name=i.get('cate2Name'),
                        vod_pic=i.get('icon'),
                        vod_remarks=i.get('count'),
                        vod_tag=1,
                        style={"type": "oval", "ratio": 1}
                    )
                    vdata.append(v)
            return vdata, 1
        else:
            path = f'/japi/weblist/apinc/allpage/6/{pg}'
            if 'click' in tid:
                ids = tid.split('_')[1].split('@@')
                tid = ids[0]
                path = f'/gapi/rkc/directory/mixList/2_{ids[1]}/{pg}'
            url = f'{self.hosts[tid]}{path}'
            data = self.fetch(url, headers=self.headers[1]).json()
            for i in data['data']['rl']:
                v = self.buildvod(
                    vod_id=f"{tid}@@{i['rid']}",
                    vod_name=i.get('rn'),
                    vod_pic=i.get('rs16'),
                    vod_year=str(int(i.get('ol', 1)) / 10000) + '万',
                    vod_remarks=i.get('nn'),
                    style={"type": "rect", "ratio": 1.33}
                )
                vdata.append(v)
            return vdata, 9999

    # ==================== 快手 ====================
    def ksContent(self, tid, pg, filter, extend, vdata):
        cate_id = extend.get('cate', 'kwai_live')
        page = int(pg or 1)
        try:
            want_game = str(cate_id).replace('game_', '') if str(cate_id).startswith('game_') else ''
            want_cat = str(cate_id).replace('cat_', '') if str(cate_id).startswith('cat_') else ''
            rows = []
            scan_pages = 8 if (want_game or want_cat) else 3
            for pp in range(page, page + scan_pages):
                resp = requests.post(
                    f'{self.hosts["kuaishou"]}/live_api/liveroom/reco',
                    json={'page': pp, 'count': 50},
                    headers=self.playheaders['ks'],
                    timeout=10
                )
                data = resp.json() if resp.status_code == 200 else {}
                rows += (((data or {}).get('data') or {}).get('list') or [])
            if rows:
                for it in rows:
                    try:
                        ls = it.get('liveStream') or {}
                        user = it.get('author') or it.get('user') or {}
                        game = it.get('gameInfo') or {}
                        if want_game and str(game.get('id') or '') != want_game:
                            continue
                        if want_cat and str(game.get('categoryAbbr') or '') != want_cat:
                            continue
                        sid = user.get('id') or ls.get('principalId') or ls.get('userId') or ls.get('id') or ''
                        if not sid:
                            continue
                        name = user.get('name') or user.get('userName') or ls.get('caption') or '快手直播'
                        pic = ls.get('poster') or ls.get('coverUrl') or user.get('avatar') or ''
                        remark = (game.get('name') + ' · ' if game.get('name') else '') + (ls.get('caption') or name or '直播')
                        info = {'sid': sid, 'liveStreamId': ls.get('id') or '', 'is_live': True, 'source': 'pc_reco_cache', 'anchor_name': name, 'title': name, 'pic': pic, 'm3u8_url_list': [], 'flv_url_list': [], 'backup': {}, 'error': ''}
                        self._ks_collect_streams_from_playurls(ls.get('playUrls'), info)
                        self._ks_cache['live:' + sid] = (time.time(), info)
                        v = self.buildvod(
                            vod_id=f"ks@@{self._ks_pack_vod_id(sid, info)}",
                            vod_name=name,
                            vod_pic=pic,
                            vod_remarks=str(remark),
                            style={"type": "rect", "ratio": 1.33}
                        )
                        vdata.append(v)
                    except Exception:
                        continue
        except Exception as e:
            print(f"快手分类获取失败: {e}")
        return vdata, 9999

    def _ks_collect_streams_from_playurls(self, playurls, info):
        if not playurls:
            return
        reps = []
        try:
            if isinstance(playurls, dict):
                h264 = playurls.get('h264') or playurls.get('hevc') or {}
                reps = (((h264.get('adaptationSet') or {}).get('representation')) or [])
            elif isinstance(playurls, list):
                for x in playurls:
                    reps += (((x.get('adaptationSet') or {}).get('representation')) or [])
        except Exception:
            reps = []
        for r in reps:
            if not isinstance(r, dict):
                continue
            url = r.get('url') or r.get('playUrl') or r.get('hlsPlayUrl') or ''
            if not url:
                continue
            item = {'url': url, 'bitrate': r.get('bitrate') or r.get('vbitrate') or 0, 'name': r.get('name') or r.get('shortName') or r.get('qualityType') or ''}
            if 'm3u8' in url.lower():
                info.setdefault('m3u8_url_list', []).append(item)
            elif 'flv' in url.lower() or url.startswith('http'):
                info.setdefault('flv_url_list', []).append(item)

    def _ks_pack_vod_id(self, sid, info):
        try:
            mini = {
                'sid': sid,
                'is_live': bool(info.get('is_live')),
                'source': info.get('source') or 'packed',
                'liveStreamId': info.get('liveStreamId') or '',
                'anchor_name': info.get('anchor_name') or '',
                'title': info.get('title') or info.get('anchor_name') or '',
                'pic': info.get('pic') or '',
                'flv_url_list': (info.get('flv_url_list') or [])[:8],
                'm3u8_url_list': (info.get('m3u8_url_list') or [])[:8],
                'backup': info.get('backup') or {},
                'error': ''
            }
            raw = json.dumps(mini, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
            return 'kwai+json://' + base64.urlsafe_b64encode(raw).decode('utf-8').rstrip('=')
        except Exception:
            return 'kwai://' + sid

    # ==================== 酷狗 ====================
    def kugouContent(self, tid, pg, filter, extend, vdata):
        cate_id = extend.get('cate', '0')
        page = int(pg or 1)
        limit = 50
        try:
            if cate_id == "0":
                vdata = self._kg_get_recommend_list(page, limit)
            elif cate_id == "game":
                vdata = self._kg_get_game_list(page, limit)
            elif cate_id == "music":
                vdata = self._kg_get_music_list(page, limit)
            elif cate_id in ["dance", "face", "acg", "chinese"]:
                cid_map = {"dance": "7024", "face": "1009", "acg": "3007", "chinese": "1001"}
                vdata = self._kg_get_list_v4(page, limit, cid_map[cate_id])
        except Exception as e:
            print(f"酷狗分类获取失败: {e}")
        return vdata, 9999

    def _kg_save_room_cache(self, room_id, nickName, imgPath, cityName="", tagText="", viewerNum=0):
        self.kg_room_cache[str(room_id)] = {
            "roomId": str(room_id),
            "nickName": nickName,
            "imgPath": imgPath,
            "cityName": cityName,
            "tagText": tagText,
            "viewerNum": viewerNum
        }

    def _kg_get_recommend_list(self, page, limit):
        videos = []
        api_url = f"{self.kg_api_host}/mfanxing-home/h5/cdn/room/index/list"
        params = {
            "pid": "0", "kugouId": "0", "doubleLiveFirst": "1", "sysVersion": "0",
            "platform": "7", "device": "d5e6f3453f454395fb41be10305af348", "channel": "0",
            "version": "99999", "longitude": "0", "latitude": "0", "appid": "1010",
            "liveTypeFilter": "0", "isNew": "0", "entranceType": "0", "uiMode": "0",
            "page": page, "areaName": ""
        }
        resp = self.fetch(api_url, params=params, headers=self.playheaders['kugou']).json()
        if resp.get('code') == 0:
            for item in resp.get('data', {}).get('list', []):
                v = self._kg_normalize_room_item(item)
                if v: videos.append(v)
        return videos

    def _kg_get_game_list(self, page, limit):
        videos = []
        api_url = f"{self.kg_api_host}/fxservice/activity/entrance/game/square/rooms"
        params = {
            "gameCode": "ALL_GAME", "appid": "1010", "version": "99999",
            "platform": "7", "device": "d5e6f3453f454395fb41be10305af348",
            "channel": "0", "pageSize": limit, "pageNum": page, "userKugouId": "0", "pid": "0"
        }
        resp = self.fetch(api_url, params=params, headers=self.playheaders['kugou']).json()
        if resp.get('code') == 0:
            for item in resp.get('data', {}).get('roomList', []):
                v = self._kg_normalize_game_item(item)
                if v: videos.append(v)
        return videos

    def _kg_get_music_list(self, page, limit):
        videos = []
        api_url = "https://fxservice3.kugou.com/mfanxing-home/h5/song/recommend/star/list"
        params = {"page": page, "platform": "7", "version": "99999", "device": "d5e6f3453f454395fb41be10305af348", "kugouId": "0", "pid": "0", "type": "8"}
        resp = self.fetch(api_url, params=params, headers=self.playheaders['kugou']).json()
        if resp.get('code') == 0:
            for song_item in resp.get('data', {}).get('list', []):
                song_name = song_item.get('songName', '')
                for star in song_item.get('starList', []):
                    star['_song_name'] = song_name
                    v = self._kg_normalize_music_item(star)
                    if v: videos.append(v)
        return videos

    def _kg_get_list_v4(self, page, limit, cid):
        videos = []
        api_url = "https://fx2.service.kugou.com/mfanxing-home/h5/cdn/room/index/list_v4"
        params = {
            "pid": "0", "kugouId": "0", "doubleLiveFirst": "1", "sysVersion": "0",
            "platform": "7", "device": "d5e6f3453f454395fb41be10305af348", "channel": "0",
            "version": "99999", "longitude": "0", "latitude": "0", "appid": "1010",
            "liveTypeFilter": "0", "isNew": "0", "entranceType": "0", "uiMode": "0",
            "page": page, "cid": cid
        }
        resp = self.fetch(api_url, params=params, headers=self.playheaders['kugou']).json()
        if resp.get('code') == 0:
            for item in resp.get('data', {}).get('list', []):
                room_data = item.get('data', {})
                if room_data:
                    v = self._kg_normalize_v4_item(room_data, cid)
                    if v: videos.append(v)
        return videos

    def _kg_normalize_room_item(self, raw):
        if not isinstance(raw, dict): return None
        room_id = str(raw.get('roomId', ''))
        if not room_id: return None
        nickname = raw.get('nickName', f"主播{room_id}")
        pic = raw.get('imgPath', '')
        if pic and not pic.startswith('http'): pic = 'http:' + pic
        tag_text, city_name = '', ''
        for tag in raw.get('tags', []):
            if tag.get('tagId') == 26: city_name = tag.get('tagName', '')
            elif tag.get('tagName'):
                tag_text += ("," + tag.get('tagName')) if tag_text else tag.get('tagName')
        online = raw.get('viewerNum', 0)
        self._kg_save_room_cache(room_id, nickname, pic, city_name, tag_text, online)
        online_str = f"🔥{online}" if online else ''
        category_name = raw.get('category', [{}])[0].get('name', '') if raw.get('category') else ''
        remark = ' '.join(filter(None, [category_name, tag_text, online_str]))
        return self.buildvod(f"kugou@@{room_id}", nickname, pic, remark, style={"type": "rect", "ratio": 1.33})

    def _kg_normalize_game_item(self, raw):
        if not isinstance(raw, dict): return None
        room_id = str(raw.get('roomId', ''))
        if not room_id: return None
        star_name = raw.get('starName', f"主播{room_id}")
        pic = raw.get('starCover', '')
        if pic and not pic.startswith('http'): pic = 'http://p3.fx.kgimg.com' + pic
        game_name = raw.get('gameName', '')
        status_text = raw.get('statusText', '')
        main_title = raw.get('bottomData', {}).get('mainTitle', '')
        remark = ' '.join(filter(None, [f"🎮{game_name}" if game_name else '', status_text, main_title])) or '一起玩'
        self._kg_save_room_cache(room_id, star_name, pic, "", game_name, 0)
        return self.buildvod(f"kugou@@{room_id}", star_name, pic, remark, style={"type": "rect", "ratio": 1.33})

    def _kg_normalize_music_item(self, raw):
        if not isinstance(raw, dict): return None
        room_id = str(raw.get('roomId', ''))
        if not room_id: return None
        nickname = raw.get('nickName', f"主播{room_id}")
        pic = raw.get('imgPath', '')
        if pic and not pic.startswith('http'): pic = 'http:' + pic
        song_name = raw.get('_song_name', '')
        city_name = raw.get('cityName', '')
        tag_text = raw.get('tags', [{}])[0].get('tagName', '') if raw.get('tags') else ''
        online = raw.get('getViewerNum', 0)
        remark = ' '.join(filter(None, [f"🎵{song_name}" if song_name else '', city_name, tag_text, f"🔥{online}" if online else ''])) or '音乐直播'
        self._kg_save_room_cache(room_id, nickname, pic, city_name, tag_text, online)
        return self.buildvod(f"kugou@@{room_id}", nickname, pic, remark, style={"type": "rect", "ratio": 1.33})

    def _kg_normalize_v4_item(self, raw, cid):
        if not isinstance(raw, dict): return None
        room_id = str(raw.get('roomId', ''))
        if not room_id: return None
        nickname = raw.get('nickName', f"主播{room_id}")
        pic = raw.get('imgPath', '')
        if pic and not pic.startswith('http'): pic = 'http:' + pic
        city_name = raw.get('cityName', '')
        tag_text = raw.get('tags', [{}])[0].get('tagName', '') if raw.get('tags') else ''
        online = raw.get('getViewerNum', 0)
        label_title = raw.get('labelV2', {}).get('title', '') if isinstance(raw.get('labelV2'), dict) else ''
        cid_names = {"7024": "舞蹈", "1009": "颜值", "3007": "酷次元", "1001": "新秀"}
        cate_name = cid_names.get(str(cid), "")
        remark = ' '.join(filter(None, [cate_name, label_title, city_name, tag_text, f"🔥{online}" if online else ''])) or f'{cate_name}直播'
        self._kg_save_room_cache(room_id, nickname, pic, city_name, tag_text, online)
        return self.buildvod(f"kugou@@{room_id}", nickname, pic, remark, style={"type": "rect", "ratio": 1.33})

    # ==================== 详情调度 ====================
    def detailContent(self, ids):
        ids = ids[0].split('@@')
        if ids[0] == 'bili':
            vod = self.biliDetail(ids)
        elif ids[0] == 'huya':
            vod = self.huyaDetail(ids)
        elif ids[0] == 'douyu':
            vod = self.douyuDetail(ids)
        elif ids[0] == 'ks':
            vod = self.ksDetail(ids)
        elif ids[0] == 'kugou':
            vod = self.kugouDetail(ids)
        elif ids[0] == 'douyin':
            vod = self.douyinDetail(ids)
        return {'list': [vod]}

    def biliDetail(self, ids):
        try:
            room_id = ids[1]
            url = f'{self.hosts["bili_api"]}/xlive/web-room/v2/index/getRoomPlayInfo?room_id={room_id}&platform=web&protocol=0,1&format=0,1,2&codec=0,1'
            detail = requests.get(url=url, headers=self.headers[0], timeout=10)
            data = detail.json()

            if data.get('code') != 0:
                return self.handle_exception(Exception("获取房间流信息失败"))

            vod = self.buildvod(
                vod_name=f"B站直播 - {room_id}",
                vod_content='欢迎观看哔哩直播\n👖 关注裤佬TG频道 https://t.me/stymei',
                vod_play_from='👖裤佬独家聚合-哔哩专线'
            )

            streams = data['data'].get('playurl_info', {}).get('playurl', {}).get('stream', [])
            play_lines = []
            line_idx = 0
            for stream_item in streams:
                for fmt in stream_item.get('format', []):
                    for codec in fmt.get('codec', []):
                        url_infos = codec.get('url_info', [])
                        if not url_infos:
                            continue
                        for ui in url_infos:
                            host = ui.get('host', '')
                            base_url = codec.get('base_url', '')
                            extra = ui.get('extra', '')
                            full_url = host + base_url + extra
                            if not full_url:
                                continue
                            line_idx += 1
                            play_lines.append(f"线路{line_idx}${full_url}")
                        break
                    if play_lines:
                        break
                if play_lines:
                    break

            if not play_lines:
                return self.handle_exception(Exception("无法获取B站播放地址"))

            vod['vod_play_url'] = '#'.join(play_lines)
            return vod

        except Exception as e:
            print(f"B站详情错误: {e}")
            return self.handle_exception(e)

    def huyaDetail(self, ids):
        try:
            room_id = ids[1]
            api_url = f'{self.hosts[ids[0]][1]}/cache.php?m=Live&do=profileRoom&roomid={room_id}'
            res = self.fetch(api_url, headers=self.headers[0])
            
            if res.status_code != 200:
                return self.handle_exception(Exception(f"API请求失败: {res.status_code}"))
            
            data = res.json()
            if not data or not data.get('data'):
                return self.handle_exception(Exception("房间数据为空"))
            
            room_data = data['data']
            uid = room_data.get('profileInfo', {}).get('uid')
            stream_info = room_data.get('stream', {})
            live_data = room_data.get('liveData', {})
            
            if not uid:
                return self.handle_exception(Exception("缺少uid"))
            
            base_stream_list = stream_info.get('baseSteamInfoList', [])
            if not base_stream_list:
                return self.handle_exception(Exception("无直播流信息"))
            
            base_stream = base_stream_list[0]
            stream_name = base_stream.get('sStreamName')
            if not stream_name:
                return self.handle_exception(Exception("无法获取streamName"))
            
            vod = self.buildvod(
                vod_name=live_data.get('introduction', '虎牙直播'),
                type_name=live_data.get('gameFullName', ''),
                vod_director=live_data.get('nick', ''),
                vod_remarks=live_data.get('contentIntro', ''),
                vod_content='欢迎观看虎牙直播\n👖 关注裤佬TG频道 https://t.me/stymei'
            )
            
            cdn_list = []
            for stream in base_stream_list:
                cdn_type = stream.get('sCdnType', 'AL')
                flv_url = stream.get('sFlvUrl', '')
                hls_url = stream.get('sHlsUrl', '')
                stream_name_cdn = stream.get('sStreamName', stream_name)
                
                if flv_url:
                    cdn_list.append({
                        'cdn': cdn_type,
                        'flv_base': flv_url,
                        'hls_base': hls_url,
                        'stream_name': stream_name_cdn,
                        'priority': stream.get('iWebPriorityRate', 0)
                    })
            
            cdn_list.sort(key=lambda x: x['priority'], reverse=True)
            rate_array = stream_info.get('rateArray', [])
            
            if not rate_array and 'vMultiStreamInfo' in room_data:
                rate_array = room_data['vMultiStreamInfo']
            
            if not rate_array:
                rate_array = [
                    {'sDisplayName': '蓝光4M', 'iBitRate': 4000},
                    {'sDisplayName': '蓝光', 'iBitRate': 3000},
                    {'sDisplayName': '超清', 'iBitRate': 2000},
                    {'sDisplayName': '高清', 'iBitRate': 1200},
                    {'sDisplayName': '流畅', 'iBitRate': 500}
                ]
            
            filtered_rates = []
            seen_bitrates = set()
            
            for rate in rate_array:
                bit_rate = rate.get('iBitRate', 0)
                name = rate.get('sDisplayName', '')
                if bit_rate in seen_bitrates:
                    continue
                if bit_rate == 2000 and ('高清' in name or '720' in name):
                    name = '超清'
                elif bit_rate == 1200 and ('标清' in name or '480' in name):
                    name = '高清'
                elif bit_rate == 2000 and name == '原画':
                    name = '超清'
                
                seen_bitrates.add(bit_rate)
                filtered_rates.append({
                    'sDisplayName': name,
                    'iBitRate': bit_rate
                })
            
            sorted_rates = sorted(filtered_rates, key=lambda x: x['iBitRate'], reverse=True)
            
            play_lines = []
            line_names = []
            
            for cdn_idx, cdn in enumerate(cdn_list[:3]):
                cdn_name = cdn['cdn']
                line_names.append(f"👖裤佬独家聚合-线路{cdn_idx + 1}({cdn_name})")
                
                qualities = []
                for rate in sorted_rates:
                    quality_name = rate['sDisplayName']
                    bit_rate = rate['iBitRate']
                    quality_url = self._generate_huya_play_url(cdn, uid, stream_name, bit_rate)
                    qualities.extend([quality_name, quality_url])
                
                encoded_qualities = self.e64(json.dumps(qualities))
                play_lines.append(f"{live_data.get('introduction', '直播')}${ids[0]}@@{encoded_qualities}")
            
            vod['vod_play_from'] = "$$$".join(line_names)
            vod['vod_play_url'] = "$$$".join(play_lines)
            return vod
            
        except Exception as e:
            return self.handle_exception(e)
    
    def _generate_huya_play_url(self, cdn, uid, stream_name, bit_rate):
        flv_base = cdn['flv_base']
        stream = cdn['stream_name']
        timestamp = int(time.time())
        seqid = f"{uid}{timestamp}"
        ss = hashlib.md5(f"{seqid}|huya_adr|102".encode()).hexdigest()
        ws_time = hex(timestamp + 21600)[2:]
        ws_secret = hashlib.md5(f"DWq8BcJ3h6DJt6TY_{uid}_{stream_name}_{ss}_{ws_time}".encode()).hexdigest()
        base_url = f"{flv_base}/{stream}.flv"
        ratio_param = f"ratio={bit_rate}" if bit_rate > 0 else "ratio=2000"
        
        play_url = (
            f"{base_url}?{ratio_param}&wsSecret={ws_secret}&wsTime={ws_time}"
            f"&ctype=huya_adr&seqid={seqid}&uid={uid}"
            f"&fs=bgct&ver=1&t=102"
        )
        return play_url

    def douyuDetail(self, ids):
        try:
            channel = ids[1]
            headers = self.gethr(0, zr=f'{self.hosts[ids[0]]}/{channel}')
            session = {}
            
            try:
                home_res = self.fetch(f'{self.hosts[ids[0]]}/{channel}', headers=headers)
                if home_res.headers.get('Set-Cookie'):
                    cookie_str = home_res.headers.get('Set-Cookie')
                    did_match = re.search(r'dy_did=([a-f0-9]{32})', cookie_str)
                    device_id = did_match.group(1) if did_match else self._generate_random_hex(32)
                else:
                    device_id = self._generate_random_hex(32)
            except:
                device_id = self._generate_random_hex(32)
            
            session['dy_did'] = device_id
            session['mantine-color-scheme-value'] = 'light'
            
            betard_res = self.fetch(f'{self.hosts[ids[0]]}/betard/{channel}', headers=headers).json()
            if not betard_res or not betard_res.get('room'):
                return self.handle_exception(Exception("获取房间信息失败"))
            
            room_info = betard_res['room']
            vname = room_info.get('room_name', '斗鱼直播')
            
            vod = self.buildvod(
                vod_name=vname,
                vod_remarks=room_info.get('second_lvl_name', ''),
                vod_director=room_info.get('nickname', ''),
                vod_content='欢迎观看斗鱼直播\n👖 关注裤佬TG频道 https://t.me/stymei'
            )
            
            sec_url = f"{self.hosts[ids[0]]}/wgapi/livenc/liveweb/websec/getEncryption?did={device_id}"
            sec_res = self.fetch(sec_url, headers=headers).json()
            
            if not sec_res or sec_res.get('error') != 0:
                return self.handle_exception(Exception("获取加密密钥失败"))
            
            security_data = sec_res['data']
            secret_key = security_data.get('key')
            random_str = security_data.get('rand_str')
            enc_time = security_data.get('enc_time', 1)
            enc_data = security_data.get('enc_data')
            
            current_time = int(time.time())
            current = random_str
            for _ in range(enc_time):
                current = hashlib.md5(f"{current}{secret_key}".encode()).hexdigest()
            
            signature = hashlib.md5(f"{current}{secret_key}{channel}{current_time}".encode()).hexdigest()
            
            play_payload = {
                'enc_data': enc_data,
                'tt': str(current_time),
                'did': device_id,
                'auth': signature,
                'cdn': '',
                'rate': '',
                'hevc': '0',
                'fa': '0',
                'ive': '0'
            }
            
            play_api = f"{self.hosts[ids[0]]}/lapi/live/getH5PlayV1/{channel}"
            play_headers = headers.copy()
            cookie_str = '; '.join([f"{k}={v}" for k, v in session.items()])
            play_headers['Cookie'] = cookie_str
            play_headers['Content-Type'] = 'application/x-www-form-urlencoded'
            
            play_res = requests.post(play_api, data=play_payload, headers=play_headers, timeout=10).json()
            
            if not play_res or play_res.get('error') != 0:
                play_res = self._try_legacy_douyu_api(channel, device_id, signature, current_time, play_headers)
                if not play_res:
                    return self.handle_exception(Exception("获取播放地址失败"))
            
            stream_info = play_res.get('data', {})
            rtmp_live = stream_info.get('rtmp_live', '')
            if rtmp_live:
                did_match = re.search(r'did=([a-f0-9]{32})', rtmp_live)
                if did_match and did_match.group(1) != device_id:
                    device_id = did_match.group(1)
                    session['dy_did'] = device_id
                    play_payload['did'] = device_id
                    play_res = requests.post(play_api, data=play_payload, headers=play_headers, timeout=10).json()
                    if play_res and play_res.get('error') == 0:
                        stream_info = play_res.get('data', {})
            
            stream_url = None
            if stream_info.get('rtmp_url') and stream_info.get('rtmp_live'):
                stream_url = f"{stream_info['rtmp_url']}/{stream_info['rtmp_live']}"
            elif stream_info.get('hls_url'):
                stream_url = stream_info['hls_url']
            
            if not stream_url:
                return self.handle_exception(Exception("无法获取播放地址"))
            
            multirates = stream_info.get('multirates', [])
            qualities = []
            
            if multirates:
                sorted_rates = sorted(multirates, key=lambda x: x.get('bit', 0), reverse=True)
                for rate in sorted_rates:
                    bit_rate = rate.get('rate', -1)
                    name = rate.get('name', f"{bit_rate}P")
                    qualities.extend([name, f"#{bit_rate}"])
            else:
                qualities = ['原画', '#-1']
            
            session_info = {
                'channel': channel,
                'device_id': device_id,
                'secret_key': secret_key,
                'random_str': random_str,
                'enc_time': enc_time,
                'enc_data': enc_data
            }
            encoded_session = self.e64(json.dumps(session_info))
            encoded_qualities = self.e64(json.dumps(qualities))
            
            vod['vod_play_from'] = '👖裤佬独家聚合-斗鱼直播'
            vod['vod_play_url'] = f"{vname}${ids[0]}@@{encoded_qualities}@@{encoded_session}"
            
            return vod
            
        except Exception as e:
            return self.handle_exception(e)

    def ksDetail(self, ids):
        try:
            packed_str = ids[1]
            info = {}
            if packed_str.startswith('kwai+json://'):
                b = packed_str.replace('kwai+json://', '')
                b += '=' * ((4 - len(b) % 4) % 4)
                info = json.loads(base64.urlsafe_b64decode(b.encode('utf-8')).decode('utf-8'))
            
            sid = info.get('sid', '3x4546nfkivjsxe')
            title = info.get('title') or info.get('anchor_name') or f'快手直播 {sid}'
            
            arr = (info.get('flv_url_list') or []) + (info.get('m3u8_url_list') or [])
            try:
                arr = sorted(arr, key=lambda x: int(x.get('bitrate') or 0), reverse=True)
            except Exception:
                pass
            
            parts = []
            for i, x in enumerate(arr[:8]):
                url = x.get('url') if isinstance(x, dict) else str(x)
                if not url:
                    continue
                name = (x.get('name') if isinstance(x, dict) else '') or ('线路%d' % (i + 1))
                parts.append(f"{name}${url}")
            
            play_url = '#'.join(parts) if parts else f"播放$https://live.kuaishou.com/u/{sid}"
            
            vod = self.buildvod(
                vod_name=title,
                vod_pic=info.get('pic', ''),
                vod_actor=info.get('anchor_name', ''),
                vod_remarks='直播中' if info.get('is_live') else '未开播',
                vod_content='欢迎观看快手直播\n👖 关注裤佬TG频道 https://t.me/stymei',
                vod_play_from='👖裤佬独家聚合-快手直播',
                vod_play_url=play_url
            )
            return vod
        except Exception as e:
            return self.handle_exception(e)

    def kugouDetail(self, ids):
        try:
            room_id = ids[1]
            if room_id in self.kg_room_cache:
                room_info = self.kg_room_cache[room_id]
            else:
                room_info = {
                    'nickName': f'主播{room_id}',
                    'cityName': '', 'tagText': '', 'viewerNum': 0, 'imgPath': ''
                }
            
            nickname = room_info.get('nickName', f'主播{room_id}')
            pic = room_info.get('imgPath', '')
            if pic and not pic.startswith('http'): pic = 'http:' + pic
            
            api_url = "https://fx1.service.kugou.com/video/mo/live/pull/h5/v3/streamaddr"
            params = {
                "roomId": room_id, "platform": "12", "version": "1000",
                "streamType": "3-6", "liveType": "1", "ch": "fx",
                "ua": "fx-mobile-h5", "kugouId": "0", "layout": "1", "appid": "2815", "token": ""
            }
            resp = self.fetch(api_url, params=params, headers=self.playheaders['kugou']).json()
            
            play_url = ""
            if resp.get('code') == 0 and resp.get('data', {}).get('status') != 0:
                live_data = resp.get('data', {}).get('vertical') or resp.get('data', {}).get('horizontal')
                if live_data and isinstance(live_data, list) and len(live_data) > 0:
                    source = live_data[0]
                    if source.get('hls'): play_url = source['hls'][0]
                    elif source.get('httpshls'): play_url = source['httpshls'][0]
                    elif source.get('flv'): play_url = source['flv'][0]

            vod = self.buildvod(
                vod_name=f"{nickname}的直播间",
                vod_pic=pic,
                vod_actor=nickname,
                vod_content=f"酷狗直播 房间号：{room_id}\n👖 关注裤佬TG频道 https://t.me/stymei",
                vod_play_from="👖裤佬独家聚合-酷狗直播",
                vod_play_url=f"直播${play_url}" if play_url else f"未开播${self.excepturl}"
            )
            return vod
        except Exception as e:
            return self.handle_exception(e)

    # ==================== 搜索逻辑 ====================
    def searchContent(self, key, quick, pg="1"):
        vdata = []
        try:
            url = f'{self.hosts["bili_search"]}/live?keyword={urllib.parse.quote(key)}&page={str(pg)}'
            detail = requests.get(url=url, headers=self.headers[0], timeout=10)
            detail.encoding = "utf-8"
            res = detail.text
            doc = BeautifulSoup(res, "lxml")

            soups = doc.find_all('div', class_="video-list-item")

            for vod in soups:
                names = vod.find('h3', class_="bili-live-card__info--tit")
                if not names: continue
                name = names.text.strip().replace('直播中', '')
                room_id = names.find('a')['href']
                room_id = self._extract_middle_text(room_id, 'bilibili.com/', '?')
                pic = vod.find('img')['src'] if vod.find('img') else ''
                if pic and 'http' not in pic: pic = "https:" + pic
                remarks = vod.find('a', class_="bili-live-card__info--uname")
                remark = remarks.text.strip() if remarks else ''

                v = self.buildvod(vod_id=f"bili@@{room_id}", vod_name=name, vod_pic=pic, vod_remarks=remark)
                vdata.append(v)

            kg_url = "https://fxpc.kugou.com/fx/search/live"
            kg_params = {"keyword": key, "page": pg, "pagesize": 20, "platform": "pc", "version": "1.0"}
            kg_resp = self.fetch(kg_url, params=kg_params, headers=self.playheaders['kugou']).json()
            if kg_resp.get('errcode') == 0:
                for item in kg_resp.get('data', {}).get('list', []):
                    v = self._kg_normalize_room_item(item)
                    if v: vdata.append(v)

            return {'list': vdata, 'page': pg, 'pagecount': 9999, 'limit': 90, 'total': 999999}
        except Exception as e:
            print(f"搜索处理异常: {e}")
            return {'list': []}

    # ==================== 播放调度 ====================
    def playerContent(self, flag, id, vipFlags):
        try:
            ids = id.split('@@')
            p = 0
            if ids[0] == 'bili':
                url = ids[1]
            elif ids[0] == 'huya':
                p, url = self.huyaplay(ids)
            elif ids[0] == 'douyu':
                p, url = self.douyuplay(ids)
            elif ids[0] == 'ks':
                url = ids[1]
            elif ids[0] == 'kugou':
                url = ids[1]
            elif ids[0] == 'douyin':
                url = self.d64(ids[2]) if len(ids) > 2 and ids[2] else f"https://live.douyin.com/{ids[1]}"
            else:
                url = id
                
            header = self.playheaders.get(ids[0], self.headers[0])
            return {'parse': p, 'url': url, 'header': header}
        except Exception as e:
            return {'parse': 1, 'url': self.excepturl, 'header': self.headers[0]}

    # ==================== 虎牙/斗鱼 辅助播放 ====================
    def huyaplay(self, ids):
        try:
            decoded = json.loads(self.d64(ids[1]))
            return 0, decoded
        except Exception as e:
            print(f"虎牙播放解析错误: {e}")
            return 1, self.excepturl

    def douyuplay(self, ids):
        try:
            if len(ids) < 3:
                decoded = json.loads(self.d64(ids[1]))
                return 0, decoded
            
            qualities = json.loads(self.d64(ids[1]))
            session_info = json.loads(self.d64(ids[2]))
            
            channel = session_info['channel']
            device_id = session_info['device_id']
            secret_key = session_info['secret_key']
            random_str = session_info['random_str']
            enc_time = session_info['enc_time']
            enc_data = session_info['enc_data']
            
            result = []
            for i in range(0, len(qualities), 2):
                name = qualities[i]
                rate_marker = qualities[i + 1]
                rate = int(rate_marker[1:]) if rate_marker.startswith('#') else -1
                
                play_url = self._get_douyu_play_url(
                    channel, device_id, secret_key, random_str, 
                    enc_time, enc_data, rate
                )
                if play_url:
                    result.extend([name, play_url])
            
            if not result:
                return 1, self.excepturl
            
            return 0, result
        except Exception as e:
            print(f"斗鱼播放解析错误: {e}")
            return 1, self.excepturl

    def _generate_random_hex(self, length):
        hex_chars = '0123456789abcdef'
        return ''.join(random.choice(hex_chars) for _ in range(length))

    def _try_legacy_douyu_api(self, channel, device_id, signature, timestamp, headers):
        try:
            legacy_payload = {
                'did': device_id, 'tt': str(timestamp), 'sign': signature,
                'cdn': '', 'rate': '-1', 'ver': 'Douyu_223061205',
                'iar': '1', 'ive': '1', 'hevc': '0', 'fa': '0'
            }
            legacy_api = f"https://www.douyu.com/lapi/live/getH5Play/{channel}"
            res = requests.post(legacy_api, data=legacy_payload, headers=headers, timeout=10)
            return res.json() if res.status_code == 200 else None
        except:
            return None

    def _get_douyu_play_url(self, channel, device_id, secret_key, random_str, enc_time, enc_data, rate):
        try:
            current_time = int(time.time())
            current = random_str
            for _ in range(enc_time):
                current = hashlib.md5(f"{current}{secret_key}".encode()).hexdigest()
            
            signature = hashlib.md5(f"{current}{secret_key}{channel}{current_time}".encode()).hexdigest()
            
            play_payload = {
                'enc_data': enc_data, 'tt': str(current_time), 'did': device_id,
                'auth': signature, 'cdn': '', 'rate': str(rate) if rate > 0 else '',
                'hevc': '0', 'fa': '0', 'ive': '0'
            }
            play_api = f"https://www.douyu.com/lapi/live/getH5PlayV1/{channel}"
            headers = {
                'User-Agent': self.headers[0]['User-Agent'],
                'Referer': f'https://www.douyu.com/{channel}',
                'Origin': 'https://www.douyu.com',
                'Cookie': f'dy_did={device_id}; mantine-color-scheme-value=light',
                'Content-Type': 'application/x-www-form-urlencoded'
            }
            play_res = requests.post(play_api, data=play_payload, headers=headers, timeout=10).json()
            if not play_res or play_res.get('error') != 0:
                return self._get_douyu_play_url_legacy(channel, device_id, signature, current_time, rate)
            
            stream_info = play_res.get('data', {})
            if stream_info.get('rtmp_live'):
                did_match = re.search(r'did=([a-f0-9]{32})', stream_info['rtmp_live'])
                if did_match and did_match.group(1) != device_id:
                    return self._get_douyu_play_url(channel, did_match.group(1), secret_key, random_str, enc_time, enc_data, rate)
            
            if stream_info.get('rtmp_url') and stream_info.get('rtmp_live'):
                return f"{stream_info['rtmp_url']}/{stream_info['rtmp_live']}"
            elif stream_info.get('hls_url'):
                return stream_info['hls_url']
            
            return None
        except Exception as e:
            print(f"获取斗鱼播放URL失败: {e}")
            return None

    def _get_douyu_play_url_legacy(self, channel, device_id, signature, timestamp, rate):
        try:
            legacy_payload = {
                'did': device_id, 'tt': str(timestamp), 'sign': signature,
                'cdn': '', 'rate': str(rate) if rate > 0 else '-1',
                'ver': 'Douyu_223061205', 'iar': '1', 'ive': '1', 'hevc': '0', 'fa': '0'
            }
            legacy_api = f"https://www.douyu.com/lapi/live/getH5Play/{channel}"
            headers = {
                'User-Agent': self.headers[0]['User-Agent'],
                'Referer': f'https://www.douyu.com/{channel}',
                'Cookie': f'dy_did={device_id}',
                'Content-Type': 'application/x-www-form-urlencoded'
            }
            res = requests.post(legacy_api, data=legacy_payload, headers=headers, timeout=10)
            if res.status_code == 200:
                data = res.json()
                if data.get('error') == 0:
                    stream_info = data.get('data', {})
                    if stream_info.get('rtmp_url') and stream_info.get('rtmp_live'):
                        return f"{stream_info['rtmp_url']}/{stream_info['rtmp_live']}"
            return None
        except:
            return None

    def localProxy(self, param):
        return None

    def e64(self, text):
        try:
            text_bytes = text.encode('utf-8')
            encoded_bytes = b64encode(text_bytes)
            return encoded_bytes.decode('utf-8')
        except Exception as e:
            print(f"Base64编码错误: {str(e)}")
            return ""

    def d64(self, encoded_text):
        try:
            encoded_bytes = encoded_text.encode('utf-8')
            decoded_bytes = b64decode(encoded_bytes)
            return decoded_bytes.decode('utf-8')
        except Exception as e:
            print(f"Base64解码错误: {str(e)}")
            return ""

    def buildvod(self, vod_id='', vod_name='', vod_pic='', vod_year='', vod_tag='', vod_remarks='', style='',
                 type_name='', vod_area='', vod_actor='', vod_director='',
                 vod_content='', vod_play_from='', vod_play_url=''):
        vod = {
            'vod_id': vod_id, 'vod_name': vod_name, 'vod_pic': vod_pic,
            'vod_year': vod_year, 'vod_tag': 'folder' if vod_tag else '',
            'vod_remarks': vod_remarks, 'style': style, 'type_name': type_name,
            'vod_area': vod_area, 'vod_actor': vod_actor, 'vod_director': vod_director,
            'vod_content': vod_content, 'vod_play_from': vod_play_from, 'vod_play_url': vod_play_url
        }
        return {key: value for key, value in vod.items() if value}

    def gethr(self, index, rf='', zr=''):
        headers = self.headers[index]
        if zr:
            headers['referer'] = zr
        else:
            headers['referer'] = f"{self.referers[rf]}/"
        return headers

    def handle_exception(self, e):
        print(f"报错: {str(e)}")
        return {'vod_play_from': '哎呀翻车啦', 'vod_play_url': f'翻车啦${self.excepturl}'}