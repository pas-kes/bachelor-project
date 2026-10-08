#!/usr/bin/env python3
"""A/B benchmark with QLever's own tooling (the `qlever` command line tool).

For every round and target (build) it runs
  qlever index            (fresh index, wall/CPU/peak memory, size per file type)
  qlever start            (load time)
  qlever benchmark-queries (every query, cache cleared before each query)
  qlever stop
and compares the targets (ratios to the first target, order alternates A B /
B A). Every target is run either

- native:    --target LABEL=<dir with qlever-index and qlever-server>
- container: --container --target LABEL=<QLever source checkout>
             The image is built automatically from the `Dockerfile` of that
             checkout (cached by commit + diff), the same file the CI uses.
             Both targets then run in the same Linux environment, with the same
             resource limits (--cpus, --memory) and number of threads.

Examples:
  qlever_ab.py --target old=/path/baseline-bin --target new=/path/build
  qlever_ab.py --container --cpus 4 --memory 8g \\
               --checkout old=master --target new=/path/to/qlever-checkout
  qlever_ab.py --container --dry-run --target old=... --target new=...

Only the ratio new/old of the same session is meaningful, not absolute times.
"""
import argparse
import csv
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
# Shared helpers (measurement container, summary and report).
from run_ab_benchmark import (Measurements, format_report,  # noqa: E402
                              machine_context, max_rss_bytes, run_text, sha256,
                              summarize)

SUITE = HERE.parent
DATASET_NAME = "scientists"
YAML_TO_JSON = r"""
import json, sys, yaml
data = yaml.safe_load(open(sys.argv[1]))
out = []
for q in data["queries"]:
    info = q.get("runtime_info") or {}
    tree = info.get("query_execution_tree") or {}
    out.append({"name": q["name"], "client_s": info.get("client_time"),
                "size": q.get("result_size"), "exec_ms": tree.get("total_time"),
                "plan_ms": (info.get("meta") or {}).get("time_query_planning"),
                "error": q.get("error")})
print(json.dumps(out))
"""


# _____________________________________________________________________________
class Target:
    def __init__(self, label, path, container):
        self.label, self.path, self.container = label, Path(path), container
        self.image = None


def qlever_python():
    """The interpreter of the `qlever` tool (it has PyYAML)."""
    exe = shutil.which("qlever")
    if not exe:
        sys.exit("The `qlever` command line tool is needed (brew install qlever-control).")
    first = open(exe).readline().strip()
    return first[2:] if first.startswith("#!") else sys.executable


def ansi_free(text):
    return re.sub(r"\x1b\[[0-9;]*m", "", text)


def docker_ok():
    return subprocess.run(["docker", "info"], capture_output=True).returncode == 0


# What goes into the three binaries that the image builds (see `Dockerfile`).
IMAGE_SOURCE_PATHS = ["src", "CMakeLists.txt", "CompilationInfo.cmake",
                      "GitVersion.cmake", "Dockerfile", "docker-entrypoint.sh"]


def image_tag(target):
    """Tag from the content of the files that the binaries are built from
    (tracked and untracked), not from the commit or `.git`. So the image is
    reused when only tests, benchmarks, the commit or the `.git` directory
    changed (any git command changes `.git`, which would invalidate Docker's
    cache for everything after `COPY .git`, including the long compile)."""
    files = run_text(["git", "-C", str(target.path), "ls-files", "-co",
                      "--exclude-standard", "--", *IMAGE_SOURCE_PATHS])
    digest = hashlib.sha1()
    for name in sorted(files.splitlines()):
        path = target.path / name
        if path.is_file():
            digest.update(name.encode() + sha256(path).encode())
    return f"qlever-bench-{target.label}:{digest.hexdigest()[:12]}"


BUILDER = "qlever-bench-builder"


def ensure_builder(args):
    """A buildx builder with limited CPUs. QLever's `Dockerfile` runs
    `cmake --build .` without `-j`, so ninja starts `nproc + 2` compile jobs. On
    all CPUs of the Docker VM, this runs out of memory (the VM has less than
    1.5 GB per CPU), so the builder is restricted to `--build-cpus` CPUs.
    Measured: 4 CPUs (6 jobs) exhausted the 11.7 GiB of the Docker Desktop VM
    after ~30 minutes (swap full, no progress), so the default is 2 CPUs (4
    jobs)."""
    if subprocess.run(["docker", "buildx", "inspect", BUILDER],
                      capture_output=True).returncode == 0:
        return
    cpus = f"0-{args.build_cpus - 1}"
    subprocess.run(["docker", "buildx", "create", "--name", BUILDER, "--driver",
                    "docker-container", "--driver-opt", f"cpuset-cpus={cpus}"],
                   check=True, capture_output=True)


