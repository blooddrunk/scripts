#!/usr/bin/env python3
"""honk 组策略实验（一次性、完全隔离）：用假节点验证配置里"首选 + 兜底"的行为。

用途：改了 group 结构，或升级 honk 之后，确认这些行为没变——
首选 VPS 下线后流量顺延到下一个、首选恢复后自动切回、配额脚本切换后仍有兜底。

做法：在 `unshare -Urn` 的私有用户/网络命名空间里起一个 honk-core（--mock-ebpf，不拦截任何
流量），三个本地的假 SOCKS5 节点模拟 VPS，通过"DNS 上游钉到某个组"驱动真实选路
（dns/upstream_pool/routing.rs 的 select_group_leaf_for_target 与生产流量同一函数），
出口以 honk 自己的 debug 日志为准（它会复用已建好的连接，数新连接有盲区）。

准备（只需一次）：
  1. 从 https://github.com/daeuniverse/honk/releases 下载与路由器上同版本的
     honk-core-<版本>-x86_64-unknown-linux-musl-full.tar.gz 和 SHA256SUMS，核对后解压。
  2. 系统需要允许普通用户创建用户/网络命名空间（unshare -Urn）。

用法（工作目录任意，会新建 lab/run-<变体> 目录）：
  unshare -Urn sh -c "ip link set lo up; python3 honk_group_lab.py <honk-core> <工作目录> <变体>"

变体：
  design    本文档顶部的标准结构：zh(→us) / us(→jp) / jp + proxy(score) + premium(固定 zh) + doh(→zh)
  real      跑某份真实配置里原样的 group 段。需设环境变量 LAB_GROUPS 指向一个文件，内容是该配置
            的 group 段，并把 filter: subtag(...) 预先换成 filter: name(zh1|us1|jp1)：
              sed -n '/^group {/,/^}/p' config.dae | sed -e 's/subtag(lightlayer-zh)/name(zh1)/' \
                  -e 's/subtag(raksmart)/name(us1)/' -e '/subtag(lightlayer)/d' -e 's/subtag(bitsflow)/name(jp1)/' > groups.dae
            LAB_GROUPS=groups.dae unshare -Urn sh -c "ip link set lo up; python3 honk_group_lab.py <honk-core> <工作目录> real"
  nofinal   对照：不配 final，首选下线后应当无路可走
  fallback  对照：premium 用 fallback 策略，首选恢复后不切回
  badfinal  启动校验应当拒绝 final 指向不存在的组
  cycle     只报告现象：成环的 final 启动时不会被拒绝（拒绝发生在运行时选路），不计入通过/失败

环境变量：LAB_WARMUP=<秒> 注入故障前的等待，默认 75（节点注册后有 60 秒宽限期）。

已知局限：实验用的 DNS 查询路径不会给 honk 的健康判定提供"真实流量失败"的反馈，
所以量到的切换时间（约 33 秒）是"纯靠常规健康检查"的节奏，线上有人在用时会更快。
"""
import asyncio
import datetime
import json
import os
import re
import random
import struct
import subprocess
import sys
import time
import urllib.error
import urllib.request

BIN, WORK, VARIANT = sys.argv[1], sys.argv[2], sys.argv[3]
RUN = os.path.join(WORK, 'lab', 'run-' + VARIANT)
os.makedirs(RUN, exist_ok=True)
API = 'http://127.0.0.1:19090'
HC = 8081                                   # 健康检查目标（HTTP 204）
DNSBIND = 1053                              # honk 的独立 DNS 监听
INTERVAL = 1                              # 健康检查间隔；失败后按 2 倍、4 倍退避
# 假 VPS：组名 -> 本机 SOCKS5 端口；每组对应一个 TCP DNS 上游端口，用来区分"经哪个组转发"
NODE = {'zh1': 1081, 'us1': 1082, 'jp1': 1083}
GROUP_PORT = {'zh': 1081, 'us': 1082, 'jp': 1083}
DNSD = {'zh': 8085, 'premium': 8082, 'proxy': 8083, 'doh': 8084}
# zh 组在候选配置里的组名是 lightlayer-zh；real 变体用它，其余变体用 zh
ZH = 'lightlayer-zh' if VARIANT == 'real' else 'zh'
LOG = []                                    # (时间, 种类, 标签, 详情)
RESULTS = []


