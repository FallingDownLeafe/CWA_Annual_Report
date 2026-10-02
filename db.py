"""
db.py — 資料庫連線模組
用途：load_env（不依賴外部套件）+ 多主機依序嘗試的 MySQL 連線
"""
import os
import sys
from pathlib import Path
import pymysql


def load_env(debug: bool = False) -> dict:
    """
    從 .env 載入環境變數。
    凍結(.exe)時：讀 sys.executable 旁的 .env
    開發時：      讀 db.py 旁的 .env
    回傳已載入的 key→value dict（可用 debug=True 確認路徑與內容）。
    """
    if getattr(sys, 'frozen', False):
        base = Path(sys.executable).parent   # .exe 旁邊
    else:
        base = Path(__file__).parent          # 腳本旁邊

    env_file = base / '.env'

    if debug:
        mode = '凍結(.exe)' if getattr(sys, 'frozen', False) else '開發'
        print(f"[load_env] 模式：{mode}")
        print(f"[load_env] 尋找：{env_file.resolve()}")

    if not env_file.exists():
        if debug:
            print(f"[load_env] ⚠ 找不到 .env，請確認檔案放在 {base.resolve()}")
        return {}

    loaded = {}
    for i, raw in enumerate(
        env_file.read_text(encoding='utf-8-sig').splitlines(), start=1
    ):
        line = raw.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, val = line.split('=', 1)
        key = key.strip()
        val = val.strip().strip('"').strip("'")   # 容許值加引號
        if not key:
            continue
        os.environ.setdefault(key, val)
        loaded[key] = val
        if debug:
            # 密碼只顯示前2字元 + ***
            display = val[:2] + '***' if 'PASS' in key.upper() else val
            print(f"[load_env]   第{i}行 {key} = {display}")

    if debug:
        print(f"[load_env] ✓ 共載入 {len(loaded)} 個變數")
    return loaded


def get_connection() -> pymysql.connections.Connection:
    """
    建立 MySQL 連線。
    DB_HOST 可填多個 IP，逗號分隔，依序嘗試，第一個成功即回傳。
    """
    hosts = [h.strip() for h in os.environ.get('DB_HOST', '').split(',') if h.strip()]
    port  = int(os.environ.get('DB_PORT', 3306))
    db    = os.environ.get('DB_NAME', '')
    user  = os.environ.get('DB_USER', '')
    pw    = os.environ.get('DB_PASSWORD', '')

    if not hosts:
        raise ValueError("[db] DB_HOST 未設定，請檢查 .env")
    if not db:
        raise ValueError("[db] DB_NAME 未設定，請檢查 .env")

    last_err = None
    for host in hosts:
        try:
            conn = pymysql.connect(
                host=host,
                port=port,
                database=db,
                user=user,
                password=pw,
                charset='utf8mb3',
                connect_timeout=5,
                autocommit=True,
                cursorclass=pymysql.cursors.DictCursor,   # 結果為 dict，後面讀欄位比較方便
            )
            print(f"[db] ✓ 連線成功：{host}:{port}/{db}")
            return conn
        except pymysql.Error as e:
            print(f"[db] ✗ {host} 失敗：{e}")
            last_err = e

    raise ConnectionError(f"[db] 所有主機均無法連線。最後錯誤：{last_err}")