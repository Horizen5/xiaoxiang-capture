# 小象超市小程序抓包

读取微信小程序「小象超市」分类页的接口响应，保存到本地 JSON，同时下载商品图，并提供一个本地看板显示抓取进度。

> **简体中文** | [English](README.en.md)

- 只读取小程序自己发出的响应，不重放请求，不改参数
- 看板跑在 `127.0.0.1:8899`，不监听外网
- 支持手动翻页抓取，也可以让脚本按固定节奏补页

![platform](https://img.shields.io/badge/platform-Windows%2010%20%7C%2011-0078D4)
![python](https://img.shields.io/badge/python-3.10%2B-3776AB)
![node](https://img.shields.io/badge/node-18%2B-339933)
![protocol](https://img.shields.io/badge/protocol-CDP-4285F4)
![license](https://img.shields.io/badge/license-MIT-success)

![看板](docs/screenshots/dashboard.png)

## 用途

抓下来是为了把整份菜价丢给 AI 分析，不是拿去卖数据。具体用在这几件事上：

- **选菜**：从当季在售的上千个商品里挑出这周值得买的，省得每次打开小程序翻半天还不知道吃啥
- **比价**：同类商品算单价，比如 `230g*6袋` 和散装 500g 哪个划算，打折价和日常价差多少
- **配菜谱**：按买到的食材反推能做什么菜，减少买多了放坏

数据是结构化的 JSON，带上名称、价格、规格、标签和分类，可以直接喂给模型。

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

仓库里有两个启动脚本：

| 脚本 | 行为 |
|---|---|
| `启动抓包.bat` | 后台运行，双击即走，看板就绪后自动打开网页。已经在跑的时候再双击，只会把看板网页打开，不会重复起服务 |
| `start_beta.bat` | 前台运行，窗口里能看到「调试代理已就绪」「小程序已连上」这些过程信息，排查问题时用 |

也可以直接用命令行，`--daemon` 表示脱离当前终端在后台跑：

```bat
python tools\start_beta.py --daemon
```

步骤：

1. 双击 `启动抓包.bat`
2. 把微信里的小程序整个关掉（关窗口，退到上一页不算）
3. 重新打开小程序，进入「分类」页
4. 上下滑动翻页，看板上的数字会跟着涨

小程序要在启动器之后打开。CDP 连接是在小程序进程启动时建立的，先开的接管不到。

看板在 <http://127.0.0.1:8899/>。

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
├── 启动抓包.bat            # 双击入口，后台跑，自动开网页
├── start_beta.bat           # 前台跑，窗口里能看到过程信息
├── tools/
│   ├── start_beta.py        # 启动器，守护三个进程
│   ├── start_hidden.bat     # 启动抓包.bat 的源文件（GBK 编码）
│   ├── _mkbat.py            # 生成上面那个 bat，要改提示语改这里再重跑
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

**重复启动。** 再双击一次时先探 8899 端口。服务活着就把看板网页打开，打印「已启动网页」，不再重复起进程；pid 记录还在但进程没了，就把旧的收掉重新拉起。

**后台运行。** `start_beta.py --daemon` 用 `DETACHED_PROCESS` 重新拉起自己然后退出，服务不挂在调用方的终端上，关掉窗口也带不走。批处理 `启动抓包.bat` 走的就是这条路径。

**批处理用 GBK 保存。** cmd 按系统 ANSI 读批处理文件，存成 UTF-8 的话中文提示会变成问号。要改 `启动抓包.bat` 的内容，改 `tools/_mkbat.py` 里的 `LINES` 再重新生成，别直接编辑 bat 文件。

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

**双击了但浏览器没反应？**

看板起来之后浏览器才会自动打开，先等两三秒。还是没有的话，手动访问 <http://127.0.0.1:8899/>。这个页面只监听 127.0.0.1，别的机器访问不到。

**点看板上的「启动小程序」没反应？**

微信收进托盘时窗口还在，但 `IsWindowVisible` 是假，早期的版本找不到它就放弃了。现在会退回找隐藏的窗口并恢复显示。如果仍然不行，先把微信主窗口点出来再点按钮。

## 致谢

- [wmpfdebugger](https://github.com/evi0s/wmpfdebugger)：WMPF 调试代理，本项目在其基础上加了环境变量擦除
- [Frida](https://frida.re/)：运行时注入

## 声明

本项目仅供个人学习与数据分析使用，与美团、小象超市及其关联公司没有任何关系，未获其授权或认可。

抓取范围仅限本人账号在小程序中正常浏览到的公开商品信息（名称、价格、规格、标签、图片），不涉及账号凭证、支付信息、他人数据，也不调用任何非公开接口。

用途是把菜价数据交给 AI 分析，用于自己买菜时的选品、比价和配菜谱，不对外提供数据服务，不做二次分发，不用于商业目的。

如果本项目的内容、截图或抓取方式侵犯了你的权益，请在仓库开一个 [Issue](https://github.com/Horizen5/xiaoxiang-capture/issues) 说明情况，我确认后会立即删除相关内容并停止更新。

使用前请自行判断是否符合目标平台的服务条款和当地法律法规，因使用本项目产生的后果由使用者自行承担。

## License

[MIT](LICENSE)。`tools/wmpfdebugger/` 沿用原项目的 GPL-2.0。
