#!/usr/bin/env python3
import sys
from pathlib import Path


def die(msg):
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(1)


def read_stream(stream, source="<stdin>"):
    for lineno, line in enumerate(stream, 1):
        for tok in line.strip().split():
            if not tok:
                continue
            try:
                yield float(tok)
            except ValueError:
                die(f"{source}:{lineno}: cannot parse float: {tok!r}")


def read_inputs(args):
    if not args:
        # No args → read stdin
        yield from read_stream(sys.stdin)
        return

    saw_stdin = False

    for arg in args:
        if arg == "-":
            if saw_stdin:
                die("multiple '-' arguments")
            saw_stdin = True
            yield from read_stream(sys.stdin)
            continue

        p = Path(arg)
        if not p.is_file():
            die(f"cannot read file: {arg}")
        try:
            with p.open() as f:
                yield from read_stream(f, source=arg)
        except OSError as e:
            die(f"{arg}: {e}")


def main():
    values = list(read_inputs(sys.argv[1:]))

    if not values:
        die("no values read")

    n = len(values)
    mn = min(values)
    mx = max(values)
    mean = sum(values) / n

    print(f"   n: {n}")
    print(f" min: {mn:g}")
    print(f"mean: {mean:g}")
    print(f" max: {mx:g}")


if __name__ == "__main__":
    main()
