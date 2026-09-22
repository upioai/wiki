# upio.ai Wiki

团队多项目知识库，纯静态 HTML 站点，线上地址 **[upio.ai](https://upio.ai)**（Vercel 部署，备用域名 upioai-wiki.vercel.app）。

适合放在这里的：能让团队长期共享、反复查阅的教程、讲解、方案、复盘、报告。Lark 打不开 HTML，所以这类页面放到这里，再把链接发到群里。

> [!WARNING]
> **这是公开仓库，站点也公开可访问。** 仓里每个文件任何人都能在 GitHub 上直接翻到，不要提交凭据、token、真实金额、客户身份信息或未脱敏的聊天记录。`public/internal/` 只是不被搜索引擎收录，并不保密。

## 站点结构

| 线上路径 | 源文件 | 索引页怎么来 |
|---|---|---|
| `/` 首页 | 模板写在 `scripts/build-index.js` 里 | 构建时生成，项目卡片与精选指南分别在 `PROJECTS`、`GUIDES` 数组里手动维护 |
| `/akke` Akke 子站 | `public/akke/` | 构建时由 `scripts/build-akke-map.js` 扫描目录自动生成 |
| `/learn` 知识分享 | `public/learn/` | 构建时由 `scripts/build-learn.js` 按 `CATEGORIES` 清单生成，每个分类内按 `date` 新→旧 |
| `/timeline` 知识时间线 | 无（汇总页） | 构建时由 `scripts/build-timeline.js` 生成：只收已被首页、`/learn`、`/akke`、`/workflow`、`/softie`、`/vivi` 索引链接的页面，按月分组、默认新→旧 |
| `/vivi` | `public/vivi/` | 手写；`/vivi/characters/*` 由 `scripts/build-characters.js` 生成 |
| `/softie` | `public/softie/` | 手写 |
| `/workflow` | `public/workflow/` | 手写，新页面要自己在 `index.html` 里加卡片 |
| `/internal` | `public/internal/` | 不进任何索引；页面自带 `noindex` |
| `/partners` | `public/partners/` | 不进任何索引；页面自带 `noindex` |
| `/design-system` | `public/design-system.html` | 全站设计规范，新页面照这里取色板、字体和组件 |

`vercel.json` 开了 `cleanUrls`：`public/learn/kv-cache.html` 对应线上 `/learn/kv-cache`，站内链接一律写不带 `.html` 的路径。

## 目录

```
public/            站点根目录，所有页面和静态资源（Vercel outputDirectory）
scripts/           构建脚本 + Akke 索引的配置（akke-map.json / .dates.json / .shell.html）
data/              vivi-characters.json：Vivi 角色页的数据源，由 Vivi 仓导出，只含 SFW 字段
tools/             quote-guard 引语核验闸及其台账；privacy-guard 隐私闸及其白名单；gen-cases-manifest.mjs（一次性回填脚本）
docs/              设计文档、spec 存档
.github/workflows/ quote-guard、privacy-guard CI
```

## 新增页面

页面统一写成**自包含的单文件 HTML**（CSS/JS 内联，图片等资源放同目录）。`<head>` 里建议加一行 `<meta name="upio:date" content="YYYY-MM-DD">` 写明内容本身的日期，`/timeline` 优先用它排序；不写就退回到日期缓存（`scripts/timeline.dates.json`、`scripts/akke-map.dates.json`）。按放的位置不同，还要多做一步：

| 放在哪 | 除了放文件，还要 |
|---|---|
| `public/learn/<slug>.html` | 在 `scripts/build-learn.js` 的 `CATEGORIES` 对应分类里加一条 `{ slug, date, title, desc }`（`date` 为 `YYYY-MM-DD`，不写会排到分类末尾）。没登记的页面线上能打开，但不会出现在 `/learn` 和 `/timeline` |
| `public/akke/<name>.html` | 多半要登记分类和日期，见下方 [Akke 页面](#akke-页面) |
| `public/<name>.html`（通用指南） | 想上首页就在 `scripts/build-index.js` 的 `GUIDES` 数组里登记 |
| `public/workflow/<slug>.html` | 在 `public/workflow/index.html` 里手动加一张卡片 |
| `public/internal/<name>.html` | `<head>` 里加 `<meta name="robots" content="noindex,nofollow">`，不要在任何索引页里链接它 |

### Akke 页面

`/akke` 索引页只收这几处的页面：`public/akke/*.html`、`cases/index.html`、`prompt-atlas/index.html`、`reports/<目录>/index.html`。放在别的子目录里、或直接放在 `reports/` 下的 html 线上能打开，但不会出现在索引里。

没登记的页面按路径自动归类，按以下顺序匹配：

1. `reports/daily-*` 进存档里的日报条。
2. 路径含 `cloud-pc` / `wuying` / `second-touch` 的进「云电脑」。
3. 带日期、含 `scrap` / `pricing` / `dialogue` / `topic` 等关键词（完整列表见 `classify()`），或在 `reports/` 下的，进「报告 · 复盘存档」。
4. 其余全部落进「待归类」，构建日志里会告警。

所以大多数新页面都要在 `scripts/akke-map.json` 的 `overrides` 里登记一条：键是不带 `.html` 的路径（`reports` 目录页写成 `/akke/reports/<目录>/`，带尾斜杠）；要指定分类就写 `cat`，`title` 不写就取页面 `<title>`：

```json
"/akke/<name>": { "cat": "tech", "title": "页面标题", "desc": "卡片上的一句话描述", "label": "卡片角标" }
```

`cat` 可选值：`foundation`（入门 · 架构）、`tech`（技术方案）、`ops`（触达运营 · SOP）、`cloudpc`（云电脑 · 无影通道）、`multi`（多通道触达 · 调研）、`cases`（用户案例库）、`retro`（项目复盘 · 0→1）、`archive`（报告 · 复盘存档）。

文件名不带日期的页面，还要在 `scripts/akke-map.dates.json` 里手动加一行 `"/akke/<name>": "YYYY-MM-DD"`，填页面的创建日期。不加的话，线上每次构建都会把它当成当天新增，一直排在「最近新增」最前面。

### 不要手改的文件

**构建产物**，每次部署都会重新生成：

- `public/index.html`、`public/akke/index.html`、`public/learn/index.html`、`public/timeline/`（已在 `.gitignore`）
- `public/vivi/characters/*`、`public/sitemap.xml`、`public/robots.txt`（由 `build-characters.js` 生成，但提交在仓里）

**从 Akke 仓自动同步的文件**，提交者是 `akke-bot`。源头在 Akke 仓，要改就去那边改，在这里改会被下一次同步覆盖：

- `public/akke/model-watch-*.html`（模型监控日报）
- `public/akke/dialogue-strategy-progress-2026-05-29.html`
- `public/akke/reports/` 下的 `dialogue-logic-0to1/`、`price-logic/`、`zeroxing-dm-rescue/`、`cloudpc-tuwen-0to1/`
- `public/akke/wuying-dm/`、`wuying-tuwen/`、`wecom-chat/` 里的大部分脚本
- `public/akke/cases/manifest.json`（案例列表的数据源，也会被生成案例的流程追加写入）

> [!IMPORTANT]
> `wuying-dm/`、`wecom-chat/` 里的 `update*.bat` 会让云电脑直接从本仓 `main` 分支的 raw 地址下载这些脚本。在这里改坏、改名或删掉，会直接影响线上正在跑的云电脑。

拿不准的时候先看最后一次是谁提交的：

```bash
git log -1 --format=%an -- <文件路径>   # 输出 akke-bot 就别在这里改
```

## 本地构建与预览

不需要 `npm install`，装了 Node.js 就能跑。构建命令和 Vercel 上的 `buildCommand` 相同：

```bash
node scripts/build-index.js && node scripts/build-characters.js && node scripts/build-akke-map.js && node scripts/build-learn.js && node scripts/build-timeline.js
npx serve public          # 支持无后缀 URL；python -m http.server 不支持，会 404
```

- 构建日志里的 `WARN` / `待归类` 值得看一眼：`/learn` 清单里登记了但文件不存在的条目会被跳过，Akke 新页面没归上类也会在这里列出来。
- 本地构建会把 `public/sitemap.xml` 里的日期改成今天，这个改动不用提交：`git checkout -- public/sitemap.xml`。

## 发布

1. 推之前先同步：`git pull --rebase origin main`。工作区要先干净：该提交的 commit 掉，本地构建改出来的 `public/sitemap.xml` 先还原。`akke-bot` 几乎每天都会往 main 推几次，不同步很容易推不上去。
2. 推到 `main`。绝大多数提交是直接推 main 的，也可以走 PR。
3. Vercel 自动构建并部署。
4. 上线后用 `curl` 打一下新页面确认是 200。注意用**不带尾斜杠**的地址：`/akke/` 会先 308 跳转，curl 默认不跟随，容易误以为没部署上。

自动部署偶尔会不触发。有 Vercel 权限的话，用 `vercel ls --environment production` 看最新一次生产部署是不是晚于你的 push，没有就手动发一次：

```bash
vercel link --project upioai-wiki   # 新克隆的仓先关联项目（.vercel/ 不进仓），只需一次
vercel --prod
```

**缓存规则**（见 `vercel.json`）：HTML 每次都回源校验，改完刷新就能看到；图片、CSS、JS、字体缓存一年且标记为 immutable。**替换这类资源时请换个文件名**，否则读者的浏览器会一直用旧版本。

## CI：引语核验闸（quote-guard）

`.github/workflows/quote-guard.yml` 在 push 到 main 或提 PR、且改动涉及 `public/akke/**.html` 时运行，检查改动过的页面。

- **检查什么**：页面正文里用 `「」` 包起来、12 个字以上的句子，会被当成引语，必须已经记在 `tools/quotes.verified.json` 台账里。台账只存归一化后的哈希，不存原文（一手素材含真实姓名、报价等信息，不能进公开仓库）。
- **push 到 main 时是事后标红**，不会阻断发布；看到红了要回来处理。
- **CI 会漏检**：push 时只和上一个提交比，一次推多个提交只查到最后一个的改动；同一分支上新的运行会取消还没跑完的旧运行，你的检查可能被紧接着推送的 `akke-bot` 顶掉。所以**推之前先在本地跑一遍**：

  ```bash
  node tools/quote-guard.mjs --check public/akke/<页面>
  ```

- **旧页面大多本来就通不过**：存量页面里有大量没进台账的旧引语，`--check` 会把整页的都报出来，不只是你改的那几句。改之前先跑一次存下输出，改完再跑一次，只处理多出来的那几条。输出最多列 40 条，超出的只在首行给总数，这种页面要连首行的条数一起比。
- **多出来的每一条**，二选一：
  1. 确实是引用：先在 Akke 仓本地用 `scripts/quote-lint.ts` 对照一手素材逐字核对，对上了再回本仓执行 `VERIFY_DATE=$(date +%F) node tools/quote-guard.mjs --accept public/akke/<页面>` 记进台账，一起提交。注意 `--accept` 会把**整页所有引语**一起记账，页面里还有没核过的旧引语时不能直接对它用，否则那些也会被记成已核验。台账只认句子的哈希、不认页面，所以这时把核过的那一句单独放进一个临时文件再记账：

     ```bash
     # 临时文件用原页面名命名，台账里记的页面路径就能对上是哪一页
     echo '<p>「核过的那句原文」</p>' > /tmp/<页面名>.html
     VERIFY_DATE=$(date +%F) node tools/quote-guard.mjs --accept /tmp/<页面名>.html
     ```

  2. 其实是自己的归纳：去掉 `「」`，改用加粗或者换个说法。

## CI：隐私闸（privacy-guard）

`.github/workflows/privacy-guard.yml` 在 push 到 main 或提 PR、且改动涉及 `public/**` 时运行，只查改动过的文件。和引语核验闸一样，**push 到 main 时是事后标红**，不会阻断发布。

- **检查什么**：`public/` 下改动过的 html/json/js/md/txt/csv，抽出文本后找四类终端客户信息：
  - 完整的大陆手机号，包括 `139-1234-5678`、`139 1234 5678` 这种分段写法
  - 车牌式客户编码，如 `闽D12345`
  - 门牌级住址：`X号楼1502`、`X栋X室`、`X单元X室/号`（`5号楼162现代简约`、`16号楼130平` 这类户型面积不算）
  - 微信号 `wxid_…`

  改动文件的路径本身也查，截图文件名里带客户编码同样算泄露。中间四位是 `0000` 的示例号、紧挨着 `*` 的已打码样本自动放行。
- **输出已打码**：Actions 日志和仓库一样公开，所以命中时只打印「文件:行:列 类型 打码样本」，不打印原文，要看原文就在本地打开文件。
- **push 不会漏查**：和推送前的 main 比，一次推多个提交也都查得到；每次推送的检查互不取消，不会被紧接着推送的 `akke-bot` 顶掉。
- **推之前先在本地跑**，有命中时退出码为 1：

  ```bash
  node tools/privacy-guard.mjs                           # 相对 origin/main 的改动，含未提交、未跟踪的文件
  node tools/privacy-guard.mjs --check public/akke/<页面>  # 指定文件
  node tools/privacy-guard.mjs --all                     # 全量扫 public/
  ```

- **命中了**，先改原文，打码口径：
  - 手机号保留前 3 位和后 2 位：`139******78`
  - 住址写到小区为止，楼栋、门牌去掉
  - 客户编码改成 `闽X******`
  - 客户姓名改成角色（客户、业主、老板娘）
- **确实不是客户信息的**，比如商家对外公开的售后热线、商家在公开视频里自留的联系电话、形似手机号的 ID、户型面积，加进 `tools/privacy-guard.allow.json`：写 `value`（一组共用理由的写 `values`）和 `why`；能限定页面就写 `files`（路径前缀）；`2号楼901` 这类短串再加 `near`（同一行必须出现的字），免得把别处真实的住址一起放过去。白名单本身也是公开的，终端客户的信息不能放进去。
- **已经推上 main 的**，改原文只能让站点不再显示，原文还留在 git 历史里。
