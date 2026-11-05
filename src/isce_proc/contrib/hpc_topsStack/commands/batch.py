"""Batched submission and chain watching for `topsstack.py submit --batch` and `topsstack.py watch`.

Slurm here allows 10000 queued array tasks per user (QOSMaxSubmitJobPerUserLimit); a whole stack (e.g. d123:
~17 900 tasks) does not fit. `submit --batch` splits the requested steps into blocks of whole steps that fit,
submits the first block now and leaves a detached process that submits each next block once the queue has room,
after the previous block's final gate (afterok while it is queued; if it already ended, only if COMPLETED).

`watch` polls the stack's jobs (all IDs in run_files/job_id_logfile_*.txt) and exits on the first new failed task,
a never-satisfiable dependency, a stall (jobs queued, none running, no task ended for --stall hours) or the end.
"""
import datetime as dt
import glob
import json
import os
import re
import subprocess
import sys
import time

QUEUE_LIMIT = 10000        # QOSMaxSubmitJobPerUserLimit (queued array tasks per user)
BAD_STATES = ('FAILED', 'TIMEOUT', 'OUT_OF_MEMORY', 'NODE_FAIL')


def _sh(cmd):
    return subprocess.run(cmd, shell=True, capture_output=True, text=True).stdout


def queued_tasks():
    """Array tasks this user has in the queue (pending ones counted one by one)."""
    return len(_sh(f'squeue -u {os.environ.get("USER", "")} -h -r -o %i').split())


def step_tasks(run_dir):
    """[(number, run file, tasks)]: rows of each run file that has a job file (fused steps have none: 0)."""
    out = []
    for f in sorted(glob.glob(os.path.join(run_dir, 'run_[0-9][0-9]_*'))):
        name = os.path.basename(f)
        if not re.fullmatch(r'run_\d\d_\w+', name):
            continue
        jobs = glob.glob(os.path.join(run_dir, f'{name}.job')) + glob.glob(os.path.join(run_dir, f'{name}.p*.job'))
        n = sum(1 for line in open(f) if line.strip()) if jobs else 0
        out.append((int(name[4:6]), name, n))
    return out


def plan_blocks(steps, start, end, limit):
    """Contiguous blocks of whole steps in [start, end], each with at most `limit` tasks (a larger step alone)."""
    blocks, cur, n = [], [], 0
    for num, name, k in steps:
        if not start <= num <= end:
            continue
        if cur and n + k > limit:
            blocks.append(cur)
            cur, n = [], 0
        cur.append((num, name, k))
        n += k
    if cur:
        blocks.append(cur)
    # wait on the last step that has jobs: a fused step (no job file, 0 tasks) submits nothing (a149_south
    # 2026-11-02: block 01-13 ended on run_13_generate_burst_igram, no IDs found, later blocks never submitted)
    last = lambda b: next((x[1] for x in reversed(b) if x[2]), b[-1][1])
    return [dict(start=b[0][0], end=b[-1][0], last=last(b), tasks=sum(x[2] for x in b)) for b in blocks]


def _submit(stack, template, start, end, dep, log):
    """Run submit_chained_dependencies.sh for steps start-end; return the IDs to wait on for this block
    (its final gate, or without gates the jobs of its last step) and the submit output."""
    cmd = ['bash', 'submit_chained_dependencies.sh', '-s', str(start), '-e', str(end)] + (['-d', dep] if dep else [])
    r = subprocess.run(cmd, cwd=os.path.join(stack, 'run_files'), capture_output=True, text=True)
    out = r.stdout + r.stderr
    open(log, 'a').write(f'## {dt.datetime.now():%F %T} steps {start}-{end} {dep or ""}\n{out}\n')
    return out


def _block_wait_ids(out, last):
    """The final gate of a submitted block, else the job IDs of its last step."""
    g = re.findall(rf'gate after {re.escape(last)} - (\d+)', out)
    if g:
        return g[-1:]
    return re.findall(rf'Submitted \d+/\d+ (?:\S*/)?{re.escape(last)}\S*\.job - (\d+)', out)


def _state(jid):
    return (_sh(f'sacct -j {jid} -X -n -o State | head -1').split() or ['UNKNOWN'])[0]


