"""Step 3: Register the index with Foundry IQ as two knowledge sources and two knowledge bases.

Both read the SAME index, so any difference in results comes from how metadata is used:

  pir-kb-baseline  -> pir-ks-baseline
      searches title + content only, content-only semantic configuration, no query hints.
      Approximates a native SharePoint knowledge source: metadata plays no part in ranking.

  pir-kb-metadata  -> pir-ks-metadata
      searches the metadata fields too, reranks with the metadata semantic configuration,
      and gives the query planner query hints: field-value boosts and filters built from
      the actual values in metadata_values.json.

Query hints (2026-08-01-preview) rules this script respects:
  - up to 5 fieldValue boost hints and 5 filter hints, each on a unique field
  - boost hints: up to 20 values, 128 chars each, 1,024 chars combined; boost > 1.0
  - filter hints: 128 chars per value, 2,048 chars combined
  - hints are best effort, need non-minimal reasoning effort, and filter hints return
    HTTP 400 with GPT-4o / GPT-4.1 family models, so use a GPT-5 family model.

Usage:
  python 3_create_foundryiq_kb.py            # create/update in Azure AI Search
  python 3_create_foundryiq_kb.py --dry-run  # write payloads/ only
"""
import argparse
import json

import common as c

BOOST_HINTS = [
    # field, boost, instructions
    ("rootCauseCategory", 3.0,
     "Boost when the user describes what caused an incident, such as a configuration change, "
     "capacity, a dependency failure, a software defect or an expired certificate."),
    ("contributingFactors", 2.5,
     "Boost when the user asks what made an incident worse or harder to resolve, such as alert fatigue, "
     "a missing runbook, an untested rollback or a single point of failure."),
    ("detectionMethod", 2.5,
     "Boost when the user asks how an incident was found or missed, for example reported by customers, "
     "caught by monitoring or alerting, or noticed by staff."),
    ("service", 2.0,
     "Boost when the user names a service or product."),
    ("owningTeam", 1.5,
     "Boost when the user names a team."),
]

FILTER_HINTS = [
    # Filter values are Edm.String, so generated OData must quote them. Saying so in the
    # instructions matters: without it, planners have produced severity eq Sev1 (unquoted)
    # and changeRelated eq true (boolean), and either one fails the whole retrieve.
    ("severity", "Filter only when the user names a severity level. Valid values are the quoted "
                 "strings 'Sev1', 'Sev2', 'Sev3' and 'Sev4'."),
    ("environment", "Filter only when the user names an environment. Valid values are the quoted "
                    "strings 'Production' and 'Staging'."),
    ("region", "Filter only when the user names a region, such as 'AU East' or 'US West'. "
               "Always compare to a quoted string."),
    ("changeRelated", "Filter only when the user asks specifically about change-related incidents. "
                      "This is a text field, not a boolean: the only valid values are the quoted "
                      "strings 'Yes' and 'No'. Never compare it to true or false."),
]

SEARCH_FIELDS_METADATA = ["title", "content", *[f for f in c.METADATA_FIELDS if f != "incidentId"]]

SOURCE_DATA_FIELDS = ["parentId", "title", "fileName", "incidentDate", *c.METADATA_FIELDS, "content"]

RETRIEVAL_INSTRUCTIONS = (
    f"The knowledge source holds {c.LIBRARY_DESCRIPTION}. Each review is classified by Service, "
    "Owning Team, Severity, Root Cause Category, Contributing Factors, Detection Method, Change Related, "
    "Environment and Region. When a question names a cause, contributing factor, detection method, "
    "service or team, prefer reviews whose classification fields match, not only reviews that mention "
    "the words in their narrative."
)


def fit_values(values, max_count, max_each=128, max_total=1024):
    """Most frequent values first, trimmed to the query hint limits."""
    picked, total = [], 0
    for v in values:
        if len(picked) >= max_count:
            break
        if not v or len(v) > max_each or total + len(v) > max_total:
            continue
        picked.append(v)
        total += len(v)
    return picked