def log(kind, tag, detail=''):
    LOG.append((time.time(), kind, tag, detail))


def via(node, target, since):
    """since 之后经假节点 node 成功建连、目标是 target 的次数。"""
    return sum(1 for t, k, n, d in LOG if t > since and k == 'socks' and n == node and d == target)


# ---------------- 配置 ----------------

def config_text():
    finals = {'zh': 'us', 'us': 'jp', 'doh': 'zh'}
    premium = ['        policy: fixed', '        default: zh']
    if VARIANT == 'nofinal':
        finals = {}
    elif VARIANT == 'cycle':
        finals = {'zh': 'us', 'us': 'zh'}
    elif VARIANT == 'badfinal':
        finals = {'zh': 'nosuch'}
    elif VARIANT == 'fallback':
        finals = {}
        premium = ['        policy: fallback']

    def group(name, policy, members):
        lines = ['    %s {' % name]
        for m in members:
            lines.append('        filter: %s' % ('name(%s)' % m if m in NODE else 'group(%s)' % m))
        lines.append('        policy: %s' % policy)
        if policy == 'min_moving_avg':
            lines.append('        tolerance: 50')
        if name in finals:
            lines.append('        final: %s' % finals[name])
        return '\n'.join(lines + ['    }'])

    upstreams = '\n'.join("        via%s: 'tcp://127.0.0.1:%d' -> %s" % (g, p, ZH if g == 'zh' else g)
                          for g, p in DNSD.items())
    rules = '\n'.join('            qname(keyword: x-%s-x) -> via%s' % (g, g) for g in DNSD)
    return '\n'.join([
        'global {',
        '    wan_interface: auto',
        "    log_level: 'debug'",      # 出口判定读的就是 honk 的 debug 日志，所有变体都打开
        '    dial_mode: domain',
        "    data_dir: '%s'" % os.path.join(RUN, 'data'),
        "    tcp_check_url: 'http://127.0.0.1:%d/'" % HC,
        '    tcp_check_http_method: HEAD',
        "    udp_check_dns: '127.0.0.1:5353'",
        '    check_interval: %ds' % INTERVAL,
        '    check_tolerance: 50ms',
        '    store_subscribe: false',
        '    nfqueue_enable: false',
        '}',
        'experimental {',
        '    clash_api {',
        "        external_controller: '127.0.0.1:19090'",
        '        default_mode: Rule',
        '    }',
        '}',
        'node {',
        "    zh1: 'socks5://127.0.0.1:1081'",
        "    us1: 'socks5://127.0.0.1:1082'",
        "    jp1: 'socks5://127.0.0.1:1083'",
        '}',
        'group {',
        real_groups() if VARIANT == 'real' else '\n'.join([
            group('zh', 'min_moving_avg', ['zh1']),
            group('us', 'min_moving_avg', ['us1']),
            group('jp', 'min_moving_avg', ['jp1']),
            group('proxy', 'score', ['us', 'jp']),
            group('premium', 'fixed', ['zh', 'us', 'jp']).replace('        policy: fixed', '\n'.join(premium)),
            group('doh', 'min_moving_avg', ['us', 'jp']),
        ]),
        '}',
        'dns {',
        "    bind: 'tcp+udp://127.0.0.1:%d'" % DNSBIND,
        '    upstream {', upstreams, '    }',
        '    routing {',
        '        request {', rules, '            fallback: reject', '        }',
        '    }',
        '}',
        'routing {',
        '    fallback: proxy',
        '}',
        '',
    ])


def real_groups():
    """LAB_GROUPS 指向的文件：某份真实配置的 group 段，只保留花括号内的内容。"""
    text = open(os.environ['LAB_GROUPS'], encoding='utf-8').read().strip()
    return text[len('group {'):text.rindex('}')].strip('\n')


# ---------------- 假节点与测试服务 ----------------

async def pump(r, w):
    try:
        while True:
            data = await r.read(65536)
            if not data:
                break
            w.write(data)
            await w.drain()
    except Exception:  # noqa: BLE001
        pass
    finally:
        w.close()


