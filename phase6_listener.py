"""
phase6_listener.py - ACS 2.0 (Authenticated Command System) Phase 6
Bitcoin Core regtest 監聽器 + OP_RETURN 解密 + GPIO 電子鎖觸發

流程：
  新區塊 / mempool 交易
    → 解析 OP_RETURN payload
    → 解密指令
    → 呼叫 gpio_mock.trigger_lock()

接駁現有模組：
  若專案已有加密模組，將第 ──CRYPTO ADAPTER── 區塊替換為對應 import 即可。
  若專案已有 RPC 連線模組，將第 ──RPC ADAPTER── 區塊替換即可。
"""

from __future__ import annotations

import json
import time
import base64
import hashlib
import hmac
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import requests
from requests.auth import HTTPBasicAuth

import gpio_mock as GPIO

# ─────────────────────────────────────────────────────────────────────────────
# 設定載入
# ─────────────────────────────────────────────────────────────────────────────

_CONFIG_PATH = Path(__file__).parent / "config.json"


def load_config() -> dict[str, Any]:
    if not _CONFIG_PATH.exists():
        print(f"[ERROR] 找不到設定檔：{_CONFIG_PATH}")
        sys.exit(1)
    with _CONFIG_PATH.open(encoding="utf-8") as f:
        return json.load(f)


# ─────────────────────────────────────────────────────────────────────────────
# ──CRYPTO ADAPTER──
# 若專案已有加密模組（例如 acs_crypto.py），請替換此區塊：
#   from acs_crypto import decrypt_payload
# ─────────────────────────────────────────────────────────────────────────────

def decrypt_payload(raw_hex: str, secret_key: str) -> str | None:
    """
    預設解密邏輯：HMAC-SHA256 簽章驗證 + Base64 明文。

    格式期望：<base64(plaintext)>:<hex(hmac-sha256)>
    例如：T1BFTl9MT0NL:3a9f...（OPEN_LOCK 的編碼）

    ── 替換說明 ──────────────────────────────────────────────
    若您使用 AES-CBC / AES-GCM 或其他方案，將此函數內容替換，
    保持回傳值語意不變：
      成功 → 解密後的明文字串
      失敗 → None
    ──────────────────────────────────────────────────────────
    """
    try:
        if ":" not in raw_hex:
            # 無簽章模式：直接 hex → UTF-8（開發測試用）
            return bytes.fromhex(raw_hex).decode("utf-8")

        b64_payload, sig_hex = raw_hex.split(":", 1)
        plaintext_bytes = base64.b64decode(b64_payload)

        expected_sig = hmac.new(
            secret_key.encode(), plaintext_bytes, hashlib.sha256
        ).hexdigest()

        if not hmac.compare_digest(expected_sig, sig_hex):
            return None

        return plaintext_bytes.decode("utf-8")
    except Exception:
        return None


# ─────────────────────────────────────────────────────────────────────────────
# ──RPC ADAPTER──
# 若專案已有 Bitcoin RPC 模組（例如 bitcoin_rpc.py），請替換此區塊：
#   from bitcoin_rpc import BitcoinRPC
# ─────────────────────────────────────────────────────────────────────────────

