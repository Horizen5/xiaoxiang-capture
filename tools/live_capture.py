# -*- coding: utf-8 -*-
"""旁路记账守护进程 v3：用户手动操作小程序，本脚本只挂 hook 抄响应。

v3 相对 v2 的改动（针对"顶部这一行应该是该二级对应的三级标签，而不是二级列表"）：
1. 记账键加入「三级分类 id」（thirdCid），文件名 c{cid}_f{first}_t{third}_o{off}.json。
   v2 里「低价捡漏」和「限时秒杀」的 categoryId 同为 48602、offset 都从 0 开始，
   同名文件互相覆盖 —— 这是三级数据一直对不上的根因。
2. 三级 id 读取：优先请求体里的 third* 字段，其次读页面 data 的
   selectThirdCategory[currentIndex].id（页面里这两个数组是按一级索引的）。
   同时在 __M 里留下完整请求体，便于日后按真实字段收紧判定。
3. 记录页面当前所在位置（一级/二级/三级），看板可显示"正在看哪里"。

用法：python -u live_capture.py
"""
import asyncio, json, os, re, time, threading, urllib.request
import websockets

CDP_URL = 'ws://127.0.0.1:62000'
APP_HINT = 'wx92916b3adca84096'
BASE = os.path.dirname(os.path.abspath(__file__))
OUTDIR = os.path.join(BASE, 'live_raw')
IMGDIR = os.path.join(BASE, 'live_imgs')
STATUS = os.path.join(BASE, 'live_status.txt')
META = os.path.join(OUTDIR, '_meta.jsonl')
CUR = os.path.join(OUTDIR, '_current.json')
# 链路心跳：位置没变的时候 _current.json 不会重写，看板光看它分不清
# 「一直停在同一页」和「根本就没接上」。这个文件只回答一个问题：还连着吗。
LINK = os.path.join(OUTDIR, '_link.json')
os.makedirs(OUTDIR, exist_ok=True)
os.makedirs(IMGDIR, exist_ok=True)

PROBE = "(typeof wx!=='undefined'&&typeof wx.request==='function')?'REQ_OK':('no:'+typeof wx)"

# 用版本号判定；v3 从 v2 保存的原始 wx.request 重新包，避免叠加两层包装
HOOK = r"""(function(){
  if (globalThis.__LIVE_V === 6) return 'already-v6';
  var base = globalThis.__LIVE_ORIG || globalThis.__LIVE_V2_ORIG || wx.request;
  globalThis.__LIVE_ORIG = base;
  globalThis.__LIVE_V = 6;
  globalThis.__P = globalThis.__P || {};
  globalThis.__M = globalThis.__M || {};
  globalThis.__CAT = globalThis.__CAT || null;
  globalThis.__HITS = globalThis.__HITS || {sku:0, cat:0};

  // 只在分类列表页才认，避免在首页/搜索页时读到别的页面 data 造成误判
  function pageSel(){
    try {
      var ps = getCurrentPages ? (getCurrentPages()||[]) : [];
      var p = ps[ps.length-1]; if (!p) return {};
      var route = String(p.route || '');
      if (route.indexOf('categorySkuList') < 0) return {};
      var d = p.data || {};
      var idx = (typeof d.currentIndex === 'number') ? d.currentIndex : 0;
      var s2 = null, t3 = null;
      try { var a = d.selectSubCategory || []; if (a[idx]) s2 = a[idx].id; } catch(e){}
      try { var b = d.selectThirdCategory || []; if (b[idx]) t3 = b[idx].id; } catch(e){}
      if (t3 == null && d.thirdCategoryId) t3 = d.thirdCategoryId;
      var sn = null, tn = null;
      try { sn = (d.selectSubCategory||[])[idx] ? (d.selectSubCategory||[])[idx].name : null; } catch(e){}
      try { tn = (d.selectThirdCategory||[])[idx] ? (d.selectThirdCategory||[])[idx].name : null; } catch(e){}
      return { route: route, idx: idx, sub: s2, third: t3, top: d.categoryId,
               subName: sn, thirdName: tn };
    } catch(e){ return {}; }
  }
  // 暴露给外部轮询：用户不发请求（命中缓存）时也能读到当前分类位置
  globalThis.__SEL = pageSel;

  // 隐身：WMPF 在调试模式下会往全局注入几个调试框架变量，
  // 风险 SDK 一个 typeof 就能判定「这不是真机正式版」。它们都可配置，直接删。
  // 另外把我们自己的记账变量改成不可枚举，避免被遍历扫到。
  function _scrub(){
    var dbg = ['__isAppServiceRemoteDebugMode__', '__remoteDebug__', '__debuggerMessager__',
               '__remoteDebugMap__', '__devtools__', '__wxDebugger__', '__debugConsole__'];
    for (var i = 0; i < dbg.length; i++) {
      try { if (dbg[i] in globalThis) delete globalThis[dbg[i]]; } catch(e) {}
    }
    var mine = ['__LIVE_V', '__LIVE_ORIG', '__P', '__M', '__CAT', '__HITS',
                '__SEL', '__TPL', '__REQ', '__TPLGET', '__ENV', '__CUR', '__SC'];
    for (var j = 0; j < mine.length; j++) {
      try {
        var d = Object.getOwnPropertyDescriptor(globalThis, mine[j]);
        if (d && d.enumerable) {
          Object.defineProperty(globalThis, mine[j], {value: globalThis[mine[j]],
            enumerable: false, configurable: true, writable: true});
        }
      } catch(e) {}
    }
  }
  try { Object.defineProperty(globalThis, '__SC',
        {value: _scrub, enumerable: false, configurable: true, writable: true}); } catch(e) {}
  _scrub();

  // 环境自检：把风控可能读到的「调试特征」原样记下来，
  // 这样能判断验证码到底是被哪个信号触发的，而不是靠猜。
  try {
    var _si = (typeof wx.getSystemInfoSync === 'function') ? wx.getSystemInfoSync() : {};
    globalThis.__ENV = {
      enableDebug: _si.enableDebug,
      platform: _si.platform,
      SDKVersion: _si.SDKVersion,
      version: _si.version,
      host: _si.host || null,
      cfgDebug: (typeof __wxConfig !== 'undefined' ? __wxConfig.debug : null),
      envVersion: (function(){ try { return wx.getAccountInfoSync().miniProgram.envVersion; } catch(e){ return null; } })(),
      reqNative: (function(){ try { return String(wx.request).slice(0, 80); } catch(e){ return null; } })(),
      globals: Object.getOwnPropertyNames(globalThis).filter(function(k){ return k.indexOf('__') === 0; }).slice(0, 60),
      scrubbed: (function(){
        var left = ['__isAppServiceRemoteDebugMode__', '__remoteDebug__', '__debuggerMessager__']
          .filter(function(k){ return k in globalThis; });
        return left.length ? left : 'clean';
      })()
    };
  } catch(e) {}

  wx.request = function(o){
    var u = (o && o.url) || '';
    var isSku = u.indexOf('sku/list/category/v8') >= 0;
    var isCat = u.indexOf('category/list/v4') >= 0;
    if (!isSku && !isCat) return base.call(wx, o);
    var req = null;
    if (isSku) { try { req = o.data; } catch(e){} }
    // v5：留一份「真实成功请求」的模板（url / method / header / body），
    // 后台巡航时照着它重放，只改 pageInfo.offset，风控特征和真人翻页一致。
    if (isSku && req) {
      try {
        globalThis.__TPL = { url: u, method: (o.method || 'POST'),
                             header: (o.header || {}), data: req, t: Date.now() };
      } catch(e){}
    }
    var route = '';
    try {
      var ps = getCurrentPages ? (getCurrentPages()||[]) : [];
      var p = ps[ps.length-1];
      route = p ? (p.route + '|' + JSON.stringify(p.options||{})) : '';
    } catch(e){}
    var sc = o.success;
    o.success = function(r){
      try {
        if (isSku) {
          var d = r.data.data, pi = (d && d.pageInfo) || {};
          var items = d.contents[0].subModule.contents;
          var cid = req ? req.categoryId : -1;
          var off = req && req.pageInfo ? req.pageInfo.offset : -1;
          // 请求体里找三级：不同版本字段名不一致，全都试一遍
          var third = null;
          try {
            if (req) {
              third = req.thirdCategoryId
                || (req.thirdCategoryInfo && (req.thirdCategoryInfo.thirdCategoryId || req.thirdCategoryInfo.id))
                || (req.tagInfo && req.tagInfo.id) || null;
            }
          } catch(e){}
          var sel = pageSel();
          if (third == null && sel.third != null) third = sel.third;
          // 三级等于二级自己 = 「全部」，按未细分记账
          if (third != null && String(third) === String(cid)) third = null;
          var second = null;
          try { second = req.secondCategoryInfo ? req.secondCategoryInfo.secondCategoryId : null; } catch(e){}
          if (second == null && sel.sub != null) second = sel.sub;
          var key = cid + '_' + off + '_' + (third == null ? 0 : third);
          globalThis.__P[key] = { body: JSON.stringify(r.data), t: Date.now() };
          globalThis.__M[key] = {
            cid: cid,
            code: (r.data ? r.data.code : null),
            firstCid: req ? req.firstCategoryId : null,
            secondCid: second,
            thirdCid: third,
            off: off,
            limit: req && req.pageInfo ? req.pageInfo.limit : null,
            n: items.length,
            total: pi.total,
            pageSize: pi.pageSize,
            lastOff: pi.lastPageOffset,
            route: route,
            sel: sel,
            req: (function(){ try { var s = JSON.stringify(req); return s && s.length > 1200 ? s.slice(0,1200) : s; } catch(e){ return null; } })(),
            t: Date.now()
          };
          globalThis.__CUR = sel;
          globalThis.__HITS.sku++;
        } else {
          globalThis.__CAT = { t: Date.now(), body: JSON.stringify(r.data) };
          globalThis.__HITS.cat++;
        }
      } catch(e){}
      return sc && sc.apply(this, arguments);
    };
    return base.call(wx, o);
  };

  // v5：后台静默重放。直接借小程序自己的网络栈发原始请求：
  //   - 不碰 UI、不滚动、不切分类，页面停在哪就一直停在哪
  //   - 走原始 wx.request（不经过上面的 hook），所以不会污染 __P / __CUR
  globalThis.__REQ = function(bodyStr){
    return new Promise(function(resolve){
      function done(o){ resolve(JSON.stringify(o)); }
      var tpl = globalThis.__TPL;
      if (!tpl || !tpl.url) { done({ ok:false, err:'no-template' }); return; }
      var body; try { body = JSON.parse(bodyStr); } catch(e){ done({ ok:false, err:'bad-body' }); return; }
      globalThis.__LIVE_ORIG.call(wx, {
        url: tpl.url, method: tpl.method || 'POST', header: (tpl.header || {}), data: body,
        success: function(r){
          try {
            var d = r.data || {}, dd = d.data || {}, pi = dd.pageInfo || {};
            var n = 0;
            try { n = (dd.contents[0].subModule.contents || []).length; } catch(e){}
            done({ ok:true, code: d.code, msg: (d.msg || d.message || ''), n: n,
                   total: pi.total, lastOff: pi.lastPageOffset, body: JSON.stringify(r.data) });
          } catch(e){ done({ ok:false, err:'parse' }); }
        },
        fail: function(e){ done({ ok:false, err: String((e && e.errMsg) || e) }); }
      });
    });
  };
  globalThis.__TPLGET = function(){
    try { return JSON.stringify(globalThis.__TPL || null); } catch(e){ return 'null'; }
  };
  return 'v6-installed';
})()"""


