#!/usr/bin/env bash

set -euo pipefail

usage() {
    cat <<'EOF'
Usage: md2html.sh FILE.md [...]

Convert each Markdown file to an HTML fragment next to the source file.

For each FILE.md, writes FILE.html. All inputs are validated before any
output file is written.
EOF
}

die() {
    echo "Error: $*" >&2
    exit 1
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
    usage
    exit 0
fi

if [[ $# -lt 1 ]]; then
    usage >&2
    exit 2
fi

if ! command -v pandoc >/dev/null 2>&1; then
    die "pandoc command not found on PATH"
fi

declare -a md_filenames=()
declare -a html_filenames=()

for md_filename in "$@"; do
    if [[ ! -e "$md_filename" ]]; then
        die "Markdown file does not exist: $md_filename"
    fi
    if [[ ! -f "$md_filename" ]]; then
        die "Markdown path is not a regular file: $md_filename"
    fi
    if [[ "$md_filename" != *.md ]]; then
        die "Markdown filename must end with .md: $md_filename"
    fi

    html_filename="${md_filename%.md}.html"
    if [[ -e "$html_filename" ]]; then
        die "HTML output file already exists: $html_filename"
    fi

    md_filenames+=("$md_filename")
    html_filenames+=("$html_filename")
done

for i in "${!md_filenames[@]}"; do
    md_filename=${md_filenames[$i]}
    html_filename=${html_filenames[$i]}
    pandoc "$md_filename" -f markdown -t html --wrap=none -o "$html_filename"
    echo "Wrote: $html_filename"
done
