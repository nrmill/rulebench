"""A stdio MCP server exposing the study's simulated tools, for agent products that run their own loop.

  python mcp_tools.py STATE.json

State is one JSON file holding {"files": {}, "asked": [], "calls": []}, rewritten after
every call so the harness can grade it when the agent stops. Standard library only,
and importable as a plain script because the agent product launches it directly.
"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from rulebench.rules import ASK_RESULT, TOOLS  # noqa: E402


def load(path):
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return {"files": {}, "asked": [], "calls": []}


def save(path, state):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False)
    os.replace(tmp, path)


def call(state, name, args):
    state["calls"].append({"name": name, "input": args})
    if name == "ask_user":
        state["asked"].append(str(args.get("question", "")))
        return ASK_RESULT
    if name == "read_file":
        return state["files"].get(str(args.get("path", "")), "File not found.")
    if name == "write_file":
        state["files"][str(args.get("path", ""))] = str(args.get("content", ""))
        return "Written."
    if name == "move_file":
        src, dst = str(args.get("source", "")), str(args.get("destination", ""))
        if src not in state["files"]:
            return "File not found."
        state["files"][dst] = state["files"].pop(src)
        return "Moved."
    return "Unknown tool."


def handle(msg, path):
    method, params = msg.get("method"), msg.get("params") or {}
    if method == "initialize":
        return {"protocolVersion": params.get("protocolVersion", "2024-11-05"),
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "rulebench", "version": "1.0"}}
    if method == "tools/list":
        return {"tools": [{"name": t["name"], "description": t["description"],
                           "inputSchema": t["input_schema"]} for t in TOOLS]}
    if method == "tools/call":
        state = load(path)
        text = call(state, params.get("name", ""), params.get("arguments") or {})
        save(path, state)
        return {"content": [{"type": "text", "text": text}], "isError": False}
    if method == "ping":
        return {}
    return None


def main():
    path = sys.argv[1]
    for line in sys.stdin:
        if not line.strip():
            continue
        msg = json.loads(line)
        if "id" not in msg:
            continue  # A notification needs no reply.
        result = handle(msg, path)
        if result is None:
            reply = {"jsonrpc": "2.0", "id": msg["id"],
                     "error": {"code": -32601, "message": "Method not found"}}
        else:
            reply = {"jsonrpc": "2.0", "id": msg["id"], "result": result}
        sys.stdout.write(json.dumps(reply) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
