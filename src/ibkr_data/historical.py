"""
最小 IB 歷史下載：連 TWS → reqHistoricalData → DataFrame

前置：TWS/Gateway 已開，API port 預設 7496（live）或 7497（paper）。

用法：
    from ibkr_data import download, Duration, BarSize

    # A. 相對期間：從 end（預設最新）往回 duration
    df = download(
        codes=["QQQ", "AAPL"],
        duration=Duration.ONE_YEAR,
        bar_size=BarSize.DAY_1,
    )

    # B. 指定日期區間（IB 內部 = endDateTime + durationStr 天數，再篩回區間）
    df = download(
        codes="QQQ",
        start_da="2024-01-01",
        end_da="2024-06-01",
        bar_size=BarSize.DAY_1,
    )
"""

from __future__ import annotations

import threading
import time
from datetime import date, datetime
from enum import Enum
from typing import Iterable, List, Optional, Sequence, Tuple, Union

import pandas as pd
from ibapi.client import EClient
from ibapi.contract import Contract
from ibapi.wrapper import EWrapper

# ── 連線預設（可改）──────────────────────────────────────────
IB_HOST = "127.0.0.1"
IB_PORT = 7496  # paper 常用 7497
IB_CLIENT_ID = 101  # 勿與其他連線撞號
PACING_SEC = 11.0  # 多檔 / 多段請求間隔，避開 IB pacing


# ── 人性化參數（對應 IB 字串，避免 typo）─────────────────────

class Duration(str, Enum):
    """
    IB durationStr。用法：Duration.ONE_YEAR / Duration.Y1

    字串值就是送給 IB 的格式（"1 Y", "30 D", ...）。
    """

    # 日
    D1 = "1 D"
    D2 = "2 D"
    D5 = "5 D"
    D7 = "7 D"
    D10 = "10 D"
    D14 = "14 D"
    D21 = "21 D"
    D30 = "30 D"
    D60 = "60 D"
    D90 = "90 D"
    D180 = "180 D"
    D365 = "365 D"

    # 週 / 月 / 年
    W1 = "1 W"
    W2 = "2 W"
    M1 = "1 M"
    M2 = "2 M"
    M3 = "3 M"
    M6 = "6 M"
    Y1 = "1 Y"
    Y2 = "2 Y"
    Y5 = "5 Y"

    # 別名（讀起來更自然）
    ONE_DAY = "1 D"
    ONE_WEEK = "1 W"
    ONE_MONTH = "1 M"
    THREE_MONTHS = "3 M"
    SIX_MONTHS = "6 M"
    ONE_YEAR = "1 Y"
    TWO_YEARS = "2 Y"
    FIVE_YEARS = "5 Y"

    def ib(self) -> str:
        return self.value


class BarSize(str, Enum):
    """
    IB barSizeSetting。用法：BarSize.DAY_1 / BarSize.MIN_5

    注意：短 bar + 長 duration 會觸及 IB 上限，需自行切段（本檔暫一次請求）。
    """

    SEC_1 = "1 secs"
    SEC_5 = "5 secs"
    SEC_10 = "10 secs"
    SEC_15 = "15 secs"
    SEC_30 = "30 secs"

    MIN_1 = "1 min"
    MIN_2 = "2 mins"
    MIN_3 = "3 mins"
    MIN_5 = "5 mins"
    MIN_10 = "10 mins"
    MIN_15 = "15 mins"
    MIN_20 = "20 mins"
    MIN_30 = "30 mins"

    HOUR_1 = "1 hour"
    HOUR_2 = "2 hours"
    HOUR_3 = "3 hours"
    HOUR_4 = "4 hours"
    HOUR_8 = "8 hours"

    DAY_1 = "1 day"
    WEEK_1 = "1 week"
    MONTH_1 = "1 month"

    # 別名
    ONE_MIN = "1 min"
    FIVE_MINS = "5 mins"
    FIFTEEN_MINS = "15 mins"
    ONE_HOUR = "1 hour"
    ONE_DAY = "1 day"

    def ib(self) -> str:
        return self.value


