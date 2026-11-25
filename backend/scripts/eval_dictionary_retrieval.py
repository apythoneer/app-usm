"""
Retrieval eval for the chat data dictionary: keyword vs embedding vs hybrid.

For each case in backend/eval/dictionary_retrieval.json, ranks the dictionary
with every mode and checks the expected entries are in the top-k (what the
prompt would get). Reports recall@k, "all expected found" rate, mean rank of
the first expected entry, and per-question misses.

Needs the backend importable and an Ollama with OLLAMA_EMBED_MODEL reachable via
the usual settings (OLLAMA_BASE_URL + OLLAMA_CRED_KEY/OLLAMA_API_KEY). No DB.

    cd backend && python scripts/eval_dictionary_retrieval.py [--k 8] [--weight 0.6]
"""

import argparse
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from app.core.config import get_settings  # noqa: E402
from app.services import dictionary as d  # noqa: E402

MODES = ("keyword", "embedding", "hybrid")


def name(e) -> str:
    return f"{e.table}.{e.column}" if e.column else e.table


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--k", type=int, default=get_settings().chat_dictionary_top_k)
    ap.add_argument("--weight", type=float, help="override CHAT_HYBRID_WEIGHT")
    ap.add_argument("--cases", default=os.path.join(os.path.dirname(HERE), "eval", "dictionary_retrieval.json"))
    ap.add_argument("--verbose", action="store_true", help="print every miss")
    args = ap.parse_args()
    if args.weight is not None:
        get_settings().chat_hybrid_weight = args.weight
    cases = json.load(open(args.cases, encoding="utf-8"))["cases"]
    print(f"{len(cases)} cases, k={args.k}, embed model={get_settings().ollama_embed_model}, "
          f"hybrid weight={get_settings().chat_hybrid_weight}")

    results = {}
    for mode in MODES:
        rows, t0 = [], time.monotonic()
        for c in cases:
            ranked, used = d.rank_entries(c["q"], mode)
            if used != mode:
                print(f"{mode}: UNAVAILABLE (fell back to {used}) — is {get_settings().ollama_embed_model} loaded?")
                rows = None
                break
            names = [name(e) for e, s in ranked if s > 0]
            top = names[:args.k]
            found = [x for x in c["expect"] if x in top]
            ranks = [names.index(x) + 1 for x in c["expect"] if x in names]
            rows.append({"q": c["q"], "style": c["style"], "expect": c["expect"], "found": found,
                         "first_rank": min(ranks) if ranks else None, "top": top})
        if rows is not None:
            results[mode] = (rows, (time.monotonic() - t0) / len(cases))

    def summary(rows):
        n_exp = sum(len(r["expect"]) for r in rows)
        recall = sum(len(r["found"]) for r in rows) / n_exp
        allf = sum(len(r["found"]) == len(r["expect"]) for r in rows) / len(rows)
        fr = [r["first_rank"] for r in rows if r["first_rank"]]
        return recall, allf, (sum(fr) / len(fr) if fr else float("nan"))

    print(f"\n{'mode':10s} {'subset':11s} {'recall@k':>9s} {'all-found':>10s} {'mean 1st rank':>14s} {'s/question':>11s}")
    for mode, (rows, per_q) in results.items():
        for subset in ("all", "direct", "paraphrase"):
            sub = rows if subset == "all" else [r for r in rows if r["style"] == subset]
            rec, allf, mr = summary(sub)
            print(f"{mode:10s} {subset:11s} {rec:9.0%} {allf:10.0%} {mr:14.1f} {per_q if subset == 'all' else 0:11.2f}"
                  .replace(" 0.00", "     "))

    for mode, (rows, _) in results.items():
        misses = [r for r in rows if len(r["found"]) < len(r["expect"])]
        print(f"\n{mode}: {len(misses)} question(s) missing an expected entry")
        for r in misses if (args.verbose or len(misses) <= 12) else misses[:12]:
            missing = [x for x in r["expect"] if x not in r["found"]]
            print(f"  [{r['style'][:4]}] {r['q']}\n         missing {missing}; top3 {r['top'][:3]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
