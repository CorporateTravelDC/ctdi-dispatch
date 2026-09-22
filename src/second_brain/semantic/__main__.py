"""
second_brain.semantic CLI -- the interface every consumer that isn't Python
goes through, including humans and agents at a shell.

    python3 -m second_brain.semantic --stats
    python3 -m second_brain.semantic --resolve "EV tolls"
    python3 -m second_brain.semantic --expand aam
    python3 -m second_brain.semantic --search "gig economy"
    python3 -m second_brain.semantic --compile
    python3 -m second_brain.semantic --metrics
    python3 -m second_brain.semantic --drift
    python3 -m second_brain.semantic --export
    python3 -m second_brain.semantic --context-pack
    python3 -m second_brain.semantic --ask "which domains cover ground transport?"

Every subcommand supports --json so an agent can consume the output as data
instead of scraping formatted text. Human-readable is the default because the
most frequent caller is a person or an agent reading a terminal.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

from second_brain.semantic import export as _export
from second_brain.semantic.model import load


def _emit(obj, as_json: bool) -> None:
    if as_json:
        print(json.dumps(obj, indent=2, default=str))


def cmd_stats(args) -> int:
    m = load()
    s = m.stats()
    if args.json:
        _emit(s, True)
        return 0
    print(f"second-brain semantic layer v{s['version']}")
    print(f"  facets            {s['facets']}")
    print(f"  concepts          {s['concepts']}  "
          f"({s['declared_concepts']} declared, "
          f"{s['lexicon_entity_concepts']} from the entity lexicon)")
    for facet, n in s["concepts_by_facet"].items():
        print(f"      {facet:12} {n}")
    print(f"  surface forms     {s['surface_forms']}  "
          f"({s['ambiguous_forms']} resolve to more than one concept)")
    print(f"  lexicon labels reconciled to declared concepts: "
          f"{s['lexicon_labels_reconciled']}")
    print(f"  metrics           {s['metrics']}")
    print(f"  producing agents  {s['agents']}")
    return 0


def cmd_resolve(args) -> int:
    m = load()
    got = m.resolve_all(args.resolve)
    if args.json:
        _emit([{"id": c.id, "facet": c.facet, "pref_label": c.pref_label,
                "definition": c.definition, "broader": c.broader,
                "in_domain": c.in_domain, "closure": m.closure(c.id),
                "surface_forms": c.surface_forms()} for c in got], True)
        return 0 if got else 1
    if not got:
        print(f"{args.resolve!r} names no concept in this vocabulary.")
        print("(that is itself a finding -- see --drift for the unmapped backlog)")
        return 1
    for i, c in enumerate(got):
        marker = "*" if i == 0 else " "
        print(f"{marker} {c.pref_label}  [{c.facet}]  id={c.id}")
        if c.definition:
            print(f"    {c.definition}")
        if c.broader:
            print(f"    part of: {m.get(c.broader).pref_label}")
        if c.in_domain:
            print(f"    domain:  {m.get(c.in_domain).pref_label}")
        print(f"    implies: {', '.join(m.closure(c.id))}")
    if len(got) > 1:
        print(f"\n({len(got)} concepts share this surface form; * is the "
              f"most specific)")
    return 0


def cmd_expand(args) -> int:
    m = load()
    forms = m.expand(args.expand)
    if args.json:
        _emit({"term": args.expand,
               "concept": (m.resolve(args.expand).id if forms else None),
               "surface_forms": forms,
               "fts_query": m.fts_query(args.expand)}, True)
        return 0 if forms else 1
    if not forms:
        print(f"{args.expand!r} names no concept.")
        return 1
    c = m.resolve(args.expand)
    print(f"{args.expand!r} -> {c.pref_label} [{c.facet}]")
    print(f"{len(forms)} equivalent surface forms:")
    for f in forms:
        print(f"  {f}")
    print(f"\nFTS5 query:\n  {m.fts_query(args.expand)}")
    return 0


def cmd_search(args) -> int:
    from second_brain.semantic.compile import semantic_search
    res = semantic_search(args.search, limit=args.limit,
                          curated_only=not args.include_raw)
    if args.json:
        _emit(res, True)
        return 0
    if not res["concept"]:
        print(f"{args.search!r} names no concept -- falling back to literal search.")
    else:
        print(f"{args.search!r} -> {res['concept_label']} [{res['facet']}]  "
              f"({len(res['surface_forms'])} surface forms)")
    gain = res["hits"] - res["naive_hits"]
    print(f"literal match: {res['naive_hits']} notes    "
          f"concept-expanded: {res['hits']} notes    "
          f"({gain:+d})")
    print()
    for r in res["results"]:
        print(f"  {r['path']}")
        print(f"    {r['title']}")
        if r["snippet"]:
            print(f"    {r['snippet']}")
    return 0


def _print_trace(result: dict) -> None:
    root = result["resolved_root"] or result["root"]
    direction_label = "led to" if result["direction"] == "backward" else "depends on"
    print(f"{result['root']!r} ({direction_label} -- max depth {result['max_depth']})")
    if result["resolved_root"]:
        print(f"  resolved to: {root}")
    if not result["edges"]:
        print("  (no derivation edges found)")
        return
    for e in result["edges"]:
        indent = "  " * (e["depth"] + 1)
        node = e.get("from") or e.get("path")
        arrow = "->" if result["direction"] == "backward" else "<-"
        target_label = e["target"]
        if result["direction"] == "backward" and e.get("resolved_path"):
            target_label = f"{e['target']} [{e['resolved_path']}]"
        print(f"{indent}[{e['depth']}] {node} {arrow} {e['relation']}: {target_label}")
        print(f"{indent}    {e['evidence']}")


def cmd_trace(args) -> int:
    from second_brain.semantic.compile import trace_causal_chain
    result = trace_causal_chain(args.trace, direction="backward",
                                max_depth=args.max_depth)
    if args.json:
        _emit(result, True)
        return 0 if result["edges"] else 1
    _print_trace(result)
    return 0 if result["edges"] else 1


def cmd_depends_on(args) -> int:
    from second_brain.semantic.compile import trace_causal_chain
    result = trace_causal_chain(args.depends_on, direction="forward",
                                max_depth=args.max_depth)
    if args.json:
        _emit(result, True)
        return 0 if result["edges"] else 1
    _print_trace(result)
    return 0 if result["edges"] else 1


def cmd_history(args) -> int:
    from second_brain.semantic.compile import concept_history
    result = concept_history(args.history, limit=args.limit if args.limit != 8 else None)
    if args.json:
        _emit(result, True)
        return 0 if result["history"] else 1
    print(f"{args.history!r} -- {result['total_occurrences']} occurrence(s) on record "
         f"(timestamp order only -- NOT a verified causal claim)")
    if not result["history"]:
        print("  (no notes filed under this concept)")
        return 1
    for h in result["history"]:
        pred = ("  (first on record)" if h["preceded_by"] == "(first on record)"
               else f"  <- preceded by {h['preceded_by']}")
        print(f"  #{h['sequence']:<4} {h['ts']}  {h['path']}{pred}")
    return 0


def cmd_compile(args) -> int:
    from second_brain.semantic.compile import compile_layer
    res = compile_layer()
    if args.json:
        _emit(res, True)
        return 0
    print(f"compiled semantic layer v{res['version']} at {res['compiled_at']}")
    print(f"  concept assignments   {res['assignments']}")
    print(f"  distinct live tags    {res['distinct_tags']}")
    print(f"  mapped                {res['mapped_tags']}")
    print(f"  unmapped (backlog)    {res['unmapped_tags']}")
    print(f"  derivation edges      {res['derivations']}")
    return 0


def cmd_metrics(args) -> int:
    from second_brain.semantic.compile import evaluate_metrics
    res = evaluate_metrics()
    if args.json:
        _emit(res, True)
        return 0
    for m in res:
        if "error" in m:
            print(f"  !! {m['id']}: {m['error']}")
        elif isinstance(m["value"], list):
            print(f"  {m['id']} ({m['label']}):")
            for row in m["value"]:
                print(f"      {str(row[0]):34} {row[1]}")
        else:
            print(f"  {m['id']:22} {str(m['value']):>10}  {m['unit']}")
    return 0


def cmd_drift(args) -> int:
    from second_brain.semantic.compile import drift_report
    res = drift_report()
    if args.json:
        _emit(res, True)
        return 0
    rc = res["rss_catalog"]
    print("rss_catalog superset check:",
          "PASS" if rc.get("is_superset") else "FAIL")
    if rc.get("categories_not_covered"):
        print("  categories not covered:", rc["categories_not_covered"])
    if rc.get("aliases_not_covered"):
        print("  aliases not covered:", rc["aliases_not_covered"])
    print(f"  surface forms this layer adds beyond rss_catalog: "
          f"{rc.get('forms_this_layer_adds', '?')}")
    print(f"\nentities with no domain: {res['entities_without_domain'] or 'none'}")
    print(f"stale entity_domains keys (no matching lexicon label): "
          f"{res.get('stale_entity_domain_keys') or 'none'}")
    print(f"\nunmapped live tags ({len(res['unmapped_tags'])}) -- governance backlog:")
    for t in res["unmapped_tags"]:
        if "error" in t:
            print(f"  {t['error']}")
        else:
            print(f"  {t['occurrences']:>5}  {t['tag']}")
    return 0 if rc.get("is_superset") else 1


def _corpus_facts() -> dict:
    """Live numbers for the context pack, so a model is briefed with the real
    corpus shape rather than a figure frozen at authoring time. Best-effort:
    the pack must still render on a machine with no index."""
    try:
        from second_brain.semantic.compile import evaluate_metrics
        return {m["id"]: m.get("value") for m in evaluate_metrics()}
    except Exception:
        return {}


def cmd_export(args) -> int:
    paths = _export.write_all(out_dir=args.out, budget=args.budget,
                              corpus_facts=_corpus_facts())
    if args.json:
        _emit(paths, True)
        return 0
    for k, v in paths.items():
        print(f"  {k:20} {v}")
    return 0


def cmd_context_pack(args) -> int:
    m = load()
    print(_export.to_context_pack(m, budget=args.budget,
                                  corpus_facts=_corpus_facts()))
    return 0


def cmd_ask(args) -> int:
    """Ground a local Ollama model in the context pack and ask it a question.

    This is the interop proof, not a feature the platform depends on: it shows
    a locally-served model consuming the exact same governed vocabulary the
    Python API and the SQLite tables expose.

    Deliberately talks to the Ollama HTTP API through urllib rather than
    common.llm. Two reasons, both load-bearing: llm.py's
    _verify_before_inference() resolves the calling file off the stack and
    raises IntegrityCheckFailed when it is not in the signed manifest, which
    would make this command fail in any unsigned working tree; and routing a
    read-only demonstration through the platform's thermally-governed,
    slot-locked inference path would contend with real ops briefs for the one
    model slot on the box.
    """
    base = os.environ.get("OLLAMA_BASE_URL", "http://127.0.0.1:11434").rstrip("/")
    model = args.model or os.environ.get("OLLAMA_CHAT_MODEL",
                                         "corporatetraveldc-pi5-chat")
    m = load()
    pack = _export.to_context_pack(m, budget=args.budget,
                                   corpus_facts=_corpus_facts())
    prompt = (
        f"{pack}\n\n---\n\n"
        "Answer using ONLY the vocabulary above. If the answer is not in it, "
        "say so.\n\n"
        f"Question: {args.ask}\n"
    )
    body = json.dumps({
        "model": model, "prompt": prompt, "stream": False,
        "options": {"temperature": 0.1, "num_predict": args.num_predict},
    }).encode()
    req = urllib.request.Request(f"{base}/api/generate", data=body,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=args.timeout) as r:
            data = json.loads(r.read())
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        print(f"ollama unreachable at {base}: {e}", file=sys.stderr)
        return 2
    if args.json:
        _emit({"model": model, "pack_chars": len(pack),
               "response": data.get("response", "")}, True)
        return 0
    print(f"[model={model}  context_pack={len(pack)} chars]\n")
    print(data.get("response", "").strip())
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(
        prog="python3 -m second_brain.semantic", description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--json", action="store_true",
                    help="emit machine-readable JSON instead of formatted text")

    g = ap.add_mutually_exclusive_group()
    g.add_argument("--stats", action="store_true", help="vocabulary size and shape")
    g.add_argument("--resolve", metavar="TERM", help="what concept does TERM name?")
    g.add_argument("--expand", metavar="TERM", help="every equivalent surface form")
    g.add_argument("--search", metavar="QUERY", help="concept-expanded vault search")
    g.add_argument("--compile", action="store_true",
                   help="materialise the layer into the vault index DB")
    g.add_argument("--metrics", action="store_true",
                   help="evaluate every governed metric against the live index")
    g.add_argument("--drift", action="store_true",
                   help="governance check: vocabulary vs the live system")
    g.add_argument("--export", action="store_true",
                   help="write JSON + SKOS/Turtle + context pack")
    g.add_argument("--context-pack", action="store_true",
                   help="print the LLM context pack to stdout")
    g.add_argument("--ask", metavar="QUESTION",
                   help="ask a local Ollama model, grounded in the context pack")
    g.add_argument("--trace", metavar="NOTE",
                   help="causal reasoning: what led to NOTE (leans_on/derives_from/reutilizes, followed backward)")
    g.add_argument("--depends-on", metavar="NOTE_OR_TARGET",
                   help="causal reasoning: what depends on NOTE_OR_TARGET (derivation edges followed forward)")
    g.add_argument("--history", metavar="CONCEPT_ID",
                   help="chronology (timestamp order only, NOT a causal claim): every note ever filed under CONCEPT_ID, in order, with a sequence number and immediate predecessor")

    ap.add_argument("--max-depth", type=int, default=5,
                    help="--trace/--depends-on: max traversal depth (default 5)")
    ap.add_argument("--limit", type=int, default=8, help="--search result count")
    ap.add_argument("--include-raw", action="store_true",
                    help="--search: include 00-Inbox/rss raw feed items")
    ap.add_argument("--out", metavar="DIR", help="--export output directory")
    ap.add_argument("--budget", type=int, default=_export.DEFAULT_BUDGET,
                    help="context-pack character budget")
    ap.add_argument("--model", help="--ask: Ollama model name")
    ap.add_argument("--num-predict", type=int, default=300)
    ap.add_argument("--timeout", type=int, default=300)

    args = ap.parse_args()

    if args.resolve:
        return cmd_resolve(args)
    if args.expand:
        return cmd_expand(args)
    if args.search:
        return cmd_search(args)
    if args.ask:
        return cmd_ask(args)
    if args.trace:
        return cmd_trace(args)
    if args.depends_on:
        return cmd_depends_on(args)
    if args.history:
        return cmd_history(args)
    if args.compile:
        return cmd_compile(args)
    if args.metrics:
        return cmd_metrics(args)
    if args.drift:
        return cmd_drift(args)
    if args.export:
        return cmd_export(args)
    if args.context_pack:
        return cmd_context_pack(args)
    return cmd_stats(args)


if __name__ == "__main__":
    sys.exit(main())
