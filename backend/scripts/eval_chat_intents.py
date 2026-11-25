"""
Routing eval for the chat intent router (app/services/chat_intents.py).

1. Embedding scores for every case (no LLM): what the best-matching intent is
   and how close, plus accuracy if we accepted on embeddings alone at a sweep
   of thresholds — use this to calibrate CHAT_INTENT_ACCEPT / _CONSIDER.
2. Full route() with the LLM chooser for the borderline band: accuracy,
   wrong-intent count (the costly error), fall-through count, latency.
3. --run: also execute each routed intent against the DB and print the answer.

Needs the backend importable, an Ollama with the embed + chat models (the usual
OLLAMA_* settings) and, for --run, the DB.

    cd backend && python scripts/eval_chat_intents.py [--run] [--no-llm]
"""

import argparse
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from app.core.config import get_settings  # noqa: E402
from app.services import chat_intents as ci  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cases", default=os.path.join(os.path.dirname(HERE), "eval", "chat_intents.json"))
    ap.add_argument("--no-llm", action="store_true", help="skip the full route() pass")
    ap.add_argument("--run", action="store_true", help="execute routed intents against the DB")
    args = ap.parse_args()
    s = get_settings()
    cases = json.load(open(args.cases, encoding="utf-8"))["cases"]

    scored = []
    for c in cases:
        sc = ci._example_scores(c["q"])
        if sc is None:
            print("embeddings unavailable — is", s.ollama_embed_model, "loaded?")
            return 1
        scored.append((c, sc))

    print(f"{len(cases)} cases. Embedding-only accuracy if we accept best >= T (margin {s.chat_intent_margin}):")
    print(f"{'T':>5s} {'right':>6s} {'WRONG':>6s} {'fall-through':>13s}")
    for t in (0.70, 0.72, 0.75, 0.78, 0.80, 0.82, 0.85):
        right = wrong = fall = 0
        for c, sc in scored:
            (top, s1), s2 = sc[0], sc[1][1]
            pick = top if (s1 >= t and s1 - s2 >= s.chat_intent_margin) else "other"
            if pick == c["expect"]:
                right += 1
            elif pick == "other":
                fall += 1
            else:
                wrong += 1
        print(f"{t:5.2f} {right:6d} {wrong:6d} {fall:13d}")

    print("\nper-case best embedding match:")
    for c, sc in scored:
        (top, s1), (sec, s2) = sc[0], sc[1]
        flag = "  " if top == c["expect"] else ("~ " if c["expect"] == "other" else "X ")
        print(f" {flag}{s1:.2f} {top:16s} (2nd {sec} {s2:.2f})  expect {c['expect']:16s} | {c['q']}")

    if args.no_llm:
        return 0

    from app.services.chat import ChatService
    svc = ChatService()
    print(f"\nfull route() — accept {s.chat_intent_accept}, consider {s.chat_intent_consider}, LLM chooser in between:")
    right = wrong = fall = 0
    lat = []
    for c in cases:
        t0 = time.monotonic()
        r = ci.route(c["q"], svc)
        dt = time.monotonic() - t0
        lat.append(dt)
        ok = r.intent == c["expect"]
        right += ok
        if not ok:
            if r.intent == "other":
                fall += 1
            else:
                wrong += 1
        tag = "ok " if ok else ("FALL" if r.intent == "other" else "WRONG")
        print(f" {tag:5s} {dt:5.1f}s {r.how:9s} {r.score:.2f} -> {r.intent:16s} expect {c['expect']:16s} | {c['q']}")
        if args.run and r.intent != "other":
            res = ci.answer(c["q"], svc)
            print("        " + (res["answer"] if res else "None").replace("\n", "\n        ")[:600])
    lat.sort()
    print(f"\nright {right}/{len(cases)}, WRONG intent {wrong}, fell through {fall}; "
          f"routing latency p50 {lat[len(lat) // 2]:.1f}s p95 {lat[int(len(lat) * 0.95)]:.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
