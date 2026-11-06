"""Run the experiment and append one JSON line per trial.

  python -m rulebench.run --backend mock --out results/mock.jsonl
  python -m rulebench.run --backend anthropic --model claude-haiku-5-5 --stage pilot \
      --out results/pilot.jsonl --budget-usd 3
  python -m rulebench.run --backend anthropic --check-tokens --model claude-haiku-5-5
  python -m rulebench.run --backend cli --model cli:claude-haiku-5.5 --stage pilot \
      --out results/pilot-cli-haiku.jsonl --budget-credits 40

  python -m rulebench.run --backend cli --model cli:claude-haiku-5.5 \
      --contexts ctx0,ctx80k --out results/repair-cli-haiku.jsonl --budget-credits 100

--contexts runs only those contexts, with their trials interleaved at random.
Re-running with the same --out resumes: finished trials are skipped. The budget
stops the run before the next block once logged spend in this file reaches it.
Trials run in blocks that share a prompt prefix so the cache stays warm. Order
inside a block is shuffled with the seed.
"""
import argparse
import concurrent.futures as cf
import json
import os
import random
import re
import subprocess
import sys
import tempfile
import threading
import time

from .backends import AnthropicBackend, MockBackend, cost_usd
from .filler import CHUNKS
from .harness import build_messages, build_system, run_trial, trial_id
from .cli import cli_version, on_target, probe_cli, run_trial_cli
from .rules import TOOLS
from .scenarios import CONFIRMATORY, PILOT

SEED = 20261104
ARMS = ["prompt_only", "enforced"]
PULLS = ["neutral", "pull"]
MAX_DEAD_RUNS = 5  # Consecutive runs with no output mean the backend is down, so stop.
MAX_OFF_TARGET = 5  # Runs in all whose context meter is out of band mean the filler failed.
INTERLEAVED_BLOCK = 100
PILOT_REPS = {"neutral": 1, "pull": 3}
DEFAULT_REPS = {"neutral": 2, "pull": 8}
REPLICATION_REPS = {"neutral": 2, "pull": 4}
MODEL_REPS = {"claude-sonnet-5-5": REPLICATION_REPS}


def plan(stage, model, seed=SEED, contexts=None):
    """Blocks of (scenario, pull, context, arm, rep), one block per shared prefix.

    With contexts, only those run, shuffled together so that time and backend
    changes cannot line up with a condition.
    """
    if stage == "pilot":
        scenarios, reps = PILOT, PILOT_REPS
    else:
        scenarios, reps = CONFIRMATORY, MODEL_REPS.get(model, DEFAULT_REPS)
    suffix = f":{','.join(contexts)}" if contexts else ""
    rng = random.Random(f"{seed}:{model}:{stage}{suffix}")
    blocks = []
    for context in contexts or CHUNKS:
        for arm in ARMS:
            block = [(sc, pull, context, arm, rep)
                     for sc in scenarios for pull in PULLS for rep in range(reps[pull])]
            rng.shuffle(block)
            blocks.append(block)
    rng.shuffle(blocks)
    if contexts:
        flat = [item for block in blocks for item in block]
        rng.shuffle(flat)
        return [flat[i:i + INTERLEAVED_BLOCK] for i in range(0, len(flat), INTERLEAVED_BLOCK)]
    return blocks


def dead_run(rec):
    """True when the agent produced no result at all, as with an expired login."""
    return rec.get("first", {}).get("status") == "no_result"


def redact(line, temp_dir=None):
    """Replace the local temp folder, which names the machine's user, in a log line."""
    parts = re.split(r"[\\/]+", temp_dir or tempfile.gettempdir())
    return re.sub(r"[\\/]+".join(re.escape(p) for p in parts if p), "<tmp>", line, flags=re.I)


def git_commit():
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    except OSError:
        return "unknown"


def load_done(path):
    done, spent = set(), 0.0
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            for line in f:
                rec = json.loads(line)
                if "trial_id" not in rec:
                    continue
                if rec.get("status") != "error":
                    done.add(rec["trial_id"])
                spent += rec.get("cost_usd", 0.0) + rec.get("credits", 0.0)
    return done, spent


