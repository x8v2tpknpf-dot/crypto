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
    # 如果未設定 Token 則跳過
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
        # Telegram 通知本身失敗時只記錄 log，不再遞迴通知
        logger.error(f"Telegram 發送失敗: {e}")
        return False


def notify_open_position(side: str, symbol: str, price: float,
                          qty: float, stop_loss: float, leverage: int):
    """發送開倉通知"""
    direction = "做多 📈" if side == "LONG" else "做空 📉"
    msg = (
        f"*【開倉通知】*\n"
        f"交易對：`{symbol}`\n"
        f"方向：{direction}\n"
        f"開倉價：`{price:.4f}` USDT\n"
        f"數量：`{qty}` 張\n"
        f"槓桿：`{leverage}x`\n"
        f"停損價：`{stop_loss:.4f}` USDT"
    )
    send_message(msg)


def notify_close_position(side: str, symbol: str, entry_price: float,
                           close_price: float, pnl: float):
    """發送平倉通知"""
    direction = "多單 📈" if side == "LONG" else "空單 📉"
    pnl_emoji = "✅" if pnl >= 0 else "❌"
    msg = (
        f"*【平倉通知】*\n"
        f"交易對：`{symbol}`\n"
        f"方向：{direction}\n"
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
        f"策略：MACD + RSI + EMA25"
    )
    send_message(msg)
