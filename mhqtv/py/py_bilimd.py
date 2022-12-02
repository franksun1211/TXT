#coding=utf-8
#!/usr/bin/python
import sys
sys.path.append('..')
from base.spider import Spider
import json
from requests import session, utils
import os
import time
import base64

class Spider(Spider):  # 元类 默认的元类 type
	def getName(self):
		return "B站影视"
	def init(self,extend=""):
		print("============{0}============".format(extend))
		pass
	def isVideoFormat(self,url):
		pass
	def manualVideoCheck(self):
		pass
	def homeContent(self,filter):
		result = {}
		cateManual = {
			"番剧": "1",
			"国创": "4",
			"电影": "2",
			"综艺": "7",
			"电视剧": "5",
			"纪录片": "3"
		}
		classes = []
		for k in cateManual:
			classes.append({
				'type_name':k,
				'type_id':cateManual[k]
			})
		result['class'] = classes
		if(filter):
			result['filters'] = self.config['filter']
		return result
	def homeVideoContent(self):
		result = {
			'list':[]
		}
		return result
	cookies = ''
	def getCookie(self):
		#在cookies_str中填入会员或大会员cookie，以获得更好的体验
		cookies_str = "buvid3=CFF74DA7-E79E-4B53-BB96-FC74AB8CD2F3184997infoc; LIVE_BUVID=AUTO4216125328906835; rpdid=|(umRum~uY~R0J'uYukYukkkY; balh_is_closed=; balh_server_inner=__custom__; PVID=4; video_page_version=v_old_home; i-wanna-go-back=-1; CURRENT_BLACKGAP=0; blackside_state=0; fingerprint=8965144a609d60190bd051578c610d72; buvid_fp_plain=undefined; CURRENT_QUALITY=120; hit-dyn-v2=1; nostalgia_conf=-1; buvid_fp=CFF74DA7-E79E-4B53-BB96-FC74AB8CD2F3184997infoc; CURRENT_FNVAL=4048; DedeUserID=85342; DedeUserID__ckMd5=f070401c4c699c83; b_ut=5; hit-new-style-dyn=0; buvid4=15C64651-E8B7-100C-4B1F-C7CFD2DB473007906-022110820-jYQRaMeS%2BRXRfw14q70%2FLQ%3D%3D; b_nut=1667910208; b_lsid=3CE4AE79_184578915C0; is-2022-channel=1; innersign=0; SESSDATA=a5e4d58d%2C1683641322%2C2c39a%2Ab1; bili_jct=2f3126b5954e37f593130f2fef082cd8; sid=p7tjqv22; bp_video_offset_85342=726936847258746900"
		cookies_dic = dict([co.strip().split('=') for co in cookies_str.split(';')])
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

	def categoryContent(self,tid,pg,filter,extend):		
		result = {}
		url = 'https://api.bilibili.com/pgc/season/index/result?order=2&season_status=-1&style_id=-1&sort=0&area=-1&pagesize=20&type=1&st={0}&season_type={0}&page={1}'.format(tid,pg)
		if len(self.cookies) <= 0:
			self.getCookie()
		rsp = self.fetch(url, cookies=self.cookies)
		content = rsp.text
		jo = json.loads(content)
		videos = []
		vodList = jo['data']['list']
		for vod in vodList:
			aid = str(vod['season_id']).strip()
			title = vod['title'].strip()
			img =  vod['cover'].strip()
			remark = vod['index_show'].strip()
			videos.append({
				"vod_id":aid,
				"vod_name":title,
				"vod_pic":img,
				"vod_remarks":remark
			})
		result['list'] = videos
		result['page'] = pg
		result['pagecount'] = 9999
		result['limit'] = 90
		result['total'] = 999999
		return result
	def cleanSpace(self,str):
		return str.replace('\n','').replace('\t','').replace('\r','').replace(' ','')
	def detailContent(self,array):
		aid = array[0]
		url = "http://api.bilibili.com/pgc/view/web/season?season_id={0}".format(aid)
		rsp = self.fetch(url,headers=self.header)
		jRoot = json.loads(rsp.text)
		jo = jRoot['result']
		id = jo['season_id']
		title = jo['title']
		pic = jo['cover']
		areas = jo['areas'][0]['name']
		typeName = jo['share_sub_title']
		dec = jo['evaluate']
		remark = jo['new_ep']['desc']
		vod = {
			"vod_id":id,
			"vod_name":title,
			"vod_pic":pic,
			"type_name":typeName,
			"vod_year":"",
			"vod_area":areas,
			"vod_remarks":remark,
			"vod_actor":"",
			"vod_director":"",
			"vod_content":dec
		}
		ja = jo['episodes']
		playUrl = ''
		for tmpJo in ja:
			eid = tmpJo['id']
			cid = tmpJo['cid']
			part = tmpJo['title'].replace("#", "-")
			playUrl = playUrl + '{0}${1}_{2}#'.format(part, eid, cid)

		vod['vod_play_from'] = 'B站影视'
		vod['vod_play_url'] = playUrl

		result = {
			'list':[
				vod
			]
		}
		return result
	def searchContent(self,key,quick):
		url = 'https://api.bilibili.com/x/web-interface/search/type?search_type=media_bangumi&keyword={0}'.format(key)  # 番剧搜索
		if len(self.cookies) <= 0:
			self.getCookie()
		rsp = self.fetch(url, cookies=self.cookies)
		content = rsp.text
		jo = json.loads(content)
		rs = jo['data']
		if rs['numResults'] == 0:
			url = 'https://api.bilibili.com/x/web-interface/search/type?search_type=media_ft&keyword={0}'.format(key)  # 影视搜索
			rspRetry = self.fetch(url, cookies=self.cookies)
			content = rspRetry.text
		jo = json.loads(content)
		videos = []
		vodList = jo['data']['result']
		for vod in vodList:
			aid = str(vod['season_id']).strip()
			title = vod['title'].strip().replace("<em class=\"keyword\">", "").replace("</em>", "")
			img = vod['eps'][0]['cover'].strip()
			remark = vod['index_show']
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

	def playerContent(self,flag,id,vipFlags):
		result = {}
		ids = id.split("_")
		header = {
			"Referer": "https://www.bilibili.com",
			"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/100.0.4896.127 Safari/537.36"
		}
		url = 'https://api.bilibili.com/pgc/player/web/playurl?qn=116&ep_id={0}&cid={1}'.format(ids[0],ids[1])
		if len(self.cookies) <= 0:
			self.getCookie()
		rsp = self.fetch(url,cookies=self.cookies,headers=header)
		jRoot = json.loads(rsp.text)
		if jRoot['message'] != 'success':
			print("需要大会员权限才能观看")
			return {}
		jo = jRoot['result']
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
			"Referer":"https://www.bilibili.com",
			"User-Agent":"Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/100.0.4896.127 Safari/537.36"
		}
		result["contentType"] = 'video/x-flv'
		return result

	config = {
		"player": {},
		"filter": {}
	}
	header = {}

	def localProxy(self,param):
		return [200, "video/MP2T", action, ""]