class Socks:
    """无鉴权、仅 CONNECT 的 SOCKS5；每次成功建连记一条日志。"""

    def __init__(self, node, port):
        self.node, self.port, self.server = node, port, None
        self.writers = set()

    async def start(self):
        self.server = await asyncio.start_server(self.handle, '127.0.0.1', self.port)

    def stop(self):
        """整机下线：停止接受新连接，并断开所有已建立的连接。"""
        self.server.close()
        self.server = None
        for w in list(self.writers):
            w.close()
        self.writers.clear()

    async def handle(self, reader, writer):
        self.writers.add(writer)
        try:
            ver, n = await reader.readexactly(2)
            await reader.readexactly(n)
            writer.write(b'\x05\x00')
            await writer.drain()
            _, cmd, _, atyp = await reader.readexactly(4)
            if atyp == 3:
                host = (await reader.readexactly((await reader.readexactly(1))[0])).decode()
            else:
                host = '.'.join(str(b) for b in await reader.readexactly(4))
            target = '%s:%d' % (host, struct.unpack('>H', await reader.readexactly(2))[0])
            ur, uw = await asyncio.open_connection(host, port_of(target))
            log('socks', self.node, target)
            writer.write(b'\x05\x00\x00\x01' + b'\x00' * 6)
            await writer.drain()
            await asyncio.gather(pump(reader, uw), pump(ur, writer))
        except (asyncio.IncompleteReadError, ConnectionError, OSError):
            pass
        finally:
            self.writers.discard(writer)
            writer.close()


def port_of(target):
    return int(target.rsplit(':', 1)[1])


async def http_hc(reader, writer):
    try:
        await asyncio.wait_for(reader.read(4096), 3)
        writer.write(b'HTTP/1.1 204 No Content\r\nContent-Length: 0\r\nConnection: close\r\n\r\n')
        await writer.drain()
    except Exception:  # noqa: BLE001
        pass
    finally:
        writer.close()


def parse_qname(buf):
    labels, i = [], 0
    while i < len(buf) and buf[i]:
        labels.append(buf[i + 1:i + 1 + buf[i]].decode(errors='replace'))
        i += buf[i] + 1
    return '.'.join(labels)


async def dns_tcp(group, reader, writer):
    """每组一个 TCP DNS 上游：记下查询名，回一个空应答，然后断开。
    不复用连接，这样每条查询都会新建一条经假节点的连接，出口统计才完整。"""
    try:
        ln = struct.unpack('>H', await reader.readexactly(2))[0]
        q = await reader.readexactly(ln)
        log('dns', group, parse_qname(q[12:]))
        resp = struct.pack('>HHHHH', 0x8180, 1, 0, 0, 0) + q[12:]   # flags + counts + question
        writer.write(struct.pack('>H', len(resp) + 2) + q[:2] + resp)
        await writer.drain()
    except (asyncio.IncompleteReadError, ConnectionError, OSError):
        pass
    finally:
        writer.close()


def parse_qname(buf):
    labels, i = [], 0
    while i < len(buf) and buf[i]:
        labels.append(buf[i + 1:i + 1 + buf[i]].decode(errors='replace'))
        i += buf[i] + 1
    return '.'.join(labels)


async def dns_tcp(group, reader, writer):
    """每组一个 TCP DNS 上游：记下收到的查询名，并回一个空应答。"""
    try:
        while True:
            ln = struct.unpack('>H', await reader.readexactly(2))[0]
            q = await reader.readexactly(ln)
            log('dns', group, parse_qname(q[12:]))
            resp = struct.pack('>HHHHH', 0x8180, 1, 0, 0, 0) + q[12:]   # flags + counts + question
            writer.write(struct.pack('>H', len(resp) + 2) + q[:2] + resp)   # 长度 = ID + 其余部分
            await writer.drain()
    except (asyncio.IncompleteReadError, ConnectionError, OSError):
        pass
    finally:
        writer.close()


# ---------------- 驱动 ----------------

def api(path, method='GET', body=None):
    req = urllib.request.Request(API + path, method=method, data=body)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()
    except Exception as e:  # noqa: BLE001
        return 0, str(e)


def now(name):
    try:
        return str(json.loads(api('/proxies/' + name)[1]).get('now'))
    except ValueError:
        return '?'


