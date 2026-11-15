#!/usr/bin/env bash
# Focused regression checks for discovery, preservation, and sourced activation.
# shellcheck disable=SC1090
set -euo pipefail
installer=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../bin" && pwd)/cpython_install.sh
test_workspace=$(mktemp -d)
trap 'rm -rf -- "$test_workspace"' EXIT
export W="$test_workspace/workspace"
export GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL=/dev/null
mkdir -p "$W/forked/cpython" "$W/cpython_installs"
git -C "$W/forked/cpython" init -q --initial-branch=main
git -C "$W/forked/cpython" -c user.name=Test -c user.email=test@example.invalid \
    -c commit.gpgsign=false commit -q --allow-empty -m fixture
for tag in v3.9.25 v3.10.9 v3.10.10 v3.10.11rc1 v3.14.2 v3.14.10 v3.14.11a1; do
    git -C "$W/forked/cpython" tag "$tag"
done
[[ $("$installer" releases 3.10 3.14) == $'3.10.10\n3.14.10' ]]

# No tag or runnable interpreter is required to preserve an existing prefix.
mkdir "$W/cpython_installs/3.11.18_withgil"
printf 'preserve this\n' >"$W/cpython_installs/3.11.18_withgil/example"
[[ $("$installer" install 3.11.18) == *'Keeping existing installation:'* ]]
[[ $(cat "$W/cpython_installs/3.11.18_withgil/example") == 'preserve this' ]]
[[ $(find "$W/cpython_installs/3.11.18_withgil" -mindepth 1 | wc -l) == 1 ]]
if "$installer" install 3.12.16 freethreaded >"$test_workspace/error" 2>&1; then
    echo 'Unexpected free-threaded installation below 3.13' >&2
    exit 1
fi
if "$installer" install 3.9.25 unknown >"$test_workspace/error" 2>&1; then
    echo 'Unexpected acceptance of an unknown variant' >&2
    exit 1
fi

mkdir "$W/cpython_installs/3.14.2_withgil" "$W/cpython_installs/3.14.10_withgil"
[[ $("$installer" path 3.14) == "$W/cpython_installs/3.14.10_withgil/bin/python3" ]]
[[ $("$installer" path 3.14.2) == "$W/cpython_installs/3.14.2_withgil/bin/python3" ]]
printf 'failed build\n' >"$W/cpython_installs/3.14.10_withgil/.cpython-install-in-progress"
if "$installer" install 3.14.10 withgil >"$test_workspace/error" 2>&1; then
    echo 'Unexpected skip of a known interrupted installation' >&2
    exit 1
fi
[[ $(cat "$W/cpython_installs/3.14.10_withgil/.cpython-install-in-progress") == 'failed build' ]]

mkdir -p "$W/cpython_installs/3.9.25_withgil/bin" "$W/cpython_installs/3.9.25_withgil/lib"
printf '#!/bin/sh\nexit 0\n' >"$W/cpython_installs/3.9.25_withgil/bin/python3"
chmod +x "$W/cpython_installs/3.9.25_withgil/bin/python3"
for initial_library_path in unset empty populated; do
    (
        case $initial_library_path in
        unset) unset LD_LIBRARY_PATH ;;
        empty) export LD_LIBRARY_PATH= ;;
        populated) export LD_LIBRARY_PATH=/example/libraries ;;
        esac
        before_path=$PATH
        before_library_path=${LD_LIBRARY_PATH-}
        before_library_set=${LD_LIBRARY_PATH+x}
        before_options=$-
        before_functions=$(declare -F)
        source "$installer" activate 3.9.25_withgil
        [[ $PATH == "$W/cpython_installs/3.9.25_withgil/bin:$before_path" ]]
        [[ $CPYTHON_INSTALL_NAME == 3.9.25_withgil ]]
        if source "$installer" activate 3.9.25_withgil >"$test_workspace/error" 2>&1; then
            echo 'Unexpected nested activation' >&2
            exit 1
        fi
        source "$installer" deactivate
        [[ $PATH == "$before_path" && ${LD_LIBRARY_PATH-} == "$before_library_path" ]]
        [[ ${LD_LIBRARY_PATH+x} == "$before_library_set" && $- == "$before_options" ]]
        [[ $(declare -F) == "$before_functions" && -z ${CPYTHON_INSTALL_ACTIVE+x} ]]
    )
done
if "$installer" activate 3.9.25_withgil >"$test_workspace/error" 2>&1; then
    echo 'Executable activation should explain the source interface' >&2
    exit 1
fi
printf 'CPython collection discovery, preservation, and activation checks passed.\n'
