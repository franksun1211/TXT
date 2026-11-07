# -*- coding: utf-8 -*-
import sys, os, json, time, threading, re, urllib.request, urllib.parse, http.cookiejar, traceback, base64, codecs, ssl

try:
    from java import jclass, dynamic_proxy
except ImportError:
    jclass = None
    dynamic_proxy = None

try:
    from base.spider import Spider as BaseSpider
except Exception:
    BaseSpider = object

try:
    import requests
except Exception:
    requests = None

CONFIG = {}
_LOG_BUF = []
_LOG_MAX = 300

# CF cookie 缓存目录
CF_COOKIE_DIR = '/storage/emulated/0/tmp/123'

HOST = 'https://supjav.com'
LK_BASE = 'https://lk1.supremejav.com/supjav.php'

# 符合 Android WebView 特征的 Chrome 移动端 UA
UA_MOBILE = "Mozilla/5.0 (Linux; Android 12; Pixel 6 Build/SQ3A.220705.004; wv) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/120.0.6099.210 Mobile Safari/537.36"

# 分类列表
CATS = [
    ('__home', '最新'),
    ('__popular', '热门'),
    ('censored-jav', '有码 Censored'),
    ('uncensored-jav', '无码 Uncensored'),
    ('amateur', '素人 Amateur'),
    ('chinese-subtitles', '中文字幕 Chn Sub'),
    ('reducing-mosaic', '破解 Reducing Mosaic'),
    ('english-subtitles', '英文字幕 Eng Sub'),
]

SORTS = [
    {'key': 'sort', 'name': '排序',
     'value': [{'n': '最新', 'v': ''}, {'n': '最多观看', 'v': 'views'}]},
]

# ---------- 动态代理类辅助 ----------
_dialog_proxy_classes = {}

def _ensure_dialog_proxy_classes():
    if _dialog_proxy_classes:
        return _dialog_proxy_classes
    Runnable = jclass("java.lang.Runnable")
    DialogInterface = jclass("android.content.DialogInterface")

    class _RunProxy(dynamic_proxy(Runnable)):
        def run(self):
            fn = getattr(self, "_fn", None)
            if fn is not None:
                try:
                    fn()
                except Exception:
                    pass

    class _ClickProxy(dynamic_proxy(DialogInterface.OnClickListener)):
        def onClick(self, dialog, which):
            fn = getattr(self, "_fn", None)
            if fn is not None:
                try:
                    fn(dialog, which)
                except Exception:
                    pass

    _dialog_proxy_classes["run"] = _RunProxy
    _dialog_proxy_classes["click"] = _ClickProxy
    return _dialog_proxy_classes


def dp2px(act, dp):
    TypedValue = jclass("android.util.TypedValue")
    metrics = act.getResources().getDisplayMetrics()
    return int(TypedValue.applyDimension(TypedValue.COMPLEX_UNIT_DIP, float(dp), metrics))


class MiniDialog(object):
    def __init__(self, spider=None, config=None):
        cfg = config or {}
        self.spider = spider
        self.width_ratio = float(cfg.get("width_ratio", 0.88))
        self.max_refs = int(cfg.get("max_refs", 60))
        self._refs = []

    def log(self, msg):
        try:
            if self.spider is not None and hasattr(self.spider, "_log"):
                self.spider._log(msg)
                return
        except Exception:
            pass

    def _activity(self):
        try:
            AT = jclass("java.lang.Class").forName("android.app.ActivityThread")
            cur = AT.getMethod("currentActivityThread").invoke(None)
            f = AT.getDeclaredField("mActivities")
            f.setAccessible(True)
            map_obj = f.get(cur)
            values = map_obj.values().toArray() if hasattr(map_obj, "values") else map_obj.toArray()
            for r in values:
                rc = r.getClass()
                pf = rc.getDeclaredField("paused")
                pf.setAccessible(True)
                if not pf.getBoolean(r):
                    af = rc.getDeclaredField("activity")
                    af.setAccessible(True)
                    a = af.get(r)
                    if a:
                        return a
        except Exception:
            pass
        return None

    def available(self):
        return not (jclass is None or dynamic_proxy is None)

    def _keep(self, *objs):
        self._refs.extend([o for o in objs if o is not None])
        if len(self._refs) > self.max_refs:
            self._refs = self._refs[-self.max_refs:]

    def run_on_ui(self, ui_fn):
        if not self.available():
            return False
        act = self._activity()
        if not act:
            return False
        p = _ensure_dialog_proxy_classes()

        def _make():
            r = p["run"]()
            r._fn = lambda: ui_fn(act)
            return r

        try:
            r = _make()
            act.getWindow().getDecorView().post(r)
            self._keep(r)
        except Exception:
            Handler = jclass("android.os.Handler")
            Looper = jclass("android.os.Looper")
            r2 = _make()
            Handler(Looper.getMainLooper()).post(r2)
            self._keep(r2)
        return True

    def run_bg(self, fn, *args, **kwargs):
        def _work():
            try:
                fn(*args, **kwargs)
            except Exception as e:
                self.log("后台任务异常: " + str(e))
        t = threading.Thread(target=_work, daemon=True)
        t.start()
        return t

    def _make_click(self, fn, *args):
        p = _ensure_dialog_proxy_classes()
        c = p["click"]()
        c._fn = lambda d, w: fn(d, w, *args)
        self._keep(c)
        return c


