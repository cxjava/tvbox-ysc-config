import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import update
from make_pages import make_pages


def response(data, status=200, url="https://source.test/config.json"):
    result = requests.Response()
    result.status_code = status
    result.url = url
    result._content = (data if isinstance(data, str) else json.dumps(data)).encode()
    return result


SINGLE = {"spider": "./plugin.jar", "sites": [{"key": "demo", "type": 3, "api": "csp_Demo"}]}


class FetchTests(unittest.TestCase):
    def test_http_and_challenge_page_fall_back_to_third_ua(self):
        with patch.object(update, "validate_public_url", side_effect=lambda url: url), patch.object(update.requests, "get", side_effect=[
            response("denied", 403), response("<html>challenge</html>"), response(SINGLE)
        ]) as get:
            data, _, attempts = update.fetch_config("https://source.test/config.json")
        self.assertEqual(data, SINGLE)
        self.assertEqual([x["ok"] for x in attempts], [False, False, True])
        self.assertEqual([c.kwargs["headers"]["User-Agent"] for c in get.call_args_list],
                         [ua for _, ua in update.USER_AGENTS])

    def test_all_failures_recorded(self):
        with patch.object(update, "validate_public_url", side_effect=lambda url: url), patch.object(update.requests, "get", side_effect=requests.Timeout("timeout")) as get:
            with self.assertRaises(update.FetchError) as error:
                update.fetch_config("https://source.test/config.json")
        self.assertEqual(get.call_count, 3)
        self.assertEqual(len(error.exception.attempts), 3)

    def test_comments_and_empty_configs(self):
        self.assertEqual(update.parse_json_lenient('\ufeff{"url":"https://a/b", // comment\n"x":1}'),
                         {"url": "https://a/b", "x": 1})
        for data in ({"sites": []}, {"flags": []}, {"urls": []}, [], {"sites": "bad"}):
            self.assertFalse(update.is_valid_tvbox_config(data))


class BuildTests(unittest.TestCase):
    def test_nested_filtering_mirrors_and_aggregate(self):
        root = "https://source.test/root.json"
        payloads = {
            root: {"urls": [{"name": "nested", "url": "nested.json"},
                             {"name": "bad", "url": "bad.json"}, {"name": "missing"}]},
            "https://source.test/nested.json": {"urls": [{"name": "leaf", "url": "leaf.json"},
                                                        {"name": "cycle", "url": root}]},
            "https://source.test/leaf.json": SINGLE,
        }

        def get(url, **kwargs):
            return response(payloads.get(url, "denied"), 200 if url in payloads else 403, url)

        cfg = {"sources": [{"id": "demo", "name": "Demo", "urls": [root]}]}
        with patch.object(update.requests, "get", side_effect=get):
            with patch.object(update, "validate_public_url", side_effect=lambda url: url):
                files, status = update.build(cfg, "owner/project", "https://owner.github.io/project")
        self.assertEqual(status["summary"]["single_configs"], 1)
        self.assertEqual(status["summary"]["checks_failed"], 3)
        self.assertEqual(len(files["demo.json"]["urls"]), 1)
        for data in files.values():
            if isinstance(data, dict):
                for child in data.get("urls", []):
                    filename = child["url"].removeprefix("https://owner.github.io/project/")
                    self.assertIn(filename, files)
        site = files["单仓聚合.json"]["sites"][0]
        self.assertEqual(site["jar"], "https://source.test/plugin.jar")
        self.assertEqual(site["api"], "csp_Demo")
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp)
            for name, data in files.items():
                update.write_json(output / name, data)
            make_pages(output, status)
            document = (output / "index.html").read_text()
            self.assertIn("本轮检测明细", document)
            self.assertIn("REPORT.md", document)
            self.assertIn('class="copy-url"', document)
            self.assertIn('data-url="https://owner.github.io/project/%E5', document)
            self.assertIn("https://owner.github.io/project/单仓聚合-1.json</a>", document)
            self.assertIn("第 1 份（共 1 份）", document)

    def test_no_old_cache_used_and_no_aggregate_when_all_fail(self):
        cfg = {"sources": [{"id": "old", "name": "Old", "urls": ["https://a.test/config"]}]}
        with patch.object(update, "validate_public_url", side_effect=lambda url: url), \
             patch.object(update.requests, "get", return_value=response("bad", 403)):
            files, status = update.build(cfg, "owner/repo", "https://owner.github.io/repo")
        self.assertEqual(status["summary"]["sources_ok"], 0)
        self.assertNotIn("old.json", files)
        self.assertNotIn("单仓聚合.json", files)
        self.assertEqual(files["多仓订阅.json"], {"urls": []})

    def test_each_spider_retained_and_inputs_not_mutated(self):
        second = {"spider": "https://b.test/b.jar", "sites": SINGLE["sites"]}
        merged = update.merge_configs([("a", "A", SINGLE), ("b", "B", second)])
        self.assertEqual([s["jar"] for s in merged["sites"]], ["./plugin.jar", "https://b.test/b.jar"])
        self.assertNotIn("jar", SINGLE["sites"][0])

    def test_parts_have_at_most_100_sites_and_preserve_every_site_and_config(self):
        for count, sizes in ((0, []), (100, [100]), (101, [100, 1]), (250, [100, 100, 50])):
            with self.subTest(count=count):
                merged = {"spider": "https://a.test/plugin.jar", "parses": [{"name": "parser"}],
                          "sites": [{"key": str(i), "jar": f"https://a.test/{i}.jar"} for i in range(count)]}
                parts = update.split_config(merged, size=100)
                self.assertEqual([len(p["sites"]) for p in parts.values()], sizes)
                self.assertEqual([site for part in parts.values() for site in part["sites"]], merged["sites"])
                for part in parts.values():
                    self.assertEqual(part["parses"], merged["parses"])
                    self.assertEqual(part["spider"], merged["spider"])
                    self.assertIsNot(part["parses"], merged["parses"])

    def test_default_part_size_is_500_and_invalid_sizes_are_rejected(self):
        parts = update.split_config({"sites": [{"key": str(i)} for i in range(1001)]})
        self.assertEqual([len(p["sites"]) for p in parts.values()], [500, 500, 1])
        for size in (0, -1, "abc", 1.5, True):
            with self.subTest(size=size), self.assertRaises(ValueError):
                update.split_config({"sites": []}, size)
        with self.assertRaises(ValueError):
            update.build({}, "owner/repo", "https://example.com", sites_per_part=0)

    def test_pages_base_supports_user_site_and_custom_domain(self):
        with patch.dict(update.os.environ, {}, clear=True):
            self.assertEqual(update.pages_base("owner/owner.github.io"), "https://owner.github.io")
            self.assertEqual(update.pages_base("owner/repo"), "https://owner.github.io/repo")
        with patch.dict(update.os.environ, {"PAGES_BASE_URL": "https://custom.test/"}):
            self.assertEqual(update.pages_base("owner/repo"), "https://custom.test")

    def test_public_url_blocks_private_hosts_and_credentials(self):
        with patch.object(update.socket, "getaddrinfo", return_value=[(None, None, None, None, ("127.0.0.1", 80))]):
            with self.assertRaisesRegex(ValueError, "公网 IP"):
                update.validate_public_url("http://localhost/config.json")
        with self.assertRaisesRegex(ValueError, "用户名或密码"):
            update.validate_public_url("https://user:pass@example.com/config.json")


if __name__ == "__main__":
    unittest.main()
