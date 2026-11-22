#!/usr/bin/env python3
"""SmartPOS UI audit - catches the layout bugs that broke pages before.
Run:  python tools/ui_audit.py        (exit code 1 on ERROR; also runs inside `manage.py test`)
ERRORS: unbalanced tags inside {% block content %}; overflow-wrap:anywhere / break-all on td/th;
        toast container moved with translateX(50%).
WARNINGS: tables with no scroll wrapper, unprefixed grid-cols-3+, big fixed px widths.
"""
import glob, os, re, sys
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TPL = sorted(glob.glob(os.path.join(ROOT, 'templates', '**', '*.html'), recursive=True))
STATIC = sorted(glob.glob(os.path.join(ROOT, 'static', '**', '*.css'), recursive=True))
BLOCK = re.compile(r'<(/?)(div|section|header|form|table|ul|ol|details|nav|article|aside)\b[^>]*?(/?)>', re.S)

def blank(m): return '\n' * m.group(0).count('\n')
def rel(p): return os.path.relpath(p, ROOT)

def balance(path, s):
    s = re.sub(r'<style.*?</style>|<script.*?</script>|<!--.*?-->', blank, s, flags=re.S)
    a = s.find('{% block content %}')
    if a < 0: return []
    m = re.search(r'{% endblock( content)? %}', s[a + 10:])
    b = a + 10 + m.start() if m else len(s)
    stack, errs = [], []
    for t in BLOCK.finditer(s, a, b):
        if t.group(3): continue
        line = s.count('\n', 0, t.start()) + 1
        if not t.group(1): stack.append((t.group(2), line)); continue
        if not stack: errs.append(f'{rel(path)}:{line}: extra </{t.group(2)}> closes the page layout early'); break
        if stack[-1][0] == t.group(2): stack.pop()
        else:
            errs.append(f'{rel(path)}:{line}: </{t.group(2)}> does not match <{stack[-1][0]}> opened at line {stack[-1][1]}'); break
    if not errs and stack and not path.endswith('auth_base.html'):
        errs.append(f'{rel(path)}:{stack[-1][1]}: <{stack[-1][0]}> is never closed')
    return errs

def run():
    errors, warns = [], []
    for p in TPL + STATIC:
        s = open(p, encoding='utf8').read()
        if p.endswith('.html'):
            errors += balance(p, s)
        for m in re.finditer(r'([^{}]*\b(?:td|th)\b[^{}]*)\{[^}]*(overflow-wrap:\s*anywhere|word-break:\s*break-all)', s):
            if 'NOTE:' in m.group(1): continue
            errors.append(f'{rel(p)}: "{m.group(2)}" on td/th collapses table columns on phones (use break-word)')
        if re.search(r'toast-host[^{}]*\{[^}]*translateX\(50%\)(?![^}]*!important)', s) and 'V43' not in s:
            errors.append(f'{rel(p)}: toast container shifted with translateX(50%) - alerts get cut off on phones')
        if not p.endswith('.html'): continue
        for m in re.finditer(r'<table\b', s):
            if not re.search(r'overflow-x-auto|overflow-auto|sp-table-scroll|pc-table-keep', s[max(0, m.start() - 400):m.start()]):
                warns.append(f'{rel(p)}:{s.count(chr(10), 0, m.start()) + 1}: table without scroll wrapper (base.html JS wraps it at runtime)')
        for m in re.finditer(r'class="([^"]*)"', s):
            c = m.group(1)
            if re.search(r'(?<![:\w-])grid-cols-([3-9]|1[0-2])\b', c) and 'gap' in c:
                warns.append(f'{rel(p)}:{s.count(chr(10), 0, m.start()) + 1}: unprefixed grid-cols-N+ (check it on a 360px phone)')
    return errors, warns

if __name__ == '__main__':
    e, w = run()
    for x in e: print('ERROR  ', x)
    for x in w[:60]: print('warning', x)
    print(f'\n{len(e)} error(s), {len(w)} warning(s)')
    sys.exit(1 if e else 0)
