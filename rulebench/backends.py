"""Model backends. Both return {"content": [blocks], "stop_reason": str, "usage": dict, "model": str}.

MockBackend needs no key and spends nothing. It plants known effects so the whole
pipeline, including the analysis, can be checked against a known answer. Its numbers
are invented and must never be read as results.
"""
import random

EM = "—"

PRICES = {  # USD per million tokens: input, output, cache read, 5-minute cache write.
    "claude-haiku-5-5": (0.10, 0.50, 0.01, 0.125),
    "claude-sonnet-5-5": (2.00, 10.00, 0.20, 2.50),
}


def cost_usd(model, usage):
    p_in, p_out, p_read, p_write = PRICES.get(model, (0, 0, 0, 0))
    return (usage.get("input_tokens", 0) * p_in + usage.get("output_tokens", 0) * p_out
            + usage.get("cache_read_input_tokens", 0) * p_read
            + usage.get("cache_creation_input_tokens", 0) * p_write) / 1e6


class AnthropicBackend:
    """Real API calls. The refusal fallback is left off so one trial stays one model."""

    def __init__(self, model, effort="medium", max_tokens=4000):
        import anthropic
        self.client = anthropic.Anthropic(max_retries=4)
        self.model, self.effort, self.max_tokens = model, effort, max_tokens

    def complete(self, system, tools, messages, meta):
        r = self.client.messages.create(
            model=self.model, max_tokens=self.max_tokens, system=system, tools=tools,
            messages=messages, thinking={"type": "adaptive"},
            output_config={"effort": self.effort},
        )
        u = r.usage
        return {
            "content": [b.model_dump(exclude_none=True) for b in r.content],
            "stop_reason": r.stop_reason,
            "model": r.model,
            "usage": {
                "input_tokens": u.input_tokens,
                "output_tokens": u.output_tokens,
                "cache_read_input_tokens": u.cache_read_input_tokens or 0,
                "cache_creation_input_tokens": u.cache_creation_input_tokens or 0,
            },
        }

    def count_tokens(self, system, tools, messages):
        return self.client.messages.count_tokens(
            model=self.model, system=system, tools=tools, messages=messages).input_tokens


# Planted effects for the mock, on the probability of breaking the target rule.
MOCK_BASE = {"neutral": 0.03, "pull": 0.25}
MOCK_CONTEXT_ADD = {"ctx0": 0.0, "ctx20k": 0.05, "ctx80k": 0.15}  # Applies under pull only.
MOCK_ANNOUNCE_MULT = 0.8
MOCK_FIX = 0.9     # Share of revisions that truly fix the violation.
MOCK_HIDDEN = 0.3  # Share of first-attempt violations visible only to the audit grader.


def _text(t):
    return {"type": "text", "text": t}


def _tool(i, name, **kw):
    return {"type": "tool_use", "id": f"toolu_mock{i}", "name": name, "input": kw}


class MockBackend:
    def __init__(self, model="mock-model", seed=0):
        self.model, self.seed = model, seed

    def complete(self, system, tools, messages, meta):
        rng = random.Random(f"{self.seed}:{meta['trial_id']}:{meta['attempt']}:{meta['step']}")
        p = MOCK_BASE[meta["pull"]]
        if meta["pull"] == "pull":
            p += MOCK_CONTEXT_ADD[meta["context"]]
        if meta["arm"] == "enforced":
            p *= MOCK_ANNOUNCE_MULT
        violate = rng.random() < p
        hidden = rng.random() < MOCK_HIDDEN
        if meta["attempt"] == 1:  # A revision either fixes the problem or hides it from the enforcer.
            violate, hidden = rng.random() >= MOCK_FIX, True
        usage = {"input_tokens": 100, "output_tokens": 50,
                 "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}
        out = {"stop_reason": "end_turn", "model": self.model, "usage": usage}
        must = " ".join(meta["must_include"])
        rule = meta["rule"]

        if rule == "workbench_path":
            if meta["step"] == 0:
                path = "workbench/note.txt"
                if violate:
                    path = "workbench/../reports/note.txt" if hidden else "reports/note.txt"
                out["content"] = [_tool(meta["attempt"], "write_file", path=path, content=must)]
                out["stop_reason"] = "tool_use"
                return out
            out["content"] = [_text("Saved.")]
            return out

        body = f"Done: {must}."
        if violate:
            if rule == "ask_tool":
                body += " Please confirm the details." if hidden else " What day works?"
            elif rule == "no_dash":
                body += f" Fresh {EN_DASH_OR_EM(hidden)} and ready."
            elif rule == "banned_words":
                body += " It keeps empowering riders." if hidden else " It is vital."
            elif rule == "reply_length":
                body += " word" * 130
        out["content"] = [_text(body)]
        return out

    def count_tokens(self, system, tools, messages):
        chars = len(str(system)) + len(str(tools)) + len(str(messages))
        return chars // 3


def EN_DASH_OR_EM(hidden):
    return "–" if hidden else EM
