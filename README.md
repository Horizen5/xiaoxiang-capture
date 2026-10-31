<p align="center">
  <img src="https://img.shields.io/badge/platform-Windows%2010%20%7C%2011-0078D4?style=flat-square" alt="platform">
  <img src="https://img.shields.io/badge/python-3.10%2B-3776AB?style=flat-square" alt="python">
  <img src="https://img.shields.io/badge/node-18%2B-339933?style=flat-square" alt="node">
  <img src="https://img.shields.io/badge/protocol-CDP-4285F4?style=flat-square" alt="cdp">
  <img src="https://img.shields.io/badge/license-MIT-3DA639?style=flat-square" alt="license">
</p>

**简体中文** | [English](README.en.md)

# 小象超市小程序抓包

读取微信小程序「小象超市」分类页的接口响应，保存到本地 JSON，同时下载商品图，并提供一个本地看板显示抓取进度。

- 只读取小程序自己发出的响应，不重放请求，不改参数
- 看板跑在 `127.0.0.1:8899`，不监听外网
- 支持手动翻页抓取，也可以让脚本按固定节奏补页

![看板](docs/screenshots/dashboard.png)

## 环境要求

| | |
| --- | --- |
| 系统 | Windows 10 / 11 |
| Python | 3.10+ |
| Node.js | 18+ |
| 微信 PC 版 | 4.1.15.56（其他版本需更新偏移表） |

## 安装

```bat
git clone https://github.com/Horizen5/xiaoxiang-capture.git
cd xiaoxiang-capture

pip install -r tools\requirements.txt
cd tools\wmpfdebugger && npm install && cd ..\..
```

## 使用

1. 完全关闭微信里的小程序（关闭窗口，不是退到上一页）
2. 双击 `start beta.bat`，等它打印「调试代理已就绪」
3. 打开小程序，进入「分类」页
4. 浏览器打开 <http://127.0.0.1:8899/>
5. 上下滑动翻页，看板上的数字会跟着涨

小程序要在启动器之后打开。CDP 连接是在小程序进程启动时建立的，先开的接管不到。

看板右上角的「启动小程序」可以自动完成第 3 步：已有窗口就调到前台，没有就走快捷键搜索打开，实测 2.8 秒。

## 数据

抓到的数据放在 `tools/` 下，运行时生成，不进仓库：

```
tools/live_raw/
├── c1003_f0_t0_o0.json      # 二级1003 一级0 三级0 偏移0
├── _meta.jsonl              # 每页一行：时间、分类、条数、code
├── _category_tree.json      # 分类树，含每个三级的标称数量
├── _current.json            # 小程序当前停在哪
└── _risk.json               # 风控记录

tools/live_imgs/             # 商品图，保留 CDN 原文件名
```

商品字段在 `data.contents[0].subModule.contents[0].showContents...singleDataInfo` 里，包含名称、价格、规格、标签和 SKU 号。`samples/` 下有一页真实数据和一份精简分类树，不用跑起来也能看懂格式，字段含义见 [docs/数据说明.md](docs/数据说明.md)。

`make_image_index.py` 会生成 `image_index.csv`，把 SKU、商品名、文件名对应起来。加 `--link` 会额外建一份按商品名命名的硬链接，不占空间。

## 项目结构

```
xiaoxiang-capture/
├── start beta.bat           # 双击入口
├── tools/
│   ├── start_beta.py        # 启动器，守护三个进程
│   ├── live_capture.py      # 抓包，连 CDP 抄响应
│   ├── monitor_server.py    # 看板服务
│   ├── make_image_index.py  # 生成图片索引
│   ├── _open_mini.py        # 一键打开小程序
│   ├── _pidutil.py          # pid 文件读写
│   └── wmpfdebugger/        # 调试代理，第三方项目
├── docs/
└── samples/
```

三个进程的关系：

```
小程序 ──CDP(62000)── wmpfdebugger ──▶ live_capture.py ──▶ live_raw / live_imgs
                                                             │
                                             monitor_server.py(8899) ──▶ 看板
```

`start_beta.py` 负责把这三个拉起来，挂了自动重启。它会在几处常见位置找 node，找不到就提示，不会无限重试。

## 实现说明

**用 CDP 而不是代理。** 小程序走 WMPF 的自定义通道，普通代理看不到明文。CDP 直接连渲染进程，钩住 `wx.request` 拿到的就是结构化对象，不用解密也不用装证书。

**pid 文件存三个字段。** Windows 会复用 pid，上一个进程退出后它占的号可能被别的程序拿走。只判断「这个 pid 还在」的话，启动器会一直以为已经有一份在跑，清理残留时还可能杀错进程。所以记的是 `pid:进程名:创建时间`，三个都对上才算同一个进程。

**抓取节奏。** 只做被动记录时不用限速。脚本自己补页时取 2.1 秒一页，这个数是实测的：613 次请求没有一次返回非 0 的 code，最快稳定在 1.67 秒，留 20% 余量。另外同一时刻只调一个翻页方法，同时调多个会让小程序的翻页速度变成 0.5 秒一页，这时弹验证码的概率很高。

**看板数字。** 标称数量来自 `_category_tree.json` 的 `skuCount`。没抓满时显示 `已抓-标称` 并标黄，抓满只显示实际条数。接口翻到底也算抓满，因为有些分类标称 31 条但实际只给 24 条。

## 已知问题

- PC 微信没有对外的唤起小程序的接口。`weixin://launchapplet` 在 `Weixin.dll` 里存在，但只接受微信进程内部调用；`weixin://dl/business/?appid=` 需要服务端签发的 token，否则是白页。目前只能靠 UI 自动化，或者用小程序「添加到桌面」生成的 `.lnk`。
- 调试开关是进程级的。注入后 `RemoteDebugMode` 会一直是 1，只有重启微信才复位。抓完想恢复原状，需要彻底退出微信。
- 依赖微信的具体版本，升级后偏移可能失效。

## 常见问题

**抓的时候会不会触发风控？**

只做被动记录不会。让脚本自己补页时按 2.1 秒一页走，目前没触发过。

**图片为什么是数字名？**

用的是 CDN 原名。改商品名的话有 312 个文件因为含 `*`、`?` 这类字符写不进去。

**杀毒软件报警？**

调试代理会往微信进程注入代码，行为上确实像外挂。源码都在这里，可以自己看。

## 致谢

- [wmpfdebugger](https://github.com/evi0s/wmpfdebugger)：WMPF 调试代理，本项目在其基础上加了环境变量擦除
- [Frida](https://frida.re/)：运行时注入

## 免责声明

仅用于个人学习和数据分析，抓的是自己账号本来就能看到的商品信息。请勿用于商业爬取或对目标服务造成压力。

## License

[MIT](LICENSE)。`tools/wmpfdebugger/` 沿用原项目的 GPL-2.0。
