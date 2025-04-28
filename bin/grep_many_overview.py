#!/usr/bin/env python3

import sys
import subprocess


def main():
    if len(sys.argv) < 3:
        print("Usage: grep_many_overview.py PATTERN FILES...")
        sys.exit(1)

    pattern = sys.argv[1]
    files = sys.argv[2:]

    for filename in files:
        cmd = ["grep", "--", pattern, filename]  # Explicit pattern/file separation
        try:
            result = subprocess.run(cmd, capture_output=True, text=True)
            if result.stderr:
                raise RuntimeError(f"grep error: {result.stderr.strip()}")

            lines = result.stdout.strip().splitlines()
            count = len(lines)
            if count == 0:
                print(f"{filename} 0 lines")
            else:
                plural = "line" if count == 1 else "lines"
                print(f"{filename} {count} {plural} {lines[0]}")

        except subprocess.CalledProcessError as e:
            print(f"{filename} <grep error: {e}>")
        except Exception as e:
            print(f"{filename} <error: {str(e)}>")


if __name__ == "__main__":
    main()