def _follow_gate(jid, run_dir):
    """A gate that found bad rows submits reruns plus a follow-up gate and ends COMPLETED: follow the
    'follow-up gate N' lines of slurm-gate-<id>.out to the gate that really decides."""
    for _ in range(10):
        f = os.path.join(run_dir, f'slurm-gate-{jid}.out')
        m = re.findall(r'follow-up gate (\d+)', open(f).read()) if os.path.isfile(f) else []
        if not m:
            return jid
        jid = m[-1]
    return jid


def submit_batched(c, start, end, dep=None, limit=9000):
    """Submit steps start-end in queue-sized blocks: the first now, the rest from a detached process."""
    run_dir = os.path.join(c.stack, 'run_files')
    blocks = plan_blocks(step_tasks(run_dir), start, end, limit)
    if not blocks:
        sys.exit(f'no steps with job files in {start}-{end}')
    stamp = f'{dt.datetime.now():%Y-%m-%d_%H%M}'
    log = os.path.join(c.stack, 'logs', f'submit_batch_{stamp}.log')
    os.makedirs(os.path.dirname(log), exist_ok=True)
    for b in blocks:
        print(f'  block steps {b["start"]:02d}-{b["end"]:02d}: {b["tasks"]:6d} tasks')
    room = QUEUE_LIMIT - queued_tasks()
    if blocks[0]['tasks'] > room:
        print(f'note: {blocks[0]["tasks"]} tasks, {room} free in the queue: the first block waits too')
        blocks_left, wait = blocks, dep
    else:
        b = blocks[0]
        out = _submit(c.stack, c.template, b['start'], b['end'], dep, log)
        ids = _block_wait_ids(out, b['last'])
        print(f'submitted steps {b["start"]:02d}-{b["end"]:02d}; next block waits for {ids}; log {log}')
        if not ids:
            print(out[-2000:])
            sys.exit('could not find the job IDs of the block (see the log)')
        blocks_left, wait = blocks[1:], 'afterok:' + ':'.join(ids)
    if not blocks_left:
        return 0
    state = os.path.join(c.stack, 'logs', f'submit_batch_{stamp}.json')
    json.dump(dict(stack=c.stack, template=c.template, blocks=blocks_left, wait=wait, limit=limit, log=log),
              open(state, 'w'), indent=1)
    me = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'topsstack.py')
    clog = state.replace('.json', '_chain.out')
    p = subprocess.Popen(['nohup', sys.executable, me, 'submit', c.template, '--chain', state],
                         stdout=open(clog, 'w'), stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                         start_new_session=True, cwd=c.stack)
    open(os.path.join(c.stack, 'logs', 'submit_batch.pid'), 'w').write(f'{p.pid}\n')
    print(f'{len(blocks_left)} more block(s) from a detached process (pid {p.pid}): {clog}')
    return 0


def chain(state_file, poll=900):
    """Detached part of submit_batched: submit the remaining blocks one by one."""
    s = json.load(open(state_file))
    wait = s['wait']
    for b in s['blocks']:
        run_dir = os.path.join(s['stack'], 'run_files')
        while True:
            ids = [_follow_gate(j, run_dir) for j in re.findall(r'\d+', wait or '')]
            ended = [_state(j) for j in ids] if ids else []
            bad = [st for st in ended if st.startswith(('FAILED', 'CANCELLED', 'TIMEOUT', 'OUT_OF', 'NODE_FAIL'))]
            if bad:
                print(f'{dt.datetime.now():%F %T} block before {b["start"]:02d}: {ids} ended {bad}; stop', flush=True)
                return 1
            if QUEUE_LIMIT - 200 - queued_tasks() >= b['tasks']:
                break
            time.sleep(poll)
        queued = [j for j in ids if _sh(f'squeue -h -j {j}').strip()]
        if ids and not queued and any(st != 'COMPLETED' for st in ended):
            print(f'{dt.datetime.now():%F %T} {ids} ended {ended}; stop', flush=True)
            return 1
        dep = ('afterok:' + ':'.join(queued)) if queued else None   # finished OK: none (Slurm rejects old IDs)
        out = _submit(s['stack'], s['template'], b['start'], b['end'], dep, s['log'])
        new = _block_wait_ids(out, b['last'])
        n = len(re.findall(r'^Submitted', out, re.M))
        print(f'{dt.datetime.now():%F %T} submitted steps {b["start"]:02d}-{b["end"]:02d} ({dep or "no dep"}): '
              f'{n} jobs; waits for {new}', flush=True)
        if not new:
            print(out[-2000:], flush=True)
            return 1
        wait = 'afterok:' + ':'.join(new)
    print(f'{dt.datetime.now():%F %T} all blocks submitted', flush=True)
    return 0


