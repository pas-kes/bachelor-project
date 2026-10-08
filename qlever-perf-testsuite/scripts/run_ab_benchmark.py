#!/usr/bin/env python3
"""A/B benchmark of QLever builds: index build, server load and query latency.

Replaces `run_e2e_benchmark.py` (which is kept as it was). Differences:

- The query cache of the server is disabled (`--cache-max-size 0B`), so that
  repeated queries are really executed. (With the default cache, the 2nd to
  7th run of a query is a cache hit with `computeResult: 0ms`.)
- Several targets (builds) are compared in the same session. The order of the
  targets alternates from round to round (A B, B A, A B, ...), so that drift
  of the machine does not look like a difference between builds.
- Every round builds a fresh index (wall time, CPU time, peak RSS, sizes per
  file type), starts a fresh server (load time, peak RSS) and runs all
  queries (client time and server-side time).
- Prints ratios relative to the first target, not only absolute numbers.
  Only the ratios are meaningful across machines.
- The context (machine, binaries, inputs) is written next to the results.

Example (two builds, three rounds):

  run_ab_benchmark.py --target old=/path/to/baseline-bin \
                      --target new=/path/to/build --rounds 3

An A/A run (the same binaries twice) shows the noise of the machine.
"""
import argparse
import csv
import hashlib
import json
import os
import platform
import re
import statistics
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
SUITE = HERE.parent
QUERY_TIMEOUT_S = 60
PERMUTATION_FILE = re.compile(r"\.index\.(pso|pos|spo|sop|osp|ops)$")


# _____________________________________________________________________________
def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def run_text(cmd):
    try:
        return subprocess.check_output(cmd, stderr=subprocess.STDOUT,
                                       text=True).strip()
    except Exception:  # pylint: disable=broad-except
        return ""


def machine_context():
    context = {
        "platform": platform.platform(),
        "machine": platform.machine(),
        "python": platform.python_version(),
        "cpu_count": os.cpu_count(),
    }
    if sys.platform == "darwin":
        context["cpu"] = run_text(["sysctl", "-n", "machdep.cpu.brand_string"])
        context["memory_bytes"] = run_text(["sysctl", "-n", "hw.memsize"])
        context["power"] = run_text(["pmset", "-g", "batt"]).splitlines()[0:1]
    return context


def max_rss_bytes(rusage):
    # `ru_maxrss` is in bytes on macOS and in kilobytes on Linux.
    return rusage.ru_maxrss if sys.platform == "darwin" else rusage.ru_maxrss * 1024


def parse_ms(text):
    """Parse the server-side time strings like `17ms`, `1.5s` or `300us`."""
    match = re.fullmatch(r"\s*([0-9.]+)\s*(us|ms|s)\s*", str(text))
    if not match:
        return None
    value, unit = float(match.group(1)), match.group(2)
    return value * {"us": 0.001, "ms": 1.0, "s": 1000.0}[unit]


# _____________________________________________________________________________
def run_measured(cmd, log_path):
    """Run `cmd` to completion, return (wall seconds, cpu seconds, peak RSS)."""
    with open(log_path, "w") as log:
        start = time.perf_counter()
        proc = subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT)
        _, status, rusage = os.wait4(proc.pid, 0)
        wall = time.perf_counter() - start
    proc.returncode = os.waitstatus_to_exitcode(status)
    if proc.returncode != 0:
        raise RuntimeError(f"{' '.join(map(str, cmd))} failed, see {log_path}")
    return wall, rusage.ru_utime + rusage.ru_stime, max_rss_bytes(rusage)


def index_sizes(index_prefix):
    sizes = defaultdict(int)
    prefix = Path(index_prefix)
    for path in prefix.parent.glob(prefix.name + "*"):
        size = path.stat().st_size
        sizes["total"] += size
        if PERMUTATION_FILE.search(path.name):
            sizes["permutations"] += size
        elif path.name.endswith(".index.patterns"):
            sizes["patterns"] += size
        elif ".vocabulary" in path.name:
            sizes["vocabulary"] += size
    return sizes


