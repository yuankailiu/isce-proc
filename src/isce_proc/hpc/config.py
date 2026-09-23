"""Read a track template (e.g. ChileSenAT076.txt) for topsstack.py.

The template is the one run_isce_stack.py reads; topsstack.py also uses the asf.*, dem.*,
select.* and hpc.* keys (defaults in isce_proc.utils.config.AUTO_DICT). Values left as
`auto` / missing are derived here, so a track needs only what differs from the defaults.
"""
import datetime as dt
import math
import os
import re
from types import SimpleNamespace

from isce_proc.utils import config as defaults
from isce_proc.utils import utils

NAME = re.compile(r'(?P<dir>[AD])T(?P<orbit>\d+)', re.I)   # ...SenAT076 / ...SenDT127


def _floats(v):
    return [float(x) for x in re.split(r'[,\s]+', str(v).strip()) if x] if v not in (None, '') else None


def load(template):
    """Namespace with groups asf, dem, select, hpc, isce and paths (stack, data, template)."""
    from mintpy.utils import readfile
    template = os.path.abspath(template)
    raw = utils.check_template_auto_value(readfile.read_template(template), defaults.AUTO_DICT)
    group = lambda g: {k.split('.', 1)[1]: v for k, v in raw.items() if k.startswith(g + '.')}
    c = SimpleNamespace(template=template, stack=os.path.dirname(template),
                        **{g: SimpleNamespace(**group(g)) for g in ('isce', 'asf', 'dem', 'select', 'hpc')})
    name = os.path.splitext(os.path.basename(template))[0]
    m = NAME.search(name)
    bbox = _floats(c.isce.boundingBox)                      # S, N, W, E

    # asf
    c.asf.bbox = _floats(c.asf.bbox) or bbox
    c.asf.relativeOrbit = int(c.asf.relativeOrbit) if c.asf.relativeOrbit else (int(m['orbit']) if m else None)
    c.asf.flightDirection = (c.asf.flightDirection or
                             ({'A': 'ASCENDING', 'D': 'DESCENDING'}[m['dir'].upper()] if m else None))
    c.asf.platforms = [p.strip().upper() for p in str(c.asf.platforms).split(',') if p.strip()]
    c.asf.end = c.asf.end or dt.date.today().isoformat()
    c.asf.processes, c.asf.shards = int(c.asf.processes), int(c.asf.shards)
    if not c.asf.wkt and c.asf.bbox:
        s, n, w, e = c.asf.bbox
        c.asf.wkt = f'POLYGON(({w} {s},{e} {s},{e} {n},{w} {n},{w} {s}))'
    c.data = os.path.normpath(os.path.join(c.stack, c.asf.dataDir))

    # dem: integer-degree box around the bounding box
    c.dem.buffer = float(c.dem.buffer)
    snwe = _floats(c.dem.snwe)
    if not snwe and bbox:
        b = c.dem.buffer
        snwe = [math.floor(bbox[0] - b), math.ceil(bbox[1] + b), math.floor(bbox[2] - b), math.ceil(bbox[3] + b)]
    c.dem.snwe = [int(x) for x in snwe] if snwe else None
    c.dem.dir = os.path.normpath(os.path.join(c.stack, c.dem.dir))

    # select
    c.select.southNorth = _floats(c.select.southNorth) or (bbox[:2] if bbox else None)
    c.select.minAcq = int(c.select.minAcq)

    # hpc
    c.hpc.track = c.hpc.track or (f"{m['dir'].lower()}{int(m['orbit']):03d}" if m else name)
    c.hpc.mail = c.hpc.mail or f"{os.environ.get('USER', 'user')}@caltech.edu"
    c.hpc.ompTopo = int(c.hpc.ompTopo)
    return c


def show(c):
    """Human-readable summary of the resolved settings."""
    lines = [f'template : {c.template}', f'stack    : {c.stack}', f'data     : {c.data}']
    for g in ('asf', 'dem', 'select', 'hpc'):
        lines += [f'{g}.{k:16s} = {v}' for k, v in vars(getattr(c, g)).items()]
    return '\n'.join(lines)
