"""LLM-based parsing 'sub-agent': extracts structured transactions from
bank statements (and legacy notification emails).

Because financial figures must never be guessed, every claimed raw_amount,
raw_date, and raw_balance is checked for a verbatim substring match against
the source text before being trusted -- a mismatch means the value is rejected
and queued for review, not silently accepted.
"""

from dataclasses import dataclass
from typing import Optional

from deepagents import create_deep_agent

from finance_tracker.config import PARSER_MODEL
from finance_tracker.models import (
    ParsedStatement,
    ParsedTransaction,
    StatementTransaction,
)

STATEMENT_PARSER_SYSTEM_PROMPT = """You are a precision financial bank statement extractor for Thai bank statements (Kasikornbank / KBank).

The statement table has the following standard columns:
- Date: e.g. '01-09-26' (DD-MM-YY)
- Time/Eff.Date: e.g. '11:05', '14:40:08' (or null)
- Withdrawal / Deposit: the transaction amount
- Outstanding Balance (THB): the resulting account balance
- Channel: e.g. 'EDC/E-Commerce', 'K PLUS', 'EDC/K SHOP/MYQR', 'Other Bank'
- Description/Note (รายละเอียด/บันทึกช่วยจำ) or Details:
  In KBank statements, this column contains structured transaction details formatted with prefixes:
  * DESC : <the main transaction description / merchant / payee text> (e.g. 'จ่ายบิล BE OKAY CLINIC CO.,LTD.', 'เติมเงิน WIDx3811/K Plus W', 'รับโอนจาก KBANK x1769')
  * NOTE : <user-entered memo or note> (e.g. 'สิว', 'shabu', or '-' if no note exists)

Extraction Rules:
1. Ignore header rows, summary totals, and 'Beginning Balance' rows (Beginning Balance is an opening balance, not a transaction).
2. For each transaction row, extract:
   * raw_date: copy the Date verbatim (e.g. '01-09-26').
   * raw_time: copy the Time verbatim (e.g. '11:05'), or null if absent.
   * tx_type:
     - 'debit' for withdrawals/expenses/spending/payments out (where balance decreases).
     - 'credit' for deposits/transfers in/refunds (where balance increases).
   * raw_amount: copy the amount verbatim (e.g. '68.00', '205.44', '1,337.00'). Do not round or reformat.
   * raw_balance: copy the Outstanding Balance verbatim (e.g. '27,493.72').
   * channel: copy the Channel string (e.g. 'EDC/E-Commerce', 'K PLUS', 'EDC/K SHOP/MYQR').
   * description: copy the main description string after 'DESC :' (or the entire description text if no 'DESC :' prefix).
   * details: copy merchant, payee, or reference info from the DESC line (e.g. 'BE OKAY CLINIC CO.,LTD.', 'ร้านค้าออมสิน มหาเฮง').
   * memo: extract the user note after 'NOTE :' if present and not '-' (e.g. 'สิว', 'shabu'). If NOTE is '-' or missing, set memo to null.
   * currency: 'THB'.
3. Every single number and date MUST appear VERBATIM in the source text. Never invent or adjust any digits.
"""


# Legacy email parser prompt (unused - statement ingestion uses STATEMENT_PARSER_SYSTEM_PROMPT)
# EMAIL_PARSER_SYSTEM_PROMPT = """You are a careful financial-email field extractor.
#
# You will be given the raw body of one email from a Thai bank (KBank). Decide
# whether it reports a single, already-completed (successful) transaction with
# a clear amount -- not a failed/pending transfer, not marketing, not a
# general alert.
#
# If it is a completed transaction, extract fields EXACTLY as they appear in
# the text:
# - raw_amount and raw_fee: copy the digits/punctuation verbatim, do not
#   reformat, round, or recompute them.
# - raw_datetime: copy the date/time text verbatim, do not reorder or
#   reformat it.
# - transaction_number, counterparty: copy verbatim if present.
# - memo: only set this if the email actually contains a distinct user-entered
#   note/description field. Many emails do not have one -- in that case leave
#   memo null. Never invent a memo from the counterparty name or transaction
#   type.
#
# If the email is bilingual, prefer the English-labeled section for accuracy,
# but the values must still match what's in the email verbatim.
#
# If this is not a completed transaction (e.g. failed, pending, or not a
# transaction email at all), set is_transaction to false and give a short
# skip_reason. Do not fabricate any fields in that case.
# """



