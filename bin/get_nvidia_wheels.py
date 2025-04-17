#!/usr/bin/env python3

import requests
import re
import argparse
import os


def find_nvidia_cu12_packages():
    print("Fetching PyPI package index...")
    simple_index_url = "https://pypi.org/simple/"
    resp = requests.get(simple_index_url)
    resp.raise_for_status()

    package_names = re.findall(r'<a href="[^"]+">([^<]+)</a>', resp.text)
    pattern = re.compile(r"^nvidia-[a-z0-9\-]+-cu12$", re.IGNORECASE)
    cu12_pkgs = sorted([name for name in package_names if pattern.match(name)])

    results = []

    for pkg in cu12_pkgs:
        print(f"Querying {pkg}...")
        json_url = f"https://pypi.org/pypi/{pkg}/json"
        try:
            data = requests.get(json_url).json()
            latest_ver = data["info"]["version"]
            files = [
                (f["filename"], f["url"])
                for f in data["releases"].get(latest_ver, [])
                if f["filename"].endswith(".whl")
            ]
            results.append((pkg, latest_ver, files))
        except Exception as e:
            print(f"  Failed: {e}")
            continue

    return results


def print_package_table(packages):
    for name, version, wheels in packages:
        print(f"\n{name} ({version})")
        for filename, url in wheels:
            print(f"  - {filename}\n    {url}")


def download_wheels(packages, dest_dir="downloads"):
    os.makedirs(dest_dir, exist_ok=True)
    print(f"\nDownloading wheels into ./{dest_dir}/ ...")

    for name, version, wheels in packages:
        for filename, url in wheels:
            dest_path = os.path.join(dest_dir, filename)
            if os.path.exists(dest_path):
                print(f"Skipping (already exists): {filename}")
                continue
            print(f"Downloading: {filename}")
            with requests.get(url, stream=True) as r:
                r.raise_for_status()
                with open(dest_path, "wb") as f:
                    for chunk in r.iter_content(chunk_size=8192):
                        f.write(chunk)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="List NVIDIA cu12 wheels on PyPI.")
    parser.add_argument(
        "--download-all",
        action="store_true",
        help="Download all wheel files after listing",
    )
    args = parser.parse_args()

    pkgs = find_nvidia_cu12_packages()
    print_package_table(pkgs)

    if args.download_all:
        download_wheels(pkgs)
