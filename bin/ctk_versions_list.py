#!/usr/bin/env python3

import re
import sys
from pathlib import Path
from urllib.request import urlopen

REDIST_URL = "https://developer.download.nvidia.com/compute/cuda/redist/"


def read_source():
    # priority: stdin → file argument → fetch live
    if not sys.stdin.isatty():
        return sys.stdin.read()
    if len(sys.argv) == 2:
        return Path(sys.argv[1]).read_text(encoding="utf-8", errors="replace")
    with urlopen(REDIST_URL) as r:
        return r.read().decode("utf-8", errors="replace")


def main():
    html = read_source()

    # match filename + date, across the <li>…</li> block
    pattern = re.compile(
        r"redistrib_([0-9.]+)\.json.*?<span class='date'>([^<]+)</span>",
        re.DOTALL,
    )

    results = pattern.findall(html)

    for version, date in results:
        print(f"{version} — {date}")


if __name__ == "__main__":
    main()