def _normalize_text(s: str) -> str:
    return " ".join(s.split())


def verify_statement_tx_verbatim(source_text: str, tx: StatementTransaction) -> list[str]:
    """Verify that transaction figures exist character-for-character in the source text."""
    haystack = _normalize_text(source_text)
    failures = []
    for field_name in ("raw_amount", "raw_date", "raw_balance"):
        val = getattr(tx, field_name)
        if val is None:
            continue
        if _normalize_text(val) not in haystack:
            failures.append(field_name)
    return failures


# Legacy email verbatim check (unused - active function is verify_statement_tx_verbatim)
# def _verify_verbatim(raw_email_text: str, parsed: ParsedTransaction) -> list[str]:
#     haystack = _normalize_text(raw_email_text)
#     failures = []
#     for field_name in ("raw_amount", "raw_fee", "raw_datetime", "transaction_number"):
#         value = getattr(parsed, field_name)
#         if value is None:
#             continue
#         if _normalize_text(value) not in haystack:
#             failures.append(field_name)
#     return failures



@dataclass
class VerifiedStatementTx:
    tx: StatementTransaction
    verbatim_ok: bool
    verbatim_failures: list[str]
    retry_count: int = 0


@dataclass
class StatementParseOutcome:
    parsed: ParsedStatement
    verified_transactions: list[VerifiedStatementTx]


@dataclass
class ParseOutcome:
    parsed: ParsedTransaction
    verbatim_ok: bool
    verbatim_failures: list[str]


_statement_agent = None
_email_agent = None


def _get_statement_agent():
    global _statement_agent
    if _statement_agent is None:
        _statement_agent = create_deep_agent(
            model=PARSER_MODEL,
            tools=[],
            system_prompt=STATEMENT_PARSER_SYSTEM_PROMPT,
            response_format=ParsedStatement,
        )
    return _statement_agent


def reparse_failed_transactions(
    page_text: str,
    failed_items: list[dict],
    page_num: int = 1,
) -> list[VerifiedStatementTx]:
    """Re-invoke parser with structured critique feedback on failed transactions.
    failed_items is a list of dicts:
      {
        'index': int,
        'tx': StatementTransaction,
        'critique': str,  # specific explanation of what failed (verbatim failure or balance mismatch)
      }
    """
    import time
    from finance_tracker.tracing import extract_token_usage, log_event

    if not failed_items:
        return []

    t0 = time.time()
    agent = _get_statement_agent()

    critiques_text = []
    for item in failed_items:
        tx = item["tx"]
        critique = item["critique"]
        critiques_text.append(
            f"- Row item (extracted as Date: '{tx.raw_date}', Amount: '{tx.raw_amount}', "
            f"Balance: '{tx.raw_balance}', Desc: '{tx.description}'):\n"
            f"    Issue: {critique}"
        )

    critique_prompt = (
        f"Statement Page {page_num} text:\n\n{page_text}\n\n"
        f"--- CRITIQUE FEEDBACK ---\n"
        f"The following transaction(s) extracted from this page failed verification checks.\n"
        f"Please re-inspect the source text carefully and re-extract ONLY these {len(failed_items)} transaction(s) "
        f"with exact, verbatim characters for raw_amount, raw_date, and raw_balance:\n\n"
        + "\n".join(critiques_text)
    )

    result = agent.invoke({"messages": [{"role": "user", "content": critique_prompt}]})
    duration = round(time.time() - t0, 3)
    tokens = extract_token_usage(result)
    parsed: ParsedStatement = result["structured_response"]

    reparsed_verified = []
    for tx in parsed.transactions:
        failures = verify_statement_tx_verbatim(page_text, tx)
        reparsed_verified.append(
            VerifiedStatementTx(
                tx=tx,
                verbatim_ok=len(failures) == 0,
                verbatim_failures=failures,
                retry_count=1,
            )
        )

    log_event(
        "parser_self_critique_completed",
        {
            "page_num": page_num,
            "reparsed_count": len(reparsed_verified),
            "verbatim_ok_count": sum(1 for v in reparsed_verified if v.verbatim_ok),
            "duration_sec": duration,
            "tokens": tokens,
        },
    )
    return reparsed_verified


