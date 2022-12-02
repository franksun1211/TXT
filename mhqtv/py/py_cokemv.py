#coding=utf-8
#!/usr/bin/python
import sys
sys.path.append('..')
from base.spider import Spider
import json
import requests
import base64
import re

class Spider(Spider):  # 元类 默认的元类 type
	def getName(self):
		return "COKEMV"
	def init(self,extend=""):
		print("============{0}============".format(extend))
		pass
	def homeContent(self,filter):
		result = {}
		cateManual = {
			"电影":"1",
			"剧集":"2",
			"动漫":"3",
			"综艺":"29"
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
		rsp = self.fetch("https://cokemv.me/")
		root = self.html(rsp.text)
		aList = root.xpath("//div[@class='main']//div[contains(@class,'module-items')]/a")

		videos = []
		for a in aList:
			name = a.xpath('./@title')[0]
			pic = a.xpath('.//img/@data-original')[0]
			mark = a.xpath(".//div[@class='module-item-note']/text()")[0]
			sid = a.xpath("./@href")[0]
			sid = self.regStr(sid,"/voddetail/(\\S+).html")
			videos.append({
				"vod_id":sid,
				"vod_name":name,
				"vod_pic":pic,
				"vod_remarks":mark
			})
		result = {
			'list':videos
		}
		return result
	def categoryContent(self,tid,pg,filter,extend):
		result = {}
		url = 'https://cokemv.me/index.php/vod/show/id/{0}.html'.format(tid)
		rsp = self.fetch(url)
		root = self.html(rsp.text)
		aList = root.xpath("//div[@class='module']/a")
		videos = []
		for a in aList:
			name = a.xpath('./@title')[0]
			pic = a.xpath('.//img/@data-original')[0]
			mark = a.xpath(".//div[contains(@class,'module-item-note')]/text()")[0]
			sid = a.xpath("./@href")[0]
			sid = self.regStr(sid,"/id/(\\d+).html")
			videos.append({
				"vod_id":sid,
				"vod_name":name,
				"vod_pic":pic,
				"vod_remarks":mark
			})
		result['list'] = videos
		result['page'] = pg
		result['pagecount'] = 9999
		result['limit'] = 90
		result['total'] = 999999
		return result
	def detailContent(self,array):
		tid = array[0]
		url = 'https://cokemv.me/index.php/vod/detail/id/{0}.html'.format(tid)
		rsp = self.fetch(url)
		root = self.html(rsp.text)
		divContent = root.xpath("//div[@class='module-main']")[0]
		title = divContent.xpath('.//h1/text()')[0]
		year = divContent.xpath('.//div/div/div[1]/a/text()')[0]
		area = divContent.xpath('.//div/div/div[2]/a/text()')[0]
		typ = divContent.xpath('.//div/div/div[3]/a/text()')
		type = ','.join(typ)
		pic = divContent.xpath(".//div[@class='module-item-pic']/img/@data-original")[0]
		detail = root.xpath(".//div[@class='module-info-introduction-content show-desc']/p/text()")[0].strip()
		vod = {
			"vod_id":tid,
			"vod_name":title,
			"vod_pic":pic,
			"type_name":type,
			"vod_year":year,
			"vod_area":area,
			"vod_content":detail
		}
		infoArray = divContent.xpath(".//div[@class='module-info-item']")
		for info in infoArray:
			content = info.xpath('string(.)').replace('\r','').replace('\n','').replace('\t','')
			if content.startswith('导演'):
				vod['vod_director'] = content.replace("导演：", "").strip('/')
			if content.startswith('主演'):
				vod['vod_director'] = content.replace("主演：", "").strip('/')
			if content.startswith('更新：'):
				vod['vod_remarks'] = content.split('，')[1]
		vod_play_from = '$$$'
		playFrom = []
		vodHeader = root.xpath("//div[@class='module-tab-items']/div/div/span/text()")
		for v in vodHeader:
			playFrom.append(v)
		vod_play_from = vod_play_from.join(playFrom)
		vod_play_url = '$$$'
		playList = []
		vodList = root.xpath("//div[@class='module-play-list']")
		for vl in vodList:
			vodItems = []
			aList = vl.xpath('./div/a')
			for tA in aList:
				href = tA.xpath('./@href')[0]
				name = tA.xpath('.//span/text()')[0]
				tId = self.regStr(href,'/id/(\\S+).html')
				vodItems.append(name + "$" + tId)
			joinStr = '#'
			joinStr = joinStr.join(vodItems)
			playList.append(joinStr)
		vod_play_url = vod_play_url.join(playList)

		vod['vod_play_from'] = vod_play_from
		vod['vod_play_url'] = vod_play_url

		result = {
			'list':[
				vod
			]
		}
		return result

	def verifyCode(self, url):
		retry = 5
		header = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/100.0.4896.127 Safari/537.36"}
		while retry:
			try:
				session = requests.session()
				img = session.get('https://cokemv.me/index.php/verify/index.html?', headers=header).content
				code = session.post('https://api.nn.ci/ocr/b64/text', data=base64.b64encode(img).decode()).text
				res = session.post(url=f"https://cokemv.me/index.php/ajax/verify_check?type=search&verify={code}", headers=header).json()
				if res["msg"] == "ok":
					return session
			except Exception as e:
				print(e)
			finally:
				retry = retry - 1

	def searchContent(self, key, quick):
		url = 'https://cokemv.me/index.php/vod/search.html?wd={0}'.format(key)
		session = self.verifyCode(url)
		rsp = session.get(url)
		root = self.html(rsp.text)
		vodList = root.xpath("//div[@class='module-card-item module-item']/a[@class='module-card-item-poster']")
		videos = []
		for vod in vodList:
			name = vod.xpath(".//img/@alt")[0]
			pic = vod.xpath(".//img/@data-original")[0]
			mark = vod.xpath(".//div[@class='module-item-note']/text()")[0]
			sid = vod.xpath("./@href")[0]
			sid = self.regStr(sid,"/id/(\\S+).html")
			videos.append({
				"vod_id":sid,
				"vod_name":name,
				"vod_pic":pic,
				"vod_remarks":mark
			})
		result = {
			'list':videos
		}
		return result

	config = {
		"player": {"ddzy":{"show":"蓝光采集","des":"","ps":"0","parse":""},"tkm3u8":{"show":"采集路线","des":"","ps":"0","parse":""},"if101":{"show":"海外(禁國內)","des":"","ps":"0","parse":""}},
        "filter": {}
	}
	header = {
		"origin":"https://cokemv.me",
		"User-Agent":"Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.114 Safari/537.36",
		"Accept":" */*",
		"Accept-Language":"zh-CN,zh;q=0.9,en-US;q=0.3,en;q=0.7",
		"Accept-Encoding":"gzip, deflate"
	}
	def playerContent(self,flag,id,vipFlags):
		url = 'https://cokemv.me/index.php/vod/play/id/{0}.html'.format(id)
		header = {
			"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/100.0.4896.127 Safari/537.36"}
		rsp = self.fetch(url,headers=header)
		root = self.html(rsp.text)
		scripts = root.xpath("//script/text()")
		jo = {}
		result = {}
		for script in scripts:
			if(script.startswith("var player_")):
				target = script[script.index('{'):]
				jo = json.loads(target)
				break;
		if jo['from'] in self.config['player']:
			playerConfig = self.config['player'][jo['from']]
			if jo['from'] == 'coke12345':
				Url = jo['url']
				m3u = self.fetch(Url).text.split('\n')[2]
				link = re.findall(r"http.*://.*?/", Url)[0].strip('/')
				videoUrl = link + m3u
				header = {
					"User-Agent": "Mozilla/5.0 (Windows NT 10.0; WOW64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/86.0.4240.198 Safari/537.36"
				}
				result["header"] = header
			else:
				videoUrl = jo['url']
				result["header"] = json.dumps(self.header)
			playerUrl = playerConfig['parse']
			result["parse"] = playerConfig['ps']
			result["playUrl"] = playerUrl
			result["url"] = videoUrl

		return result
	def isVideoFormat(self,url):
		pass
	def manualVideoCheck(self):
		pass
	def localProxy(self,param):
		return [200, "video/MP2T", action, ""]