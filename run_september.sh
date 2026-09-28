#!/usr/bin/env bash
set -e

# Change directory to project root
cd "$(dirname "$0")"

# Activate virtual environment if present
if [ -d ".venv" ]; then
    source .venv/bin/activate
fi

STATEMENT_PATH="statements/september_statement.pdf"

if [ ! -f "$STATEMENT_PATH" ]; then
    echo "Error: Statement file not found at $STATEMENT_PATH"
    exit 1
fi

echo "============================================================"
echo " Starting Ingestion for September Statement ($STATEMENT_PATH)"
echo "============================================================"

# Pass inline password if provided as $1, or via KBANK_PDF_PASSWORD env var
PASSWORD_ARG=""
if [ -n "$1" ]; then
    PASSWORD_ARG="--password $1"
elif [ -n "$KBANK_PDF_PASSWORD" ]; then
    PASSWORD_ARG="--password $KBANK_PDF_PASSWORD"
fi

# Run ingestion
python main.py ingest "$STATEMENT_PATH" $PASSWORD_ARG

echo ""
echo "============================================================"
echo " Generating September 2026 Monthly Report"
echo "============================================================"
python main.py report 2026-09