def parse_statement_page(
    page_text: str, page_num: int = 1, max_retries: int = 2
) -> StatementParseOutcome:
    """Parse a single statement page, verify verbatim figures, and execute bounded
    agentic self-critique retries on any verbatim failures."""
    import time
    from finance_tracker.tracing import extract_token_usage, log_event

    t0 = time.time()
    agent = _get_statement_agent()
    prompt = f"Statement Page {page_num} text:\n\n{page_text}"
    result = agent.invoke({"messages": [{"role": "user", "content": prompt}]})
    duration = round(time.time() - t0, 3)
    tokens = extract_token_usage(result)

    parsed: ParsedStatement = result["structured_response"]

    verified: list[VerifiedStatementTx] = []
    for tx in parsed.transactions:
        failures = verify_statement_tx_verbatim(page_text, tx)
        verified.append(
            VerifiedStatementTx(
                tx=tx,
                verbatim_ok=len(failures) == 0,
                verbatim_failures=failures,
                retry_count=0,
            )
        )

    # Agentic self-critique loop for verbatim failures
    for attempt in range(1, max_retries + 1):
        failed_indices = [i for i, v in enumerate(verified) if not v.verbatim_ok]
        if not failed_indices:
            break

        items_to_critique = []
        for idx in failed_indices:
            v = verified[idx]
            critique = (
                f"Verbatim match failed for field(s): {v.verbatim_failures}. "
                f"Values {[(f, getattr(v.tx, f)) for f in v.verbatim_failures]} were NOT found verbatim in the source page text. "
                "Ensure digits, commas, and decimals match character-for-character with the text."
            )
            items_to_critique.append({"index": idx, "tx": v.tx, "critique": critique})

        reparsed = reparse_failed_transactions(page_text, items_to_critique, page_num=page_num)

        # Match reparsed transactions back to the failed indices
        for original_idx, rep in zip(failed_indices, reparsed):
            rep.retry_count = attempt
            if rep.verbatim_ok:
                verified[original_idx] = rep
            else:
                verified[original_idx].retry_count = attempt

    log_event(
        "parser_page_completed",
        {
            "page_num": page_num,
            "transactions_count": len(verified),
            "verbatim_failures_count": sum(1 for v in verified if not v.verbatim_ok),
            "duration_sec": duration,
            "tokens": tokens,
        },
    )

    parsed.transactions = [v.tx for v in verified]
    return StatementParseOutcome(parsed=parsed, verified_transactions=verified)



# def parse_email(raw_email_text: str, subject: str, sender: str) -> ParseOutcome:
#     """Legacy parser for single transaction notification emails."""
#     agent = _get_email_agent()
#     task = (
#         f"Email subject: {subject}\n"
#         f"Email sender: {sender}\n\n"
#         f"Email body:\n{raw_email_text}"
#     )
#     result = agent.invoke({"messages": [{"role": "user", "content": task}]})
#     parsed: ParsedTransaction = result["structured_response"]
#
#     if not parsed.is_transaction:
#         return ParseOutcome(parsed=parsed, verbatim_ok=True, verbatim_failures=[])
#
#     failures = _verify_verbatim(raw_email_text, parsed)
#     return ParseOutcome(parsed=parsed, verbatim_ok=len(failures) == 0, verbatim_failures=failures)

