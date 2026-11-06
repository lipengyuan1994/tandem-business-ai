"""Static evidence only: live Caddy header/framing verification belongs to the reviewer."""
import gzip
import pathlib
import re
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]


class EmbedRoutes(unittest.TestCase):
    def test_dynamic_policy_precedes_widget_rewrite_and_keeps_ordinary_csp(self):
        for name in ("Caddyfile", "Caddyfile.dev"):
            source = (ROOT / "deploy/compose" / name).read_text()
            self.assertIn("request>headers>X-Embed-Token delete", source)
            self.assertIn("resp_headers>X-Embed-Token delete", source)
            block = source.split("handle /embed/* {", 1)[1].split("handle /embed.js", 1)[0]
            self.assertIn("route {", block)
            self.assertIn("forward_auth api:8000", block)
            self.assertIn("uri /api/v1/public/embed-policy?path={http.request.uri.path}", block)
            self.assertIn("copy_headers X-Embed-CSP", block)
            self.assertLess(block.index("forward_auth"), block.index('header Content-Security-Policy "{http.request.header.X-Embed-CSP}"'))
            self.assertLess(block.index("copy_headers"), block.index("rewrite * /widget.html"))
            for header in ("Cross-Origin-Embedder-Policy", "Cross-Origin-Opener-Policy"):
                self.assertIn(f'header {header} "unsafe-none"', block)
            self.assertIn("frame-ancestors 'none'", block)
            loader = source.split("handle /embed.js", 1)[1].split("handle /api", 1)[0]
            self.assertIn('Cross-Origin-Resource-Policy "cross-origin"', loader)
            self.assertIn('Cache-Control "public, max-age=3600, must-revalidate"', loader)
            if name == "Caddyfile.dev":
                self.assertIn('header Content-Security-Policy "script-src \'self\';" "script-src \'self\' \'unsafe-inline\';"', block)
            if name == "Caddyfile":
                self.assertIn("frame-ancestors 'none';", source.split("(app_csp)")[1].split("{$DOMAIN}")[0])

    def test_loader_budget_and_version(self):
        source = (ROOT / "frontend/public/embed.js").read_bytes()
        self.assertLessEqual(len(gzip.compress(source)), 5 * 1024)
        self.assertRegex(source.decode(), r"embed v\d+\.\d+\.\d+")
        for fragment in ("prefers-reduced-motion", "aria-expanded", "event.origin !== origin", "event.source !== frame.contentWindow", "postMessage", "Escape"):
            self.assertIn(fragment, source.decode())
        self.assertNotRegex(source.decode(), re.compile(r"postMessage\([^;]*[\"']\*[\"']"))
