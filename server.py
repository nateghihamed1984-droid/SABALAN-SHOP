```python
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from datetime import datetime
from urllib.parse import urlsplit, unquote
import json
import mimetypes
import os
import secrets
import sqlite3

HOST = "127.0.0.1"
PORT = int(os.getenv("SABALAN_PORT", "8000"))

BASE_DIR = Path(__file__).resolve().parent
SITE_DIR = (BASE_DIR / "site").resolve()
DB_PATH = Path(
    os.getenv("SABALAN_ORDERS_DB", str(BASE_DIR / "orders.db"))
).resolve()

API_KEY = os.getenv("WEBSITE_API_KEY", "").strip()

# Files explicitly permitted from the project root.
ROOT_PUBLIC_FILES = {
    "order.html",
    "products.html",
    "landing.html",
    "api-config.js",
}

ALLOWED_SITE_EXTENSIONS = {
    ".html", ".css", ".js",
    ".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg", ".ico",
    ".woff", ".woff2",
}


def get_conn():
    conn = sqlite3.connect(str(DB_PATH), timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with get_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS orders (
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
            )
        """)

        columns = [
            row[1]
            for row in conn.execute(
                "PRAGMA table_info(orders)"
            ).fetchall()
        ]

        if "customer_ref" not in columns:
            conn.execute(
                "ALTER TABLE orders ADD COLUMN customer_ref TEXT"
            )


def json_bytes(data):
    return json.dumps(data, ensure_ascii=False).encode("utf-8")


def is_inside(path, parent):
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


class Handler(BaseHTTPRequestHandler):

    def _headers(
        self,
        status=200,
        content_type="application/json; charset=utf-8",
        content_length=None,
    ):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header(
            "Access-Control-Allow-Methods",
            "GET, POST, OPTIONS",
        )
        self.send_header(
            "Access-Control-Allow-Headers",
            "Content-Type, X-API-Key",
        )
        self.send_header("Cache-Control", "no-store")

        if content_length is not None:
            self.send_header("Content-Length", str(content_length))

        self.end_headers()

    def _reply(self, status, payload):
        body = json_bytes(payload)
        self._headers(
            status,
            "application/json; charset=utf-8",
            len(body),
        )
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        # Avoid printing customer phone numbers or full addresses.
        print("HTTP:", fmt % args)

    def do_OPTIONS(self):
        self._headers(204, content_length=0)

    def _serve_static(self, request_path):
        try:
            relative = unquote(request_path).lstrip("/")

            if not relative:
                relative = "index.html"

            requested = Path(relative)

            # Prevent traversal and access to private backup folders.
            if (
                requested.is_absolute()
                or "\\" in relative
                or ".." in requested.parts
                or "back" in {
                    part.lower() for part in requested.parts
                }
            ):
                return self._reply(
                    404,
                    {
                        "success": False,
                        "message": "فایل پیدا نشد.",
                    },
                )

            site_candidate = (SITE_DIR / requested).resolve()

            # Website pages, CSS, JavaScript and images in site/.
            if (
                is_inside(site_candidate, SITE_DIR)
                and site_candidate.is_file()
                and site_candidate.suffix.lower()
                in ALLOWED_SITE_EXTENSIONS
            ):
                file_path = site_candidate

            # Only explicitly approved files may be served from root.
            elif (
                len(requested.parts) == 1
                and requested.name in ROOT_PUBLIC_FILES
            ):
                file_path = (BASE_DIR / requested.name).resolve()

                if not file_path.is_file():
                    return self._reply(
                        404,
                        {
                            "success": False,
                            "message": "فایل پیدا نشد.",
                        },
                    )
            else:
                return self._reply(
                    404,
                    {
                        "success": False,
                        "message": "فایل پیدا نشد.",
                    },
                )

            content = file_path.read_bytes()
            content_type, _ = mimetypes.guess_type(str(file_path))

            if not content_type:
                content_type = "application/octet-stream"

            if (
                content_type.startswith(
                    ("text/", "application/javascript")
                )
                or content_type in {
                    "application/json",
                    "image/svg+xml",
                }
            ):
                content_type += "; charset=utf-8"

            self._headers(200, content_type, len(content))
            self.wfile.write(content)

        except (OSError, ValueError):
            self._reply(
                500,
                {
                    "success": False,
                    "message": "خطا در خواندن فایل.",
                },
            )

    def do_GET(self):
        path = urlsplit(self.path).path

        if path == "/api/orders":
            if (
                not API_KEY
                or not secrets.compare_digest(
                    self.headers.get("X-API-Key", ""),
                    API_KEY,
                )
            ):
                return self._reply(
                    403,
                    {
                        "success": False,
                        "message": "دسترسی مجاز نیست.",
                    },
                )

            with get_conn() as conn:
                rows = conn.execute("""
                    SELECT *
                    FROM orders
                    ORDER BY id DESC
                    LIMIT 500
                """).fetchall()

            return self._reply(
                200,
                {
                    "success": True,
                    "orders": [dict(row) for row in rows],
                },
            )

        return self._serve_static(path)

    def do_POST(self):
        path = urlsplit(self.path).path

        if path != "/api/orders":
            return self._reply(
                404,
                {
                    "success": False,
                    "message": "مسیر پیدا نشد.",
                },
            )

        try:
            length = int(self.headers.get("Content-Length", "0"))

            if length <= 0 or length > 20000:
                return self._reply(
                    400,
                    {
                        "success": False,
                        "message": "اطلاعات سفارش معتبر نیست.",
                    },
                )

            raw_data = self.rfile.read(length).decode("utf-8")
            data = json.loads(raw_data)

            if not isinstance(data, dict):
                return self._reply(
                    400,
                    {
                        "success": False,
                        "message": "اطلاعات سفارش معتبر نیست.",
                    },
                )

            required = [
                "product",
                "quantity",
                "fullname",
                "phone",
                "province",
                "city",
                "address",
            ]

            if any(
                not str(data.get(key, "")).strip()
                for key in required
            ):
                return self._reply(
                    400,
                    {
                        "success": False,
                        "message": "لطفاً همه اطلاعات سفارش را کامل کنید.",
                    },
                )

            quantity = int(data.get("quantity", 1))

            if quantity < 1 or quantity > 99:
                return self._reply(
                    400,
                    {
                        "success": False,
                        "message": "تعداد باید بین ۱ تا ۹۹ باشد.",
                    },
                )

            values = [
                str(data.get(key, "")).strip()
                for key in [
                    "product",
                    "fullname",
                    "phone",
                    "province",
                    "city",
                    "address",
                ]
            ]

            product, fullname, phone, province, city, address = values
            customer_ref = str(
                data.get("customer_ref", "")
            ).strip()

            if (
                len(product) > 200
                or len(phone) > 40
                or len(fullname) > 120
                or len(province) > 100
                or len(city) > 100
                or len(address) > 1500
                or len(customer_ref) > 300
            ):
                return self._reply(
                    400,
                    {
                        "success": False,
                        "message": "یکی از اطلاعات واردشده بیش از حد طولانی است.",
                    },
                )

            created_at = datetime.now().strftime(
                "%Y-%m-%d %H:%M:%S"
            )

            with get_conn() as conn:
                cursor = conn.execute("""
                    INSERT INTO orders (
                        product_code,
                        quantity,
                        fullname,
                        phone,
                        province,
                        city,
                        address,
                        customer_ref,
                        status,
                        created_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    product,
                    quantity,
                    fullname,
                    phone,
                    province,
                    city,
                    address,
                    customer_ref,
                    "جدید",
                    created_at,
                ))

                order_id = cursor.lastrowid

            return self._reply(
                201,
                {
                    "success": True,
                    "message": "سفارش با موفقیت ثبت شد.",
                    "order_id": order_id,
                    "status": "جدید",
                },
            )

        except (
            ValueError,
            json.JSONDecodeError,
            UnicodeDecodeError,
        ):
            return self._reply(
                400,
                {
                    "success": False,
                    "message": "اطلاعات ارسالی معتبر نیست.",
                },
            )

        except Exception as exc:
            print("ORDER SAVE ERROR:", type(exc).__name__)
            return self._reply(
                500,
                {
                    "success": False,
                    "message": "خطای داخلی در ثبت سفارش رخ داد.",
                },
            )


if __name__ == "__main__":
    if not API_KEY:
        raise SystemExit(
            "WEBSITE_API_KEY تنظیم نشده است؛ "
            "ابتدا کلید مشترک سرور و ربات را تنظیم کنید."
        )

    init_db()

    print("SERVER STARTING...")
    print(f"Website: http://{HOST}:{PORT}")
    print(f"Order form: http://{HOST}:{PORT}/order.html")
    print("Order API is running.")
    print("Private order retrieval requires X-API-Key.")

    HTTPServer((HOST, PORT), Handler).serve_forever()
```