def start_server(binary_dir, index_prefix, args, log_path):
    """Start the server, return (process, seconds until it answers)."""
    cmd = [str(Path(binary_dir) / "qlever-server"), "-i", index_prefix,
           "-p", str(args.port), "-m", args.memory,
           "--cache-max-size", "0B",
           "--default-query-timeout", "30s"] + args.server_arg
    log = open(log_path, "w")
    start = time.perf_counter()
    proc = subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT)
    url = f"http://localhost:{args.port}/"
    while time.perf_counter() - start < 120:
        if proc.poll() is not None:
            raise RuntimeError(f"server exited early, see {log_path}")
        try:
            urllib.request.urlopen(url, timeout=1).read()
            return proc, time.perf_counter() - start
        except urllib.error.HTTPError:
            # Any HTTP answer (the root URL without a query may be a 4xx)
            # means that the server has loaded the index and listens.
            return proc, time.perf_counter() - start
        except (urllib.error.URLError, ConnectionError, OSError):
            time.sleep(0.02)
    proc.kill()
    raise RuntimeError("server did not become ready")


def stop_server(proc):
    """Stop the server, return its peak RSS in bytes."""
    proc.terminate()
    _, status, rusage = os.wait4(proc.pid, 0)
    proc.returncode = os.waitstatus_to_exitcode(status)
    time.sleep(0.2)
    return max_rss_bytes(rusage)


def run_query(port, sparql):
    params = urllib.parse.urlencode(
        {"query": sparql, "action": "qlever_json_export"})
    url = f"http://localhost:{port}/?{params}"
    start = time.perf_counter()
    try:
        with urllib.request.urlopen(url, timeout=QUERY_TIMEOUT_S) as conn:
            body, status = conn.read(), conn.status
    except urllib.error.HTTPError as e:
        body, status = e.read(), e.code
    client_ms = (time.perf_counter() - start) * 1000.0
    rows = total_ms = compute_ms = None
    if status == 200:
        try:
            data = json.loads(body)
            rows = data.get("resultsize")
            total_ms = parse_ms(data.get("time", {}).get("total"))
            compute_ms = parse_ms(data.get("time", {}).get("computeResult"))
        except ValueError:
            pass
    return status == 200, client_ms, rows, total_ms, compute_ms


# _____________________________________________________________________________
class Measurements:
    FIELDS = ["label", "round", "position", "metric", "query", "rep", "value",
              "unit", "rows"]

    def __init__(self):
        self.rows = []

    def add(self, label, rnd, position, metric, value, unit, query="", rep="",
            rows=""):
        self.rows.append(dict(label=label, round=rnd, position=position,
                              metric=metric, query=query, rep=rep,
                              value=value, unit=unit, rows=rows))

    def write(self, path):
        with open(path, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=self.FIELDS)
            writer.writeheader()
            writer.writerows(self.rows)


def run_target_round(target, rnd, position, args, queries, workdir, m):
    label, binary_dir = target
    base = workdir / label / f"round{rnd}"
    (base / "logs").mkdir(parents=True, exist_ok=True)
    index_prefix = str(base / "index" / "scientists")
    (base / "index").mkdir(exist_ok=True)

    # 1. Index build.
    cmd = [str(Path(binary_dir) / "qlever-index"), "-i", index_prefix,
           "-F", "ttl", "-f", str(args.nt_file), "-s", str(args.settings_file)]
    wall, cpu, rss = run_measured(cmd, base / "logs" / "index_build.log")
    m.add(label, rnd, position, "build_wall", wall, "s")
    m.add(label, rnd, position, "build_cpu", cpu, "s")
    m.add(label, rnd, position, "build_peak_rss", rss / 2**20, "MiB")
    for kind, size in index_sizes(index_prefix).items():
        m.add(label, rnd, position, f"index_size_{kind}", size / 2**20, "MiB")

    # 2. Server start (load) and queries.
    proc, load = start_server(binary_dir, index_prefix, args,
                              base / "logs" / "server.log")
    m.add(label, rnd, position, "load_wall", load, "s")
    try:
        for number, query in enumerate(queries):
            name = f"{number:02d}_{query['name']}"
            for _ in range(args.warmup):
                run_query(args.port, query["sparql"])
            for rep in range(args.reps):
                ok, client, rows, total, compute = run_query(
                    args.port, query["sparql"])
                if not ok:
                    continue
                m.add(label, rnd, position, "query_client", client, "ms", name,
                      rep, rows)
                if total is not None:
                    m.add(label, rnd, position, "query_server_total", total,
                          "ms", name, rep, rows)
                if compute is not None:
                    m.add(label, rnd, position, "query_server_compute",
                          compute, "ms", name, rep, rows)
    finally:
        m.add(label, rnd, position, "server_peak_rss", stop_server(proc) / 2**20,
              "MiB")


