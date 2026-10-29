#!/usr/bin/env python3
"""检测 TVBox 配置及多仓子地址，生成可直接部署到 Pages 的 output。"""
import copy
import datetime
import hashlib
import ipaddress
import json
import os
import re
import shutil
import socket
import sys
import tempfile
import threading
import time
import urllib.parse
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path

import requests

from make_pages import make_pages

ROOT = Path(__file__).resolve().parent.parent
CONFIG_FILE = ROOT / "config/sources.json"
OUTPUT_DIR = ROOT / "output"
USER_AGENTS = (
    ("okhttp", "okhttp/3.12.11"),
    ("android", "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 "
     "(KHTML, like Gecko) Chrome/131.0.0.0 Mobile Safari/537.36"),
    ("iphone", "Mozilla/5.0 (iPhone; CPU iPhone OS 18_1 like Mac OS X) "
     "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.1 Mobile/15E148 Safari/604.1"),
)
TIMEOUT = 15
MAX_DEPTH = 10


def parse_json_lenient(text):
    """容忍 BOM、字符串外的 JS 注释及拼接 JSON，保留 URL 中的 //。"""
    text = re.sub(r'"(?:\\.|[^"\\])*"|//[^\n]*|/\*[\s\S]*?\*/',
                  lambda m: m[0] if m[0].startswith('"') else " ", text.lstrip("\ufeff")).strip()
    return json.JSONDecoder(strict=False).raw_decode(text)[0]


def idn_encode(url):
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ValueError("仅支持完整的 HTTP/HTTPS 配置地址")
    host = parsed.hostname.encode("idna").decode("ascii")
    if ":" in host:
        host = f"[{host}]"
    if parsed.port:
        host += f":{parsed.port}"
    return urllib.parse.urlunsplit((parsed.scheme, host, parsed.path, parsed.query, ""))


def validate_public_url(url):
    """Prevent a public config URL or redirect from targeting private services."""
    original = urllib.parse.urlsplit(url)
    if original.username or original.password:
        raise ValueError("配置地址不允许包含用户名或密码")
    target = idn_encode(url)
    parsed = urllib.parse.urlsplit(target)
    host = parsed.hostname
    try:
        addresses = {entry[4][0] for entry in socket.getaddrinfo(
            host, parsed.port or (443 if parsed.scheme == "https" else 80), type=socket.SOCK_STREAM
        )}
    except OSError as exc:
        raise ValueError(f"域名解析失败：{exc}") from exc
    if not addresses or any(not ipaddress.ip_address(address).is_global for address in addresses):
        raise ValueError("配置地址必须解析到公网 IP")
    return target


def multi_key(data):
    return next((k for k in ("urls", "订阅") if isinstance(data.get(k), list)), None)


def is_valid_tvbox_config(data):
    if not isinstance(data, dict):
        return False
    key = multi_key(data)
    if key is not None:
        return bool(data[key])
    return any(isinstance(data.get(k), list) and any(isinstance(x, dict) and x for x in data[k])
               for k in ("sites", "lives", "spiders"))


class FetchError(ValueError):
    def __init__(self, attempts):
        self.attempts = attempts
        super().__init__("；".join(f"{a['ua']}: {a['error']}" for a in attempts))


def fetch_config(url):
    """HTTP 错误、挑战页、空配置等失败均切换到下一个 UA。"""
    target = validate_public_url(url)
    attempts = []
    for label, ua in USER_AGENTS:
        start = time.monotonic()
        attempt = {"ua": label, "user_agent": ua, "ok": False}
        try:
            current_url = target
            for redirect in range(6):
                current_url = validate_public_url(current_url)
                resp = requests.get(current_url, headers={"User-Agent": ua,
                                    "Accept": "application/json,text/plain,*/*"},
                                    timeout=TIMEOUT, allow_redirects=False)
                if resp.status_code not in (301, 302, 303, 307, 308):
                    break
                location = resp.headers.get("Location")
                if not location or redirect == 5:
                    raise ValueError("重定向次数过多或缺少 Location")
                current_url = urllib.parse.urljoin(current_url, location)
            attempt["http_status"] = resp.status_code
            resp.raise_for_status()
            if not resp.encoding or resp.encoding.lower() == "iso-8859-1":
                resp.encoding = "utf-8-sig"
            data = parse_json_lenient(resp.text)
            if not is_valid_tvbox_config(data):
                raise ValueError("不是非空 TVBox 单仓或多仓配置")
            attempt["ok"] = True
            attempt["elapsed_ms"] = round((time.monotonic() - start) * 1000)
            attempts.append(attempt)
            return data, resp.url, attempts
        except (requests.RequestException, ValueError) as exc:
            attempt["error"] = str(exc)
            attempt["elapsed_ms"] = round((time.monotonic() - start) * 1000)
            attempts.append(attempt)
    raise FetchError(attempts)


