#!/bin/bash

set -euo pipefail

usage() {
    cat <<'EOF'
Usage:
    generate_ctk_ldd_graphs.sh [--exclude=SUBSTRING[,SUBSTRING...]] /path/to/root [output-prefix]

Generate a shared-library dependency graph for a CUDA Toolkit installation or directory tree.

Options:
    --exclude=...    Comma-separated path substrings to exclude before running ldd.

Outputs are written to the current working directory:
    <prefix>_find_ldd_output.txt
    <prefix>_graphviz.gv
    <prefix>_connected_components.txt
    <prefix>_graphviz.pdf
    <prefix>_graphviz.svg
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

write_packed_graphviz_output() {
    local graphviz_output="$1"
    local output="$2"
    local format="$3"
    local format_label="$4"
    shift 4
    local -a output_args=("$@")
    local packed_output="${output}.tmp"
    local packed_status

    rm -f "$packed_output"
    set +e
    ccomps -x "$graphviz_output" | dot | gvpack | neato -s -n2 "${output_args[@]}" -T"$format" >"$packed_output"
    packed_status=$?
    set -e
    if [[ -s "$packed_output" ]]; then
        mv "$packed_output" "$output"
        if [[ $packed_status -ne 0 ]]; then
            echo "Graphviz packing pipeline returned status $packed_status, but produced a $format_label; keeping it." >&2
        fi
        return 0
    fi

    rm -f "$packed_output"
    return 1
}

write_connected_components_text() {
    local graphviz_output="$1"
    local components_output="$2"
    local components_output_tmp="${components_output}.tmp"
    local components_status

    rm -f "$components_output_tmp"
    set +e
    ccomps -x "$graphviz_output" |
        awk '
function flush_component(    i) {
    if (component == 0) {
        return
    }
    if (printed_component) {
        print ""
    }
    printf "component %d (%d node(s)):\n", component, node_count
    for (i = 1; i <= node_count; i++) {
        print "  " nodes[i]
    }
    printed_component = 1
    delete seen
    delete nodes
    node_count = 0
}

/^digraph[[:space:]]/ {
    flush_component()
    component += 1
    next
}

{
    if ($0 ~ /^[[:space:]]*(graph|node|edge)[[:space:]]*\[/) {
        next
    }
    line = $0
    while (match(line, /"[^"]+"/)) {
        node = substr(line, RSTART + 1, RLENGTH - 2)
        if (!(node in seen)) {
            seen[node] = 1
            nodes[++node_count] = node
        }
        line = substr(line, RSTART + RLENGTH)
    }
}

END {
    flush_component()
}
' >"$components_output_tmp"
    components_status=$?
    set -e

    if [[ -s "$components_output_tmp" ]]; then
        mv "$components_output_tmp" "$components_output"
        if [[ $components_status -ne 0 ]]; then
            echo "Connected-component pipeline returned status $components_status, but produced a text file; keeping it." >&2
        fi
        return 0
    fi

    rm -f "$components_output_tmp"
    return 1
}

main() {
    local -a exclude_substrings=()
    local -a exclude_substrings_to_add=()
    local -a positional_args=()
    local exclude_value
    local exclude

    while [[ $# -gt 0 ]]; do
        case "$1" in
        -h | --help)
            usage
            exit 0
            ;;
        --exclude=* | --exlude=*)
            exclude_value="${1#*=}"
            exclude_substrings_to_add=()
            IFS=, read -r -a exclude_substrings_to_add <<<"$exclude_value"
            for exclude in "${exclude_substrings_to_add[@]}"; do
                if [[ -n "$exclude" ]]; then
                    exclude_substrings+=("$exclude")
                fi
            done
            shift
            ;;
        --)
            shift
            while [[ $# -gt 0 ]]; do
                positional_args+=("$1")
                shift
            done
            ;;
        --*)
            echo "Unknown option: $1" >&2
            usage >&2
            exit 1
            ;;
        *)
            positional_args+=("$1")
            shift
            ;;
        esac
    done

    if [[ ${#positional_args[@]} -gt 2 ]]; then
        usage >&2
        exit 1
    fi
    if [[ ${#positional_args[@]} -eq 0 ]]; then
        usage
        exit 0
    fi

    local input_root="${positional_args[0]}"
    if [[ ! -d "$input_root" ]]; then
        echo "Input root does not exist: $input_root" >&2
        exit 1
    fi
    input_root=$(readlink -f "$input_root")

    require_command find
    require_command ldd
    require_command convert_find_ldd_output_to_graphviz.py

    local -a search_dirs=()
    local -A seen_search_dirs=()
    local -a search_dir_candidates=()
    local candidate
    local resolved
    local search_mode

    if [[ -f "$input_root/include/cuda.h" ]]; then
        search_mode="CTK layout (include/cuda.h found)"
        search_dir_candidates=(
            "$input_root/lib64"
            "$input_root/nvvm/lib64"
        )
    elif [[ -d "$input_root/nvidia" || -d "$input_root/cutensor" ]]; then
        search_mode="Python CUDA package layout (nvidia/cutensor found)"
        search_dir_candidates=(
            "$input_root/nvidia"
            "$input_root/cutensor"
        )
    else
        search_mode="directory tree (include/cuda.h not found)"
        search_dir_candidates=("$input_root")
    fi

    for candidate in "${search_dir_candidates[@]}"; do
        if [[ -d "$candidate" ]]; then
            resolved=$(readlink -f "$candidate")
            if [[ -d "$resolved" && -z "${seen_search_dirs[$resolved]+x}" ]]; then
                seen_search_dirs["$resolved"]=1
                search_dirs+=("$resolved")
            fi
        fi
    done

    if [[ ${#search_dirs[@]} -eq 0 ]]; then
        echo "No supported library directories found under: $input_root" >&2
        exit 1
    fi

    local prefix="${positional_args[1]:-$(sanitize_prefix "$input_root")}"
    local ldd_output="${prefix}_find_ldd_output.txt"
    local graphviz_output="${prefix}_graphviz.gv"
    local components_output="${prefix}_connected_components.txt"
    local pdf_output="${prefix}_graphviz.pdf"
    local svg_output="${prefix}_graphviz.svg"

    printf 'Input root: %s\n' "$input_root"
    printf 'Search mode: %s\n' "$search_mode"
    printf 'Search directories:\n'
    printf '  %s\n' "${search_dirs[@]}"
    if [[ ${#exclude_substrings[@]} -gt 0 ]]; then
        printf 'Exclude substrings:\n'
        printf '  %s\n' "${exclude_substrings[@]}"
    fi

    printf 'Writing %s\n' "$ldd_output"
    while IFS= read -r candidate; do
        for exclude in "${exclude_substrings[@]}"; do
            if [[ "$candidate" == *"$exclude"* ]]; then
                continue 2
            fi
        done
        printf '%s\n' "$candidate"
        ldd "$candidate"
    done < <(
        find "${search_dirs[@]}" \
            -path '*/stubs' -prune -o \
            -type f -name '*.so*' -print
    ) >"$ldd_output"

    printf 'Writing %s\n' "$graphviz_output"
    convert_find_ldd_output_to_graphviz.py "$ldd_output" >"$graphviz_output"

    if command -v ccomps >/dev/null 2>&1; then
        printf 'Writing %s\n' "$components_output"
        if ! write_connected_components_text "$graphviz_output" "$components_output"; then
            echo "Connected-component pipeline failed; skipping $components_output." >&2
        fi
    else
        echo "ccomps not available; skipping $components_output." >&2
    fi

    require_command dot
    printf 'Writing %s\n' "$pdf_output"
    if command -v ccomps >/dev/null 2>&1 &&
        command -v gvpack >/dev/null 2>&1 &&
        command -v neato >/dev/null 2>&1; then
        if ! write_packed_graphviz_output "$graphviz_output" "$pdf_output" pdf PDF; then
            echo "Graphviz packing pipeline failed; falling back to dot -Tpdf." >&2
            dot -Tpdf "$graphviz_output" >"$pdf_output"
        fi
        printf 'Writing %s\n' "$svg_output"
        if ! write_packed_graphviz_output "$graphviz_output" "$svg_output" svg SVG; then
            echo "Graphviz packing pipeline failed; falling back to dot -Tsvg." >&2
            dot -Tsvg "$graphviz_output" >"$svg_output"
        fi
    else
        echo "ccomps/gvpack/neato not all available; falling back to dot outputs." >&2
        dot -Tpdf "$graphviz_output" >"$pdf_output"
        printf 'Writing %s\n' "$svg_output"
        dot -Tsvg "$graphviz_output" >"$svg_output"
    fi

    if command -v exiftool >/dev/null 2>&1; then
        exiftool -Title="$input_root" -overwrite_original "$pdf_output" >/dev/null
    fi

    printf 'Done.\n'
}

main "$@"
