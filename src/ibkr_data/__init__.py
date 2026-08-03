"""
ibkr-data — Interactive Brokers historical market data → pandas DataFrame.

Requires TWS or IB Gateway running with API enabled.
"""

from ibkr_data.historical import (
    BarSize,
    Duration,
    IBHistClient,
    download,
    download_qqq_one_year,
)

__version__ = "0.1.0"

__all__ = [
    "BarSize",
    "Duration",
    "IBHistClient",
    "download",
    "download_qqq_one_year",
    "__version__",
]