# _____________________________________________________________________________
def summarize(rows, labels):
    """Per (metric, query): median per label (median over rounds of the median
    over repetitions) and the ratio to the first label."""
    per_round = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))
    units, row_counts = {}, defaultdict(lambda: defaultdict(set))
    for r in rows:
        key = (r["metric"], r["query"])
        per_round[key][r["label"]][int(r["round"])].append(float(r["value"]))
        units[r["metric"]] = r["unit"]
        if r["rows"] not in ("", None):
            row_counts[key][r["label"]].add(str(r["rows"]))

    baseline = labels[0]
    summary = []
    for key, by_label in per_round.items():
        medians = {}
        round_medians = {}
        for label in labels:
            rounds = by_label.get(label, {})
            round_medians[label] = {k: statistics.median(v)
                                    for k, v in rounds.items()}
            if rounds:
                medians[label] = statistics.median(round_medians[label].values())
        entry = {"metric": key[0], "query": key[1], "unit": units[key[0]]}
        for label in labels:
            entry[label] = medians.get(label)
        for label in labels[1:]:
            base_value, value = medians.get(baseline), medians.get(label)
            entry[f"ratio_{label}"] = (value / base_value
                                       if base_value and value else None)
            ratios = [round_medians[label][k] / round_medians[baseline][k]
                      for k in round_medians[label]
                      if k in round_medians[baseline]
                      and round_medians[baseline][k] > 0]
            entry[f"ratio_min_{label}"] = min(ratios) if ratios else None
            entry[f"ratio_max_{label}"] = max(ratios) if ratios else None
        mismatch = len({tuple(sorted(v)) for v in row_counts[key].values()}) > 1
        entry["rows_differ"] = mismatch
        summary.append(entry)
    return summary


def format_report(summary, labels, context):
    lines = [f"Targets (baseline first): {', '.join(labels)}", ""]
    others = labels[1:]

    def fmt(x):
        return "-" if x is None else f"{x:10.3f}"

    def row(entry, name):
        cells = [f"{name:<34}", f"{entry['unit']:>4}"]
        cells += [fmt(entry[label]) for label in labels]
        for label in others:
            ratio = entry[f"ratio_{label}"]
            lo, hi = entry[f"ratio_min_{label}"], entry[f"ratio_max_{label}"]
            cells.append("   -" if ratio is None
                         else f"  x{ratio:.3f} [{lo:.2f}..{hi:.2f}]")
        return " ".join(cells)

    header = (f"{'metric':<34} {'unit':>4} "
              + " ".join(f"{label:>10}" for label in labels)
              + "".join(f"  ratio {label}/{labels[0]} [per-round range]"
                        for label in others))
    single = [e for e in summary if e["query"] == ""]
    lines += ["Index and server", header]
    lines += [row(e, e["metric"]) for e in sorted(single,
                                                  key=lambda e: e["metric"])]

    for metric in sorted({e["metric"] for e in summary if e["query"] != ""}):
        queries = [e for e in summary if e["metric"] == metric]
        if not queries:
            continue
        lines += ["", f"Queries, {metric} (sorted by ratio, worst first)",
                  header]
        for label in others:
            ratios = [e[f"ratio_{label}"] for e in queries
                      if e[f"ratio_{label}"]]
            if ratios:
                geo = statistics.geometric_mean(ratios)
                slower = sum(r > 1.0 for r in ratios)
                lines.append(f"-- {label}: geometric mean ratio {geo:.3f}, "
                             f"slower in {slower} of {len(ratios)} queries")
        key = (lambda e: -(e[f"ratio_{others[0]}"] or 0)) if others else (
            lambda e: e["query"])
        lines += [row(e, e["query"] + (" (ROWS DIFFER)" if e["rows_differ"]
                                       else "")) for e in sorted(queries,
                                                                 key=key)]
    lines += ["", "Context: " + json.dumps(context["machine"])]
    return "\n".join(lines)