def check_tokens(backend):
    for context in CHUNKS:
        n = backend.count_tokens(build_system("prompt_only"), TOOLS, build_messages(context, "Hello."))
        print(f"{context}: {n} tokens")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--backend", choices=["mock", "anthropic", "cli"], default="mock")
    ap.add_argument("--model", default="mock-model")
    ap.add_argument("--stage", choices=["pilot", "confirmatory"], default="confirmatory")
    ap.add_argument("--out", default="results/mock.jsonl")
    ap.add_argument("--budget-usd", type=float, default=0.0, help="Required for the anthropic backend.")
    ap.add_argument("--budget-credits", type=float, default=0.0, help="Required for the cli backend.")
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--effort", default="medium")
    ap.add_argument("--check-tokens", action="store_true")
    ap.add_argument("--contexts", help="Comma-separated contexts to run, interleaved.")
    args = ap.parse_args()
    contexts = args.contexts.split(",") if args.contexts else None

    if args.backend == "anthropic":
        backend = AnthropicBackend(args.model, effort=args.effort)
    elif args.backend == "cli":
        backend = None
        probe_cli()
        if args.budget_credits <= 0:
            sys.exit("Set --budget-credits for a CLI run.")
    else:
        backend = MockBackend(args.model, seed=SEED)
    if args.check_tokens:
        check_tokens(backend)
        return
    if args.backend == "anthropic" and args.budget_usd <= 0:
        sys.exit("Set --budget-usd for a paid run.")

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    done, spent = load_done(args.out)
    limit = args.budget_usd or args.budget_credits
    lock = threading.Lock()
    out = open(args.out, "a", encoding="utf-8")
    header = {"run_header": True, "git_commit": git_commit(), "seed": SEED,
              "model": args.model, "stage": args.stage, "effort": args.effort,
              "backend": args.backend, "contexts": contexts, "started": time.time()}
    if args.backend == "cli":
        header["cli_version"] = cli_version()
    out.write(json.dumps(header) + "\n")  # One per launch, so a resumed run records its version too.

    dead = off_target = 0

    def halted():
        return dead >= MAX_DEAD_RUNS or off_target >= MAX_OFF_TARGET

    def one(item):
        nonlocal spent, dead, off_target
        sc, pull, context, arm, rep = item
        tid = trial_id(args.model, sc["id"], pull, context, arm, rep)
        if tid in done or (limit and spent >= limit) or halted():
            return
        try:
            if args.backend == "cli":
                cli_model = args.model.split(":", 1)[1]
                rec = run_trial_cli(sc, pull, context, arm, rep, args.model, cli_model, args.effort)
            else:
                rec = run_trial(backend, sc, pull, context, arm, rep, args.model)
                rec["cost_usd"] = sum(cost_usd(args.model, s["usage"]) for s in rec["log"])
            rec["status"] = "error" if dead_run(rec) else "ok"
            if dead_run(rec):
                rec = {k: rec[k] for k in ("trial_id", "model", "scenario", "rule", "pull", "context", "arm", "rep", "status")}
                rec["error"] = "no_result"
            elif args.backend == "cli" and not on_target(context, rec.get("context_usage_pct")):
                rec["status"], rec["error"] = "error", "context_off_target"
        except Exception as e:  # Logged and counted, never silently dropped.
            rec = {"trial_id": tid, "model": args.model, "scenario": sc["id"], "rule": sc["rule"],
                   "pull": pull, "context": context, "arm": arm, "rep": rep,
                   "status": "error", "error": type(e).__name__}
        with lock:
            spent += rec.get("cost_usd", 0.0) + rec.get("credits", 0.0)
            dead = dead + 1 if rec.get("error") == "no_result" else 0
            off_target += rec.get("error") == "context_off_target"
            out.write(redact(json.dumps(rec, ensure_ascii=False)) + "\n")
            out.flush()

    for block in plan(args.stage, args.model, contexts=contexts):
        if dead >= MAX_DEAD_RUNS:
            sys.exit(f"Stopped: {dead} runs in a row produced nothing. Check the backend login, then re-run.")
        if off_target >= MAX_OFF_TARGET:
            sys.exit(f"Stopped: {off_target} runs had the wrong context size. Check how the filler reaches the model.")
        if limit and spent >= limit:
            print(f"Budget reached: {spent:.2f}. Stopping; re-run to resume.")
            break
        # The first request writes the cache; the rest of the block can then read it.
        one(block[0])
        with cf.ThreadPoolExecutor(args.workers) as pool:
            list(pool.map(one, block[1:]))
        print(f"Block done, spent {spent:.2f}", flush=True)
    out.close()


if __name__ == "__main__":
    main()
