"""Power simulation for the H1 contrast, no model calls.

Each scenario gets its own baseline compliance and its own effect on the logit
scale, then each cell draws `reps` Bernoulli trials. The test is the sign-flip
permutation test from analyze.py at the strictest Holm threshold, 0.05/4.
`python -m rulebench.power`
"""
import numpy as np

SEED = 20261104
N_SCENARIOS = 30
SIMS = 1000
PERMS = 1000
ALPHA = 0.05 / 4
BASELINE_SD = 1.0
EFFECT_SD = 0.5


def logit(p):
    return np.log(p / (1 - p))


def expit(x):
    return 1 / (1 + np.exp(-x))


def rejects(v, rng):
    obs = abs(v.mean())
    signs = rng.choice([-1.0, 1.0], size=(PERMS, len(v)))
    return (1 + (np.abs((signs * v).mean(axis=1)) >= obs - 1e-12).sum()) / (PERMS + 1) < ALPHA


def power(base, drop, reps, rng):
    shift = logit(base - drop) - logit(base)
    hits = 0
    for _ in range(SIMS):
        u = rng.normal(logit(base), BASELINE_SD, N_SCENARIOS)
        e = rng.normal(shift, EFFECT_SD, N_SCENARIOS)
        a = rng.binomial(reps, expit(u)) / reps
        b = rng.binomial(reps, expit(u + e)) / reps
        hits += rejects(b - a, rng)
    return hits / SIMS


def main():
    rng = np.random.default_rng(SEED)
    print(f"Power for H1 at alpha {ALPHA}, {N_SCENARIOS} scenarios, baseline SD {BASELINE_SD}, effect SD {EFFECT_SD}")
    print("baseline  drop  reps=2  reps=4  reps=8")
    for base in (0.9, 0.75, 0.6):
        for drop in (0.10, 0.15, 0.20, 0.30):
            cells = "  ".join(f"{power(base, drop, r, rng):6.2f}" for r in (2, 4, 8))
            print(f"{base:7.2f}  {drop:4.2f}  {cells}")


if __name__ == "__main__":
    main()
