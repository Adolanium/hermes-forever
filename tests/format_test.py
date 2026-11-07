"""Unit tests for H.Format in addon/HermesWoW/UI.lua (Markdown -> WoW escape codes). Needs lupa.

  python tests/format_test.py
"""
from pathlib import Path

from lupa import lua51

ROOT = Path(__file__).resolve().parent.parent
lua = lua51.LuaRuntime()
lua.execute("HermesWoW = {}")
lua.execute((ROOT / "addon" / "HermesWoW" / "UI.lua").read_text("utf-8"))
fmt = lua.globals().HermesWoW.Format

CASES = [
    ("plain", "plain"),
    ("a | b", "a || b"),                                         # pipes cannot become codes
    ("|cffff0000red|r |Hitem:1|h[x]|h |Tpath:0|t", "||cffff0000red||r ||Hitem:1||h[x]||h ||Tpath:0||t"),
    ("## Repo state", "|cffffd100Repo state|r"),
    ("**Repo state**", "|cffffe8a3Repo state|r"),
    ("use `git push` now", "use |cffb4c8e6git push|r now"),
    ("odd ` backtick", "odd ` backtick"),
    ("``", "``"),
    ("- item", "• item"),
    ("  - nested **b**", "    • nested |cffffe8a3b|r"),
    ("1. first", "1. first"),
    ("> quoted **x**", "|cff8c8c8c> quoted x|r"),
    ("---", "|cff8c8c8c" + "-" * 32 + "|r"),
    ("PR: https://github.com/a/b/pull/1.", "PR: |Hhermesurl:https://github.com/a/b/pull/1|h|cff4fa3ffhttps://github.com/a/b/pull/1|r|h."),
    ("[docs](https://x.y/z)", "|Hhermesurl:https://x.y/z|h|cff4fa3ffdocs|r|h"),
    ("`https://not.a/link`", "|cffb4c8e6https://not.a/link|r"),
    ("```\ncode **x**\n\n```\nafter", "|cffb4c8e6code **x**|r\n \nafter"),
    ("x\n\ny", "x\n\ny"),
]

failed = 0
for src, want in CASES:
    got = fmt(src)
    if got != want:
        failed += 1
        print(f"FAIL {src!r}\n  want {want!r}\n  got  {got!r}")
print(f"{len(CASES) - failed}/{len(CASES)} passed")
raise SystemExit(1 if failed else 0)
