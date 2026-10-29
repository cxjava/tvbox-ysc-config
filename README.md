# TVBox / 影视仓配置聚合与检测

定时下载公开配置，逐层检测多仓子地址，剔除失败项，将 **整个 `output/` 发布到 GitHub Pages**。发布方式与 `lx-merged-repo` 一致，使用官方 Pages Actions，不再把自动生成结果提交回 `main`。

## 使用地址

- **检测报告首页**：https://cxjava.github.io/tvbox-ysc-config/
- **多仓订阅（推荐）**：https://cxjava.github.io/tvbox-ysc-config/多仓订阅.json
- **单仓聚合**：https://cxjava.github.io/tvbox-ysc-config/单仓聚合.json
- **完整检测记录**：https://cxjava.github.io/tvbox-ysc-config/status.json

将订阅地址填入 TVBox、影视仓或 OK影视的配置设置。首页展示本轮可用源、有效单仓、失败检查项、聚合站点数量，以及逐层子仓和 UA 请求结果，也提供全部 JSON 产物链接。

首次部署：在仓库 **Settings → Pages → Build and deployment → Source** 选择 **GitHub Actions**，然后运行 **Actions → Auto Update TVBox Configs → Run workflow**。

Fork 后地址自动使用自己的仓库；自定义域名使用 `actions/configure-pages` 返回的实际基址。Pages 路径直接对应 `output/` 内的文件，不带 `/output/` 前缀。

## 检测机制

每个源按主地址、备用地址顺序检测。每个地址依次尝试：

1. `okhttp/3.12.11`
2. Android 14 / Chrome 手机 UA
3. iPhone / Safari 手机 UA

请求成功且返回非空 TVBox 配置后停止切换 UA。403、超时、HTML 挑战页、无法解析或空配置都会换下一个 UA；三个全部失败，记录为**当前检测环境不可用**，再尝试备用地址。网络或地区限制也可能造成失败，不能仅据此认定网站永久失效。

- 同时兼容 `urls` / `订阅` 多仓列表，递归检测所有子地址，支持相对子地址、循环引用检测和最多 10 层嵌套。
- 多仓只保留通过本轮检测的子项，并将链接改为 Pages 上的已过滤镜像，避免客户端再次拉到未过滤列表。
- 相同地址在同一轮复用网络检测结果；不同配置源最多 6 路并发，子仓按序检查。
- 子仓失败即剔除，多仓没有任何有效子仓也判为失败。**不把旧缓存算作本轮成功。**
- 单仓聚合包含有效子仓里的站点，为不同源保留各自的插件 JAR；镜像时转换显式相对资源地址。
- `status.json` 记录原始地址、重定向后的地址、每次 UA、HTTP 状态、耗时、错误和子仓结果。
- **检测范围为配置可下载、可解析，不包含插件执行、站内搜索或视频播放验证。**

## 自动发布

工作流每天 UTC 02:00、14:00（北京时间 10:00、22:00）运行，也支持手动触发。修改脚本、配置、测试、依赖或工作流后推送 `main` 同样触发。

流程：回归测试 → 配置 Pages → 清空 CI 内的旧 output → 检测并生成最新产物 → 上传整个 output → 部署 Pages。

只要一个源通过就发布本轮有效结果。全部源失败时生成诊断报告、工作流返回失败，**保留线上上次部署**。本轮产物可从 Actions 的 `tvbox-test-results` artifact 下载排查。部署权限仅使用内置 `GITHUB_TOKEN` 的 `pages: write` 和 `id-token: write`，无需额外密钥。

## 自定义配置源

编辑 `config/sources.json`：

```json
{
  "id": "mysource",
  "name": "我的源",
  "urls": ["https://example.com/tv.json", "https://backup.example.com/tv.json"]
}
```

`id` 使用英文字母、数字、下划线或连字符，决定输出文件名；`urls` 按优先级排列。已包含 `https://xmbjm.fh4u.org/dc.txt?i=1`，其下的子地址也会参与检测。

## 本地运行

需要 Python 3.11+：

```bash
pip install -r requirements.txt
python -m unittest discover -s tests -v
REPO=cxjava/tvbox-ysc-config python scripts/update.py
python -m http.server 8000 --directory output
```

打开 `http://localhost:8000` 预览报告。本地指定部署域名可设置 `PAGES_BASE_URL=https://example.com`。本地重新生成会更新产物清单管理的文件，移除本轮失效的旧文件；生成结果不会自动提交或上传。

## 产物

```text
output/
├── index.html          # 检测报告首页、主要订阅、全部 JSON 下载
├── REPORT.md           # Actions 摘要
├── 多仓订阅.json         # 通过检测的配置源
├── 单仓聚合.json         # 含有效子仓站点的聚合
├── <id>.json           # 各源已过滤镜像
├── children/*.json     # 有效子仓镜像
├── status.json         # 本轮检测详细记录
├── shield.json         # shields.io 徽章数据
└── manifest.json       # 本轮产物清单
```

仓库内历史 `output` 仅为旧快照，最新结果以 Pages 为准，CI 每次都从干净的产物目录生成。

## 免责声明

配置来自互联网公开分享，版权归原作者所有。本项目仅供学习、测试与个人使用，请勿商用；若涉及侵权，请联系删除相应内容。
