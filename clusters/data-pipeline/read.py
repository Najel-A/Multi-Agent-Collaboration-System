import pandas as pd

df = pd.read_parquet("sample.parquet")  # uses pyarrow under the hood

print("Columns:")
print(df.columns.tolist())

print("\nHead:")
print(df.head(2))

col = "evidence"  # <- your log text

print(f"\n{col} column type (row 0):", type(df.iloc[0][col]))
print(f"\n{col} preview:\n", str(df.iloc[0][col])[:500])
print("Number of rows:", len(df))