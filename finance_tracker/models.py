"""Structured schemas exchanged with the LLM sub-agents.

Amounts, balances, and dates are kept as the *exact substrings* the model claims
to have read from the raw bank statement, never normalized or reformatted by the model.
This lets the pipeline verify each one appears verbatim in the source document
before trusting it.
"""

from typing import Literal, Optional

from pydantic import BaseModel, Field, create_model


class StatementTransaction(BaseModel):
    raw_date: str = Field(
        description="The transaction date EXACTLY as it appears in the statement (e.g. '01-09-26' or '12/09/2026')."
    )
    raw_time: Optional[str] = Field(
        default=None,
        description="The transaction time EXACTLY as it appears in the statement (e.g. '11:05'), if present.",
    )
    tx_type: Literal["debit", "credit"] = Field(
        description="Whether this transaction is a withdrawal/expense ('debit') or a deposit/income ('credit')."
    )
    raw_amount: str = Field(
        description=(
            "The transaction amount EXACTLY as it appears in the statement text "
            "(same digits, commas, decimals -- e.g. '68.00', '205.44', '1,337.00'). Do not reformat or compute it."
        )
    )
    raw_balance: Optional[str] = Field(
        default=None,
        description="The outstanding balance EXACTLY as it appears after this transaction (e.g. '27,493.72'), if present.",
    )
    channel: Optional[str] = Field(
        default=None,
        description="Transaction channel or code (e.g. 'EDC/E-Commerce', 'K PLUS', 'EDC/K SHOP/MYQR', 'Other Bank').",
    )
    description: Optional[str] = Field(
        default=None,
        description="Transaction description from the statement (e.g. 'Debit Card Spending', 'Payment', 'Transfer Deposit', 'Error Correction').",
    )
    details: Optional[str] = Field(
        default=None,
        description="The Details column text containing merchant/payee/ref (e.g. 'Paid for Ref X4912 อ่องเหวิน ข้าวมันไก่', 'Ref Code EDC45445').",
    )
    memo: Optional[str] = Field(
        default=None,
        description="User-entered note or memo, if distinct from the details. ",
    )
    currency: str = Field(default="THB", description="Currency code, e.g. THB.")


class ParsedStatement(BaseModel):
    account_number: Optional[str] = Field(
        default=None,
        description="The bank account number shown on the statement, if present.",
    )
    statement_period: Optional[str] = Field(
        default=None,
        description="The statement period or date range (e.g. '01/09/2026 - 30/09/2026'), if present.",
    )
    transactions: list[StatementTransaction] = Field(
        default_factory=list,
        description="List of all transactions extracted in chronological order from the statement.",
    )


class CategoryResult(BaseModel):
    category: str = Field(description="Best-matching category for the transaction memo or description.")
    confidence: float = Field(description="0.0-1.0 confidence in this category assignment.")
    rationale: str = Field(description="One short sentence on why this category was chosen.")


class CategorizedItem(BaseModel):
    index: int = Field(description="0-based index of the transaction item in the batch.")
    category: str = Field(description="Best-matching category for this transaction.")
    confidence: float = Field(description="0.0-1.0 confidence in this category assignment.")
    rationale: str = Field(description="One short sentence on why this category was chosen.")


class BatchCategoryResults(BaseModel):
    results: list[CategorizedItem] = Field(description="Categorization results for all input items in the batch.")


def create_category_result_model(categories: list[str]) -> type[BaseModel]:
    """Dynamically construct a CategoryResult model where category is restricted
    to an enum Literal of the provided taxonomy list."""
    CategoryLiteral = Literal[tuple(categories)]  # type: ignore[valid-type]
    return create_model(
        "CategoryResult",
        category=(CategoryLiteral, Field(description="Best-matching category for the transaction memo or description.")),
        confidence=(float, Field(description="0.0-1.0 confidence in this category assignment.")),
        rationale=(str, Field(description="One short sentence on why this category was chosen.")),
        __module__=__name__,
    )


def create_batch_category_result_model(categories: list[str]) -> type[BaseModel]:
    """Dynamically construct a BatchCategoryResults model where items are restricted
    to an enum Literal of the provided taxonomy list."""
    CategoryLiteral = Literal[tuple(categories)]  # type: ignore[valid-type]
    item_model = create_model(
        "CategorizedItem",
        index=(int, Field(description="0-based index of the transaction item in the batch.")),
        category=(CategoryLiteral, Field(description="Best-matching category for this transaction.")),
        confidence=(float, Field(description="0.0-1.0 confidence in this category assignment.")),
        rationale=(str, Field(description="One short sentence on why this category was chosen.")),
        __module__=__name__,
    )
    return create_model(
        "BatchCategoryResults",
        results=(list[item_model], Field(description="Categorization results for all input items in the batch.")),
        __module__=__name__,
    )


