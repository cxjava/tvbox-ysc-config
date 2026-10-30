"""从本轮检测报告生成静态 Pages 首页与 Markdown 摘要。"""
import html
import urllib.parse


def make_pages(output, status):
    esc = lambda value: html.escape(str(value), quote=True)
    summary = status["summary"]
    base = status["base_url"]
    artifacts = sorted(p.relative_to(output).as_posix() for p in output.rglob("*.json"))

    def link(filename):
        return base + "/" + urllib.parse.quote(filename, safe="/")

    def report_rows(report, depth=0):
        attempts = report.get("attempts", [])
        detail = "；".join(f"{a['ua']}: " + ("通过" if a["ok"] else a.get("error", "失败")) for a in attempts)
        if report.get("error"):
            detail += "；" + report["error"]
        # 原始地址来自第三方配置，仅允许 HTTP(S) 链接。
        url = str(report["url"])
        address = (f'<a href="{esc(url)}">{esc(url)}</a>'
                   if urllib.parse.urlsplit(url).scheme in ("http", "https") else esc(url))
        row = (f"<tr><td>{'↳ ' * depth}{esc(report['name'])}</td>"
               f"<td class=\"{'ok' if report['ok'] else 'fail'}\">{'通过' if report['ok'] else '剔除'}</td>"
               f"<td>{address}<details><summary>请求详情 · {esc(report.get('used_ua', '全部失败'))}</summary>{esc(detail)}</details></td></tr>")
        return row + "".join(report_rows(c, depth + 1) for c in report.get("children", []))

    rows = "".join(report_rows(r) for s in status["sources"] for r in s["checks"])
    downloads = "".join(f'<li><a href="{esc(link(f))}">{esc(f)}</a></li>' for f in artifacts)
    entries = [("多仓订阅.json", "多仓订阅（推荐）", "保留各配置源，导入后可在应用内切换。")]
    parts = status.get("single_parts", [])
    sites_per_part = status.get("sites_per_part", 500)
    entries += [(part["file"], f"单仓聚合 · 第 {index} 份（共 {len(parts)} 份）",
                 f"包含 {part['sites']} 个站点，每份最多 {sites_per_part} 个；可单独导入使用。")
                for index, part in enumerate(parts, 1)]
    entries.append(("单仓聚合.json", "单仓聚合 · 完整版", "包含全部站点，数量较多；日常使用建议选择上方分份单仓。"))
    primary = ""
    for filename, title, description in entries:
        if not (output / filename).exists():
            continue
        url = link(filename)
        primary += (f'<div class="download"><strong>{esc(title)}</strong><p class="muted">{esc(description)}</p>'
                    f'<div class="url-row"><a href="{esc(url)}">{esc(base + "/" + filename)}</a>'
                    f'<button type="button" class="copy-url" data-url="{esc(url)}" '
                    f'aria-label="复制{esc(title)}地址">复制地址</button></div></div>')
    document = f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>TVBox 配置 · 检测报告</title><style>
