# Metadata-Aware Retrieval in Foundry IQ (POC)

Companion to *From Content to Context: Metadata-Aware Agentic RAG with Foundry IQ*.

This POC shows SharePoint metadata columns influencing what Foundry IQ returns, and
measures it with a side-by-side test against a baseline that ignores the metadata.

It ships with **synthetic sample data**: 64 post-incident reviews from *Contoso Platform
Engineering*, a fictional company. Each review is a PDF with SharePoint-style columns
(Service, Severity, Root Cause Category, Contributing Factors, Detection Method, and more).
To use your own library, see Part E.

## The problem the sample data reproduces

The review narratives describe what happened in plain language. A configuration-change
incident says *"a gateway routing rule was updated"*, not *"configuration change"*. Some
reviews also mention category words misleadingly, as real write-ups do (*"the team first
suspected a recent configuration change, but ..."*).

In the sample data, **none of the 19 configuration-change reviews contains the word
"configuration", and 6 other reviews do**. Retrieval over the text alone will favour the
wrong reviews. The Root Cause Category column says exactly which reviews are relevant, and
this POC makes Foundry IQ use it.

## How metadata influences ranking here

| Mechanism | Where | What it does for metadata |
|---|---|---|
| Individual metadata fields | Index | Each column is its own searchable, filterable field, never merged into one text blob |
| Metadata on every chunk | Index | Every chunk of a review carries that review's metadata, so any chunk can rank on it |
| Semantic configuration | Index | Metadata fields are prioritised keyword fields for the semantic (L2) reranker |
| `searchFields` | Knowledge source | Subqueries are matched against the metadata fields, not only the narrative |
| **Query hints** (preview) | Knowledge source | The query planner turns a question into field-value **boosts** and **filters** on the metadata |
| Retrieval instructions | Knowledge base | Tells the planner the taxonomy exists and should be preferred |
| Embeddings | Upload + vectorizer | Chunks are embedded at upload; the vectorizer embeds subqueries at query time |

**Why there is no scoring profile:** agentic retrieval does not honour an index's scoring
profiles (including `defaultScoringProfile`), so field weights in a scoring profile have
no effect on results returned through a Foundry IQ knowledge base.

## How the test works

Both knowledge bases read **the same index**. The only difference is whether metadata is used:

```
                      pir-reviews (one Azure AI Search index)
                      chunks: content + 10 metadata fields + embeddings
                                  |                    |
       pir-ks-baseline -----------+                    +----------- pir-ks-metadata
       searches title + content                        searches metadata fields too
       content-only semantic config                    metadata semantic config
       no query hints                                  query hints: 5 boosts, 4 filters
                |                                                    |
       pir-kb-baseline                                      pir-kb-metadata
       (like a native SharePoint source)                    (metadata-aware)
                \______________ 4_test_foundryiq_queries.py _________/
                     same 10 questions, top 5 reviews scored against the metadata
```

For each question the test knows which metadata a relevant review carries (for example
`Root Cause Category = Configuration change`) and counts how many of the top 5 reviews from
each knowledge base meet it. It also prints the boosts and filters the query planner generated.

---

## Part A: Azure setup

### A1. Azure AI Search
- Tier **Basic or above**, in a region that supports agentic retrieval.
- **Semantic ranker** enabled (Settings > Semantic ranker > Free or Standard).
- For Entra ID access, **Settings > Keys > API access control** set to *Both* or *Role-based*.

### A2. Foundry resource with two model deployments
| Deployment | Model | Used for |
|---|---|---|
| `text-embedding-3-small` | text-embedding-3-small | Embeddings at upload and query time |
| `gpt-5.4-mini` | **a GPT-5 family model** | Query planning and query hints |

GPT-4o and GPT-4.1 are deprecated for knowledge bases, and filter query hints return
HTTP 400 with them. Script 3 refuses to run with a GPT-4o/4.1 model name.

### A3. Permissions
Use API keys for a quick start, or Entra ID:

| Who | Role | Scope |
|---|---|---|
| You | Search Service Contributor, Search Index Data Contributor, Search Index Data Reader | Search service |
| You (if no `FOUNDRY_API_KEY`) | Cognitive Services OpenAI User | Foundry resource |
| Search service **system-assigned managed identity** (if no `FOUNDRY_API_KEY`) | Cognitive Services User | Foundry resource |

The search service calls the models itself (query planning and query-time
vectorisation), so it needs its own access: either its managed identity with the role
above, or the key passed through `FOUNDRY_API_KEY`.

---

## Part B: Local setup

Python 3.10 or later.

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env               # fill in the values
```

The scripts load `.env` from this folder automatically, so no `source`/`export` step is
needed. Variables already set in your shell take precedence over the file.

If you use Entra ID instead of keys, run `az login` first.

---

## Part C: Run the POC

```bash
# 1. Build chunk documents from the sample export and PDFs
python 1_prepare_data.py --metadata sample_data/post_incident_reviews.xlsx --pdfs sample_data/pdfs

# 2. Create the index, embed the chunks and upload them
python 2_create_index_and_upload.py

# 3. Create the two knowledge sources and two knowledge bases
python 3_create_foundryiq_kb.py

# 4. Run the A/B test
python 4_test_foundryiq_queries.py
python 4_test_foundryiq_queries.py --verbose       # also list both top-5 result sets
python 4_test_foundryiq_queries.py --only 1 9      # selected questions
```

`documents.json` and `metadata_values.json` are already included, so you can start at step 2.

Scripts 2 and 3 accept `--dry-run`, which writes the exact JSON they would send to
`payloads/` without calling Azure. Useful for review before anything is created.

---

## Part D: Reading the results

The format of the output for one question (real output from a run against Azure AI Search):

```
[1] What have we learned from outages caused by configuration changes?
    matching reports in top 5:  baseline 0   metadata-aware 5   (best possible 5: ...)
    planner subquery: outages caused by configuration change post-incident review
      generated filter: rootCauseCategory eq 'Configuration change'
```

- **baseline / metadata-aware**: how many of the top 5 distinct reviews carry the expected metadata.
- **best possible**: how many matching reviews exist in the data (capped at 5).
- **generated boost / filter**: evidence that the planner used the query hints for this question.

The full responses, including the retrieval activity log, are saved to `results.json`.

Measured totals across four runs of all ten questions, with `gpt-5.4-mini` for query
planning: **baseline 26 of 50, metadata-aware 39-41 of 50.** The gains come from the
questions whose answer is only in the metadata; questions whose wording already appears in
the narrative score the same in both.

### Things to know
- **Results vary between runs.** Query planning uses an LLM. Run the test two or three times.
- **Query hints are best effort.** The planner may not generate a boost for every question. For rules that must always apply (security, mandatory scope) use a `baseFilter` on the knowledge source or `filterAddOn` at query time, not a hint.
- **A generated filter can be invalid.** The planner can emit OData the service rejects, which fails the whole retrieve with HTTP 502 rather than falling back to an unfiltered search. Two forms seen in testing: `severity eq Sev1` (unquoted string) and `changeRelated eq true` (boolean against a text field). Mitigate it in the filter instructions by stating the field's type and quoting its values, as `FILTER_HINTS` in script 3 does. Script 4 records a failure and continues; application code should handle it too.
- **Limits.** Up to five field-value boost hints and five filter hints, each on a unique field, with up to 20 values per boost hint. Script 3 trims values to fit: it prints a note for boost hints, and a warning for filter hints, because a filter hint is meant to list every allowed value and an omitted value can never be filtered on.
- **Filters apply only when the question asks for them**, as set by the filter instructions.
- **Preview.** Query hints and query planning are in `2026-08-01-preview`. The GA `2026-04-01` API supports minimal, extractive retrieval only.

### Trying it from an agent
The metadata-aware knowledge base is exposed as an MCP server at:

```
{AZURE_SEARCH_ENDPOINT}/knowledgebases/pir-kb-metadata/mcp?api-version=2026-08-01-preview
```

In the Foundry portal, add it to an agent through a project connection (MCP tool,
`knowledge_base_retrieve`), ask the same questions, and compare with `pir-kb-baseline`.

---

## Part E: Using your own library

1. **Export the library's metadata** to Excel or CSV (in SharePoint: *Export > Export to CSV*),
   and download the documents into one folder.
2. **Edit the configuration block in `common.py`**:
   - `METADATA_COLUMNS`: your column names, the index field names, and which columns hold
     several values separated by `;`
   - `NAME_COLUMN`, `ID_COLUMN`, `TITLE_COLUMN`, `DATE_COLUMN`, `CONTENT_COLUMNS`
   - `LIBRARY_DESCRIPTION`, and the index, knowledge source and knowledge base names
3. **Edit `SEMANTIC_KEYWORD_FIELDS`** in script 2: your most important classification
   fields, most important first.
4. **Edit `BOOST_HINTS` and `FILTER_HINTS`** in script 3: pick up to five fields to boost
   (the classification users ask about) and up to five to filter (scoping fields such as
   region or business unit), and write instructions that say when each applies.
5. **Rewrite `TESTS`** in script 4 with questions your users ask, and the metadata a
   correct answer carries.

PDFs are matched to rows when the file name is **exactly** the `NAME_COLUMN` value. Script 1
prints how many matched.

---

## Part F: From POC to production

Production needs a pipeline that keeps the index in sync with the SharePoint library.

1. **Azure AI Search SharePoint indexer (preview).** Set `additionalColumns` in the data
   source query to pull the custom columns, and use a skillset for chunking and
   embeddings. The least code, but the indexer is preview: no SLA, not recommended for
   production workloads.
2. **Azure Function with Microsoft Graph (recommended for production).** On a timer, use
   Graph delta queries to find changed items. Read each file and its `listItem.fields`,
   then run the same logic as scripts 1 and 2: extract, chunk, attach metadata to every
   chunk, embed, upload with `mergeOrUpload`. Delete chunks when items are removed.
3. **Logic App**, if a low-code option is preferred (awkward for text extraction).
4. **Data Factory / Fabric pipeline**, if SharePoint is one of many sources already in a data platform.

Also plan for:
- **Permissions.** A custom index does not inherit SharePoint permissions. Carry ACLs into
  the index (document-level access control) or confirm that everyone who can use the
  agent may see every document.
- **Taxonomy changes.** Query hint values come from `metadata_values.json`. Re-run script 3
  when new category values are added.

---

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| 400 creating a knowledge source mentioning `queryHints` | API version is not `2026-08-01-preview`, or a hint breaks a limit |
| 400 on retrieve mentioning filters | The knowledge base model is GPT-4o or GPT-4.1. Deploy a GPT-5 family model |
| 401/403 on retrieve, planning or vectorizer errors | The search service cannot reach the Foundry models. Set `FOUNDRY_API_KEY`, or give its managed identity *Cognitive Services User* |
| `CredentialUnavailableError: Failed to invoke the Azure CLI` | `az` is slower than the SDK's timeout on some managed machines. The scripts allow 60s; raise it with `AZURE_CREDENTIAL_TIMEOUT` |
| `Failed to list key. disableLocalAuth is set to be true` | The Foundry resource has key auth disabled. Leave `FOUNDRY_API_KEY` unset and use Entra ID, and give the search service's managed identity *Cognitive Services User* |
| 502 on retrieve with `Invalid filter value` | The planner generated malformed OData for a filter hint, for example an unquoted string or `eq true` against a text field. State the field's type and quote its values in `filterInstructions` |
| Semantic errors | Semantic ranker is disabled on the search service |
| No `generated boost` lines | The planner chose not to use a hint for that question (best effort), or reasoning effort is `minimal` |
| `PDFs matched by filename: 0` | PDF file names don't exactly match the `Name` column |
| `Columns not found` from script 1 | Your export's column names differ from `METADATA_COLUMNS` in `common.py` |
| Embedding 429 errors | Script 2 retries automatically; raise the embedding deployment's rate limit for large libraries |

## Cleanup

```bash
for p in knowledgebases/pir-kb-baseline knowledgebases/pir-kb-metadata \
         knowledgesources/pir-ks-baseline knowledgesources/pir-ks-metadata indexes/pir-reviews; do
  curl -X DELETE "$AZURE_SEARCH_ENDPOINT/$p?api-version=2026-08-01-preview" -H "api-key: $AZURE_SEARCH_API_KEY"
done
```

Delete knowledge bases before their knowledge sources, and knowledge sources before the index.

## Files

| File | Purpose |
|---|---|
| `common.py` | Library configuration (columns, names), authentication, HTTP helpers |
| `1_prepare_data.py` | Metadata export + PDFs -> chunk documents with metadata; distinct values for query hints |
| `2_create_index_and_upload.py` | Index (fields, semantic configurations, vectorizer), embeddings, upload |
| `3_create_foundryiq_kb.py` | Baseline and metadata-aware knowledge sources and knowledge bases |
| `4_test_foundryiq_queries.py` | A/B test of 10 questions through the Foundry IQ retrieve API |
| `sample_data/post_incident_reviews.xlsx` | Synthetic library export (64 reviews) |
| `sample_data/pdfs/` | Synthetic review PDFs |
| `sample_data/generate_sample_data.py` | Regenerates the sample data |
| `documents.json`, `metadata_values.json` | Prepared from the sample data |

## References
- [Create a search index knowledge source, including query hints](https://learn.microsoft.com/en-us/azure/search/agentic-knowledge-source-how-to-search-index)
- [Create a knowledge base](https://learn.microsoft.com/en-us/azure/search/agentic-retrieval-how-to-create-knowledge-base)
- [Query a knowledge base via API or MCP](https://learn.microsoft.com/en-us/azure/search/agentic-retrieval-how-to-retrieve)
- [Agentic retrieval overview](https://learn.microsoft.com/en-us/azure/search/agentic-retrieval-overview)
- [Configure semantic ranker](https://learn.microsoft.com/en-us/azure/search/semantic-how-to-configure)
- [Connect agents to Foundry IQ knowledge bases](https://learn.microsoft.com/en-us/azure/foundry/agents/how-to/foundry-iq-connect)
- [Index data from SharePoint document libraries](https://learn.microsoft.com/en-us/azure/search/search-howto-index-sharepoint-online)
