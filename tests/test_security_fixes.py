"""Regression tests for two confirmed security defects (see SECURITY_AUDIT.md SEC-01, SEC-02)."""
import os
import unittest
from unittest import mock

import sys

import httpx

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _isolate  # noqa: F401,E402
from core.downloader import Downloader, _classify
from core.security import DowngradeRefused, refuse_https_downgrade


class HttpsDowngrade(unittest.TestCase):
    """SEC-01: the plain-HTTP hop of an HTTPS redirect must never be sent (it carries the URL/token in clear text)."""

    @staticmethod
    def client_with_transport(url):
        reached = []

        def handler(request):
            reached.append(str(request.url))
            if request.url.scheme == "https":
                return httpx.Response(302, headers={"Location": "http://other.example/secret?token=SIGNED"})
            return httpx.Response(200, content=b"abc")

        no_proxy = {k: v for k, v in os.environ.items() if "proxy" not in k.lower()}     # env proxies would bypass the mock transport
        with mock.patch.dict(os.environ, no_proxy, clear=True):
            client = Downloader(url, "x.bin")._client(None)
        client._transport = httpx.MockTransport(handler)
        return client, reached

    def test_https_redirect_to_http_is_blocked_before_sending(self):
        client, reached = self.client_with_transport("https://start.example/f?token=SIGNED")
        with client, self.assertRaises(DowngradeRefused):
            client.get("https://start.example/f?token=SIGNED")
        self.assertEqual(reached, ["https://start.example/f?token=SIGNED"], "no request may reach the http:// target")

    def test_downgrade_error_is_not_retried(self):
        self.assertEqual(_classify(DowngradeRefused("x")), "invalid")

    def test_plain_http_start_and_https_to_https_are_untouched(self):
        for start, target, blocked in (("http://a/x", "http://b/x", False), ("https://a/x", "https://b/x", False), ("http://a/x", "https://b/x", False), ("https://a/x", "http://b/x", True)):
            with self.subTest(start=start, target=target):
                hook = refuse_https_downgrade(start)
                req = httpx.Request("GET", target)
                if blocked:
                    with self.assertRaises(DowngradeRefused):
                        hook(req)
                else:
                    hook(req)


class DefenderScanCommand(unittest.TestCase):
    """SEC-02: powershell -Command joins its arguments into one script, so the file path must not be part of it."""

    def test_path_never_enters_the_script_text(self):
        from tests.test_nexus import get_window
        w = get_window()
        evil = r"C:\Users\x\Downloads\a;calc.exe;$(calc).txt"
        w._defender_proc = None
        w._defender_queue = [evil]
        with mock.patch("gui.main_window.subprocess.Popen") as popen:
            w._pump_defender_queue()
        args, kwargs = popen.call_args
        self.assertNotIn(evil, " ".join(args[0]), "path must not be inside the PowerShell command line")
        self.assertEqual(kwargs["env"]["NEXUS_SCAN_PATH"], evil)
        self.assertIn("$env:NEXUS_SCAN_PATH", args[0][-1])
        w._defender_proc = None


if __name__ == "__main__":
    unittest.main()