_WV_ANCHOR = {}
_VC_PROXY = None

def _ensure_vc_proxy():
    global _VC_PROXY
    if _VC_PROXY is None:
        VC = jclass('android.webkit.ValueCallback')
        base = dynamic_proxy(VC)
        class _VC(base):
            def onReceiveValue(self, value):
                fn = getattr(self, '_fn', None)
                if fn:
                    fn(value)
        _VC_PROXY = _VC
    return _VC_PROXY


# ---------- Spider 核心类 ----------
class Spider(BaseSpider):

    def getName(self):
        return 'SupJav'

    def init(self, extend=''):
        global CONFIG
        if extend:
            try:
                ext = json.loads(extend) if isinstance(extend, str) else extend
                if isinstance(ext, dict):
                    CONFIG.update(ext)
            except Exception:
                pass
        self.dlg = MiniDialog(self)
        self._last_wv_ua = ''
        self._cf_restore()

    def destroy(self):
        pass

    def localProxy(self, param):
        return [404, 'text/plain', '']

    def isVideoFormat(self, url):
        return bool(url and re.search(r'\.(m3u8|mp4|ts)(\?|$)', url, re.I))

    def _log(self, msg):
        global _LOG_BUF
        line = "[%s] %s" % (time.strftime('%H:%M:%S'), msg)
        _LOG_BUF.append(line)
        if len(_LOG_BUF) > _LOG_MAX:
            _LOG_BUF[:] = _LOG_BUF[-_LOG_MAX:]
        print(line)

    # ---------- CF Cookie 本地文件读写与恢复 ----------
    def _domain(self, url):
        return urllib.parse.urlparse(url).netloc or 'supjav.com'

    def _cf_path(self, domain):
        return os.path.join(CF_COOKIE_DIR, domain + '.json')

    def _cf_load(self, domain='supjav.com'):
        p = self._cf_path(domain)
        if not os.path.exists(p):
            p = self._cf_path('supjav.com')
        try:
            if os.path.exists(p):
                with open(p, 'r', encoding='utf-8') as f:
                    d = json.load(f)
                if d.get('cookies'):
                    return d
        except Exception as e:
            self._log('读取 CF cookie 缓存失败: ' + str(e))
        return None

    def _cf_save(self, domain, cookies, ua):
        try:
            os.makedirs(CF_COOKIE_DIR, exist_ok=True)
            with open(self._cf_path(domain), 'w', encoding='utf-8') as f:
                json.dump({'cookies': cookies, 'ua': ua, 'ts': int(time.time())},
                          f, ensure_ascii=False, indent=2)
            self._log('已保存 CF cookie -> ' + self._cf_path(domain))
        except Exception as e:
            self._log('保存 CF cookie 失败: ' + str(e))

    def _cf_restore(self):
        try:
            d = self._cf_load('supjav.com')
            if not (d and d.get('cookies')) or jclass is None:
                return
            cm = jclass('android.webkit.CookieManager').getInstance()
            cm.setAcceptCookie(True)
            for k, v in d['cookies'].items():
                cm.setCookie('https://supjav.com', '%s=%s; domain=.supjav.com; path=/' % (k, v))
            cm.flush()
            self._log('[恢复] CookieManager 写入完毕')
        except Exception as e:
            self._log('[恢复] 写回 CookieManager 失败: ' + str(e))

    # ---------- 页面有效性判断 ----------
    def _is_cf(self, text):
        if not text or len(text) < 200:
            return True
        low = text.lower()
        cf_keywords = [
            'just a moment', 'checking your browser', 'cf-mitigated',
            'challenge-platform', 'enable javascript and cookies to continue',
            'attention required! | cloudflare', 'verify you are human', 'turnstile'
        ]
        return any(k in low for k in cf_keywords)

    def _is_valid_page(self, text):
        if not text or len(text) < 500:
            return False
        if self._is_cf(text):
            return False
        low = text.lower()
        if 'supjav' in low or 'class="post"' in low or '<h1' in low or 'category' in low:
            return True
        return False

    # ---------- 核心页面获取流程 ----------
    def _get_html(self, url):
        # 1. 优先使用“隐式后台 WebView”加载（已保存 Cookie 时，Cloudflare 会在 1 秒内无感直过，绝不弹窗）
        html, _ = self._wv_fetch(url, silent=True, timeout=8)
        if self._is_valid_page(html):
            self._log('隐式复用 Cookie 成功（完全无感，未触发弹窗）')
            return html

        self._log('Cookie 失效或缺少安全凭证，弹出可视化窗口重新验证...')
        # 2. 只有当 Cookie 彻底失效导致隐式超时，才弹出可视化界面供人机验证
        html, _ = self._wv_fetch(url, silent=False, timeout=40)
        if self._is_valid_page(html):
            self._log('可视化验证成功，Cookie 已更新！')
            return html

        self._log('页面获取失败')
        return ''

    def _wv_fetch(self, url, silent=True, timeout=30):
        if jclass is None or self.dlg is None:
            return None, 'no_jclass'

        cached = self._cf_load(self._domain(url))
        target_ua = (cached.get('ua') if cached else None) or self._last_wv_ua or UA_MOBILE

        evt = threading.Event()
        store = {'html': None, 'err': None}
        is_finished = [False]

        def on_ui(act):
            WebView = jclass('android.webkit.WebView')
            WebViewClient = jclass('android.webkit.WebViewClient')
            CookieManager = jclass('android.webkit.CookieManager')

            wv = WebView(act)
            settings = wv.getSettings()
            
            settings.setJavaScriptEnabled(True)
            settings.setDomStorageEnabled(True)
            settings.setDatabaseEnabled(True)
            settings.setJavaScriptCanOpenWindowsAutomatically(True)
            settings.setLoadsImagesAutomatically(True)
            
            try:
                settings.setMixedContentMode(0)
            except Exception:
                pass
            
            settings.setUserAgentString(str(target_ua))

            try:
                self._last_wv_ua = str(settings.getUserAgentString())
            except Exception:
                pass

            wv.setWebViewClient(WebViewClient())
            dialog_ref = [None]
            decor = act.getWindow().getDecorView()

            def do_cleanup():
                try:
                    if silent and wv.getParent() is not None:
                        decor.removeView(wv)
                except Exception:
                    pass
                try:
                    wv.stopLoading()
                    wv.loadUrl("about:blank")
                    wv.clearHistory()
                    wv.removeAllViews()
                    wv.destroy()
                except Exception:
                    pass

            def finish(html_val, err_val=None):
                if is_finished[0]:
                    return
                is_finished[0] = True
                store['html'] = html_val
                store['err'] = err_val

                def _ui_close(a):
                    if dialog_ref[0]:
                        try:
                            dialog_ref[0].dismiss()
                        except Exception:
                            pass
                    do_cleanup()

                if self.dlg:
                    self.dlg.run_on_ui(_ui_close)
                else:
                    _ui_close(None)
                evt.set()

            vc = _ensure_vc_proxy()()
            def _on_val(value):
                if is_finished[0]:
                    return
                try:
                    html = json.loads(value) if isinstance(value, str) else str(value)
                except Exception:
                    html = str(value) if value is not None else ''

                if self._is_valid_page(html):
                    try:
                        CookieManager.getInstance().flush()
                        raw = CookieManager.getInstance().getCookie(url) or ''
                        cks = {}
                        for p in raw.split(';'):
                            if '=' in p:
                                k, v = p.split('=', 1)
                                cks[k.strip()] = v.strip()
                        if cks:
                            self._cf_save(self._domain(url), cks, self._last_wv_ua or target_ua)
                            self._cf_restore()
                    except Exception as e:
                        self._log('保存 Cookie 异常: ' + str(e))
                    finish(html, None)

            vc._fn = _on_val
            _WV_ANCHOR[id(vc)] = vc

            if silent:
                # 【关键修复】：静默模式下将 WebView 以 1x1 像素隐藏挂载到 DecorView，保证系统不冻结 JS 引擎
                try:
                    FrameLayout = jclass('android.widget.FrameLayout')
                    lp = FrameLayout.LayoutParams(1, 1)
                    decor.addView(wv, lp)
                except Exception:
                    pass
            else:
                # 非静默模式：弹出包含 WebView 的可视化窗口
                LinearLayout = jclass('android.widget.LinearLayout')
                TextView = jclass('android.widget.TextView')
                Builder = jclass('android.app.AlertDialog$Builder')
                LayoutParams = jclass('android.widget.LinearLayout$LayoutParams')

                match_parent = LayoutParams.MATCH_PARENT
                container = LinearLayout(act)
                container.setOrientation(LinearLayout.VERTICAL)
                
                tip = TextView(act)
                tip.setText('正在完成 Cloudflare 人机验证，完成后将自动刷新 Cookie...')
                pad = dp2px(act, 8)
                tip.setPadding(pad, pad, pad, pad)
                container.addView(tip)

                wv_lp = LayoutParams(match_parent, dp2px(act, 420))
                wv.setLayoutParams(wv_lp)
                container.addView(wv)

                builder = Builder(act)
                builder.setTitle('人机安全验证')
                builder.setView(container)
                builder.setNegativeButton('取消', self.dlg._make_click(lambda d, w: finish(None, 'cancelled')))
                dialog = builder.create()
                dialog.setCanceledOnTouchOutside(False)
                dialog_ref[0] = dialog
                dialog.show()

            wv.loadUrl(url)

            # 轮询监测 DOM 状态
            def poll_worker():
                start_t = time.time()
                while not is_finished[0]:
                    if time.time() - start_t > timeout:
                        finish(None, 'timeout')
                        break

                    def _check_ui(a):
                        if is_finished[0]:
                            return
                        try:
                            wv.evaluateJavascript('document.documentElement.outerHTML', vc)
                        except Exception:
                            pass

                    if self.dlg:
                        self.dlg.run_on_ui(_check_ui)
                    time.sleep(0.8)

            self.dlg.run_bg(poll_worker)

        self.dlg.run_on_ui(on_ui)
        evt.wait(timeout=timeout + 3)
        return store['html'], store['err']

    def _fetch_url(self, url, referer=None, timeout=10):
        cached = self._cf_load('supjav.com')
        ua = (cached.get('ua') if cached else None) or self._last_wv_ua or UA_MOBILE
        headers = {
            'User-Agent': ua,
            'Accept': '*/*',
            'Referer': referer or HOST + '/',
        }
        if cached and cached.get('cookies'):
            headers['Cookie'] = '; '.join('%s=%s' % (k, v) for k, v in cached['cookies'].items())

        try:
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
                data = resp.read()
                return data.decode('utf-8', 'ignore')
        except Exception:
            pass
        return ''

    # ---------- 分类与业务逻辑 ----------
    def _cards(self, html):
        out, seen = [], set()
        blocks = re.split(r'<div class="post">', html)[1:]
        for b in blocks:
            m = re.search(r'href="' + re.escape(HOST) + r'/(\d+)\.html"', b)
            if not m:
                continue
            vid = m.group(1)
            if vid in seen:
                continue
            t = re.search(r'title="([^"]+)"', b)
            title = t.group(1) if t else ''
            title = (title.replace('&amp;', '&').replace('&#8217;', "'")
                     .replace('&quot;', '"').replace('&#8211;', '-')).strip()
            if not title:
                continue
            seen.add(vid)
            pic = ''
            for pat in (r'<img[^>]+data-original="([^"]+)"',
                        r'<img[^>]+data-src="([^"]+)"',
                        r'<img[^>]+src="(https?://[^"]+)"'):
                pm = re.search(pat, b)
                if pm:
                    pic = pm.group(1)
                    break
            if pic.startswith('//'):
                pic = 'https:' + pic

            code = ''
            cm = re.search(r'\b([A-Z]{2,6}-?\d{2,6}|FC2PPV[\s-]?\d{5,8})\b', title)
            if cm:
                code = cm.group(1)
            out.append({
                'vod_id': HOST + '/' + vid + '.html',
                'vod_name': title[:90],
                'vod_pic': pic,
                'vod_remarks': code,
            })
        return out

    @staticmethod
    def _pagecount(html, cur):
        nums = [int(x) for x in re.findall(r'/page/(\d+)', html)]
        if not nums:
            return cur
        mx = max(nums)
        return mx if 0 < mx <= 5000 else cur

    def homeContent(self, filter):
        classes = [{'type_id': cid, 'type_name': cname} for cid, cname in CATS]
        filters = {}
        for cid, _ in CATS:
            filters[cid] = SORTS
        return {'class': classes, 'filters': filters}

    def categoryContent(self, tid, pg, filter, extend):
        page = max(1, int(pg or 1))
        tid = str(tid).strip()
        ext = extend if isinstance(extend, dict) else {}
        sort = str(ext.get('sort') or '').strip()

        if tid == '__home':
            url = HOST + '/' if page == 1 else HOST + '/page/%d/' % page
        elif tid == '__popular':
            url = (HOST + '/popular/' if page == 1 else HOST + '/popular/page/%d/' % page)
        else:
            base = HOST + '/category/' + tid
            url = base + ('/' if page == 1 else '/page/%d/' % page)
            if sort:
                url += '?sort=' + urllib.parse.quote(sort)

        html = self._get_html(url)
        items = self._cards(html)
        return {'page': page, 'pagecount': self._pagecount(html, page), 'limit': len(items) or 24, 'total': 9999, 'list': items}

    def searchContent(self, key, quick, pg="1"):
        page = max(1, int(pg or 1))
        kw = urllib.parse.quote(str(key))
        url = (HOST + '/?s=' + kw) if page == 1 else (HOST + '/page/%d/?s=%s' % (page, kw))
        html = self._get_html(url)
        items = self._cards(html)
        return {'page': page, 'pagecount': self._pagecount(html, page), 'limit': len(items) or 24, 'total': 9999, 'list': items}

    def detailContent(self, ids):
        url = ids[0] if isinstance(ids, (list, tuple)) else ids
        if not url.startswith('http'):
            url = HOST + '/' + str(url)
        html = self._get_html(url)

        title = ''
        tm = re.search(r'<h1[^>]*>(.*?)</h1>', html, re.S)
        if tm:
            title = re.sub(r'<[^>]+>', '', tm.group(1)).strip()

        pic = ''
        pm = re.search(r'background-image:\s*url\((https://img\.supjav\.com/[^)]+)\)', html)
        if pm:
            pic = pm.group(1)
        if not pic:
            im = re.search(r'(https://img\.supjav\.com/[^\s"\'<>)]+\.(?:jpg|jpeg|png|webp)[^\s"\'<>)]*)', html, re.I)
            if im:
                pic = im.group(1)

        links = re.findall(r'data-link="([0-9a-f]{40,})"', html)
        names = re.findall(r'data-link="[0-9a-f]{40,}"[^>]*>([^<]{1,12})<', html)
        pairs = []
        for i, lk in enumerate(links):
            nm = names[i].strip() if i < len(names) else ('线路%d' % (i + 1))
            pairs.append((nm, '正片$%s|%s' % (url, lk)))

        froms = [p[0] for p in pairs]
        urls = [p[1] for p in pairs]

        vod = {
            'vod_id': url,
            'vod_name': title or 'SupJav Video',
            'vod_pic': pic,
            'vod_play_from': '$$$'.join(froms) if froms else 'SupJav',
            'vod_play_url': '$$$'.join(urls) if urls else ('正片$%s|' % url),
        }
        return {'list': [vod]}

    def playerContent(self, flag, id, vipFlags):
        raw = str(id)
        detail, _, lk = raw.partition('|')
        fail = {'parse': 0, 'playUrl': '', 'url': '', 'jx': 0}

        if not lk:
            return fail

        s1_url = LK_BASE + '?l=' + lk
        s1 = self._fetch_url(s1_url, referer=detail)

        om = re.search(r"var\s+OLID\s*=\s*'([0-9a-f]{40,})'", s1 or '')
        olid = om.group(1)[::-1] if om else lk[::-1]

        s2_url = LK_BASE + '?c=' + olid
        s2 = self._fetch_url(s2_url, referer=s1_url)
        if not s2:
            return fail

        hits = re.findall(r'https?://[^\s"\'<>\\]+\.m3u8[^\s"\'<>\\]*', s2)
        play_url = hits[0] if hits else ''
        
        if not play_url:
            return fail

        play_url = play_url.replace('\\/', '/').replace('&amp;', '&')
        cached = self._cf_load('supjav.com')
        ua = (cached.get('ua') if cached else None) or self._last_wv_ua or UA_MOBILE

        return {'parse': 0, 'url': play_url, 'header': {'User-Agent': ua, 'Referer': HOST + '/'}}
