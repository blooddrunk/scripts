#!/usr/bin/env python3
"""router-home honk：订阅一致性审计（只读）与节点入口直连规则生成。

核对「config.dae 订阅 → 组 subtag 过滤 → 节点入口直连规则 → 运行态成员」是否一致，提示
Sub-Store 里"已建未订阅"的订阅，并按订阅实际内容生成 # honk:nodes-begin/end 之间的规则段。

推荐流程（改仓库里的脱敏模板，带密钥的完整配置不进仓库）：
  python3 honk_subs.py --candidate OUT [HOST]   代入路由器上的真实值生成完整配置（0600）并审计

直接改路由器配置的流程（FILE 是完整配置的本地副本）：
  python3 honk_subs.py --patch FILE [HOST]      按订阅实际内容重写 FILE 的规则段，并审计
  python3 honk_subs.py --render [HOST]          只打印应有的规则段

只审计：
  python3 honk_subs.py [HOST]                   审计路由器上的 /etc/honk/config.dae
  python3 honk_subs.py --config FILE [HOST]     审计本地完整配置

HOST 是 ssh 别名，默认 router-home；有问题时退出码 1。对路由器只做读取：订阅 token 与 clash
secret 只在路由器侧的远端脚本里使用，不传回本机；输出只含节点名、服务器地址与端口。
"""
import argparse
import base64
import ipaddress
import json
import os
import re
import shlex
import subprocess
import sys
import time
import urllib.parse
from pathlib import Path

DEFAULT_HOST = 'router-home'
BEGIN = '# honk:nodes-begin'
END = '# honk:nodes-end'
TEMPLATE = Path(__file__).resolve().parent.parent / 'config.home.dae.example'
PLACEHOLDER = re.compile(r'203\.0\.113\.\d+|example\.top|CHANGE_ME_RANDOM')
BAR = '# ============================================================\n'
DEPLOY_HEADER = BAR + '''# router-home honk 配置 — 由 scripts 仓库 homelab/openwrt-honk/config.home.dae.example
# 经 tools/honk_subs.py --candidate 生成（%s），真实值取自路由器。
# 改动请改仓库模板后重新生成，不要直接编辑本文件。设计与踩坑记录见仓库 README。
# ============================================================
'''
UDP_SCHEMES = {'hysteria2', 'hysteria', 'tuic', 'juicity'}
NON_NODE = {'selector', 'url_test', 'fallback', 'loadbalance', 'reject', 'direct', 'compatible'}
# 在路由器上执行（busybox sh）。每个分段以 "@@KIND [arg]" 开头、"@@END" 结束；不落临时文件。
# Sub-Store 列表只取订阅名，用于提示"已建未订阅"；未被 config.dae 订阅的订阅不拉正文。
# 先取 config.dae 里的订阅；位置参数里点名、且 Sub-Store 确有的订阅也会拉取（审计本地候选时用）。
REMOTE = r'''
C=/etc/honk/config.dae
S=$(sed -n "s/^ *secret: *'\(.*\)' *$/\1/p" $C)
echo "@@CONFIG"; cat $C; echo "@@END"
echo "@@PROXIES"
curl -s -m 8 -H "Authorization: Bearer $S" http://127.0.0.1:9090/proxies
echo; echo "@@END"
BASE=$(sed -n "s|^[^#]*'\(https\{0,1\}://[^']*\)/download/[^']*'.*|\1|p" $C | head -1)
STORE=
[ -n "$BASE" ] && STORE=$(curl -s -m 20 "$BASE/api/subs")
echo "@@STORE"; printf '%s\n' "$STORE"; echo "@@END"
fetch() {
    echo "@@SUB $1"
    curl -s -m 25 -A honk-subs-audit -w '
@@CODE %{http_code}
' "$2"
    echo "@@END"
}
EXTRA="$*"
SEEN=
set -- $(sed -n '/^subscription {/,/^}/p' $C \
    | sed -n "s|^[[:space:]]*['\"]\{0,1\}\([A-Za-z0-9_.-]\{1,64\}\)['\"]\{0,1\}: *'\(https\{0,1\}://[^']*\)'.*|\1 \2|p")
while [ $# -ge 2 ]; do
    fetch "$1" "$2"
    SEEN="$SEEN $1"
    shift 2
done
NAMES=" $(printf '%s' "$STORE" | jsonfilter -e '@.data[*].name' 2>/dev/null | tr '\n' ' ')"
for n in $EXTRA; do
    case "$SEEN" in *" $n "*) continue ;; esac
    case "$NAMES" in *" $n "*) ;; *) continue ;; esac
    fetch "$n" "$BASE/download/$n"
done
'''