def log(msg):
    line = '[%s] %s' % (time.strftime('%H:%M:%S'), msg)
    print(line, flush=True)
    try:
        with open(STATUS, 'a', encoding='utf-8') as f:
            f.write(line + '\n')
    except Exception:
        pass


class Conn:
    def __init__(self, ws):
        self.ws = ws
        self.i = 0
        self.pending = {}
        self.contexts = {}
        self.dead = False     # 调试代理重启 / 小程序关闭 → 置真，外层负责重连

    def _die(self):
        self.dead = True
        for fut in list(self.pending.values()):
            if not fut.done():
                fut.cancel()
        self.pending.clear()

    async def reader(self):
        # 连接断开不能把整个进程带走：标记 dead 后由 main() 重连即可
        try:
            async for raw in self.ws:
                try:
                    m = json.loads(raw)
                except Exception:
                    continue
                if 'id' in m:
                    fut = self.pending.pop(m['id'], None)
                    if fut and not fut.done():
                        fut.set_result(m)
                    continue
                if m.get('method') == 'Runtime.executionContextCreated':
                    ctx = m['params']['context']
                    self.contexts[(m.get('sessionId') or '', ctx.get('id'))] = ctx
        except Exception:
            pass
        finally:
            self._die()

    async def call(self, method, params=None, sessionId=None, tmo=8):
        if self.dead:
            raise ConnectionError('cdp-dead')
        self.i += 1
        mid = self.i
        msg = {'id': mid, 'method': method}
        if params:
            msg['params'] = params
        if sessionId:
            msg['sessionId'] = sessionId
        fut = asyncio.get_event_loop().create_future()
        self.pending[mid] = fut
        try:
            await self.ws.send(json.dumps(msg))
        except Exception:
            self._die()
            raise ConnectionError('cdp-send-fail')
        try:
            return await asyncio.wait_for(fut, timeout=tmo)
        except asyncio.TimeoutError:
            self.pending.pop(mid, None)
            return None
        except asyncio.CancelledError:
            # 连接断开时挂起的请求会被取消，交给外层重连
            raise ConnectionError('cdp-dead')

    async def ev(self, sid, cid, expr, tmo=25):
        p = {'expression': expr, 'returnByValue': True}
        if cid is not None:
            p['contextId'] = cid
        r = await self.call('Runtime.evaluate', p, sessionId=sid, tmo=tmo)
        if not r:
            return None
        res = r.get('result') or {}
        if 'exceptionDetails' in res:
            return 'EXC:' + json.dumps(res['exceptionDetails'], ensure_ascii=False)[:180]
        return (res.get('result') or {}).get('value')

    async def eva(self, sid, cid, expr, tmo=40):
        """evaluate + awaitPromise：用来等小程序里 __REQ 返回的 Promise。"""
        p = {'expression': expr, 'returnByValue': True, 'awaitPromise': True}
        if cid is not None:
            p['contextId'] = cid
        r = await self.call('Runtime.evaluate', p, sessionId=sid, tmo=tmo)
        if not r:
            return None
        res = r.get('result') or {}
        if 'exceptionDetails' in res:
            return 'EXC:' + json.dumps(res['exceptionDetails'], ensure_ascii=False)[:180]
        return (res.get('result') or {}).get('value')


