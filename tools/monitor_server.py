# -*- coding: utf-8 -*-
"""实时抓取看板 v4：复刻小程序「分类」页布局
  ├ 顶部：一级分类横滑胶囊（鼠标滚轮可前后滑）
  ├ 左侧：该一级下的二级分类竖排（为你推荐 / 在家火锅 / … / 超低折扣）
  ├ 顶部第二行：当前二级对应的三级标签（如 超低折扣 → 低价捡漏 / 限时秒杀）
  └ 右侧：商品卡片流

三级归属来自抓包文件名 c{cid}_f{first}_t{third}_o{off}.json。
只读扫描 live_raw / live_imgs，不干扰抓取进程。
用法：python -u monitor_server.py [端口]
"""
import json, os, glob, sys, time, threading, re, urllib.request, subprocess
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, parse_qs

BASE = os.path.dirname(os.path.abspath(__file__))
RAW = os.path.join(BASE, 'live_raw')
IMGS = os.path.join(BASE, 'live_imgs')
CUR = os.path.join(RAW, '_current.json')
LINK = os.path.join(RAW, '_link.json')      # 链路心跳：还连着小程序吗
MODE_F = os.path.join(BASE, '_mode.json')      # 自动/手动开关，抓包端每轮都读它
PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8899

_locks = threading.RLock()
_file_cache = {}          # fn -> {'sig':(mtime,size), 'rec':parsed}
_tree_cache = {'sig': None, 'tree': None}
_scan_cache = {'t': 0, 'key': None, 'data': None}
_pic_cache = {}
_imgs_cache = {'t': 0, 'set': set()}
_cur_cache = {'sig': None, 'data': {}}
_link_cache = {'sig': None, 'data': {}}

PAGE_DEFAULT = 20
FN_RE = re.compile(r'^c(\d+)((?:_[a-z]+\d+)*)_o(\d+)\.json$')


# ---------------- 一键启动小程序 ----------------
LAUNCH_F = os.path.join(BASE, '_launch.json')   # 可选：{"cmd": "快捷方式或命令", "search": "xxcs"}
MINI_KEY = '小象超市'
SEARCH_KEY = 'xxcs'      # 微信搜索框里敲的词：用户实测搜 xxcs，第一条就是小象超市


def search_key():
    """关键词可以写在 _launch.json 里改，不用改代码。"""
    try:
        with open(LAUNCH_F, encoding='utf-8') as f:
            v = str(json.load(f).get('search') or '').strip()
            if v:
                return v
    except Exception:
        pass
    return SEARCH_KEY


def _visible_windows(prefix='Chrome_WidgetWin'):
    """枚举可见窗口（标题, 句柄）。只用来判断小程序窗口在不在、要不要置前。"""
    out = []
    try:
        import ctypes
        from ctypes import wintypes
        u = ctypes.WinDLL('user32', use_last_error=True)
        PROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

        def cb(hwnd, lp):
            if not u.IsWindowVisible(hwnd):
                return True
            cls = ctypes.create_unicode_buffer(128)
            u.GetClassNameW(hwnd, cls, 128)
            if not cls.value.startswith(prefix):
                return True
            n = u.GetWindowTextLengthW(hwnd)
            buf = ctypes.create_unicode_buffer(n + 1)
            u.GetWindowTextW(hwnd, buf, n + 1)
            out.append((hwnd, buf.value))
            return True

        u.EnumWindows(PROC(cb), 0)
    except Exception:
        pass
    return out


def _focus(hwnd):
    """置前。后台进程直接 SetForegroundWindow 会被系统拦，交给 _open_mini 借输入队列。"""
    try:
        import _open_mini
        if _open_mini.focus(hwnd):
            return True
    except Exception:
        pass
    try:
        import ctypes
        u = ctypes.WinDLL('user32', use_last_error=True)
        u.ShowWindow(hwnd, 9)          # SW_RESTORE
        u.SetForegroundWindow(hwnd)
        return True
    except Exception:
        return False


def launch_miniapp():
    """一键打开小程序。

    顺序（前面的最硬、最不打扰）：
      1) 已经开着 → 直接置前（0 打扰）
      2) _launch.json 里配的命令
      3) 桌面短名字含「小象超市」的 .lnk（微信自己生成的，参数齐全，等于硬调用）
      4) 全自动：调起微信 → Ctrl+F → 输入关键词 → 回车（不需要人动手）
      5) 兜底：把微信拉到前台

    PC 微信没有对外开放的小程序唤起协议：Weixin.dll 里虽然有 weixin://launchapplet，
    但外部进程调用会被忽略（只有 appex/launchgame 生效），
    weixin://dl/business/?appid= 则需要服务端签发的 t token，落到白页。
    所以第 4 步是唯一不依赖人工的可靠路径。
    """
    if os.name != 'nt':
        return {'ok': False, 'msg': '这个按钮只在 Windows 上有效'}

    # 1) 已经开着 → 直接调到前台
    #    必须限定 Chrome_WidgetWin_0（小程序容器），否则会误命中本看板那个 Chrome 标签页——
    #    标题里同样有「小象超市」三个字。
    for hwnd, title in _visible_windows('Chrome_WidgetWin_0'):
        if MINI_KEY in (title or '') and '看板' not in (title or ''):
            _focus(hwnd)
            return {'ok': True, 'msg': '小程序已打开，已调到前台', 'how': 'focus'}

    # 2) 用户自己配的启动命令（_launch.json 里的 cmd）
    cmd = ''
    try:
        with open(LAUNCH_F, encoding='utf-8') as f:
            cmd = str(json.load(f).get('cmd') or '').strip()
    except Exception:
        pass
    if cmd:
        try:
            subprocess.Popen(cmd, shell=True, cwd=BASE)
            return {'ok': True, 'msg': '已执行自定义启动命令', 'how': 'cfg'}
        except Exception as e:
            return {'ok': False, 'msg': '自定义启动命令失败：%s' % e}

    # 3) 桌面上叫「小象超市」的快捷方式（微信「添加到桌面」生成，等于硬调用）
    home = os.environ.get('USERPROFILE') or os.path.expanduser('~')
    for d in (os.path.join(home, 'Desktop'), r'D:\Users\Administrator\Desktop',
              r'C:\Users\Public\Desktop', BASE):
        try:
            names = os.listdir(d)
        except Exception:
            continue
        for fn in names:
            if fn.lower().endswith('.lnk') and MINI_KEY in fn:
                try:
                    os.startfile(os.path.join(d, fn))
                    return {'ok': True, 'msg': '已用桌面快捷方式启动', 'how': 'lnk'}
                except Exception:
                    pass

    # 4) 全自动：微信里搜关键词再回车
    try:
        import _open_mini
        r = _open_mini.open_mini(search_key(), MINI_KEY)
        if r.get('ok'):
            return {'ok': True, 'msg': '小程序已打开', 'how': 'auto'}
    except Exception as e:
        pass

    # 5) 兜底：把微信拉到前面，用户点一下小程序
    try:
        os.startfile('weixin://')
        return {'ok': True,
                'msg': '没自动打开，已把微信调到前台。'
                       '如果小程序窗口里点右上角「…」→「添加到桌面」，'
                       '把那个快捷方式放到桌面，以后这个按钮就是真·硬调用',
                'how': 'weixin'}
    except Exception as e:
        return {'ok': False, 'msg': '打不开微信：%s' % e}