# 节点入口域名经路由器上的 honk DNS 解析。
RESOLVE = r'''
S=$(sed -n "s/^ *secret: *'\(.*\)' *$/\1/p" /etc/honk/config.dae)
for n in %s; do
    echo "@@DNS $n"
    curl -s -m 8 -H "Authorization: Bearer $S" "http://127.0.0.1:9090/dns/query?name=$n&type=A"
    echo; echo "@@END"
done
'''

# 一条节点入口直连规则：(dip|sip)(网段...) && l4proto(x) && (dport|sport)(端口或区间) -> direct(must)
RULE = re.compile(r'^(dip|sip)\(([^)]*)\)\s*&&\s*l4proto\((\w+)\)\s*&&\s*(dport|sport)\(([^)]*)\)'
                  r'\s*->\s*direct\(must\)$')
SECTION_STARTS = ('@@CONFIG', '@@PROXIES', '@@STORE', '@@SUB ', '@@DNS ')


def remote(host, script, args=(), timeout=150):
    """经 ssh 执行脚本并返回 stdout；args 成为脚本的位置参数。"""
    p = subprocess.run(['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=15', host, 'sh', '-s', '--', *args],
                       input=script, text=True, errors='replace', capture_output=True, timeout=timeout)
    if p.returncode != 0:
        sys.exit('ssh %s 失败（exit %d）：%s' % (host, p.returncode, p.stderr.strip()))
    return p.stdout


def sections(text):
    """把 "@@KIND arg … @@END" 分段文本解析成 [(kind, arg, body)]（保序）。"""
    out, kind, arg, buf = [], None, '', []
    for line in text.split('\n'):
        if line == '@@END':
            out.append((kind, arg, '\n'.join(buf)))
            kind, buf = None, []
        elif line.startswith(SECTION_STARTS):
            kind, _, arg = line.partition(' ')
            arg, buf = arg.strip(), []
        elif kind:
            buf.append(line)
    return out


def parse_nodes(body):
    """订阅正文 → [{'scheme','name','server','port','hop'}]；凭据字段一概不保留。"""
    s = body.strip()
    try:
        s = base64.b64decode(s + '=' * (-len(s) % 4), validate=True).decode('utf-8')
    except (ValueError, UnicodeDecodeError):
        pass
    nodes = []
    for line in s.splitlines():
        line = line.strip()
        if '://' not in line or line.startswith('#'):
            continue
        try:
            u = urllib.parse.urlsplit(line)
            q = dict(urllib.parse.parse_qsl(u.query, keep_blank_values=True))
            nodes.append({'scheme': u.scheme.lower(), 'name': urllib.parse.unquote(u.fragment),
                          'server': (u.hostname or '').lower(), 'port': u.port,
                          'hop': q.get('mport') or q.get('ports') or ''})
        except ValueError:
            continue
    return nodes


def is_ip(s):
    try:
        ipaddress.ip_address(s)
        return True
    except ValueError:
        return False


def collect(host, extra=()):
    """取回路由器配置、Sub-Store 订阅名、各订阅正文（config.dae 里的 + extra 里点名的），
    再用 honk DNS 解析节点入口域名。"""
    data = {'config': '', 'proxies': {}, 'store_names': [], 'subs': {}}
    for kind, arg, body in sections(remote(host, REMOTE, extra)):
        if kind == '@@CONFIG':
            data['config'] = body
        elif kind == '@@PROXIES' and body.strip():
            data['proxies'] = (json.loads(body) or {}).get('proxies') or {}
        elif kind == '@@STORE' and body.strip():
            data['store_names'] = [s.get('name') for s in (json.loads(body) or {}).get('data') or []]
        elif kind == '@@SUB':
            m = re.search(r'^@@CODE (\d+)$', body, re.M)
            data['subs'][arg] = {'code': m.group(1) if m else '000',
                                 'body': body[:m.start()] if m else ''}
    data['nodes'] = {tag: parse_nodes(sub['body']) for tag, sub in data['subs'].items()}
    names = sorted({n['server'] for nodes in data['nodes'].values() for n in nodes
                    if n['server'] and not is_ip(n['server'])})
    data['dns'] = {}
    if names:
        script = RESOLVE % ' '.join(shlex.quote(n) for n in names)
        for kind, arg, body in sections(remote(host, script, timeout=60)):
            if kind == '@@DNS':
                try:
                    answers = (json.loads(body) or {}).get('Answer') or []
                    data['dns'][arg] = next(a['data'] for a in answers
                                            if a.get('type') == 1 and is_ip(a['data']))
                except (StopIteration, ValueError, KeyError, TypeError):
                    data['dns'][arg] = None
    return data


