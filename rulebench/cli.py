"""Run one trial through the CLI, which owns the agent loop.

Differences from the API path, all recorded in PREREGISTRATION.md:
  - the CLI's own system prompt stays in place; the study's prompt is added to it.
  - The filler is a context file, not a history of tool calls. CLI 3.1 cuts context
    files at 100,000 characters, so a longer ledger goes in the agent prompt instead.
  - The simulated tools come from the MCP server in mcp_tools.py.
Only chat text, tool calls, stop reasons and credits are logged. Raw events carry
local paths and are never stored.
"""
import json
import os
import shutil
import subprocess
import tempfile

from .filler import CHUNKS, ledger_chunk
from .harness import FEEDBACK, FILLER_SEED, grade, trial_id
from .rules import system_prompt

AGENT_CLI = os.environ.get("AGENT_CLI", os.path.expandvars(r"agent-cli"))
TIMEOUT_S = 300
RESOURCE_LIMIT = 100_000  # Characters of context files an agent may load in CLI 3.1.
# Share of the 1M window the meter must show after a first run, in percent.
METER_BANDS = {"ctx0": (0.3, 2.5), "ctx20k": (2.2, 4.0), "ctx80k": (8.0, 11.0)}
LEDGER_INTRO = "Inventory ledger, for reference:"
SERVER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "mcp_tools.py")


def parse_events(lines):
    """Reduce a stream-json run to chat blocks, credits, session id and status."""
    chat, current = [], []
    credits, session, status, usage_pct = 0.0, None, "no_result", None

    def flush():
        text = "".join(current).strip()
        if text:
            chat.append(text)
        current.clear()

    for line in lines:
        try:
            event = json.loads(line)
        except ValueError:
            continue
        data = event.get("data") or {}
        update = data.get("update") or {}
        kind = update.get("sessionUpdate")
        if kind == "agent_message_chunk":
            current.append((update.get("content") or {}).get("text", ""))
        elif kind == "tool_call":
            flush()
        meta = (update.get("_meta") or {}).get("cli") or {}
        usage_pct = (meta.get("contextUsage") or {}).get("usagePercentage", usage_pct)
        for summary in meta.get("promptTurnSummaries") or []:
            credits += summary.get("usage", 0.0)
        if event.get("type") == "runFinished":
            session = data.get("sessionId")
            ok = data.get("status") == "success" and data.get("stopReason") == "end_turn"
            status = "ok" if ok else str(data.get("stopReason") or data.get("status"))
    flush()
    return {"chat": chat, "credits": credits, "session": session, "status": status, "usage_pct": usage_pct}


def write_agent(folder, arm, context, cli_model):
    os.makedirs(os.path.join(folder, ".agent", "agents"))
    agent = {
        "name": "rb", "description": "study agent", "prompt": system_prompt(arm), "tools": ["@rb"],
        "model": cli_model,
        "mcpServers": {"rb": {"command": "python", "args": [SERVER, os.path.join(folder, "state.json")]}},
    }
    ledger = "\n".join(ledger_chunk(i, FILLER_SEED) for i in range(CHUNKS[context]))
    if len(ledger) > RESOURCE_LIMIT:
        agent["prompt"] += f"\n\n{LEDGER_INTRO}\n{ledger}"
    elif ledger:
        with open(os.path.join(folder, "ledger.txt"), "w", encoding="utf-8") as f:
            f.write(ledger)
        agent["resources"] = ["file://ledger.txt"]
    with open(os.path.join(folder, ".agent", "agents", "rb.json"), "w", encoding="utf-8") as f:
        json.dump(agent, f)


def run_cli(folder, cli_model, effort, prompt, session=None):
    cmd = [AGENT_CLI, "chat", "--no-interactive", "--trust-workspace", "--agent", "rb", "--model", cli_model,
           "--effort", effort, "--trust-all-tools", "--require-mcp-startup", "--output-format", "stream-json"]
    if session:
        cmd += ["--resume-id", session]
    cmd.append(prompt)
    r = subprocess.run(cmd, cwd=folder, capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=TIMEOUT_S)
    return parse_events(r.stdout.splitlines())


def read_state(folder):
    path = os.path.join(folder, "state.json")
    if not os.path.exists(path):
        return {"files": {}, "asked": [], "calls": []}
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def attempt_from(run, state, asked_before, calls_before):
    return {"chat": run["chat"], "final": run["chat"][-1] if run["chat"] else "",
            "files": dict(state["files"]), "asked": state["asked"][asked_before:],
            "status": run["status"], "tool_calls": state["calls"][calls_before:]}


def run_trial_cli(scenario, pull, context, arm, rep, model, cli_model, effort):
    tid = trial_id(model, scenario["id"], pull, context, arm, rep)
    folder = tempfile.mkdtemp(prefix="rb-")
    try:
        write_agent(folder, arm, context, cli_model)
        run = run_cli(folder, cli_model, effort, scenario[pull])
        state = read_state(folder)
        first = attempt_from(run, state, 0, 0)
        first_grade = grade(first, scenario)
        credits, first_usage = run["credits"], run["usage_pct"]
        record = {"trial_id": tid, "model": model, "scenario": scenario["id"], "rule": scenario["rule"],
                  "pull": pull, "context": context, "arm": arm, "rep": rep,
                  "first": {**first, **first_grade}, "revised": False, "log": []}
        final, final_grade = first, first_grade
        if arm == "enforced" and first_grade["enforcer_flags"] and first["status"] == "ok":
            items = "\n".join(f"- {m}" for m in first_grade["enforcer_flags"].values())
            asked, calls = len(state["asked"]), len(state["calls"])
            run = run_cli(folder, cli_model, effort, FEEDBACK.format(items=items), run["session"])
            final = attempt_from(run, read_state(folder), asked, calls)
            final_grade = grade(final, scenario)
            credits += run["credits"]
            record["revised"] = True
        record["final"] = {**final, **final_grade}
        record["credits"] = credits
        record["context_usage_pct"] = first_usage
        return record
    finally:
        shutil.rmtree(folder, ignore_errors=True)


def on_target(context, usage_pct):
    """False when the context meter shows the trial did not get its filler."""
    low, high = METER_BANDS[context]
    return usage_pct is None or low <= usage_pct <= high


def cli_version():
    r = subprocess.run([AGENT_CLI, "--version"], capture_output=True, text=True)
    return r.stdout.strip()


def probe_cli():
    """Fail early if the CLI is missing."""
    if not shutil.which(AGENT_CLI) and not os.path.exists(AGENT_CLI):
        raise SystemExit(f"the CLI not found at {AGENT_CLI}; set AGENT_CLI.")
