"""Step 4: A/B test. Does metadata influence what Foundry IQ returns?

Each query is sent through the Foundry IQ retrieve API to BOTH knowledge bases:
  pir-kb-baseline  (metadata not used)   vs   pir-kb-metadata  (metadata used)

For each query we know which metadata a relevant report should carry (the 'expect'
rule, checked against the metadata export). The script reports how many of the top
5 distinct reports meet that rule in each knowledge base, and shows the boosts and
filters the query planner generated from the query hints.

Agentic retrieval uses an LLM for query planning, so results vary slightly between
runs. Run it two or three times before drawing conclusions.

Usage:
  python 4_test_foundryiq_queries.py                 # all queries
  python 4_test_foundryiq_queries.py --only 1 4      # selected queries
  python 4_test_foundryiq_queries.py --verbose       # also print both result lists
"""
import argparse
import json

import requests

import common as c

TOP_N = 5

# expect: list of groups; a report matches when EVERY group has at least one
# (field, substring) hit. Substring checks are case-insensitive.
TESTS = [
    {"q": "What have we learned from outages caused by configuration changes?",
     "expect": [[("rootCauseCategory", "configuration change")]]},
    {"q": "Which incidents did customers tell us about before our own monitoring noticed?",
     "expect": [[("detectionMethod", "customer report")]]},
    {"q": "Where did alert fatigue contribute to an incident?",
     "expect": [[("contributingFactors", "alert fatigue")]]},
    {"q": "Incidents caused by an expired certificate",
     "expect": [[("rootCauseCategory", "certificate expiry")]]},
    {"q": "Outages where the rollback didn't work",
     "expect": [[("contributingFactors", "untested rollback")]]},
    {"q": "What went wrong when a third-party or upstream provider failed?",
     "expect": [[("rootCauseCategory", "dependency failure")]]},
    {"q": "Production incidents caused by running out of capacity",
     "expect": [[("rootCauseCategory", "capacity")], [("environment", "production")]]},
    {"q": "Payments API incidents and what we learned",
     "expect": [[("service", "payments api")]]},
    {"q": "Configuration change incidents that customers reported",
     "expect": [[("rootCauseCategory", "configuration change")], [("detectionMethod", "customer report")]]},
    {"q": "Incidents where nobody knew which team owned the component",
     "expect": [[("contributingFactors", "unclear ownership")]]},
]


def retrieve(kb, ks, question):
    body = {
        "messages": [{"role": "user", "content": [{"type": "text", "text": question}]}],
        "knowledgeSourceParams": [{
            "knowledgeSourceName": ks, "kind": "searchIndex",
            "includeReferences": True, "includeReferenceSourceData": True,
        }],
        "includeActivity": True,
        "retrievalReasoningEffort": {"kind": "low"},   # query hints are not applied at 'minimal'
        "outputMode": "extractiveData",
    }
    try:
        return c.search_request("POST", f"knowledgebases/{kb}/retrieve", body)
    except requests.HTTPError as exc:
        # A retrieve can fail outright, for example when the planner generates a filter
        # the service rejects. Record it and carry on so one bad query doesn't abandon
        # the whole run.
        try:
            detail = exc.response.json().get("error", {}).get("message", "")
        except ValueError:
            detail = ""
        return {"_error": " ".join((detail or str(exc)).split())[:200]}


def top_reports(result):
    """Distinct parent reports in reference order (chunks of the same report collapse)."""
    seen, reports = set(), []
    for ref in result.get("references", []):
        data = ref.get("sourceData") or {}
        pid = data.get("parentId") or ref.get("docKey")
        if pid in seen:
            continue
        seen.add(pid)
        reports.append({**data, "_score": ref.get("rerankerScore")})
        if len(reports) == TOP_N:
            break
    return reports


def matches(report, expect):
    return all(any(sub.lower() in str(report.get(f, "")).lower() for f, sub in group) for group in expect)


def hint_activity(result):
    """Boosts/filters the planner generated from query hints, plus the subqueries issued."""
    out = []
    for a in result.get("activity", []):
        if a.get("type") != "searchIndex":
            continue
        args = a.get("searchIndexArguments", {})
        qh = a.get("queryHintProcessing") or args.get("queryHintProcessing") or {}
        out.append({"search": args.get("search"), "filter": args.get("filter"),
                    "generatedBoost": qh.get("generatedBoost"), "generatedFilter": qh.get("generatedFilter")})
    return out