def block(text, name):
    m = re.search(r'^%s \{\n(.*?)^\}' % re.escape(name), text, re.S | re.M)
    return m.group(1) if m else ''


def parse_config(text):
    """→ subscriptions [(tag, url)]、groups {name: {'filters': [...]}}（有序）、rules [(…)]]"""
    subs = []
    for line in block(text, 'subscription').splitlines():
        m = re.match(r"\s*['\"]?([\w.-]+)['\"]?\s*:\s*'(https?://[^']*)'", line)
        if m:
            subs.append((m.group(1), m.group(2)))
    groups, cur = {}, None
    for line in block(text, 'group').splitlines():
        s = line.strip()
        m = re.match(r"['\"]?([\w.-]+)['\"]?\s*\{$", s)
        if m:
            cur = m.group(1)
            groups[cur] = {'filters': []}
        elif s == '}':
            cur = None
        elif cur and s.startswith('filter:'):
            groups[cur]['filters'].append(s.split(':', 1)[1].strip())
    rules = [m.groups() for line in block(text, 'routing').splitlines() for m in [RULE.match(line.strip())] if m]
    return subs, groups, rules


def subtag_args(flt):
    m = re.match(r'^subtag\((.*)\)$', flt)
    return [a.strip().strip("'\"") for a in m.group(1).split(',')] if m else []


def has(rules, side, ip, proto, ptype, port):
    """rules 里是否有一条覆盖 (ip, proto, ptype, port)。端口写成区间时要求完全一致。"""
    for rside, nets, rproto, rpt, spec in rules:
        if (rside, rproto, rpt) != (side, proto, ptype):
            continue
        ports = [p.strip() for p in spec.split(',')]
        if port not in ports and not any(p.isdigit() and port.isdigit() and int(p) == int(port)
                                         for p in ports):
            continue
        for net in nets.split(','):
            try:
                if ipaddress.ip_address(ip) in ipaddress.ip_network(net.strip().strip("'\""), strict=False):
                    return True
            except ValueError:
                continue
    return False


def expect(node):
    """该节点需要的入口直连规则：[(说明, side, proto, ptype, port)]"""
    if node['scheme'] not in UDP_SCHEMES:
        return [('tcp %d' % node['port'], 'dip', 'tcp', 'dport', str(node['port']))]
    out = [('udp %d dip' % node['port'], 'dip', 'udp', 'dport', str(node['port'])),
           ('udp %d sip' % node['port'], 'sip', 'udp', 'sport', str(node['port']))]
    if node['hop']:
        out += [('跳跃段 %s dip' % node['hop'], 'dip', 'udp', 'dport', node['hop']),
                ('跳跃段 %s sip' % node['hop'], 'sip', 'udp', 'sport', node['hop'])]
    return out


def node_ip(node, dns):
    if is_ip(node['server']):
        return node['server']
    return dns.get(node['server'])


def render(data, config_tags):
    """按 IP 聚合 config_tags 各订阅的节点，生成 markers 之间的规则段（其它订阅一律不碰）。"""
    entries = {}
    for tag in config_tags:
        for n in data['nodes'].get(tag, []):
            ip = node_ip(n, data['dns'])
            if not ip or not n['port']:
                continue
            e = entries.setdefault(ip, {'tags': [], 'udp': set(), 'hop': set(), 'tcp': set()})
            if tag not in e['tags']:
                e['tags'].append(tag)
            if n['scheme'] in UDP_SCHEMES:
                e['udp'].add(n['port'])
                if n['hop']:
                    e['hop'].add(n['hop'])
            else:
                e['tcp'].add(n['port'])
    blocks = []
    for ip in sorted(entries, key=ipaddress.ip_address):
        e = entries[ip]
        label = [t for t in e['tags'] if t in config_tags] or e['tags']
        head = '    # %s %s' % ('/'.join(label), ip)
        if e['udp']:
            head += '（hy2 固定口 %s）' % '/'.join(str(p) for p in sorted(e['udp']))
        lines = []
        for p in sorted(e['udp']):
            lines += ['    dip(%s/32) && l4proto(udp) && dport(%d) -> direct(must)' % (ip, p),
                      '    sip(%s/32) && l4proto(udp) && sport(%d) -> direct(must)' % (ip, p)]
        for h in sorted(e['hop']):
            lines += ['    dip(%s/32) && l4proto(udp) && dport(%s) -> direct(must)' % (ip, h),
                      '    sip(%s/32) && l4proto(udp) && sport(%s) -> direct(must)' % (ip, h)]
        lines += ['    dip(%s/32) && l4proto(tcp) && dport(%d) -> direct(must)' % (ip, p)
                  for p in sorted(e['tcp'])]
        if lines:
            blocks.append('\n'.join([head] + lines))
    return '\n\n'.join(blocks) + '\n'


