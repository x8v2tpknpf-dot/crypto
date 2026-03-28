"""
gpio_mock.py - ACS 2.0 (Authenticated Command System)
Raspberry Pi GPIO 模擬模組（Windows / 非 Pi 環境開發用）

提供與 RPi.GPIO 相同的介面：setup、output、cleanup
並額外提供 trigger_lock(pin, duration_sec) 供外部呼叫電子鎖。
"""

import time
import threading
from datetime import datetime
from pathlib import Path

# ── GPIO 模式常數（與 RPi.GPIO 相容）──────────────────────────────────────────
BCM = "BCM"
BOARD = "BOARD"
OUT = "OUT"
IN = "IN"
HIGH = 1
LOW = 0

# ── 內部狀態 ─────────────────────────────────────────────────────────────────
_mode: str | None = None
_pin_states: dict[int, int] = {}
_LOG_FILE = Path(__file__).parent / "gpio_log.txt"


# ── 私有工具函數 ──────────────────────────────────────────────────────────────

def _timestamp() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _log(message: str) -> None:
    """同時輸出到終端機與 gpio_log.txt。"""
    print(message)
    with _LOG_FILE.open("a", encoding="utf-8") as f:
        f.write(message + "\n")


def _pin_label(pin: int, value: int) -> str:
    level = "HIGH" if value == HIGH else "LOW "
    if value == HIGH:
        state_str = "鎖已開啟 🔓"
    else:
        state_str = "鎖已關閉 🔒"
    return f"[{_timestamp()}] GPIO PIN {pin} → {level} ({state_str})"


# ── RPi.GPIO 相容介面 ─────────────────────────────────────────────────────────

def setmode(mode: str) -> None:
    """設定 GPIO 編號模式（BCM / BOARD）。"""
    global _mode
    _mode = mode
    _log(f"[{_timestamp()}] GPIO 模式設定為 {mode}")


def setup(pin: int, direction: str) -> None:
    """初始化指定 GPIO pin 的方向（OUT / IN）。"""
    _pin_states[pin] = LOW
    _log(f"[{_timestamp()}] GPIO PIN {pin} 初始化為 {direction}")


def output(pin: int, value: int) -> None:
    """設定指定 GPIO pin 的電位（HIGH / LOW）。"""
    _pin_states[pin] = value
    _log(_pin_label(pin, value))


def input(pin: int) -> int:
    """讀取指定 GPIO pin 的目前電位。"""
    return _pin_states.get(pin, LOW)


def cleanup(pin: int | None = None) -> None:
    """釋放 GPIO 資源（模擬：重置 pin 狀態）。"""
    if pin is None:
        _pin_states.clear()
        _log(f"[{_timestamp()}] GPIO cleanup：所有 PIN 已重置")
    else:
        _pin_states.pop(pin, None)
        _log(f"[{_timestamp()}] GPIO cleanup：PIN {pin} 已重置")


# ── 電子鎖控制 API ────────────────────────────────────────────────────────────

def trigger_lock(pin: int, duration_sec: float = 3.0) -> None:
    """
    觸發電子鎖開啟，經過 duration_sec 秒後自動上鎖。

    Args:
        pin:          控制電磁鎖的 GPIO pin 編號（BCM）
        duration_sec: 開鎖持續秒數，預設 3 秒
    """
    if pin not in _pin_states:
        setup(pin, OUT)

    output(pin, HIGH)

    def _auto_lock():
        time.sleep(duration_sec)
        output(pin, LOW)

    t = threading.Thread(target=_auto_lock, daemon=True)
    t.start()


# ── 直接執行測試 ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    setmode(BCM)
    setup(17, OUT)

    print("\n=== ACS 2.0 GPIO Mock 測試 ===")
    trigger_lock(pin=17, duration_sec=3)

    # 等待自動上鎖完成
    time.sleep(4)
    cleanup()
    print("=== 測試結束 ===\n")
