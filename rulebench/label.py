"""Blinded human check of the audit grader.

  python -m rulebench.label sample results/repair results/confirmatory@ctx20k --out results/labels.csv
  (a person fills the `violates` column with 1 or 0, reading only the rule and the output)
  python -m rulebench.label score results/labels.csv results/repair results/confirmatory@ctx20k

The sample holds 6 audit-pass and 6 audit-fail attempts per rule, drawn with a fixed
seed, shuffled, with condition and grader verdict hidden. Scoring joins the verdicts
back by key and reports agreement, Cohen's kappa, and the audit grader's precision
and recall against the human labels.
"""
import argparse
import csv
import json
import random

from .analyze import load, use_lenient
from .rules import STANDING_RULES

SEED = 20261104
PER_CELL = 6
RULE_NUMBER = {"ask_tool": 1, "workbench_path": 2, "no_dash": 3, "banned_words": 4, "reply_length": 5}


def rule_text(rule):
    prefix = f"{RULE_NUMBER[rule]}. "
    return next(line for line in STANDING_RULES.splitlines() if line.startswith(prefix))


def rendered(attempt):
    parts = ["CHAT:\n" + "\n\n".join(attempt["chat"])]
    if attempt["asked"]:
        parts.append("ASKED THROUGH TOOL:\n" + "\n".join(attempt["asked"]))
    for path, content in attempt["files"].items():
        parts.append(f"FILE {path}:\n{content}")
    return "\n\n".join(parts)


def sample(log, out):
    trials, _ = load(log)
    rng = random.Random(SEED)
    picked = []
    for rule in RULE_NUMBER:
        for verdict in (True, False):
            pool = sorted((t for t in trials if t["rule"] == rule and t["first"]["target_audit_pass"] == verdict),
                          key=lambda t: t["trial_id"])
            picked += rng.sample(pool, min(PER_CELL, len(pool)))
    rng.shuffle(picked)
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["key", "rule", "output", "violates"])
        for t in picked:
            w.writerow([t["trial_id"], rule_text(t["rule"]), rendered(t["first"]), ""])
    print(f"{len(picked)} items written to {out}")


def score(labels, log):
    trials, _ = load(log)
    print("Registered audit:", agreement(labels, trials))
    use_lenient(trials)
    print("Second reading, post hoc:", agreement(labels, trials))


def agreement(labels, trials):
    by_id = {t["trial_id"]: t for t in trials}
    tp = fp = fn = tn = 0
    with open(labels, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row["violates"].strip() not in ("0", "1"):
                continue
            human = row["violates"].strip() == "1"
            grader = not by_id[row["key"]]["first"]["target_audit_pass"]
            tp += human and grader
            fp += grader and not human
            fn += human and not grader
            tn += not human and not grader
    n = tp + fp + fn + tn
    agree = (tp + tn) / n
    chance = ((tp + fp) * (tp + fn) + (fn + tn) * (fp + tn)) / (n * n)
    kappa = (agree - chance) / (1 - chance) if chance < 1 else float("nan")
    return (f"n={n} agreement={agree:.3f} kappa={kappa:.3f} "
            f"precision={tp / max(tp + fp, 1):.3f} recall={tp / max(tp + fn, 1):.3f} "
            f"(tp={tp} fp={fp} fn={fn} tn={tn})")


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("sample")
    a.add_argument("log", nargs="+")
    a.add_argument("--out", default="results/labels.csv")
    b = sub.add_parser("score")
    b.add_argument("labels")
    b.add_argument("log", nargs="+")
    args = ap.parse_args()
    if args.cmd == "sample":
        sample(args.log, args.out)
    else:
        score(args.labels, args.log)


if __name__ == "__main__":
    main()