def _chain_alive(stack):
    f = os.path.join(stack, 'logs', 'submit_batch.pid')
    if not os.path.isfile(f):
        return False
    pid = open(f).read().strip()
    return os.path.isdir(f'/proc/{pid}') and 'chain' in open(f'/proc/{pid}/cmdline').read().replace('\0', ' ')


def watch(c, every=20, stall=3.0, once=False, seed=False):
    """Poll the stack's jobs; return 2 on a problem (printed), 0 when nothing is left to run."""
    run_dir = os.path.join(c.stack, 'run_files')
    seen_f = os.path.join(c.stack, 'logs', 'watch_seen.txt')
    os.makedirs(os.path.dirname(seen_f), exist_ok=True)
    seen = set(open(seen_f).read().split()) if os.path.isfile(seen_f) else set()
    while True:
        ids = sorted({x.split()[-1] for f in glob.glob(os.path.join(run_dir, 'job_id_logfile_*.txt'))
                      for x in open(f) if x.split() and x.split()[-1].isdigit()})
        acct = _sh(f'sacct -j {",".join(ids)} -n -X -P -o JobID,JobName,State,End,NodeList') if ids else ''
        rows = [r.split('|') for r in acct.splitlines() if r.count('|') >= 4]
        bad = [r for r in rows if r[2].startswith(BAD_STATES) and r[0] not in seen]
        q = _sh(f'squeue -h -j {",".join(ids)} -o "%i|%j|%T|%r"').splitlines() if ids else []
        never = [x for x in q if 'DependencyNeverSatisfied' in x]
        running = [x for x in q if '|RUNNING|' in x]
        # an array left on reason JobArrayTaskLimit with no task running is stuck (seen 2026-11-03/04 on
        # filter_coherence of a149_south and a120_south2, for hours); re-setting its throttle releases it
        for jid in sorted({x.split('|')[0].split('_')[0] for x in q if x.endswith('|JobArrayTaskLimit')}):
            if not any(x.startswith(jid + '_') and '|RUNNING|' in x for x in q):
                t = re.search(r'ArrayTaskThrottle=(\d+)', _sh(f'scontrol show job {jid}'))
                _sh(f'scontrol update jobid={jid} ArrayTaskThrottle={t.group(1) if t else 200}')
                print(f'{dt.datetime.now():%F %T} {jid}: stuck on JobArrayTaskLimit with none running; throttle re-set', flush=True)
        ends = [r[3] for r in rows if r[3] not in ('Unknown', '', 'None')]
        last = max(ends) if ends else None
        age = (dt.datetime.now() - dt.datetime.fromisoformat(last)).total_seconds() / 3600 if last else 0
        stalled = q and not running and age > stall
        chain_on = _chain_alive(c.stack)
        if seed:                                             # mark the failures handled so far as seen
            seen |= {r[0] for r in bad}
            open(seen_f, 'w').write('\n'.join(sorted(seen)) + '\n')
            print(f'{len(bad)} failed tasks marked as seen'); bad, seed = [], False
        if bad or never or stalled or (not q and not chain_on):
            print(f'{dt.datetime.now():%F %T} {c.stack}')
            for r in bad:
                print(f'  {r[2]:14s} {r[0]:16s} {r[1]} {r[4]}')
            for x in never[:10]:
                print(f'  never satisfied: {x}')
            if stalled:
                print(f'  stall: {len(q)} jobs queued, none running, last task ended {last} ({age:.1f} h ago)')
            print(f'  jobs queued {len(q)}, running {len(running)}, batch submitter {"on" if chain_on else "off"}')
            seen |= {r[0] for r in bad}
            open(seen_f, 'w').write('\n'.join(sorted(seen)) + '\n')
            return 2 if (bad or never or stalled) else 0
        if once:
            print(f'ok: {len(q)} jobs queued, {len(running)} running, last task end {last}')
            return 1
        time.sleep(every * 60)