async def find_ctx(c, tries=300):
    for n in range(tries):
        r = await c.call('Target.getTargets', tmo=8)
        if not r:
            await asyncio.sleep(2)
            continue
        infos = r['result']['targetInfos']
        cands = [t for t in infos if t['type'] in ('page', 'worker') and APP_HINT in (t.get('url') or '')]
        if not cands:
            if n % 8 == 0:
                log('等待小程序出现 ...（当前 %d 个 target）' % len(infos))
            await asyncio.sleep(2)
            continue
        for t in cands:
            ra = await c.call('Target.attachToTarget', {'targetId': t['targetId'], 'flatten': True}, tmo=5)
            sid = (ra or {}).get('result', {}).get('sessionId')
            if not sid:
                continue
            await c.call('Runtime.enable', sessionId=sid, tmo=4)
            await asyncio.sleep(0.8)
            for (s, cc), ctx in list(c.contexts.items()):
                if s != sid:
                    continue
                if await c.ev(sid, cc, PROBE, tmo=8) == 'REQ_OK':
                    return sid, cc
        await asyncio.sleep(2)
    return None, None


async def fetch_bodies(c, sid, cid, keys, conc=3):
    """并发取回多条响应体，串行时一次积压就会拖十几秒"""
    sem = asyncio.Semaphore(conc)

    async def one(k):
        async with sem:
            b = await c.ev(sid, cid, "(globalThis.__P[%s]||{}).body" % json.dumps(k), tmo=90)
            return k, b

    return await asyncio.gather(*[one(k) for k in keys])


IMG_QUEUE = []
IMG_SEEN = set()
IMG_LOCK = threading.Lock()


def img_worker():
    while True:
        with IMG_LOCK:
            url = IMG_QUEUE.pop(0) if IMG_QUEUE else None
        if not url:
            time.sleep(0.4)
            continue
        try:
            fn = url.rsplit('/', 1)[-1].split('?')[0]
            if not fn or '.' not in fn:
                fn = str(abs(hash(url))) + '.jpg'
            path = os.path.join(IMGDIR, fn)
            if os.path.exists(path):
                continue
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            data = urllib.request.urlopen(req, timeout=20).read()
            if len(data) > 500:
                with open(path, 'wb') as f:
                    f.write(data)
        except Exception:
            pass


def queue_images(body_txt):
    try:
        d = json.loads(body_txt)
        items = d['data']['contents'][0]['subModule']['contents']
    except Exception:
        return 0
    n = 0
    for it in items:
        pic = ((it.get('logicContents') or {}).get('detailPagePic') or '').strip()
        if not pic:
            continue
        if pic.startswith('//'):
            pic = 'https:' + pic
        elif pic.startswith('http://'):
            pic = 'https://' + pic[7:]
        with IMG_LOCK:
            if pic in IMG_SEEN:
                continue
            IMG_SEEN.add(pic)
            IMG_QUEUE.append(pic)
        n += 1
    return n


def dump_current(sel):
    try:
        with open(CUR, 'w', encoding='utf-8') as f:
            json.dump({'ts': time.time(), **sel}, f, ensure_ascii=False)
    except Exception:
        pass


def dump_link(ok, why=''):
    """写链路心跳。看板靠它判断「跟随」到底还连不连着，而不是傻等一个不动的位置。"""
    try:
        with open(LINK, 'w', encoding='utf-8') as f:
            json.dump({'ts': time.time(), 'ok': bool(ok), 'why': why}, f, ensure_ascii=False)
    except Exception:
        pass


# 小程序当前位置：由 sel_watcher 高频刷新，供看板秒级跟随。
# 不放在主循环里，否则会被抓取循环的 1.2 秒 sleep 拖慢。
SEL_STATE = {'sel': {}, 'ts': 0.0, 'raw': ''}


async def sel_watcher(c, holder):
    raw_last = ''
    last_beat = 0.0
    fails = 0
    while True:
        try:
            sid, cid = holder.get('sid'), holder.get('cid')
            if sid and cid:
                cs = await c.ev(
                    sid, cid,
                    "(function(){try{var s=(globalThis.__SEL?globalThis.__SEL():null);"
                    "if(s&&s.top!=null)return JSON.stringify(s);}catch(e){}"
                    "return JSON.stringify(globalThis.__CUR||{});})()", tmo=5)
                if cs and isinstance(cs, str):
                    fails = 0
                    # 心跳优先级最高：能读到就说明调试链路是通的
                    if time.time() - last_beat > 1.0:
                        last_beat = time.time()
                        dump_link(True)
                    if cs != raw_last and cs != '{}':
                        raw_last = cs
                        try:
                            sel = json.loads(cs)
                        except Exception:
                            sel = {}
                        # 只在分类页（有 top）才更新，离开分类页保持最后位置
                        if isinstance(sel, dict) and sel.get('top') is not None:
                            if sel != SEL_STATE['sel']:
                                SEL_STATE['sel'] = sel
                                SEL_STATE['ts'] = time.time()
                                SEL_STATE['raw'] = cs
                                dump_current(sel)
                else:
                    # 读不到：连续几次就把心跳打灭，看板立刻能看出「没接上」
                    fails += 1
                    if fails == 6:
                        dump_link(False, '位置读不到（小程序关了 / 调试通道断了）')
            else:
                fails += 1
                if fails == 6:
                    dump_link(False, '还没抓到小程序上下文')
        except Exception:
            fails += 1
            if fails == 6:
                dump_link(False, '位置监听异常')
        await asyncio.sleep(0.15)


# ---------------- 自动补页 ----------------
# 记录每个（二级, 三级）已经拿到的 offset，用来判断「还缺哪几页」。
PAGES = {}


