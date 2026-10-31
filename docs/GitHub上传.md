# 上传到 GitHub（交给另一个 AI 执行）

这份文档是**写给 AI 助手看的操作手册**。人只需要把下面「一、任务说明」整段
复制给 AI，并把三个占位符填好。

---

## 一、任务说明（直接复制给 AI）

> 请把我本地这个文件夹上传到我的 GitHub 仓库，全程用命令行完成。
>
> **本地路径**：`C:\Users\Administrator\WorkBuddy\2026-10-17-17-27-18\publish\xiaoxiang-capture`
> **GitHub 用户名**：`<你的用户名>`
> **仓库名**：`<仓库名>`（如果还没建，请用 `git` 推送方式创建，或提示我先在网页上建一个空仓库）
> **Token**：我另外给你，**不要写进任何文件、不要提交、不要留在 `.git/config` 里**
>
> **要求**：
> 1. 不要修改任何代码文件，只做 git 操作
> 2. 不要提交 `live_raw/`、`live_imgs/`、`node_modules/`、`*.log`、`*.pid` —— 仓库里已有 `.gitignore`，请按它执行，不要用 `git add -f` 强加
> 3. 提交前先跑 `git status` 和 `git count-objects -vH`，把要提交的文件数量和总体积报给我确认
> 4. 默认分支用 `main`
> 5. 推送完成后，把远端 URL 里的 token 去掉，并确认工作区干净

---

## 二、标准流程

```bash
cd "C:/Users/Administrator/WorkBuddy/2026-10-17-17-27-18/publish/xiaoxiang-capture"

# 1) 初始化
git init -b main

# 2) 提交前先看一眼（应该只有 20 来个文件，几百 KB）
git add -A
git status --short
git count-objects -vH

# 3) 提交
git commit -m "feat: 小象超市小程序抓包与实时看板

- start_beta.py 统一守护调试代理/抓包/看板三个进程
- live_capture.py 走 CDP 旁路记录接口响应，落 JSON + 图片
- monitor_server.py 复刻小程序分类页布局的本地实时看板
- 文档：架构、数据说明、部署与排错"

# 4) 关联远端（用 token 认证）
git remote add origin https://<TOKEN>@github.com/<USER>/<REPO>.git

# 5) 推送
git push -u origin main

# 6) ★ 立刻把 token 从 remote URL 里摘掉
git remote set-url origin https://github.com/<USER>/<REPO>.git
git remote -v          # 确认已经不含 token
```

### 如果远端仓库不是空的

（比如建仓库时勾了「Add a README」）

```bash
git pull --rebase origin main     # 会要求身份，此时用 token 当密码
git push -u origin main
```

rebase 有冲突时，别硬来，先报给用户看冲突文件。

---

## 三、Token 的用法

GitHub 的 Personal Access Token（`ghp_...` 或 `github_pat_...`）**当密码用**：

- 用户名随便填（`x-access-token` 或你的 GitHub 用户名都行）
- 密码位置填 token

推荐写法（一次性的，不落盘）：

```bash
# push 时才带上，命令结束就不存在了
git -c credential.helper= push https://<USER>:<TOKEN>@github.com/<USER>/<REPO>.git main
```

**不要**做这几件事：

- ❌ 把 token 写进 `.md` / `.py` / `.env` 然后提交
- ❌ `git remote add origin` 里带 token 之后就不管了（会永久留在 `.git/config`）
- ❌ `git config --global credential.helper store`（明文存到 `~/.git-credentials`）

### 需要的权限

| 场景 | Token 类型 | 权限 |
|---|---|---|
| 推送到**已有**仓库 | Classic | `repo` |
| 推送并**创建**新仓库 | Classic | `repo` + `workflow`（若要 Actions） |
| 用 Fine-grained token | Fine-grained | 选中该仓库，Repository permissions → **Contents: Read and write** |

权限不够会报 `403 Permission denied`，不是 token 写错了。

---

## 四、常见报错对照

| 报错 | 原因 | 处理 |
|---|---|---|
| `remote: Support for password authentication was removed` | 用了账号密码而非 token | 换成 token |
| `403 Permission denied` | token 没 `repo` / Contents 权限，或没勾到该仓库 | 重新签发 token |
| `404 Not Found` | 仓库不存在，或 token 无权访问（私有库会伪装成 404） | 确认仓库名和权限 |
| `remote origin already exists` | 之前加过 | `git remote set-url origin <新地址>` |
| `failed to push some refs / non-fast-forward` | 远端有你本地没有的提交 | `git pull --rebase origin main` 再推 |
| `file is 105.00 MB; exceeds GitHub's limit` | 单文件超 100MB | 本包最大 819KB，正常不会遇到；真遇到说明误加了数据目录，用 `git rm --cached` 撤掉 |
| `LF will be replaced by CRLF` | 换行警告 | 无害。仓库已有 `.gitattributes` 处理 |
| `fatal: not a git repository` | 不在目标目录 | 检查 `cd` 的路径 |

---

## 五、推送完成后的验收

```bash
# 远端分支和本地一致
git log --oneline -3
git status                  # 应该是 clean
git remote -v               # 不应出现 token
```

再确认这三件事：

1. 网页上看得到 `README.md` 正常渲染（中文不乱码）
2. 仓库里**没有** `live_raw/`、`live_imgs/`、`node_modules/`
3. `tools/` 下是 `.py` 文件，`docs/` 下是 4 份文档，`samples/` 下是 3 个样本

---

## 六、这个包会被提交什么（预览）

```
.gitattributes
.gitignore
README.md
start beta.bat
start_beta.bat
docs/架构.md
docs/数据说明.md
docs/部署与排错.md
docs/GitHub上传.md
samples/_current.json
samples/c1003_f0_t0_o0.json
samples/category_tree_sample.json
tools/requirements.txt
tools/_open_mini.py
tools/_pidutil.py
tools/_win_shot.py
tools/live_capture.py
tools/make_image_index.py
tools/monitor_server.py
tools/start_beta.py
tools/wmpfdebugger/  (源码，约 20 个文件；node_modules 已被忽略)
```

合计 **101 个文件 / 约 197 KB**。

> 文件数看着多，主要是 `tools/wmpfdebugger/frida/config/` 下的 70 多个
> `addresses.*.json`（各微信版本的偏移表），是调试代理运行必需的，不要删。
