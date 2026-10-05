"""Step 1: Merge SharePoint metadata and document text into chunk-level search documents.

Every chunk carries its parent document's metadata as individual fields. This is the
core of the pattern: boosts and semantic reranking act on the chunks a subquery
returns, so a chunk can only rank on its metadata if it carries that metadata.

Inputs:
  --metadata  Excel or CSV export of the SharePoint library (one row per document)
  --pdfs      Optional folder of the library's PDFs. A PDF is matched to a row when its
              filename equals the row's Name column exactly. Rows without a matching
              PDF use the CONTENT_COLUMNS (see common.py) as content.

The column-to-field mapping lives in common.py (METADATA_COLUMNS).

Outputs:
  documents.json        chunk documents (no vectors yet; step 2 embeds them)
  metadata_values.json  distinct values per metadata field, most frequent first,
                        used to build query hints in step 3

Example (sample data):
  python 1_prepare_data.py --metadata sample_data/post_incident_reviews.xlsx --pdfs sample_data/pdfs
"""
import argparse
import json
import re
from collections import Counter
from pathlib import Path

import pandas as pd

import common as c

CHUNK_CHARS = 1500      # roughly 300 to 400 tokens: keeps metadata inside the reranker input
CHUNK_OVERLAP = 200


def clean(value):
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    text = str(value).strip()
    # Backslashes complicate the Lucene boost syntax that query hints generate.
    text = re.sub(r"\s*\\\s*", " / ", text)
    return re.sub(r"[ \t]+", " ", text).strip()


def split_multi(value):
    return [v for v in (clean(p) for p in re.split(r"[;\n]", clean(value))) if v]


def chunk_text(text):
    text = re.sub(r"\n{3,}", "\n\n", text.strip())
    if len(text) <= CHUNK_CHARS:
        return [text]
    chunks, start = [], 0
    while start < len(text):
        end = min(start + CHUNK_CHARS, len(text))
        if end < len(text):
            window = text[start:end]
            cut = max(window.rfind("\n\n"), window.rfind(". "))   # prefer paragraph, then sentence
            if cut > CHUNK_CHARS * 0.5:
                end = start + cut + 1
        chunks.append(text[start:end].strip())
        if end >= len(text):
            break
        start = max(end - CHUNK_OVERLAP, start + 1)
    return [ch for ch in chunks if ch]


def read_pdf(path):
    from pypdf import PdfReader
    return "\n\n".join((page.extract_text() or "") for page in PdfReader(str(path)).pages)


def read_table(path):
    return pd.read_csv(path) if str(path).lower().endswith(".csv") else pd.read_excel(path)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--metadata", required=True, help="Excel or CSV export of the SharePoint library")
    ap.add_argument("--pdfs", help="Folder containing the library's PDF files")
    args = ap.parse_args()

    df = read_table(args.metadata)
    missing = [col for col, *_ in c.METADATA_COLUMNS if col not in df.columns]
    if missing:
        raise SystemExit(f"Columns not found in {args.metadata}: {missing}. Update METADATA_COLUMNS in common.py.")

    pdf_index = {p.name: p for p in Path(args.pdfs).glob("*.pdf")} if args.pdfs else {}
    documents, values, pdf_matches = [], {f: Counter() for f in c.METADATA_FIELDS}, 0

    for n, row in df.iterrows():
        name = clean(row.get(c.NAME_COLUMN))
        parent_id = f"doc{clean(row.get(c.ID_COLUMN)) or n}"
        meta = {"parentId": parent_id, "fileName": name,
                "title": clean(row.get(c.TITLE_COLUMN)) or Path(name).stem}

        for col, field, _, multi in c.METADATA_COLUMNS:
            items = split_multi(row.get(col)) if multi else [clean(row.get(col))]
            items = [i for i in items if i]
            values[field].update(items)
            # Multi-value columns are stored as one '; '-joined string: query hint boosts
            # match phrases inside it, and it stays a single keyword field for the reranker.
            meta[field] = "; ".join(items)

        date = row.get(c.DATE_COLUMN)
        meta["incidentDate"] = pd.to_datetime(date).strftime("%Y-%m-%dT00:00:00Z") if pd.notna(date) else None

        if name in pdf_index:
            content, meta["contentSource"] = read_pdf(pdf_index[name]), "pdf"
            pdf_matches += 1
        else:
            content = "\n\n".join(clean(row.get(col)) for col in c.CONTENT_COLUMNS if clean(row.get(col)))
            meta["contentSource"] = "spreadsheet"

        for i, chunk in enumerate(chunk_text(content or meta["title"])):
            documents.append({"id": f"{parent_id}-{i:03d}", "chunkIndex": i, "content": chunk, **meta})

    (c.HERE / "documents.json").write_text(json.dumps(documents, indent=2, ensure_ascii=False), encoding="utf-8")
    (c.HERE / "metadata_values.json").write_text(
        json.dumps({f: [v for v, _ in cnt.most_common()] for f, cnt in values.items()}, indent=2, ensure_ascii=False),
        encoding="utf-8")

    print(f"Rows in export:           {len(df)}")
    print(f"PDFs matched by filename: {pdf_matches}" + ("" if args.pdfs else " (no --pdfs folder given)"))
    print(f"Chunk documents written:  {len(documents)}  -> documents.json")
    print("\nMetadata coverage (rows with a value) and distinct values:")
    firsts = [d for d in documents if d["chunkIndex"] == 0]
    for col, field, _, _ in c.METADATA_COLUMNS:
        print(f"  {col:<24} {sum(1 for d in firsts if d[field]):>3}/{len(df)}   distinct: {len(values[field])}")


if __name__ == "__main__":
    main()
