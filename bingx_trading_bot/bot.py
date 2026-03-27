# ============================================================
# bot.py - 主程式 / 主循環
# BingX 永續合約交易機器人（MACD + RSI + EMA25 策略）
# ============================================================

import time
import hmac
import hashlib
import logging
import requests
import pandas as pd
from datetime import datetime

from config import (
    API_KEY, SECRET_KEY, BASE_URL,
    SYMBOL, INTERVAL, LEVERAGE, RISK_PCT,
    STOP_LOSS_PCT, CHECK_INTERVAL, KLINE_LIMIT,
)
from strategy import add_indicators, get_signal, calc_stop_loss, calc_position_size
from telegram_notify import (
    notify_open_position, notify_close_position,
    notify_error, notify_start,
)

# ── 日誌設定 ─────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("bot.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger(__name__)


# ── API 工具函式 ──────────────────────────────────────────────

def _timestamp() -> int:
    """回傳當前毫秒時間戳"""
    return int(time.time() * 1000)


def _sign(params: dict) -> str:
    """產生 HMAC-SHA256 簽名"""
    query = "&".join(f"{k}={v}" for k, v in sorted(params.items()))
    return hmac.new(SECRET_KEY.encode(), query.encode(), hashlib.sha256).hexdigest()


def _get(endpoint: str, params: dict) -> dict:
    """發送 GET 請求（自動加簽名）"""
    params["timestamp"] = _timestamp()
    params["signature"] = _sign(params)
    resp = requests.get(
        f"{BASE_URL}{endpoint}",
        params=params,
        headers={"X-BX-APIKEY": API_KEY},
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()


def _post(endpoint: str, params: dict) -> dict:
    """發送 POST 請求（自動加簽名）"""
    params["timestamp"] = _timestamp()
    params["signature"] = _sign(params)
    resp = requests.post(
        f"{BASE_URL}{endpoint}",
        params=params,
        headers={"X-BX-APIKEY": API_KEY},
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()


# ── 帳戶與行情 ────────────────────────────────────────────────

def get_balance() -> float:
    """取得帳戶可用 USDT 餘額"""
    try:
        data = _get("/openApi/swap/v2/user/balance", {})
        # 取出 USDT 可用餘額
        for asset in data.get("data", {}).get("balance", []):
            if asset.get("asset") == "USDT":
                return float(asset.get("availableMargin", 0))
        return 0.0
    except Exception as e:
        logger.error(f"取得餘額失敗: {e}")
        notify_error(f"取得餘額失敗: {e}")
        return 0.0


def get_klines(symbol: str, interval: str, limit: int = KLINE_LIMIT) -> pd.DataFrame:
    """取得最新 K 線資料"""
    try:
        data = _get("/openApi/swap/v3/quote/klines", {
            "symbol":   symbol,
            "interval": interval,
            "limit":    limit,
        })
        klines = data.get("data", {}).get("klines", [])
        if not klines:
            return pd.DataFrame()

        rows = [{
            "timestamp": int(k["time"]),
            "open":      float(k["open"]),
            "high":      float(k["high"]),
            "low":       float(k["low"]),
            "close":     float(k["close"]),
            "volume":    float(k["volume"]),
        } for k in klines]

        df = pd.DataFrame(rows).sort_values("timestamp").reset_index(drop=True)
        return df
    except Exception as e:
        logger.error(f"取得 K 線失敗: {e}")
        notify_error(f"取得 K 線失敗: {e}")
        return pd.DataFrame()


def get_position(symbol: str) -> dict | None:
    """取得當前倉位（若無則回傳 None）"""
    try:
        data = _get("/openApi/swap/v2/user/positions", {"symbol": symbol})
        positions = data.get("data", [])
        for pos in positions:
            # positionAmt 不為零表示持有倉位
            if float(pos.get("positionAmt", 0)) != 0:
                return pos
        return None
    except Exception as e:
        logger.error(f"取得倉位失敗: {e}")
        notify_error(f"取得倉位失敗: {e}")
        return None


def get_latest_price(symbol: str) -> float:
    """取得最新成交價"""
    try:
        data = _get("/openApi/swap/v2/quote/price", {"symbol": symbol})
        return float(data.get("data", {}).get("price", 0))
    except Exception as e:
        logger.error(f"取得最新價格失敗: {e}")
        return 0.0


# ── 下單函式 ─────────────────────────────────────────────────

def set_leverage(symbol: str, leverage: int):
    """設定槓桿倍數"""
    try:
        _post("/openApi/swap/v2/trade/leverage", {
            "symbol":   symbol,
            "side":     "LONG",
            "leverage": leverage,
        })
        _post("/openApi/swap/v2/trade/leverage", {
            "symbol":   symbol,
            "side":     "SHORT",
            "leverage": leverage,
        })
        logger.info(f"槓桿設定為 {leverage}x")
    except Exception as e:
        logger.error(f"設定槓桿失敗: {e}")
        notify_error(f"設定槓桿失敗: {e}")


def place_market_order(symbol: str, side: str, qty: float) -> dict | None:
    """
    市價下單
    :param side: "BUY"（做多）或 "SELL"（做空）
    :param qty: 下單數量
    """
    try:
        # 判斷倉位方向
        position_side = "LONG" if side == "BUY" else "SHORT"
        data = _post("/openApi/swap/v2/trade/order", {
            "symbol":       symbol,
            "side":         side,
            "positionSide": position_side,
            "type":         "MARKET",
            "quantity":     qty,
        })
        logger.info(f"市價下單成功: {side} {qty} {symbol}")
        return data.get("data", {})
    except Exception as e:
        logger.error(f"下單失敗: {e}")
        notify_error(f"下單失敗 ({side} {qty} {symbol}): {e}")
        return None


def place_stop_loss_order(symbol: str, side: str,
                           qty: float, stop_price: float) -> dict | None:
    """
    掛停損單（STOP_MARKET）
    :param side: 平倉方向（做多停損 -> "SELL"，做空停損 -> "BUY"）
    """
    try:
        position_side = "LONG" if side == "SELL" else "SHORT"
        data = _post("/openApi/swap/v2/trade/order", {
            "symbol":       symbol,
            "side":         side,
            "positionSide": position_side,
            "type":         "STOP_MARKET",
            "quantity":     qty,
            "stopPrice":    stop_price,
            "workingType":  "MARK_PRICE",
        })
        logger.info(f"停損單掛出: {side} stopPrice={stop_price}")
        return data.get("data", {})
    except Exception as e:
        logger.error(f"停損單失敗: {e}")
        notify_error(f"停損單失敗 ({side} stopPrice={stop_price}): {e}")
        return None


def close_position(symbol: str, position: dict):
    """
    平倉（市價對沖平倉）
    :param position: get_position() 回傳的倉位字典
    """
    try:
        amt = float(position.get("positionAmt", 0))
        pos_side = position.get("positionSide", "LONG")

        # 平多倉 -> 賣出；平空倉 -> 買入
        side = "SELL" if pos_side == "LONG" else "BUY"
        qty  = abs(amt)

        data = _post("/openApi/swap/v2/trade/order", {
            "symbol":       symbol,
            "side":         side,
            "positionSide": pos_side,
            "type":         "MARKET",
            "quantity":     qty,
        })
        logger.info(f"平倉成功: {pos_side} {qty} {symbol}")
        return data.get("data", {})
    except Exception as e:
        logger.error(f"平倉失敗: {e}")
        notify_error(f"平倉失敗 ({symbol}): {e}")
        return None


def cancel_all_orders(symbol: str):
    """取消所有掛單（包含停損單）"""
    try:
        _post("/openApi/swap/v2/trade/allOpenOrders", {"symbol": symbol})
        logger.info(f"已取消所有 {symbol} 掛單")
    except Exception as e:
        logger.warning(f"取消掛單失敗: {e}")


# ── 主循環 ────────────────────────────────────────────────────

class TradingBot:
    """BingX 合約交易機器人主類別"""

    def __init__(self):
        self.symbol   = SYMBOL
        self.interval = INTERVAL
        self.leverage = LEVERAGE
        # 追蹤當前倉位方向（用於 Telegram 通知）
        self.current_side: str | None = None
        self.entry_price: float = 0.0
        self.stop_order_id: str | None = None

    def run(self):
        """啟動主循環"""
        logger.info("=" * 50)
        logger.info(f"BingX 交易機器人啟動 | {self.symbol} {self.interval}")
        logger.info("=" * 50)

        # 發送啟動通知
        notify_start(self.symbol, self.interval, self.leverage)

        # 設定槓桿
        set_leverage(self.symbol, self.leverage)

        while True:
            try:
                self._tick()
            except KeyboardInterrupt:
                logger.info("收到中斷信號，機器人停止運行")
                break
            except Exception as e:
                logger.error(f"主循環發生未預期錯誤: {e}", exc_info=True)
                notify_error(f"主循環錯誤: {e}")

            # 等待下一個週期
            logger.info(f"等待 {CHECK_INTERVAL} 秒後再次檢查...")
            time.sleep(CHECK_INTERVAL)

    def _tick(self):
        """單次檢查邏輯"""
        logger.info(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 開始檢查信號...")

        # ── 取得資料 ────────────────────────────────────────
        df = get_klines(self.symbol, self.interval)
        if df.empty:
            logger.warning("K 線資料為空，跳過本次檢查")
            return

        # 計算指標
        df = add_indicators(df)

        # 取得信號
        signal = get_signal(df)
        logger.info(f"當前信號: {signal}")

        # 取得當前倉位
        position = get_position(self.symbol)

        # ── 有倉位時的處理 ──────────────────────────────────
        if position is not None:
            pos_side = position.get("positionSide", "LONG")
            entry    = float(position.get("entryPrice", 0))
            amt      = float(position.get("positionAmt", 0))

            # 反向信號 -> 平倉
            if (pos_side == "LONG" and signal == "SHORT") or \
               (pos_side == "SHORT" and signal == "LONG"):
                logger.info(f"反向信號出現，平倉 {pos_side}")
                current_price = get_latest_price(self.symbol)

                # 先取消停損單
                cancel_all_orders(self.symbol)

                # 執行平倉
                close_position(self.symbol, position)

                # 計算損益並通知
                if pos_side == "LONG":
                    pnl = (current_price - entry) * abs(amt)
                else:
                    pnl = (entry - current_price) * abs(amt)
                notify_close_position(pos_side, self.symbol, entry, current_price, pnl)

                # 清除本地狀態
                self.current_side = None
                self.entry_price  = 0.0

                # 稍等後再開新倉
                time.sleep(1)
                position = None

        # ── 無倉位時的處理 ──────────────────────────────────
        if position is None and signal in ("LONG", "SHORT"):
            # 取得帳戶餘額
            balance = get_balance()
            if balance <= 0:
                logger.warning("帳戶餘額不足，跳過開倉")
                return

            # 取得最新價格
            current_price = get_latest_price(self.symbol)
            if current_price <= 0:
                logger.warning("無法取得有效價格，跳過開倉")
                return

            # 計算停損價與倉位大小
            stop_price = calc_stop_loss(signal, current_price, STOP_LOSS_PCT)
            qty = calc_position_size(
                balance, RISK_PCT, current_price, stop_price, self.leverage
            )

            if qty <= 0:
                logger.warning("計算倉位大小為 0，跳過開倉")
                return

            logger.info(f"開倉信號: {signal} | 價格: {current_price} | "
                        f"數量: {qty} | 停損: {stop_price}")

            # 執行市價開倉
            side = "BUY" if signal == "LONG" else "SELL"
            order = place_market_order(self.symbol, side, qty)

            if order:
                self.current_side = signal
                self.entry_price  = current_price

                # 掛停損單
                sl_side = "SELL" if signal == "LONG" else "BUY"
                sl_order = place_stop_loss_order(
                    self.symbol, sl_side, qty, stop_price
                )

                # 發送 Telegram 通知
                notify_open_position(
                    signal, self.symbol, current_price,
                    qty, stop_price, self.leverage
                )
        else:
            logger.info("無信號或已有倉位，持續觀察")


# ── 程式入口 ─────────────────────────────────────────────────

if __name__ == "__main__":
    bot = TradingBot()
    bot.run()