# _____________________________________________________________________________
def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    parser.add_argument("--target", action="append", default=[],
                        metavar="LABEL=BINARY_DIR",
                        help="a build to compare, the first one is the "
                        "baseline (repeat the option)")
    parser.add_argument("--rounds", type=int, default=3)
    parser.add_argument("--reps", type=int, default=5)
    parser.add_argument("--warmup", type=int, default=2)
    parser.add_argument("--port", type=int, default=9099)
    parser.add_argument("--memory", default="2GB")
    parser.add_argument("--server-arg", action="append", default=[],
                        help="extra argument for qlever-server")
    parser.add_argument("--nt-file", default=SUITE / "dataset/scientists.nt")
    parser.add_argument("--settings-file",
                        default=HERE / "e2e-build-settings.json")
    parser.add_argument("--queries-json", default=HERE / "queries_no_text.json")
    parser.add_argument("--workdir", default=None,
                        help="indexes and logs (default: <out-dir>/work)")
    parser.add_argument("--out-dir", default=None)
    parser.add_argument("--max-queries", type=int, default=None)
    parser.add_argument("--summarize-only", metavar="OUT_DIR", default=None,
                        help="only summarize an existing result directory")
    args = parser.parse_args()

    if args.summarize_only:
        out_dir = Path(args.summarize_only)
        context = json.load(open(out_dir / "context.json"))
        with open(out_dir / "measurements.csv", newline="") as f:
            rows = list(csv.DictReader(f))
        labels = context["labels"]
        print(format_report(summarize(rows, labels), labels, context))
        return

    targets = []
    for spec in args.target:
        label, _, directory = spec.partition("=")
        if not directory or not (Path(directory) / "qlever-server").exists():
            parser.error(f"invalid target {spec!r}: needs LABEL=DIR with "
                         "qlever-index and qlever-server in DIR")
        targets.append((label, directory))
    if len(targets) < 1 or len({t[0] for t in targets}) != len(targets):
        parser.error("give at least one --target with unique labels")

    out_dir = Path(args.out_dir or SUITE / "results" /
                   datetime.now().strftime("ab_%Y%m%d_%H%M%S"))
    out_dir.mkdir(parents=True, exist_ok=True)
    workdir = Path(args.workdir) if args.workdir else out_dir / "work"
    queries = json.load(open(args.queries_json))
    if args.max_queries:
        queries = queries[:args.max_queries]

    labels = [t[0] for t in targets]
    context = {
        "started": datetime.now().isoformat(timespec="seconds"),
        "labels": labels,
        "machine": machine_context(),
        "parameters": {k: str(v) for k, v in vars(args).items()},
        "inputs": {"nt_sha256": sha256(args.nt_file),
                   "queries_sha256": sha256(args.queries_json),
                   "settings_sha256": sha256(args.settings_file),
                   "num_queries": len(queries)},
        "binaries": {
            label: {
                "server_version": run_text([str(Path(d) / "qlever-server"),
                                            "--version"]).splitlines()[-1:],
                "server_sha256": sha256(Path(d) / "qlever-server"),
                "index_sha256": sha256(Path(d) / "qlever-index")}
            for label, d in targets},
    }
    json.dump(context, open(out_dir / "context.json", "w"), indent=2)

    m = Measurements()
    for rnd in range(args.rounds):
        order = targets if rnd % 2 == 0 else list(reversed(targets))
        for position, target in enumerate(order):
            print(f"round {rnd + 1}/{args.rounds}, position {position + 1}: "
                  f"{target[0]}", flush=True)
            run_target_round(target, rnd, position, args, queries, workdir, m)
            m.write(out_dir / "measurements.csv")

    summary = summarize(m.rows, labels)
    with open(out_dir / "summary.csv", "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(summary[0].keys()))
        writer.writeheader()
        writer.writerows(summary)
    report = format_report(summary, labels, context)
    (out_dir / "report.txt").write_text(report + "\n")
    print()
    print(report)
    print(f"\nResults in {out_dir}")


if __name__ == "__main__":
    main()