def _as_duration(d: Union[Duration, str]) -> str:
    if isinstance(d, Duration):
        return d.ib()
    return str(d).strip()


def _as_bar_size(b: Union[BarSize, str]) -> str:
    if isinstance(b, BarSize):
        return b.ib()
    return str(b).strip()


def _normalize_codes(codes: Union[str, Sequence[str], Iterable[str]]) -> List[str]:
    if isinstance(codes, str):
        items = [codes]
    else:
        items = list(codes)
    out: List[str] = []
    seen = set()
    for x in items:
        if x is None or (isinstance(x, float) and pd.isna(x)):
            continue
        s = str(x).strip().upper()
        if not s or s in seen:
            continue
        seen.add(s)
        out.append(s)
    if not out:
        raise ValueError("codes 不可為空")
    return out


def _parse_da(value: Union[str, date, datetime]) -> date:
    """'2024-01-01' / '20240101' / date / datetime → date。"""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    s = str(value).strip()
    if not s:
        raise ValueError("日期不可為空字串")
    for fmt in ("%Y-%m-%d", "%Y%m%d", "%Y/%m/%d"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    # 交給 pandas 兜底
    return pd.to_datetime(s).date()


def _format_ib_end(end: date, market_tz: str = "US/Eastern") -> str:
    """IB endDateTime：該日收盤後，往回推 duration。"""
    return end.strftime("%Y%m%d") + f" 23:59:59 {market_tz}"


def _resolve_request_window(
    start_da: Optional[Union[str, date, datetime]],
    end_da: Optional[Union[str, date, datetime]],
    duration: Optional[Union[Duration, str]],
) -> Tuple[str, str, Optional[date], Optional[date]]:
    """
    回傳 (end_dt 字串給 IB, duration_str 給 IB, filter_start, filter_end)。

    模式 A — 指定區間（有 start_da）：
        IB 只認 endDateTime + durationStr，所以：
          endDateTime = end_da 當天 23:59:59
          durationStr = (end - start).days + 1 天（再加緩衝 1 天）
        抓完後用 filter_start/end 裁成精確區間。

    模式 B — 相對期間（只有 duration，可選 end_da）：
        endDateTime = end_da 或 ""（最新）
        durationStr = duration
        無 filter（若有 end_da 則只裁 end 側也可，此處僅裁 end_da 日）
    """
    if start_da is not None:
        start = _parse_da(start_da)
        end = _parse_da(end_da) if end_da is not None else date.today()
        if start > end:
            raise ValueError(f"start_da ({start}) 不可晚於 end_da ({end})")
        # 含首尾 + 1 天緩衝，避免時區/週末邊界少一天
        n_days = (end - start).days + 2
        n_days = max(n_days, 1)
        return (
            _format_ib_end(end),
            f"{n_days} D",
            start,
            end,
        )

    # 純相對期間
    if duration is None:
        duration = Duration.ONE_YEAR
    duration_str = _as_duration(duration)

    if end_da is not None:
        end = _parse_da(end_da)
        return _format_ib_end(end), duration_str, None, end

    return "", duration_str, None, None


def _filter_by_dates(
    df: pd.DataFrame,
    time_col: str,
    start: Optional[date],
    end: Optional[date],
) -> pd.DataFrame:
    if df.empty or (start is None and end is None):
        return df
    out = df.copy()
    if time_col == "da":
        series = pd.to_datetime(out["da"]).dt.date
        if start is not None:
            out = out[series >= start]
            series = pd.to_datetime(out["da"]).dt.date
        if end is not None:
            out = out[series <= end]
    else:
        ts = pd.to_datetime(out["ts"])
        if start is not None:
            start_ts = pd.Timestamp(start)
            out = out[ts >= start_ts]
            ts = pd.to_datetime(out["ts"])
        if end is not None:
            # 含 end 整天
            end_ts = pd.Timestamp(end) + pd.Timedelta(days=1) - pd.Timedelta(microseconds=1)
            out = out[ts <= end_ts]
    return out.reset_index(drop=True)


# ── IB client ────────────────────────────────────────────────

class IBHistClient(EWrapper, EClient):
    """最簡歷史 K 客戶端：一次請求、等歷史 bar 收完。"""

    def __init__(self) -> None:
        EClient.__init__(self, self)
        self._ready = threading.Event()  # connection ready
        self._done = threading.Event()
        self._bars: List = []
        self._error: Optional[str] = None
        self._req_id = 0

    def nextValidId(self, orderId: int) -> None:
        self._ready.set()

    def error(self, reqId, errorCode, errorString, advancedOrderRejectJson="") -> None:
        # 2104/2106/2158 等是狀態訊息，不是失敗
        if errorCode in (2104, 2106, 2158, 2119, 2174, 2176):
            return
        msg = f"IB error {errorCode}: {errorString} (reqId={reqId})"
        if errorCode in (326, 502, 504, 1100):
            self._error = msg
            self._ready.set()
            self._done.set()
        elif reqId >= 0:
            self._error = msg
            self._done.set()

    def historicalData(self, reqId, bar) -> None:
        self._bars.append(bar)

    def historicalDataEnd(self, reqId: int, start: str, end: str) -> None:
        self._done.set()

    def connect_and_start(self, host: str, port: int, client_id: int) -> None:
        self.connect(host, port, client_id)
        thread = threading.Thread(target=self.run, daemon=True)
        thread.start()
        if not self._ready.wait(timeout=10):
            raise RuntimeError("連線逾時：請確認 TWS 已開、API 已啟用、port 正確")
        if self._error:
            raise RuntimeError(self._error)

    def fetch(
        self,
        symbol: str,
        duration: Union[Duration, str] = Duration.ONE_YEAR,
        bar_size: Union[BarSize, str] = BarSize.DAY_1,
        end_dt: str = "",
        use_rth: int = 1,
        what_to_show: str = "TRADES",
        timeout_sec: float = 60,
    ) -> pd.DataFrame:
        """抓單一 symbol 一段歷史（一次 reqHistoricalData）。"""
        self._bars = []
        self._done.clear()
        self._error = None
        self._req_id += 1
        req_id = self._req_id

        duration_str = _as_duration(duration)
        bar_size_str = _as_bar_size(bar_size)

        c = Contract()
        c.symbol = symbol
        c.secType = "STK"
        c.exchange = "SMART"
        c.currency = "USD"

        self.reqHistoricalData(
            reqId=req_id,
            contract=c,
            endDateTime=end_dt,
            durationStr=duration_str,
            barSizeSetting=bar_size_str,
            whatToShow=what_to_show,
            useRTH=use_rth,
            formatDate=1,
            keepUpToDate=False,
            chartOptions=[],
        )

        if not self._done.wait(timeout=timeout_sec):
            raise TimeoutError(f"{symbol}: historicalData 逾時 ({timeout_sec}s)")
        if self._error:
            raise RuntimeError(f"{symbol}: {self._error}")

        is_daily = bar_size_str in ("1 day", "1 week", "1 month")
        rows = []
        for b in self._bars:
            raw = str(b.date)
            if is_daily:
                da = raw[:8]
                ts_or_da = datetime.strptime(da, "%Y%m%d").date()
                key = "da"
            else:
                # 日內通常是 "YYYYMMDD  HH:MM:SS"
                ts_or_da = pd.to_datetime(raw)
                key = "ts"
            rows.append(
                {
                    key: ts_or_da,
                    "code": symbol,
                    "op": b.open,
                    "hi": b.high,
                    "lo": b.low,
                    "cl": b.close,
                    "vol": b.volume,
                    "wap": getattr(b, "average", None),
                    "bar_count": getattr(b, "barCount", None),
                    "bar_size": bar_size_str,
                }
            )
        return pd.DataFrame(rows)


# ── 公開 API ─────────────────────────────────────────────────

def download(
    codes: Union[str, Sequence[str]],
    duration: Optional[Union[Duration, str]] = None,
    bar_size: Union[BarSize, str] = BarSize.DAY_1,
    *,
    start_da: Optional[Union[str, date, datetime]] = None,
    end_da: Optional[Union[str, date, datetime]] = None,
    use_rth: int = 1,
    what_to_show: str = "TRADES",
    host: str = IB_HOST,
    port: int = IB_PORT,
    client_id: int = IB_CLIENT_ID,
    pacing_sec: float = PACING_SEC,
    timeout_sec: float = 60,
) -> pd.DataFrame:
    """
    下載一檔或多檔歷史 K 線 → DataFrame。

    兩種區間指定方式（二選一邏輯）：

    1) 指定日期（分批抓時最常用）
        start_da="2024-01-01", end_da="2024-06-01"
        → 內部轉成 IB 的 endDateTime + "N D"，再裁成 [start, end]

    2) 相對期間
        duration=Duration.ONE_YEAR
        可選 end_da="2024-12-31"（從該日往回）
        不設 end_da = 抓到最新

    Parameters
    ----------
    codes : str | list[str]
        "QQQ" 或 ["QQQ", "AAPL"]
    duration : Duration | str | None
        有 start_da 時可省略（會被日期區間覆寫）
    bar_size : BarSize | str
        BarSize.DAY_1 / BarSize.MIN_5
    start_da, end_da : str | date | None
        "YYYY-MM-DD"；有 start_da 時即走日期模式
    use_rth : int
        1=正規盤, 0=含盤前盤後
    """
    symbols = _normalize_codes(codes)
    bar_size_str = _as_bar_size(bar_size)
    is_daily = bar_size_str in ("1 day", "1 week", "1 month")
    time_col = "da" if is_daily else "ts"

    end_dt, duration_str, filter_start, filter_end = _resolve_request_window(
        start_da, end_da, duration,
    )
    print(
        f"request window: endDateTime={end_dt or 'latest'!r} | "
        f"durationStr={duration_str!r} | filter={filter_start} → {filter_end}"
    )

    client = IBHistClient()
    client.connect_and_start(host, port, client_id)
    frames: List[pd.DataFrame] = []
    try:
        for i, sym in enumerate(symbols):
            if i > 0:
                time.sleep(pacing_sec)
            df = client.fetch(
                symbol=sym,
                duration=duration_str,
                bar_size=bar_size,
                end_dt=end_dt,
                use_rth=use_rth,
                what_to_show=what_to_show,
                timeout_sec=timeout_sec,
            )
            df = _filter_by_dates(df, time_col, filter_start, filter_end)
            if not df.empty:
                frames.append(df)
                print(f"✓ {sym}: {len(df)} bars")
            else:
                print(f"✗ {sym}: 無資料（或區間過濾後為空）")
    finally:
        client.disconnect()
        time.sleep(0.5)

    if not frames:
        return pd.DataFrame()

    out = pd.concat(frames, ignore_index=True)
    return out.sort_values(["code", time_col]).reset_index(drop=True)


def download_qqq_one_year(**kwargs) -> pd.DataFrame:
    """相容舊介面：QQQ 過去一年日線。"""
    kwargs.setdefault("duration", Duration.ONE_YEAR)
    kwargs.setdefault("bar_size", BarSize.DAY_1)
    return download(codes="QQQ", **kwargs)


if __name__ == "__main__":
    print("Connecting IB …")
    # 範例：指定半年區間（分批抓的典型寫法）
    out = download(
        codes="QQQ",
        start_da="2024-01-01",
        end_da="2024-06-01",
        bar_size=BarSize.DAY_1,
        use_rth=0,
    )
    print(out)
    if not out.empty:
        print(f"\n{len(out)} rows | codes={sorted(out['code'].unique())}")
        print(out.groupby("code")["da"].agg(["min", "max", "count"]))
