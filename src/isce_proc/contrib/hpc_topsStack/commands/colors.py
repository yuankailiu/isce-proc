"""ANSI colors for `-h` output, with the theme of Python 3.14 argparse (no extra package).

Headings bold blue, program names bold magenta, long options bold cyan, short options bold
green, metavars bold yellow; added here: template keys cyan, default values yellow, comments dim.
Colors only on a terminal; NO_COLOR=1 turns them off, FORCE_COLOR=1 forces them.
"""
import os
import re
import sys

B, BLUE, MAG, CYAN, GREEN, YEL, DIM, R = ('\033[1m', '\033[1;34m', '\033[1;35m', '\033[1;36m', '\033[1;32m',
                                          '\033[1;33m', '\033[2m', '\033[0m')


def enabled(stream=sys.stdout):
    if os.environ.get('NO_COLOR'):
        return False
    if os.environ.get('FORCE_COLOR'):
        return True
    return stream.isatty() and os.environ.get('TERM') != 'dumb'


OPT = re.compile(r'(?<![\w-])(--[a-zA-Z][\w-]*|-[a-zA-Z])(?:([ =])([A-Z][A-Z0-9_/]*(?: \[[A-Z][^\]]*\])?))?(?![\w-])')


def _opts(s):
    def f(m):
        c = CYAN if m.group(1).startswith('--') else GREEN
        out = f'{c}{m.group(1)}{R}'
        if m.group(3):
            out += f'{m.group(2)}{YEL}{m.group(3)}{R}'
        return out
    return OPT.sub(f, s)


def colorize(text):
    out = []
    for line in text.split('\n'):
        code, hash_, comment = line.partition('  # ')               # example lines: command  # comment
        if hash_ and code.strip().startswith(('topsstack.py', './', 'check_', 'clean_', 'write_')):
            out.append(colorize(code) + f'{DIM}  # {comment}{R}')
            continue
        m = re.match(r'^(usage:)(\s+)(\S+(?: [a-z]+)?(?= |$))(.*)$', line)
        if m:                                                      # usage: prog [subcommand] [options]
            rest = re.sub(r'\b(TEMPLATE|CMD|command)\b', f'{YEL}\\1{R}', _opts(m.group(4)))
            out.append(f'{BLUE}{m.group(1)}{R}{m.group(2)}{MAG}{m.group(3)}{R}{rest}')
        elif re.match(r'^[A-Za-z][\w ()/.,*-]*:\s*$', line) or re.match(r'^template keys read', line):
            out.append(f'{BLUE}{line}{R}')                         # section headings
        elif re.match(r'^topsstack\.py \w+ TEMPLATE', line):         # first line of `CMD -h`
            p, _, d = line.partition(': ')
            out.append(f'{MAG}{p}{R}: {d}' if d else f'{MAG}{line}{R}')
        elif re.match(r'^  [a-z][\w]*\.\w+\s+= ', line):             # template key = default (key list)
            k, _, v = line.partition('= ')
            out.append(f'{CYAN}{k}{R}= {YEL}{v}{R}')
        elif re.match(r'^    [a-z]+\s{2,}\S', line) and not line.lstrip().startswith(('topsstack', '-')):
            name, rest = line[4:].split(None, 1)                   # subcommand list
            out.append(f'    {MAG}{name}{R}{line[4 + len(name):len(line) - len(rest)]}{rest}')
        else:
            line = re.sub(r'\b([a-z]+\.[A-Za-z]\w*) = ([^\s),]+)', f'{CYAN}\\1{R} = {YEL}\\2{R}', _opts(line))
            out.append(re.sub(r'\((default: )([^)]*)\)', f'(\\1{YEL}\\2{R})', line))
    return '\n'.join(out)


class Colored:
    """Context manager: capture stdout, print it colorized on exit (also on SystemExit from -h)."""

    def __enter__(self):
        import io
        self.buf, self.old = io.StringIO(), sys.stdout
        self.on = enabled(self.old)
        if self.on:
            sys.stdout = self.buf
        return self

    def __exit__(self, *exc):
        if self.on:
            sys.stdout = self.old
            sys.stdout.write(colorize(self.buf.getvalue()))
            sys.stdout.flush()
        return False