def absolutize(data, base):
    """镜像后保留相对资源的原始基址，不改写插件标识和内联 ext。"""
    data = copy.deepcopy(data)

    def resolve(value, relative_only=False):
        if not isinstance(value, str) or not value or value.startswith(("csp_", "clan://")):
            return value
        if relative_only and not value.startswith(("./", "../", "/")):
            return value
        return urllib.parse.urljoin(base, value)

    for key in ("spider", "wallpaper", "logo"):
        if key in data:
            data[key] = resolve(data[key])
    for key in ("sites", "lives", "parses"):
        for item in data.get(key, []) or []:
            if not isinstance(item, dict):
                continue
            for field in ("api", "url", "jar"):
                if field in item:
                    item[field] = resolve(item[field], relative_only=True)
            if "ext" in item and isinstance(item["ext"], str) and item["ext"].startswith(("./", "../", "/")):
                item["ext"] = resolve(item["ext"], relative_only=True)
    return data


class Checker:
    def __init__(self, base_url):
        self.base_url = base_url.rstrip("/")
        self.cache = {}
        self.lock = threading.Lock()

    def fetch(self, url):
        # 仅缓存网络获取；递归检测保留各条路径自己的循环检查。
        with self.lock:
            owner = url not in self.cache
            future = self.cache.setdefault(url, Future())
        if owner:
            try:
                future.set_result(fetch_config(url))
            except Exception as exc:
                future.set_exception(exc)
        return copy.deepcopy(future.result())

    def check(self, url, name, ancestors=()):
        report = {"name": name, "url": url, "ok": False, "attempts": [], "children": []}
        files, leaves = {}, {}
        try:
            url = idn_encode(url)
            if url in ancestors or len(ancestors) >= MAX_DEPTH:
                raise ValueError("多仓循环引用或嵌套超过 10 层")
            data, final_url, attempts = self.fetch(url)
            report.update(attempts=attempts, final_url=final_url,
                          used_ua=attempts[-1]["ua"])
            key = multi_key(data)
            if key is not None:
                accepted = []
                for item in data[key]:
                    child = item if isinstance(item, dict) else {"url": item}
                    child_url = child.get("url")
                    child_name = str(child.get("name") or child_url or "无效条目")
                    if not isinstance(child_url, str) or not child_url.strip():
                        report["children"].append({"name": child_name, "url": "", "ok": False,
                                                   "error": "缺少 url", "attempts": [], "children": []})
                        continue
                    child_url = urllib.parse.urljoin(final_url, child_url.strip())
                    result, child_files, child_leaves = self.check(child_url, child_name, ancestors + (url,))
                    report["children"].append(result)
                    if result["ok"]:
                        entry = dict(child)
                        entry.update(name=child_name, url=f"{self.base_url}/{result['file']}")
                        accepted.append(entry)
                        files.update(child_files)
                        leaves.update(child_leaves)
                if not accepted:
                    raise ValueError("多仓内没有通过检测的子仓")
                data[key] = accepted
            else:
                data = absolutize(data, final_url)
                leaves[url] = (hashlib.sha256(url.encode()).hexdigest()[:16], name, data)
            filename = "children/" + hashlib.sha256(url.encode()).hexdigest()[:24] + ".json"
            files[filename] = data
            report.update(ok=True, file=filename, kind="multi" if key is not None else "single")
        except Exception as exc:
            report["error"] = str(exc)
            if isinstance(exc, FetchError):
                report["attempts"] = exc.attempts
        return report, files, leaves


def merge_configs(fetched):
    if not fetched:
        return None
    merged = copy.deepcopy(fetched[0][2])
    merged.pop("urls", None)
    merged.pop("订阅", None)
    merged["sites"] = []
    seen = set()
    for sid, _name, data in fetched:
        for site in data.get("sites", []) or []:
            if not isinstance(site, dict):
                continue
            site = copy.deepcopy(site)
            # 每个源可能依赖不同 jar；不能全部套用第一个源的 spider。
            if site.get("type") == 3 and data.get("spider"):
                site.setdefault("jar", data["spider"])
            key = str(site.get("key") or len(merged["sites"]))
            site["key"] = f"{sid}_{key}"
            if site["key"] not in seen:
                seen.add(site["key"])
                merged["sites"].append(site)
    for field in ("lives", "parses", "doh", "rules", "flags", "exts", "ads"):
        values, identities = [], set()
        for _sid, _name, data in fetched:
            items = data.get(field, [])
            if not isinstance(items, list):
                continue
            for item in items:
                ident = json.dumps(item, ensure_ascii=False, sort_keys=True)
                if ident not in identities:
                    identities.add(ident)
                    values.append(item)
        merged[field] = values
    return merged


