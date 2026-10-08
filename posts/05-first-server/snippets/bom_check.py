"""Post 05, section 8: a configuration file that starts with a byte order mark.

Run from the series root:
    uv run python posts/05-first-server/snippets/bom_check.py

Writes the same small host configuration three ways into a temporary directory
(UTF-8, UTF-8 with a byte order mark, and UTF-16 with one, the two marked forms
Windows PowerShell 5.1 produces), prints the first bytes of each, and parses
each the way most programs read a text file: decode as UTF-8, then parse the
string. Then rewrites the marked file without the mark, the same repair the
post's PowerShell lines make, and parses it again.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

CONFIG = '{"mcpServers": {"system-info": {"command": "uv", "args": []}}}'


def first_bytes(path: Path) -> str:
    return " ".join(f"{b:02X}" for b in path.read_bytes()[:4])


def parse_as_utf8_text(path: Path) -> str:
    try:
        json.loads(path.read_bytes().decode("utf-8"))
        return "parsed"
    except (ValueError, UnicodeDecodeError) as exc:
        return f"{type(exc).__name__}: {exc}"


with tempfile.TemporaryDirectory() as tmp:
    tmp = Path(tmp)
    files = {
        "utf-8": ("plain.json", "utf-8"),
        "utf-8 with BOM": ("bom8.json", "utf-8-sig"),
        "utf-16 with BOM": ("bom16.json", "utf-16"),
    }
    for label, (name, encoding) in files.items():
        path = tmp / name
        path.write_bytes(CONFIG.encode(encoding))
        print(f"{label:<16} first bytes {first_bytes(path)}")
        print(f"{'':<16} read as UTF-8 text: {parse_as_utf8_text(path)}")

    # Tolerance belongs to the parser, not the file: handed raw bytes, Python's
    # json sniffs the encoding first and accepts all three.
    sniffed = [json.loads((tmp / name).read_bytes()) == json.loads(CONFIG) for name, _ in files.values()]
    print(f"\njson.loads on the raw bytes instead: {sniffed}")

    repaired = tmp / "bom8.json"
    repaired.write_text(repaired.read_text(encoding="utf-8-sig"), encoding="utf-8")
    print(f"utf-8 with BOM, rewritten without the mark: first bytes {first_bytes(repaired)}; "
          f"read as UTF-8 text: {parse_as_utf8_text(repaired)}")
