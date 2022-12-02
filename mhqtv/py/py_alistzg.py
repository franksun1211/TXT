# coding=utf-8
# !/usr/bin/python
import sys
sys.path.append('..')
from base.spider import Spider
import json
import re
import urllib

class Spider(Spider):  # 元类 默认的元类 type
    def getName(self):
        return "Alist"

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
"灭火器":"https://alist1.owen10086.repl.co",
"小雅":"http://101.34.67.237:5244/"
}
        classes = []
        for k in cateManual:
            classes.append({
                'type_name': k,
				"type_flag": "1",
                'type_id': cateManual[k]
            })
        result['class'] = classes
        if (filter):
            filters = {}
            for lk in cateManual:
                link = cateManual[lk]
                filters.update({
                    link: [{"key": "nm", "name": "名        称", "value": [{"n": "正序", "v": "False"},{"n": "反序", "v": "True"}]},{"key": "sz", "name": "大        小", "value": [{"n": "升序", "v": "False"},{"n": "降序", "v": "True"}]},{"key": "tp", "name": "类        型", "value": [{"n": "升序", "v": "False"},{"n": "降序", "v": "True"}]},{"key": "tm", "name": "修改时间", "value": [{"n": "升序", "v": "False"},{"n": "降序", "v": "True"}]}]
                })
            result['filters'] = filters
        return result

    def homeVideoContent(self):
        result = {
            'list': []
        }
        return result

    ver = ''
    baseurl = ''
    def getVersion(self, gtid):
        param = {
            "path": '/'
        }
        if gtid.count('/') == 2:
            gtid = gtid + '/'
        baseurl = re.findall(r"http.*://.*?/", gtid)[0]
        ver = self.fetch(baseurl + 'api/public/settings', param)
        vjo = json.loads(ver.text)['data']
        if type(vjo) is dict:
            ver = 3
        else:
            ver = 2
        self.ver = ver
        self.baseurl = baseurl

    def categoryContent(self, tid, pg, filter, extend):
        result = {}
        if tid.count('/') == 2:
            tid = tid + '/'
        nurl = re.findall(r"http.*://.*?/", tid)[0]
        if self.ver == '' or self.baseurl != nurl:
            self.getVersion(tid)
        ver = self.ver
        baseurl = self.baseurl
        if tid.count('/') == 2:
            tid = tid + '/'
        pat = tid.replace(baseurl,"")
        param = {
            "path": '/' + pat
        }
        if ver == 2:
            rsp = self.postJson(baseurl + 'api/public/path', param)
            jo = json.loads(rsp.text)
            vodList = jo['data']['files']
        elif ver == 3:
            rsp = self.postJson(baseurl + 'api/fs/list', param)
            jo = json.loads(rsp.text)
            vodList = jo['data']['content']
        ovodList = vodList
        if len(extend) != 0:
            if 'tp' in extend:
                fl = 'type'
                if extend['tp'] == "True":
                    key = True
                if extend['tp'] == "False":
                    key = False
                vodList.sort(key=lambda x: (x['{0}'.format(fl)]), reverse=key)
            elif 'sz' in extend:
                fl = 'size'
                if extend['sz'] == "True":
                    key = True
                if extend['sz'] == "False":
                    key = False
                vodList.sort(key=lambda x: (x['{0}'.format(fl)]), reverse=key)
            elif 'nm' in extend:
                fl = 'name'
                if extend['nm'] == "True":
                    key = True
                if extend['nm'] == "False":
                    key = False
                vodList.sort(key=lambda x: (x['{0}'.format(fl)]), reverse=key)
            elif 'tm' in extend:
                if ver == 2:
                    fl = 'updated_at'
                elif ver == 3:
                    fl = 'modified'
                if extend['tm'] == "True":
                    key = True
                if extend['tm'] == "False":
                    key = False
                vodList.sort(key=lambda x: (x['{0}'.format(fl)]), reverse=key)
            else:
                vodList = ovodList
        else:
            vodList = ovodList
        videos = []
        cid = ''
        purl = ''
        svodList = str(vodList)
        lenvodList = len(vodList)
        substr = str(re.findall(r"\'name\': \'(.*?)\'", svodList))
        foldernum = svodList.count('\'type\': 1')
        filenum = lenvodList - foldernum
        for vod in vodList:
            if ver == 2:
                img = vod['thumbnail']
            elif ver == 3:
                img = vod['thumb']
            if len(img) == 0:
                if vod['type'] == 1:
                    img = "http://img1.3png.com/281e284a670865a71d91515866552b5f172b.png"
            if pat != '':
                aid = pat + '/'
            else:
                aid = pat
            if vod['type'] == 1:
                tag = "folder"
                remark = "文件夹"
                cid = baseurl + aid + vod['name']
            #计算文件大小
            else:
                size = vod['size']
                if size > 1024 * 1024 * 1024 * 1024.0:
                    fs = "TB"
                    sz = round(size / (1024 * 1024 * 1024 * 1024.0), 2)
                elif size > 1024 * 1024 * 1024.0:
                    fs = "GB"
                    sz = round(size / (1024 * 1024 * 1024.0), 2)
                elif size > 1024 * 1024.0:
                    fs = "MB"
                    sz = round(size / (1024 * 1024.0), 2)
                elif size > 1024.0:
                    fs = "KB"
                    sz = round(size / (1024.0), 2)
                else:
                    fs = "KB"
                    sz = round(size / (1024.0), 2)
                tag = "file"
                remark = str(sz) + fs
                cid = baseurl + aid + vod['name']
                # 开始爬视频与字幕
                if filenum < 150:
                    if 'mp4' in vod['name'] or 'mkv' in vod['name'] or 'TS' in vod['name'] or 'flv' in vod['name'] or 'rmvb' in vod['name'] or 'mp3' in vod['name'] or 'flac' in vod['name'] or 'wav' in vod['name'] or 'wma' in vod['name'] or 'dff' in vod['name']:
                        vodurl = vod['name']
                        # 开始爬字幕
                        cid = '###'
                        subname = re.findall(r"(.*)\.", vod['name'])[0]
                        if filenum == 2:
                            if '.ass' in substr:
                                sub = re.findall(r"'(.*)\.ass", substr)[0]
                                subt = '@@@' + sub + '.ass'
                                if ',' in sub:
                                    sub = re.findall(r"', '(.*)\.ass", substr)[0]
                                    subt = '@@@' + sub + '.ass'
                            if '.srt' in substr:
                                sub = re.findall(r"'(.*)\.srt", substr)[0]
                                subt = '@@@' + sub + '.srt'
                                if ',' in sub:
                                    sub = re.findall(r"', '(.*)\.srt", substr)[0]
                                    subt = '@@@' + sub + '.srt'
                        else:
                            if '.ass' in substr:
                                if '(' in subname or ')' in subname or '[' in subname or ']' in subname:
                                    subname = subname.replace("(","\(").replace(")","\)").replace("[","\[").replace("]","\]")
                                sub = re.findall(r"{0}.*?\.ass".format(subname), substr)
                                if len(sub) != 0:
                                    sub = sub[0]
                                else:
                                    sub = ''
                                if ',' in sub:
                                    lsub = sub.replace('\'',"").split(', ')
                                    sub = lsub[len(lsub)-1]
                                if '(' in subname or ')' in subname or '[' in subname or ']' in subname:
                                    ssubname = subname.replace("\(","(").replace("\)",")").replace("\[","[").replace("\]","]")
                                if subname in sub:
                                    subt = '@@@' + sub
                            elif '.srt' in substr:
                                if '(' in subname or ')' in subname or '[' in subname or ']' in subname:
                                    subname = subname.replace("(","\(").replace(")","\)").replace("[","\[").replace("]","\]")
                                sub = re.findall(r'{0}.*?\.srt'.format(subname), substr)
                                if len(sub) != 0:
                                    sub = sub[0]
                                else:
                                    sub = ''
                                if ',' in sub:
                                    lsub = sub.replace('\'',"").split(', ')
                                    sub = lsub[len(lsub)-1]
                                if '(' in subname or ')' in subname or '[' in subname or ']' in subname:
                                    subname = subname.replace("\(","(").replace("\)",")").replace("\[","[").replace("\]","]")
                                if subname in sub:
                                    subt = '@@@' + sub
                        # 合并链接
                        if 'subt' in locals().keys():
                            purl = purl + '{0}{1}#'.format(vodurl, subt)
                        else:
                            purl = purl + '{0}#'.format(vodurl)
                else:
                    subname = re.findall(r"(.*)\.", vod['name'])[0]
                    if '.ass' in substr:
                        if '(' in subname or ')' in subname or '[' in subname or ']' in subname:
                            subname = subname.replace("(","\(").replace(")","\)").replace("[","\[").replace("]","\]")
                        sub = re.findall(r"{0}.*?\.ass".format(subname), substr)
                        if len(sub) != 0:
                            sub = sub[0]
                        else:
                            sub = ''
                        if ',' in sub:
                            lsub = sub.replace('\'', "").split(', ')
                            sub = lsub[len(lsub) - 1]
                        if '(' in subname or ')' in subname or '[' in subname or ']' in subname:
                            subname = subname.replace("\(","(").replace("\)",")").replace("\[","[").replace("\]","]")
                        if subname in sub:
                            subt = '@@@' + sub
                            cid = cid + subt
                    elif '.srt' in substr:
                        if '(' in subname or ')' in subname or '[' in subname or ']' in subname:
                            subname = subname.replace("(","\(").replace(")","\)").replace("[","\[").replace("]","\]")
                        sub = re.findall(r"{0}.*?\.srt".format(subname), substr)
                        if len(sub) != 0:
                            sub = sub[0]
                        else:
                            sub = ''
                        if ',' in sub:
                            lsub = sub.replace('\'', "").split(', ')
                            sub = lsub[len(lsub) - 1]
                        if '(' in subname or ')' in subname or '[' in subname or ']' in subname:
                            subname = subname.replace("\(","(").replace("\)",")").replace("\[","[").replace("\]","]")
                        if subname in sub:
                            subt = '@@@' + sub
                            cid = cid + subt
            videos.append({
                "vod_id":  cid,
                "vod_name": vod['name'],
                "vod_pic": img,
                "vod_tag": tag,
                "vod_remarks": remark
            })
        if 'purl' in locals().keys():
            purl = baseurl + aid + '+++' + purl
            for i in range(foldernum, lenvodList):
                if videos[i]['vod_id'] == '###':
                    videos[i]['vod_id'] = purl
        result['list'] = videos
        result['page'] = 1
        result['pagecount'] = 1
        result['limit'] = 999
        result['total'] = 999999
        return result

    def detailContent(self, array):
        id = array[0]
        if '+++' in id:
            ids = id.split('+++')
            durl = ids[0]
            vsList = ids[1].strip('#').split('#')
            vsurl = ''
            for vs in vsList:
                if '@@@' in vs:
                    dvs = vs.split('@@@')
                    vname = dvs[0]
                    vurl = durl + dvs[0]
                    surl = durl + dvs[1]
                    vsurl = vsurl + '{0}${1}@@@{2}#'.format(vname, vurl, surl)
                else:
                    vurl = durl + vs
                    vsurl = vsurl + '{0}${1}#'.format(vs, vurl)
            url = vsurl
        else:
            durl = id
        if self.ver == '' or self.baseurl == '':
            self.getVersion(durl)
        baseurl = self.baseurl
        if '+++' in id:
            vid = durl.replace(baseurl, "").strip('/')
        else:
            vid = durl.replace(re.findall(r".*/", durl)[0], "")
            url = vid + '$' + id
        vod = {
            "vod_id": vid,
            "vod_name": vid,
            "vod_pic": '',
            "vod_tag": '',
            "vod_play_from": "播放",
            "vod_play_url": url
        }
        result = {
            'list': [
                vod
            ]
        }
        return result

    def searchContent(self, key, quick):
        result = {
            'list': []
        }
        return result

    def playerContent(self, flag, id, vipFlags):
        result = {}
        url = ''
        subturl = ''
        ifsub = '@@@' in id
        if ifsub is True:
            ids = id.split('@@@')
            if self.ver == '' or self.baseurl == '':
                self.getVersion(ids[1])
            ver = self.ver
            baseurl = self.baseurl
            fileName = ids[1].replace(baseurl, "")
            vfileName = ids[0].replace(baseurl, "")
            param = {
                "path": '/' + fileName,
                "password": "",
                "page_num": 1,
                "page_size": 100
            }
            vparam = {
                "path": '/' + vfileName,
                "password": "",
                "page_num": 1,
                "page_size": 100
            }
            if ver == 2:
                rsp = self.postJson(baseurl + 'api/public/path', param)
                jo = json.loads(rsp.text)
                vodList = jo['data']['files'][0]
                subturl = vodList['url']
                vrsp = self.postJson(baseurl + 'api/public/path', vparam)
                vjo = json.loads(vrsp.text)
                vList = vjo['data']['files'][0]
                url = vList['url']
            elif ver == 3:
                rsp = self.postJson(baseurl + 'api/fs/get', param)
                jo = json.loads(rsp.text)
                vodList = jo['data']
                subturl = vodList['raw_url']
                vrsp = self.postJson(baseurl + 'api/fs/get', vparam)
                vjo = json.loads(vrsp.text)
                vList = vjo['data']
                url = vList['raw_url']
            if subturl.startswith('http') is False:
                head = re.findall(r"h.*?:", baseurl)[0]
                subturl = head + subturl
            if url.startswith('http') is False:
                head = re.findall(r"h.*?:", baseurl)[0]
                url = head + url
            urlfileName = urllib.parse.quote(fileName)
            subturl = subturl.replace(fileName, urlfileName)
            urlvfileName = urllib.parse.quote(vfileName)
            url = url.replace(vfileName, urlvfileName)
            result['subt'] = subturl
        else:
            if self.ver == '' or self.baseurl == '':
                self.getVersion(id)
            ver = self.ver
            baseurl = self.baseurl
            vfileName = id.replace(baseurl, "")
            vparam = {
                "path": '/' + vfileName,
                "password": "",
                "page_num": 1,
                "page_size": 100
            }
            if ver == 2:
                vrsp = self.postJson(baseurl + 'api/public/path', vparam)
                vjo = json.loads(vrsp.text)
                vList = vjo['data']['files'][0]
                driver = vList['driver']
                url = vList['url']
            elif ver == 3:
                vrsp = self.postJson(baseurl + 'api/fs/get', vparam)
                vjo = json.loads(vrsp.text)
                vList = vjo['data']
                url = vList['raw_url']
                driver = vList['provider']
            if url.startswith('http') is False:
                head = re.findall(r"h.*?:", baseurl)[0]
                url = head + url
            urlvfileName = urllib.parse.quote(vfileName)
            url = url.replace(vfileName, urlvfileName)
            if driver == 'Baidu.Disk':
                result["header"] = {"User-Agent": "pan.baidu.com"}

        result["parse"] = 0
        result["playUrl"] = ''
        result["url"] = url

        return result

    flurl = ''
    config = {
        "player": {},
        "filter": {}
    }
    header = {}

    def localProxy(self, param):
        return [200, "video/MP2T", action, ""]