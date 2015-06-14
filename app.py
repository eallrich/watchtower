import platform
import os
import random
import subprocess
import time

import psutil
from statsd import StatsClient

host = platform.node().replace('.', '_')

statsd = StatsClient('localhost', 48125, prefix=host)

def ns(*args):
    """Create a metric key from the given strings"""
    return ".".join(args)


def loadavg():
    keys = ['1m', '5m', '15m']
    values = os.getloadavg()
    
    metrics = {ns('loadavg', k): v for k, v in zip(keys, values)}
    
    for metric, value in metrics.items():
        statsd.gauge(metric, value)


def ram():
    with open('/proc/meminfo', 'r') as f:
        lines = f.readlines()

    memory_stats = {}
    keys = ('MemTotal', 'MemFree', 'Buffers', 'Cached', 'SwapTotal', 'SwapFree')
    for line in lines:
        name, data = line.split(':')
        if name in keys:
            value, suffix = data.strip().split(' ')
            memory_stats[name] = float(value) * 1024

    buffer = memory_stats['Buffers']
    buffer_pct = (buffer / memory_stats['MemTotal']) * 100
    cache = memory_stats['Cached']
    cache_pct = (cache / memory_stats['MemTotal']) * 100
    rss = memory_stats['MemTotal'] - memory_stats['MemFree'] - buffer - cache
    rss_pct = (rss / memory_stats['MemTotal']) * 100
    ram_used = buffer + cache + rss
    ram_used_pct = (ram_used / memory_stats['MemTotal']) * 100

    keys = ['buffer', 'buffer_pct', 'cache', 'cache_pct', 'rss', 'rss_pct', 'ram_used', 'ram_used_pct']
    values = [buffer, buffer_pct, cache, cache_pct, rss, rss_pct, ram_used, ram_used_pct]

    if 'SwapTotal' in memory_stats and memory_stats['SwapTotal'] > 0:
        # Not every host has swap defined. If no swap, don't report it. Since
        # 'total' is just RAM plus swap, don't report that either (it's the
        # same 'ram_used')
        swap = memory_stats['SwapTotal'] - memory_stats['SwapFree']
        swap_pct = (swap / memory_stats['SwapTotal']) * 100
        total_used = buffer + cache + rss + swap
        total_used_pct = (total_used / (memory_stats['MemTotal'] + memory_stats['SwapTotal'])) * 100

        keys.extend(['swap', 'swap_pct', 'total_used', 'total_used_pct'])
        values.extend([swap, swap_pct, total_used, total_used_pct])

    metrics = {ns('memory', k): v for k, v in zip(keys, values)}

    with statsd.pipeline() as pipe:
        for metric, value in metrics.items():
            pipe.gauge(metric, value)


network_fields = (
    'bytes_sent',
    'bytes_recv',
    'packets_sent',
    'packets_recv',
    'errin',
    'errout',
    'dropin',
    'dropout',
)
def network():
    metrics = {}
    for nic_name, details in psutil.net_io_counters(pernic=True).items():
        metrics.update({ns('network', nic_name, k): v for k, v in zip(network_fields, details)})

    with statsd.pipeline() as pipe:
        for metric, value in metrics.items():
            pipe.gauge(metric, value)


def os_status():
    # Unlike most stats, we don't need up-to-the-second reports on these.
    # We're going to sample the data approximately once every ten cycles.
    if random.random() > 0.1:
        return

    with statsd.pipeline() as pipe:
        # Restart required?
        if os.path.isfile('/var/run/reboot-required'):
            pipe.gauge('os.restart_required', 1)
        else:
            pipe.gauge('os.restart_required', 0)

        # Updates availale?
        apt_update_check = '/usr/lib/update-notifier/apt-check'
        if os.path.isfile(apt_update_check):
            process = subprocess.Popen(apt_update_check, stderr=subprocess.PIPE)
            _, response = process.communicate()
            regular, security = response.split(';')
            pipe.gauge('os.updates.regular', regular)
            pipe.gauge('os.updates.security', security)

        # Uptime
        # TODO: Consider replacing this check with the uptime package in pip
        with open('/proc/uptime', 'rb') as f:
            uptime, _ = f.readline().split()
            pipe.gauge('os.uptime', uptime)


start = used = 0
metric = ns('watchtower', 'gathering')
while True:
    start = int(time.time())
    with statsd.timer(metric):
        loadavg()
        ram()
        network()
        os_status()
    used = int(time.time()) - start
    try:
        time.sleep(10 - used) # sleep for the remainder of the interval
    except IOError as exc:
        print "IOError on time.sleep(10 - %r): %s" % (used, exc)
        # Default sleep after an error
        time.sleep(8)

