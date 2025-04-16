#!/usr/bin/env python3

# /usr/bin/python -m venv scratch_venv
# . scratch_venv/bin/activate
# pip install ...
# find scratch_venv/ -type f -name '*.so*' -print -exec ldd {} \; > find_ldd_output.txt
# convert_find_ldd_output_to_graphviz.py find_ldd_output.txt > find_ldd_output.gv
# ccomps -x find_ldd_output.gv | dot | gvpack | neato -s -n2 -Tpdf > find_ldd_output.pdf

import sys
from pathlib import Path
from collections import defaultdict, deque


def strip_path(path):
    return Path(path).name


def find_connected_components(deps):
    # Build undirected adjacency list
    adj = defaultdict(set)
    for src, targets in deps.items():
        for tgt in targets:
            adj[src].add(tgt)
            adj[tgt].add(src)

    visited = set()
    components = []

    for node in adj:
        if node not in visited:
            queue = deque([node])
            component = []
            while queue:
                current = queue.popleft()
                if current in visited:
                    continue
                visited.add(current)
                component.append(current)
                queue.extend(adj[current] - visited)
            components.append(component)

    return components


def run(args):
    assert len(args) == 1, "find_ldd_output.txt"
    ldd_input = args[0]
    out = sys.stdout

    # Parse find-ldd output
    deps = defaultdict(list)
    top_nodes = set()

    with open(ldd_input) as f:
        current = None
        for line in f:
            assert line
            if not line[0].isspace():
                current = strip_path(line.strip())
                top_nodes.add(current)
            elif " => " in line and " => /lib/" not in line:
                flds = line.split()
                dep = flds[0]
                deps[current].append(dep)

    # Find connected components
    components = find_connected_components(deps)

    # Write dot file
    out.write("digraph deps {\n")
    out.write('  node [fontname="Courier New", fontsize=10];\n')

    done = set()
    len_c_c = [(len(c), c) for c in components]
    for _, component in reversed(sorted(len_c_c)):
        for node in component:
            assert node not in done
            done.add(node)
            out.write(f'  "{node}";\n')
            for dep in deps.get(node, []):
                if dep in component:
                    out.write(f'  "{node}" -> "{dep}";\n')
        out.write("\n")

    for node in top_nodes:
        if node not in done:
            out.write(f'  "{node}";\n')
            out.write("\n")

    out.write("}\n")


if __name__ == "__main__":
    run(args=sys.argv[1:])