def ensure_image(target, args, logs):
    """Build the image from the `Dockerfile` of the checkout, if not there."""
    if not (target.path / "Dockerfile").exists():
        sys.exit(f"{target.path} has no Dockerfile (is it a QLever checkout?)")
    if not (target.path / ".git").is_dir():
        sys.exit(f"{target.path}/.git is not a directory (a git worktree?). The "
                 "Dockerfile copies `.git`, use a full checkout (see --checkout).")
    target.image = image_tag(target)
    cmd = ["docker", "buildx", "build", "--builder", BUILDER, "--load",
           "--progress", "plain", "--build-arg", "RUN_TESTS=false", "-t",
           target.image, "-f", str(target.path / "Dockerfile"), str(target.path)]
    if args.dry_run:
        print(f"would create a builder with {args.build_cpus} CPUs and build:",
              shlex.join(cmd))
        return
    if subprocess.run(["docker", "image", "inspect", target.image],
                      capture_output=True).returncode == 0:
        print(f"image {target.image} exists, not rebuilding")
        return
    ensure_builder(args)
    log_path = logs / f"docker_build_{target.label}.log"
    print(f"building image {target.image} on {args.build_cpus} CPUs (this takes "
          f"a while), log: {log_path}", flush=True)
    start = time.perf_counter()
    with open(log_path, "w") as log:
        if subprocess.run(cmd, stdout=log, stderr=subprocess.STDOUT).returncode:
            sys.exit(f"docker build failed, see {log_path}")
    print(f"built {target.image} in {(time.perf_counter() - start) / 60:.1f} min")


def checkout(repo, ref, directory):
    """A full local clone (a real `.git` directory) at `ref`."""
    if not directory.exists():
        subprocess.run(["git", "clone", "--local", "-q", str(repo), str(directory)],
                       check=True)
    subprocess.run(["git", "-C", str(directory), "checkout", "-q", ref],
                   check=True)


# _____________________________________________________________________________
def write_round_dir(base, target, args, settings_json, queries):
    base.mkdir(parents=True, exist_ok=True)
    for f in base.iterdir():  # fresh index in every round
        if f.is_dir():
            shutil.rmtree(f)
        else:
            f.unlink()
    # `qlever index` wants relative input files. A copy (not a symlink), because
    # a container only sees the mounted directory.
    shutil.copy(args.nt_file, base / "scientists.nt")
    (base / "queries.tsv").write_text("".join(
        f"{i:02d}_{q['name']}\t{' '.join(q['sparql'].split())}\n"
        for i, q in enumerate(queries)))
    runtime = (f"SYSTEM = docker\nIMAGE = {target.image}\n" if target.container
               else "SYSTEM = native\n")
    binaries_index = binaries_server = ""
    if not target.container:
        binaries_index = f"INDEX_BINARY = {target.path / 'qlever-index'}\n"
        binaries_server = f"SERVER_BINARY = {target.path / 'qlever-server'}\n"
    (base / "Qleverfile").write_text(f"""[data]
NAME = {DATASET_NAME}
DESCRIPTION = Scientists benchmark
FORMAT = ttl

[index]
INPUT_FILES = scientists.nt
CAT_INPUT_FILES = cat ${{INPUT_FILES}}
SETTINGS_JSON = {settings_json}
{binaries_index}
[server]
PORT = {args.port}
{binaries_server}MEMORY_FOR_QUERIES = {args.memory_for_queries}
CACHE_MAX_SIZE = 0B
NUM_THREADS = {args.threads}
TIMEOUT = 60s

[runtime]
{runtime}""")


