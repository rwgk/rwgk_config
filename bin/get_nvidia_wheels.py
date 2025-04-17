#!/usr/bin/env python3

"""Download and extract NVIDIA cuXX wheels from PyPI

Run this in an empty directory, e.g.

mkdir cu12_wheels
cd cu12_wheels
get_nvidia_wheels.py 12

This will create:
    get_nvidia_wheels_log.txt
    get_nvidia_wheels_pkgs.json
    downloads/*.whl
    unzip_l/*.txt

If interrupted, simply running the same command again will pick up downloading
in the middle.
"""

import os
import sys
import re
import json
import zipfile
import io
import requests
import argparse
from datetime import datetime

LOG_FILE = "get_nvidia_wheels_log.txt"
PKGS_JSON = "get_nvidia_wheels_pkgs.json"
DOWNLOAD_DIR = "downloads"
UNZIP_L_DIR = "unzip_l"


# Logger that prints to stdout and a file
class TeeLogger:
    def __init__(self, filename):
        self.terminal = sys.stdout
        self.logfile = open(filename, "a", encoding="utf-8")
        self.logfile.write(f"\n\n--- Script started at {datetime.now()} ---\n")

    def write(self, message):
        self.terminal.write(message)
        self.logfile.write(message)

    def flush(self):
        self.terminal.flush()
        self.logfile.flush()


sys.stdout = TeeLogger(LOG_FILE)


def fetch_cu_wheel_metadata(cuda_version):
    print(f"Fetching package index from PyPI for cu{cuda_version} ...")
    simple_index_url = "https://pypi.org/simple/"
    resp = requests.get(simple_index_url)
    resp.raise_for_status()
    package_names = re.findall(r'<a href="[^"]+">([^<]+)</a>', resp.text)
    pattern = re.compile(rf"^nvidia-[a-z0-9\-]+-cu{cuda_version}$", re.IGNORECASE)
    cu_pkgs = sorted([name for name in package_names if pattern.match(name)])

    results = []

    for pkg in cu_pkgs:
        print(f"Querying {pkg} ...")
        try:
            data = requests.get(f"https://pypi.org/pypi/{pkg}/json").json()
            latest_ver = data["info"]["version"]
            files = [
                (f["filename"], f["url"])
                for f in data["releases"].get(latest_ver, [])
                if f["filename"].endswith(".whl")
            ]
            results.append((pkg, latest_ver, files))
        except Exception as e:
            print(f"  Failed to fetch {pkg}: {e}")
            continue

    return results


def download_and_extract_listing(filename, url):
    dest_path = os.path.join(DOWNLOAD_DIR, filename)
    listing_path = os.path.join(UNZIP_L_DIR, filename.replace(".whl", ".txt"))

    if os.path.exists(listing_path):
        print(f"Skipping (already extracted): {filename}")
        return

    print(f"Downloading: {filename}")
    with requests.get(url, stream=True) as r:
        r.raise_for_status()
        data = r.content
        with open(dest_path, "wb") as f:
            f.write(data)

    print(f"Extracting listing to: {listing_path}")
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        with open(listing_path, "w", encoding="utf-8") as out:
            for info in zf.infolist():
                out.write(f"{info.file_size:>10} {info.filename}\n")


def main():
    parser = argparse.ArgumentParser(
        description="Download and extract NVIDIA cuXX wheels from PyPI"
    )
    parser.add_argument("cuda_version", help="CUDA version suffix (e.g., 11, 12, 13)")
    args = parser.parse_args()

    os.makedirs(DOWNLOAD_DIR, exist_ok=True)
    os.makedirs(UNZIP_L_DIR, exist_ok=True)

    if os.path.exists(PKGS_JSON):
        print(f"Loading cached package list from {PKGS_JSON} ...")
        with open(PKGS_JSON, "r", encoding="utf-8") as f:
            pkgs = json.load(f)
    else:
        pkgs = fetch_cu_wheel_metadata(args.cuda_version)
        with open(PKGS_JSON, "w", encoding="utf-8") as f:
            json.dump(pkgs, f, indent=2)

    for name, version, wheels in pkgs:
        print(f"\n{name} ({version})")
        for filename, url in wheels:
            print(f"  - {filename}\n    {url}")
            download_and_extract_listing(filename, url)


if __name__ == "__main__":
    main()
