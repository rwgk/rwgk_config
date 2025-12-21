#!/bin/bash
set -euo pipefail
set -x

# Verify patchelf is available (needed to set RUNPATH on installed python)
if ! command -v patchelf &>/dev/null; then
    echo "Error: patchelf is not installed or not in PATH" >&2
    exit 1
fi

# Verify git_sha_info is available (needed to track installed commits)
if ! command -v git_sha_info &>/dev/null; then
    echo "Error: git_sha_info is not installed or not in PATH" >&2
    exit 1
fi

# Verify required dev packages are installed (needed for ctypes, ssl, sqlite3 modules)
missing_pkgs=()
for pkg in libffi-dev libssl-dev libsqlite3-dev; do
    if ! dpkg -s "$pkg" &>/dev/null; then
        missing_pkgs+=("$pkg")
    fi
done
if [ ${#missing_pkgs[@]} -gt 0 ]; then
    echo "Error: Missing required dev packages: ${missing_pkgs[*]}" >&2
    echo "Install with: sudo apt install ${missing_pkgs[*]}" >&2
    exit 1
fi

# Determine current short git SHA for per-commit install prefixes.
short_sha="$(git rev-parse --short HEAD)"
base_dir="$HOME/wrk/cpython_installs"

# Helper function to build and install CPython.
# Usage: build_cpython <suffix> [extra_configure_flags...]
build_cpython() {
    local suffix="$1"
    shift
    local extra_flags=("$@")

    local install_dir="${base_dir}/v3.14_${short_sha}_${suffix}"

    mkdir -p "$install_dir"

    make distclean || true

    ./configure \
        --prefix="$install_dir" \
        --enable-shared \
        "${extra_flags[@]}"

    make -j"$(nproc)"
    make install

    # Set RUNPATH so python can find libpython without LD_LIBRARY_PATH
    patchelf --add-rpath '$ORIGIN/../lib' "$install_dir/bin/python3"
}

# Build default (GIL-enabled) Python
build_cpython "default"

# Build free-threaded (no-GIL) Python
build_cpython "freethreaded" --disable-gil

# Update sha_info.txt with the current commit info
sha_info_file="$base_dir/sha_info.txt"
git_sha_info $short_sha >>"$sha_info_file"
sort -u "$sha_info_file" -o "$sha_info_file"
