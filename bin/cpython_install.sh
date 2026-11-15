#!/usr/bin/env bash

# Activation must execute in the calling shell.
if [[ ${BASH_SOURCE[0]} != "$0" ]]; then
    _cpython_install_source() {
        case ${1:-} in
        activate | a)
            if [[ $# != 2 || -z ${W:-} || $2 == */* || $2 == . || $2 == .. ]]; then
                echo 'Usage: source cpython_install.sh activate NAME (requires W)' >&2
                return 1
            fi
            if [[ -n ${CPYTHON_INSTALL_ACTIVE:-} ]]; then
                echo 'Deactivate the current CPython installation first.' >&2
                return 1
            fi
            local prefix="$W/cpython_installs/$2"
            if [[ ! -x $prefix/bin/python3 || -e $prefix/.cpython-install-in-progress ]]; then
                echo "No complete installation at $prefix" >&2
                return 1
            fi
            export CPYTHON_INSTALL_OLD_PATH=$PATH
            export CPYTHON_INSTALL_OLD_LD_LIBRARY_PATH=${LD_LIBRARY_PATH-}
            export CPYTHON_INSTALL_OLD_LD_LIBRARY_PATH_SET=${LD_LIBRARY_PATH+x}
            export CPYTHON_INSTALL_ACTIVE=1 CPYTHON_INSTALL_NAME=$2
            export PATH="$prefix/bin:$PATH"
            export LD_LIBRARY_PATH="$prefix/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
            hash -r
            ;;
        deactivate | d)
            if [[ $# != 1 || -z ${CPYTHON_INSTALL_ACTIVE:-} ]]; then
                echo 'No active CPython installation to deactivate.' >&2
                return 1
            fi
            export PATH=$CPYTHON_INSTALL_OLD_PATH
            if [[ -n ${CPYTHON_INSTALL_OLD_LD_LIBRARY_PATH_SET:-} ]]; then
                export LD_LIBRARY_PATH=$CPYTHON_INSTALL_OLD_LD_LIBRARY_PATH
            else
                unset LD_LIBRARY_PATH
            fi
            unset CPYTHON_INSTALL_ACTIVE CPYTHON_INSTALL_NAME CPYTHON_INSTALL_OLD_PATH \
                CPYTHON_INSTALL_OLD_LD_LIBRARY_PATH CPYTHON_INSTALL_OLD_LD_LIBRARY_PATH_SET
            hash -r
            ;;
        *)
            echo 'Source only for activate NAME or deactivate; execute the script for other commands.' >&2
            return 1
            ;;
        esac
    }
    if _cpython_install_source "$@"; then
        unset -f _cpython_install_source
        return 0
    else
        unset -f _cpython_install_source
        return 1
    fi
fi

set -euo pipefail

usage() {
    cat <<'EOF'
Usage: cpython_install.sh COMMAND [ARGS]
  update-source                  Clone if absent; pull clean main and fetch tags
  releases [MINOR ...]            Latest final source release for each minor
  refresh [MINOR ...]             Update source and install missing latest releases
  install VERSION [VARIANT]       Install an exact final tag already fetched
  list                           List existing installation directories
  path MINOR_OR_VERSION [VARIANT] Print newest installed matching bin/python3
  check NAME                     Validate a specific installed prefix

VARIANT is withgil, freethreaded, or (install only) both.
install defaults to both for 3.13+, withgil for older releases.
releases/refresh default to 3.9 3.10 3.11 3.12 3.13 3.14 3.15.
Existing prefixes are assumed healthy and skipped. No packages are installed.
CPYTHON_BUILD_JOBS controls make parallelism (default: at most 32).

For interactive activation:
  source cpython_install.sh activate NAME
  source cpython_install.sh deactivate
See ~/rwgk_config/kb/cpython_collection.md for the maintenance workflow.
EOF
}

die() {
    echo "cpython_install.sh: $*" >&2
    exit 1
}
case ${1:-help} in help | -h | --help)
    usage
    exit 0
    ;;
