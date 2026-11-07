# -*- coding: utf-8 -*-
# 聚合平台3 - 影视资源 API 聚合
# 对应 聚合平台3 (2).js；结构参考 ppnix.py
import json
import re
import sys
from urllib.parse import quote, urlencode

sys.path.append('..')
try:
    from base.spider import Spider as BaseSpider
except ImportError:
    class BaseSpider(object):
        def init(self, extend=""):
            pass

try:
    import requests
except ImportError:
    requests = None


class Spider(BaseSpider):
    def init(self, extend=""):
        self._first_cate = {}

    def getName(self):
        return '聚合平台3'

    def isVideoFormat(self, url):
        if not url:
            return False
        u = str(url).lower()
        if any(x in u for x in ('.mp4', '.m3u8', '.flv', '.mkv')):
            return True
        if u.startswith('magnet:') or u.startswith('ed2k:'):
            return True
        return False

    def manualVideoCheck(self):
        pass

    def destroy(self):
        pass

    UA = (
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
        'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
    )
    headers = {'User-Agent': UA}

    # 绅士影视解析站（优先顺序）
    PARSERS = [
        {'name': '组豪富英', 'api': 'https://coffee-5c93e1f751eb.edge.tvapp.eu.org:31000/api/?key=6f8622b2-8402-43c9-ae29-0adaa292bc71&url='},
        {'name': '组4K·P', 'api': 'https://jx.meilinvps.com/api/?key=7dba17e4cc9b887faf7afaf9a20fd391&url='},
        {'name': '组4K·C', 'api': 'https://vip1.123jx.vip/api/?key=f60311e9bc7c1eac9dcaf5e336647b65&url='},
        {'name': '组C4K·E', 'api': 'http://175.24.181.180:5000/api/jiexi/common?Key=Dg3tqWmzSgcKGlZY2c&url='},
        {'name': 'huaqi', 'api': 'https://api.huaqi.pro/api/?key=5bd0db7c858ba9f999373450f3651af7&url='},
        {'name': 'zqcb', 'api': 'https://kx.zyzqcb.cc/api/?key=df8bfba9c7ce22ff751465ec8cb73623&url='},
        {'name': '789', 'api': 'https://jx.789jiexi.icu:4433/?url='},
        {'name': '12321', 'api': 'https://test1.12321app.com/daoliansiquanjia.php?url='},
    ]

    # type: 0=XML旧版  1=JSON标准(默认)  2=代理源  3=大地feifei2
    SOURCES = {
        's1': {'name': '🎬电影天堂', 'api': 'http://caiji.dyttzyapi.com/api.php/provide/vod/from/dyttm3u8/at/json'},
        's2': {'name': '💧无水印', 'api': 'https://api.wsyzy.net/api.php/provide/vod'},
        's3': {'name': '🧸量子', 'api': 'https://cj.lziapi.com/api.php/provide/vod'},
        's4': {'name': '📺1080资源', 'api': 'https://api.yyzy-tv.vip/inc/apijson.php'},
        's5': {'name': '🔥大众资源', 'api': 'https://cdn.dzzyapi.com/api.php/provide/vod/'},
        's6': {'name': '📺天涯', 'api': 'https://tyyszy.com/api.php/provide/vod'},
        's7': {'name': '📺暴风', 'api': 'https://bfzyapi.com/api.php/provide/vod'},
        's8': {'name': '⚡闪电', 'api': 'https://xsd.sdzyapi.com/api.php/provide/vod'},
        's9': {'name': '📺索尼', 'api': 'https://suoniapi.com/api.php/provide/vod'},
        's10': {'name': '📺红牛', 'api': 'https://www.hongniuzy2.com/api.php/provide/vod'},
        's11': {'name': '📺茅台', 'api': 'https://caiji.maotaizy.cc/api.php/provide/vod'},
        's12': {'name': '🐯虎牙', 'api': 'https://www.huyaapi.com/api.php/provide/vod'},
        's13': {'name': '📺猫眼', 'api': 'https://api.maoyanapi.top/api.php/provide/vod/'},
        's14': {'name': '📺豆瓣', 'api': 'https://dbzy.tv/api.php/provide/vod'},
        's15': {'name': '📺豪华', 'api': 'https://hhzyapi.com/api.php/provide/vod'},
        's16': {'name': '📺CK资源', 'api': 'https://ckzy.me/api.php/provide/vod'},
        's17': {'name': '📺U酷', 'api': 'https://api.ukuapi88.com/api.php/provide/vod'},
        's18': {'name': '📺ikun', 'api': 'https://ikunzyapi.com/api.php/provide/vod'},
        's19': {'name': '📺无尽', 'api': 'https://api.wujinapi.cc/api.php/provide/vod'},
        's20': {'name': '🌕光速', 'api': 'https://api.guangsuapi.com/api.php/provide/vod'},
        's21': {'name': '📺西瓜', 'api': 'https://caiji.xgzyapi.com/api.php/provide/vod/'},
        's22': {'name': '📺新浪', 'api': 'https://api.xinlangapi.com/xinlangapi.php/provide/vod'},
        's23': {'name': '📺旺旺', 'api': 'https://zhangqun66.xyz/ww.php'},
        's24': {'name': '📺最大', 'api': 'https://api.zuidapi.com/api.php/provide/vod'},
        's25': {'name': '🌸樱花', 'api': 'https://m3u8.apiyhzy.com/api.php/provide/vod'},
        's26': {'name': '🐮牛牛', 'api': 'https://api.niuniuzy.me/api.php/provide/vod'},
        's27': {'name': '☁️百度云', 'api': 'https://api.apibdzy.com/api.php/provide/vod'},
        's28': {'name': '🏎速播', 'api': 'https://subocaiji.com/api.php/provide/vod'},
        's29': {'name': '🦅金鹰', 'api': 'https://jyzyapi.com/provide/vod/'},
        's30': {'name': '⚡闪电', 'api': 'https://sdzyapi.com/api.php/provide/vod'},
        's31': {'name': '👑非凡', 'api': 'https://cj.ffzyapi.com/api.php/provide/vod'},
        's32': {'name': '🍃飘零', 'api': 'https://p2100.net/api.php/provide/vod'},
        's33': {'name': '🐾360', 'api': 'https://360zyzz.com/api.php/provide/vod/'},
        's34': {'name': '🐾淘片', 'api': 'https://taopianapi.com/cjapi/mc/vod/json.html'},
        's35': {'name': '🐾快车', 'api': 'https://caiji.kuaichezy.org/api.php/provide/vod/'},
        's36': {'name': '🐾奇异', 'api': 'https://iqiyizyapi.com/api.php/provide/vod/'},
        's37': {'name': '🐾鸭鸭', 'api': 'https://cj.yayazy.net/api.php/provide/vod/'},
        's38': {'name': '🐾极速', 'api': 'https://jszyapi.com/api.php/provide/vod/'},
        's39': {'name': '🐾如意', 'api': 'https://cj.rycjapi.com/api.php/provide/vod/'},
        's40': {'name': '🐾火狐', 'api': 'https://hhzyapi.com/api.php/provide/vod/'},
        's41': {'name': '🐾刺桐', 'api': 'http://pg.cttv.vip/api.php/provide/vod/'},
        's42': {'name': '🐾巨量', 'api': 'https://api.juliang.live/api/provide/vod/'},
        's43': {'name': '🐾荐片', 'api': 'https://zhangqun1818.serv00.net/jianpian.php', 'type': 2},
        's44': {'name': '🐾独播库', 'api': 'http://101.42.104.195:7123/api/tvbox/source/2050160339265261568?token=OLFNw46CpJpG9fP2Y2zBW0tvbcLJ9Si2', 'type': 5},
        's45': {'name': '📺魔都', 'api': 'https://www.mdzyapi.com/api.php/provide/vod'},
        's46': {'name': '🐾极影4K', 'api': 'http://101.201.171.207:802/api.php/provide/vod/', 'type': 1, 'parse': 1, 'parser': '175.24.181.180'},
        's47': {'name': '🐾tw采集', 'api': 'http://cj.10010888.xyz/api.php/provide/vod/', 'type': 1, 'parse': 1},
        's48': {'name': '🐾绅士官采', 'api': 'https://cj.jusj.top/api.php/provide/vod/', 'type': 1, 'parse': 1},
        's49': {'name': '🐾绅4K·P', 'api': 'https://cms.meilinvps.com/api.php/provide/vod/', 'type': 1, 'parse': 1, 'parser': 'jx.meilinvps.com'},
        's50': {'name': '🐾绅4K·C', 'api': 'https://cms.123jx.vip/api.php/provide/vod/', 'type': 1, 'parse': 1, 'parser': '123jx.vip'},
        's51': {'name': '🐾绅2K·P', 'api': 'http://down-hk1.1ljx.com:10800/c_api/co_cj/', 'type': 1, 'parse': 1, 'parser': 'tvapp.eu.org'},
        's53': {'name': '🐾绅4K·E', 'api': 'https://co4k.1ljx.com:32010/c_api/co4k_cj', 'type': 1, 'parse': 1, 'parser': '175.24.181.180'},
    }

    def _text(self, v):
        return str(v if v is not None else '').strip()

    def _build_url(self, base, params=None):
        url = self._text(base).strip(' "\'')
        params = params or {}
        keys = [k for k, v in params.items() if v is not None and str(v) != '']
        if not keys:
            return url
        qs = urlencode({k: str(params[k]) for k in keys})
        return url + ('&' if '?' in url else '?') + qs

    def _request(self, url, extra_headers=None):
        try:
            hdr = dict(self.headers)
            if extra_headers:
                hdr.update(extra_headers)
            if requests is None:
                import urllib.request
                import ssl
                ctx = ssl.create_default_context()
                ctx.check_hostname = False
                ctx.verify_mode = ssl.CERT_NONE
                req = urllib.request.Request(url, headers=hdr)
                with urllib.request.urlopen(req, timeout=12, context=ctx) as r:
                    return r.read().decode('utf-8', 'ignore')
            r = requests.get(url, headers=hdr, timeout=12, verify=False)
            r.encoding = 'utf-8'
            return r.text if r.status_code == 200 else ''
        except Exception as e:
            print('request error', url, e)
            return ''

    def _safe_json(self, s):
        try:
            if not s:
                return None
            # 大整数 id 保精度
            s2 = re.sub(
                r'"(vod_id|id|type_id|list_id|vodId)"\s*:\s*(\d{16,})',
                r'"\1":"\2"',
                str(s),
            )
            return json.loads(s2)
        except Exception:
            try:
                return json.loads(s)
            except Exception:
                return None

    def _fix_pic(self, url):
        url = self._text(url)
        if not url:
            return ''
        if url.startswith('//'):
            return 'https:' + url
        if url.startswith('http://') or url.startswith('https://'):
            return url
        return ''

    def _extract_cdata(self, s):
        if not s:
            return ''
        m = re.search(r'<!\[CDATA\[([\s\S]*?)\]\]>', s)
        if m:
            return m.group(1).strip()
        return re.sub(r'<[^>]+>', '', s).strip()

    def _normalize_vod(self, item):
        if not item or not isinstance(item, dict):
            return item
        o = dict(item)
        if not o.get('vod_play_from') and o.get('vod_play'):
            o['vod_play_from'] = o['vod_play']
        if not o.get('vod_play_url') and o.get('vod_url'):
            o['vod_play_url'] = o['vod_url']
        if not o.get('vod_id') and o.get('id'):
            o['vod_id'] = o['id']
        if o.get('vod_id') is not None:
            o['vod_id'] = str(o['vod_id'])
        if not o.get('vod_name') and o.get('name'):
            o['vod_name'] = o['name']
        if not o.get('vod_name') and o.get('title'):
            o['vod_name'] = o['title']
        if not o.get('vod_pic') and o.get('pic'):
            o['vod_pic'] = o['pic']
        if not o.get('vod_pic') and o.get('cover'):
            o['vod_pic'] = o['cover']
        if not o.get('vod_remarks') and o.get('remarks'):
            o['vod_remarks'] = o['remarks']
        if not o.get('vod_remarks') and o.get('note'):
            o['vod_remarks'] = o['note']
        if not o.get('vod_remarks') and o.get('vod_continu'):
            o['vod_remarks'] = o['vod_continu']
        if not o.get('vod_content') and o.get('content'):
            o['vod_content'] = o['content']
        if not o.get('vod_content') and o.get('des'):
            o['vod_content'] = o['des']
        if not o.get('vod_actor') and o.get('actor'):
            o['vod_actor'] = o['actor']
        if not o.get('vod_director') and o.get('director'):
            o['vod_director'] = o['director']
        if not o.get('vod_year') and o.get('year'):
            o['vod_year'] = o['year']
        if not o.get('vod_year') and o.get('d_year'):
            o['vod_year'] = o['d_year']
        if not o.get('vod_area') and o.get('area'):
            o['vod_area'] = o['area']
        if not o.get('type_name') and o.get('list_name'):
            o['type_name'] = o['list_name']
        if not o.get('type_name') and o.get('type'):
            o['type_name'] = o['type']
        o['vod_pic'] = self._fix_pic(o.get('vod_pic'))
        return o

    def _parse_xml(self, html):
        empty = {'list': [], 'page': 1, 'pagecount': 1, 'pagesize': 20, 'total': 0, 'class': []}
        if not html or ('<rss' not in html and '<video>' not in html):
            return empty
        page, pagecount, pagesize, total = 1, 1, 20, 0
        lm = re.search(r'<list\s+[^>]*>', html, re.I)
        if lm:
            attrs = lm.group(0)
            m = re.search(r'page="(\d+)"', attrs, re.I)
            if m:
                page = int(m.group(1))
            m = re.search(r'pagecount="(\d+)"', attrs, re.I)
            if m:
                pagecount = int(m.group(1))
            m = re.search(r'pagesize="(\d+)"', attrs, re.I)
            if m:
                pagesize = int(m.group(1))
            m = re.search(r'recordcount="(\d+)"', attrs, re.I)
            if m:
                total = int(m.group(1))
        classes = []
        cb = re.search(r'<class>([\s\S]*?)</class>', html, re.I)
        if cb:
            for m in re.finditer(r'<ty\s+id=["\']?(\d+)["\']?>([^<]+)</ty>', cb.group(1), re.I):
                classes.append({'type_id': m.group(1), 'type_name': m.group(2).strip()})
        lst = []
        for vm in re.finditer(r'<video>([\s\S]*?)</video>', html, re.I):
            v = vm.group(1)

            def get(tag):
                m = re.search(r'<%s[^>]*>([\s\S]*?)</%s>' % (tag, tag), v, re.I)
                return self._extract_cdata(m.group(1)) if m else ''

            vid, name = get('id'), get('name')
            if not vid or not name:
                continue
            dt = get('dt')
            play_from, play_url = [], []
            dl = re.search(r'<dl>([\s\S]*?)</dl>', v, re.I)
            if dl:
                for dm in re.finditer(
                    r'<dd[^>]*?(?:flag=["\']([^"\']+)["\']|flag=([^\s>]+))[^>]*>([\s\S]*?)</dd>',
                    dl.group(1), re.I
                ):
                    flag = self._text(dm.group(1) or dm.group(2) or dt or '播放')
                    content = self._extract_cdata(dm.group(3) or '')
                    if content:
                        play_from.append(flag)
                        play_url.append(content)
            if not play_from and dt:
                play_from.append(dt)
                play_url.append('')
            lst.append(self._normalize_vod({
                'vod_id': vid,
                'vod_name': name,
                'vod_pic': get('pic'),
                'vod_remarks': get('note') or get('type') or '',
                'vod_year': get('year'),
                'vod_area': get('area'),
                'vod_actor': get('actor'),
                'vod_director': get('director'),
                'vod_content': get('des'),
                'vod_play_from': '$$$'.join(play_from),
                'vod_play_url': '$$$'.join(play_url),
                'type_name': get('type'),
            }))
        return {
            'list': lst, 'page': page, 'pagecount': pagecount,
            'pagesize': pagesize, 'total': total, 'class': classes,
        }

    def _parse_response(self, html):
        empty = {'list': [], 'page': 1, 'pagecount': 1, 'pagesize': 20, 'total': 0, 'class': []}
        if not html:
            return empty
        j = self._safe_json(html)
        if j:
            # 大地 feifei2
            if j.get('status') is not None and (isinstance(j.get('data'), list) or j.get('page')):
                page_obj = j.get('page') or {}
                classes = []
                if isinstance(j.get('list'), list) and j['list']:
                    first = j['list'][0]
                    if first and (first.get('list_id') is not None or first.get('list_name')):
                        classes = [{
                            'type_id': self._text(c.get('list_id') or c.get('type_id') or c.get('id')),
                            'type_name': self._text(c.get('list_name') or c.get('type_name') or c.get('name')),
                        } for c in j['list']]
                raw = j['data'] if isinstance(j.get('data'), list) else []
                return {
                    'list': [self._normalize_vod(x) for x in raw],
                    'page': int(page_obj.get('pageindex') or page_obj.get('page') or 1),
                    'pagecount': int(page_obj.get('pagecount') or 1),
                    'pagesize': int(page_obj.get('pagesize') or 20),
                    'total': int(page_obj.get('recordcount') or page_obj.get('total') or len(raw)),
                    'class': classes,
                }
            if isinstance(j.get('list'), list) or isinstance(j.get('class'), list) or j.get('filters'):
                raw = j['list'] if isinstance(j.get('list'), list) else []
                classes = j['class'] if isinstance(j.get('class'), list) else []
                if raw and raw[0] and raw[0].get('list_id') is not None and not raw[0].get('vod_id') and not raw[0].get('vod_name'):
                    classes = [{
                        'type_id': self._text(c.get('list_id') or c.get('type_id')),
                        'type_name': self._text(c.get('list_name') or c.get('type_name')),
                    } for c in raw]
                    raw = []
                else:
                    raw = [self._normalize_vod(x) for x in raw]
                classes = [{
                    'type_id': self._text(c.get('type_id') or c.get('list_id') or c.get('id')),
                    'type_name': self._text(c.get('type_name') or c.get('list_name') or c.get('name')),
                } for c in (classes or [])]
                return {
                    'list': raw,
                    'page': int(j.get('page') or 1),
                    'pagecount': int(j.get('pagecount') or 1),
                    'pagesize': int(j.get('limit') or j.get('pagesize') or 20),
                    'total': int(j.get('total') or 0),
                    'class': classes,
                }
        return self._parse_xml(html)

    def _clean_item(self, item, source_key, source_name, is_detail=False):
        o = self._normalize_vod(dict(item))
        o['vod_id'] = self._text(o.get('vod_id'))
        if not is_detail:
            o['vod_id'] = '%s@@%s' % (source_key, o['vod_id'])
        rem = self._text(o.get('vod_remarks'))
        o['vod_remarks'] = '%s | %s' % (source_name, rem)
        from_arr = [s.strip() for s in self._text(o.get('vod_play_from')).split('$$$') if s.strip()]
        url_arr = [s.strip() for s in self._text(o.get('vod_play_url')).split('$$$')]
        if from_arr:
            from_arr = [x if x.startswith(source_name) else '%s-%s' % (source_name, x) for x in from_arr]
        if not from_arr and any(url_arr):
            from_arr = ['%s-线路%d' % (source_name, i + 1) for i in range(len(url_arr))]
        if from_arr and url_arr:
            while len(url_arr) < len(from_arr):
                url_arr.append('')
            while len(from_arr) < len(url_arr):
                from_arr.append('%s-线路%d' % (source_name, len(from_arr) + 1))
        o['vod_play_from'] = '$$$'.join(from_arr)
        o['vod_play_url'] = '$$$'.join(url_arr)
        o.pop('vod_down_from', None)
        o.pop('vod_down_url', None)
        return o

    def _load_filter(self, source_key, source_obj):
        stype = source_obj.get('type') or 1
        if stype == 2:
            url = self._build_url(source_obj['api'], {})
        else:
            url = self._build_url(source_obj['api'], {'ac': 'list'})
        data = self._parse_response(self._request(url))
        vals = [{'n': '全部(最新)', 'v': ''}]
        first_cate = ''
        for c in (data.get('class') or []):
            cid = self._text(c.get('type_id'))
            name = self._text(c.get('type_name'))
            if cid or name:
                vals.append({'n': name or cid, 'v': cid})
                if not first_cate and cid:
                    first_cate = cid
        return vals, first_cate

    def homeContent(self, filter):
        # 只返回源列表，不逐个请求分类（否则 40+ 源会卡死加载）
        classes = []
        filters = {}
        self._first_cate = {'s43': '1', 's44': 'site_duoduo'}
        default_vals = [{'n': '全部(最新)', 'v': ''}]
        for sk, so in self.SOURCES.items():
            classes.append({'type_id': sk, 'type_name': so['name']})
            if (so.get('type') or 1) == 5:
                vals = [
                    {'n': '玩偶', 'v': 'site_wanou'},
                    {'n': '木偶', 'v': 'site_muou'},
                    {'n': '蜡笔', 'v': 'site_labi'},
                    {'n': '至臻', 'v': 'site_zhizhen'},
                    {'n': '二小', 'v': 'site_erxiao'},
                    {'n': '虎斑', 'v': 'site_huban'},
                    {'n': '快映', 'v': 'site_kuaiying'},
                    {'n': '闪电', 'v': 'site_shandian'},
                    {'n': '欧哥', 'v': 'site_ouge'},
                    {'n': '多多', 'v': 'site_duoduo'},
                ]
                filters[sk] = [{'key': 'cateId', 'name': '站点', 'value': vals}]
                self._first_cate[sk] = 'site_duoduo'
            elif (so.get('type') or 1) == 4:
                vals = [
                    {'n': '全部(连续剧)', 'v': 'tv'},
                    {'n': '连续剧', 'v': 'tv'},
                    {'n': '电影', 'v': 'movie'},
                    {'n': '综艺', 'v': 'variety'},
                    {'n': '动漫', 'v': 'anime'},
                ]
                filters[sk] = [{'key': 'cateId', 'name': '分类', 'value': vals}]
                self._first_cate[sk] = 'tv'
            elif sk == 's43':

                vals = [
                    {'n': '电影', 'v': '1'},
                    {'n': '电视剧', 'v': '2'},
                    {'n': '动漫', 'v': '3'},
                    {'n': '综艺', 'v': '4'},
                    {'n': '纪录片', 'v': '50'},
                    {'n': 'Netflix', 'v': '99'},
                ]
                filters[sk] = [{'key': 'cateId', 'name': '分类', 'value': vals}]
                self._first_cate[sk] = '1'
            else:
                filters[sk] = [{'key': 'cateId', 'name': '分类', 'value': list(default_vals)}]
        return {'class': classes, 'filters': filters if filter else {}}

    def homeVideoContent(self):
        return {'list': []}

    def categoryContent(self, tid, pg, filter, extend):
        pg = int(pg or 1)
        source = self.SOURCES.get(str(tid))
        if not source:
            return {'list': [], 'page': pg, 'pagecount': 0}
        cate_id = ''
        if isinstance(extend, dict):
            cate_id = self._text(extend.get('cateId'))
        elif isinstance(filter, dict):
            cate_id = self._text(filter.get('cateId'))
        stype = source.get('type') or 1
        if not cate_id and stype == 2 and self._first_cate.get(tid):
            cate_id = self._first_cate[tid]
        if stype in (0, 3):
            params = {'ac': 'videolist', 'pg': pg}
            if cate_id:
                params['t'] = cate_id
            url = self._build_url(source['api'], params)
        elif stype == 5:
            if not cate_id:
                cate_id = self._first_cate.get(tid) or 'site_duoduo'
            params = {'t': cate_id, 'pg': pg, 'categoryId': '1'}
            # extend 可覆盖 categoryId
            if isinstance(extend, dict) and extend.get('categoryId') is not None:
                params['categoryId'] = self._text(extend.get('categoryId'))
            url = self._build_url(source['api'], params)
        elif stype in (2, 4):
            params = {'pg': pg}
            if not cate_id:
                # dbo 无 t 时列表为空，默认连续剧
                cate_id = self._first_cate.get(tid) or 'tv'
            params['t'] = cate_id
            url = self._build_url(source['api'], params)
        else:
            params = {'ac': 'detail', 'pg': pg}
            if cate_id:
                params['t'] = cate_id
            url = self._build_url(source['api'], params)
        data = self._parse_response(self._request(url))
        if stype == 2 and not data.get('list'):
            fc = self._first_cate.get(tid)
            if fc and fc != cate_id:
                data = self._parse_response(
                    self._request(self._build_url(source['api'], {'pg': pg, 't': fc}))
                )
        out = []
        for it in (data.get('list') or []):
            cleaned = self._clean_item(it, tid, source['name'], False)
            cleaned['vod_pic'] = self._fix_pic(cleaned.get('vod_pic'))
            out.append(cleaned)
        return {
            'list': out,
            'page': int(data.get('page') or pg),
            'pagecount': int(data.get('pagecount') or 1),
            'limit': int(data.get('pagesize') or 20),
            'total': int(data.get('total') or len(out)),
        }

    def detailContent(self, ids):
        raw = ids[0] if isinstance(ids, list) else ids
        if '@@' not in str(raw):
            return {'list': []}
        source_key, real_id = str(raw).split('@@', 1)
        source = self.SOURCES.get(source_key)
        if not source:
            return {'list': []}
        stype = source.get('type') or 1
        if stype == 0:
            html = self._request(self._build_url(source['api'], {'ac': 'videolist', 'ids': real_id}))
        elif stype == 5:
            html = self._request(self._build_url(source['api'], {'ac': 'detail', 'ids': real_id}))
            if not html or not self._parse_response(html).get('list'):
                html = self._request(self._build_url(source['api'], {'ids': real_id}))
        elif stype == 4:
            html = self._request(self._build_url(source['api'], {'ac': 'detail', 'ids': real_id}))
            if not html:
                html = self._request(self._build_url(source['api'], {'ids': real_id}))
        elif stype == 3:
            html = self._request(self._build_url(source['api'], {'ac': 'videolist', 'ids': real_id}))
            tmp = self._parse_response(html)
            first = (tmp.get('list') or [None])[0]
            if not first or not self._text(first.get('vod_play_url') or first.get('vod_url')):
                html2 = self._request(self._build_url(source['api'], {'ac': 'detail', 'ids': real_id}))
                if html2:
                    html = html2
        else:
            html = self._request(self._build_url(source['api'], {'ac': 'detail', 'ids': real_id}))
            tmp0 = self._parse_response(html)
            first0 = (tmp0.get('list') or [None])[0]
            if not first0 or not self._text(first0.get('vod_play_url') or first0.get('vod_url')):
                html_vl = self._request(self._build_url(source['api'], {'ac': 'videolist', 'ids': real_id}))
                dvl = self._parse_response(html_vl)
                if (dvl.get('list') or [None])[0] and self._text(
                    (dvl['list'][0].get('vod_play_url') or dvl['list'][0].get('vod_url') or '')
                ):
                    html = html_vl
            if stype == 2:
                tmp = self._parse_response(html)
                first = (tmp.get('list') or [None])[0]
                no_play = not first or not self._text(first.get('vod_play_url') or first.get('vod_url'))
                if no_play and re.search(r'https?://', real_id, re.I):
                    nm = re.search(r'/(\d+)/?$', real_id) or re.search(r'(\d{4,})', real_id)
                    if nm:
                        html3 = self._request(
                            self._build_url(source['api'], {'ac': 'detail', 'ids': nm.group(1)})
                        )
                        if html3:
                            d3 = self._parse_response(html3)
                            if (d3.get('list') or [None])[0] and self._text(
                                (d3['list'][0].get('vod_play_url') or d3['list'][0].get('vod_url') or '')
                            ):
                                html = html3
        data = self._parse_response(html)
        out = []
        for it in (data.get('list') or []):
            cleaned = self._clean_item(it, source_key, source['name'], True)
            cleaned['vod_id'] = str(raw)
            cleaned['vod_pic'] = self._fix_pic(cleaned.get('vod_pic'))
            if not self._text(cleaned.get('vod_play_from')) and self._text(cleaned.get('vod_play_url')):
                segs = self._text(cleaned.get('vod_play_url')).split('$$$')
                cleaned['vod_play_from'] = '$$$'.join(
                    ['%s-线路%d' % (source['name'], i + 1) for i in range(len(segs))]
                )
            out.append(cleaned)
        return {'list': out}

    def searchContent(self, key, quick, pg='1'):
        return self.searchContentPage(key, quick, pg)

    def searchContentPage(self, key, quick, pg=1):
        pg = int(pg or 1)
        key = self._text(key)
        if not key:
            return {'list': [], 'page': pg, 'pagecount': 1, 'limit': 40, 'total': 0}
        result = []
        max_page = 1
        # 优先源：荐片/独播库/极影/tw/绅4K 系列（避免被前 60 条提前截断）
        priority_keys = ['s43', 's44', 's46', 's47', 's48', 's49', 's50', 's51', 's52', 's53']
        all_items = list(self.SOURCES.items())
        pri = [(k, v) for k, v in all_items if k in priority_keys]
        rest = [(k, v) for k, v in all_items if k not in priority_keys]
        if quick:
            items = pri + rest[:12]
        else:
            items = pri + rest

        def _one(sk_so):
            sk, so = sk_so
            try:
                stype = so.get('type') or 1
                # 独播库较慢，单独加长超时
                extra_to = 15 if sk == 's44' else None
                if stype in (0, 3):
                    url = self._build_url(so['api'], {'ac': 'videolist', 'wd': key, 'pg': pg})
                elif stype == 2:
                    # 荐片：直接 wd
                    url = self._build_url(so['api'], {'wd': key, 'pg': pg})
                elif stype == 5:
                    url = self._build_url(so['api'], {'wd': key, 'pg': pg})
                elif stype == 4:
                    url = self._build_url(so['api'], {'t': 'tv', 'pg': pg, 'wd': key})
                else:
                    url = self._build_url(so['api'], {'ac': 'detail', 'wd': key, 'pg': pg})
                html = self._request(url) if extra_to is None else self._request_timeout(url, extra_to)
                data = self._parse_response(html)
                # 空结果时兜底 videolist
                if not data.get('list') and stype not in (0, 2, 3, 5):
                    data2 = self._parse_response(
                        self._request(self._build_url(so['api'], {'ac': 'videolist', 'wd': key, 'pg': pg}))
                    )
                    if data2.get('list'):
                        data = data2
                if not data.get('list') and stype == 2:
                    data2 = self._parse_response(
                        self._request(self._build_url(so['api'], {'ac': 'detail', 'wd': key, 'pg': pg}))
                    )
                    if data2.get('list'):
                        data = data2
                lst = []
                for it in (data.get('list') or []):
                    cleaned = self._clean_item(it, sk, so['name'], False)
                    cleaned['vod_pic'] = self._fix_pic(cleaned.get('vod_pic'))
                    lst.append(cleaned)
                return lst, int(data.get('pagecount') or 1)
            except Exception as e:
                print('search skip', sk, e)
                return [], 1

        try:
            from concurrent.futures import ThreadPoolExecutor, as_completed
            # 先跑优先源，再跑其余
            with ThreadPoolExecutor(max_workers=6) as pool:
                futs = [pool.submit(_one, it) for it in items]
                for fut in as_completed(futs, timeout=35):
                    try:
                        lst, pc = fut.result(timeout=1)
                        result.extend(lst)
                        if pc > max_page:
                            max_page = pc
                    except Exception:
                        pass
        except Exception:
            for it in items[:20]:
                lst, pc = _one(it)
                result.extend(lst)
                if pc > max_page:
                    max_page = pc
        return {
            'list': result,
            'page': pg,
            'pagecount': max_page,
            'limit': 40,
            'total': 9999,
        }

    def _request_timeout(self, url, timeout=15, extra_headers=None):
        try:
            hdr = dict(self.headers)
            if extra_headers:
                hdr.update(extra_headers)
            if requests is None:
                import urllib.request, ssl
                ctx = ssl.create_default_context()
                ctx.check_hostname = False
                ctx.verify_mode = ssl.CERT_NONE
                req = urllib.request.Request(url, headers=hdr)
                with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
                    return r.read().decode('utf-8', 'ignore')
            r = requests.get(url, headers=hdr, timeout=timeout, verify=False)
            r.encoding = 'utf-8'
            return r.text if r.status_code == 200 else ''
        except Exception as e:
            print('request error', url, e)
            return ''


    def _parse_url(self, token_url, prefer=''):
        """绅士影视解析逻辑：多解析站轮询取直链"""
        from urllib.parse import quote
        if not token_url:
            return ''
        # 已是直链
        if re.search(r'\.(m3u8|mp4|flv|mkv)(\?|$)', token_url, re.I):
            return token_url
        cands = []
        if prefer:
            for p in self.PARSERS:
                if prefer in p['api'] or prefer in p.get('name', ''):
                    cands.append(p)
        # co_ 令牌：组豪富英 / 12321 优先
        if token_url.startswith('co_') or token_url.startswith('CO4K') or 'tvapp' in (prefer or ''):
            for p in self.PARSERS:
                if p['name'] in ('组豪富英', '12321') and p not in cands:
                    cands.insert(0, p)
        # 页面链接优先 huaqi / 12321
        if re.search(r'https?://', token_url) and not re.search(r'\.(m3u8|mp4)(\?|$)', token_url, re.I):
            for p in self.PARSERS:
                if p['name'] in ('huaqi', '12321') and p not in cands:
                    cands.insert(0, p)
        cands += [p for p in self.PARSERS if p not in cands]
        urls_try = [token_url]
        try:
            enc = quote(token_url, safe='')
            if enc != token_url:
                urls_try.append(enc)
        except Exception:
            pass
        for p in cands:
            for tu in urls_try:
                try:
                    txt = self._request(p['api'] + tu)
                    if not txt:
                        continue
                    data = self._safe_json(txt)
                    if isinstance(data, dict):
                        code = data.get('code')
                        ok = True
                        if code is not None:
                            try:
                                ok = int(code) in (200, 0, 1, 2000)
                            except Exception:
                                ok = True
                        if ok:
                            for k in ('url', 'Url', 'URL', 'play_url', 'play', 'm3u8', 'data'):
                                v = data.get(k)
                                if isinstance(v, dict):
                                    v = v.get('url') or v.get('play')
                                if isinstance(v, str) and v.startswith('http'):
                                    return v.replace('\\/', '/')
                    for m in re.finditer(r'"(?:url|Url|URL|play_url|data|play)"\s*:\s*"([^"]+)"', txt):
                        v = m.group(1).replace('\\/', '/')
                        if re.search(r'\.(m3u8|mp4)', v, re.I) or v.startswith('http'):
                            return v
                    m = re.search(r'https?://[^"\'\s<>]+?\.(?:m3u8|mp4)[^"\'\s<>]*', txt.replace('\\/', '/'))
                    if m:
                        return m.group(0)
                except Exception:
                    continue
        return ''

    def playerContent(self, flag, id, vipFlags):
        play_url = self._text(id)
        if ';' in play_url and re.search(r'https?://', play_url, re.I):
            first = play_url.split(';')[0]
            if re.match(r'^https?://', first, re.I):
                play_url = first
        # 独播库 type5：网盘链接含 | 或 @@，调接口解析真实地址
        if ('|' in play_url and '@@' in play_url) or (
            'pan.baidu.com' in play_url or 'pan.quark.cn' in play_url or 'drive.uc.cn' in play_url
        ):
            api = self.SOURCES.get('s44', {}).get('api') or ''
            fl = self._text(flag)
            if '-' in fl:
                fl = fl.split('-', 1)[-1]
            # flag 用空或站点名均可
            resp = self._request(self._build_url(api, {'flag': fl or '', 'play': play_url}))
            j = self._safe_json(resp) or {}
            real = j.get('url')
            if isinstance(real, list):
                # ["RAW", "https://..."] 或 ["proxy", "..."]
                for x in real:
                    xs = self._text(x)
                    if xs.startswith('http'):
                        real = xs
                        break
                else:
                    real = self._text(real[-1] if real else '')
            else:
                real = self._text(real or '')
            hdr = {'User-Agent': self.UA}
            hraw = j.get('header')
            if isinstance(hraw, str):
                try:
                    hraw = json.loads(hraw)
                except Exception:
                    hraw = None
            if isinstance(hraw, dict):
                hdr.update({k: v for k, v in hraw.items() if v})
            if real:
                return {'parse': 0, 'jx': 0, 'url': real, 'header': hdr}
            return {'parse': 0, 'jx': 0, 'url': play_url, 'header': hdr}
        # 旧 dbo：相对 /play/xxx → 调接口取 m3u8（必须带 Referer）
        if play_url.startswith('/play/') or re.match(r'^\d+-ep\d+', play_url):
            path = play_url if play_url.startswith('/') else '/play/' + play_url
            fl = self._text(flag)
            if '-' in fl:
                fl = fl.split('-', 1)[-1]
            if not fl or fl.startswith('🐾') or fl.startswith('s') or '荐片' in fl or '独播' in fl:
                fl = '独播库内网'
            api = 'http://bob2.hkt.net.cn/miraplay/dbo.php'
            dbo_hdr = {
                'User-Agent': self.UA,
                'Referer': 'https://www.dbkk.cc/',
                'Origin': 'https://www.dbkk.cc',
            }
            resp = self._request(
                self._build_url(api, {'flag': fl, 'play': path}),
                extra_headers=dbo_hdr,
            )
            try:
                j = self._safe_json(resp) or {}
            except Exception:
                j = {}
            real = self._text(j.get('url') or '')
            hdr = {
                'User-Agent': self.UA,
                'Referer': 'https://www.dbkk.cc/',
                'Origin': 'https://www.dbkk.cc',
            }
            hraw = j.get('header')
            if isinstance(hraw, str):
                try:
                    hraw = json.loads(hraw)
                except Exception:
                    hraw = None
            if isinstance(hraw, dict):
                for k, v in hraw.items():
                    if v:
                        hdr[k] = v
            if real:
                return {'parse': 0, 'jx': 0, 'url': real, 'header': hdr}
            # 二次尝试：去掉 /play/ 前缀
            if path.startswith('/play/'):
                resp2 = self._request(
                    self._build_url(api, {'flag': fl, 'play': path[6:]}),
                    extra_headers=dbo_hdr,
                )
                j2 = self._safe_json(resp2) or {}
                real2 = self._text(j2.get('url') or '')
                if real2:
                    return {'parse': 0, 'jx': 0, 'url': real2, 'header': hdr}
            return {'parse': 0, 'jx': 0, 'url': '', 'header': hdr}
        need_parse = not self.isVideoFormat(play_url)
        if not need_parse:
            return {
                'parse': 0, 'jx': 0, 'url': play_url,
                'header': {'User-Agent': self.UA},
            }
        # 需要解析：按 flag/源 优先匹配解析站
        prefer = ''
        fl = self._text(flag)
        # 从 SOURCES 找 parser 标记
        for sk, so in self.SOURCES.items():
            if so.get('parse') and (sk in fl or so['name'].replace('🐾', '') in fl or so['name'] in fl):
                prefer = so.get('parser') or ''
                break
        # token 前缀 / 线路名提示（绅2K·P = co_ → tvapp）
        if not prefer:
            for tk, api in (('CO4K', '175.24.181.180'), ('co_egg', 'tvapp.eu.org'),
                            ('co_', 'tvapp.eu.org'), ('zijian', '123jx.vip'), ('JYY', '175.24.181.180')):
                if play_url.startswith(tk):
                    prefer = api
                    break
        if not prefer and ('绅2K' in fl or '2K·P' in fl or fl.endswith('-co') or fl == 'co'):
            prefer = 'tvapp.eu.org'
        if not prefer and ('绅4K·E' in fl or '极影' in fl):
            prefer = '175.24.181.180'
        if not prefer and ('绅4K·C' in fl or '4K·C' in fl):
            prefer = '123jx.vip'
        if not prefer and ('绅4K·P' in fl or '4K·P' in fl):
            prefer = 'jx.meilinvps.com'
        # co_ 令牌必须优先用组豪富英
        if play_url.startswith('co_') or play_url.startswith('CO4K'):
            prefer = prefer or 'tvapp.eu.org'
        real = self._parse_url(play_url, prefer)
        if real:
            return {
                'parse': 0, 'jx': 0, 'url': real,
                'header': {'User-Agent': self.UA},
            }
        return {
            'parse': 1, 'jx': 1, 'url': play_url,
            'header': {'User-Agent': self.UA},
        }

    def localProxy(self, param):
        return None