def note_page(m):
    """把一个落盘页计入 PAGES（meta 里的 cid/thirdCid/off/lastOff）。"""
    try:
        cid = str(m.get('cid'))
        if cid in ('None', '-1'):
            return
        third = m.get('thirdCid')
        key = (cid, str(third) if third not in (None, 0, '0') else '0')
        rec = PAGES.setdefault(key, {'offs': set(), 'last': None})
        off = m.get('off')
        if isinstance(off, int) and off >= 0:
            rec['offs'].add(off)
        lo = m.get('lastOff')
        if isinstance(lo, int) and lo >= 0:
            rec['last'] = lo if rec['last'] is None else max(rec['last'], lo)
    except Exception:
        pass


async def dump_env(c, sid, cid):
    """把 hook 采到的环境特征落盘，用来定位验证码到底是被哪个信号触发的。"""
    try:
        s = await c.ev(sid, cid, "JSON.stringify(globalThis.__ENV||null)", tmo=8)
        if s and s != 'null':
            with open(os.path.join(OUTDIR, '_env.json'), 'w', encoding='utf-8') as f:
                f.write(s)
            log('环境自检: %s' % str(s)[:260])
    except Exception as e:
        log('环境自检失败: %s' % e)


def seed_pages():
    """启动时用磁盘上已有的落盘文件回填 PAGES，否则重启后不知道当前分类缺哪几页。"""
    n = 0
    try:
        names = os.listdir(OUTDIR)
    except Exception:
        return 0
    for fn in names:
        m = re.match(r'^c(\d+)((?:_[a-z]+\d+)*)_o(\d+)\.json$', fn)
        if not m:
            continue
        cid, mods, off = m.group(1), m.group(2) or '', int(m.group(3))
        kv = dict(re.findall(r'_([a-z]+)(\d+)', mods))
        third = kv.get('t') or '0'
        try:
            with open(os.path.join(OUTDIR, fn), encoding='utf-8') as f:
                pi = ((json.load(f).get('data') or {}).get('pageInfo') or {})
        except Exception:
            continue
        rec = PAGES.setdefault((cid, third), {'offs': set(), 'last': None})
        rec['offs'].add(off)
        lo = pi.get('lastPageOffset')
        if isinstance(lo, int) and lo >= 0:
            rec['last'] = lo if rec['last'] is None else max(rec['last'], lo)
        n += 1
    return n


def _page_key(cid, third):
    """(二级, 三级) 归一化成落盘用的键：三级等于二级自己 = 「全部」→ '0'。"""
    t = str(third) if third not in (None, 0, '0') else '0'
    if t == str(cid):
        t = '0'
    return str(cid), t


def gap_of(cid, third, ps=None):
    """返回 (已翻页数, 总页数, 缺的页数)；末页位置未知时总页数给 None。

    步长不能写死 20：不同分类的 pageInfo.limit 不一样（有的 10 有的 20），
    写死会把页数算错、导致该补的页被判定为「已齐」。
    """
    rec = PAGES.get(_page_key(cid, third))
    if not rec or not rec['offs']:
        return 0, None, None
    if rec['last'] is None:
        return len(rec['offs']), None, None
    if ps is None:
        offs = sorted(rec['offs'])
        diffs = [b - a for a, b in zip(offs, offs[1:]) if b > a]
        ps = min(diffs) if diffs else 20
    pt = rec['last'] // ps + 1
    pn = len([o for o in rec['offs'] if o <= rec['last']])
    return pn, pt, max(0, pt - pn)


async def page_session(c):
    """拿到渲染层（page-frame）的 sessionId，滚动事件要打在它上面。"""
    r = await c.call('Target.getTargets', tmo=6)
    if not r:
        return None
    for t in r.get('result', {}).get('targetInfos', []):
        u = t.get('url') or ''
        if t.get('type') == 'page' and APP_HINT in u and 'page-frame' in u:
            ra = await c.call('Target.attachToTarget',
                              {'targetId': t['targetId'], 'flatten': True}, tmo=4)
            return (ra or {}).get('result', {}).get('sessionId')
    return None


async def thumb_wheel(c, psid, times=3, dy=900):
    """向渲染层派发滚轮，触发 scroll-view 自己加载下一页。"""
    vw = await c.ev(psid, None,
                    "JSON.stringify({w:innerWidth,h:innerHeight,vis:document.visibilityState})", tmo=6)
    if not vw:
        return False
    try:
        vp = json.loads(vw)
    except Exception:
        return False
    if vp.get('vis') != 'visible':
        # 小程序在后台时滚动无效（页面栈不会动），直接放弃这一轮
        return False
    x, y = int(vp.get('w', 414) / 2), int(vp.get('h', 780) * 0.65)
    for _ in range(times):
        await c.call('Input.dispatchMouseEvent',
                     {'type': 'mouseWheel', 'x': x, 'y': y,
                      'deltaX': 0, 'deltaY': dy, 'modifiers': 0},
                     sessionId=psid, tmo=5)
        await asyncio.sleep(0.25)
    return True


# ---------- 后台自动巡航（脚本自己把缺的页一页页补完） ----------
# 节奏怎么定出来的（10-27 / 10-29 共 613 次真实请求统计）：
#   · 手动翻页最快持续速度 1.67～1.76 秒/次（任意 60 秒内最多 34 次）
#   · 这个速度下 600 个响应 code 全是 0，一次风控都没触发
#   于是把它当「安全阈值」，取 80% 速度 → 1.67 / 0.8 ≈ 2.1 秒/次。
CRUISE_ON = os.environ.get('CRUISE', '0') == '1'    # 默认关闭：手动翻页更安全；--cruise 或 CRUISE=1 才补页
CRUISE_PACE = float(os.environ.get('CRUISE_PACE', '2.1'))
CRUISE_IDLE_GATE = float(os.environ.get('CRUISE_IDLE', '40'))   # 用户停手多久后接管
CRUISE_MAX_FAIL = 4
RISK_PAUSE = float(os.environ.get('RISK_PAUSE', '300'))         # 疑似风控后歇多久

RISK = {'hits': 0, 'last': 0.0, 'why': '', 'paused_until': 0.0}


def risk_hit(why):
    """疑似被限流：立刻停巡航一段时间，并把证据落到 _risk.json 方便回查。"""
    now = time.time()
    RISK['hits'] += 1
    RISK['last'] = now
    RISK['why'] = why
    RISK['paused_until'] = now + RISK_PAUSE
    log('!! 疑似风控：%s —— 自动巡航暂停 %d 秒' % (why, int(RISK_PAUSE)))
    try:
        with open(os.path.join(OUTDIR, '_risk.json'), 'w', encoding='utf-8') as f:
            json.dump(RISK, f, ensure_ascii=False)
    except Exception:
        pass

# 不依赖窗口可见：直接在渲染层把滚动容器拉到底并派发 scroll，
# scroll-view 自己的到底判定会把「加载下一页」发给逻辑层。
_SCROLL_JS = r"""(function(){
  var out=[], n=0;
  var all=document.querySelectorAll('*');
  for(var i=0;i<all.length;i++){
    var el=all[i];
    var gap=el.scrollHeight-el.clientHeight;
    if(gap>200 && el.clientHeight>100){
      el.scrollTop = el.scrollHeight;
      try{ el.dispatchEvent(new Event('scroll',{bubbles:true})); }catch(e){}
      out.push(el.tagName.toLowerCase()+'@'+el.scrollTop+'/'+el.scrollHeight);
      if(++n>=3) break;
    }
  }
  return JSON.stringify({n:n,out:out});
})()"""

