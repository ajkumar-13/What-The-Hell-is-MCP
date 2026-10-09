"""Post 20, section 7: a Client ID Metadata Document, and the rules on its URL.

Run from the series root:
    uv run python posts/20-authorization/snippets/client_id_document.py

Prints an example document for a client whose `client_id` is the HTTPS URL the
document is served from, checks that the SDK's client-registration model
accepts it, checks the `client_id` against the URL it would be fetched from,
and runs the SDK client's own test of whether a URL may serve as a `client_id`
on five candidates. Nothing is fetched and nothing is served.
"""

from __future__ import annotations

import json

from mcp.client.auth.utils import is_valid_client_metadata_url
from mcp.shared.auth import OAuthClientInformationFull

URL = "https://app.example.com/oauth/client-metadata.json"

DOCUMENT = {
    "client_id": URL,
    "client_name": "Example MCP Client",
    "redirect_uris": ["http://127.0.0.1:3000/callback"],
    "grant_types": ["authorization_code"],
    "response_types": ["code"],
    "token_endpoint_auth_method": "none",
}


def main() -> None:
    print(json.dumps(DOCUMENT, indent=2))
    print()
    info = OAuthClientInformationFull.model_validate(DOCUMENT)
    print(f"OAuthClientInformationFull accepts it: client_id={info.client_id}, "
          f"token_endpoint_auth_method={info.token_endpoint_auth_method}")
    print(f"client_id equals the URL it is served from: {DOCUMENT['client_id'] == URL}")
    print(f"carries client_id, client_name, redirect_uris: "
          f"{all(k in DOCUMENT for k in ('client_id', 'client_name', 'redirect_uris'))}")
    print()
    print("is_valid_client_metadata_url:")
    for candidate in [
        URL,
        "http://app.example.com/oauth/client-metadata.json",
        "https://app.example.com",
        "https://app.example.com/",
        "app.example.com/oauth/client-metadata.json",
    ]:
        print(f"  {candidate:<52} {is_valid_client_metadata_url(candidate)}")


if __name__ == "__main__":
    main()