def pages_base(repo):
    owner, project = repo.split("/", 1)
    path = "" if project.lower() == f"{owner.lower()}.github.io" else "/" + project
    return os.environ.get("PAGES_BASE_URL", f"https://{owner}.github.io{path}").rstrip("/")


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def split_config(merged, size=100):
    """每份保留完整公共配置，站点按原顺序无遗漏地拆分。"""
    parts = {}
    sites = merged.get("sites", [])
    for offset in range(0, len(sites), size):
        part = copy.deepcopy(merged)
        part["sites"] = part["sites"][offset:offset + size]
        parts[f"单仓聚合-{offset // size + 1}.json"] = part
    return parts


def build(cfg, repo, base):
    checker = Checker(base)

    def source(src):
        sid, name = src["id"], src["name"]
        if not re.fullmatch(r"[A-Za-z0-9_-]+", sid):
            raise ValueError(f"无效源 id: {sid}")
        entry = {"id": sid, "name": name, "ok": False, "checks": []}
        for url in src.get("urls", []):
            result, files, leaves = checker.check(url, name)
            entry["checks"].append(result)
            print(f"[{'OK' if result['ok'] else 'FAIL'}] {name} <- {url}", flush=True)
            if result["ok"]:
                filename = f"{sid}.json"
                files[filename] = files[result["file"]]
                entry.update(ok=True, used_url=url, used_ua=result["used_ua"], file=filename)
                return entry, files, leaves
        entry["error"] = "所有主/备用地址均未通过本轮检测"
        return entry, {}, {}

    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(source, cfg.get("sources", [])))
    files, leaves = {}, {}
    sources, subscriptions = [], []
    for entry, artifacts, single_configs in results:
        sources.append(entry)
        files.update(artifacts)
        leaves.update(single_configs)
        if entry["ok"]:
            subscriptions.append({"name": entry["name"], "url": f"{base}/{entry['file']}"})
    files["多仓订阅.json"] = {"urls": subscriptions}
    merged = merge_configs(list(leaves.values()))
    parts = {}
    if merged:
        files["单仓聚合.json"] = merged
        parts = split_config(merged)
        files.update(parts)
    checks = []

    def collect(report):
        checks.append(report)
        for child in report.get("children", []):
            collect(child)

    for entry in sources:
        for report in entry["checks"]:
            collect(report)
    ok = len(subscriptions)
    status = {"updated_at": datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8))).isoformat(timespec="seconds"),
              "repo": repo, "base_url": base, "sources": sources,
              "single_parts": [{"file": name, "sites": len(data["sites"])}
                               for name, data in parts.items()],
              "summary": {"sources_ok": ok, "sources_total": len(sources),
                          "checks_total": len(checks), "checks_ok": sum(x["ok"] for x in checks),
                          "checks_failed": sum(not x["ok"] for x in checks),
                          "single_configs": len(leaves), "sites": len((merged or {}).get("sites", []))}}
    files["status.json"] = status
    files["shield.json"] = {"schemaVersion": 1, "label": "TVBox 配置", "message": f"{ok}/{len(sources)} 可用",
                            "color": "green" if ok == len(sources) and ok else "orange" if ok else "red"}
    return files, status


def main():
    cfg = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    repo = os.environ.get("GITHUB_REPOSITORY") or os.environ.get("REPO") or "cxjava/tvbox-ysc-config"
    files, status = build(cfg, repo, pages_base(repo))
    # 先完整生成，再更新 output，防止抓取中断留下半套产物。
    with tempfile.TemporaryDirectory(prefix="tvbox-") as temp:
        stage = Path(temp)
        for filename, data in files.items():
            write_json(stage / filename, data)
        make_pages(stage, status)
        manifest = sorted([*files, "index.html", "REPORT.md", "manifest.json"])
        write_json(stage / "manifest.json", manifest)
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        previous = OUTPUT_DIR / "manifest.json"
        old = json.loads(previous.read_text(encoding="utf-8")) if previous.exists() else []
        old += [f"{s['id']}.json" for s in cfg.get("sources", [])] + ["单仓聚合.json"]
        for filename in set(old) - set(manifest):
            target = (OUTPUT_DIR / filename).resolve()
            if target.is_relative_to(OUTPUT_DIR.resolve()) and target.is_file():
                target.unlink()
        shutil.copytree(stage, OUTPUT_DIR, dirs_exist_ok=True)
    print(json.dumps(status["summary"], ensure_ascii=False))
    return 0 if status["summary"]["sources_ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