async def query(group):
    """经 honk 的 DNS 监听发一条查询，request 规则会把它交给钉在 group 上的上游。"""
    name = 'x-%s-x.%d.test.' % ('zh' if group == ZH else group, random.randrange(10 ** 9))
    qid = random.randrange(65536)
    q = struct.pack('>HHHHHH', qid, 0x0100, 1, 0, 0, 0)
    q += b''.join(bytes([len(l)]) + l.encode() for l in name.split('.') if l) + b'\x00'
    q += struct.pack('>HH', 1, 1)
    r, w = await asyncio.open_connection('127.0.0.1', DNSBIND)
    w.write(struct.pack('>H', len(q)) + q)          # DNS over TCP：2 字节长度前缀
    await w.drain()
    async def read_response():
        head = await r.readexactly(2)
        return head + await r.readexactly(struct.unpack('>H', head)[0])
    try:
        data = await asyncio.wait_for(read_response(), float(os.environ.get('LAB_TIMEOUT', '9')))
    except (asyncio.TimeoutError, asyncio.IncompleteReadError, ConnectionError, OSError):
        data = b''
    w.close()
    # 必须是 NOERROR：SERVFAIL 也带同一个 ID，不算成功
    return len(data) >= 14 and data[2:4] == q[:2] and (data[5] & 0x0F) == 0, name


LOGPATH = os.path.join(RUN, 'honk.log')


def leaves_since(group, since):
    """honk 自己的 debug 日志里，since 之后为「经 group 转发」的查询选过的出口节点。
    以它为准而不是数假节点的新连接：honk 会复用已建好的连接，后者有盲区。"""
    pat = re.compile(r'DNS dial leaf \(forced -> %s\): Some\("(\w+)"\)' % re.escape(group))
    out = []
    try:
        with open(LOGPATH, encoding='utf-8', errors='replace') as f:
            for line in f:
                if 'DNS dial leaf' not in line:
                    continue
                m = re.match(r'(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d+)', line)
                if not m or datetime.datetime.fromisoformat(m.group(1)).timestamp() < since:
                    continue
                hit = pat.search(line)
                if hit:
                    out.append(hit.group(1))
    except OSError:
        pass
    return out


async def check(label, group, expect, expect_fail=False):
    """经 group 发一条查询。expect 是允许的出口假节点；expect_fail 表示应当无路可走。"""
    await asyncio.sleep(0.5)
    since = time.time() - 0.05
    t0 = time.time()
    answered, name = await query(group)
    elapsed = time.time() - t0
    await asyncio.sleep(0.7)
    leaves = set(leaves_since(group, since))
    passed = ((not answered and leaves <= set(expect)) if expect_fail
              else (answered and bool(leaves) and leaves <= set(expect)))
    RESULTS.append(passed)
    print('%s  %s\n        应答=%s(%.1fs) honk 选的出口=%s 期望=%s | now: premium=%s zh=%s us=%s jp=%s'
          % ('PASS' if passed else 'FAIL', label, answered, elapsed, sorted(leaves),
             '失败（无兜底）' if expect_fail else sorted(expect),
             now('premium'), now(ZH), now('us'), now('jp')), flush=True)


async def wait_alive(nodes):
    """等健康检查确认这些假节点活着：最近一个周期内经它访问过健康检查目标。"""
    target = '127.0.0.1:%d' % HC
    for _ in range(80):
        t = time.time()
        if all(via(n, target, t - INTERVAL - 1.5) > 0 for n in nodes):
            return True
        await asyncio.sleep(0.5)
    return False


def marked_dead_at(node, since):
    """honk 日志里，节点自 since 之后第一次被判死时已经过的时间（秒）；没有则返回 None。"""
    pat = re.compile(r"Node '%s' marked dead \(domain=Tcp" % re.escape(node))
    try:
        with open(LOGPATH, encoding='utf-8', errors='replace') as f:
            for line in f:
                m = re.match(r'(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d+)', line)
                if m and pat.search(line):
                    t = datetime.datetime.fromisoformat(m.group(1)).timestamp()
                    if t >= since:
                        return t - since
    except OSError:
        pass
    return None


