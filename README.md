# ibkr-data

Download Interactive Brokers historical OHLCV bars into a **pandas DataFrame**.

Requires a running **TWS** or **IB Gateway** with the socket API enabled. This library does not ship market data by itself — it talks to your local IB session.

## Install

### From this repo (recommended while developing)

```bash
git clone https://github.com/YOUR_USERNAME/ibkr_data_api.git
cd ibkr_data_api
pip install -e .
```

### Directly from GitHub

```bash
pip install "git+https://github.com/YOUR_USERNAME/ibkr_data_api.git"
```

### Dependencies

- Python 3.9+
- `pandas`
- `ibapi` (Interactive Brokers Python API)

If `pip install ibapi` fails on your platform, install the official IB API wheel from [Interactive Brokers API downloads](https://interactivebrokers.github.io/), then reinstall this package.

## Prerequisites (TWS / Gateway)

1. Open **TWS** or **IB Gateway** and log in.
2. **Configure → API → Settings**
   - Enable ActiveX and Socket Clients
   - Socket port: **7496** (live) or **7497** (paper) by default
   - (Optional) Allow connections from localhost only
3. Make sure no other script is already using the same `client_id`.

## Quick start

```python
from ibkr_data import download, Duration, BarSize

# A) Relative window from the latest bar
df = download(
    codes=["QQQ", "AAPL", "NVDA"],
    duration=Duration.ONE_YEAR,   # also Duration.Y1
    bar_size=BarSize.DAY_1,       # also BarSize.ONE_DAY
    use_rth=0,                    # 0 = include extended hours (stocks)
)

# B) Fixed calendar range (good when you batch long history)
df = download(
    codes="QQQ",
    start_da="2024-01-01",
    end_da="2024-06-01",
    bar_size=BarSize.DAY_1,
)

# C) Single symbol, shorter bar size
df = download(
    "QQQ",
    duration=Duration.SIX_MONTHS,
    bar_size=BarSize.MIN_5,
    use_rth=0,
)
```

Run the example script after `pip install -e .` and with TWS open:

```bash
python examples/basic_usage.py
```

## API

### `download(...)`

| Argument | Type | Default | Description |
|----------|------|---------|-------------|
| `codes` | `str` or list | required | Symbol(s), e.g. `"QQQ"` or `["QQQ","AAPL"]` |
| `duration` | `Duration` / `str` | `ONE_YEAR` if no `start_da` | IB lookback, e.g. `Duration.Y1` |
| `bar_size` | `BarSize` / `str` | `DAY_1` | Bar size, e.g. `BarSize.MIN_5` |
| `start_da` | date-like | `None` | Start of range `YYYY-MM-DD` |
| `end_da` | date-like | `None` | End of range / anchor for duration |
| `use_rth` | `int` | `1` | `1` regular hours only, `0` include pre/post |
| `host` | `str` | `127.0.0.1` | TWS host |
| `port` | `int` | `7496` | `7497` for many paper setups |
| `client_id` | `int` | `101` | Must be unique per connection |
| `pacing_sec` | `float` | `11` | Sleep between symbols (IB pacing) |
| `what_to_show` | `str` | `TRADES` | IB `whatToShow` |

**Date range mode** (`start_da` set):

- Converted internally to IB `endDateTime` + `durationStr = N D`
- Result is filtered back to `[start_da, end_da]`

**Relative mode** (`duration` only):

- Looks back from `end_da` if provided, otherwise from the latest bar

### `Duration` and `BarSize`

Enums map to official IB strings to reduce typos:

```python
Duration.ONE_YEAR   # "1 Y"
Duration.Y1         # same
BarSize.DAY_1       # "1 day"
BarSize.MIN_5       # "5 mins"
```

You may still pass raw IB strings: `duration="1 Y"`, `bar_size="5 mins"`.

## Return value

- Daily / weekly / monthly: columns include `da`, `code`, `op`, `hi`, `lo`, `cl`, `vol`, `bar_size`, …
- Intraday bars: time column is `ts` instead of `da`

No database is required. The function returns a DataFrame only.

## Limitations (v0.1)

- US stocks via `STK` / `SMART` / `USD` only (no futures / HK yet)
- One `reqHistoricalData` **per symbol** (no automatic multi-chunk for long intraday windows)
- Very long **intraday** ranges may hit IB single-request limits — batch with `start_da` / `end_da`
- Needs live IB market data permissions for the requested product

## Development layout

```text
ibkr_data_api/
  pyproject.toml
  README.md
  examples/
  src/
    ibkr_data/
      __init__.py      # public exports
      historical.py    # download / enums / IB client
```

Editable install for local work:

```bash
pip install -e ".[dev]"
```

## License

MIT — see [LICENSE](LICENSE).

## Disclaimer

This project is not affiliated with Interactive Brokers. Use at your own risk. Market data is governed by your IB account subscriptions and IB’s API rules.
