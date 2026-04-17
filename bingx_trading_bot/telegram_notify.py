# ============================================================
# telegram_notify.py - Telegram 通知模組
# ============================================================

import requests
import logging
from config import TELEGRAM_TOKEN, TELEGRAM_CHAT_ID

logger = logging.getLogger(__name__)


def send_message(text: str) -> bool:
    """
    發送 Telegram 訊息
    :param text: 訊息內容（支援 Markdown）
    :return: 是否發送成功
    """
    if not TELEGRAM_TOKEN or TELEGRAM_TOKEN == "your_telegram_bot_token_here":
        logger.warning("Telegram Token 未設定，跳過通知")
        return False

    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "Markdown",
    }
    try:
        resp = requests.post(url, json=payload, timeout=10)
        resp.raise_for_status()
        return True
    except requests.RequestException as e:
        logger.error(f"Telegram 發送失敗: {e}")
        return False


def notify_open_position(side: str, symbol: str, price: float,
                          qty: float, stop_loss: float, leverage: int,
                          tp1_price: float = 0.0, tp2_price: float = 0.0):
    """發送開倉通知（含止盈目標）"""
    direction = "做多 📈" if side == "LONG" else "做空 📉"
    msg = (
        f"*【開倉通知】*\n"
        f"交易對：`{symbol}`\n"
        f"方向：{direction}\n"
        f"開倉價：`{price:.4f}` USDT\n"
        f"數量：`{qty}` 張\n"
        f"槓桿：`{leverage}x`\n"
        f"停損價：`{stop_loss:.4f}` USDT\n"
        f"TP1（10R/35%）：`{tp1_price:.4f}` USDT\n"
        f"TP2（20R/35%）：`{tp2_price:.4f}` USDT\n"
        f"TP3（30%）：反向信號觸發"
    )
    send_message(msg)


def notify_tp_hit(tp_level: int, side: str, symbol: str,
                  tp_price: float, closed_qty: float,
                  new_sl: float | None):
    """
    發送止盈達成通知
    :param tp_level: 1 或 2
    :param new_sl: TP1 時傳入保本停損價；TP2 時為 None
    """
    direction = "多單 📈" if side == "LONG" else "空單 📉"
    sl_line   = f"\n停損移至：`{new_sl:.4f}` USDT（保本）" if new_sl is not None else ""
    msg = (
        f"*【TP{tp_level} 達成】* ✅\n"
        f"交易對：`{symbol}`\n"
        f"方向：{direction}\n"
        f"止盈價：`{tp_price:.4f}` USDT\n"
        f"已平倉：`{closed_qty}` 張"
        f"{sl_line}"
    )
    send_message(msg)


def notify_close_position(side: str, symbol: str, entry_price: float,
                           close_price: float, pnl: float,
                           reason: str = "反向信號"):
    """發送全倉平倉通知"""
    direction = "多單 📈" if side == "LONG" else "空單 📉"
    pnl_emoji = "✅" if pnl >= 0 else "❌"
    msg = (
        f"*【平倉通知】*\n"
        f"交易對：`{symbol}`\n"
        f"方向：{direction}\n"
        f"出場原因：{reason}\n"
        f"開倉價：`{entry_price:.4f}` USDT\n"
        f"平倉價：`{close_price:.4f}` USDT\n"
        f"盈虧：{pnl_emoji} `{pnl:+.2f}` USDT"
    )
    send_message(msg)


def notify_error(error_msg: str):
    """發送錯誤通知"""
    msg = (
        f"*【錯誤通知】* ⚠️\n"
        f"```\n{error_msg}\n```"
    )
    send_message(msg)


def notify_start(symbol: str, interval: str, leverage: int):
    """發送機器人啟動通知"""
    msg = (
        f"*【機器人啟動】* 🤖\n"
        f"交易對：`{symbol}`\n"
        f"時間框架：`{interval}`\n"
        f"槓桿：`{leverage}x`\n"
        f"策略：MACD + RSI + EMA25\n"
        f"止盈：TP1(10R/35%) → TP2(20R/35%) → TP3(反向/30%)"
    )
    send_message(msg)
