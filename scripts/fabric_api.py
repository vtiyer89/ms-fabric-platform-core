"""Fabric REST helpers shared by the deploy-time scripts.

`nb_seed_metadata` deliberately carries its own copy of this logic. A Fabric notebook has no
import path back to platform-core at run time, so the duplication is forced rather than chosen —
see scripts/metadata_reader_source.py for the same constraint and the same reasoning. Keep the
two in step when changing resolution semantics.
"""

import os
import sys

import requests
from azure.identity import ClientSecretCredential

FABRIC_API = "https://api.fabric.microsoft.com/v1"
FABRIC_SCOPE = "https://api.fabric.microsoft.com/.default"

CREDENTIAL_VARIABLES = ("AZURE_CLIENT_ID", "AZURE_CLIENT_SECRET", "AZURE_TENANT_ID")


def session_for_service_principal():
    """Authenticated session, using the same three variables the deploy script uses."""
    missing = [name for name in CREDENTIAL_VARIABLES if not os.environ.get(name)]
    if missing:
        sys.exit(f"[error] missing credential variable(s): {', '.join(missing)}")

    credential = ClientSecretCredential(
        client_id=os.environ["AZURE_CLIENT_ID"],
        client_secret=os.environ["AZURE_CLIENT_SECRET"],
        tenant_id=os.environ["AZURE_TENANT_ID"],
    )
    session = requests.Session()
    session.headers.update({"Authorization": f"Bearer {credential.get_token(FABRIC_SCOPE).token}"})
    return session


def api_get_all(session, path):
    """GET a Fabric collection endpoint, following continuationToken pagination.

    A workspace with more items than fit one page returns the rest behind a token. Ignoring it
    silently truncates the listing and produces "not found" for items that do exist — a
    resolution failure that looks exactly like a misspelled name.
    """
    results, url, params = [], f"{FABRIC_API}{path}", {}
    while True:
        response = session.get(url, params=params, timeout=60)
        response.raise_for_status()
        body = response.json()
        results.extend(body.get("value", []))
        token = body.get("continuationToken")
        if not token:
            return results
        params = {"continuationToken": token}


def resolve_workspace_id(display_name, workspaces):
    """Display names are matched literally and case-sensitively, as fabric-cicd matched them."""
    matches = [w for w in workspaces if w.get("displayName") == display_name]
    if len(matches) > 1:
        raise ValueError(
            f"{len(matches)} workspaces are named {display_name!r}. Display names are the only "
            f"handle this framework has; rename one before deploying."
        )
    return matches[0]["id"] if matches else None


def resolve_item_id(display_name, item_type, items):
    match = next(
        (i for i in items if i.get("displayName") == display_name and i.get("type") == item_type),
        None,
    )
    return match["id"] if match else None
