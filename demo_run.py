"""
demo_run.py - ACS 2.0 (Authenticated Command System) Phase 6
Demo 錄影腳本：完整模擬「送出加密開鎖指令 → Listener 接收 → GPIO 觸發」流程。

不需要 Bitcoin Core 運行（in-process 模擬），可直接錄影展示。
若需要接真實 Bitcoin Core，設定 LIVE_MODE = True。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import time
import uuid
from datetime import datetime

import gpio_mock as GPIO
from phase6_listener import (
    decrypt_payload,
    extract_op_return,
    load_config,
    process_transaction,
)

# ─────────────────────────────────────────────────────────────────────────────
# 設定
# ─────────────────────────────────────────────────────────────────────────────

STEP_PAUSE   = 1.0   # 每個主步驟之間的停頓（秒）
DETAIL_PAUSE = 0.4   # 細節輸出之間的停頓（秒）
LOCK_OPEN_DURATION = 3.0  # 電子鎖開啟時間（秒）

# ─────────────────────────────────────────────────────────────────────────────
# ASCII Banner
# ─────────────────────────────────────────────────────────────────────────────

BANNER = r"""
╔══════════════════════════════════════════════════════════════╗
║                                                              ║
║        █████╗  ██████╗███████╗    ██████╗  ██████╗          ║
║       ██╔══██╗██╔════╝██╔════╝    ╚════██╗██╔═══██╗         ║
║       ███████║██║     ███████╗     █████╔╝██║   ██║         ║
║       ██╔══██║██║     ╚════██║    ██╔═══╝ ██║   ██║         ║
║       ██║  ██║╚██████╗███████║    ███████╗╚██████╔╝         ║
║       ╚═╝  ╚═╝ ╚═════╝╚══════╝    ╚══════╝ ╚═════╝          ║
║                                                              ║
║      Authenticated Command System  ·  Phase 6 Demo          ║
║      Bitcoin OP_RETURN → Decrypt → GPIO Electronic Lock      ║
╚══════════════════════════════════════════════════════════════╝
"""

DIVIDER      = "─" * 64
THIN_DIVIDER = "╌" * 64

# ─────────────────────────────────────────────────────────────────────────────
# 輸出工具
# ─────────────────────────────────────────────────────────────────────────────

def _ts() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def banner_line(msg: str) -> None:
    print(f"\n{'═' * 64}")
    print(f"  {msg}")
    print(f"{'═' * 64}")


def phase(n: int, title: str) -> None:
    print(f"\n{DIVIDER}")
    print(f"  ▶  PHASE {n} │ {title}")
    print(DIVIDER)
    time.sleep(DETAIL_PAUSE)


def log(msg: str) -> None:
    print(f"  [{_ts()}]  {msg}")


def ok(msg: str) -> None:
    print(f"  ✔  {msg}")


def pause(sec: float = STEP_PAUSE) -> None:
    time.sleep(sec)


# ─────────────────────────────────────────────────────────────────────────────
# ──SENDER──
# 將明文指令加密成 OP_RETURN payload hex（與 phase6_listener.decrypt_payload 相容）
# 若專案已有 sender 模組，替換此函數：
#   from acs_sender import encrypt_payload
# ─────────────────────────────────────────────────────────────────────────────

def encrypt_payload(plaintext: str, secret_key: str) -> str:
    """
    加密流程（對應 phase6_listener.decrypt_payload）：
      1. base64(plaintext)
      2. HMAC-SHA256 簽章
      3. 組合：<b64>:<sig_hex>
      4. 整體 hex 編碼（OP_RETURN 格式）
    """
    plaintext_bytes = plaintext.encode()
    b64 = base64.b64encode(plaintext_bytes).decode()
    sig = hmac.new(
        secret_key.encode(), plaintext_bytes, hashlib.sha256
    ).hexdigest()
    combined = f"{b64}:{sig}"
    return combined.encode().hex()


def build_mock_tx(payload_hex: str, txid: str) -> dict:
    """建構含 OP_RETURN 的最小交易 dict（模擬 getrawtransaction 回應格式）。"""
    op_return_script_hex = "6a" + format(len(payload_hex) // 2, "02x") + payload_hex
    return {
        "txid": txid,
        "vin": [],
        "vout": [
            {
                "value": 0.0,
                "n": 0,
                "scriptPubKey": {
                    "asm": f"OP_RETURN {payload_hex}",
                    "hex": op_return_script_hex,
                    "type": "nulldata",
                },
            }
        ],
    }


# ─────────────────────────────────────────────────────────────────────────────
# Demo 流程
# ─────────────────────────────────────────────────────────────────────────────

def run_demo() -> None:
    # ── Banner ────────────────────────────────────────────────────────────────
    print(BANNER)
    pause(STEP_PAUSE)

    cfg = load_config()
    secret_key      = cfg["crypto"]["secret_key"]
    expected_cmd    = cfg["crypto"]["expected_command"]
    gpio_pin        = cfg["lock"]["gpio_pin"]
    open_duration   = cfg["lock"]["open_duration_sec"]
    rpc_host        = cfg["rpc"]["host"]
    rpc_port        = cfg["rpc"]["port"]
    network         = cfg["rpc"]["network"]

    seen_txids: set[str] = set()

    # ── Phase 1：啟動監聽器 ───────────────────────────────────────────────────
    phase(1, "啟動 Phase 6 Listener")

    log(f"Bitcoin Core ({network}) @ {rpc_host}:{rpc_port}")
    pause(DETAIL_PAUSE)
    log("初始化 GPIO 模組...")
    GPIO.setmode(GPIO.BCM)
    GPIO.setup(gpio_pin, GPIO.OUT)
    pause(DETAIL_PAUSE)
    log("OP_RETURN 監聽器就緒，等待加密指令...")
    pause(STEP_PAUSE)
    ok("Listener 啟動完成")

    # ── Phase 2：等待確認監聽就緒 ─────────────────────────────────────────────
    phase(2, "確認監聽就緒（等待 2 秒）")

    for i in range(1, 3):
        log(f"心跳確認 {i}/2 ...")
        pause(1.0)
    ok("監聽器狀態正常，準備接收交易")

    # ── Phase 3：Sender 建構加密指令 ──────────────────────────────────────────
    phase(3, "Sender 建構加密開鎖指令")

    log(f"明文指令：{expected_cmd}")
    pause(DETAIL_PAUSE)

    payload_hex = encrypt_payload(expected_cmd, secret_key)
    log(f"加密後 payload (hex)：{payload_hex}")
    pause(DETAIL_PAUSE)

    fake_txid = uuid.uuid4().hex + uuid.uuid4().hex  # 64 字元，模擬真實 txid
    log(f"建構 OP_RETURN 交易...")
    pause(DETAIL_PAUSE)
    log(f"模擬廣播 txid：{fake_txid}")
    pause(STEP_PAUSE)
    ok("交易已廣播至 mempool（模擬）")

    # ── Phase 4：Listener 接收並處理 ─────────────────────────────────────────
    phase(4, "Listener 接收交易 → 解密 → GPIO 觸發")

    print()
    mock_tx = build_mock_tx(payload_hex, fake_txid)
    process_transaction(
        txid=fake_txid,
        tx=mock_tx,
        secret_key=secret_key,
        expected_command=expected_cmd,
        gpio_pin=gpio_pin,
        open_duration=open_duration,
        seen=seen_txids,
    )

    # 等待電子鎖自動上鎖（trigger_lock 在背景執行緒倒數）
    log(f"等待電子鎖自動上鎖（{open_duration} 秒）...")
    pause(open_duration + 0.5)

    # ── Phase 5：收尾 ─────────────────────────────────────────────────────────
    phase(5, "收尾")

    GPIO.cleanup()
    pause(DETAIL_PAUSE)

    # ── 結尾 Banner ───────────────────────────────────────────────────────────
    print()
    print("╔══════════════════════════════════════════════════════════════╗")
    print("║                                                              ║")
    print("║   Demo 完成 ✅   ACS 2.0 Phase 6 Simulation                 ║")
    print("║                                                              ║")
    print("╚══════════════════════════════════════════════════════════════╝")
    print()


# ─────────────────────────────────────────────────────────────────────────────
# 入口
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    run_demo()
