#!/usr/bin/env python3

import ctypes
import ctypes.util
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
        raise RuntimeError(
            f"Could not load {name!r} (required for dlinfo/dlerror on Linux)"
        ) from e


LIBDL = _load_libdl()

# dlinfo
LIBDL.dlinfo.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p]
LIBDL.dlinfo.restype = ctypes.c_int

# dlerror (thread-local error string; cleared after read)
LIBDL.dlerror.argtypes = []
LIBDL.dlerror.restype = ctypes.c_char_p

# First appeared in 2004-era glibc. Universally correct on Linux for all practical purposes.
RTLD_DI_LINKMAP = 2


class LinkMap(ctypes.Structure):
    # Minimal definition for our purposes: include only the fields up to l_name.
    # The real struct link_map has additional members, which we omit here.
    _fields_ = (
        ("l_addr", ctypes.c_void_p),
        ("l_name", ctypes.c_char_p),
    )


def _dl_last_error() -> Optional[str]:
    msg = LIBDL.dlerror()
    return msg.decode() if msg else None


def abs_path_for_dynamic_library(libname: str, handle: ctypes.CDLL) -> str:
    lm_ptr = ctypes.POINTER(LinkMap)()
    rc = LIBDL.dlinfo(
        ctypes.c_void_p(handle._handle), RTLD_DI_LINKMAP, ctypes.byref(lm_ptr)
    )
    if rc != 0:
        err = _dl_last_error()
        raise OSError(
            f"dlinfo failed for {libname=!r} (rc={rc})" + (f": {err}" if err else "")
        )
    if not lm_ptr:
        raise OSError(f"dlinfo returned NULL link_map pointer for {libname=!r}")
    name = lm_ptr.contents.l_name
    if not name:
        raise OSError(f"dlinfo returned empty l_name for {libname=!r}")
    path = name.decode()
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