:root{{color-scheme:light;--ink:#172e44;--muted:#607487;--line:#dce4eb}}*{{box-sizing:border-box}}
body{{margin:0;background:#f4f7fa;color:var(--ink);font:16px/1.65 system-ui,sans-serif}}main{{max-width:1120px;margin:auto;padding:48px 24px}}
h1{{font-size:clamp(28px,5vw,44px);line-height:1.2;margin:8px 0 16px}}h2{{margin-top:36px}}a{{color:#165c9b;overflow-wrap:anywhere}}
.muted,summary{{color:var(--muted)}}.stats{{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:16px;margin:28px 0}}
.stats div,.download{{background:white;border:1px solid var(--line);border-radius:12px;padding:20px}}.stats strong{{display:block;font-size:30px}}
.download{{margin:12px 0}}.download p{{margin:6px 0 12px}}.url-row{{display:flex;align-items:center;gap:12px;flex-wrap:wrap}}.url-row a{{flex:1;min-width:0;word-break:break-all}}
.copy-url{{flex:none;min-width:96px;padding:8px 12px;border:1px solid #165c9b;border-radius:6px;background:white;color:#165c9b;font:inherit;cursor:pointer}}
.copy-url:hover{{background:#eaf0f5}}.copy-url:focus-visible{{outline:3px solid #165c9b;outline-offset:3px}}.copy-status{{min-height:1.65em;color:var(--muted)}}
@media(max-width:480px){{.url-row a{{flex-basis:100%}}}}
.table{{overflow:auto;background:white;border:1px solid var(--line);border-radius:12px}}
table{{border-collapse:collapse;width:100%;font-size:14px}}th,td{{padding:14px;text-align:left;border-bottom:1px solid var(--line);vertical-align:top}}
th{{background:#eaf0f5}}td:first-child{{min-width:160px}}td:nth-child(2){{white-space:nowrap}}.ok{{color:#14744a}}.fail{{color:#ad352e}}
details{{margin-top:6px;max-width:700px;overflow-wrap:anywhere}}summary{{cursor:pointer}}footer{{margin-top:36px;color:var(--muted)}}
</style></head><body><main><div class="muted">TVBOX / 影视仓 · 自动检测</div>
<h1>最新配置与检测报告</h1><p class="muted">更新于 {esc(status['updated_at'])} · 每日两次</p>
<div class="stats"><div><strong>{summary['sources_ok']} / {summary['sources_total']}</strong>可用配置源</div>
<div><strong>{summary['single_configs']}</strong>有效单仓</div><div><strong>{summary['checks_failed']}</strong>失败检查项</div>
<div><strong>{summary['sites']}</strong>聚合站点</div></div>
<h2>订阅入口</h2><p>复制地址到 TVBox / 影视仓的配置设置。单仓按每份最多 {sites_per_part} 个站点拆分，每份都可独立使用；中文地址便于辨认，复制按钮会复制兼容客户端的编码地址。</p>{primary}
<div class="copy-status" role="status" aria-live="polite"></div>
<p>依次使用 okhttp、Android Chrome、iPhone Safari 检测；子仓失败即从本轮产物剔除。检测范围为配置下载与结构解析，未验证站内搜索、播放或插件运行。</p>
<h2>本轮检测明细</h2><p><a href="status.json">完整 JSON 报告</a> · <a href="REPORT.md">Markdown 摘要</a> · <a href="https://github.com/{esc(status['repo'])}">项目仓库</a></p>
<div class="table"><table><thead><tr><th>配置 / 子仓</th><th>结果</th><th>原始地址与 UA 记录</th></tr></thead><tbody>{rows}</tbody></table></div>
<h2>全部产物</h2><details><summary>{len(artifacts)} 个 JSON 文件</summary><ul>{downloads}</ul></details>
<footer>公开配置聚合与检测 · 仅供学习交流，版权归原作者所有。</footer></main>
<script>
document.querySelectorAll('.copy-url').forEach(button => {{
  button.addEventListener('click', async () => {{
    const url = button.dataset.url;
    const status = document.querySelector('.copy-status');
    try {{
      try {{
        if (!navigator.clipboard || !window.isSecureContext) throw new Error('fallback');
        await navigator.clipboard.writeText(url);
      }} catch (error) {{
        const input = document.createElement('textarea');
        input.value = url;
        input.style.position = 'fixed';
        input.style.opacity = '0';
        document.body.appendChild(input);
        try {{
          input.select();
          if (!document.execCommand('copy')) throw new Error('copy failed');
        }} finally {{ input.remove(); button.focus(); }}
      }}
      button.textContent = '已复制';
      status.textContent = '地址已复制，可以粘贴到 TVBox / 影视仓。';
      clearTimeout(button.copyTimer);
      button.copyTimer = setTimeout(() => {{ button.textContent = '复制地址'; }}, 2000);
    }} catch (error) {{
      status.textContent = '复制失败，请长按或选中旁边的地址手动复制。';
    }}
  }});
}});
</script></body></html>"""
    (output / "index.html").write_text(document, encoding="utf-8")
    lines = ["# TVBox 检测摘要", "", f"更新时间：{status['updated_at']}", "",
             f"可用源 {summary['sources_ok']}/{summary['sources_total']}；有效单仓 {summary['single_configs']}；"
             f"失败检查项 {summary['checks_failed']}；聚合站点 {summary['sites']}。", "",
             f"- [报告首页]({base}/)", f"- [多仓订阅]({link('多仓订阅.json')})"]
    if (output / "单仓聚合.json").exists():
        lines.append(f"- [单仓聚合]({link('单仓聚合.json')})")
    lines += [f"- [单仓聚合第 {i} 份（{part['sites']} 个站点）]({link(part['file'])})"
              for i, part in enumerate(parts, 1)]
    lines += ["", "检测范围：配置可下载、可解析；不代表站内视频均可播放。", ""]
    lines += [f"- {s['name']}：{'通过' if s['ok'] else '失败，已剔除'}" for s in status["sources"]]
    (output / "REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