_fs_cache = {'id': None, 't': 0}


async def page_session_cached(c):
    """渲染层 sessionId 缓存 60 秒，避免每轮都 attach 一次。"""
    now = time.time()
    if _fs_cache['id'] and now - _fs_cache['t'] < 60:
        return _fs_cache['id']
    psid = await page_session(c)
    if psid:
        _fs_cache['id'] = psid
        _fs_cache['t'] = now
    return psid


_NUDGE_JS = r"""(function(){
  // 一次只触发一个翻页方法。以前把 loadNextPage / loadNextPageDebounce /
  // onReachBottom 全调一遍，等于同一下翻两三页（记录里同一秒出现 off=20 和 off=40），
  // 这是最像脚本的特征，也是这次 12 秒就被判异常的直接原因之一。
  try{
    var p=(getCurrentPages()||[]).slice(-1)[0];
    if(!p) return 'no-page';
    var fns=['loadNextPageDebounce','loadNextPage','onReachBottom'];
    for(var i=0;i<fns.length;i++){
      var k=fns[i];
      if(typeof p[k]==='function'){
        try{ p[k](); return k; }catch(e){ return k+':err'; }
      }
    }
    return 'no-method';
  }catch(e){ return 'exc:'+String(e).slice(0,80); }
})()"""


_RISK_WORDS = ('频繁', '风控', '限制', '验证', '异常', 'too many', 'forbidden')
_REPLAY = {'bad': 0, 't': 0.0}


def page_size_of(cid, third):
    """从已落盘的 offset 推断步长（不同分类 10/20 都有），推不出来才用 20。"""
    rec = PAGES.get(_page_key(cid, third))
    offs = sorted(rec['offs']) if rec else []
    diffs = [b - a for a, b in zip(offs, offs[1:]) if b > a]
    return min(diffs) if diffs else 20


async def tpl_now(c, holder):
    """取一份「真人刚发过的」商品列表请求模板（url/header/body 都是真的）。"""
    raw = await c.ev(holder['sid'], holder['cid'], 'globalThis.__TPLGET()', tmo=8)
    if not raw or raw == 'null':
        return None
    try:
        t = json.loads(raw)
    except Exception:
        return None
    return t if (t.get('url') and t.get('data')) else None


def _sel_from_req(body):
    """从请求体里把二级/三级 cid 抠出来，和 hook 里的规则保持一致。"""
    second = third = None
    try:
        si = body.get('secondCategoryInfo') or {}
        second = si.get('secondCategoryId')
    except Exception:
        pass
    try:
        ti = body.get('thirdCategoryInfo') or {}
        third = ti.get('thirdCategoryId') or ti.get('id')
        if third is None:
            ti2 = body.get('tagInfo') or {}
            third = ti2.get('id')
    except Exception:
        pass
    return second, third


async def fetch_page_by_req(c, holder, cid, third, off):
    """借小程序自己的 wx.request 补一页：只改 pageInfo.offset，别的原样。

    这条路不碰界面、不滚动，窗口被挡住/最小化也能跑；签名和 header 都是
    小程序自己上一次真请求留下的，所以风控特征和真人翻页一致。
    返回 (meta, body) 或 None。
    """
    tpl = await tpl_now(c, holder)
    if not tpl:
        return None
    body = json.loads(json.dumps(tpl['data'], ensure_ascii=False))
    # 模板必须属于「当前正在看的这个列表」，否则补出来的页会记错分类
    if str(body.get('categoryId')) != str(cid):
        return None
    pi = body.setdefault('pageInfo', {})
    pi['offset'] = off
    expr = 'globalThis.__REQ(%s)' % json.dumps(json.dumps(body, ensure_ascii=False))
    r = await c.eva(holder['sid'], holder['cid'], expr, tmo=45)
    if not r:
        return None
    try:
        d = json.loads(r)
    except Exception:
        return None

    code = d.get('code')
    msg = str(d.get('msg') or d.get('err') or '')
    if not d.get('ok'):
        return None
    if code not in (0, 200, None):
        low = msg.lower()
        risky = any(w in low or w in msg for w in _RISK_WORDS)
        if code in (429, 1001, 1007) or risky:
            risk_hit('重放返回 code=%s %s' % (code, msg[:40]))
        elif code in (401, 403):
            # 多半只是模板里的签名过期了，先记一笔；120 秒内连着两次才当风控
            _REPLAY['bad'] += 1
            if _REPLAY['bad'] >= 2 and time.time() - _REPLAY['t'] < 120:
                risk_hit('重放连续 403（签名被拒两次）')
            _REPLAY['t'] = time.time()
            log('重放被拒 code=%s，改用真实滚动刷新模板' % code)
        else:
            log('重放失败 code=%s %s（改用真实滚动）' % (code, msg[:40]))
        return None
    if not d.get('n'):
        return None
    _REPLAY['bad'] = 0

    second, _third = _sel_from_req(body)
    sel = SEL_STATE.get('sel') or {}
    meta = {
        'cid': cid, 'firstCid': body.get('firstCategoryId'),
        'secondCid': second if second is not None else sel.get('sub'),
        'thirdCid': third, 'off': off,
        'limit': pi.get('limit'), 'n': d.get('n'), 'total': d.get('total'),
        'pageSize': pi.get('pageSize'), 'lastOff': d.get('lastOff'),
        'route': 'req-replay', 'sel': sel,
        't': int(time.time() * 1000),
    }
    return meta, d.get('body')


def save_page(meta, body_txt):
    """和主循环完全一致地落盘：页面文件 + _meta.jsonl + 图片队列。"""
    cid_s = str(meta.get('cid'))
    off = meta.get('off') or 0
    fn = os.path.join(OUTDIR, 'c%s_f%s_t%s_o%s.json' % (
        cid_s, meta.get('firstCid') or 0, meta.get('thirdCid') or 0, off))
    with open(fn, 'w', encoding='utf-8') as f:
        f.write(body_txt)
    try:
        with open(META, 'a', encoding='utf-8') as f:
            f.write(json.dumps(meta, ensure_ascii=False) + '\n')
    except Exception:
        pass
    note_page(meta)
    queue_images(body_txt)


_NUDGE_GATE = {'t': 0.0}