def query_hints():
    values = json.loads((c.HERE / "metadata_values.json").read_text(encoding="utf-8"))
    missing = [f for f, *_ in BOOST_HINTS] + [f for f, _ in FILTER_HINTS]
    missing = sorted({f for f in missing if f not in values})
    if missing:
        raise SystemExit(f"Fields in BOOST_HINTS/FILTER_HINTS are not in metadata_values.json: {missing}. "
                         "They must be index fields declared in METADATA_COLUMNS (common.py). "
                         "Re-run 1_prepare_data.py after changing that mapping.")

    boosts, filters = [], []
    for field, boost, instructions in BOOST_HINTS:
        vals = fit_values(values[field], max_count=20)
        dropped = len(values[field]) - len(vals)
        if dropped:
            print(f"  note: {field} has {len(values[field])} values; boosting the {len(vals)} most frequent")
        boosts.append({"kind": "fieldValue", "field": field, "fieldValues": vals,
                       "boost": boost, "boostInstructions": instructions})
    for field, instructions in FILTER_HINTS:
        vals = fit_values(values[field], 1000, max_total=2048)
        dropped = len(values[field]) - len(vals)
        # Filter hints are meant to list every allowed value: the planner is told not to
        # filter on the field when a request doesn't map to a listed value. Dropping a
        # value therefore makes that value unfilterable, so warn instead of failing quietly.
        if dropped:
            print(f"  WARNING: {field} has {len(values[field])} values but only {len(vals)} fit the "
                  f"filter hint limit (128 chars per value, 2,048 combined). The {dropped} omitted "
                  f"value(s) can never be filtered on. Consider dropping {field} from FILTER_HINTS "
                  "or using a baseFilter instead.")
        filters.append({"field": field, "fieldValues": vals, "filterInstructions": instructions})
    return {"filters": filters, "boosts": boosts}


def knowledge_source(name, semantic_config, search_fields, hints=None, description=""):
    params = {
        "searchIndexName": c.INDEX_NAME,
        "semanticConfigurationName": semantic_config,
        "searchFields": [{"name": f} for f in search_fields],
        "sourceDataFields": [{"name": f} for f in SOURCE_DATA_FIELDS],
    }
    if hints:
        params["queryHints"] = hints
    return {"name": name, "kind": "searchIndex", "description": description, "searchIndexParameters": params}


def knowledge_base(name, ks_name, description, instructions):
    return {
        "name": name,
        "description": description,
        "knowledgeSources": [{"name": ks_name}],
        "retrievalInstructions": instructions,
        "outputMode": "extractiveData",
        "models": [{
            "kind": "azureOpenAI",
            "azureOpenAIParameters": {
                "resourceUri": c.foundry_endpoint(),
                "deploymentId": c.env("FOUNDRY_LLM_DEPLOYMENT"),
                "modelName": c.env("FOUNDRY_LLM_MODEL", "gpt-5.4-mini"),
                **c.foundry_api_key_param(),
            },
        }],
        "retrievalReasoningEffort": {"kind": "low"},
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="Write payloads/ and stop")
    args = ap.parse_args()

    model = c.env("FOUNDRY_LLM_MODEL", "gpt-5.4-mini")
    if model.startswith(("gpt-4o", "gpt-4.1")):
        raise SystemExit(f"FOUNDRY_LLM_MODEL={model}: filter query hints return HTTP 400 with GPT-4o/4.1 "
                         "family models. Deploy a GPT-5 family model (for example gpt-5.4-mini).")

    print("Building query hints from metadata_values.json ...")
    items = [
        ("knowledgesources", knowledge_source(
            c.KS_BASELINE, c.SEMANTIC_CONTENT_ONLY, ["title", "content"],
            description="Baseline: narrative text only, metadata not used for matching, boosting or reranking.")),
        ("knowledgesources", knowledge_source(
            c.KS_METADATA, c.SEMANTIC_METADATA, SEARCH_FIELDS_METADATA, hints=query_hints(),
            description="Post-incident reviews with metadata used for matching, semantic reranking and query hints.")),
        ("knowledgebases", knowledge_base(
            c.KB_BASELINE, c.KS_BASELINE, "Baseline for comparison: metadata does not influence ranking.",
            f"The knowledge source holds {c.LIBRARY_DESCRIPTION}.")),
        ("knowledgebases", knowledge_base(
            c.KB_METADATA, c.KS_METADATA, "Post-incident reviews with metadata-aware retrieval.",
            RETRIEVAL_INSTRUCTIONS)),
    ]

    for kind, body in items:
        print(f"{kind[:-1]:<16} {body['name']:<18} payload -> {c.save_payload(body['name'], body)}")
    if args.dry_run:
        return

    for kind, body in items:
        c.search_request("PUT", f"{kind}/{body['name']}", body)
        print(f"Created/updated {kind[:-1]} '{body['name']}'")

    ep = c.search_endpoint()
    print("\nEndpoints for the metadata-aware knowledge base:")
    print(f"  Retrieve API: POST {ep}/knowledgebases/{c.KB_METADATA}/retrieve?api-version={c.SEARCH_API_VERSION}")
    print(f"  MCP endpoint:      {ep}/knowledgebases/{c.KB_METADATA}/mcp?api-version={c.SEARCH_API_VERSION}")
    print("\nNext: python 4_test_foundryiq_queries.py")


if __name__ == "__main__":
    main()
