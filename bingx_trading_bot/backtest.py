# ============================================================
# backtest.py - 回測引擎（從 BingX 抓歷史 K 線）
# ============================================================

import time
import hmac
import hashlib
import requests
import pandas as pd
import numpy as np
from datetime import datetime, timezone
from config import (
    API_KEY, SECRET_KEY, BASE_URL, SYMBOL, INTERVAL,
    LEVERAGE, RISK_PCT, STOP_LOSS_PCT, INITIAL_CAPITAL,
    BACKTEST_START, BACKTEST_END, KLINE_LIMIT,
    MACD_FAST, MACD_SLOW, MACD_SIGNAL,
)
from strategy import add_indicators, get_signal, calc_stop_loss, calc_position_size


# ── 工具函式 ─────────────────────────────────────────────────

def _sign(params: dict) -> str:
    """產生 HMAC-SHA256 簽名"""
    query = "&".join(f"{k}={v}" for k, v in sorted(params.items()))
    return hmac.new(SECRET_KEY.encode(), query.encode(), hashlib.sha256).hexdigest()


def _dt_to_ms(dt_str: str) -> int:
    """將 'YYYY-MM-DD' 字串轉成毫秒時間戳"""
    dt = datetime.strptime(dt_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def fetch_klines_range(symbol: str, interval: str,
                        start_ms: int, end_ms: int) -> pd.DataFrame:
    """
    分批從 BingX 抓取指定日期範圍的歷史 K 線
    :return: DataFrame（timestamp, open, high, low, close, volume）
    """
    # 時間框架對應毫秒數
    interval_ms = {
        "1m": 60_000, "5m": 300_000, "15m": 900_000,
        "1h": 3_600_000, "4h": 14_400_000, "1d": 86_400_000,
    }.get(interval, 3_600_000)

    all_rows = []
    current_start = start_ms

    print(f"[回測] 開始抓取 {symbol} {interval} K 線資料...")

    while current_start < end_ms:
        params = {
            "symbol":    symbol,
            "interval":  interval,
            "startTime": current_start,
            "endTime":   end_ms,
            "limit":     KLINE_LIMIT,
            "timestamp": int(time.time() * 1000),
        }
        params["signature"] = _sign(params)

        try:
            resp = requests.get(
                f"{BASE_URL}/openApi/swap/v3/quote/klines",
                params=params,
                headers={"X-BX-APIKEY": API_KEY},
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()

            # BingX 回傳格式：{"data": {"klines": [...]}}
            klines = data.get("data", {}).get("klines", [])
            if not klines:
                break

            for k in klines:
                all_rows.append({
                    "timestamp": int(k["time"]),
                    "open":  float(k["open"]),
                    "high":  float(k["high"]),
                    "low":   float(k["low"]),
                    "close": float(k["close"]),
                    "volume": float(k["volume"]),
                })

            # 移動到下一批的起始時間
            last_ts = int(klines[-1]["time"])
            current_start = last_ts + interval_ms

            # 避免觸發頻率限制
            time.sleep(0.3)

        except Exception as e:
            print(f"[回測] 抓取 K 線失敗: {e}")
            break

    if not all_rows:
        return pd.DataFrame()

    df = pd.DataFrame(all_rows).drop_duplicates("timestamp").sort_values("timestamp")
    df["datetime"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    df = df.reset_index(drop=True)
    print(f"[回測] 共抓取 {len(df)} 根 K 線")
    return df


# ── 回測核心邏輯 ─────────────────────────────────────────────

class BacktestEngine:
    """簡單的事件驅動回測引擎"""

    def __init__(self, df: pd.DataFrame, initial_capital: float = INITIAL_CAPITAL):
        self.df      = df.copy()
        self.capital = initial_capital
        self.initial_capital = initial_capital

        # 當前倉位狀態
        self.position  = None   # None | "LONG" | "SHORT"
        self.entry_price = 0.0
        self.qty       = 0.0
        self.stop_loss = 0.0

        # 交易記錄
        self.trades: list[dict] = []

    def _open_position(self, side: str, price: float, stop_loss: float, qty: float):
        """建立倉位"""
        self.position   = side
        self.entry_price = price
        self.stop_loss  = stop_loss
        self.qty        = qty

    def _close_position(self, close_price: float, reason: str) -> float:
        """平倉並計算損益"""
        if self.position == "LONG":
            pnl = (close_price - self.entry_price) * self.qty
        else:
            pnl = (self.entry_price - close_price) * self.qty

        self.capital += pnl

        self.trades.append({
            "side":        self.position,
            "entry":       self.entry_price,
            "exit":        close_price,
            "qty":         self.qty,
            "pnl":         pnl,
            "reason":      reason,
        })

        self.position  = None
        self.entry_price = 0.0
        self.qty       = 0.0
        self.stop_loss = 0.0
        return pnl

    def run(self) -> list[dict]:
        """執行回測，返回交易記錄列表"""
        # 先計算所有指標
        self.df = add_indicators(self.df)

        min_bars = MACD_SLOW + MACD_SIGNAL + 5

        for i in range(min_bars, len(self.df)):
            # 取到當前索引的子集（模擬 get_signal 只看到 i 根 K 線）
            window = self.df.iloc[: i + 1]
            current_bar = self.df.iloc[i]
            current_price = current_bar["close"]
            current_low   = current_bar["low"]
            current_high  = current_bar["high"]

            # ── 先檢查停損是否觸發 ──────────────────────────
            if self.position == "LONG" and current_low <= self.stop_loss:
                self._close_position(self.stop_loss, "停損")
                continue

            if self.position == "SHORT" and current_high >= self.stop_loss:
                self._close_position(self.stop_loss, "停損")
                continue

            # ── 取得信號 ────────────────────────────────────
            signal = get_signal(window)

            # ── 反向信號平倉 ────────────────────────────────
            if self.position == "LONG" and signal == "SHORT":
                self._close_position(current_price, "反向信號平倉")

            elif self.position == "SHORT" and signal == "LONG":
                self._close_position(current_price, "反向信號平倉")

            # ── 無倉位時開倉 ────────────────────────────────
            if self.position is None and signal in ("LONG", "SHORT"):
                sl = calc_stop_loss(signal, current_price, STOP_LOSS_PCT)
                qty = calc_position_size(
                    self.capital, RISK_PCT, current_price, sl, LEVERAGE
                )
                if qty > 0:
                    self._open_position(signal, current_price, sl, qty)

        # 回測結束時若仍有倉位則強制平倉
        if self.position is not None:
            last_price = self.df.iloc[-1]["close"]
            self._close_position(last_price, "回測結束平倉")

        return self.trades


# ── 績效統計 ─────────────────────────────────────────────────

def calc_metrics(trades: list[dict], initial_capital: float) -> dict:
    """計算回測績效指標"""
    if not trades:
        return {}

    df = pd.DataFrame(trades)
    total   = len(df)
    wins    = (df["pnl"] > 0).sum()
    losses  = (df["pnl"] <= 0).sum()
    win_rate = wins / total * 100 if total > 0 else 0

    gross_profit = df[df["pnl"] > 0]["pnl"].sum()
    gross_loss   = abs(df[df["pnl"] <= 0]["pnl"].sum())
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")

    # 最大回撤（資金曲線）
    cumulative = initial_capital + df["pnl"].cumsum()
    rolling_max = cumulative.cummax()
    drawdown    = (cumulative - rolling_max) / rolling_max * 100
    max_drawdown = drawdown.min()

    # 停損次數
    stop_count = (df["reason"] == "停損").sum()

    total_return = (cumulative.iloc[-1] - initial_capital) / initial_capital * 100

    return {
        "總交易次數":   total,
        "獲利次數":     int(wins),
        "虧損次數":     int(losses),
        "勝率":         f"{win_rate:.2f}%",
        "總報酬率":     f"{total_return:.2f}%",
        "獲利因子":     f"{profit_factor:.2f}",
        "最大回撤":     f"{max_drawdown:.2f}%",
        "停損次數":     int(stop_count),
        "總損益(USDT)": f"{df['pnl'].sum():.2f}",
        "期末資金":     f"{initial_capital + df['pnl'].sum():.2f}",
    }


def print_report(metrics: dict, trades: list[dict]):
    """印出回測報告表格"""
    print("\n" + "=" * 50)
    print("           📊  回測績效報告")
    print("=" * 50)
    for key, val in metrics.items():
        print(f"  {key:<15} {val}")
    print("=" * 50)

    if trades:
        print("\n  最近 10 筆交易：")
        print(f"  {'方向':<6} {'開倉價':>10} {'平倉價':>10} {'損益':>10} {'原因'}")
        print("  " + "-" * 52)
        for t in trades[-10:]:
            side_str = "做多" if t["side"] == "LONG" else "做空"
            pnl_str  = f"{t['pnl']:+.2f}"
            print(f"  {side_str:<6} {t['entry']:>10.2f} {t['exit']:>10.2f} "
                  f"{pnl_str:>10} {t['reason']}")
    print("=" * 50 + "\n")


# ── 主入口 ───────────────────────────────────────────────────

def run_backtest(symbol: str = SYMBOL, interval: str = INTERVAL,
                 start: str = BACKTEST_START, end: str = BACKTEST_END):
    """執行完整回測流程"""
    start_ms = _dt_to_ms(start)
    end_ms   = _dt_to_ms(end)

    # 抓取歷史資料
    df = fetch_klines_range(symbol, interval, start_ms, end_ms)
    if df.empty:
        print("[回測] 無法取得 K 線資料，回測中止")
        return

    # 執行回測
    engine = BacktestEngine(df)
    trades = engine.run()

    # 計算並印出績效
    metrics = calc_metrics(trades, INITIAL_CAPITAL)
    print_report(metrics, trades)
    return metrics, trades


if __name__ == "__main__":
    run_backtest()