async def nudge(c, holder):
    """轻推一下当前分类页，让小程序自己加载下一页。返回是否真的多了一页。

    不管走哪条路，两次翻页之间强制间隔 CRUISE_PACE 秒 ——
    实测手动最快 1.67 秒/次，这里 2.1 秒，且绝不允许一秒钟翻两页。
    """
    wait = _NUDGE_GATE['t'] + CRUISE_PACE - time.time()
    if wait > 0:
        await asyncio.sleep(wait)
    _NUDGE_GATE['t'] = time.time()

    cur = SEL_STATE.get('sel') or {}
    sub = cur.get('sub')
    if not sub:
        return False
    third = cur.get('third') or '0'
    key = _page_key(sub, third)
    before = len((PAGES.get(key) or {}).get('offs') or [])

    # 1) 直接调页面自己的翻页方法：走 App 内部的签名与参数构造，最像真人
    sid, cid = holder.get('sid'), holder.get('cid')
    if sid:
        r = await c.ev(sid, cid, _NUDGE_JS, tmo=12)
        if r and r != 'no-method' and not str(r).startswith('exc'):
            await asyncio.sleep(1.5)
            after = len((PAGES.get(key) or {}).get('offs') or [])
            if after > before:
                return True

    # 2) 渲染层把列表拉到底，让 scroll-view 的到底判定自己触发
    psid = await page_session_cached(c)
    if psid:
        r2 = await c.ev(psid, None, _SCROLL_JS, tmo=10)
        await asyncio.sleep(1.2)
        after = len((PAGES.get(key) or {}).get('offs') or [])
        if after > before:
            return True

    # 3) 兜底：窗口可见时用真实滚轮（效果和真人一样）
    if psid and await thumb_wheel(c, psid, times=2, dy=900):
        await asyncio.sleep(CRUISE_PACE)
        after = len((PAGES.get(key) or {}).get('offs') or [])
        return after > before
    return False


# ---------- 自动 / 手动 开关，以及「跳到没抓完的分类」 ----------
MODE_F = os.path.join(BASE, '_mode.json')
_mode_cache = {'t': 0.0, 'on': False}
_st_cache = {'t': 0.0, 'data': None}


def auto_on():
    """看板右上角那个「自动」按钮写的开关，2 秒缓存，不用重启抓包。"""
    now = time.time()
    if now - _mode_cache['t'] < 2:
        return _mode_cache['on']
    on = False
    try:
        with open(MODE_F, encoding='utf-8') as f:
            on = bool(json.load(f).get('auto'))
    except Exception:
        on = False
    _mode_cache.update(t=now, on=on)
    return on


def board_state(ttl=5.0):
    """直接问看板要全站进度（它已经算好了 skus/plan/gap，不用重复实现）。带 5 秒缓存。"""
    now = time.time()
    if _st_cache['data'] is not None and now - _st_cache['t'] < ttl:
        return _st_cache['data']
    try:
        with urllib.request.urlopen('http://127.0.0.1:8899/api/state', timeout=8) as r:
            d = json.loads(r.read().decode('utf-8'))
        _st_cache.update(t=now, data=d)
        return d
    except Exception:
        return _st_cache['data']


def pick_gap(st, cur):
    """挑一个没抓完的分支：优先同二级，其次同一级；跨一级要重开页面，先不碰。"""
    cur_top, cur_sub, cur_third = (str(cur.get('top')), str(cur.get('sub')),
                                   str(cur.get('third')))
    best = None
    for t in (st.get('tops') or []):
        for c in (t.get('children') or []):
            for k in (c.get('thirds') or []):
                plan, skus = k.get('plan') or 0, k.get('skus') or 0
                if not plan or skus >= plan or k.get('end'):
                    continue          # 接口已翻到底的分类不用再补
                if str(k.get('id')) == cur_third and str(c.get('id')).split(':')[-1] == cur_sub:
                    continue                     # 当前这个由「滚动」负责，不在这里挑
                cid_plain = str(c.get('id')).split(':')[-1]
                rank = (4 if str(t.get('id')) == cur_top else 0) + \
                       (2 if cid_plain == cur_sub else 0)
                if rank <= 0:
                    continue
                if best is None or rank > best[0]:
                    best = (rank, t, c, k)
    return best


def cur_branch_done(st, cur):
    """当前分支是否已经没得翻了。用看板口径：end=True 或 条数已达标称都算完。

    看板里二级的 id 是 "40488:1003" 这种组合键，三级是纯数字，比较前要拆开。
    返回 True/False，看不出来时返回 None（让调用方按「还没完」处理，别乱跳）。
    """
    sub = str(cur.get('sub') or '')
    third = str(cur.get('third') or '')
    if not st or not sub:
        return None
    for t in (st.get('tops') or []):
        for c in (t.get('children') or []):
            if str(c.get('id')).split(':')[-1] != sub:
                continue
            if not third or third == sub:
                plan, skus, end = c.get('plan') or 0, c.get('skus') or 0, c.get('end')
            else:
                hit = None
                for k in (c.get('thirds') or []):
                    if str(k.get('id')) == third:
                        hit = k
                        break
                if not hit:
                    return None
                plan, skus, end = hit.get('plan') or 0, hit.get('skus') or 0, hit.get('end')
            if not plan:
                return None
            return bool(end or skus >= plan)
    return None


_JUMP_JS = r"""(function(){
  try{
    var p=(getCurrentPages()||[]).slice(-1)[0]; if(!p) return 'no-page';
    var fn=%(fn)s, arg=%(arg)s;
    if(typeof p[fn]!=='function') return 'no-fn';
    p[fn](arg);
    return 'ok';
  }catch(e){ return 'exc:'+String(e).slice(0,80); }
})()"""


async def call_page_fn(c, holder, fn, arg_js):
    sid, cid = holder.get('sid'), holder.get('cid')
    if not sid:
        return None
    return await c.ev(sid, cid, _JUMP_JS % {'fn': json.dumps(fn), 'arg': arg_js}, tmo=12)


async def auto_jump(c, holder, cur):
    """跳到一个还没抓完的分类：先切二级，再切三级，每步都用 __SEL 验证。"""
    st = board_state()
    if not st:
        log('自动：拿不到看板状态，跳过这一轮')
        return False
    pick = pick_gap(st, cur)
    if not pick:
        tops = {str(t.get('id')) for t in (st.get('tops') or [])}
        if str(cur.get('top')) not in tops:
            log('自动：现在停在「%s」这种推荐页/首页，脚本找不到分类入口 —— '
                '先手动点进任意一个一级分类（比如蔬菜豆制品），之后就会自己跑'
                % (cur.get('subName') or cur.get('top')))
        else:
            log('自动：这个一级下已没有待补的分类了，换一级请手动点（避免重新拉整个首页）')
        return False
    _rank, top, sub, third = pick
    tid = third.get('id')
    label = '%s / %s / %s' % (top.get('name'), sub.get('name'), third.get('name'))

    if str(cur.get('sub')) != str(sub.get('id')):
        ev = ("{currentTarget:{dataset:{id:'%s',subid:'%s',subCategoryId:'%s',categoryId:'%s'}}}"
              % (sub.get('id'), sub.get('id'), sub.get('id'), sub.get('id')))
        for fn in ('activeSubCategoryByClick', 'selectSubCategory'):
            if await call_page_fn(c, holder, fn, ev) is None:
                break
            await asyncio.sleep(2.5)
            if str((SEL_STATE.get('sel') or {}).get('sub')) == str(sub.get('id')):
                break

    ok = False
    for fn, arg in (('activeThirdCategory', "'%s'" % tid),
                    ('triggerThirdCategory', "'%s'" % tid),
                    ('handleTapThirdCategory',
                     "{currentTarget:{dataset:{id:'%s'}},target:{dataset:{id:'%s'}}}" % (tid, tid)),
                    ('activeThirdCategory',
                     "{currentTarget:{dataset:{id:'%s'}},target:{dataset:{id:'%s'}}}" % (tid, tid))):
        r = await call_page_fn(c, holder, fn, arg)
        if r in (None, 'no-fn'):
            continue
        await asyncio.sleep(2.5)
        if str((SEL_STATE.get('sel') or {}).get('third') or '0') == str(tid):
            ok = True
            break

    log('自动跳转 → %s：%s' % (label, '成功' if ok else '失败（几个入口都没生效，先停在这个分类）'))
    return ok


