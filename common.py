"""Shared configuration, authentication and HTTP helpers for the POC scripts.

Configuration is read from environment variables. If a .env file sits next to these
scripts, it is loaded automatically; real environment variables always win.

Authentication:
  - Azure AI Search: uses AZURE_SEARCH_API_KEY if set, otherwise Microsoft Entra ID
    (DefaultAzureCredential, e.g. `az login`) with the https://search.azure.com scope.
  - Foundry / Azure OpenAI: uses FOUNDRY_API_KEY if set, otherwise Microsoft Entra ID
    with the https://cognitiveservices.azure.com scope.
"""
import json
import os
import sys
from pathlib import Path

import requests

HERE = Path(__file__).parent
PAYLOAD_DIR = HERE / "payloads"


def load_dotenv(path=HERE / ".env"):
    """Minimal .env reader so the scripts work the same on Windows, macOS and Linux.
    Existing environment variables take precedence, so a shell export still wins."""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if key.startswith("export "):
            key = key[len("export "):].strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if key and key not in os.environ:
            os.environ[key] = value


load_dotenv()

# Agentic retrieval features used here (query planning, query hints, retrieval
# instructions) are only in the preview API. 2026-04-01 (GA) supports minimal,
# extractive retrieval only.
SEARCH_API_VERSION = os.getenv("SEARCH_API_VERSION", "2026-08-01-preview")
AOAI_API_VERSION = os.getenv("AOAI_API_VERSION", "2024-10-21")

# ---------------------------------------------------------------------------
# Library configuration. To use your own SharePoint library, change this block.
# ---------------------------------------------------------------------------
LIBRARY_DESCRIPTION = "post-incident reviews from Contoso Platform Engineering (fictional sample data)"

NAME_COLUMN = "Name"                 # file name in the library; matched to PDF file names
ID_COLUMN = "ID"                     # unique item id
TITLE_COLUMN = "Title"
DATE_COLUMN = "Incident Date"
CONTENT_COLUMNS = ["Summary"]        # used as content when a row has no matching PDF

# (spreadsheet column, index field, facetable, multi-value separated by ';')
METADATA_COLUMNS = [
    ("Incident ID", "incidentId", False, False),
    ("Service", "service", True, False),
    ("Owning Team", "owningTeam", True, False),
    ("Severity", "severity", True, False),
    ("Root Cause Category", "rootCauseCategory", True, False),
    ("Contributing Factors", "contributingFactors", True, True),
    ("Detection Method", "detectionMethod", True, False),
    ("Change Related", "changeRelated", True, False),
    ("Environment", "environment", True, False),
    ("Region", "region", True, False),
]
METADATA_FIELDS = [field for _, field, _, _ in METADATA_COLUMNS]

INDEX_NAME = os.getenv("INDEX_NAME", "pir-reviews")
KS_BASELINE = "pir-ks-baseline"
KS_METADATA = "pir-ks-metadata"
KB_BASELINE = "pir-kb-baseline"
KB_METADATA = "pir-kb-metadata"

SEMANTIC_CONTENT_ONLY = "pir-semantic-content-only"
SEMANTIC_METADATA = "pir-semantic-metadata"

EMBEDDING_DIMENSIONS = int(os.getenv("FOUNDRY_EMBEDDING_DIMENSIONS", "1536"))


def env(name, default=None, required=True):
    value = os.getenv(name, default)
    if required and not value:
        sys.exit(f"Missing environment variable: {name} (see README, Part B)")
    return value


def search_endpoint():
    return env("AZURE_SEARCH_ENDPOINT").rstrip("/")


def foundry_endpoint():
    return env("FOUNDRY_RESOURCE_ENDPOINT").rstrip("/")


_credential = None


def _token(scope):
    global _credential
    if _credential is None:
        from azure.identity import DefaultAzureCredential
        # The Azure CLI can take longer than the SDK's 10 second default to return a
        # token on managed corporate machines, which surfaces as a confusing
        # CredentialUnavailableError("Failed to invoke the Azure CLI").
        _credential = DefaultAzureCredential(
            process_timeout=int(os.getenv("AZURE_CREDENTIAL_TIMEOUT", "60")))
    return _credential.get_token(scope).token


def search_headers():
    key = os.getenv("AZURE_SEARCH_API_KEY")
    if key:
        return {"Content-Type": "application/json", "api-key": key}
    return {"Content-Type": "application/json",
            "Authorization": f"Bearer {_token('https://search.azure.com/.default')}"}


def foundry_headers():
    key = os.getenv("FOUNDRY_API_KEY")
    if key:
        return {"Content-Type": "application/json", "api-key": key}
    return {"Content-Type": "application/json",
            "Authorization": f"Bearer {_token('https://cognitiveservices.azure.com/.default')}"}


def foundry_api_key_param():
    """If a Foundry key is set, pass it to Azure AI Search so the service can call the
    models. Otherwise the search service's managed identity is used, and it needs the
    'Cognitive Services User' role on the Foundry resource."""
    key = os.getenv("FOUNDRY_API_KEY")
    return {"apiKey": key} if key else {}


def search_request(method, path, body=None, ok=(200, 201, 204)):
    url = f"{search_endpoint()}/{path.lstrip('/')}"
    sep = "&" if "?" in url else "?"
    url = f"{url}{sep}api-version={SEARCH_API_VERSION}"
    resp = requests.request(method, url, headers=search_headers(),
                            data=json.dumps(body) if body is not None else None, timeout=120)
    if resp.status_code not in ok:
        print(f"\nERROR {resp.status_code} on {method} {path}")
        print(resp.text[:3000])
        resp.raise_for_status()
    return resp.json() if resp.text else {}


def save_payload(name, body):
    PAYLOAD_DIR.mkdir(exist_ok=True)
    path = PAYLOAD_DIR / f"{name}.json"
    path.write_text(json.dumps(body, indent=2, ensure_ascii=False), encoding="utf-8")
    return path
