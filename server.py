from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from datetime import datetime
import json
import os
import secrets
import sqlite3

HOST = "127.0.0.1"  # Keep local; expose through an HTTPS tunnel when ready.
PORT = int(os.getenv("SABALAN_PORT", "8000"))
BASE_DIR = Path(__file__).resolve().parent
DB_PATH = Path(os.getenv("SABALAN_ORDERS_DB", str(BASE_DIR / "orders.db")))
API_KEY = os.getenv("WEBSITE_API_KEY", "").strip()


def get_conn():
    conn = sqlite3.connect(str(DB_PATH), timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with get_conn() as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS orders(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_code TEXT NOT NULL,
            quantity INTEGER NOT NULL DEFAULT 1,
            fullname TEXT NOT NULL,
            phone TEXT NOT NULL,
            province TEXT NOT NULL,
            city TEXT NOT NULL,
            address TEXT NOT NULL,
            customer_ref TEXT,
            status TEXT NOT NULL DEFAULT 'جدید',
            created_at TEXT NOT NULL
        )""")
        cols = [r[1] for r in conn.execute("PRAGMA table_info(orders)").fetchall()]
        if "customer_ref" not in cols:
            conn.execute("ALTER TABLE orders ADD COLUMN customer_ref TEXT")


def json_bytes(data):
    return json.dumps(data, ensure_ascii=False).encode("utf-8")


class Handler(BaseHTTPRequestHandler):
    def _headers(self, status=200):
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-API-Key")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

    def _reply(self, status, payload):
        self._headers(status)
        self.wfile.write(json_bytes(payload))

    def log_message(self, fmt, *args):
        # Avoid logging customer phone numbers or full addresses.
        print("API:", fmt % args)

    def do_OPTIONS(self):
        self._headers(204)

    def do_GET(self):
        if self.path == "/":
            return self._reply(200, {"success": True, "message": "سرور سبلان شاپ فعال است."})
        if self.path != "/api/orders":
            return self._reply(404, {"success": False, "message": "مسیر پیدا نشد."})
        if not API_KEY or not secrets.compare_digest(self.headers.get("X-API-Key", ""), API_KEY):
            return self._reply(403, {"success": False, "message": "دسترسی مجاز نیست."})
        with get_conn() as conn:
            rows = conn.execute("SELECT * FROM orders ORDER BY id DESC LIMIT 500").fetchall()
        return self._reply(200, {"success": True, "orders": [dict(row) for row in rows]})

    def do_POST(self):
        if self.path != "/api/orders":
            return self._reply(404, {"success": False, "message": "مسیر پیدا نشد."})
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > 20000:
                return self._reply(400, {"success": False, "message": "اطلاعات سفارش معتبر نیست."})
            data = json.loads(self.rfile.read(length).decode("utf-8"))
            if not isinstance(data, dict):
                return self._reply(400, {"success": False, "message": "اطلاعات سفارش معتبر نیست."})
            required = ["product", "quantity", "fullname", "phone", "province", "city", "address"]
            if any(not str(data.get(k, "")).strip() for k in required):
                return self._reply(400, {"success": False, "message": "لطفاً همه اطلاعات سفارش را کامل کنید."})
            quantity = int(data.get("quantity", 1))
            if quantity < 1 or quantity > 99:
                return self._reply(400, {"success": False, "message": "تعداد باید بین ۱ تا ۹۹ باشد."})
            values = [str(data.get(k, "")).strip() for k in ["product", "fullname", "phone", "province", "city", "address"]]
            product, fullname, phone, province, city, address = values
            if len(phone) > 40 or len(fullname) > 120 or len(address) > 1500 or len(str(data.get("customer_ref", ""))) > 300:
                return self._reply(400, {"success": False, "message": "یکی از اطلاعات واردشده بیش از حد طولانی است."})
            created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            with get_conn() as conn:
                cur = conn.execute("""INSERT INTO orders
                    (product_code, quantity, fullname, phone, province, city, address, customer_ref, status, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (product, quantity, fullname, phone, province, city, address,
                     str(data.get("customer_ref", "")).strip(), "جدید", created_at))
                order_id = cur.lastrowid
            return self._reply(201, {"success": True, "message": "سفارش با موفقیت ثبت شد.", "order_id": order_id, "status": "جدید"})
        except (ValueError, json.JSONDecodeError, UnicodeDecodeError):
            return self._reply(400, {"success": False, "message": "اطلاعات ارسالی معتبر نیست."})
        except Exception as exc:
            print("ORDER SAVE ERROR:", type(exc).__name__)
            return self._reply(500, {"success": False, "message": "خطای داخلی در ثبت سفارش رخ داد."})


if __name__ == "__main__":
    if not API_KEY:
        raise SystemExit("WEBSITE_API_KEY تنظیم نشده است؛ برای حفاظت از اطلاعات سفارش‌ها ابتدا کلید مشترک را تنظیم کنید.")
    init_db()
    print("SERVER STARTING...")
    print(f"Local API: http://{HOST}:{PORT}")
    print("Customer data endpoint requires X-API-Key; public form can submit new orders.")
    HTTPServer((HOST, PORT), Handler).serve_forever()