async def cruiser(c, holder):
    """自动模式时接管：当前分类一页页往下滚，滚完就跳到还没抓完的分类。

    「自动/手动」由看板右上角按钮控制，落到 _mode.json，这里每轮现读，
    不用重启。自动模式走真实滚动（和小程序里手指上滑一样），不发重放包。
    """
    log('自动补页待命：点看板右上角「自动」才会动手（当前 %s）'
        % ('自动' if auto_on() else '手动'))
    auto_prev = None
    fail = 0
    paused_until = 0.0
    while True:
        try:
            await asyncio.sleep(1.5)
            now = time.time()
            auto = auto_on()
            if auto != auto_prev:
                auto_prev = auto
                log('模式切换：%s' % ('自动（脚本自己滚 + 补未抓分类）' if auto else '手动（只记录，不操作小程序）'))
            idle = now - SEL_STATE['ts']
            cur = SEL_STATE.get('sel') or {}
            sub = cur.get('sub')
            third = cur.get('third') or '0'
            pn, pt, gap = gap_of(sub, third) if sub else (0, None, None)
            rec = PAGES.get(_page_key(sub, third)) if sub else None
            nxt = None
            if rec and rec['offs']:
                nxt = max(rec['offs']) + page_size_of(sub, third)
            # 每轮把判断依据写出去，出问题时不用猜卡在哪
            try:
                with open(os.path.join(OUTDIR, '_cruise.json'), 'w', encoding='utf-8') as f:
                    json.dump({'ts': now, 'idle': round(idle, 1), 'auto': auto,
                               'gate': (8.0 if auto else CRUISE_IDLE_GATE), 'pace': CRUISE_PACE,
                               'sub': sub, 'third': third, 'pn': pn, 'pt': pt, 'gap': gap,
                               'next_off': nxt, 'fail': fail,
                               'paused': max(0, round(paused_until - now, 1)),
                               'risk_hits': RISK['hits'],
                               'risk_why': RISK['why'],
                               'risk_paused': max(0, round(RISK['paused_until'] - now, 1)),
                               'sel': cur}, f, ensure_ascii=False)
            except Exception:
                pass

            if now < paused_until or now < RISK['paused_until']:
                continue
            if not (auto or CRUISE_ON):
                fail = 0            # 手动模式：什么都不做，只记账
                continue
            gate = 8.0 if auto else CRUISE_IDLE_GATE
            if idle < gate:
                fail = 0            # 用户正在操作 → 完全让路，不计失败
                continue

            if auto:
                # 自动模式：当前分支还有缺页就继续往下滚；滚完了就换个没抓完的分支。
                # 「还有没有得翻」以看板口径为准（end / 标称），比本地 offset 推算可靠。
                st = board_state()
                done = cur_branch_done(st, cur)
                more = (done is False) or (done is None and gap and nxt is not None)
                if sub and more:
                    ok = await nudge(c, holder)
                    if ok:
                        fail = 0
                        log('自动滚动 二级=%s 三级=%s 第 %d/%d 页'
                            % (sub, third, min(pn + 1, pt or 0), pt or 0))
                    else:
                        fail += 1
                        # 滚不动：可能已到底，也可能窗口不在前台。
                        # 不要代点页面按钮 —— 那会被判成异常操作（实测 12 秒就弹验证）。
                        if fail >= 2:
                            fail = 0
                            paused_until = time.time() + 60
                            log('自动：这个列表滚不动了，歇 60 秒。'
                                '换分类请手动点一下（脚本不代点，代点会触发风控）')
                        else:
                            await asyncio.sleep(CRUISE_PACE)
                    continue
                # 当前分类已抓完 → 只提示，不代点
                paused_until = time.time() + 60
                log('自动：「%s / %s」已抓完。换下一个分类请手动点一下，停手 8 秒后自动继续'
                    % (cur.get('subName') or sub, cur.get('thirdName') or third))
                continue

            # 显式 --cruise：先试重放补页（不碰界面），不行再退回滚动
            if not sub or not gap or nxt is None:
                continue
            got = await fetch_page_by_req(c, holder, sub, third, nxt)
            if got:
                meta, body = got
                save_page(meta, body)
                fail = 0
                log('发包补页 二级=%s 三级=%s off=%s 共%s条（%d/%d 页）' % (
                    sub, third, nxt, meta.get('total'),
                    pn + 1, pt or (pn + 1)))
                await asyncio.sleep(CRUISE_PACE)     # 按 80% 节奏走
                continue

            # 2) 发不出去 → 让页面自己滚（需要窗口可见）
            ok = await nudge(c, holder)
            if ok:
                fail = 0
                log('巡航补页 二级=%s 三级=%s 第 %d/%d 页' % (sub, third, min(pn + 1, pt or 0), pt or 0))
            else:
                fail += 1
                if fail >= CRUISE_MAX_FAIL:
                    paused_until = time.time() + 90
                    fail = 0
                    log('巡航：连续 %d 次推不动，歇 90 秒（多半是小程序窗口被最小化）' % CRUISE_MAX_FAIL)
                else:
                    await asyncio.sleep(CRUISE_PACE)
        except Exception as e:
            log('巡航异常: %s %s' % (type(e).__name__, str(e)[:120]))
            await asyncio.sleep(5)


