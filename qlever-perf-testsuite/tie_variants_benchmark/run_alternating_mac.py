# Run two builds of TieVariantsBenchmark alternately and print the median and
# the minimum of every measurement (milliseconds).
# usage: python3 run_alternating_mac.py <binary without change> <binary with change> <rounds>
import collections
import re
import statistics
import subprocess
import sys

plain, split, rounds = sys.argv[1], sys.argv[2], int(sys.argv[3])
results = {plain: collections.defaultdict(list), split: collections.defaultdict(list)}
order = []


def run(binary):
    r = subprocess.run([binary, "-p"], capture_output=True, text=True)
    name, out = None, {}
    for line in (r.stdout + r.stderr).splitlines():
        m = re.search(r'Running measurement "(.*)" \.\.\.', line)
        if m:
            name = m.group(1)
        m = re.search(r"Done in ([0-9.]+) seconds", line)
        if m and name:
            out[name] = float(m.group(1)) * 1000
            name = None
    return out


for i in range(rounds):
    # The order alternates, so that drifts of the machine hit both binaries.
    for binary in ((plain, split) if i % 2 == 0 else (split, plain)):
        for name, ms in run(binary).items():
            if name not in order:
                order.append(name)
            results[binary][name].append(ms)

print(f"{'':95s} {'without':>20s} {'with':>20s}")
for name in order:
    a, b = results[plain][name], results[split][name]
    print(f"{name.replace(' of group ', ' | '):95s} "
          f"{statistics.median(a):7.1f} (min {min(a):5.1f})   "
          f"{statistics.median(b):7.1f} (min {min(b):5.1f})")
