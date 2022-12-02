# coding=utf-8
# !/usr/bin/python
import sys

sys.path.append('..')
from base.spider import Spider
import json
import requests
from requests import session, utils
import os
import time
import base64


class Spider(Spider):  # 元类 默认的元类 type
    def getName(self):
        return "哔哩"

    def init(self, extend=""):
        print("============{0}============".format(extend))
        pass

    def isVideoFormat(self, url):
        pass

    def manualVideoCheck(self):
        pass

    def homeContent(self, filter):
        result = {}
        cateManual = {
        "广场舞":"广场舞",
            "Zard": "Zard",
            "玩具汽车": "玩具汽车",
            "儿童": "儿童",
            "幼儿": "幼儿",
            "儿童玩具": "儿童玩具",
            "昆虫": "昆虫",
            "动物世界": "动物世界",
            "纪录片": "纪录片",
            "相声小品": "相声小品",
            "搞笑": "搞笑",
            "假窗-白噪音": "窗+白噪音",
            "演唱会": "演唱会"
        }
        classes = []
        for k in cateManual:
            classes.append({
                'type_name': k,
                'type_id': cateManual[k]
            })
        result['class'] = classes
        if (filter):
            result['filters'] = self.config['filter']
        return result

    def homeVideoContent(self):
        result = {
            'list': []
        }
        return result

    cookies = ''

    def getCookie(self):
        # 在cookies_str中填入会员或大会员cookie，以获得更好的体验。
        cookies_str = "buvid3=CFF74DA7-E79E-4B53-BB96-FC74AB8CD2F3184997infoc; LIVE_BUVID=AUTO4216125328906835; rpdid=|(umRum~uY~R0J'uYukYukkkY; balh_is_closed=; balh_server_inner=__custom__; PVID=4; video_page_version=v_old_home; i-wanna-go-back=-1; CURRENT_BLACKGAP=0; blackside_state=0; fingerprint=8965144a609d60190bd051578c610d72; buvid_fp_plain=undefined; CURRENT_QUALITY=120; hit-dyn-v2=1; nostalgia_conf=-1; buvid_fp=CFF74DA7-E79E-4B53-BB96-FC74AB8CD2F3184997infoc; CURRENT_FNVAL=4048; DedeUserID=85342; DedeUserID__ckMd5=f070401c4c699c83; b_ut=5; hit-new-style-dyn=0; buvid4=15C64651-E8B7-100C-4B1F-C7CFD2DB473007906-022110820-jYQRaMeS%2BRXRfw14q70%2FLQ%3D%3D; b_nut=1667910208; b_lsid=3CE4AE79_184578915C0; is-2022-channel=1; innersign=0; SESSDATA=a5e4d58d%2C1683641322%2C2c39a%2Ab1; bili_jct=2f3126b5954e37f593130f2fef082cd8; sid=p7tjqv22; bp_video_offset_85342=726936847258746900"
        cookies_dic = dict([co.strip().split('=',1) for co in cookies_str.split(';')])
        rsp = session()
        cookies_jar = utils.cookiejar_from_dict(cookies_dic)
        rsp.cookies = cookies_jar
        content = self.fetch("http://api.bilibili.com/x/web-interface/nav", cookies=rsp.cookies)
        res = json.loads(content.text)
        if res["code"] == 0:
            self.cookies = rsp.cookies
        else:
            rsp = self.fetch("https://www.bilibili.com/")
            self.cookies = rsp.cookies
        return rsp.cookies

    def categoryContent(self, tid, pg, filter, extend):
        result = {}
        url = 'https://api.bilibili.com/x/web-interface/search/type?search_type=video&keyword={0}&page={1}'.format(tid, pg)
        if len(self.cookies) <= 0:
            self.getCookie()
        rsp = self.fetch(url, cookies=self.cookies)
        content = rsp.text
        jo = json.loads(content)
        videos = []
        vodList = jo['data']['result']
        for vod in vodList:
            aid = str(vod['aid']).strip()
            title = vod['title'].replace("<em class=\"keyword\">", "").replace("</em>", "").replace("&quot;", '"')
            img = 'https:' + vod['pic'].strip()
            remark = str(vod['duration']).strip()
            videos.append({
                "vod_id": aid,
                "vod_name": title,
                "vod_pic": img,
                "vod_remarks": remark
            })
        result['list'] = videos
        result['page'] = pg
        result['pagecount'] = 9999
        result['limit'] = 90
        result['total'] = 999999
        return result

    def cleanSpace(self, str):
        return str.replace('\n', '').replace('\t', '').replace('\r', '').replace(' ', '')

    def detailContent(self, array):
        aid = array[0]
        url = "https://api.bilibili.com/x/web-interface/view?aid={0}".format(aid)
        rsp = self.fetch(url, headers=self.header)
        jRoot = json.loads(rsp.text)
        jo = jRoot['data']
        title = jo['title'].replace("<em class=\"keyword\">", "").replace("</em>", "")
        pic = jo['pic']
        desc = jo['desc']
        timeStamp = jo['pubdate']
        timeArray = time.localtime(timeStamp)
        year = str(time.strftime("%Y", timeArray))
        dire = jo['owner']['name']
        typeName = jo['tname']
        remark = str(jo['duration']).strip()
        vod = {
            "vod_id": aid,
            "vod_name": title,
            "vod_pic": pic,
            "type_name": typeName,
            "vod_year": year,
            "vod_area": "",
            "vod_remarks": remark,
            "vod_actor": "",
            "vod_director": dire,
            "vod_content": desc
        }
        ja = jo['pages']
        playUrl = ''
        for tmpJo in ja:
            cid = tmpJo['cid']
            part = tmpJo['part'].replace("#", "-")
            playUrl = playUrl + '{0}${1}_{2}#'.format(part, aid, cid)

        vod['vod_play_from'] = 'B站视频'
        vod['vod_play_url'] = playUrl

        result = {
            'list': [
                vod
            ]
        }
        return result

    def searchContent(self, key, quick):
        header = {
            "Referer": "https://www.bilibili.com",
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/100.0.4896.127 Safari/537.36"
        }
        url = 'https://api.bilibili.com/x/web-interface/search/type?search_type=video&keyword={0}'.format(key)
        if len(self.cookies) <= 0:
            self.getCookie()
        rsp = self.fetch(url, cookies=self.cookies,headers=header)
        content = rsp.text
        jo = json.loads(content)
        if jo['code'] != 0:
            rspRetry = self.fetch(url, cookies=self.getCookie())
            content = rspRetry.text
        jo = json.loads(content)
        videos = []
        vodList = jo['data']['result']
        for vod in vodList:
            aid = str(vod['aid']).strip()
            title = vod['title'].replace("<em class=\"keyword\">", "").replace("</em>", "").replace("&quot;", '"')
            img = 'https:' + vod['pic'].strip()
            remark = str(vod['duration']).strip()
            videos.append({
                "vod_id": aid,
                "vod_name": title,
                "vod_pic": img,
                "vod_remarks": remark
            })
        result = {
            'list': videos
        }
        return result

    def playerContent(self, flag, id, vipFlags):
        result = {}

        ids = id.split("_")
        url = 'https://api.bilibili.com:443/x/player/playurl?avid={0}&cid={1}&qn=116'.format(ids[0], ids[1])
        if len(self.cookies) <= 0:
            self.getCookie()
        rsp = self.fetch(url, cookies=self.cookies)
        jRoot = json.loads(rsp.text)
        jo = jRoot['data']
        ja = jo['durl']

        maxSize = -1
        position = -1
        for i in range(len(ja)):
            tmpJo = ja[i]
            if maxSize < int(tmpJo['size']):
                maxSize = int(tmpJo['size'])
                position = i

        url = ''
        if len(ja) > 0:
            if position == -1:
                position = 0
            url = ja[position]['url']

        result["parse"] = 0
        result["playUrl"] = ''
        result["url"] = url
        result["header"] = {
            "Referer": "https://www.bilibili.com",
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/100.0.4896.127 Safari/537.36"
        }
        result["contentType"] = 'video/x-flv'
        return result

    config = {
        "player": {},
        "filter": {}
    }
    header = {}

    def localProxy(self, param):
        return [200, "video/MP2T", action, ""]