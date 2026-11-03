#!/usr/bin/env python3
"""Navigation for collage.html (plot_imgs.py): a fixed bar with one jump menu per highlight group (the colored frames),
a pair/date search box and a back-to-top button.

plot_imgs.py writes it directly. For collages made before (or by older versions), add it in place, without
re-plotting: the images, their frames and the legend are read back from the SVG.

  collage_nav.py pic/img_ion_amp/collage.html [more.html ...]
"""
import html
import json
import os
import re
import sys


NAV_CSS = """
#nav{position:fixed;top:0;left:0;right:0;z-index:10;background:#f4f4f4;border-bottom:1px solid #bbb;
     font:13px sans-serif;padding:4px 8px;display:flex;flex-wrap:wrap;gap:10px;align-items:center}
#nav select,#nav input,#nav button{font:13px sans-serif}
#nav .grp{display:flex;gap:3px;align-items:center}
#top{position:fixed;right:14px;bottom:14px;z-index:10;font:14px sans-serif;padding:6px 10px;
     border:1px solid #888;border-radius:4px;background:#fff;cursor:pointer}
#board{padding-top:44px}
.flash{animation:flash .45s 4}
@keyframes flash{50%{opacity:.15}}
"""
NAV_JS = """
const order = ORDER;
function go(name){
  const g = document.getElementById('p_' + name);
  if (!g) { alert(name + ': not in this collage'); return; }
  const r = g.getBoundingClientRect(), h = document.getElementById('nav').offsetHeight;
  window.scrollTo({top: window.scrollY + r.top - h - 40, behavior: 'smooth'});
  g.classList.remove('flash'); void g.getBoundingClientRect(); g.classList.add('flash');
}
function step(sel, d){            // previous / next entry of one group
  const n = sel.options.length - 1; if (n < 1) return;
  let i = sel.selectedIndex + d; if (i < 1) i = n; if (i > n) i = 1;
  sel.selectedIndex = i; go(sel.value);
}
function find(v){                 // a pair, or the first image containing a date
  v = v.trim().replace('-', '_'); if (!v) return;
  go(order.includes(v) ? v : (order.find(x => x.includes(v)) || v));
}
"""


def collage_html(svg, title, order, groups):
    """collage.html: the SVG plus a fixed bar with one jump menu per highlight group, a pair/date search box and a
    back-to-top button. groups: [(label, color, names in collage order)]."""
    import html, json
    sel = "".join(
        f'<span class="grp"><button onclick="step(this.nextElementSibling,-1)">&#9664;</button>'
        f'<select style="color:{c}" onchange="if(this.value)go(this.value)">'
        f'<option value="">{html.escape(lab)} ({len(n)})</option>'
        + "".join(f'<option value="{x}">{x}</option>' for x in n)
        + f'</select><button onclick="step(this.previousElementSibling,1)">&#9654;</button></span>'
        for lab, c, n in groups if n)
    nav = (f'<div id="nav"><b>{html.escape(os.path.basename(os.path.normpath(title)))}</b> {len(order)} images {sel}'
           '<span class="grp"><input id="q" list="all" size="19" placeholder="pair or date" '
           'onkeydown="if(event.key===\'Enter\')find(this.value)">'
           '<button onclick="find(document.getElementById(\'q\').value)">go</button></span>'
           '<datalist id="all">' + "".join(f'<option value="{x}">' for x in order) + '</datalist></div>'
           '<button id="top" onclick="window.scrollTo({top:0,behavior:\'smooth\'})">&#9650; top</button>')
    return ("<!DOCTYPE html><html><head><meta charset='utf-8'><title>Collage</title>"
            f"<style>body{{margin:0}}{NAV_CSS}</style></head><body>\n{nav}\n<div id='board'>\n{svg}\n</div>\n"
            f"<script>{NAV_JS.replace('ORDER', json.dumps(order))}</script>\n</body></html>\n")


ITEM = re.compile(r'<image xlink:href="([^"/]+)\.png"[^>]*/>.*?</text>', re.S)
LEGEND = re.compile(r'fill:([#\w]+);">&#9633; (.*?) \((\d+)\)</text>')


def add_nav(path):
    """Rewrite one collage.html with the navigation bar; returns (images, groups) or None if unchanged."""
    txt = open(path).read()
    i, j = txt.find('<svg'), txt.rfind('</svg>')
    if i < 0 or j < 0:
        return None
    svg = txt[txt.rfind('<?xml', 0, i) if '<?xml' in txt[:i] else i:j + 6]
    labels = {c: lab for c, lab, _ in LEGEND.findall(svg)}
    order, members = [], {}
    if '<g id="p_' not in svg:                                   # wrap each image + its frames + its label
        def wrap(m):
            name = m.group(1)
            if name.startswith('colorbar'):
                return m.group(0)
            order.append(name)
            for c in dict.fromkeys(re.findall(r'<rect fill="none" stroke="([#\w]+)"', m.group(0))):
                members.setdefault(c, []).append(name)
            return f'<g id="p_{name}">{m.group(0)}</g>'
        svg = ITEM.sub(wrap, svg)
    else:                                                        # made with ids already: just rebuild the bar
        for m in re.finditer(r'<g id="p_([^"]+)">(.*?)</g>', svg, re.S):
            order.append(m.group(1))
            for c in dict.fromkeys(re.findall(r'<rect fill="none" stroke="([#\w]+)"', m.group(2))):
                members.setdefault(c, []).append(m.group(1))
    if not order:
        return None
    groups = [(labels.get(c, c), c, n) for c, n in members.items()]
    title = os.path.basename(os.path.dirname(os.path.abspath(path)))
    open(path, 'w').write(collage_html(svg, title, order, groups))
    return len(order), [(lab, len(n)) for lab, _, n in groups]


if __name__ == '__main__':
    for p in sys.argv[1:]:
        r = add_nav(p)
        print(p, '->', r if r else 'unchanged (no images found)')
