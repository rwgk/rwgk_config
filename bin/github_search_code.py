#! /usr/bin/env python3

import itertools
import os
import json
import requests
import sys
import time

# https://github.com/settings/personal-access-tokens/new (fine-grained)
# Defaults are sufficient: Public Repositories (read-only)
# Except maybe change Expiration to 90 days.
GITHUB_TOKEN = (
    open(os.path.join(os.environ["HOME"], ".github_api_token")).read().strip()
)

API_URL = f"https://api.github.com/search/code"

HEADERS = {
    "Authorization": f"token {GITHUB_TOKEN}",
    "Accept": "application/vnd.github.v3+json",
}


def fetch_pages(
    start_page, query, per_page=100, rate_limit_block_size=9, rate_limit_seconds=61
):
    for request_index in itertools.count(0):
        if request_index and request_index % rate_limit_block_size == 0:
            print(f"WAIT {rate_limit_seconds=}", flush=True)
            time.sleep(rate_limit_seconds)
        page = start_page + request_index
        params = {"q": query, "per_page": per_page, "page": page}
        response = requests.get(API_URL, headers=HEADERS, params=params)
        response_json = response.json()
        if response.status_code != 200:
            raise RuntimeError(
                f"requests.get() FAILURE: {page=} {response.status_code=}, {response_json=}"
            )
        items = response_json.get("items", [])
        print(f"{page=} {len(items)=}", flush=True)
        print(json.dumps(response_json, indent=4))
        if not items or len(items) < per_page:
            print("DONE fetching all pages.", flush=True)
            break


def run(args):
    assert len(args) == 2, "start_page query"
    start_page = int(args[0])
    assert start_page > 0
    query = args[1]
    fetch_pages(start_page, query)


if __name__ == "__main__":
    run(args=sys.argv[1:])
