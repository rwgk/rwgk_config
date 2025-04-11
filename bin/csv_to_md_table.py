#!/usr/bin/env python3

import csv
import sys


def csv_to_markdown(csv_path):
    with open(csv_path, newline="") as f:
        reader = list(csv.reader(f))
        if not reader:
            return

        # Print header
        header = reader[0]
        print("| " + " | ".join(header) + " |")
        print("|" + "|".join(["---"] * len(header)) + "|")

        # Print rows
        for row in reader[1:]:
            print("| " + " | ".join(row) + " |")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: csv_to_md_table.py file.csv")
        sys.exit(1)
    csv_to_markdown(sys.argv[1])
