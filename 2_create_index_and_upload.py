"""Step 2: Create the Azure AI Search index and upload chunk documents with embeddings.

What the index does for metadata:
  - Every metadata column is its own searchable + filterable field (standard analyzer),
    so query hints can boost and filter on it.
  - Semantic configuration 'pir-semantic-metadata' lists the metadata fields as
    prioritised keyword fields, so the semantic (L2) reranker sees them.
  - Semantic configuration 'pir-semantic-content-only' uses title + content only. The
    baseline knowledge source uses it to approximate "no metadata in ranking".

Why no scoring profile: agentic retrieval does not honour scoring profiles
(including defaultScoringProfile), so one would have no effect on Foundry IQ results.

Embeddings are generated here at upload time (push model) and the index also has an
Azure OpenAI vectorizer so Foundry IQ can vectorise subqueries at query time.

Usage:
  python 2_create_index_and_upload.py            # create index, embed, upload
  python 2_create_index_and_upload.py --dry-run  # write the index payload only
"""
import argparse
import json
import time

import requests

import common as c

# Order matters: fields are concatenated into the reranker input in priority order and
# later ones can be truncated, so the most important classification comes first.
SEMANTIC_KEYWORD_FIELDS = [
    "rootCauseCategory", "contributingFactors", "detectionMethod", "service", "severity",
]


def string_field(name, searchable=True, filterable=True, facetable=False, sortable=False, key=False):
    return {"name": name, "type": "Edm.String", "key": key, "searchable": searchable,
            "filterable": filterable, "facetable": facetable, "sortable": sortable, "retrievable": True}


def index_definition():
    fields = [
        string_field("id", searchable=False, key=True),
        string_field("parentId", searchable=False),
        {"name": "chunkIndex", "type": "Edm.Int32", "filterable": True, "sortable": True, "retrievable": True},
        string_field("fileName", searchable=False),
        string_field("title"),
        string_field("content", filterable=False),
        string_field("contentSource", searchable=False),
        {"name": "incidentDate", "type": "Edm.DateTimeOffset", "filterable": True, "sortable": True,
         "facetable": False, "retrievable": True},
        # Standard analyzer (the default) on purpose: query hint boosts require a
        # searchable field with a standard or language analyzer.
        *[string_field(field, facetable=facet) for _, field, facet, _ in c.METADATA_COLUMNS],
        {"name": "contentVector", "type": "Collection(Edm.Single)", "searchable": True,
         "retrievable": False, "stored": False, "dimensions": c.EMBEDDING_DIMENSIONS,
         "vectorSearchProfile": "pir-vector-profile"},
    ]
    return {
        "name": c.INDEX_NAME,
        "fields": fields,
        "semantic": {
            "defaultConfiguration": c.SEMANTIC_METADATA,
            "configurations": [
                {"name": c.SEMANTIC_METADATA, "prioritizedFields": {
                    "titleField": {"fieldName": "title"},
                    "prioritizedContentFields": [{"fieldName": "content"}],
                    "prioritizedKeywordsFields": [{"fieldName": f} for f in SEMANTIC_KEYWORD_FIELDS]}},
                {"name": c.SEMANTIC_CONTENT_ONLY, "prioritizedFields": {
                    "titleField": {"fieldName": "title"},
                    "prioritizedContentFields": [{"fieldName": "content"}],
                    "prioritizedKeywordsFields": []}},
            ],
        },
        "vectorSearch": {
            "algorithms": [{"name": "pir-hnsw", "kind": "hnsw"}],
            "profiles": [{"name": "pir-vector-profile", "algorithm": "pir-hnsw", "vectorizer": "pir-vectorizer"}],
            "vectorizers": [{
                "name": "pir-vectorizer", "kind": "azureOpenAI",
                "azureOpenAIParameters": {
                    "resourceUri": c.foundry_endpoint(),
                    "deploymentId": c.env("FOUNDRY_EMBEDDING_DEPLOYMENT"),
                    "modelName": c.env("FOUNDRY_EMBEDDING_MODEL", "text-embedding-3-small"),
                    **c.foundry_api_key_param(),
                },
            }],
        },
    }


def embed(texts):
    url = (f"{c.foundry_endpoint()}/openai/deployments/{c.env('FOUNDRY_EMBEDDING_DEPLOYMENT')}"
           f"/embeddings?api-version={c.AOAI_API_VERSION}")
    for attempt in range(6):
        resp = requests.post(url, headers=c.foundry_headers(), json={"input": texts}, timeout=120)
        if resp.status_code == 429:
            time.sleep(2 ** attempt)
            continue
        resp.raise_for_status()
        return [d["embedding"] for d in sorted(resp.json()["data"], key=lambda d: d["index"])]
    resp.raise_for_status()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="Write payloads/index.json and stop")
    args = ap.parse_args()

    definition = index_definition()
    print(f"Index payload -> {c.save_payload('index', definition)}")
    if args.dry_run:
        return

    print(f"Creating index '{c.INDEX_NAME}' ...")
    c.search_request("PUT", f"indexes/{c.INDEX_NAME}", definition)

    docs = json.loads((c.HERE / "documents.json").read_text(encoding="utf-8"))
    print(f"Embedding and uploading {len(docs)} chunk documents ...")
    batch_size = 16
    for start in range(0, len(docs), batch_size):
        batch = docs[start:start + batch_size]
        # Embed title + content so short chunks still carry the incident's subject.
        vectors = embed([f"{d['title']}\n\n{d['content']}" for d in batch])
        actions = [{"@search.action": "mergeOrUpload", **d, "contentVector": v} for d, v in zip(batch, vectors)]
        result = c.search_request("POST", f"indexes/{c.INDEX_NAME}/docs/index", {"value": actions}, ok=(200, 207))
        failed = [r for r in result.get("value", []) if not r.get("status")]
        print(f"  {start + len(batch):>4}/{len(docs)} uploaded" + (f", {len(failed)} FAILED" if failed else ""))
        for r in failed[:3]:
            print("   ", r.get("key"), r.get("errorMessage"))

    time.sleep(3)
    count = c.search_request("GET", f"indexes/{c.INDEX_NAME}/stats")
    print(f"\nDone. Index document count (may lag a few seconds): {count.get('documentCount')}")


if __name__ == "__main__":
    main()
