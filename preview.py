"""
Quick preview of `synthetic_source.jsonl` using pandas.

Usage (from your shell, inside the project root):

    cd ~/Desktop/Multi-Agent-Collaboration-System
    python3 preview.py
"""

from pathlib import Path

import pandas as pd


def main() -> None:
    # Point directly at the JSONL file (not the directory)
    data_path = Path("data/synthetic_source.jsonl").expanduser().resolve()

    if not data_path.exists():
        raise SystemExit(f"File not found: {data_path}")

    # Read JSONL into a DataFrame
    df = pd.read_json(data_path, lines=True)

    print(f"Loaded {len(df):,} rows from {data_path}")
    print("\nColumns:")
    print(df.columns.tolist())

    print("\nHead (first 5 rows):")
    # Use to_string so nested columns are fully visible in the terminal
    print(df.head().to_string())


if __name__ == "__main__":
    main()