def img_files():
    """图片是异步下载的，和 json 文件 mtime 无关；单独按目录快照，2 秒刷新一次"""
    if time.time() - _imgs_cache['t'] < 2 and _imgs_cache['set']:
        return _imgs_cache['set']
    try:
        _imgs_cache['set'] = set(os.listdir(IMGS))
    except Exception:
        _imgs_cache['set'] = set()
    _imgs_cache['t'] = time.time()
    return _imgs_cache['set']


# ---------- 分类树 ----------
def load_tree():
    p = os.path.join(RAW, '_category_tree.json')
    if not os.path.exists(p):
        return None
    st = os.stat(p)
    sig = (st.st_mtime, st.st_size)
    if _tree_cache['sig'] == sig:
        return _tree_cache['tree']
    try:
        d = json.load(open(p, encoding='utf-8'))
        pc = (d.get('data') or {}).get('poiCategories') or []
    except Exception:
        return _tree_cache['tree']
    tree = []
    for c in pc:
        top = {
            'id': str(c.get('id')), 'name': c.get('name') or '',
            'icon': c.get('icon') or '', 'declared': c.get('skuCount'),
            'children': [],
        }
        seen = set()
        has_rec = False
        for s in c.get('subPoiCategories') or []:
            sid = str(s.get('id'))
            if sid in seen:
                continue
            seen.add(sid)
            is_rec = (sid == top['id'])
            if is_rec:
                has_rec = True
            thirds = []
            seen3 = set()
            allids = set([sid])
            for k in s.get('subPoiCategories') or []:
                kid = str(k.get('id'))
                if kid in seen3:
                    continue
                seen3.add(kid)
                # 「全部」不单列（id 可能等于二级 id，也可能另给一个 id），界面上统一用虚拟「全部」
                if kid == sid or (k.get('name') or '') == '全部':
                    allids.add(kid)
                    continue
                thirds.append({'id': kid, 'name': k.get('name') or '',
                                'declared': k.get('skuCount'), 'type': k.get('type')})
            top['children'].append({
                'id': top['id'] + ':' + sid, 'cid': sid,
                'name': s.get('name') or ('为你推荐' if is_rec else ''),
                'kind': 'rec' if is_rec else 'sub',
                'declared': s.get('skuCount'), 'parent': top['id'],
                'thirds': thirds, 'allids': allids,
            })
        if not has_rec:
            top['children'].insert(0, {
                'id': top['id'] + ':REC', 'cid': top['id'], 'name': '为你推荐',
                'kind': 'rec', 'declared': None, 'parent': top['id'], 'thirds': [],
                'allids': set([top['id']]),
            })
        tree.append(top)
    _tree_cache['sig'] = sig
    _tree_cache['tree'] = tree
    return tree


def build_index(tree):
    """cid -> [节点]；节点的唯一键是 topId:cid，避免「会员折扣」这类跨一级复用的 id 冲突"""
    owner = {}
    for top in tree:
        for ch in top['children']:
            owner.setdefault(ch['cid'], []).append(ch)
    return owner


def resolve_node(tree, owner, cid, first):
    cid = str(cid)
    if first:
        first = str(first)
        for ch in owner.get(cid, []):
            if ch['parent'] == first:
                return ch
    cands = owner.get(cid) or []
    if cands:
        return cands[0]
    return None


# ---------- 落盘解析（带 mtime 缓存） ----------
def parse_file(fn):
    st = os.stat(fn)
    sig = (st.st_mtime, st.st_size)
    c = _file_cache.get(fn)
    if c and c['sig'] == sig:
        return c['rec']
    rec = None
    base = os.path.basename(fn)
    mm = FN_RE.match(base)
    if not mm:
        _file_cache[fn] = {'sig': sig, 'rec': None}
        return None
    cid, mods, off = mm.group(1), mm.group(2), int(mm.group(3))
    kv = dict(re.findall(r'_([a-z]+)(\d+)', mods or ''))
    first = kv.get('f')
    third = kv.get('t')
    # 文件名里的 0 表示「没这个维度」，当空处理，否则 t0 会被当成有效三级而挤掉旧数据
    if first in (None, '0'):
        first = None
    if third in (None, '0'):
        third = None
    try:
        j = json.load(open(fn, encoding='utf-8'))
        data = j.get('data') or {}
        arr = data['contents'][0]['subModule']['contents']
        pi = data.get('pageInfo') or {}
        rows = []
        for it in arr:
            lc = it.get('logicContents') or {}
            lx = it.get('lxInfo') or {}
            sid = lc.get('skuId')
            if not sid:
                continue
            pic = (lc.get('detailPagePic') or '').strip()
            if pic.startswith('//'):
                pic = 'https:' + pic
            pfn = pic.rsplit('/', 1)[-1].split('?')[0] if pic else ''
            rows.append({
                'sku': sid, 'name': lx.get('content_name') or '',
                'sale': lx.get('sale_price') or '', 'orig': lx.get('origin_price') or '',
                'member': lx.get('member_price') or '', 'label': lx.get('label') or '',
                'pfn': pfn, 'seq': it.get('sequence') or 0,
            })
        rec = {
            'cid': cid, 'first': first, 'third': third, 'off': off, 'n': len(arr),
            'total': pi.get('total'), 'pageSize': pi.get('pageSize') or PAGE_DEFAULT,
            'lastOff': pi.get('lastPageOffset'), 'mtime': st.st_mtime, 'rows': rows,
        }
    except Exception:
        rec = None
    _file_cache[fn] = {'sig': sig, 'rec': rec}
    return rec


def _bucket():
    return {'pages': 0, 'offs': set(), 'skus': set(), 'total': None,
            'pageSize': PAGE_DEFAULT, 'lastOff': None, 'imgs': 0, 'rows': [],
            'known': set(), 'unk': set()}


