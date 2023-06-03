#! /usr/bin/python3

"""."""

import collections
import sys


def run(args):
  assert len(args) == 2, "value_key.txt key_subset.txt"
  vk_txt, ks_txt = args

  kv_dict = collections.defaultdict(list)
  for line in open(vk_txt).read().splitlines():
    v, k = line.split()
    kv_dict[k].append(v)

  for k in set(open(ks_txt).read().splitlines()):
    print(",".join(kv_dict[k]), k)


if __name__ == "__main__":
  run(args=sys.argv[1:])