def patch_file(path, section):
    """用 section 重写 path 里 markers 之间的内容，返回新文本。"""
    text = open(path, encoding='utf-8').read()
    if PLACEHOLDER.search(text):
        sys.exit('%s 是脱敏模板，--patch 会把真实 IP 写进去。请在完整配置的副本上运行，'
                 '或改模板后用 --candidate 生成。' % path)
    lines = text.split('\n')
    b = [i for i, l in enumerate(lines) if l.strip() == BEGIN]
    e = [i for i, l in enumerate(lines) if l.strip() == END]
    if len(b) != 1 or len(e) != 1 or b[0] >= e[0]:
        sys.exit('%s 里找不到成对的 %s / %s 标记' % (path, BEGIN, END))
    new = '\n'.join(lines[:b[0] + 1] + section.rstrip('\n').split('\n') + lines[e[0]:])
    if new != text:
        open(path, 'w', encoding='utf-8').write(new)
        print('%s：节点入口规则段已更新，共 %d 行' % (path, len(section.split('\n')) - 1))
    return new


def candidate(data, template, out):
    """用路由器上的真实值代入脱敏模板：返回完整配置文本，并写入 out（0600）。"""
    live = data['config']
    base, token = re.search(r"'(https?://[^'/']+)/([^/']+)/download/[^']*'", live).groups()
    # 模板里的域名写法是 substore.example.top / hs.example.top：取去掉首段的后缀
    suffix = urllib.parse.urlsplit(base).hostname.split('.', 1)[1]
    secret = re.search(r"^ *secret: *'([^']+)' *$", live, re.M).group(1)
    local = re.search(r"( *# honk:local-direct-begin\n.*?\n *# honk:local-direct-end)", live, re.S).group(1)
    text, section = template, template[template.index(BEGIN):template.index(END)]
    # 节点段里每个块的首行是「# <订阅名> <占位 IP>」：按订阅实际内容把占位 IP 换成真实 IP
    for label, ph in re.findall(r'^ +# (\S+) (203\.0\.113\.\d+)', section, re.M):
        ips = {node_ip(n, data['dns']) for t in label.split('/') for n in data['nodes'].get(t, [])} - {None}
        if len(ips) != 1:
            sys.exit('模板节点段的「%s」应恰好对应一个 IP，实际解析出：%s' % (label, sorted(ips)))
        text = text.replace(ph, ips.pop())
    for a, b in (('example.top', suffix), ('TOKEN', token),
                 ('CHANGE_ME_RANDOM', secret),
                 ('    # honk:local-direct-begin\n    # honk:local-direct-end', local)):
        if a not in text:
            sys.exit('模板缺少占位符 ' + a)
        text = text.replace(a, b)
    second = text.index(BAR, text.index(BAR) + len(BAR))
    text = DEPLOY_HEADER % time.strftime('%Y-%m-%d') + text[second + len(BAR):]
    if PLACEHOLDER.search(text) or re.search(r'\bTOKEN\b', text):
        sys.exit('代入后仍有占位符残留，未写出文件')
    # 逐条核对订阅 URL：审计按订阅名取 URL，不会替这里发现写错的 URL
    for tag, url in parse_config(text)[0]:
        if tag in data['subs'] and url != '%s/%s/download/%s' % (base, token, tag):
            sys.exit('订阅「%s」的 URL 与路由器上实际可用的不一致，未写出文件' % tag)
    fd = os.open(out, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as f:
        f.write(text)
    return text


def audit(data, text, live, label):
    subs, groups, rules = parse_config(text)
    config_tags = [t for t, _ in subs]
    names_of = {tag: [n['name'] for n in nodes] for tag, nodes in data['nodes'].items()}
    problems, hints = [], []

    print('honk 订阅审计：%s' % label)
    for tag in config_tags:
        sub = data['subs'].get(tag)
        if sub is None:
            continue
        nodes = data['nodes'].get(tag, [])
        refs = [g for g, v in groups.items() if any(tag in subtag_args(f) for f in v['filters'])]
        print('\n[%s]  HTTP %s，%d 个节点' % (tag, sub['code'], len(nodes)))
        for n in nodes:
            ip = node_ip(n, data['dns'])
            if ip is None:
                miss = ['服务器地址未解析出 IPv4']
            else:
                miss = ['%s(%s)' % (desc, side) for desc, side, proto, ptype, port in expect(n)
                        if not has(rules, side, ip, proto, ptype, port)]
            print('  %-9s %-40s %s:%s%s  %s' % (
                n['scheme'], n['name'], ip or n['server'] + '→?', n['port'],
                '  跳跃 ' + n['hop'] if n['hop'] else '',
                '入口直连 OK' if not miss else '缺规则: ' + '，'.join(miss)))
            if tag in config_tags and miss:
                problems.append('[%s] 节点 %s 缺入口直连规则：%s' % (tag, n['name'], '，'.join(miss)))
        if not nodes:
            problems.append('[%s] 已订阅但拉取不到节点（HTTP %s）' % (tag, sub['code']))
        if not refs:
            problems.append('[%s] 已订阅，但没有任何组的 filter 引用它，节点进不了池' % tag)
        print('  组引用: %s' % (', '.join(refs) if refs else '(无)'))
        for g in refs:
            actual = set((data['proxies'].get(g) or {}).get('all') or [])
            want, missing = set(names_of.get(tag, [])), set(names_of.get(tag, [])) - actual
            if not live:
                continue
            print('  运行态: %s 含本订阅 %d/%d 个节点' % (g, len(want & actual), len(want)))
            if missing:
                problems.append('[%s] 组 %s 的运行态成员缺 %s（filter 未命中，或订阅尚未刷新）'
                                % (tag, g, '，'.join(sorted(missing))))
    for tag in config_tags:
        if tag not in data['subs']:
            problems.append('[%s] 已写入订阅段，但 Sub-Store 里没有同名订阅' % tag)
    for g, v in groups.items():
        if not v['filters']:
            hints.append('组 %s 没有 filter，会接收全部节点' % g)
        for f in v['filters']:
            for t in subtag_args(f):
                if ':' not in t and t not in config_tags:
                    problems.append('组 %s 的 filter 引用了未订阅的 tag「%s」' % (g, t))
    rest = [n for n in data['store_names'] if n not in config_tags]
    if rest:
        hints.append('Sub-Store 中未被订阅: %s' % '，'.join(rest))
    if live and data['proxies']:
        stale = {n for n, p in data['proxies'].items() if str(p.get('type', '')).lower() not in NON_NODE} \
            - {n for names in names_of.values() for n in names}
        if stale:
            hints.append('运行态残留节点（订阅已不含，刷新或重载后消失）: %s' % '，'.join(sorted(stale)))
    for h in hints:
        print('\n提示: ' + h)
    print('\n结论: %s' % ('无问题' if not problems else '%d 个问题' % len(problems)))
    for i, p in enumerate(problems, 1):
        print('  %d. %s' % (i, p))
    return problems


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('host', nargs='?', default=DEFAULT_HOST, help='ssh 别名，默认 ' + DEFAULT_HOST)
    ap.add_argument('--candidate', metavar='OUT',
                    help='代入路由器上的真实值，把 %s 生成完整配置写到 OUT（0600）并审计' % TEMPLATE.name)
    ap.add_argument('--config', metavar='FILE', help='审计本地完整配置（默认审计路由器上的 config.dae）')
    ap.add_argument('--patch', metavar='FILE', help='按订阅实际内容重写 FILE 的节点入口规则段，并审计')
    ap.add_argument('--render', action='store_true', help='只打印应有的节点入口规则段')
    a = ap.parse_args()
    path = str(TEMPLATE) if a.candidate else a.patch or a.config
    text = open(path, encoding='utf-8').read() if path else None
    # 本地文件里点名的订阅也一并拉取（候选里的新 VPS 还不在路由器配置里）
    data = collect(a.host, [t for t, _ in parse_config(text)[0]] if text else ())
    if a.candidate:
        text = candidate(data, text, a.candidate)
        print('已写入 %s（0600）' % a.candidate)
        problems = audit(data, text, False, '%s → %s' % (TEMPLATE.name, a.candidate))
    elif a.patch or a.render:
        tags = [t for t, _ in parse_config(text or data['config'])[0]]
        section = render(data, tags)
        if a.render:
            sys.stdout.write(section)
        problems = audit(data, patch_file(path, section), False, path) if a.patch else []
    else:
        problems = audit(data, text or data['config'], not text,
                         path or '%s:/etc/honk/config.dae' % a.host)
    sys.exit(1 if problems else 0)


if __name__ == '__main__':
    main()