class BitcoinRPC:
    """輕量 Bitcoin Core JSON-RPC 客戶端（無第三方依賴）。"""

    def __init__(self, host: str, port: int, user: str, password: str) -> None:
        self._url = f"http://{host}:{port}/"
        self._auth = HTTPBasicAuth(user, password)
        self._id = 0

    def call(self, method: str, *params: Any) -> Any:
        self._id += 1
        payload = {
            "jsonrpc": "1.1",
            "id": self._id,
            "method": method,
            "params": list(params),
        }
        resp = requests.post(
            self._url,
            json=payload,
            auth=self._auth,
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("error"):
            raise RuntimeError(f"RPC Error: {data['error']}")
        return data["result"]

    # 常用捷徑
    def getblockcount(self) -> int:
        return self.call("getblockcount")

    def getbestblockhash(self) -> str:
        return self.call("getbestblockhash")

    def getblock(self, blockhash: str, verbosity: int = 2) -> dict:
        return self.call("getblock", blockhash, verbosity)

    def getrawmempool(self, verbose: bool = False) -> list | dict:
        return self.call("getrawmempool", verbose)

    def getrawtransaction(self, txid: str, verbose: bool = True) -> dict:
        return self.call("getrawtransaction", txid, verbose)


# ─────────────────────────────────────────────────────────────────────────────
# OP_RETURN 解析
# ─────────────────────────────────────────────────────────────────────────────

def extract_op_return(tx: dict) -> str | None:
    """
    從交易的 vout 中提取 OP_RETURN payload hex 字串。
    OP_RETURN scriptPubKey 格式：6a<len><data>
    """
    for vout in tx.get("vout", []):
        script = vout.get("scriptPubKey", {})
        asm = script.get("asm", "")
        hex_script = script.get("hex", "")

        if asm.startswith("OP_RETURN"):
            # asm 格式："OP_RETURN <hex_payload>"
            parts = asm.split(" ", 1)
            if len(parts) == 2:
                return parts[1]

            # fallback：從 hex script 手動提取（6a = OP_RETURN）
            if hex_script.startswith("6a") and len(hex_script) > 4:
                length_byte = int(hex_script[2:4], 16)
                payload_hex = hex_script[4: 4 + length_byte * 2]
                return payload_hex

    return None


# ─────────────────────────────────────────────────────────────────────────────
# 步驟輸出工具
# ─────────────────────────────────────────────────────────────────────────────

def _ts() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def step(n: int, msg: str) -> None:
    print(f"[STEP {n}] {msg}")


def info(msg: str) -> None:
    print(f"[{_ts()}] {msg}")


def warn(msg: str) -> None:
    print(f"[WARN]  {msg}")


def error(msg: str) -> None:
    print(f"[ERROR] {msg}")


# ─────────────────────────────────────────────────────────────────────────────
# 交易處理核心
# ─────────────────────────────────────────────────────────────────────────────

def process_transaction(
    txid: str,
    tx: dict,
    secret_key: str,
    expected_command: str,
    gpio_pin: int,
    open_duration: float,
    seen: set[str],
) -> None:
    """處理單筆交易，執行完整的 Step 1-4 流程。"""
    if txid in seen:
        return
    seen.add(txid)

    step(1, f"偵測到新交易: {txid}")

    payload_hex = extract_op_return(tx)
    if payload_hex is None:
        return  # 無 OP_RETURN，靜默略過

    step(2, f"解析 OP_RETURN payload: {payload_hex}")

    plaintext = decrypt_payload(payload_hex, secret_key)
    if plaintext is None:
        warn(f"解密失敗，略過此交易（簽章不符或格式錯誤）")
        return

    step(3, f"解密成功，指令內容: {plaintext}")

    if plaintext.strip() != expected_command:
        warn(f"指令不符（期望 '{expected_command}'，收到 '{plaintext.strip()}'），略過")
        return

    step(4, "觸發 GPIO 模擬...")
    GPIO.trigger_lock(pin=gpio_pin, duration_sec=open_duration)
    info(f"電子鎖已觸發，將於 {open_duration} 秒後自動上鎖")
    print()  # 換行分隔


# ─────────────────────────────────────────────────────────────────────────────
# 監聽主迴圈
# ─────────────────────────────────────────────────────────────────────────────

def listen_mempool(
    rpc: BitcoinRPC,
    secret_key: str,
    expected_command: str,
    gpio_pin: int,
    open_duration: float,
    poll_interval: float,
    seen_txids: set[str],
) -> None:
    """輪詢 mempool，處理尚未見過的交易。"""
    txids: list[str] = rpc.getrawmempool(verbose=False)
    for txid in txids:
        if txid in seen_txids:
            continue
        try:
            tx = rpc.getrawtransaction(txid, verbose=True)
            process_transaction(
                txid, tx, secret_key, expected_command,
                gpio_pin, open_duration, seen_txids,
            )
        except Exception as exc:
            warn(f"無法讀取交易 {txid[:16]}…: {exc}")


def listen_blocks(
    rpc: BitcoinRPC,
    last_height: int,
    secret_key: str,
    expected_command: str,
    gpio_pin: int,
    open_duration: float,
    seen_txids: set[str],
) -> int:
    """掃描新區塊內的所有交易，回傳最新高度。"""
    current_height = rpc.getblockcount()
    if current_height <= last_height:
        return last_height

    for height in range(last_height + 1, current_height + 1):
        blockhash = rpc.call("getblockhash", height)
        block = rpc.getblock(blockhash, verbosity=2)
        info(f"掃描區塊 #{height}  hash={blockhash[:16]}…  txs={len(block['tx'])}")
        for tx in block["tx"]:
            txid = tx.get("txid", "")
            process_transaction(
                txid, tx, secret_key, expected_command,
                gpio_pin, open_duration, seen_txids,
            )

    return current_height


# ─────────────────────────────────────────────────────────────────────────────
# 啟動入口
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    cfg = load_config()

    rpc_cfg = cfg["rpc"]
    lock_cfg = cfg["lock"]
    listener_cfg = cfg["listener"]
    crypto_cfg = cfg["crypto"]

    # GPIO 初始化
    GPIO.setmode(GPIO.BCM)
    GPIO.setup(lock_cfg["gpio_pin"], GPIO.OUT)

    # RPC 連線
    rpc = BitcoinRPC(
        host=rpc_cfg["host"],
        port=rpc_cfg["port"],
        user=rpc_cfg["user"],
        password=rpc_cfg["password"],
    )

    info(f"連接 Bitcoin Core ({rpc_cfg['network']}) @ {rpc_cfg['host']}:{rpc_cfg['port']}")
    try:
        height = rpc.getblockcount()
        info(f"RPC 連線成功，目前區塊高度：{height}")
    except Exception as exc:
        error(f"無法連接 Bitcoin Core RPC：{exc}")
        sys.exit(1)

    print()
    info("ACS 2.0 Phase 6 監聽器啟動，等待 OP_RETURN 指令...")
    info(f"監聽模式：{'ZMQ' if listener_cfg['use_zmq'] else 'Polling'} | "
         f"Interval={listener_cfg['poll_interval_sec']}s | "
         f"GPIO PIN={lock_cfg['gpio_pin']} | "
         f"開鎖持續={lock_cfg['open_duration_sec']}s")
    print()

    seen_txids: set[str] = set()
    last_height = height
    poll_interval = listener_cfg["poll_interval_sec"]

    try:
        while True:
            # 掃描新區塊
            last_height = listen_blocks(
                rpc, last_height,
                secret_key=crypto_cfg["secret_key"],
                expected_command=crypto_cfg["expected_command"],
                gpio_pin=lock_cfg["gpio_pin"],
                open_duration=lock_cfg["open_duration_sec"],
                seen_txids=seen_txids,
            )

            # 掃描 mempool（未確認交易）
            listen_mempool(
                rpc,
                secret_key=crypto_cfg["secret_key"],
                expected_command=crypto_cfg["expected_command"],
                gpio_pin=lock_cfg["gpio_pin"],
                open_duration=lock_cfg["open_duration_sec"],
                poll_interval=poll_interval,
                seen_txids=seen_txids,
            )

            time.sleep(poll_interval)

    except KeyboardInterrupt:
        print()
        info("收到中斷信號，清理 GPIO 並退出...")
        GPIO.cleanup()
        info("ACS 2.0 Phase 6 監聽器已停止")


if __name__ == "__main__":
    main()