def qlever_command(target, sub_args, cwd, args):
    """The shell command for `qlever <sub_args>`. In container mode, take the
    `docker run` command that `qlever` would run (via `--show`) and add the
    resource limits to it."""
    if not target.container:
        return "qlever " + shlex.join(sub_args)
    result = subprocess.run(["qlever", sub_args[0], "--show", *sub_args[1:]],
                            cwd=cwd, capture_output=True, text=True)
    shown = ansi_free(result.stdout + result.stderr)  # `--show` prints to stderr
    lines = []
    for line in shown.splitlines():
        if line.startswith("Command:") or "You passed the argument" in line:
            continue
        if line.strip():
            lines.append(line)
    cmd = "\n".join(lines)
    if args.cpus:
        cmd = cmd.replace("docker run", f"docker run --cpus={args.cpus}")
    if args.memory:
        cmd = cmd.replace("docker run", f"docker run --memory={args.memory} "
                          f"--memory-swap={args.memory}")
    return cmd


def index_file_sizes(base):
    """Sizes of the index files per kind (not the input, logs and settings)."""
    sizes = {"total": 0, "permutations": 0, "patterns": 0, "vocabulary": 0}
    for path in base.glob(f"{DATASET_NAME}.*"):
        if path.suffix in (".txt", ".tsv", ".nt", ".json", ".yaml") or path.is_dir():
            continue
        size = path.stat().st_size
        sizes["total"] += size
        if re.search(r"\.index\.(pso|pos|spo|sop|osp|ops)$", path.name):
            sizes["permutations"] += size
        elif path.name.endswith(".index.patterns"):
            sizes["patterns"] += size
        elif ".vocabulary" in path.name:
            sizes["vocabulary"] += size
    return sizes


def run_measured(cmd, cwd, log_path):
    """Run a shell command, return (wall s, cpu s, peak RSS bytes incl. children)."""
    with open(log_path, "w") as log:
        start = time.perf_counter()
        proc = subprocess.Popen(["bash", "-c", cmd], cwd=cwd, stdout=log,
                                stderr=subprocess.STDOUT)
        _, status, rusage = os.wait4(proc.pid, 0)
        wall = time.perf_counter() - start
    code = os.waitstatus_to_exitcode(status)
    if code != 0:
        raise RuntimeError(f"command failed ({code}), see {log_path}:\n{cmd}")
    return wall, rusage.ru_utime + rusage.ru_stime, max_rss_bytes(rusage)


class MemorySampler(threading.Thread):
    """Samples the memory of the server (process or container), keeps the max."""

    def __init__(self, target, port):
        super().__init__(daemon=True)
        self.target, self.port, self.peak, self.stop_flag = target, port, 0.0, False

    def sample(self):
        if self.target.container:
            out = run_text(["docker", "stats", "--no-stream", "--format",
                            "{{.MemUsage}}", f"qlever.server.{DATASET_NAME}"])
            m = re.match(r"([0-9.]+)\s*([KMG]i?B)", out)
            if m:
                unit = {"K": 1 / 1024, "M": 1, "G": 1024}[m.group(2)[0]]
                return float(m.group(1)) * unit
            return None
        pid = run_text(["lsof", "-ti", f"tcp:{self.port}", "-sTCP:LISTEN"]).split()
        if pid:
            rss_kb = run_text(["ps", "-o", "rss=", "-p", pid[0]])
            return float(rss_kb) / 1024 if rss_kb else None
        return None

    def run(self):
        while not self.stop_flag:
            value = self.sample()
            if value:
                self.peak = max(self.peak, value)
            time.sleep(0.3)


def wait_until_ready(port, timeout=180):
    start = time.perf_counter()
    while time.perf_counter() - start < timeout:
        try:
            urllib.request.urlopen(f"http://localhost:{port}/", timeout=1).read()
            return
        except urllib.error.HTTPError:
            return  # any HTTP answer: the index is loaded
        except (urllib.error.URLError, ConnectionError, OSError):
            time.sleep(0.05)
    raise RuntimeError("server did not become ready")


