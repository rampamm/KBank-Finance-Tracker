"""LLM-based categorization: only ever invoked when a transaction has a memo
to reason about. The output schema's Literal type constrains the model to
the fixed category list -- it cannot return anything else. "Uncategorized"
is never assigned here; that's a pipeline-level fallback, not an agent
choice."""

from deepagents import create_deep_agent

from finance_tracker.config import CATEGORIES, CATEGORIZER_MODEL
from finance_tracker.models import (
    CategoryResult,
    create_batch_category_result_model,
    create_category_result_model,
)

CATEGORIZER_SYSTEM_PROMPT = f"""You categorize a personal bank transaction
into exactly one of these categories: {", ".join(CATEGORIES)}.

You will be given the transaction's memo/description text and the
counterparty name. Pick the single best-matching category based on the
memo. If the memo is ambiguous, use your best judgment but reflect the
uncertainty honestly in the confidence score.
"""

BATCH_CATEGORIZER_SYSTEM_PROMPT = f"""You are a precision personal finance transaction categorizer.
Categorize each given transaction in the input batch into exactly one of these allowed categories:
{", ".join(CATEGORIES)}.

For each item in the input list (identified by index 0, 1, 2...):
- Select the best-matching category strictly from the allowed list above.
- Provide a confidence score (0.0 to 1.0).
- Return an output entry for EVERY input item index.
"""

_agent = None
_batch_agent = None


def _get_agent():
    global _agent
    if _agent is None:
        model_cls = create_category_result_model(CATEGORIES)
        _agent = create_deep_agent(
            model=CATEGORIZER_MODEL,
            tools=[],
            system_prompt=CATEGORIZER_SYSTEM_PROMPT,
            response_format=model_cls,
        )
    return _agent


def _get_batch_agent():
    global _batch_agent
    if _batch_agent is None:
        batch_model_cls = create_batch_category_result_model(CATEGORIES)
        _batch_agent = create_deep_agent(
            model=CATEGORIZER_MODEL,
            tools=[],
            system_prompt=BATCH_CATEGORIZER_SYSTEM_PROMPT,
            response_format=batch_model_cls,
        )
    return _batch_agent


def categorize_from_memo(memo: str, counterparty: str | None) -> CategoryResult:
    agent = _get_agent()
    task = f"Memo/description: {memo}\nCounterparty: {counterparty or 'unknown'}"
    result = agent.invoke({"messages": [{"role": "user", "content": task}]})
    res = result["structured_response"]
    return CategoryResult(
        category=res.category,
        confidence=res.confidence,
        rationale=res.rationale,
    )


def categorize_batch(items: list[dict]) -> dict[int, CategoryResult]:
    """Categorize a list of transaction items in a single LLM API call.

    `items` is a list of dicts: [{"index": i, "memo": "...", "counterparty": "..."}, ...]
    Returns a dict mapping index -> CategoryResult.
    """
    if not items:
        return {}

    import time
    from finance_tracker.tracing import extract_token_usage, log_event

    t0 = time.time()
    agent = _get_batch_agent()
    formatted = []
    for item in items:
        idx = item["index"]
        memo_str = item.get("memo") or ""
        counterparty_str = item.get("counterparty") or "unknown"
        formatted.append(f"Item [{idx}]: description/memo='{memo_str}', counterparty='{counterparty_str}'")

    task = "Categorize all of the following transaction items:\n" + "\n".join(formatted)
    result = agent.invoke({"messages": [{"role": "user", "content": task}]})
    duration = round(time.time() - t0, 3)
    tokens = extract_token_usage(result)

    batch_res = result["structured_response"]

    out: dict[int, CategoryResult] = {}
    for res_item in batch_res.results:
        out[res_item.index] = CategoryResult(
            category=res_item.category,
            confidence=res_item.confidence,
            rationale=res_item.rationale,
        )

    log_event(
        "batch_categorization_completed",
        {
            "items_count": len(items),
            "duration_sec": duration,
            "tokens": tokens,
        },
    )

    return out



