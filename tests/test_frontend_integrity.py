from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
STATIC_DIR = PROJECT_ROOT / "src" / "nob" / "static"


def test_adr012_zero_external_cdns_and_zero_xss_sinks():
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    assert "v-html" not in html
    assert "innerHTML" not in html
    assert "document.write" not in html
    assert "eval(" not in html

    # No external http(s) script/link/img URLs (only standard SVG xmlns allowed)
    urls = re.findall(r"https?://[^\s\"'<>)]+", html)
    non_svg_urls = [u for u in urls if not u.startswith("http://www.w3.org/2000/svg")]
    assert non_svg_urls == [], f"Unexpected external URLs in index.html: {non_svg_urls}"


def test_all_i18n_keys_used_in_index_exist_in_en_and_fa():
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    i18n_js = (STATIC_DIR / "i18n.js").read_text(encoding="utf-8")

    # Split EN and FA sections in i18n.js
    fa_marker = i18n_js.find("\n  fa: {")
    assert fa_marker > 0
    en_block = i18n_js[:fa_marker]
    fa_block = i18n_js[fa_marker:]

    en_keys = set(re.findall(r"^\s+([A-Za-z0-9_]+)\s*:", en_block, re.M))
    fa_keys = set(re.findall(r"^\s+([A-Za-z0-9_]+)\s*:", fa_block, re.M))

    used_keys = set(re.findall(r"\bt\(\s*['\"]([A-Za-z0-9_]+)['\"]", html))
    missing_en = sorted(used_keys - en_keys)
    missing_fa = sorted(used_keys - fa_keys)
    assert missing_en == [], f"Missing EN i18n keys: {missing_en}"
    assert missing_fa == [], f"Missing FA i18n keys: {missing_fa}"


def test_local_es_module_imports_resolve_and_pass_node_syntax_check():
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    imports = re.findall(r"from\s+['\"](/static/[^'\"]+)['\"]", html)
    assert len(imports) >= 6
    for imp in imports:
        clean_path = imp.split("?", 1)[0].removeprefix("/static/")
        target = STATIC_DIR / clean_path
        assert target.is_file(), f"Missing ES module on disk: {target}"

    node_bin = shutil.which("node")
    if node_bin:
        js_files = [STATIC_DIR / "i18n.js"] + sorted((STATIC_DIR / "js").glob("*.js"))
        for jsf in js_files:
            res = subprocess.run([node_bin, "--check", str(jsf)], capture_output=True, text=True)
            assert res.returncode == 0, f"JS syntax error in {jsf}: {res.stderr}"

        m = re.search(r"<script type=\"module\">(.*?)</script>", html, re.S)
        assert m is not None
        with tempfile.NamedTemporaryFile(suffix=".mjs", mode="w", encoding="utf-8") as tf:
            tf.write(m.group(1))
            tf.flush()
            res = subprocess.run([node_bin, "--check", tf.name], capture_output=True, text=True)
            assert res.returncode == 0, f"index.html module script syntax error: {res.stderr}"
