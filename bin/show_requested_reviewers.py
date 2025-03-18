#! /usr/bin/env python3

# Starting point was:
# https://chatgpt.com/share/67be2b80-6b18-8008-b597-7ae9a288c96f

import os
import requests
import sys
import yaml


def get_github_username():
    """Look for this in ~/.gitconfig:
    [github]
            user = alice
    """
    with open(os.path.expandvars("$HOME/.gitconfig")) as f:
        lines = f.read().splitlines()
        for ix, line in enumerate(lines):
            ls = line.strip().replace(" ", "")
            if ls.startswith("user="):
                flds = ls.split("=")
                if (
                    len(flds) == 2
                    and flds[1]
                    and ix > 0
                    and lines[ix - 1].strip() == "[github]"
                ):
                    return flds[1]
    return None


def get_github_token():
    """Look for this in ~/.config/gh/hosts.yml:
    github.com:
        oauth_token: <redacted>

    Note: hosts.yml is usually created by running `gh auth login`
    """
    with open(os.path.expandvars("$HOME/.config/gh/hosts.yml")) as f:
        config = yaml.safe_load(f)
    return config["github.com"]["oauth_token"]


GITHUB_USERNAME = get_github_username()
assert GITHUB_USERNAME is not None
GITHUB_TOKEN = get_github_token()
assert GITHUB_TOKEN is not None


def show_requested_reviewers(owner, repo):
    """Fetch PRs where the user has been requested for review."""
    url = f"https://api.github.com/repos/{owner}/{repo}/pulls"
    headers = {"Authorization": f"token {GITHUB_TOKEN}"}
    params = {"state": "open", "per_page": 100, "draft": "false"}  # Exclude drafts

    prs = []
    while url:
        response = requests.get(url, headers=headers, params=params)
        if response.status_code != 200:
            print(f"Error: {response.status_code} - {response.json().get('message')}")
            return

        prs.extend(response.json())

        # Handle pagination via 'Link' header
        link_header = response.headers.get("Link")
        url = None
        if link_header:
            links = {}
            for part in link_header.split(","):
                section = part.split(";")
                if len(section) == 2:
                    link = section[0].strip().strip("<>")
                    rel = section[1].strip().replace("rel=", "").strip('"')
                    links[rel] = link
            url = links.get("next")

    for pr in prs:
        pr_number = pr["number"]
        pr_url = pr["html_url"]
        author = pr["user"]["login"]
        review_url = f"https://api.github.com/repos/{owner}/{repo}/pulls/{pr_number}/requested_reviewers"

        review_response = requests.get(review_url, headers=headers)
        if review_response.status_code != 200:
            print(f"Failed to fetch reviewers for PR {pr_number}")
            continue

        reviewers = review_response.json()
        logins = [user["login"] for user in reviewers.get("users", [])]

        if logins:
            print(f"{pr_url} (by {author}) → Requested: {', '.join(logins)}")


def run(args):
    assert len(args) == 1 and len(args[0].split("/")) == 2, "OWNER/REPO"
    owner, repo = args[0].split("/")

    show_requested_reviewers(owner, repo)


if __name__ == "__main__":
    run(args=sys.argv[1:])
