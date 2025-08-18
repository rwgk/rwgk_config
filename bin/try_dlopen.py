#!/usr/bin/env python3

import ctypes
import ctypes.util
import os
import sys
from typing import Optional


def _load_libdl() -> ctypes.CDLL:
    # In normal glibc-based Linux environments, find_library("dl") should return
    # something like "libdl.so.2". In minimal or stripped-down environments
    # (no ldconfig/gcc, incomplete linker cache), this can return None even
    # though libdl is present. In that case, we fall back to the stable SONAME.
    name = ctypes.util.find_library("dl") or "libdl.so.2"
    try:
        return ctypes.CDLL(name)
    except OSError as e:
        raise RuntimeError(f"Could not load {name!r} (required for dlinfo/dlerror on Linux)") from e


LIBDL = _load_libdl()

# dlinfo
LIBDL.dlinfo.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p]
LIBDL.dlinfo.restype = ctypes.c_int

# dlerror (thread-local error string; cleared after read)
LIBDL.dlerror.argtypes = []
LIBDL.dlerror.restype = ctypes.c_char_p

# First appeared in 2004-era glibc. Universally correct on Linux for all practical purposes.
RTLD_DI_LINKMAP = 2


class _LinkMapLNameView(ctypes.Structure):
    """
    Prefix-only view of glibc's `struct link_map` used **solely** to read `l_name`.

    Background:
      - `dlinfo(handle, RTLD_DI_LINKMAP, ...)` returns a `struct link_map*`.
      - The first few members of `struct link_map` (including `l_name`) have been
        stable on glibc for decades and are documented as debugger-visible.
      - We only need the offset/layout of `l_name`, not the full struct.

    Safety constraints:
      - This is a **partial** definition (prefix). It must only be used via a pointer
        returned by `dlinfo(...)`.
      - Do **not** instantiate it or pass it **by value** to any C function.
      - Do **not** access any members beyond those declared here.
      - Do **not** rely on `ctypes.sizeof(LinkMapPrefix)` for allocation.

    Rationale:
      - Defining only the leading fields avoids depending on internal/unstable
        tail members while keeping code more readable than raw pointer arithmetic.
    """

    _fields_ = (
        ("l_addr", ctypes.c_void_p),  # ElfW(Addr)
        ("l_name", ctypes.c_char_p),  # char*
    )


# Defensive assertions, mainly  to document the invariants we depend on
assert _LinkMapLNameView.l_addr.offset == 0
assert _LinkMapLNameView.l_name.offset == ctypes.sizeof(ctypes.c_void_p)


def _dl_last_error() -> Optional[str]:
    msg_bytes = cast(Optional[bytes], LIBDL.dlerror())
    if not msg_bytes:
        return None  # no pending error
    # Never raises; undecodable bytes are mapped to U+DC80..U+DCFF
    return msg_bytes.decode("utf-8", "surrogateescape")


def abs_path_for_dynamic_library(libname: str, handle: ctypes.CDLL) -> str:
    lm_view = ctypes.POINTER(_LinkMapLNameView)()
    rc = LIBDL.dlinfo(ctypes.c_void_p(handle._handle), RTLD_DI_LINKMAP, ctypes.byref(lm_view))
    if rc != 0:
        err = _dl_last_error()
        raise OSError(f"dlinfo failed for {libname=!r} (rc={rc})" + (f": {err}" if err else ""))
    if not lm_view:  # NULL link_map**
        raise OSError(f"dlinfo returned NULL link_map pointer for {libname=!r}")

    l_name_bytes = lm_view.contents.l_name
    if not l_name_bytes:
        raise OSError(f"dlinfo returned empty link_map->l_name for {libname=!r}")

    # Won't raise, and preserves undecodable bytes round-trip
    path = os.fsdecode(l_name_bytes)  # filesystem encoding + surrogateescape
    if not path:
        raise OSError(f"dlinfo returned empty path string for {libname=!r}")

    return path


def main(argv: list[str]) -> int:
    if not argv:
        prog = sys.argv[0] if sys.argv else "try_dlopen.py"
        print(f"Usage: {prog} <libname> [<libname> ...]", file=sys.stderr)
        return 2

    status = 0
    for libname in argv:
        try:
            handle = ctypes.CDLL(libname)
        except OSError as e:
            print(f"dlopen failed: {libname!r}: {e}")
            status = max(status, 1)
            continue

        print(f"dlopen succeeded: {libname!r}")
        try:
            abs_path = abs_path_for_dynamic_library(libname, handle)
            print(f"  resolved path: {abs_path!r}")
        except OSError as e:
            print(f"  could not resolve absolute path via dlinfo: {e}")
            status = max(status, 1)
    return status


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