def scan_all(tree):
    """三级分组落桶：sub_b 按二级、third_b 按（二级, 三级）。同一页重复落盘按内容指纹去重。"""
    owner = build_index(tree)
    allmap = {ch['id']: (ch.get('allids') or set()) for top in tree for ch in top['children']}
    files = glob.glob(os.path.join(RAW, 'c*_o*.json'))

    def tid_of(nid, third):
        t = third or '0'
        # 「全部」的 id 有的等于二级 id、有的是单独 id，统一归到未细分桶
        return '0' if t in allmap.get(nid, ()) else t

    groups = {}
    for f in files:
        rec = parse_file(f)
        if not rec:
            continue
        node = resolve_node(tree, owner, rec['cid'], rec['first'])
        if node is None:
            node = {'id': '?' + rec['cid'], 'cid': rec['cid'], 'name': '未识别',
                    'kind': 'unknown', 'parent': '', 'thirds': []}
        nid = node['id']
        tid = tid_of(nid, rec['third'])
        key = (nid, rec['cid'], rec['off'], tid)
        sig = frozenset(r['sku'] for r in rec['rows'])
        rank = (rec['mtime'], len(sig))
        lst = groups.setdefault(key, [])
        # 同一 offset 也可能是不同三级的两页数据（旧文件没有三级标记），
        # 只有 SKU 高度重叠才判定为同一页重复落盘，否则都要留下
        merged = False
        for i, (rk, rc, sg, nd) in enumerate(lst):
            if sig and sg and len(sig & sg) / max(1, min(len(sig), len(sg))) >= 0.8:
                if rank > rk:
                    lst[i] = (rank, rec, sig, node)
                merged = True
                break
        if not merged:
            lst.append((rank, rec, sig, node))

    sub_b, third_b = {}, {}
    all_sku = set()
    npage = 0

    # 第一遍：sku -> 所属三级。接口不返回每条的归属，只有在三级标签下抓到过才知道。
    # 「全部」里再遇到同一个 sku，就用这张表把归属补上（否则全堆在「未细分」里）。
    sku3 = {}
    for lst in groups.values():
        for _, rec, sig, node in lst:
            nid0 = node.get('id') or ''
            if nid0.startswith('?'):
                continue
            tid0 = tid_of(nid0, rec['third'])
            if tid0 == '0':
                continue
            for r in rec['rows']:
                sku3.setdefault(r['sku'], (nid0, tid0))

    for lst in groups.values():
        for _, rec, sig, node in lst:
            npage += 1
            nid = node['id']
            tid = tid_of(nid, rec['third'])
            for b, k in ((sub_b, nid), (third_b, (nid, tid))):
                st = b.setdefault(k, _bucket())
                st['pages'] += 1
                st['offs'].add(rec['off'])
                if rec['total'] is not None:
                    # 同一分组下不同页的 total 可能不同（分页时商品上下架），取最大值才不会被末页覆盖
                    st['total'] = rec['total'] if st['total'] is None else max(st['total'], rec['total'])
                if rec['pageSize']:
                    st['pageSize'] = rec['pageSize']
                if rec.get('lastOff') is not None:
                    st['lastOff'] = rec['lastOff'] if st['lastOff'] is None else max(st['lastOff'], rec['lastOff'])
                have = img_files()
                for r in rec['rows']:
                    if r['sku'] in st['skus']:
                        continue
                    st['skus'].add(r['sku'])
                    pfn = r.get('pfn') or ''
                    d = dict(r)
                    d['img'] = pfn if pfn in have else ''
                    d.pop('pfn', None)
                    if d['img']:
                        st['imgs'] += 1
                    d['node'] = nid
                    d['parent'] = node.get('parent') or ''
                    d['third'] = tid
                    if tid == '0':
                        hit = sku3.get(r['sku'])
                        if hit and hit[0] == nid:
                            d['third'] = hit[1]
                            st['known'].add(r['sku'])
                        else:
                            st['unk'].add(r['sku'])
                    st['rows'].append(d)
            for r in rec['rows']:
                all_sku.add(r['sku'])
    return sub_b, third_b, len(all_sku), npage


