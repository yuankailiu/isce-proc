#!/usr/bin/env python3
"""Connectivity of the ionosphere pair network actually inverted (invertIon), written next to the products.

MintPy reads ion_dates/ (dates already inverted from the ion pairs), so it cannot see how well each date is tied
to the others. This lists, for one stack (a resnick stack or its export copy):
  - the ion pairs (run_NN_filtIon), the ones excluded (--exc_pair of run_NN_invertIon) and the ones used
  - rank of the used network (must be dates - 1) and its connected components
  - ion pairs per date (degree); dates with few pairs are tied weakly
  - bridges: used pairs whose removal splits the network (a single pair carries the tie; an error in it shifts
    every date on one side relative to the other)

  ion_network.py STACK_DIR [STACK_DIR ...]      # writes STACK_DIR/ion_network[_<run_files suffix>].txt

A stack with several run_files* folders (segments, e.g. run_files_2014-2020) gets one file per folder.
"""
import collections
import glob
import os
import re
import sys

PAIR = re.compile(r'(?<!\d)(\d{8})_(\d{8})(?!\d)')


def bridges(dates, pairs):
    """Bridges of the undirected graph (iterative Tarjan)."""
    adj = collections.defaultdict(list)
    for k, (a, b) in enumerate(pairs):
        adj[a].append((b, k))
        adj[b].append((a, k))
    disc, low, out, t = {}, {}, [], 0
    for root in dates:
        if root in disc:
            continue
        disc[root] = low[root] = t; t += 1
        stack = [(root, -1, iter(adj[root]))]
        while stack:
            v, pe, it = stack[-1]
            for w, k in it:
                if k == pe:
                    continue
                if w not in disc:
                    disc[w] = low[w] = t; t += 1
                    stack.append((w, k, iter(adj[w])))
                    break
                low[v] = min(low[v], disc[w])
            else:
                stack.pop()
                if stack:
                    u = stack[-1][0]
                    low[u] = min(low[u], low[v])
                    if low[v] > disc[u]:
                        out.append(pairs[pe])
    return sorted(out)


def components(dates, pairs):
    parent = {d: d for d in dates}
    def root(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    for a, b in pairs:
        parent[root(a)] = root(b)
    groups = collections.defaultdict(list)
    for d in dates:
        groups[root(d)].append(d)
    return sorted(groups.values(), key=len, reverse=True)


def rank(dates, pairs):
    import numpy as np
    ix = {d: i for i, d in enumerate(dates)}
    A = np.zeros((len(pairs), len(dates)))
    for r, (a, b) in enumerate(pairs):
        A[r, ix[a]], A[r, ix[b]] = -1, 1
    return int(np.linalg.matrix_rank(A)) if len(pairs) else 0


def report(stack, run_dir):
    filt = sorted(glob.glob(os.path.join(run_dir, 'run_[0-9][0-9]_filtIon')))
    inv = sorted(glob.glob(os.path.join(run_dir, 'run_[0-9][0-9]_invertIon')))
    if not filt:
        return None
    allp = sorted(set(PAIR.findall(open(filt[0]).read())))
    exc = set()
    if inv:
        for m in re.findall(r'--exc_pair((?:\s+\d{8}_\d{8})+)', open(inv[0]).read()):
            exc |= set(tuple(p.split('_')) for p in m.split())
    used = [p for p in allp if p not in exc]
    dates = sorted({d for p in allp for d in p})
    udates = sorted({d for p in used for d in p})
    deg = collections.Counter(d for p in used for d in p)
    comp = components(dates, used)
    br = bridges(dates, used)
    rk = rank(dates, used)
    weak = sorted((deg[d], d) for d in dates if deg[d] <= 2)
    lines = [f'# ionosphere pair network inverted by invertIon: {stack}',
             f'# from {os.path.relpath(filt[0], stack)} and the --exc_pair of '
             f'{os.path.relpath(inv[0], stack) if inv else "(no invertIon run file)"}  (ion_network.py)',
             f'dates {len(dates)}  ion pairs {len(allp)}  excluded {len(exc & set(allp))}  used {len(used)}',
             f'rank {rk} (dates - 1 = {len(dates) - 1}: {"full" if rk == len(dates) - 1 else "DEFICIENT"})  '
             f'components {len(comp)}' + (f'  (sizes {[len(c) for c in comp]})' if len(comp) > 1 else ''),
             f'dates without any used pair: {" ".join(d for d in dates if d not in udates) or "none"}',
             f'ion pairs per date: min {min(deg[d] for d in dates) if dates else 0}  '
             f'median {sorted(deg[d] for d in dates)[len(dates) // 2] if dates else 0}  max {max(deg.values()) if deg else 0}',
             f'weakly tied dates (<= 2 used pairs): {len(weak)}']
    lines += [f'  {d}  {n} pair(s): {" ".join(a + "_" + b for a, b in used if d in (a, b))}' for n, d in weak]
    lines.append(f'bridges (one used pair carries the tie; removing it splits the network): {len(br)}')
    lines += [f'  {a}_{b}' for a, b in br]
    lines.append(f'excluded pairs ({len(exc & set(allp))}):')
    lines += [f'  {a}_{b}' for a, b in sorted(exc & set(allp))]
    suffix = os.path.basename(run_dir)[len('run_files'):]
    out = os.path.join(stack, f'ion_network{suffix}.txt')
    open(out, 'w').write('\n'.join(lines) + '\n')
    return out, lines[2:8]


def main(stacks):
    for s in stacks:
        s = os.path.abspath(s)
        dirs = sorted(d for d in glob.glob(os.path.join(s, 'run_files*')) if os.path.isdir(d)
                      and not d.endswith('.copy') and glob.glob(os.path.join(d, 'run_[0-9][0-9]_filtIon')))
        if not dirs:
            print(f'{s}: no run_files*/run_NN_filtIon (no ionosphere)')
            continue
        for d in dirs:
            out, head = report(s, d)
            print(f'{out}\n  ' + '\n  '.join(head))


if __name__ == '__main__':
    main(sys.argv[1:] or ['.'])
