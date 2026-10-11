
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from datetime import datetime
from urllib.parse import urlsplit, unquote
import json
import mimetypes
import os
import secrets
import sqlite3

# ============================================================
# SABALAN SHOP - LOCAL WEBSITE AND ORDER API
# ============================================================

HOST = "127.0.0.1"
PORT = int(os.getenv("SABALAN_PORT", "8000"))

BASE_DIR = Path(__file__).resolve().parent
SITE_DIR = (BASE_DIR / "site").resolve()
DB_PATH = Path(
    os.getenv("SABALAN_ORDERS_DB", str(BASE_DIR / "orders.db"))
).resolve()

API_KEY = os.getenv("WEBSITE_API_KEY", "").strip()

# Only these individual files may be served from the project root.
ROOT_PUBLIC_FILES = {
    "order.html",
    "products.html",
    "landing.html",
    "api-config.js",
}

ALLOWED_EXTENSIONS = {
    ".html",
    ".css",
    ".js",
    ".png",
    ".jpg",
    ".jpeg",
    ".webp",
    ".gif",
    ".svg",
    ".ico",
    ".woff",
    ".woff2",
}


# ============================================================
# DATABASE
# ============================================================

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

        columns = {
            row[1]
            for row in conn.execute(
                "PRAGMA table_info(orders)"
            ).fetchall()
        }

        if "customer_ref" not in columns:
            conn.execute(
                "ALTER TABLE orders ADD COLUMN customer_ref TEXT"
            )


# ============================================================
# RESPONSE HELPERS
# ============================================================

def json_bytes(data):
    return json.dumps(
        data,
        ensure_ascii=False
    ).encode("utf-8")


def is_inside(path, directory):
    try:
        path.resolve().relative_to(directory.resolve())
        return True
    except (ValueError, OSError):
        return False


# ============================================================
# HTTP HANDLER
# ============================================================

