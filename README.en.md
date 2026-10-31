# Xiaoxiang Market Capture

Reads the API responses behind the category pages of the WeChat mini program "Xiaoxiang Market" (小象超市), stores them as local JSON, downloads the product images, and serves a local dashboard showing capture progress.

> [简体中文](README.md) | **English**

- Only reads responses the mini program already received; no replay, no modified parameters
- Dashboard listens on `127.0.0.1:8899` only
- Works with manual scrolling, or lets the script page through at a fixed pace

![platform](https://img.shields.io/badge/platform-Windows%2010%20%7C%2011-0078D4)
![python](https://img.shields.io/badge/python-3.10%2B-3776AB)
![node](https://img.shields.io/badge/node-18%2B-339933)
![protocol](https://img.shields.io/badge/protocol-CDP-4285F4)
![license](https://img.shields.io/badge/license-MIT-success)

![dashboard](docs/screenshots/dashboard.png)

## Requirements

| | |
| --- | --- |
| OS | Windows 10 / 11 |
| Python | 3.10+ |
| Node.js | 18+ |
| WeChat for Windows | 4.1.15.56 (other builds need an updated offset table) |

## Install

```bat
git clone https://github.com/Horizen5/xiaoxiang-capture.git
cd xiaoxiang-capture

pip install -r tools\requirements.txt
cd tools\wmpfdebugger && npm install && cd ..\..
```

## Usage

1. Close the mini program completely (close the window, don't just navigate back)
2. Double-click `start beta.bat` and wait for "调试代理已就绪" (debug proxy ready)
3. Open the mini program and go to its category page
4. Open <http://127.0.0.1:8899/> in a browser
5. Scroll through the pages; the counters on the dashboard update as you go

The mini program must be started after the launcher. The CDP session is attached when the mini program process starts, so one that is already running will not be picked up.

The "启动小程序" (launch) button in the dashboard header automates step 3: it brings an existing window to the front, or opens it through the WeChat search shortcut. Measured at 2.8 seconds.

## Data

Everything is written under `tools/` at runtime and is not committed:

```
tools/live_raw/
├── c1003_f0_t0_o0.json      # level2 1003, level1 0, level3 0, offset 0
├── _meta.jsonl              # one line per page: time, category, count, code
├── _category_tree.json      # category tree with declared counts
├── _current.json            # which category the mini program is on
└── _risk.json               # verification prompts seen

tools/live_imgs/             # product images, original CDN filenames
```

Product fields live under `data.contents[0].subModule.contents[0].showContents...singleDataInfo`: name, price, spec, tags and SKU id. `samples/` contains one real page and a trimmed category tree so you can see the format without running anything. Field-by-field notes are in [docs/数据说明.md](docs/数据说明.md) (Chinese).

`make_image_index.py` writes `image_index.csv` mapping SKU to product name and filename. With `--link` it also creates hard links named after the products, which costs no extra disk space.

## Layout

```
xiaoxiang-capture/
├── start beta.bat           # entry point
├── tools/
│   ├── start_beta.py        # launcher, supervises the three processes
│   ├── live_capture.py      # capture, reads responses over CDP
│   ├── monitor_server.py    # dashboard server
│   ├── make_image_index.py  # image index generator
│   ├── _open_mini.py        # opens the mini program
│   ├── _pidutil.py          # pid file handling
│   └── wmpfdebugger/        # debug proxy, third-party
├── docs/
└── samples/
```

How the three processes connect:

```
mini program ──CDP(62000)── wmpfdebugger ──▶ live_capture.py ──▶ live_raw / live_imgs
                                                                      │
                                                      monitor_server.py(8899) ──▶ dashboard
```

`start_beta.py` starts and restarts them. It looks for node in a few known locations and prints a message if none is found, rather than retrying forever.

## Implementation notes

**CDP instead of a proxy.** The mini program talks over WMPF's own channel, so a normal HTTP proxy sees nothing readable. CDP connects to the renderer and hooks `wx.request`, which hands over structured objects directly. No decryption, no certificates.

**PID files store three fields.** Windows reuses PIDs. After a process exits, its number can be taken by something else. Checking only "is this PID alive" makes the launcher believe another copy is still running, and a cleanup pass can kill the wrong process. The file records `pid:image name:creation time`, and all three must match.

**Pacing.** Passive capture needs no rate limit. When the script pages on its own it waits 2.1 seconds per page. That number came from measurement: 613 requests, none returned a non-zero code, fastest sustained rate 1.67 seconds, leaving 20% headroom. It also calls exactly one paging method at a time; calling several at once drops the mini program to 0.5 seconds per page, which is when the verification slider shows up.

**Dashboard counts.** Declared counts come from `skuCount` in `_category_tree.json`. Incomplete categories render as `captured-declared` in amber; complete ones show just the actual count. Reaching the end of the list counts as complete, since some categories declare 31 items but only return 24.

## Known issues

- WeChat for Windows has no external deep link for mini programs. `weixin://launchapplet` exists in `Weixin.dll` but only works when called from inside the WeChat process. `weixin://dl/business/?appid=` needs a token issued by the server and otherwise lands on a blank page. The fallback is UI automation, or the `.lnk` WeChat creates for "add to desktop".
- The debug flag is per process. Once injected, `RemoteDebugMode` stays set until WeChat restarts, so a clean state requires fully quitting WeChat.
- Offsets are tied to a specific WeChat build and may break after an update.

## FAQ

**Does capturing trigger the anti-bot check?**

Passive capture does not. Scripted paging runs at 2.1 seconds per page and has not triggered it.

**Why are images named with numbers?**

They keep their original CDN filenames. 312 product names contain characters such as `*` and `?` that cannot be used in Windows filenames.

**Why does antivirus flag it?**

The debug proxy injects code into the WeChat process, which looks like malware behaviour. The full source is here if you want to check.

## Credits

- [wmpfdebugger](https://github.com/evi0s/wmpfdebugger): WMPF debug proxy; this project adds environment scrubbing on top
- [Frida](https://frida.re/): runtime injection

## Disclaimer

For personal study and data analysis. It captures product information that your own account can already see in the app. Do not use it for commercial scraping or to put load on the service.

## License

[MIT](LICENSE). `tools/wmpfdebugger/` remains under its original GPL-2.0.