def _stat(st):
    """统计一个分组：pn/pt = 已翻页 / 总页，end = 是否真的翻到底（拿到 lastPageOffset 且无缺页）。"""
    if not st:
        return {'pages': 0, 'covered': 0, 'total': None, 'skus': 0, 'imgs': 0,
                'pn': 0, 'pt': 0, 'gap': 0, 'end': False}
    ps = st['pageSize'] or PAGE_DEFAULT
    offs = sorted(st['offs'])
    last = st.get('lastOff')
    if last is None:
        # 服务端还没告诉我们末页位置：总数未知，只能报「已翻到第几页」
        pt = (offs[-1] // ps + 1) if offs else 0
        pn = len(offs)
        covered = min(st['total'] or 0, offs[-1] + ps) if offs else 0
        return {'pages': st['pages'], 'covered': covered, 'total': st['total'],
                'skus': len(st['skus']), 'imgs': st['imgs'],
                'pn': pn, 'pt': pt, 'gap': 0, 'end': False}
    pt = last // ps + 1
    pn = len([o for o in offs if o <= last])
    gap = max(0, pt - pn)
    end = bool(offs) and max(offs) >= last and gap == 0
    covered = min(st['total'] or 0, last + ps) if offs else 0
    return {'pages': st['pages'], 'covered': covered, 'total': st['total'],
            'skus': len(st['skus']), 'imgs': st['imgs'],
            'pn': pn, 'pt': pt, 'gap': gap, 'end': end}


def read_cur():
    """小程序当前位置。文件很小但要求秒级，用 mtime_ns 缓存避免高频重复解析。"""
    try:
        st = os.stat(CUR)
        sig = (st.st_mtime_ns, st.st_size)
        if _cur_cache['sig'] == sig:
            return _cur_cache['data']
        d = json.load(open(CUR, encoding='utf-8'))
        _cur_cache['sig'] = sig
        _cur_cache['data'] = d
        return d
    except Exception:
        return {}


def read_link():
    """链路心跳。10 秒内没更新就算断线 —— 位置文件是「变了才写」，
    光看它分不清「一直停在这页」和「压根没连上」，跟随按钮会显得没反应。"""
    try:
        st = os.stat(LINK)
        sig = (st.st_mtime_ns, st.st_size)
        if _link_cache['sig'] == sig:
            d = dict(_link_cache['data'])
        else:
            d = json.load(open(LINK, encoding='utf-8'))
            _link_cache['sig'] = sig
            _link_cache['data'] = d
        age = time.time() - (d.get('ts') or 0)
        d['stale'] = age > 10
        d['age'] = round(age, 1)
        d['ok'] = bool(d.get('ok')) and not d['stale']
        return d
    except Exception:
        return {'ok': False, 'stale': True, 'age': None,
                'why': '抓包端还没起来（没有心跳文件）'}


def build_state():
    tree = load_tree() or []
    sub_b, third_b, total_sku, npage = scan_all(tree)
    tops = []
    for top in tree:
        subs = []
        t_skus = set()
        for ch in top['children']:
            st = sub_b.get(ch['id'])
            loose = third_b.get((ch['id'], '0'))
            ss = _stat(st)
            subs.append({
                'id': ch['id'], 'cid': ch['cid'], 'name': ch['name'], 'kind': ch['kind'],
                'declared': ch['declared'],
                # 二级的「全部」用分类树标称做分母；接口 total 只对单个三级可靠（各三级集合可重叠）
                'plan': ch['declared'] if ch['declared'] else ss['total'],
                **ss,
            })
            if st:
                t_skus |= st['skus']
            ths = []
            t3_skus = set()
            for k in ch['thirds']:
                s3 = _stat(third_b.get((ch['id'], k['id'])))
                ths.append({
                    'id': k['id'], 'name': k['name'], 'declared': k['declared'],
                    'plan': s3['total'] if s3['total'] is not None else k['declared'],
                    **s3,
                })
                b3 = third_b.get((ch['id'], k['id']))
                if b3:
                    t3_skus |= b3['skus']
            subs[-1]['thirds'] = ths
            # 这个二级下有多少条已经能对上某个三级、多少条还没在任何三级标签里出现过
            if st:
                subs[-1]['known'] = len(st['skus'] & t3_skus)
                subs[-1]['unk'] = len(st['skus'] - t3_skus)
        tops.append({
            'id': top['id'], 'name': top['name'], 'icon': top['icon'],
            'declared': top['declared'], 'children': subs,
            'skus': len(t_skus),
            'pages': sum(s['pages'] for s in subs),
            'imgs': sum(s['imgs'] for s in subs),
        })
    nimg = len(os.listdir(IMGS)) if os.path.isdir(IMGS) else 0
    ncat = sum(1 for t in tops if t['pages'] > 0)
    cur = read_cur()
    return {
        'ts': time.strftime('%H:%M:%S'),
        'tops': tops,
        'cur': cur,
        'stats': {
            'cats': ncat, 'catsAll': len(tops),
            'pages': npage, 'sku': total_sku, 'imgs': nimg,
            'declared': sum((t.get('declared') or 0) for t in tops),
        },
    }


def state():
    """统计走 1.2 秒缓存（全量扫描较重）；当前位置每次都重新读，保证秒级跟随。"""
    with _locks:
        if time.time() - _scan_cache['t'] < 1.2 and _scan_cache['data']:
            d = dict(_scan_cache['data'])
        else:
            d = build_state()
            _scan_cache['t'] = time.time()
            _scan_cache['data'] = d
            d = dict(d)
    d['cur'] = read_cur()
    d['link'] = read_link()
    return d


def items_of(top=None, sub=None, third=None):
    tree = load_tree() or []
    sub_b, third_b, _, _ = scan_all(tree)
    out = []
    if sub:
        if third and third not in ('ALL', '', None):
            out = list(third_b.get((sub, str(third)), {}).get('rows', []))
        else:
            out = list(sub_b.get(sub, {}).get('rows', []))
    elif top:
        cur = next((t for t in tree if t['id'] == top), None)
        if cur:
            for ch in cur['children']:
                out.extend(sub_b.get(ch['id'], {}).get('rows', []))
    else:
        for v in sub_b.values():
            out.extend(v['rows'])
    out.sort(key=lambda x: (x.get('parent') or '', x.get('seq') or 0))
    return out


HTML = r"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>小象超市抓取看板</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
html{zoom:1.25}   /* 整体放大到 125% */
:root{--g:#12A05C;--g2:#E8F6EF;--red:#E4393C;--tx:#1F1F1D;--tx2:#8A8A83;--line:rgba(0,0,0,.07);--bg:#F4F4F1}
html,body{height:100%}
body{font-family:system-ui,-apple-system,"PingFang SC","Microsoft YaHei",sans-serif;background:var(--bg);color:var(--tx);font-size:13px;display:flex;flex-direction:column;overflow:hidden}
header{display:flex;align-items:center;gap:18px;padding:8px 16px;background:#fff;border-bottom:1px solid var(--line);flex:none}
h1{font-size:14px;font-weight:600;letter-spacing:.2px}
.led{width:7px;height:7px;border-radius:50%;background:var(--g);box-shadow:0 0 0 3px var(--g2);animation:bl 1.6s infinite;flex:none}
@keyframes bl{0%,100%{opacity:1}50%{opacity:.3}}
.st{display:flex;align-items:baseline;gap:4px}
.st b{font-size:15px;font-weight:600;font-variant-numeric:tabular-nums}
.st span{font-size:11px;color:var(--tx2)}
.here{margin-left:auto;font-size:11.5px;color:var(--tx2);display:flex;align-items:center;gap:6px;white-space:nowrap}
.here b{color:var(--g);font-weight:600}
.here .dot{width:5px;height:5px;border-radius:50%;background:var(--g)}
.here .dot.warn{background:#E0A020;box-shadow:0 0 0 3px rgba(224,160,32,.16)}
.here .wname{color:#A06800;font-weight:600}
.fol{flex:none;font-size:11px;padding:2px 9px;border-radius:10px;border:1px solid var(--line);
  background:#fff;color:var(--tx2);cursor:pointer;line-height:1.6}
.fol.on{background:var(--g);color:#fff;border-color:var(--g)}
.fol.off{background:#FFF6E2;color:#A06800;border-color:#F0D9A8}
.fol.away{background:#fff;color:var(--g);border-color:var(--g);font-weight:600}
.fol.away::after{content:' ↺'}
.here.hit{animation:pop .55s ease}
#toastBox{position:fixed;left:50%;bottom:30px;transform:translateX(-50%) translateY(10px);
  background:#1F1F1D;color:#fff;font-size:12.5px;line-height:1.5;max-width:70vw;
  padding:9px 16px;border-radius:8px;opacity:0;pointer-events:none;transition:.2s;z-index:99}
#toastBox.show{opacity:1;transform:translateX(-50%) translateY(0)}
#toastBox.ok{background:var(--g)}
@keyframes pop{0%{transform:scale(1)}35%{transform:scale(1.07)}100%{transform:scale(1)}}

nav{display:flex;gap:7px;padding:8px 12px;background:#fff;border-bottom:1px solid var(--line);overflow-x:auto;flex:none;scrollbar-width:none}
nav::-webkit-scrollbar{display:none}
.tab{flex:none;display:flex;align-items:center;gap:5px;padding:6px 12px;border-radius:16px;cursor:pointer;background:#F4F4F1;color:#3A3A36;transition:.15s;white-space:nowrap}
.tab:hover{background:#EDEDE7}
.tab.on{background:var(--g);color:#fff;font-weight:600}
.tab .ic{width:18px;height:18px;border-radius:50%;overflow:hidden;background:#fff;display:flex;align-items:center;justify-content:center;font-size:10px;color:var(--tx2);flex:none}
.tab .ic img{width:100%;height:100%;object-fit:cover}
.tab em{font-style:normal;font-size:12.5px}
.tab i{font-style:normal;font-size:10px;opacity:.75;font-variant-numeric:tabular-nums}

main{flex:1;display:flex;min-height:0}
aside{width:118px;flex:none;overflow-y:auto;background:#fff;border-right:1px solid var(--line);scrollbar-width:thin}
.sb{display:flex;align-items:center;padding:12px 8px 12px 11px;cursor:pointer;position:relative;border-bottom:1px solid rgba(0,0,0,.03)}
.sb .tx{flex:1;min-width:0}
.sb .nm{font-size:12.5px;line-height:1.25;color:#4A4A45;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.sb .mt{font-size:10px;color:#B4B4AD;margin-top:3px;font-variant-numeric:tabular-nums}
.sb.on{background:#FAFAF7}
.sb.on:before{content:"";position:absolute;left:0;top:9px;bottom:9px;width:3px;border-radius:0 2px 2px 0;background:var(--g)}
.sb.on .nm{color:var(--g);font-weight:600}
.sb.done .mt,.sb.on .mt{color:var(--g);opacity:.75}
.sb:hover{background:#FAFAF7}
.bar{height:2px;background:#EFEFE9;border-radius:2px;margin-top:5px;overflow:hidden}
.bar i{display:block;height:100%;background:var(--g);border-radius:2px}
.bar.part i{background:#F0B429}

section{flex:1;min-width:0;display:flex;flex-direction:column}
.chips{display:flex;gap:7px;padding:9px 12px;overflow-x:auto;background:#fff;border-bottom:1px solid var(--line);flex:none;scrollbar-width:none}
.chips::-webkit-scrollbar{display:none}
.chip{flex:none;display:flex;align-items:center;gap:4px;padding:5px 12px;border-radius:15px;background:#F4F4F1;color:#4A4A45;font-size:12px;cursor:pointer;white-space:nowrap;transition:.13s}
.chip:hover{background:#EDEDE7}
.chip.on{background:var(--g2);color:var(--g);font-weight:600;box-shadow:inset 0 0 0 1px var(--g)}
.chip .n{font-size:10px;opacity:.62;font-variant-numeric:tabular-nums}
.chip.have .n{opacity:.9;color:var(--g)}
.chip.on .n{opacity:1}
.scroll{flex:1;overflow-y:auto;padding:10px 12px 28px}
.path{font-size:11px;color:var(--tx2);padding:0 2px 8px;display:flex;gap:6px;align-items:center;flex-wrap:wrap;flex:1;min-width:0}
.pathbar{display:flex;align-items:flex-start;gap:8px}
.copybtn{flex:none;margin-top:-2px;font-size:10.5px;color:#6E6E66;background:#fff;border:1px solid var(--line);
  border-radius:10px;padding:3px 9px;cursor:pointer;line-height:1.4}
.copybtn:hover{color:var(--g);border-color:var(--g);background:var(--g2)}
.path b{color:#3A3A36;font-weight:600}
.path .hint{color:#9A9A92;background:#F7F7F4;padding:1px 7px;border-radius:9px}
.path .hint.warn{color:#A06800;background:#FFF6E2}
.path .hint.ok{color:#12A05C;background:#E8F6EF}
.here .hint{color:#9A9A92;background:#F7F7F4;padding:1px 7px;border-radius:9px;max-width:260px;
  overflow:hidden;text-overflow:ellipsis}
.here .hint.warn{color:#A06800;background:#FFF6E2}
.sb .mt.warn{color:#A06800;opacity:1}
.chip.warn .n{color:#C77B22;opacity:1;font-weight:600}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(156px,1fr));gap:9px}
.card{background:#fff;border-radius:10px;overflow:hidden;display:flex;flex-direction:column;border:1px solid rgba(0,0,0,.05)}
.thumb{height:118px;background:#F7F7F4;display:flex;align-items:center;justify-content:center;position:relative}
.thumb img{width:100%;height:100%;object-fit:contain;mix-blend-mode:multiply}
.thumb .no{font-size:10px;color:#C4C4BC}
.thumb .t3{position:absolute;left:5px;top:5px;background:rgba(255,255,255,.9);color:var(--g);font-size:9px;padding:1px 5px;border-radius:8px;max-width:80%;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.bd{padding:7px 8px 9px;display:flex;flex-direction:column;gap:3px}
.bd .nm{font-size:12px;line-height:1.35;height:32px;overflow:hidden;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical}
.bd .lb{font-size:10px;color:var(--g);background:var(--g2);border-radius:3px;padding:1px 4px;align-self:flex-start;max-width:100%;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.pr{display:flex;align-items:baseline;gap:4px;flex-wrap:wrap;margin-top:1px}
.sale{color:var(--red);font-weight:600;font-size:14px;font-variant-numeric:tabular-nums}
.orig{color:#B9B9B2;font-size:11px;text-decoration:line-through}
.mb{font-size:10px;color:#C77B22;background:#FDF3E3;border-radius:3px;padding:1px 3px}
.ft{font-size:10px;color:#B9B9B2;display:flex;justify-content:space-between;gap:4px}
.empty{padding:60px 20px;text-align:center;color:#B9B9B2;line-height:2}
</style></head><body>
<header>
  <span class="led"></span><h1>抓取看板</h1>
  <div class="st"><b id="s1">0</b><span>已抓分类</span></div>
  <div class="st"><b id="s2">0</b><span>页</span></div>
  <div class="st"><b id="s3">0</b><span>SKU</span></div>
  <div class="st"><b id="s4">0</b><span>图片</span></div>
  <div class="st"><b id="s5">-</b><span>全站标称</span></div>
  <div class="here" id="here"><span id="hereTxt"></span>
    <button class="fol" id="launchBtn" title="没开就自动打开（约 3 秒）；已经开着就直接调到前台">启动小程序</button>
    <button class="fol on" id="folBtn" title="跟随小程序：你在小程序里点到哪个分类，看板自动跳过去">跟随小程序</button>
    <button class="fol" id="modeBtn" title="自动：脚本自己上下滑动，并跳到没抓完的分类；手动：只记录，你自己翻">手动</button></div>
</header>
<nav id="tops"></nav>
<main>
  <aside id="side"><div class="empty">等待分类树…</div></aside>
  <section>
    <div class="chips" id="chips"></div>
    <div class="scroll">
      <div class="pathbar">
        <div class="path" id="path"></div>
        <button class="copybtn" id="copylink" title="复制当前分类的直达链接">复制链接</button>
      </div>
      <div class="grid" id="grid"><div class="empty">等待数据…</div></div>
    </div>
  </section>
</main>
<script>
let S=null, curTop=null, curSub=null, curThird='ALL', lastKey='', lastChipKey='', booted=false;
/* 支持 ?top=40488&sub=40488:48602&third=297874 直达某个分类 */
const UQ=new URLSearchParams(location.search);
const Q_TOP=UQ.get('top'), Q_SUB=UQ.get('sub'), Q_THIRD=UQ.get('third');
const $=id=>document.getElementById(id);
const esc=s=>String(s==null?'':s).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const $top=()=>S&&S.tops.find(x=>x.id===curTop);
const $sub=()=>{const t=$top();return t&&t.children.find(c=>c.id===curSub);};
function pctOf(n){ const d=n.plan||0; if(!d) return 0; return Math.min(100,Math.round((n.skus||0)/d*100)); }

/* ---- 地址栏跟随：点哪个分类，网址就跳到哪个分类（可复制、可刷新、可前进后退） ---- */
function curUrl(){
  const q=new URLSearchParams();
  if(curTop) q.set('top',curTop);
  if(curSub) q.set('sub',curSub);
  if(curThird&&curThird!=='ALL') q.set('third',curThird);
  const s=q.toString();
  return location.pathname+(s?'?'+s:'');
}
function syncUrl(mode){
  try{
    const u=curUrl();
    if(u===location.pathname+location.search) return;
    const st={top:curTop,sub:curSub,third:curThird};
    if(mode==='push') history.pushState(st,'',u);
    else history.replaceState(st,'',u);
  }catch(e){}
}
/* 切分类时把商品区滚回顶部，不然还停在上一个分类的滚动位置 */
function viewTop(){ const sc=document.querySelector('.scroll'); if(sc) sc.scrollTop=0; }
function applyUrl(){
  const q=new URLSearchParams(location.search);
  const t=q.get('top'), s=q.get('sub'), th=q.get('third')||'ALL';
  if(!S) return;
  if(t&&S.tops.some(x=>x.id===t)) curTop=t;
  const top=$top();
  if(!top) return;
  curSub=(s&&top.children.some(c=>c.id===s))?s:((top.children.find(c=>c.skus>0)||top.children[0]||{}).id||null);
  const sub=$sub();
  curThird=(sub&&th!=='ALL'&&(sub.thirds||[]).some(x=>String(x.id)===th))?th:'ALL';
}
window.addEventListener('popstate',function(){
  applyUrl();
  manualAway=true; updateFollowUI();
  renderTops(); renderSide(); renderChips(); loadItems(); viewTop();
});

/* ---- 反向跟随：你在小程序里点到哪，看板就自动跳到那一层（含三级分支） ---- */
let FOLLOW=true;
try{ FOLLOW = localStorage.getItem('wb_follow')!=='0'; }catch(e){}
let lastCurKey='', curSeen=false, manualAway=false, linkOk=true;
/* 最近一次链路心跳。tick() 那条 1.5 秒的路径不带心跳参数，
   以前它会按「已连上」去画，于是断线时位置栏 1.5 秒就被擦回「正在看…」，
   200ms 后又变回「未接上」—— 看上去就是两行字来回闪。这里留一份最新的给它用。 */
let linkState={ok:true, why:''};
function updateFollowUI(){
  const b=$('folBtn'); if(!b) return;
  if(!FOLLOW){ b.textContent='已暂停跟随'; b.className='fol'; b.title='已暂停；点一下恢复跟随，并立刻跳到小程序当前位置'; return; }
  if(!linkOk){ b.textContent='未接上'; b.className='fol off';
    b.title='抓包端没连上小程序，跟随暂时没有数据源；连上后会自动恢复。看这里：把小程序整个关掉再打开一次'; return; }
  if(manualAway){ b.textContent='回到小程序位置'; b.className='fol on away';
    b.title='你正在看别的分类；点一下回到小程序当前那一页'; return; }
  b.textContent='跟随中'; b.className='fol on';
  b.title='你在小程序里切换分类时，看板会自动跳过去（点一下可暂停）';
}
function curSig(c){ return [c.top,c.sub,c.third==null?0:c.third].join('|'); }
function flashHere(){
  const el=$('here'); if(!el) return;
  el.classList.remove('hit'); void el.offsetWidth; el.classList.add('hit');
}
/* 只改选中态，不渲染；返回 true 表示位置真的变了 */
function applyCur(c){
  if(!S||!c||c.top==null) return false;
  const top=S.tops.find(t=>String(t.id)===String(c.top));
  if(!top) return false;
  let nSub=curSub;
  if(c.sub!=null){
    const sb=top.children.find(x=>String(x.cid)===String(c.sub));
    if(sb) nSub=sb.id;
  }
  const newTop=String(curTop)!==String(top.id);
  if(newTop && nSub===curSub){            // 换了一级但没定位到二级：挑一个有数据的
    const guess=top.children.find(x=>x.skus>0)||top.children[0];
    nSub=guess?guess.id:null;
  }
  let nThird='ALL';
  if(nSub){
    const sb=top.children.find(x=>x.id===nSub);
    if(sb&&c.third!=null&&String(c.third)!=='0'){
      const hit=(sb.thirds||[]).find(x=>String(x.id)===String(c.third));
      if(hit) nThird=String(hit.id);
    }
  }
  const changed = newTop || curSub!==nSub || curThird!==nThird;
  curTop=top.id; curSub=nSub; curThird=nThird;
  return changed;
}
function markRendered(){
  if(!S) return;
  const s=$sub();
  lastKey=curTop+'|'+S.stats.pages;
  lastChipKey=curTop+'|'+curSub+'|'+curThird+'|'+((s&&s.thirds||[]).map(x=>x.skus).join('.'))+'|'+(s?s.skus:0);
}
function jumpBackToCur(){
  const c=(S&&S.cur)||{};
  if(c.top==null) return false;
  curSeen=true; lastCurKey=curSig(c);
  const changed=applyCur(c);
  manualAway=false;
  // 立即渲染，不等下一次 tick（tick 是 1.5 秒一次，等它会有明显延迟）
  renderTops(); renderSide(); renderChips(); loadItems();
  markRendered();
  syncUrl('replace'); updateFollowUI(); viewTop(); flashHere();
  return changed;
}
$('folBtn').onclick=function(){
  if(FOLLOW && manualAway){ jumpBackToCur(); return; }
  FOLLOW=!FOLLOW;
  try{ localStorage.setItem('wb_follow',FOLLOW?'1':'0'); }catch(e){}
  updateFollowUI();
  if(FOLLOW) jumpBackToCur();
};
updateFollowUI();

/* 自动 / 手动：自动模式下抓包端会自己滚动，并跳到没抓完的分类 */
let AUTO=false;
function paintMode(){
  const b=$('modeBtn'); if(!b) return;
  b.textContent=AUTO?'自动':'手动';
  b.classList.toggle('on',AUTO);
  b.title=AUTO?'自动：脚本自己上下滑动、并跳到没抓完的分类（再点一下回到手动）'
              :'手动：只记录，由你自己翻页';
}
async function loadMode(){
  try{ const r=await (await fetch('/api/mode')).json(); AUTO=!!r.auto; }catch(e){}
  paintMode();
}
/* 一键启动小程序 */
function toast(msg, ok){
  let t=$('toastBox');
  if(!t){ t=document.createElement('div'); t.id='toastBox'; document.body.appendChild(t); }
  t.textContent=msg; t.className= ok?'ok show':'show';
  clearTimeout(t._h); t._h=setTimeout(function(){ t.className=''; }, 4000);
}
$('launchBtn').onclick=async function(){
  const b=this, old=b.textContent;
  b.disabled=true; b.textContent='启动中…';
  try{
    const r=await (await fetch('/api/launch')).json();
    toast(r.msg||(r.ok?'已启动':'启动失败'), !!r.ok);
  }catch(e){ toast('启动请求失败：'+e, false); }
  setTimeout(function(){ b.disabled=false; b.textContent=old; }, 1200);
};

$('modeBtn').onclick=async function(){
  AUTO=!AUTO; paintMode();
  try{ await fetch('/api/mode?auto='+(AUTO?1:0)); }catch(e){}
};
loadMode();
setInterval(loadMode,5000);

/* 高频轮询当前位置（200ms）：只拉几百字节，命中变化就立刻跳，不等 1.5 秒的统计刷新 */
let CUR_CACHE={}, hereCache='';
function renderHere(c, lk){
  lk = lk || linkState;        // 没带心跳就用最近一次的，别默认当成「已连上」
  let h='';
  if(lk && lk.ok===false){
    // 心跳断了：宁可明说「没接上」，也别拿几十小时前的旧位置假装跟着
    const why=(lk.why||'').trim();
    h=`<span class="dot warn"></span><span class="wname">小程序未接上</span>`+
      (c&&c.top!=null?`<span class="hint">最后位置：${esc(c.subName||'')}${c.thirdName?' / '+esc(c.thirdName):''}</span>`:'')+
      (why?`<span class="hint warn">${esc(why)}</span>`:'');
  } else if(c&&c.top!=null){
    h=`<span class="dot"></span>小程序正在看：<b>${esc(c.subName||'')}</b>${c.thirdName?' / '+esc(c.thirdName):''}`;
  } else {
    // 链路通、但还没读到过分类位置（刚连上 / 停在小程序首页）
    h=`<span class="dot"></span><span class="hint">已连上小程序，等你在小程序里点进任意分类</span>`;
  }
  if(h!==hereCache){ hereCache=h; $('hereTxt').innerHTML=h; }
}
async function pollCur(){
  try{
    const r=await (await fetch('/api/cur')).json();
    const c=r.cur||{};
    const lk=r.link||{ok:true};
    linkState=lk;
    CUR_CACHE=c;
    if(S) S.cur=c;
    if(lk.ok!==linkOk){ linkOk=lk.ok; updateFollowUI(); }
    renderHere(c, lk);
    if(!S||!S.tops||!S.tops.length) return;
    if(!FOLLOW||c.top==null) return;
    const sig=curSig(c);
    if(!curSeen){
      curSeen=true; lastCurKey=sig;
      if(Q_TOP){ manualAway=true; updateFollowUI(); }
      else jumpBackToCur();
    } else if(sig!==lastCurKey){
      lastCurKey=sig;
      jumpBackToCur();
    }
  }catch(e){}
}

/* 鼠标滚轮 → 横向滑动（顶部一级、三级标签两行都可用） */
function wheelX(el){
  el.addEventListener('wheel',function(e){
    if(Math.abs(e.deltaX)>Math.abs(e.deltaY)) return;
    const max=el.scrollWidth-el.clientWidth;
    if(max<=0) return;
    el.scrollLeft += e.deltaY;
    e.preventDefault();
  },{passive:false});
}
function centerIn(el,sel){
  const it=el.querySelector(sel); if(!it) return;
  const l=it.offsetLeft-(el.clientWidth-it.offsetWidth)/2;
  if(l>0||it.offsetLeft+it.offsetWidth>el.scrollLeft+el.clientWidth) el.scrollLeft=Math.max(0,l);
}

function renderTops(){
  const box=$('tops');
  box.innerHTML=S.tops.map(t=>{
    const ic=t.icon?`<img src="/pic?u=${encodeURIComponent(t.icon)}">`:esc(t.name.slice(0,1));
    return `<div class="tab ${curTop===t.id?'on':''}" data-id="${t.id}">
      <span class="ic">${ic}</span><em>${esc(t.name)}</em>
      <i>${t.skus||''}</i></div>`;
  }).join('');
  box.querySelectorAll('.tab').forEach(el=>el.onclick=()=>{
    curTop=el.dataset.id; curSub=null; curThird='ALL';
    const t=$top(); if(t){ const best=t.children.find(c=>c.skus>0)||t.children[0]; if(best) curSub=best.id; }
    renderTops(); renderSide(); renderChips(); loadItems(); manualAway=true; updateFollowUI(); syncUrl('push'); viewTop();
  });
}

function renderSide(){
  const t=$top(), box=$('side');
  if(!t){box.innerHTML='<div class="empty">选一个一级分类</div>';return;}
    box.innerHTML=t.children.map(c=>{
    const p=pctOf(c);
    // 二级是多个三级的并集（互相重叠），标称值不可靠：只在「条数没到标称且确实还有页没翻」时才提示缺页
    const short = c.plan && (c.skus||0) < c.plan && !c.end;
    const mt = !c.skus ? '待抓'
      : short ? `${c.skus||0}-${c.plan} 条 · 还缺 ${c.gap||'?'} 页`
              : `${c.skus} 条`;
    const tip = `${c.name}：已抓 ${c.skus||0} 条`
      + (c.pt?`；服务端共 ${c.pt} 页，已翻 ${c.pn} 页`:'')
      + (c.plan?`；二级标称 ${c.plan} 条`:'');
    return `<div class="sb ${curSub===c.id?'on':''} ${!short&&c.skus?'done':''}" data-id="${c.id}"
      title="${esc(tip)}">
      <div class="tx">
        <div class="nm">${esc(c.name||'未命名')}</div>
        <div class="mt ${short?'warn':''}">${mt}</div>
        ${c.plan?`<div class="bar ${!short&&c.skus?'':'part'}"><i style="width:${p}%"></i></div>`:''}
      </div></div>`;
  }).join('');
  box.querySelectorAll('.sb').forEach(el=>el.onclick=()=>{
    curSub=el.dataset.id; curThird='ALL';
    renderSide(); renderChips(); loadItems(); manualAway=true; updateFollowUI(); syncUrl('push'); viewTop();
  });
  const on=box.querySelector('.sb.on'); if(on) on.scrollIntoView({block:'nearest'});
}

/* 顶部第二行：当前二级的三级标签 */
function renderChips(){
  const s=$sub(), box=$('chips');
  if(!s){box.innerHTML='';return;}
  const list=[{id:'ALL',name:'全部',plan:s.plan,skus:s.skus,pn:s.pn,pt:s.pt,end:s.end,gap:s.gap}]
    .concat((s.thirds||[]).map(t=>({id:String(t.id),name:t.name,plan:t.plan,skus:t.skus,pn:t.pn,pt:t.pt,end:t.end,gap:t.gap})));
  box.innerHTML=list.map(c=>{
    // 「全部」是二级汇总（三级之间重叠），只报条数；三级才给「已抓/接口总数」
    const isAll = c.id==='ALL';
    // 分类树里 skuCount 就是小程序标签上那个数字（2299 个三级全都有）。
    // 没抓完写「已抓-总数」（70-150，黄色）；抓完只写总数（150），不挂分母。
    // 「抓完」有两种：条数达标，或接口已经翻到底（有些分类标称 31、接口只给 24，
    // 那 7 条是不再上架/无货，永远翻不到，不该一直挂黄字）
    const done = (c.plan && (c.skus||0) >= c.plan) || c.end;
    const num = isAll ? (c.skus||0)
                      : (c.plan ? (done ? `${c.skus}` : `${c.skus||0}-${c.plan}`) : (c.skus||0));
    const short = !isAll && !done;
    const tip = `${c.name}：已抓 ${c.skus||0} 条`
      + (c.pt?`；共 ${c.pt} 页已翻 ${c.pn} 页`:'')
      + (!isAll&&c.plan?`；接口总数 ${c.plan} 条`:'');
    return `<div class="chip ${curThird===c.id?'on':''} ${c.skus?'have':''} ${short?'warn':''}"
      data-id="${esc(c.id)}" title="${esc(tip)}">
      ${esc(c.name)}<span class="n">${num}</span></div>`;
  }).join('');
  box.querySelectorAll('.chip').forEach(el=>el.onclick=()=>{
    curThird=el.dataset.id; renderChips(); loadItems(); manualAway=true; updateFollowUI(); syncUrl('push'); viewTop();
  });
  centerIn(box,'.chip.on');
}

async function loadItems(){
  const q=curSub?`sub=${encodeURIComponent(curSub)}&third=${encodeURIComponent(curThird)}`
                :`top=${encodeURIComponent(curTop||'')}`;
  let d={items:[]};
  try{ d=await (await fetch('/api/items?'+q)).json(); }catch(e){}
  const s=$sub(), t=$top();
  const t3=(s&&s.thirds||[]).find(x=>String(x.id)===curThird);
  let loose='';
  if(curThird==='ALL'&&s&&((s.known||0)+(s.unk||0))){
    const z=(s.thirds||[]).filter(t=>!t.skus).map(t=>t.name);
    loose=`<span style="color:#C9A227">${s.known||0} 条已归到三级`
      + ((s.unk||0)?`，${s.unk} 条还没在任何三级标签里出现过`:'')
      + (z.length?`；点一下${z.slice(0,3).join('、')}${z.length>3?' 等':''}可补归类`:'')
      + `</span>`;
  }
  // 缺口说明：优先讲「翻到第几页」，而不是拿标称数字吓人
  const node = curThird==='ALL' ? s : t3;
  let note='';
  if(node){
    if(curThird==='ALL'){
      // 二级「全部」是各三级的并集，条数通常已经齐；只有整条二级列表的页没翻完才提示
      if(node.gap>0) note=`<span class="hint">二级整表还有 ${node.gap} 页没翻（${node.pn}/${node.pt}），条数主要来自三级并集</span>`;
      else if(node.pt) note=`<span class="hint ok">二级整表 ${node.pt} 页已翻完</span>`;
    } else if(node.pt && !node.end){
      const cap = node.plan ? `共 ${node.plan} 条，已抓 ${node.skus||0} 条`
                            : `已抓 ${node.skus||0} 条`;
      note=`<span class="hint">${cap} · 接口 ${node.pt} 页已翻 ${node.pn} 页</span>`;
    } else if(node.end && node.plan && (node.skus||0)<node.plan){
      note=`<span class="hint warn">已翻到底，接口只给 ${node.skus} 条，总数 ${node.plan}（差 ${node.plan-node.skus} 条，通常是下架/无货/不在配送范围）</span>`;
    } else if(node.end && node.skus){
      note=`<span class="hint ok">已翻到底 · ${node.skus} 条齐了</span>`;
    }
  }
  $('path').innerHTML = t? `<b>${esc(t.name)}</b> › <b>${esc(s?s.name:'')}</b>${t3?' › '+esc(t3.name):''}
      <span style="color:#B9B9B2">本页 ${d.items.length} 条</span>${note}${loose}` : '';
  const box=$('grid');
  if(!d.items.length){
    box.innerHTML=`<div class="empty">「${esc(t3?t3.name:'全部')}」还没有抓到数据<br>在小程序里点进这个标签，往下滑几下</div>`;
    return;
  }
  const t3name={}; (s&&s.thirds||[]).forEach(x=>t3name[String(x.id)]=x.name);
  box.innerHTML=d.items.map(i=>`<div class="card">
    <div class="thumb">${i.img?`<img loading="lazy" src="/img/${encodeURIComponent(i.img)}">`:'<span class="no">无图</span>'}
      ${i.third&&i.third!=='0'&&t3name[i.third]?`<span class="t3">${esc(t3name[i.third])}</span>`:''}</div>
    <div class="bd">
      <div class="nm">${esc(i.name)}</div>
      ${i.label?`<div class="lb">${esc(String(i.label).split(',')[0])}</div>`:''}
      <div class="pr"><span class="sale">${esc(i.sale)}</span>
        ${i.orig?`<span class="orig">${esc(i.orig)}</span>`:''}
        ${i.member?`<span class="mb">会员${esc(i.member)}</span>`:''}</div>
      <div class="ft"><span>${esc(i.sku)}</span><span>${esc(i.catName||'')}</span></div>
    </div></div>`).join('');
}

async function tick(){
  try{
    const d=await (await fetch('/api/state')).json();
    d.cur=CUR_CACHE;              // 位置以 200ms 高频轮询为准，别被 1.2 秒统计缓存带回旧值
    S=d;
    $('s1').textContent=d.stats.cats+'/'+d.stats.catsAll;
    $('s2').textContent=d.stats.pages;
    $('s3').textContent=d.stats.sku;
    $('s4').textContent=d.stats.imgs;
    $('s5').textContent=d.stats.declared||'-';
    if(d.link){                   // 顺手用这条 1.5 秒的通道校准心跳，断线时按钮也能跟上
      linkState=d.link;
      if(d.link.ok!==linkOk){ linkOk=d.link.ok; updateFollowUI(); }
    }
    renderHere(CUR_CACHE);
    if(!d.tops.length){$('tops').innerHTML='';return;}
    if(!booted){
      booted=true;
      if(Q_TOP&&d.tops.some(t=>t.id===Q_TOP)) curTop=Q_TOP;
      if(Q_THIRD) curThird=Q_THIRD;
    }
    if(!curTop||!d.tops.some(t=>t.id===curTop)){
      const first=d.tops.find(t=>t.pages>0)||d.tops[0];
      if(first) curTop=first.id;
    }
    const t=d.tops.find(x=>x.id===curTop);
    if(t&&(!curSub||!t.children.some(c=>c.id===curSub))){
      if(Q_SUB&&t.children.some(c=>c.id===Q_SUB)) curSub=Q_SUB;
      else {
        const best=t.children.find(c=>c.skus>0)||t.children[0];
        if(best) curSub=best.id;
        if(!Q_THIRD) curThird='ALL';
      }
    }
    // 三级 id 必须属于当前二级，否则清掉（防止旧链接指到别的分类）
    { const sb=$sub();
      if(sb&&curThird!=='ALL'&&!(sb.thirds||[]).some(x=>String(x.id)===curThird)) curThird='ALL'; }
    syncUrl();
    const key=curTop+'|'+d.stats.pages;
    if(key!==lastKey){ lastKey=key; renderTops(); renderSide(); renderChips(); loadItems(); }
    else {
      const ck=curTop+'|'+curSub+'|'+curThird+'|'+($sub()?($sub().thirds||[]).map(x=>x.skus).join('.'):'')+'|'+($sub()?$sub().skus:0);
      if(ck!==lastChipKey){ lastChipKey=ck; renderSide(); renderChips(); }
    }
  }catch(e){ $('here').textContent='连接断开'; }
}
wheelX($('tops')); wheelX($('chips'));
$('copylink').onclick=async function(){
  const u=location.origin+curUrl();
  let ok=false;
  try{ await navigator.clipboard.writeText(u); ok=true; }catch(e){}
  if(!ok){
    const ta=document.createElement('textarea'); ta.value=u; ta.style.position='fixed'; ta.style.opacity='0';
    document.body.appendChild(ta); ta.select();
    try{ document.execCommand('copy'); ok=true; }catch(e){}
    ta.remove();
  }
  const b=this, old=b.textContent;
  b.textContent=ok?'已复制 ✓':'复制失败';
  setTimeout(()=>{ b.textContent=old; },1400);
};
tick(); setInterval(tick,1500);
pollCur(); setInterval(pollCur,200);
</script></body></html>"""


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, body, ctype, cache='no-store'):
        self.send_response(200)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', cache)
        self.end_headers()
        try:
            self.wfile.write(body)
        except Exception:
            pass

    def do_GET(self):
        p = self.path.split('?')[0]
        qs = parse_qs(self.path.split('?', 1)[1]) if '?' in self.path else {}
        if p == '/':
            return self._send(HTML.encode('utf-8'), 'text/html; charset=utf-8')
        if p == '/api/state':
            return self._send(json.dumps(state(), ensure_ascii=False).encode('utf-8'),
                              'application/json; charset=utf-8')
        if p == '/api/cur':
            # 极轻量：只返回小程序当前位置 + 链路心跳，供前端高频轮询
            return self._send(json.dumps({'cur': read_cur(), 'link': read_link(),
                                          'ts': time.time()},
                                         ensure_ascii=False).encode('utf-8'),
                              'application/json; charset=utf-8')
        if p == '/api/launch':
            r = launch_miniapp()
            return self._send(json.dumps(r, ensure_ascii=False).encode('utf-8'),
                              'application/json; charset=utf-8')
        if p == '/api/mode':
            if 'auto' in qs:
                on = (qs.get('auto') or ['0'])[0] in ('1', 'true', 'on')
                try:
                    with open(MODE_F, 'w', encoding='utf-8') as f:
                        json.dump({'auto': on, 'ts': time.time()}, f)
                except Exception:
                    pass
                return self._send(json.dumps({'auto': on}).encode('utf-8'),
                                  'application/json; charset=utf-8')
            on = False
            try:
                with open(MODE_F, encoding='utf-8') as f:
                    on = bool(json.load(f).get('auto'))
            except Exception:
                pass
            return self._send(json.dumps({'auto': on}).encode('utf-8'),
                              'application/json; charset=utf-8')
        if p == '/api/items':
            top = (qs.get('top') or [''])[0]
            sub = (qs.get('sub') or [''])[0]
            third = (qs.get('third') or [''])[0]
            rows = items_of(top or None, sub or None, third or None)
            return self._send(json.dumps({'items': rows, 'n': len(rows)}, ensure_ascii=False).encode('utf-8'),
                              'application/json; charset=utf-8')
        if p == '/pic':
            url = (qs.get('u') or [''])[0]
            if not url:
                self.send_response(404); self.end_headers(); return
            if url in _pic_cache:
                return self._send(_pic_cache[url], 'image/jpeg', 'max-age=3600')
            try:
                if url.startswith('//'):
                    url = 'https:' + url
                req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
                data = urllib.request.urlopen(req, timeout=15).read()
                if len(_pic_cache) < 400:
                    _pic_cache[url] = data
                ct = 'image/png' if data[:4] == b'\x89PNG' else (
                    'image/webp' if data[8:12] == b'WEBP' else 'image/jpeg')
                return self._send(data, ct, 'max-age=3600')
            except Exception:
                self.send_response(404); self.end_headers(); return
        if p.startswith('/img/'):
            fn = os.path.basename(unquote(p[5:]))
            fp = os.path.join(IMGS, fn)
            if os.path.exists(fp):
                try:
                    body = open(fp, 'rb').read()
                except Exception:
                    body = b''
                ct = 'image/png' if body[:4] == b'\x89PNG' else (
                    'image/webp' if body[8:12] == b'WEBP' else 'image/jpeg')
                return self._send(body, ct, 'max-age=600')
        self.send_response(404)
        self.end_headers()


if __name__ == '__main__':
    try:
        from _pidutil import write_pid
        write_pid('_board.pid', base=BASE)
    except Exception:
        pass
    for port in range(PORT, PORT + 8):
        try:
            srv = ThreadingHTTPServer(('127.0.0.1', port), H)
            print('看板 v4 已启动: http://127.0.0.1:%d' % port, flush=True)
            srv.serve_forever()
        except OSError:
            continue
