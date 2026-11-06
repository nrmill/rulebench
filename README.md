# rulebench

A small study of how reliably LLM agents follow standing rules, as context grows and as a task pulls against a rule, with and without a code check. The hypotheses, sample size and analysis were fixed in [PREREGISTRATION.md](PREREGISTRATION.md) before any model call.

The findings are in [WRITEUP.md](WRITEUP.md) and the registered report is [results/report.md](results/report.md). The audit grader failed its human check, so read [the write-up](WRITEUP.md) before quoting any compliance rate.

All rules, tasks and filler are synthetic. Fern Hollow Bicycle Cooperative does not exist.

## Run it

Python 3.12. The tests and the mock run need no API key and spend nothing.

```bash
pip install -r requirements.txt
```

```bash
python -m unittest discover -s tests -t .
```

```bash
python -m rulebench.run --backend mock --out results/mock.jsonl
```

```bash
python -m rulebench.analyze results/mock.jsonl
```

The mock backend plants known effects to check the pipeline. Its numbers are invented and are not results.

To reproduce the report from the logged outputs:

```bash
python -m rulebench.analyze results/repair results/confirmatory@ctx20k --out results/report.md
```

The seed is fixed, so the report comes out the same. The primary dataset is ctx0 and ctx80k from `results/repair/` plus ctx20k from `results/confirmatory/`; the last amendment in PREREGISTRATION.md says why. Logs are split into parts under 1 MB. Local temp folders in file paths are redacted as `<tmp>`. There is no temperature setting on either route, so a fresh model run will differ in its outputs; the logs are the record.

The logged trials ran through the CLI on `claude-haiku-5.5`, which needs a CLI login and a credit cap. Set `AGENT_CLI` if the program is not in the default place. Each trial's context meter must fall in a set band, and five that do not will stop the run.

```bash
python -m rulebench.run --backend cli --model cli:claude-haiku-5.5 --stage pilot --out results/pilot-new.jsonl --budget-credits 10
```

A paid run needs `ANTHROPIC_API_KEY` and a spending cap. It stops once logged spend reaches the cap, and re-running the same command resumes it.

```bash
python -m rulebench.run --backend anthropic --model claude-haiku-5-5 --stage pilot --out results/pilot-haiku.jsonl --budget-usd 1
```

In this repository, "the CLI" means an agent CLI on AWS Bedrock. Rerun commands need that CLI's own paths. This repository is a one-commit copy, so the commit history behind the dates here is not public.

## Layout

| Path | What it holds |
|---|---|
| `rulebench/rules.py` | The five rules, system prompts, tool definitions |
| `rulebench/scenarios.py` | 35 scenarios, each with a neutral and a pull request |
| `rulebench/graders.py` | The narrow enforcer and the audit grader for each rule |
| `rulebench/filler.py` | Seeded context filler |
| `rulebench/harness.py` | The agent loop and the check-and-revise pass |
| `rulebench/run.py` | The trial plan, budget cap and logging |
| `rulebench/analyze.py` | The registered analysis |
| `rulebench/power.py` | The power simulation |
| `rulebench/label.py` | The blinded human check of the audit grader |
| `rulebench/cli.py`, `rulebench/mcp_tools.py` | The CLI route and its simulated tools |
| `results/` | Logs of both pilots and both confirmatory runs, the reports, the label sample |
