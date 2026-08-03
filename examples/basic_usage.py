"""
Minimal example. Prerequisites:

1. TWS or IB Gateway is running
2. API enabled (port 7496 live / 7497 paper)
3. pip install -e .

Then:
    python examples/basic_usage.py
"""

from ibkr_data import BarSize, Duration, download


def main() -> None:
    # Relative lookback from latest
    df = download(
        codes=["QQQ"],
        duration=Duration.ONE_MONTH,
        bar_size=BarSize.DAY_1,
        use_rth=0,
        client_id=101,
    )
    print(df.tail())
    print(f"rows={len(df)}")

    # Fixed date range (useful when batching long history year by year)
    # df2 = download(
    #     codes="QQQ",
    #     start_da="2024-01-01",
    #     end_da="2024-06-01",
    #     bar_size=BarSize.DAY_1,
    # )


if __name__ == "__main__":
    main()
