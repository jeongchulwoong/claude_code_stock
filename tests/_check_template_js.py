"""Extract inline <script> blocks from Jinja templates and run `node --check` on each.

Skips:
  - <script src="..."> external imports (we don't want to download CDN files)
  - Empty / whitespace-only blocks

Substitutes Jinja directives with safe JS placeholders so node sees valid syntax:
  {{ ... }}  -> null
  {% ... %}  -> /* jinja-stmt */
  {# ... #}  -> /* jinja-comment */
"""
from __future__ import annotations

import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEMPLATES = (
    ROOT / "dashboard" / "templates" / "client_dashboard.html",
    ROOT / "dashboard" / "templates" / "advanced_dashboard.html",
)

SCRIPT_RE = re.compile(
    r'<script(?P<attrs>[^>]*)>(?P<body>.*?)</script>',
    re.IGNORECASE | re.DOTALL,
)
SRC_RE = re.compile(r'\bsrc\s*=\s*["\']', re.IGNORECASE)

JINJA_EXPR  = re.compile(r"\{\{.*?\}\}", re.DOTALL)
JINJA_STMT  = re.compile(r"\{%.*?%\}",  re.DOTALL)
JINJA_CMT   = re.compile(r"\{#.*?#\}",  re.DOTALL)


def _scrub_jinja(js: str) -> str:
    js = JINJA_CMT.sub("/* jinja-comment */", js)
    js = JINJA_STMT.sub("/* jinja-stmt */", js)
    js = JINJA_EXPR.sub("null", js)
    return js


def main() -> int:
    failed = 0
    total  = 0
    for tpl in TEMPLATES:
        html = tpl.read_text(encoding="utf-8")
        for i, m in enumerate(SCRIPT_RE.finditer(html), start=1):
            attrs = m.group("attrs")
            body  = m.group("body")
            if SRC_RE.search(attrs):
                # external: skip
                continue
            if not body.strip():
                continue
            total += 1
            scrubbed = _scrub_jinja(body)
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=f".{tpl.stem}.{i}.mjs",
                delete=False, encoding="utf-8",
            ) as f:
                f.write(scrubbed)
                tmp_path = f.name
            try:
                proc = subprocess.run(
                    ["node", "--check", tmp_path],
                    capture_output=True, text=True, timeout=20,
                )
                if proc.returncode != 0:
                    failed += 1
                    print(f"[FAIL] {tpl.name}#script{i}")
                    print(proc.stderr or proc.stdout)
                else:
                    print(f"[ ok ] {tpl.name}#script{i}")
            finally:
                try:
                    Path(tmp_path).unlink()
                except OSError:
                    pass
    print()
    print(f"summary: {total - failed}/{total} scripts pass node --check")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