esac
[[ -n ${W:-} && -d $W ]] || die 'W must name an existing workspace directory'
base="$W/cpython_installs"
source_dir="$W/forked/cpython"
scratch="$W/disposable_worktrees/cpython"
default_minors=(3.9 3.10 3.11 3.12 3.13 3.14 3.15)
minor_valid() { [[ $1 =~ ^3\.([0-9]+)$ ]] && ((10#${BASH_REMATCH[1]} >= 9)); }
version_valid() { [[ $1 =~ ^3\.([0-9]+)\.([0-9]+)$ ]] && ((10#${BASH_REMATCH[1]} >= 9)); }

update_source() {
    if [[ ! -e $source_dir ]]; then
        mkdir -p "$(dirname "$source_dir")"
        git clone --origin upstream https://github.com/python/cpython.git "$source_dir"
    fi
    [[ $(git -C "$source_dir" branch --show-current) == main ]] || die 'Source checkout must stay on main'
    [[ -z $(git -C "$source_dir" status --porcelain) ]] || die 'Source checkout has local changes; preserve them before updating'
    case $(git -C "$source_dir" remote get-url upstream) in
    https://github.com/python/cpython | https://github.com/python/cpython.git | git@github.com:python/cpython.git) ;;
    *) die 'upstream must point to the official python/cpython repository' ;;
    esac
    git -C "$source_dir" pull --ff-only upstream main
    git -C "$source_dir" fetch upstream --tags
}

release() {
    local minor=$1 tag
    minor_valid "$minor" || die "Invalid minor version: $minor"
    tag=$(git -C "$source_dir" tag -l "v$minor.*" | sed -nE '/^v3\.[0-9]+\.[0-9]+$/p' | sort -V | tail -1)
    [[ -n $tag ]] || die "No final release tag for $minor; run update-source"
    printf '%s\n' "${tag#v}"
}

preflight() {
    local command pkg
    for command in cc make git pkg-config patchelf flock; do
        command -v "$command" >/dev/null || die "Missing build command: $command; discuss prerequisites with Ralf"
    done
    if command -v dpkg-query >/dev/null; then
        local missing=()
        for pkg in libssl-dev libffi-dev libsqlite3-dev zlib1g-dev libbz2-dev liblzma-dev \
            libgdbm-dev libgdbm-compat-dev uuid-dev libzstd-dev tk-dev libreadline-dev libncurses-dev; do
            [[ $(dpkg-query -W -f='${db:Status-Status}' "$pkg" 2>/dev/null) == installed ]] || missing+=("$pkg")
        done
        ((${#missing[@]} == 0)) || die "Missing development packages: ${missing[*]}. Inspect updates and ask Ralf for feedback before package changes."
    fi
}

clean_python() { env -u PYTHONHOME -u PYTHONPATH -u LD_LIBRARY_PATH -u PYTHON_GIL "$@"; }

check_install() (
    set -euo pipefail
    local prefix=$1 expected_version=${2:-} expected_variant=${3:-} temporary
    [[ -x $prefix/bin/python3 ]] || die "Missing interpreter: $prefix/bin/python3"
    clean_python "$prefix/bin/python3" -I - "$prefix" "$expected_version" "$expected_variant" <<'PY'
import bz2
import ctypes
import curses
import dbm.gnu
import dbm.ndbm
import lzma
import os
import readline
import sqlite3
import ssl
import sys
import sysconfig
import uuid
import zlib
import _tkinter
import _uuid

prefix, version, variant = sys.argv[1:]
assert os.path.realpath(sys.prefix) == os.path.realpath(prefix), sys.prefix
actual_version = ".".join(map(str, sys.version_info[:3]))
assert not version or actual_version == version, sys.version
free = bool(sysconfig.get_config_var("Py_GIL_DISABLED"))
assert not variant or free == (variant == "freethreaded"), sysconfig.get_config_var("Py_GIL_DISABLED")
if hasattr(sys, "_is_gil_enabled"):
    assert sys._is_gil_enabled() != free
assert ssl.create_default_context().get_ca_certs()
assert ctypes.CDLL(None)
assert sqlite3.connect(":memory:").execute("select 42").fetchone() == (42,)
for module in (bz2, lzma, zlib):
    assert module.decompress(module.compress(b"collection smoke test")) == b"collection smoke test"
assert uuid.UUID(str(uuid.uuid4())).version == 4
assert _tkinter.create(None, "collection", "Tk", False, False, False, False, None).eval("expr {6 * 7}") == "42"
if sys.version_info >= (3, 14):
    from compression import zstd
    assert zstd.decompress(zstd.compress(b"zstd smoke test")) == b"zstd smoke test"
print(f"Validated Python {actual_version} {'freethreaded' if free else 'withgil'} at {prefix}")
PY
    temporary=$(mktemp -d)
    trap 'rm -rf -- "$temporary"' EXIT
    clean_python "$prefix/bin/python3" -I -m venv "$temporary/venv"
    clean_python "$temporary/venv/bin/python3" -I -m pip --version
    cat >"$temporary/embed.c" <<'C'
#include <Python.h>
int main(int argc, char **argv) {
    PyConfig config;
    PyConfig_InitIsolatedConfig(&config);
    PyStatus status = PyConfig_SetBytesString(&config, &config.program_name, argv[1]);
    if (!PyStatus_Exception(status)) {
        status = PyConfig_SetBytesArgv(&config, argc, argv);
    }
    if (!PyStatus_Exception(status)) {
        status = Py_InitializeFromConfig(&config);
    }
    PyConfig_Clear(&config);
    if (PyStatus_Exception(status)) {
        Py_ExitStatusException(status);
    }
    int result = PyRun_SimpleString(
        "import os, sys, ssl, ctypes, sqlite3; "
        "assert os.path.realpath(sys.prefix) == os.path.realpath(sys.argv[2]); "
        "assert 6 * 7 == 42");
    return Py_FinalizeEx() < 0 || result != 0;
}
C
    local cflags ldflags
    read -r -a cflags <<<"$(clean_python "$prefix/bin/python3-config" --embed --cflags)"
    read -r -a ldflags <<<"$(clean_python "$prefix/bin/python3-config" --embed --ldflags)"
    cc "${cflags[@]}" "$temporary/embed.c" "${ldflags[@]}" "-Wl,-rpath,$prefix/lib" -o "$temporary/embed"
    clean_python "$temporary/embed" "$prefix/bin/python3" "$prefix"
)

install_one() (
    set -euo pipefail
    local version=$1 variant=$2 prefix="$base/${1}_${2}" worktree="$scratch/$1" build_dir
    if [[ -e $prefix/.cpython-install-in-progress ]]; then
        die "Interrupted installation at $prefix; inspect its recorded log before recovery"
    fi
    if [[ -d $prefix ]]; then
        echo "Keeping existing installation: $prefix"
        exit 0
    fi
    [[ ! -e $prefix ]] || die "Installation path is occupied: $prefix"
    preflight
    local sha jobs=${CPYTHON_BUILD_JOBS:-} log_dir log_file
    sha=$(git -C "$source_dir" rev-parse --verify "refs/tags/v$version^{commit}")
    if [[ -z $jobs ]]; then
        jobs=$(nproc)
        ((jobs <= 32)) || jobs=32
    fi
    [[ $jobs =~ ^[1-9][0-9]*$ ]] || die 'CPYTHON_BUILD_JOBS must be a positive integer'
    mkdir -p "$scratch" "$base"
    exec 9>"$scratch/$version.lock"
    flock -n 9 || die "Another build is using release $version"
    [[ ! -e $prefix ]] || die "Installation path appeared while preparing: $prefix"
    if [[ -e $worktree ]]; then
        [[ $(git -C "$worktree" rev-parse HEAD) == "$sha" ]] || die "Worktree revision differs at $worktree"
        [[ -z $(git -C "$worktree" status --porcelain) ]] || die "Worktree contains source changes: $worktree"
    else
        git -C "$source_dir" worktree add --detach "$worktree" "$sha"
    fi
    # build/ is ignored by CPython; each variant has independent configure state.
    build_dir="$worktree/build/$variant"
    mkdir -p "$build_dir"
    log_dir=${L:-$W/codex_scratch_space/logs/cpython-collection}
    mkdir -p "$log_dir"
    log_file="$log_dir/cpython_${version}_${variant}_$(date -u +%Y%m%dT%H%M%SZ)_$$.log"
    echo "Building $prefix; log: $log_file"
    exec > >(tee "$log_file" 9>&-) 2>&1
    local configure_flags=("--prefix=$prefix" --enable-shared --without-pydebug --disable-optimizations --without-lto --with-ensurepip=install)
    local version_minor=${version#3.}
    version_minor=${version_minor%%.*}
    ((version_minor < 13)) || configure_flags+=(--enable-experimental-jit=no)
    [[ $variant == withgil ]] || configure_flags+=(--disable-gil)
    cd "$build_dir"
    env -u PYTHONHOME -u PYTHONPATH -u LD_LIBRARY_PATH -u PYTHON_GIL \
        "$worktree/configure" "${configure_flags[@]}"
    env -u PYTHONHOME -u PYTHONPATH -u LD_LIBRARY_PATH -u PYTHON_GIL make -j"$jobs"
    mkdir "$prefix"
    printf 'Log: %s\nSource: %s\n' "$log_file" "$sha" >"$prefix/.cpython-install-in-progress"
    env -u PYTHONHOME -u PYTHONPATH -u LD_LIBRARY_PATH -u PYTHON_GIL make install
    # The loader expands ORIGIN at runtime.
    # shellcheck disable=SC2016
    patchelf --add-rpath '$ORIGIN/../lib' "$prefix/bin/python3"
    [[ -e $prefix/bin/python ]] || ln -s python3 "$prefix/bin/python"
    check_install "$prefix" "$version" "$variant"
    {
        printf 'Version: %s\nVariant: %s\nTag: v%s\nCommit: %s\n' "$version" "$variant" "$version" "$sha"
        printf 'Installed UTC: %s\nSource: %s\nBuild: %s\nLog: %s\n' "$(date -u +%FT%TZ)" "$source_dir" "$build_dir" "$log_file"
        printf 'Compiler: %s\n' "$(cc --version | head -1)"
        printf 'Configure:'
        printf ' %q' "${configure_flags[@]}"
        printf '\n'
    } >"$prefix/build-info.txt"
    rm -- "$prefix/.cpython-install-in-progress"
    echo "Installed and validated: $prefix"
)

install_release() {
    local version=$1 variant=${2:-both} minor
    version_valid "$version" || die "Expected a final release VERSION such as 3.14.10: $version"
    minor=${version%.*}
    case $variant in withgil | freethreaded | both) ;; *) die "Unknown variant: $variant" ;; esac
    if [[ $minor == 3.9 || $minor == 3.10 || $minor == 3.11 || $minor == 3.12 ]]; then
        [[ $variant != freethreaded ]] || die "Free-threaded builds require Python 3.13+: $version"
        variant=withgil
    fi
    case $variant in
    withgil | freethreaded) install_one "$version" "$variant" ;;
    both)
        install_one "$version" withgil
        install_one "$version" freethreaded
        ;;
    esac
}

action=$1
shift
case $action in
update-source)
    [[ $# == 0 ]] || die 'update-source takes no arguments'
    update_source
    ;;
releases | refresh)
    (($#)) || set -- "${default_minors[@]}"
    for minor in "$@"; do minor_valid "$minor" || die "Invalid minor version: $minor"; done
    [[ $action != refresh ]] || update_source
    for minor in "$@"; do
        version=$(release "$minor")
        if [[ $action == releases ]]; then
            printf '%s\n' "$version"
        else
            install_release "$version"
        fi
    done
    ;;
install)
    [[ $# == 1 || $# == 2 ]] || die 'Usage: cpython_install.sh install VERSION [VARIANT]'
    install_release "$@"
    ;;
list)
    [[ $# == 0 ]] || die 'list takes no arguments'
    [[ ! -d $base ]] || find "$base" -mindepth 1 -maxdepth 1 -type d -printf '%f\n' | sort -V
    ;;
path)
    [[ $# == 1 || $# == 2 ]] || die 'Usage: cpython_install.sh path MINOR_OR_VERSION [VARIANT]'
    selector=$1
    variant=${2:-withgil}
    [[ $variant == withgil || $variant == freethreaded ]] || die "Unknown variant: $variant"
    if minor_valid "$selector"; then
        pattern="^${selector//./\\.}\\.[0-9]+_${variant}$"
    elif version_valid "$selector"; then
        pattern="^${selector//./\\.}_${variant}$"
    else
        die "Invalid version selector: $selector"
    fi
    name=$(find "$base" -mindepth 1 -maxdepth 1 -type d -printf '%f\n' | sed -nE "/$pattern/p" | sort -V | tail -1)
    [[ -n $name ]] || die "No installed $selector $variant interpreter"
    [[ ! -e $base/$name/.cpython-install-in-progress ]] || die "Interrupted installation: $name"
    printf '%s\n' "$base/$name/bin/python3"
    ;;
check)
    [[ $# == 1 && $1 != */* && $1 != . && $1 != .. ]] || die 'Usage: cpython_install.sh check NAME'
    if [[ -e $base/$1/.cpython-install-in-progress ]]; then
        echo "Checking interrupted installation $1; its marker will be preserved." >&2
    fi
    check_install "$base/$1"
    ;;
activate | a | deactivate | d)
    die 'Activation changes the calling shell: use source cpython_install.sh activate NAME / deactivate'
    ;;
*)
    usage >&2
    exit 1
    ;;
esac