class Handler(BaseHTTPRequestHandler):

    def log_message(self, fmt, *args):
        # Do not print customer phone numbers or addresses.
        print("HTTP:", fmt % args)

    def send_json(self, status, payload):
        body = json_bytes(payload)

        self.send_response(status)
        self.send_header(
            "Content-Type",
            "application/json; charset=utf-8"
        )
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header(
            "Access-Control-Allow-Methods",
            "GET, POST, OPTIONS"
        )
        self.send_header(
            "Access-Control-Allow-Headers",
            "Content-Type, X-API-Key"
        )
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_file(self, file_path):
        try:
            file_path = file_path.resolve()

            if not file_path.is_file():
                return self.send_json(
                    404,
                    {
                        "success": False,
                        "message": "فایل پیدا نشد."
                    }
                )

            content = file_path.read_bytes()
            content_type, _ = mimetypes.guess_type(
                str(file_path)
            )

            if not content_type:
                content_type = "application/octet-stream"

            if file_path.suffix.lower() in {
                ".html", ".css", ".js", ".svg"
            }:
                content_type += "; charset=utf-8"

            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header(
                "Content-Length",
                str(len(content))
            )
            self.send_header(
                "Cache-Control",
                "no-cache"
            )
            self.end_headers()
            self.wfile.write(content)

        except OSError:
            self.send_json(
                500,
                {
                    "success": False,
                    "message": "خواندن فایل با خطا مواجه شد."
                }
            )

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header(
            "Access-Control-Allow-Methods",
            "GET, POST, OPTIONS"
        )
        self.send_header(
            "Access-Control-Allow-Headers",
            "Content-Type, X-API-Key"
        )
        self.send_header("Content-Length", "0")
        self.end_headers()

    # ========================================================
    # GET: WEBSITE FILES AND ORDER API
    # ========================================================

    def do_GET(self):
        parsed = urlsplit(self.path)
        request_path = unquote(parsed.path)

        # The bot uses this endpoint to read orders.
        if request_path == "/api/orders":
            supplied_key = self.headers.get(
                "X-API-Key", ""
            )

            if (
                not API_KEY
                or not secrets.compare_digest(
                    supplied_key, API_KEY
                )
            ):
                return self.send_json(
                    403,
                    {
                        "success": False,
                        "message": "دسترسی مجاز نیست."
                    }
                )

            try:
                with get_conn() as conn:
                    rows = conn.execute(
                        """
                        SELECT *
                        FROM orders
                        ORDER BY id DESC
                        LIMIT 500
                        """
                    ).fetchall()

                return self.send_json(
                    200,
                    {
                        "success": True,
                        "orders": [
                            dict(row) for row in rows
                        ]
                    }
                )

            except Exception as exc:
                print(
                    "ORDER READ ERROR:",
                    type(exc).__name__
                )
                return self.send_json(
                    500,
                    {
                        "success": False,
                        "message": "خواندن سفارش‌ها ناموفق بود."
                    }
                )

        # Main website page.
        if request_path == "/":
            target = SITE_DIR / "index.html"
            return self.send_file(target)

        # Serve approved HTML and configuration files
        # located beside server.py.
        first_part = request_path.lstrip("/").split("/")[0]

        if (
            "/" not in request_path.lstrip("/")
            and first_part in ROOT_PUBLIC_FILES
        ):
            target = (BASE_DIR / first_part).resolve()

            if not is_inside(target, BASE_DIR):
                return self.send_json(
                    403,
                    {
                        "success": False,
                        "message": "دسترسی مجاز نیست."
                    }
                )

            return self.send_file(target)

        # Serve website assets from the site directory.
        relative_path = request_path.lstrip("/")

        if not relative_path:
            relative_path = "index.html"

        if Path(relative_path).suffix.lower() not in ALLOWED_EXTENSIONS:
            return self.send_json(
                404,
                {
                    "success": False,
                    "message": "مسیر پیدا نشد."
                }
            )

        target = (SITE_DIR / relative_path).resolve()

        # Prevent access outside the website directory.
        if not is_inside(target, SITE_DIR):
            return self.send_json(
                403,
                {
                    "success": False,
                    "message": "دسترسی مجاز نیست."
                }
            )

        # Support assets stored in the project-root static folder.
        # Other root folders and files remain inaccessible.
        if not target.is_file() and relative_path.startswith(
            "static/"
        ):
            root_static = (BASE_DIR / relative_path).resolve()

            if (
                is_inside(root_static, BASE_DIR)
                and root_static.is_file()
            ):
                target = root_static

        return self.send_file(target)

    # ========================================================
    # POST: SAVE A NEW ORDER
    # ========================================================

    def do_POST(self):
        request_path = urlsplit(self.path).path

        if request_path != "/api/orders":
            return self.send_json(
                404,
                {
                    "success": False,
                    "message": "مسیر پیدا نشد."
                }
            )

        try:
            length = int(
                self.headers.get("Content-Length", "0")
            )

            if length <= 0 or length > 20000:
                return self.send_json(
                    400,
                    {
                        "success": False,
                        "message": "اطلاعات سفارش معتبر نیست."
                    }
                )

            raw_data = self.rfile.read(length)
            data = json.loads(raw_data.decode("utf-8"))

            if not isinstance(data, dict):
                return self.send_json(
                    400,
                    {
                        "success": False,
                        "message": "اطلاعات سفارش معتبر نیست."
                    }
                )

            required_fields = [
                "product",
                "quantity",
                "fullname",
                "phone",
                "province",
                "city",
                "address",
            ]

            for field in required_fields:
                if not str(data.get(field, "")).strip():
                    return self.send_json(
                        400,
                        {
                            "success": False,
                            "message": (
                                "لطفاً همه اطلاعات سفارش "
                                "را کامل کنید."
                            )
                        }
                    )

            quantity = int(data.get("quantity", 1))

            if quantity < 1 or quantity > 99:
                return self.send_json(
                    400,
                    {
                        "success": False,
                        "message": (
                            "تعداد باید بین ۱ تا ۹۹ باشد."
                        )
                    }
                )

            product = str(data["product"]).strip()
            fullname = str(data["fullname"]).strip()
            phone = str(data["phone"]).strip()
            province = str(data["province"]).strip()
            city = str(data["city"]).strip()
            address = str(data["address"]).strip()
            customer_ref = str(
                data.get("customer_ref", "")
            ).strip()

            if (
                len(product) > 200
                or len(fullname) > 120
                or len(phone) > 40
                or len(province) > 100
                or len(city) > 100
                or len(address) > 1500
                or len(customer_ref) > 300
            ):
                return self.send_json(
                    400,
                    {
                        "success": False,
                        "message": (
                            "یکی از اطلاعات واردشده "
                            "بیش از حد طولانی است."
                        )
                    }
                )

            created_at = datetime.now().strftime(
                "%Y-%m-%d %H:%M:%S"
            )

            with get_conn() as conn:
                cursor = conn.execute(
                    """
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
                    """,
                    (
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
                    )
                )

                order_id = cursor.lastrowid

            return self.send_json(
                201,
                {
                    "success": True,
                    "message": "سفارش با موفقیت ثبت شد.",
                    "order_id": order_id,
                    "status": "جدید",
                }
            )

        except (
            ValueError,
            json.JSONDecodeError,
            UnicodeDecodeError,
        ):
            return self.send_json(
                400,
                {
                    "success": False,
                    "message": "اطلاعات ارسالی معتبر نیست."
                }
            )

        except Exception as exc:
            print(
                "ORDER SAVE ERROR:",
                type(exc).__name__
            )
            return self.send_json(
                500,
                {
                    "success": False,
                    "message": (
                        "خطای داخلی در ثبت سفارش رخ داد."
                    )
                }
            )


# ============================================================
# START SERVER
# ============================================================

if __name__ == "__main__":
    if not API_KEY:
        raise SystemExit(
            "WEBSITE_API_KEY تنظیم نشده است؛ "
            "ابتدا همان کلید مشترک آزمایشی را در این پنجره "
            "تنظیم کنید."
        )

    if not SITE_DIR.is_dir():
        raise SystemExit(
            f"پوشه سایت پیدا نشد: {SITE_DIR}"
        )

    init_db()

    print("SERVER STARTING...")
    print(f"Website: http://{HOST}:{PORT}/")
    print(f"Order form: http://{HOST}:{PORT}/order.html")
    print(f"API: http://{HOST}:{PORT}/api/orders")
    print("Press Ctrl+C to stop the server.")

    server = ThreadingHTTPServer(
        (HOST, PORT),
        Handler
    )

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nServer stopped.")
    finally:
        server.server_close()
