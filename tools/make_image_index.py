# -*- coding: utf-8 -*-
"""生成图片索引 + 可选的中文名硬链接目录。

为什么保留 CDN 原名（哈希/数字）：
  · 唯一 —— 同名商品很多（不同分类里都有「有机杭白菜」），用名字必然打架
  · 合法 —— 商品名带 / \\ : * ? " < > | 和 emoji，Windows 全非法
  · 长度 —— 「【地标认证】温县铁棍山药(净重)500g」这类名字接近 255 字节上限
  · 幂等 —— 名字一变就得重下，哈希名天生去重
所以名字只作为「另一层视图」，不替代主键。

用法：
  python make_image_index.py          只生成 image_index.csv
  python make_image_index.py --link   同时建 live_imgs_by_name/ 硬链接（不占空间）
"""
import csv
import os
import re
import sys

# monitor_server 会把 argv[1] 当端口，先把它清掉再导入
_ARGS = sys.argv[1:]
sys.argv = sys.argv[:1]

import monitor_server as M  # noqa: E402

BASE = os.path.dirname(os.path.abspath(__file__))
RAW = os.path.join(BASE, 'live_raw')
IMGDIR = os.path.join(BASE, 'live_imgs')
LINKDIR = os.path.join(BASE, 'live_imgs_by_name')
OUT = os.path.join(BASE, 'image_index.csv')

BAD = re.compile(r'[\\/:*?"<>|\r\n\t]+')


def safe_name(s, limit=110):
    s = BAD.sub('_', (s or '')).strip().strip('.')
    s = re.sub(r'\s+', ' ', s)
    if len(s) > limit:
        s = s[:limit]
    return s or 'unnamed'


def cat_path(tree, node):
    top = None
    for t in tree:
        if t['id'] == node.get('parent'):
            top = t
            break
    return (top['name'] if top else ''), node.get('name') or ''


def main():
    do_link = '--link' in _ARGS
    tree = M.load_tree() or []
    owner = M.build_index(tree)

    have = {}
    try:
        for fn in os.listdir(IMGDIR):
            have[fn] = round(os.path.getsize(os.path.join(IMGDIR, fn)) / 1024.0, 1)
    except Exception:
        pass

    rows = []
    seen = set()
    for fn in sorted(os.listdir(RAW)):
        if not (fn.startswith('c') and '_o' in fn):
            continue
        rec = M.parse_file(os.path.join(RAW, fn))
        if not rec:
            continue
        node = M.resolve_node(tree, owner, rec['cid'], rec['first'])
        top_name, sub_name = cat_path(tree, node) if node else ('', '')
        for r in rec['rows']:
            sku = str(r['sku'])
            if sku in seen:
                continue
            seen.add(sku)
            pfn = r.get('pfn') or ''
            rows.append({
                'sku': sku, 'name': r['name'], 'sale': r['sale'],
                'top': top_name, 'sub': sub_name,
                'img': pfn, 'url': '', 'kb': have.get(pfn, 0),
            })

    # 补 URL 和大小（URL 从页面 json 里再取一次太重，这里只按文件名对齐本地是否存在）
    with open(OUT, 'w', encoding='utf-8-sig', newline='') as f:
        w = csv.writer(f)
        w.writerow(['sku', '商品名', '售价', '一级分类', '二级分类', '图片文件名', '本地有图', '文件大小KB'])
        for r in sorted(rows, key=lambda x: (x['top'], x['sub'], x['sku'])):
            w.writerow([r['sku'], r['name'], r['sale'], r['top'], r['sub'],
                        r['img'], 'Y' if r['kb'] else 'N', r['kb']])

    n_img = sum(1 for r in rows if r['kb'])
    print('SKU %d 条，有图 %d 条，无图 %d 条 -> %s' % (len(rows), n_img, len(rows) - n_img, OUT))

    # 用名字命名会遇到的坑：非法字符 / 超长
    bad = [r['name'] for r in rows if BAD.search(r['name'])]
    longn = [r['name'] for r in rows if len(r['name']) > 60]
    print('名字里含 Windows 非法字符的 %d 条，超过 60 字的 %d 条' % (len(bad), len(longn)))
    if bad:
        print('  例：%s' % bad[0][:50])

    # 同名统计：这就是「用商品名命名」会撞车的证据
    from collections import Counter
    c = Counter(safe_name(r['name']) for r in rows if r['name'])
    dup = [(k, v) for k, v in c.most_common(5) if v > 1]
    if dup:
        print('重名商品 Top5（若按名字命名就会互相覆盖）：')
        for k, v in dup:
            print('  %-40s x%d' % (k[:40], v))

    if not do_link:
        return
    os.makedirs(LINKDIR, exist_ok=True)
    made = skip = 0
    used = Counter()
    for r in rows:
        if not r['kb']:
            continue
        ext = os.path.splitext(r['img'])[1].lower() or '.jpg'
        base = safe_name(r['name']) or ('sku' + r['sku'])
        key = base.lower()
        used[key] += 1
        if used[key] > 1:
            base = '%s_%s' % (base, r['sku'])
        dst = os.path.join(LINKDIR, base + ext)
        if os.path.exists(dst):
            skip += 1
            continue
        try:
            os.link(os.path.join(IMGDIR, r['img']), dst)
            made += 1
        except Exception:
            skip += 1
    print('中文名硬链接：新建 %d，跳过 %d -> %s' % (made, skip, LINKDIR))


if __name__ == '__main__':
    main()
