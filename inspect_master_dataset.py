"""
Run: python -m data.inspect_master_dataset /path/to/master_dataset.csv

Prints the real column names, a sample row, and the is_fraud value counts
(if present) -- run this BEFORE editing the ADAPT constants at the top of
data/master_dataset_adapter.py, so you're setting them from what the file
actually contains instead of guessing.
"""
import sys
from data.master_dataset_adapter import _read_any


def main():
    if len(sys.argv) != 2:
        print("Usage: python -m data.inspect_master_dataset /path/to/file")
        sys.exit(1)

    df = _read_any(sys.argv[1])
    print(f"Rows: {len(df)}")
    print(f"\nColumns ({len(df.columns)}):")
    for c in df.columns:
        print(f"  - {c}")

    print("\nFirst row:")
    print(df.iloc[0].to_dict())

    for candidate in ("is_fraud", "fraud_flag", "label"):
        if candidate in df.columns:
            print(f"\nValue counts for '{candidate}':")
            print(df[candidate].value_counts(dropna=False))


if __name__ == "__main__":
    main()
