"""Deterministic Gmail access: find candidate KBank emails and fetch their
raw bodies. No LLM involved here on purpose -- which emails count as
"candidates" and whether they've already been processed must be exact, not
a judgment call."""

from typing import TypedDict

from langchain_google_community.gmail.search import GmailSearch
from langchain_google_community.gmail.utils import build_resource_service, get_gmail_credentials

from finance_tracker.config import (
    GMAIL_CREDENTIALS_FILE,
    GMAIL_INITIAL_LOOKBACK,
    GMAIL_SCOPES,
    GMAIL_TOKEN_FILE,
    KBANK_SENDERS,
)


class RawEmail(TypedDict):
    id: str
    subject: str
    sender: str
    body: str


def _get_api_resource():
    creds = get_gmail_credentials(
        token_file=GMAIL_TOKEN_FILE,
        client_sercret_file=GMAIL_CREDENTIALS_FILE,
        scopes=GMAIL_SCOPES,
    )
    return build_resource_service(credentials=creds)


def fetch_candidate_kbank_emails(max_results: int = 25) -> list[RawEmail]:
    """Search Gmail for messages from known KBank senders. Does not filter by
    subject here -- new KBank email types have unknown subjects, so the
    parser sub-agent (not a hardcoded subject list) decides whether an email
    is a real completed transaction."""
    api_resource = _get_api_resource()
    search_tool = GmailSearch(api_resource=api_resource)

    sender_query = " OR ".join(f"from:{sender}" for sender in KBANK_SENDERS)
    query = f"({sender_query}) newer_than:{GMAIL_INITIAL_LOOKBACK}"

    results = search_tool.run({"query": query, "resource": "messages", "max_results": max_results})

    return [
        RawEmail(
            id=item["id"],
            subject=item.get("subject", ""),
            sender=item.get("sender", ""),
            body=item.get("body", ""),
        )
        for item in results
    ]