async def run_session():
    log('v3 启动，等待小程序连接 CDP ...')
    dump_link(False, '正在等小程序连上调试代理')
    try:
        from _pidutil import write_pid
        write_pid('_capture.pid', base=BASE)
    except Exception:
        pass
    ws = None
    for _ in range(240):
        try:
            ws = await websockets.connect(CDP_URL, max_size=None, ping_interval=20, open_timeout=5)
            break
        except Exception:
            await asyncio.sleep(2)
    if ws is None:
        raise ConnectionError('cdp-unreachable')
    c = Conn(ws)
    asyncio.create_task(c.reader())
    await asyncio.sleep(0.5)

    # 常驻等待：用户随时打开小程序都能被接上，不会因为等太久就退出
    round_no = 0
    while True:
        if c.dead:
            raise ConnectionError('cdp-dead')
        sid, cid = await find_ctx(c, tries=150)      # 5 分钟一轮
        if sid:
            break
        round_no += 1
        log('仍未发现小程序（第 %d 轮），继续静默等待 ...' % round_no)
    log('上下文就绪 session=%s ctx=%s' % (sid[:8], cid))
    log('hook 安装: %s' % await c.ev(sid, cid, HOOK, tmo=15))
    await dump_env(c, sid, cid)
    log('回填已抓页索引: %d 页' % seed_pages())

    # 高频位置监听：独立协程，抓取主循环再忙也不影响「跟随」的实时性
    holder = {'sid': sid, 'cid': cid}
    asyncio.create_task(sel_watcher(c, holder))
    asyncio.create_task(cruiser(c, holder))

    seen = set()
    cat_t = 0
    t_last_stat = 0
    t_last_check = 0
    t_last_cur = 0
    last_log_key = ''
    t_last_new = time.time()   # 最近一次抓到新页的时间
    t_last_fill = 0.0
    fill_fail = 0
    fill_key = ''

    while True:
        try:
            now = time.time()

            if now - t_last_check > 60:
                t_last_check = now
                # 只读探测，别在循环里反复擦除：那等于反复拆 WMPF 的调试通道
                if await c.ev(sid, cid, "globalThis.__LIVE_V===6?'ok':'lost'", tmo=8) != 'ok':
                    log('hook 丢失，重新定位上下文 ...')
                    s2, c2 = await find_ctx(c, tries=40)
                    if s2:
                        sid, cid = s2, c2
                        holder['sid'], holder['cid'] = sid, cid
                    log('hook 重装: %s' % await c.ev(sid, cid, HOOK, tmo=15))
                    await dump_env(c, sid, cid)

            # 分类树：先比时间戳，变了才取全量
            ct = await c.ev(sid, cid, "globalThis.__CAT?globalThis.__CAT.t:0", tmo=8)
            if isinstance(ct, (int, float)) and ct and ct != cat_t:
                cat_t = ct
                cb = await c.ev(sid, cid, "globalThis.__CAT.body", tmo=90)
                if cb and isinstance(cb, str):
                    with open(os.path.join(OUTDIR, '_category_tree.json'), 'w', encoding='utf-8') as f:
                        f.write(cb)
                    log('分类树已更新')

            # 当前位置由 sel_watcher（0.15s 一次）高频维护，主循环这里只同步
            # 「用户刚刚切过位置」的时间点，给自动补页留 10 秒观察期
            if SEL_STATE['ts'] > t_last_new:
                t_last_new = SEL_STATE['ts']
                fill_fail = 0
                fill_key = ''

            kb = await c.ev(sid, cid, "JSON.stringify(Object.keys(globalThis.__P||{}))", tmo=10)
            keys = []
            if kb and kb.startswith('['):
                try:
                    keys = [k for k in json.loads(kb) if k not in seen]
                except Exception:
                    keys = []

            if keys:
                # 先取 meta（体积小），落 _meta.jsonl
                expr = 'JSON.stringify([%s])' % ','.join(
                    '((globalThis.__M||{})[%s]||null)' % json.dumps(k) for k in keys)
                meta_raw = await c.ev(sid, cid, expr, tmo=20)
                metas = []
                if meta_raw and meta_raw.startswith('['):
                    try:
                        metas = json.loads(meta_raw)
                    except Exception:
                        metas = []
                if metas:
                    with open(META, 'a', encoding='utf-8') as f:
                        for m in metas:
                            if m:
                                f.write(json.dumps(m, ensure_ascii=False) + '\n')
                    # 顺带盯一眼响应码：手动翻页时也能第一时间发现被限流
                    for m in metas:
                        if not m:
                            continue
                        cd_ = m.get('code')
                        if cd_ not in (None, 0, 200):
                            risk_hit('小程序响应 code=%s' % cd_)
                meta_by_key = {}
                for k, m in zip(keys, metas):
                    if m:
                        meta_by_key[k] = m
                        note_page(m)

                # 再并发取响应体
                got = await fetch_bodies(c, sid, cid, keys)
                wrote = 0
                for k, body in got:
                    if not body or not isinstance(body, str):
                        continue
                    parts = k.split('_')
                    cid_s = parts[0]
                    off_s = parts[1] if len(parts) > 1 else '0'
                    m = meta_by_key.get(k) or {}
                    first = m.get('firstCid') or 0
                    third = m.get('thirdCid') or 0
                    # 同 (二级, offset) 在不同三级下会重复：文件名必须带三级，否则互相覆盖
                    fn = os.path.join(OUTDIR, 'c%s_f%s_t%s_o%s.json' % (cid_s, first, third, off_s))
                    with open(fn, 'w', encoding='utf-8') as f:
                        f.write(body)
                    queue_images(body)
                    seen.add(k)
                    wrote += 1
                    # 三级切换时打一行，方便核对「这一页到底记到哪个三级下了」
                    lk = '%s|%s' % (cid_s, third)
                    if lk != last_log_key:
                        last_log_key = lk
                        sel = m.get('sel') or {}
                        log('落盘 二级=%s %s 三级=%s %s off=%s 共%s条' % (
                            cid_s, sel.get('subName') or '', third,
                            sel.get('thirdName') or '未细分', off_s,
                            (m.get('total') if m.get('total') is not None else '未知')))
                if wrote:
                    t_last_new = now
                    await c.ev(sid, cid, ';'.join(
                        'delete globalThis.__P[%s]' % json.dumps(k) for k in keys), tmo=15)

            # 补页交给 cruiser 协程（空闲接管、用户一动就让路），主循环不再插手，
            # 否则两边同时滚会把小程序页面搞乱。

            if now - t_last_stat > 10:
                t_last_stat = now
                with IMG_LOCK:
                    q = len(IMG_QUEUE)
                nimg = len(os.listdir(IMGDIR)) if os.path.isdir(IMGDIR) else 0
                nfile = len([f for f in os.listdir(OUTDIR) if f.startswith('c') and '_o' in f])
                log('抓取中: %d 页已落盘 | 待下图 %d 已下 %d' % (nfile, q, nimg))

            await asyncio.sleep(0.5)
        except Exception as e:
            if c.dead:
                raise                      # 代理断开 → 交给 main() 整轮重连
            log('loop 异常: %s %s' % (type(e).__name__, str(e)[:140]))
            await asyncio.sleep(2)


async def main():
    """守护外壳：调试代理重启、小程序关掉再开，都自己接回来，不用人工重启。"""
    tries = 0
    while True:
        try:
            await run_session()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            tries += 1
            log('会话中断（%s），5 秒后重连调试代理 ...（第 %d 次）' % (str(e)[:70], tries))
            await asyncio.sleep(5)


if __name__ == '__main__':
    threading.Thread(target=img_worker, daemon=True).start()
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log('手动结束')