async def wait_revived(node, since, limit=90):
    """等 honk 自己记下「节点复活」：恢复后要连续两次检查成功，且判死越久检查间隔退避越长。"""
    pat = re.compile(r"Node '%s' revived \(domain=Tcp" % re.escape(node))
    end = time.time() + limit
    while time.time() < end:
        try:
            with open(LOGPATH, encoding='utf-8', errors='replace') as f:
                for line in f:
                    m = re.match(r'(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d+)', line)
                    if m and datetime.datetime.fromisoformat(m.group(1)).timestamp() >= since and pat.search(line):
                        return True
        except OSError:
            pass
        await asyncio.sleep(1)
    return False


async def settle():
    """停/起节点后等健康状态稳定：够常规检查失败三次（间隔 ×2、×4 退避）或成功两次。"""
    await asyncio.sleep(INTERVAL * 7 + 9)


async def failover_time(label, group, expect, give_up=70):
    """节点刚下线就开始持续发查询（模拟有人在用），量出流量切到别处用了多久。
    注意：DNS 上游这条路不给健康判定提供反馈，量到的是「纯靠常规健康检查」的节奏。"""
    t0 = time.time()
    answered, leaves, attempts, ok_count = False, set(), 0, 0
    while time.time() - t0 < give_up:
        since = time.time() - 0.05
        answered, _ = await query(group)
        attempts += 1
        ok_count += 1 if answered else 0
        await asyncio.sleep(0.7)
        leaves = set(leaves_since(group, since))
        if expect is not None and answered and leaves <= set(expect):
            break
        if expect is None and time.time() - t0 > give_up - 10:
            break
        await asyncio.sleep(2.0)
    elapsed = time.time() - t0
    if expect is None:      # 期望：节点被判死之后始终没有可用的兜底
        dead_at = marked_dead_at('zh1', t0)
        passed = dead_at is not None and ok_count == 0
        RESULTS.append(passed)
        print('%s  %s\n        %.0f 秒内共 %d 次查询，成功 %d 次；zh1 在第 %.0f 秒被判死（最后一次 honk 选的出口=%s）'
              % ('PASS' if passed else 'FAIL', label, elapsed, attempts, ok_count, dead_at or -1, sorted(leaves)), flush=True)
        return
    passed = answered and leaves <= set(expect)
    RESULTS.append(passed)
    print('%s  %s\n        %.1f 秒后恢复，honk 选的出口=%s 期望=%s'
          % ('PASS' if passed else 'FAIL', label, elapsed, sorted(leaves), sorted(expect)), flush=True)


