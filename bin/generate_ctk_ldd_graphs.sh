#!/bin/bash

set -euo pipefail

usage() {
    cat <<'EOF'
Usage:
    generate_ctk_ldd_graphs.sh /usr/local/cuda-13.2 [output-prefix]

Generate a shared-library dependency graph for a CUDA Toolkit installation.

Outputs are written to the current working directory:
    <prefix>_find_ldd_output.txt
    <prefix>_graphviz.gv
    <prefix>_graphviz.pdf
EOF
}

require_command() {
    if ! command -v "$1" >/dev/null 2>&1; then
        echo "Missing required command: $1" >&2
        exit 1
    fi
}

sanitize_prefix() {
    printf '%s\n' "$1" |
        sed \
            -e 's#^/*##' \
            -e 's#[^[:alnum:]]#_#g' \
            -e 's#__*#_#g' \
            -e 's#^_##' \
            -e 's#_$##'
}

main() {
    if [[ $# -gt 2 ]]; then
        usage >&2
        exit 1
    fi
    if [[ $# -eq 0 || "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
        usage
        exit 0
    fi

    local cuda_root="$1"
    if [[ ! -d "$cuda_root" ]]; then
        echo "CUDA root does not exist: $cuda_root" >&2
        exit 1
    fi
    cuda_root=$(readlink -f "$cuda_root")

    require_command find
    require_command ldd
    require_command convert_find_ldd_output_to_graphviz.py

    local -a search_dirs=()
    local -A seen_search_dirs=()
    local candidate
    local resolved
    shopt -s nullglob
    for candidate in \
        "$cuda_root/lib64" \
        "$cuda_root/lib" \
        "$cuda_root/nvvm/lib64" \
        "$cuda_root/nvvm/lib" \
        "$cuda_root"/targets/*/lib64 \
        "$cuda_root"/targets/*/lib; do
        if [[ -d "$candidate" ]]; then
            resolved=$(readlink -f "$candidate")
            if [[ -d "$resolved" && -z "${seen_search_dirs[$resolved]+x}" ]]; then
                seen_search_dirs["$resolved"]=1
                search_dirs+=("$resolved")
            fi
        fi
    done
    shopt -u nullglob

    if [[ ${#search_dirs[@]} -eq 0 ]]; then
        echo "No supported library directories found under: $cuda_root" >&2
        exit 1
    fi

    local prefix="${2:-$(sanitize_prefix "$cuda_root")}"
    local ldd_output="${prefix}_find_ldd_output.txt"
    local graphviz_output="${prefix}_graphviz.gv"
    local pdf_output="${prefix}_graphviz.pdf"

    printf 'CUDA root: %s\n' "$cuda_root"
    printf 'Search directories:\n'
    printf '  %s\n' "${search_dirs[@]}"

    printf 'Writing %s\n' "$ldd_output"
    find "${search_dirs[@]}" \
        -path '*/stubs' -prune -o \
        -type f -name '*.so*' -print -exec ldd {} \; >"$ldd_output"

    printf 'Writing %s\n' "$graphviz_output"
    convert_find_ldd_output_to_graphviz.py "$ldd_output" >"$graphviz_output"

    require_command dot
    printf 'Writing %s\n' "$pdf_output"
    if command -v ccomps >/dev/null 2>&1 &&
        command -v gvpack >/dev/null 2>&1 &&
        command -v neato >/dev/null 2>&1; then
        local packed_pdf="${pdf_output}.tmp"
        local packed_status
        rm -f "$packed_pdf"
        set +e
        ccomps -x "$graphviz_output" | dot | gvpack | neato -s -n2 -Tpdf >"$packed_pdf"
        packed_status=$?
        set -e
        if [[ -s "$packed_pdf" ]]; then
            mv "$packed_pdf" "$pdf_output"
            if [[ $packed_status -ne 0 ]]; then
                echo "Graphviz packing pipeline returned status $packed_status, but produced a PDF; keeping it." >&2
            fi
        else
            rm -f "$packed_pdf"
            echo "Graphviz packing pipeline failed; falling back to dot -Tpdf." >&2
            dot -Tpdf "$graphviz_output" >"$pdf_output"
        fi
    else
        echo "ccomps/gvpack/neato not all available; falling back to dot -Tpdf." >&2
        dot -Tpdf "$graphviz_output" >"$pdf_output"
    fi

    if command -v exiftool >/dev/null 2>&1; then
        exiftool -Title="$cuda_root" -overwrite_original "$pdf_output" >/dev/null
    fi

    printf 'Done.\n'
}

main "$@"