def run_round(target, rnd, position, args, queries, settings_json, workdir, m,
              py_yaml):
    base = workdir / target.label / f"round{rnd}"
    logs = base.parent / f"logs_round{rnd}"
    logs.mkdir(parents=True, exist_ok=True)
    write_round_dir(base, target, args, settings_json, queries)
    label = target.label

    if target.container:  # remove leftovers of a crashed run
        subprocess.run(["docker", "rm", "-f", f"qlever.server.{DATASET_NAME}",
                        f"qlever.index.{DATASET_NAME}"], capture_output=True)

    # 1. qlever index
    cmd = qlever_command(target, ["index", "--overwrite-existing"], base, args)
    wall, cpu, rss = run_measured(cmd, base, logs / "index.log")
    m.add(label, rnd, position, "build_wall", wall, "s")
    if not target.container:  # in container mode these are the `docker` CLI's
        m.add(label, rnd, position, "build_cpu", cpu, "s")
        m.add(label, rnd, position, "build_peak_rss", rss / 2**20, "MiB")
    for kind, size in index_file_sizes(base).items():
        m.add(label, rnd, position, f"index_size_{kind}", size / 2**20, "MiB")

    # 2. qlever start (load time)
    cmd = qlever_command(target, ["start", "--no-warmup",
                                  "--kill-existing-with-same-port"], base, args)
    start = time.perf_counter()
    run_measured(cmd, base, logs / "start.log")
    wait_until_ready(args.port)
    m.add(label, rnd, position, "load_wall", time.perf_counter() - start, "s")
    sampler = MemorySampler(target, args.port)
    sampler.start()
    try:
        # 3. qlever benchmark-queries, warm-up runs first, cache cleared each query.
        for run in range(args.warmup + args.reps):
            results_dir = base / f"results_{run}"
            subprocess.run(
                ["qlever", "benchmark-queries", "--queries-tsv", "queries.tsv",
                 "--clear-cache", "yes", "--accept",
                 "application/qlever-results+json", "--max-results-output-file",
                 "0", "--result-file", f"{DATASET_NAME}.qlever", "--results-dir",
                 str(results_dir)], cwd=base, check=True,
                stdout=open(logs / f"benchmark_{run}.log", "w"),
                stderr=subprocess.STDOUT)
            if run < args.warmup:
                continue
            parsed = json.loads(subprocess.check_output(
                [py_yaml, "-c", YAML_TO_JSON,
                 str(results_dir / f"{DATASET_NAME}.qlever.results.yaml")],
                text=True))
            for q in parsed:
                if q["error"] or q["client_s"] is None:
                    continue
                rep = run - args.warmup
                m.add(label, rnd, position, "query_client", q["client_s"] * 1000,
                      "ms", q["name"], rep, q["size"])
                if q["exec_ms"] is not None:
                    m.add(label, rnd, position, "query_server_exec", q["exec_ms"],
                          "ms", q["name"], rep, q["size"])
                if q["plan_ms"] is not None:
                    m.add(label, rnd, position, "query_server_planning",
                          q["plan_ms"], "ms", q["name"], rep, q["size"])
            shutil.rmtree(results_dir)
    finally:
        sampler.stop_flag = True
        sampler.join()
        if sampler.peak:
            m.add(label, rnd, position, "server_peak_rss", sampler.peak, "MiB")
        # 4. qlever stop
        subprocess.run(["qlever", "stop"], cwd=base, capture_output=True)
        if target.container:
            subprocess.run(["docker", "rm", "-f", f"qlever.server.{DATASET_NAME}"],
                           capture_output=True)
        time.sleep(0.3)