def show(label, reports, expect):
    print(f"    {label}")
    for i, r in enumerate(reports, 1):
        mark = "+" if matches(r, expect) else " "
        print(f"      {mark} {i}. {r.get('title', '?')[:42]:<42} | {r.get('rootCauseCategory', '')[:20]:<20} "
              f"| {r.get('detectionMethod', '')[:20]}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", nargs="*", type=int, help="Query numbers to run (1-based)")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    docs = json.loads((c.HERE / "documents.json").read_text(encoding="utf-8"))
    parents = {d["parentId"]: d for d in docs if d["chunkIndex"] == 0}

    selected = [(i, t) for i, t in enumerate(TESTS, 1) if not args.only or i in args.only]
    summary, log = [], []

    for i, test in selected:
        print(f"\n[{i}] {test['q']}")
        base = retrieve(c.KB_BASELINE, c.KS_BASELINE, test["q"])
        meta = retrieve(c.KB_METADATA, c.KS_METADATA, test["q"])
        for label, result in (("baseline", base), ("metadata-aware", meta)):
            if result.get("_error"):
                print(f"    !! {label} retrieve FAILED: {result['_error']}")
        # Score against the prepared metadata by report id, so scoring doesn't depend on
        # which fields the knowledge source returns in sourceData. The prepared metadata
        # is merged last so it wins: sourceData may omit a field, or return it empty,
        # depending on the knowledge source's sourceDataFields.
        base_top = [{**r, **parents.get(r.get("parentId"), {})} for r in top_reports(base)]
        meta_top = [{**r, **parents.get(r.get("parentId"), {})} for r in top_reports(meta)]
        b = sum(matches(r, test["expect"]) for r in base_top)
        m = sum(matches(r, test["expect"]) for r in meta_top)
        possible = min(TOP_N, sum(matches(p, test["expect"]) for p in parents.values()))
        failed = bool(base.get("_error") or meta.get("_error"))
        summary.append((i, b, m, possible, failed))
        print(f"    matching reports in top {TOP_N}:  baseline {b}   metadata-aware {m}   "
              f"(best possible {possible}: the data has that many matching reports)")

        for act in hint_activity(meta):
            if act["generatedBoost"] or act["generatedFilter"]:
                print(f"    planner subquery: {act['search']}")
                if act["generatedBoost"]:
                    print(f"      generated boost:  {act['generatedBoost']}")
                if act["generatedFilter"]:
                    print(f"      generated filter: {act['generatedFilter']}")
        if args.verbose:
            show("baseline:", base_top, test["expect"])
            show("metadata-aware:", meta_top, test["expect"])

        log.append({"query": test["q"], "expect": test["expect"],
                    "baseline": {"top": base_top, "matches": b, "error": base.get("_error"),
                                 "activity": base.get("activity")},
                    "metadata": {"top": meta_top, "matches": m, "error": meta.get("_error"),
                                 "activity": meta.get("activity")}})

    print("\n" + "=" * 60)
    print(f"{'Query':<8}{'Baseline':>10}{'Metadata-aware':>17}{'Best possible':>16}")
    for i, b, m, p, failed in summary:
        note = "  (retrieve failed)" if failed else ""
        print(f"[{i}]{'':<5}{b:>10}{m:>17}{p:>16}{note}")
    tb, tm, tp = (sum(s[k] for s in summary) for k in (1, 2, 3))
    print(f"{'Total':<8}{tb:>10}{tm:>17}{tp:>16}")
    errors = [i for i, *_, failed in summary if failed]
    if errors:
        print(f"\n{len(errors)} quer{'y' if len(errors) == 1 else 'ies'} had a failed retrieve "
              f"(counted as 0): {errors}")
    (c.HERE / "results.json").write_text(json.dumps(log, indent=2, ensure_ascii=False), encoding="utf-8")
    print("\nFull results, including retrieval activity, written to results.json")


if __name__ == "__main__":
    main()
