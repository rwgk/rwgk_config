#!/usr/bin/env bash
set -euo pipefail

. ~/.rwgkscrts/github_pat.env
export GITHUB_PERSONAL_ACCESS_TOKEN

exec npx -y @modelcontextprotocol/server-github