# _____________________________________________________________________________
def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    parser.add_argument("--target", action="append", default=[],
                        metavar="LABEL=PATH",
                        help="binary dir (native) or source checkout (--container); "
                        "the first target is the baseline")
    parser.add_argument("--container", action="store_true",
                        help="run in containers built from the Dockerfile")
    parser.add_argument("--checkout", action="append", default=[],
                        metavar="LABEL=GIT_REF",
                        help="container mode: clone --repo at that ref as target")
    parser.add_argument("--repo", default=None,
                        help="QLever repository for --checkout (default: the "
                        "checkout of the first --target that is a git repo)")
    parser.add_argument("--cpus", default=None, help="container: --cpus limit")
    parser.add_argument("--memory", default=None, help="container: --memory limit")
    parser.add_argument("--threads", type=int, default=4,
                        help="server threads (both modes)")
    parser.add_argument("--memory-for-queries", default="2G")
    parser.add_argument("--rounds", type=int, default=3)
    parser.add_argument("--reps", type=int, default=5)
    parser.add_argument("--warmup", type=int, default=1)
    parser.add_argument("--port", type=int, default=9199)
    parser.add_argument("--max-queries", type=int, default=None)
    parser.add_argument("--nt-file", default=SUITE / "dataset/scientists.nt")
    parser.add_argument("--settings-file", default=HERE / "e2e-build-settings.json")
    parser.add_argument("--queries-json", default=HERE / "queries_no_text.json")
    parser.add_argument("--workdir", default=None)
    parser.add_argument("--out-dir", default=None)
    parser.add_argument("--build-cpus", type=int, default=2,
                        help="container: CPUs of the image builder (see "
                        "ensure_builder)")
    parser.add_argument("--build-only", action="store_true",
                        help="container: only build the images, do not run")
    parser.add_argument("--dry-run", action="store_true",
                        help="only show what would be built and run")
    args = parser.parse_args()

    out_dir = Path(args.out_dir or SUITE / "results" /
                   datetime.now().strftime("qab_%Y%m%d_%H%M%S"))
    workdir = Path(args.workdir) if args.workdir else out_dir / "work"
    workdir.mkdir(parents=True, exist_ok=True)

    targets = []
    for spec in args.checkout:
        label, _, ref = spec.partition("=")
        repo = args.repo or next((t.split("=", 1)[1] for t in args.target
                                  if (Path(t.split("=", 1)[1]) / ".git").is_dir()),
                                 None)
        if not args.container or not repo:
            parser.error("--checkout needs --container and a --repo")
        directory = workdir / f"src-{label}"
        if not args.dry_run:
            checkout(repo, ref, directory)
        targets.append(Target(label, directory, True))
    for spec in args.target:
        label, _, path = spec.partition("=")
        if not path or not Path(path).exists():
            parser.error(f"invalid target {spec!r}")
        if not args.container and not (Path(path) / "qlever-server").exists():
            parser.error(f"{path} has no qlever-server and qlever-index")
        targets.append(Target(label, path, args.container))
    # `--checkout` targets come first (they are usually the baseline).
    if len(targets) < 1 or len({t.label for t in targets}) != len(targets):
        parser.error("give at least one target, labels have to be unique")

    if args.container and not args.dry_run and not docker_ok():
        sys.exit("The Docker daemon is not running (start Docker Desktop).")

    queries = json.load(open(args.queries_json))
    if args.max_queries:
        queries = queries[:args.max_queries]
    settings_json = json.dumps(json.load(open(args.settings_file)))
    py_yaml = qlever_python()

    logs = workdir / "docker_logs"
    logs.mkdir(exist_ok=True)
    if args.container:
        for target in targets:
            ensure_image(target, args, logs)
    if args.build_only:
        return
    if args.dry_run:
        base = workdir / "dry_run"
        write_round_dir(base, targets[0], args, settings_json, queries)
        for sub in (["index", "--overwrite-existing"], ["start", "--no-warmup"]):
            print(f"\n[{targets[0].label}] qlever {sub[0]}:\n"
                  + qlever_command(targets[0], sub, base, args))
        print(f"\nQleverfile and files in {base}")
        return

    out_dir.mkdir(parents=True, exist_ok=True)
    labels = [t.label for t in targets]
    context = {
        "started": datetime.now().isoformat(timespec="seconds"),
        "labels": labels,
        "machine": machine_context(),
        "mode": "container" if args.container else "native",
        "limits": {"cpus": args.cpus, "memory": args.memory,
                   "threads": args.threads},
        "qlever_cli": run_text(["qlever", "--version"]),
        "parameters": {k: str(v) for k, v in vars(args).items()},
        "inputs": {"nt_sha256": sha256(args.nt_file),
                   "queries_sha256": sha256(args.queries_json),
                   "settings_sha256": sha256(args.settings_file),
                   "num_queries": len(queries)},
        "targets": {t.label: ({"image": t.image} if t.container else
                              {"server_sha256": sha256(t.path / "qlever-server"),
                               "server_version": run_text(
                                   [str(t.path / "qlever-server"),
                                    "--version"]).splitlines()[-1:]})
                    for t in targets},
    }
    json.dump(context, open(out_dir / "context.json", "w"), indent=2)

    m = Measurements()
    for rnd in range(args.rounds):
        order = targets if rnd % 2 == 0 else list(reversed(targets))
        for position, target in enumerate(order):
            print(f"round {rnd + 1}/{args.rounds}, position {position + 1}: "
                  f"{target.label}", flush=True)
            run_round(target, rnd, position, args, queries, settings_json,
                      workdir, m, py_yaml)
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
