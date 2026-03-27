# ============================================================
# strategy.py - 指標計算與交易信號
# ============================================================

import numpy as np
import pandas as pd
from config import (
    MACD_FAST, MACD_SLOW, MACD_SIGNAL,
    RSI_PERIOD, RSI_OVERSOLD, RSI_OVERBOUGHT,
    EMA_PERIOD,
)


# ── 技術指標計算 ─────────────────────────────────────────────

def calc_ema(series: pd.Series, period: int) -> pd.Series:
    """計算指數移動平均線（EMA）"""
    return series.ewm(span=period, adjust=False).mean()


def calc_macd(series: pd.Series,
              fast: int = MACD_FAST,
              slow: int = MACD_SLOW,
              signal: int = MACD_SIGNAL) -> tuple[pd.Series, pd.Series, pd.Series]:
    """
    計算 MACD 指標
    :return: (macd_line, signal_line, histogram)
    """
    ema_fast   = calc_ema(series, fast)
    ema_slow   = calc_ema(series, slow)
    macd_line  = ema_fast - ema_slow
    signal_line = calc_ema(macd_line, signal)
    histogram  = macd_line - signal_line
    return macd_line, signal_line, histogram


def calc_rsi(series: pd.Series, period: int = RSI_PERIOD) -> pd.Series:
    """計算 RSI 相對強弱指數"""
    delta  = series.diff()
    gain   = delta.clip(lower=0)
    loss   = -delta.clip(upper=0)

    # 使用 Wilder 平滑法（等同 EMA alpha=1/period）
    avg_gain = gain.ewm(alpha=1 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False).mean()

    rs  = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    return rsi


def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """
    在 DataFrame 上計算所有指標並回傳
    必要欄位：close（收盤價）
    """
    close = df["close"]

    # EMA25
    df["ema25"] = calc_ema(close, EMA_PERIOD)

    # MACD
    df["macd"], df["macd_signal"], df["macd_hist"] = calc_macd(close)

    # RSI
    df["rsi"] = calc_rsi(close)

    return df


# ── 信號判斷 ─────────────────────────────────────────────────

def get_signal(df: pd.DataFrame) -> str:
    """
    根據最近兩根 K 線判斷交易信號
    :return: "LONG" | "SHORT" | "NONE"
    """
    # 至少需要足夠的資料
    if len(df) < MACD_SLOW + MACD_SIGNAL + 5:
        return "NONE"

    # 取最後兩根已收盤的 K 線（-1 為當前未收盤，-2 與 -3 為最近兩根已收盤）
    prev = df.iloc[-3]   # 前一根
    curr = df.iloc[-2]   # 最新收盤根

    # ── 多方條件 ────────────────────────────────────────────
    # 1. MACD 金叉：前一根 macd < signal，當前 macd > signal
    macd_cross_up = (prev["macd"] < prev["macd_signal"]) and \
                    (curr["macd"] > curr["macd_signal"])

    # 2. RSI 從超賣回升：前一根 RSI < 超賣門檻，當前 RSI >= 超賣門檻
    rsi_recover = (prev["rsi"] < RSI_OVERSOLD) and \
                  (curr["rsi"] >= RSI_OVERSOLD)

    # 3. 價格在 EMA25 上方
    price_above_ema = curr["close"] > curr["ema25"]

    if macd_cross_up and rsi_recover and price_above_ema:
        return "LONG"

    # ── 空方條件 ────────────────────────────────────────────
    # 1. MACD 死叉：前一根 macd > signal，當前 macd < signal
    macd_cross_down = (prev["macd"] > prev["macd_signal"]) and \
                      (curr["macd"] < curr["macd_signal"])

    # 2. RSI 從超買回落：前一根 RSI > 超買門檻，當前 RSI <= 超買門檻
    rsi_pullback = (prev["rsi"] > RSI_OVERBOUGHT) and \
                   (curr["rsi"] <= RSI_OVERBOUGHT)

    # 3. 價格在 EMA25 下方
    price_below_ema = curr["close"] < curr["ema25"]

    if macd_cross_down and rsi_pullback and price_below_ema:
        return "SHORT"

    return "NONE"


def calc_stop_loss(side: str, entry_price: float, stop_pct: float) -> float:
    """
    計算停損價格
    :param side: "LONG" 或 "SHORT"
    :param entry_price: 開倉價格
    :param stop_pct: 停損比例（例如 0.02 = 2%）
    :return: 停損價格
    """
    if side == "LONG":
        return round(entry_price * (1 - stop_pct), 4)
    else:
        return round(entry_price * (1 + stop_pct), 4)


def calc_position_size(capital: float, risk_pct: float,
                        entry_price: float, stop_loss: float,
                        leverage: int) -> float:
    """
    根據風險百分比計算倉位大小（張數）
    公式：qty = (capital × risk_pct) / |entry - stop_loss|
    再考慮槓桿後換算成合約張數
    :return: 建議下單數量（USDT 名義價值）
    """
    # 每張合約的最大虧損金額
    risk_amount   = capital * risk_pct
    # 每單位的停損距離
    stop_distance = abs(entry_price - stop_loss)

    if stop_distance == 0:
        return 0.0

    # 名義倉位大小
    notional = (risk_amount / stop_distance) * entry_price
    # 實際保證金只需 notional / leverage，但名義不變
    qty = notional / entry_price

    return round(qty, 4)
