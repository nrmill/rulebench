"""Deterministic context filler: synthetic ledger files read through tool calls.

The filler is a run of read_file calls and results. The assistant turns carry no
prose, so the history demonstrates neither compliance nor violation. The text
holds no question marks, dashes used as punctuation, or banned words.
"""
import random

ITEMS = ["chain 11 speed", "brake pad set", "inner tube 700c", "cable housing", "bottom bracket",
         "rim tape", "tire lever", "chain lube", "cassette 10 speed", "handlebar tape",
         "derailleur hanger", "spoke nipple", "valve core", "saddle clamp", "pedal set"]
NOTES = ["counted by hand", "restocked", "reorder at 5", "held for repair", "moved from back room",
         "supplier delay", "member donation", "sample unit"]
LINES_PER_CHUNK = 110  # About 4,800 tokens per chunk, measured in the pilot.
CHUNKS = {"ctx0": 0, "ctx20k": 4, "ctx80k": 17}
INTRO = "Before today's requests, read the ledger files in order, one at a time."


def ledger_chunk(index, seed):
    rng = random.Random(f"{seed}:{index}")
    rows = []
    for _ in range(LINES_PER_CHUNK):
        rows.append(
            f"SKU {rng.randint(10000, 99999)} | {rng.choice(ITEMS)} | bay {rng.randint(1, 12)} | "
            f"qty {rng.randint(0, 60)} | counted 2026-{rng.randint(1, 12):02d}-{rng.randint(1, 28):02d} | "
            f"{rng.choice(NOTES)}"
        )
    return "\n".join(rows)


def filler_messages(context, seed):
    """Messages that end on a user turn holding the last tool result, or [] for ctx0.

    Longer contexts extend shorter ones, so ctx20k is a prefix of ctx80k.
    """
    n = CHUNKS[context]
    if n == 0:
        return []
    messages = [{"role": "user", "content": [{"type": "text", "text": INTRO}]}]
    for i in range(n):
        tool_id = f"toolu_filler{i:03d}"
        messages.append({"role": "assistant", "content": [{
            "type": "tool_use", "id": tool_id, "name": "read_file",
            "input": {"path": f"ledger/part-{i:03d}.txt"},
        }]})
        messages.append({"role": "user", "content": [{
            "type": "tool_result", "tool_use_id": tool_id, "content": ledger_chunk(i, seed),
        }]})
    return messages
