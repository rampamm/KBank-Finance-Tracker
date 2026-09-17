"""Deterministic PDF extraction for bank statements.

Extracts page text and computes file checksums. Handles password-protected
PDFs via an interactive secure prompt (getpass) with retries so passwords
are never stored in plaintext or terminal history.
"""

import getpass
import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from pypdf import PdfReader
from pypdf.errors import FileNotDecryptedError


class StatementPdfError(Exception):
    """Base exception for statement PDF extraction errors."""


class StatementNotFoundError(StatementPdfError):
    """Raised when the specified PDF file cannot be found."""


class StatementDecryptionError(StatementPdfError):
    """Raised when the PDF cannot be decrypted after allowed retries."""


@dataclass
class ExtractedStatement:
    file_path: str
    file_hash: str
    filename: str
    page_count: int
    pages: list[str]
    full_text: str


def compute_file_hash(file_path: Path) -> str:
    """Compute SHA-256 hash of the PDF file to ensure idempotency."""
    hasher = hashlib.sha256()
    with open(file_path, "rb") as f:
        while chunk := f.read(65536):
            hasher.update(chunk)
    return hasher.hexdigest()


def extract_statement_pdf(
    file_path_str: str,
    password: Optional[str] = None,
    max_password_attempts: int = 3,
) -> ExtractedStatement:
    """Read a bank statement PDF and extract its text page by page.

    If the PDF is password-protected and no valid password is provided,
    interactively prompts the user via getpass with retries.
    """
    path = Path(file_path_str).resolve()
    if not path.is_file():
        raise StatementNotFoundError(f"Statement file not found: {path}")

    file_hash = compute_file_hash(path)
    reader = PdfReader(str(path))

    if reader.is_encrypted:
        decrypted = False
        # Try programmatic password first if provided
        if password:
            status = reader.decrypt(password)
            if status != 0:
                decrypted = True

        attempts = 0
        while not decrypted and attempts < max_password_attempts:
            attempts += 1
            print(f"\n[Locked PDF] '{path.name}' is password-protected.")
            print("Hint: Format is DDMMYYYY (e.g. 05011970 for 5 Jan 1970).")
            user_pwd = getpass.getpass(
                f"Enter PDF password (attempt {attempts}/{max_password_attempts}, press Enter to abort): "
            )
            if not user_pwd:
                raise StatementDecryptionError("Password entry aborted by user.")

            status = reader.decrypt(user_pwd)
            if status != 0:
                decrypted = True
                print("Password accepted.")
                break
            else:
                print("Incorrect password. Please try again.")

        if not decrypted:
            raise StatementDecryptionError(
                f"Failed to decrypt '{path.name}' after {max_password_attempts} attempts."
            )

    pages_text: list[str] = []
    try:
        for idx, page in enumerate(reader.pages):
            text = page.extract_text() or ""
            pages_text.append(text)
    except FileNotDecryptedError as exc:
        raise StatementDecryptionError(f"Could not read encrypted PDF content: {exc}") from exc

    full_text = "\n\n--- PAGE BREAK ---\n\n".join(pages_text)

    return ExtractedStatement(
        file_path=str(path),
        file_hash=file_hash,
        filename=path.name,
        page_count=len(reader.pages),
        pages=pages_text,
        full_text=full_text,
    )