async def main():
    cfg = os.path.join(RUN, 'config.dae')
    open(cfg, 'w').write(config_text())
    socks = {n: Socks(n, p) for n, p in NODE.items()}
    for s in socks.values():
        await s.start()
    await asyncio.start_server(http_hc, '127.0.0.1', HC)
    for g, p in DNSD.items():
        await asyncio.start_server(lambda r, w, g=g: dns_tcp(g, r, w), '127.0.0.1', p)
    logf = open(os.path.join(RUN, 'honk.log'), 'w')
    proc = subprocess.Popen([BIN, '--config', cfg, '--mock-ebpf', '--data-dir', os.path.join(RUN, 'data')],
                            stdout=logf, stderr=subprocess.STDOUT)
    try:
        for _ in range(40):
            await asyncio.sleep(0.3)
            if proc.poll() is not None or api('/version')[0] == 200:
                break
        if VARIANT == 'cycle':
            # 文档说"成环的 final 会拒绝"，实测那是运行时选路时的事，启动校验并不拒绝。
            # 这里只报告现象，不计入通过/失败。
            await asyncio.sleep(3)
            print('INFO  variant=cycle：honk-core 启动%s了成环的 final 配置（拒绝发生在运行时选路）'
                  % ('接受' if proc.poll() is None else '拒绝'))
            return
        if VARIANT == 'badfinal':
            await asyncio.sleep(3)
            rejected = proc.poll() is not None
            RESULTS.append(rejected)
            print('%s  variant=badfinal：指向不存在的组的 final，honk-core %s'
                  % ('PASS' if rejected else 'FAIL', '拒绝了这份配置（未启动）' if rejected else '竟然接受了这份配置'))
            return
        if proc.poll() is not None:
            print('honk-core 启动失败，exit=%d' % proc.returncode)
            return
        print('健康检查确认三个假节点都活着: %s' % await wait_alive(list(NODE)), flush=True)
        warmup = float(os.environ.get('LAB_WARMUP', '75'))
        print('等待 %.0f 秒（节点注册后有 60 秒宽限期，期间失败不计入判死）...' % warmup, flush=True)
        await asyncio.sleep(warmup)
        await check('T0 首选 zh：AI 流量经 zh 出去', 'premium', ['zh1'])
        if VARIANT in ('design', 'fallback', 'real'):
            await check('T0 日常流量走 raksmart 或 bitsflow', 'proxy', ['us1', 'jp1'])
        if VARIANT in ('design', 'real'):
            await check('T0 DNS 通道走 raksmart 或 bitsflow', 'doh', ['us1', 'jp1'])
        socks['zh1'].stop()
        if VARIANT == 'nofinal':
            await failover_time('对照（不配 final）：zh 下线后 AI 流量应当无路可走', 'premium', None)
            return
        await failover_time('T1 zh 下线后 AI 流量切到 raksmart 用时', 'premium', ['us1'])
        if VARIANT in ('design', 'real'):
            await check('T1 zh 组自己经 final 兜底到 raksmart', ZH, ['us1'])
            await check('T1 日常流量不受影响', 'proxy', ['us1', 'jp1'])
            socks['us1'].stop()
            await failover_time('T2 raksmart 也下线：AI 流量切到 bitsflow 用时', 'premium', ['jp1'])
            await settle()
            await check('T2 日常流量只剩 bitsflow', 'proxy', ['jp1'])
            await check('T2 DNS 通道的兜底链也落到 bitsflow', 'doh', ['jp1'])
            t = time.time()
            await socks['zh1'].start()
            print('zh 恢复，honk 记下复活: %s（%.0f 秒）' % ((lambda ok: (ok, time.time() - t))(await wait_revived('zh1', t))), flush=True)
            await check('T3 zh 恢复后 AI 流量自动回到 zh', 'premium', ['zh1'])
            await check('T3 DNS 通道：正常成员优先于兜底（此刻只剩 bitsflow）', 'doh', ['jp1'])
            t = time.time()
            await socks['us1'].start()
            print('raksmart 恢复，honk 记下复活: %s（%.0f 秒）' % ((lambda ok: (ok, time.time() - t))(await wait_revived('us1', t))), flush=True)
            await check('T4 全部恢复后 AI 流量在 zh', 'premium', ['zh1'])
            await check('T4 日常流量重新在 raksmart / bitsflow 之间选', 'proxy', ['us1', 'jp1'])
            print('        模拟配额脚本：PUT premium -> us: HTTP %s' % api('/proxies/premium', 'PUT', b'{"name":"us"}')[0])
            await check('T5 切到 us 后 AI 流量经 raksmart', 'premium', ['us1'])
            socks['us1'].stop()
            await failover_time('T5 此时 raksmart 下线：仍然兜底到 bitsflow 用时', 'premium', ['jp1'])
            t = time.time()
            await socks['us1'].start()
            print('raksmart 恢复，honk 记下复活: %s（%.0f 秒）' % ((lambda ok: (ok, time.time() - t))(await wait_revived('us1', t))), flush=True)
            print('        PUT premium -> zh: HTTP %s' % api('/proxies/premium', 'PUT', ('{"name":"%s"}' % ZH).encode())[0])
            await check('T5 切回 zh', 'premium', ['zh1'])
        if VARIANT == 'fallback':
            t = time.time()
            await socks['zh1'].start()
            print('zh 恢复，honk 记下复活: %s' % await wait_revived('zh1', t), flush=True)
            await asyncio.sleep(12)
            await check('对照（fallback 策略）：zh 恢复后 AI 流量是否切回 zh', 'premium', ['zh1'])
    finally:
        proc.terminate()
        try:
            proc.wait(5)
        except Exception:  # noqa: BLE001
            proc.kill()
        logf.close()
    print('\n结果: %d/%d 通过' % (sum(RESULTS), len(RESULTS)))


if __name__ == '__main__':
    asyncio.run(main())
