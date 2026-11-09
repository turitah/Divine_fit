from flask import (
    Flask,
    Response,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.utils import secure_filename
from functools import wraps
from pathlib import Path
from urllib.parse import urlsplit

import hmac
import os
import secrets
import socket
import sqlite3

from qrgen import make_qr_svg


app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

# ---------------------------------------------------------
# APPLICATION SETTINGS
# ---------------------------------------------------------

# Password used to open the private seller dashboard.
# Override it with the DIVINE_FIT_ADMIN_PASSWORD environment variable.
DEFAULT_ADMIN_PASSWORD = "Divinefit2026"

app.config["ADMIN_PASSWORD"] = os.environ.get(
    "DIVINE_FIT_ADMIN_PASSWORD",
    DEFAULT_ADMIN_PASSWORD,
)

# Reject images larger than 8 MB.
app.config["MAX_CONTENT_LENGTH"] = 8 * 1024 * 1024


# ---------------------------------------------------------
# PATHS
# ---------------------------------------------------------

BASE_DIR = Path(__file__).resolve().parent

INSTANCE_DIR = BASE_DIR / "instance"
UPLOAD_DIR = BASE_DIR / "static" / "uploads"
DB_PATH = INSTANCE_DIR / "divine_fit.db"
SELLER_TOKEN_PATH = INSTANCE_DIR / "seller_access_token.txt"

INSTANCE_DIR.mkdir(exist_ok=True)
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

SECRET_KEY_PATH = INSTANCE_DIR / "secret_key.txt"


def load_secret_key():
    """Use the environment key, or create a private key for this shop."""

    configured = os.environ.get("DIVINE_FIT_SECRET_KEY")
    if configured:
        return configured

    if SECRET_KEY_PATH.exists():
        stored = SECRET_KEY_PATH.read_text(encoding="utf-8").strip()
        if stored:
            return stored

    generated = secrets.token_hex(32)
    SECRET_KEY_PATH.write_text(generated, encoding="utf-8")
    return generated


app.secret_key = load_secret_key()

app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
)

if (os.environ.get("DIVINE_FIT_SITE_URL") or "").startswith("https://"):
    app.config["SESSION_COOKIE_SECURE"] = True


# ---------------------------------------------------------
# SHOP CHOICES
# ---------------------------------------------------------

CATEGORIES = [
    "Casual",
    "Party",
    "Office",
    "Evening",
    "Church",
]

CATEGORY_CARDS = [
    ("Casual", "image-casual"),
    ("Party", "image-party"),
    ("Office", "image-office"),
    ("Evening", "image-evening"),
    ("Church", "image-church"),
]

CATEGORY_RENAMES = {
    "Casual Wear": "Casual",
    "Party Wear": "Party",
    "Office Wear": "Office",
    "Formal": "Office",
    "Formal Wear": "Office",
    "Evening Wear": "Evening",
    "Wedding": "Church",
    "Wedding Wear": "Church",
    "Church Wear": "Church",
}

SIZES = ["XS", "S", "M", "L", "XL", "XXL"]

STATUSES = ["Available", "Few Left", "Sold Out"]

ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "webp"}

# Both numbers are on WhatsApp and can be used for calls.
DEFAULT_CONTACTS = [
    {"wa": "256752127181", "display": "+256 752 127181"},
    {"wa": "256788398965", "display": "+256 788 398965"},
]


# ---------------------------------------------------------
# DATABASE
# ---------------------------------------------------------

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()

    conn.execute("""
        CREATE TABLE IF NOT EXISTS products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            price INTEGER NOT NULL,
            category TEXT NOT NULL,
            colour TEXT,
            sizes TEXT NOT NULL,
            status TEXT NOT NULL,
            description TEXT,
            image TEXT NOT NULL,
            featured INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    for old_name, new_name in CATEGORY_RENAMES.items():
        conn.execute(
            "UPDATE products SET category = ? WHERE category = ?",
            (new_name, old_name),
        )

    conn.commit()
    conn.close()


# ---------------------------------------------------------
# HELPERS
# ---------------------------------------------------------

def allowed_file(filename):
    return (
        "." in filename
        and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS
    )


def unique_image_name(filename):
    """Return a safe filename that does not already exist in uploads/."""

    filename = secure_filename(filename)
    stem = Path(filename).stem or "dress"
    suffix = Path(filename).suffix.lower()

    final_name = f"{stem}{suffix}"
    counter = 1

    while (UPLOAD_DIR / final_name).exists():
        final_name = f"{stem}-{counter}{suffix}"
        counter += 1

    return final_name


def delete_uploaded_image(image):
    if image and image.startswith("uploads/"):
        file_path = BASE_DIR / "static" / image
        if file_path.exists():
            file_path.unlink()


def read_product_form():
    return {
        "name": request.form.get("name", "").strip(),
        "price": request.form.get("price", "").strip(),
        "category": request.form.get("category", "").strip(),
        "colour": request.form.get("colour", "").strip(),
        "sizes": ",".join(request.form.getlist("sizes")),
        "status": request.form.get("status", "Available").strip(),
        "description": request.form.get("description", "").strip(),
        "featured": 1 if request.form.get("featured") else 0,
    }


def validate_product(fields, image_file, require_image):
    errors = []

    if not fields["name"]:
        errors.append("Please enter the dress name.")

    if not fields["price"]:
        errors.append("Please enter the dress price.")
    else:
        try:
            price = int(fields["price"])
            if price < 0:
                raise ValueError
            fields["price"] = price
        except ValueError:
            errors.append("Please enter a valid price.")

    if fields["category"] not in CATEGORIES:
        errors.append("Please choose a valid category.")

    chosen_sizes = [s for s in fields["sizes"].split(",") if s]
    if not chosen_sizes:
        errors.append("Please select at least one size.")
    elif any(size not in SIZES for size in chosen_sizes):
        errors.append("One of the selected sizes is not valid.")

    if fields["status"] not in STATUSES:
        fields["status"] = "Available"

    if require_image:
        if not image_file or image_file.filename == "":
            errors.append("Please choose a dress image.")
        elif not allowed_file(image_file.filename):
            errors.append("Use PNG, JPG, JPEG or WEBP images.")
    elif image_file and image_file.filename:
        if not allowed_file(image_file.filename):
            errors.append("Use PNG, JPG, JPEG or WEBP images.")

    return errors


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("is_admin"):
            flash("Please sign in to manage dresses.", "error")
            return redirect(url_for("login", next=request.path))
        return view(*args, **kwargs)

    return wrapped


def local_network_ip():
    """Return the computer's private Wi-Fi/LAN address when available."""

    try:
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            probe.connect(("8.8.8.8", 80))
            address = probe.getsockname()[0]
        finally:
            probe.close()

        if address and not address.startswith(("127.", "169.254.")):
            return address
    except OSError:
        pass

    try:
        for address in socket.gethostbyname_ex(socket.gethostname())[2]:
            if not address.startswith(("127.", "169.254.")):
                return address
    except OSError:
        pass

    return None


def is_private_host(hostname):
    if hostname.startswith(("10.", "192.168.")):
        return True

    if hostname.startswith("172."):
        try:
            second = int(hostname.split(".")[1])
        except (IndexError, ValueError):
            return False
        return 16 <= second <= 31

    return False


def site_url():
    """Return a public catalogue URL, using the LAN address when local."""

    configured = os.environ.get("DIVINE_FIT_SITE_URL", "").strip()
    if configured:
        return configured.rstrip("/") + "/"

    parts = urlsplit(request.host_url)
    hostname = (parts.hostname or "").lower()

    if hostname in {"localhost", "127.0.0.1", "0.0.0.0", "::1"}:
        address = local_network_ip()
        if address:
            port = f":{parts.port}" if parts.port else ""
            return f"{parts.scheme}://{address}{port}/"

    return request.host_url


def build_contacts():
    """Return both shop contacts, with optional environment overrides."""

    contacts = []
    overrides = [
        ("DIVINE_FIT_WHATSAPP_1", "DIVINE_FIT_PHONE_1"),
        ("DIVINE_FIT_WHATSAPP_2", "DIVINE_FIT_PHONE_2"),
    ]

    for index, default in enumerate(DEFAULT_CONTACTS):
        wa_env, phone_env = overrides[index]

        wa = os.environ.get(wa_env)
        if index == 0 and not wa:
            wa = os.environ.get("DIVINE_FIT_WHATSAPP")

        wa = "".join(
            character
            for character in (wa or default["wa"])
            if character.isdigit()
        )

        display = os.environ.get(phone_env) or default["display"]

        contacts.append({
            "wa": wa,
            "display": display,
            "tel": f"+{wa}" if wa else display,
        })

    return contacts


def rotate_seller_token():
    """Create a fresh QR access token and store it privately."""

    token = secrets.token_urlsafe(32)
    SELLER_TOKEN_PATH.write_text(token, encoding="utf-8")
    return token


def get_seller_token():
    """Return the current QR access token, creating one if needed."""

    token = ""

    if SELLER_TOKEN_PATH.exists():
        token = SELLER_TOKEN_PATH.read_text(encoding="utf-8").strip()

    if not token:
        return rotate_seller_token()

    return token


def seller_access_url():
    base = site_url().rstrip("/")
    path = url_for("manage_scan", token=get_seller_token())
    return f"{base}{path}"


# ---------------------------------------------------------
# TEMPLATE FILTERS / GLOBALS
# ---------------------------------------------------------

@app.template_filter("money")
def money(value):
    return f"UGX {int(value):,}"


@app.template_filter("whatsapp_text")
def whatsapp_text(value):
    from urllib.parse import quote

    return quote(value, safe="")


@app.context_processor
def shop_settings():
    contacts = build_contacts()

    return {
        "shop_name": "Divine Fit",
        "contacts": contacts,
        "using_default_password": (
            app.config["ADMIN_PASSWORD"] == DEFAULT_ADMIN_PASSWORD
        ),
        "location": os.environ.get(
            "DIVINE_FIT_LOCATION",
            "Kampala, Uganda",
        ),
        "categories": CATEGORIES,
        "category_cards": CATEGORY_CARDS,
        "sizes": SIZES,
        "statuses": STATUSES,
    }


# ---------------------------------------------------------
# PUBLIC PAGES
# ---------------------------------------------------------

@app.route("/")
def home():
    conn = get_db()

    featured = conn.execute("""
        SELECT *,
               (julianday('now') - julianday(created_at) <= 14) AS is_new
        FROM products
        WHERE featured = 1
        ORDER BY id DESC
        LIMIT 4
    """).fetchall()

    latest = conn.execute("""
        SELECT *,
               (julianday('now') - julianday(created_at) <= 14) AS is_new
        FROM products
        WHERE featured = 0
        ORDER BY id DESC
        LIMIT 8
    """).fetchall()

    conn.close()

    return render_template(
        "home.html",
        featured=featured,
        latest=latest,
    )


@app.route("/shop")
def shop():
    search = request.args.get("search", "").strip()
    category = request.args.get("category", "").strip()
    size = request.args.get("size", "").strip()
    status = request.args.get("status", "").strip()
    min_price = request.args.get("min_price", "").strip()
    max_price = request.args.get("max_price", "").strip()

    query = """
        SELECT *,
               (julianday('now') - julianday(created_at) <= 14) AS is_new
        FROM products
        WHERE 1=1
    """
    params = []

    if search:
        query += """
            AND (
                name LIKE ?
                OR category LIKE ?
                OR colour LIKE ?
            )
        """
        term = f"%{search}%"
        params.extend([term, term, term])

    if category:
        query += " AND category = ?"
        params.append(category)

    if size:
        query += " AND (',' || sizes || ',') LIKE ?"
        params.append(f"%,{size},%")

    if status:
        query += " AND status = ?"
        params.append(status)

    if min_price:
        try:
            params.append(int(min_price))
            query += " AND price >= ?"
        except ValueError:
            min_price = ""

    if max_price:
        try:
            params.append(int(max_price))
            query += " AND price <= ?"
        except ValueError:
            max_price = ""

    query += " ORDER BY id DESC"

    conn = get_db()

    products = conn.execute(query, params).fetchall()

    total_products = conn.execute(
        "SELECT COUNT(*) FROM products"
    ).fetchone()[0]

    conn.close()

    return render_template(
        "shop.html",
        products=products,
        categories=CATEGORIES,
        total_products=total_products,
        search=search,
        selected_category=category,
        selected_size=size,
        selected_status=status,
        min_price=min_price,
        max_price=max_price,
    )


@app.route("/about")
def about():
    return render_template("about.html")


@app.route("/contact")
def contact():
    return render_template("contact.html")


@app.route("/product/<int:product_id>")
def product(product_id):
    conn = get_db()

    item = conn.execute("""
        SELECT *,
               (julianday('now') - julianday(created_at) <= 14) AS is_new
        FROM products
        WHERE id = ?
    """, (product_id,)).fetchone()

    if item is None:
        conn.close()
        return render_template("not_found.html"), 404

    related = conn.execute("""
        SELECT *,
               (julianday('now') - julianday(created_at) <= 14) AS is_new
        FROM products
        WHERE category = ?
          AND id != ?
        ORDER BY id DESC
        LIMIT 4
    """, (item["category"], product_id)).fetchall()

    conn.close()

    return render_template(
        "product.html",
        product=item,
        related=related,
    )


@app.route("/qr")
def qr_code():
    target = site_url()
    target_host = urlsplit(target).hostname or ""

    try:
        svg = make_qr_svg(target)
        error = None
    except ValueError:
        svg = None
        error = "The catalogue address is too long to encode in a QR code."

    return render_template(
        "qr.html",
        qr_svg=svg,
        target=target,
        lan_target=is_private_host(target_host),
        error=error,
    )


@app.route("/qr/download")
def qr_download():
    target = site_url()
    svg = make_qr_svg(target)

    return Response(
        svg,
        mimetype="image/svg+xml",
        headers={
            "Content-Disposition":
                'attachment; filename="divine-fit-qr.svg"',
        },
    )


# =========================================================
# PRIVATE SELLER DASHBOARD
# =========================================================

@app.route("/manage/login", methods=["GET", "POST"])
def login():
    if session.get("is_admin"):
        return redirect(url_for("manage"))

    next_url = request.values.get("next", "")

    if request.method == "POST":
        password = request.form.get("password", "")

        if hmac.compare_digest(
            password,
            app.config["ADMIN_PASSWORD"],
        ):
            session["is_admin"] = True
            flash("Welcome back.", "success")

            if next_url.startswith("/") and not next_url.startswith("//"):
                return redirect(next_url)

            return redirect(url_for("manage"))

        flash("Incorrect password. Please try again.", "error")

    return render_template("login.html", next_url=next_url)


@app.route("/manage/logout", methods=["POST"])
def logout():
    session.pop("is_admin", None)
    flash("You have been signed out.", "success")
    return redirect(url_for("home"))


@app.route("/manage/scan/<token>")
def manage_scan(token):
    """Sign the seller in when their private QR code is scanned."""

    if hmac.compare_digest(token, get_seller_token()):
        session["is_admin"] = True
        flash("Signed in with your seller QR code.", "success")
        return redirect(url_for("manage"))

    flash(
        "That seller QR code is not valid or has been replaced.",
        "error",
    )
    return redirect(url_for("login"))


@app.route("/manage/access")
@login_required
def manage_access():
    target = seller_access_url()
    target_host = urlsplit(target).hostname or ""

    try:
        svg = make_qr_svg(target)
        error = None
    except ValueError:
        svg = None
        error = "The access address is too long to encode in a QR code."

    return render_template(
        "manage_access.html",
        qr_svg=svg,
        target=target,
        lan_target=is_private_host(target_host),
        error=error,
    )


@app.route("/manage/access/rotate", methods=["POST"])
@login_required
def rotate_access():
    rotate_seller_token()

    flash(
        "A new seller QR code was generated. The old code no longer works.",
        "success",
    )

    return redirect(url_for("manage_access"))


@app.route("/manage/access/download")
@login_required
def manage_access_download():
    svg = make_qr_svg(seller_access_url())

    return Response(
        svg,
        mimetype="image/svg+xml",
        headers={
            "Content-Disposition":
                'attachment; filename="divine-fit-seller-access-qr.svg"',
        },
    )


@app.route("/manage")
@login_required
def manage():
    conn = get_db()

    products = conn.execute("""
        SELECT *
        FROM products
        ORDER BY id DESC
    """).fetchall()

    conn.close()

    stats = {
        "total": len(products),
        "available": sum(
            1 for product in products
            if product["status"] == "Available"
        ),
        "few": sum(
            1 for product in products
            if product["status"] == "Few Left"
        ),
        "sold": sum(
            1 for product in products
            if product["status"] == "Sold Out"
        ),
    }

    category_counts = {name: 0 for name in CATEGORIES}
    for product in products:
        category_counts[product["category"]] = (
            category_counts.get(product["category"], 0) + 1
        )

    return render_template(
        "manage.html",
        products=products,
        stats=stats,
        category_counts=category_counts,
    )


@app.route("/manage/add", methods=["POST"])
@login_required
def add_product():
    fields = read_product_form()
    image_file = request.files.get("image")

    errors = validate_product(fields, image_file, require_image=True)

    if errors:
        for message in errors:
            flash(message, "error")
        return redirect(url_for("manage"))

    final_name = unique_image_name(image_file.filename)
    image_file.save(UPLOAD_DIR / final_name)

    conn = get_db()

    conn.execute("""
        INSERT INTO products
        (
            name,
            price,
            category,
            colour,
            sizes,
            status,
            description,
            image,
            featured
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        fields["name"],
        fields["price"],
        fields["category"],
        fields["colour"],
        fields["sizes"],
        fields["status"],
        fields["description"],
        f"uploads/{final_name}",
        fields["featured"],
    ))

    conn.commit()
    conn.close()

    flash(f"{fields['name']} was added successfully.", "success")

    return redirect(url_for("manage"))


@app.route("/manage/edit/<int:product_id>", methods=["GET", "POST"])
@login_required
def edit_product(product_id):
    conn = get_db()

    item = conn.execute("""
        SELECT *
        FROM products
        WHERE id = ?
    """, (product_id,)).fetchone()

    if item is None:
        conn.close()
        return render_template("not_found.html"), 404

    if request.method == "POST":
        fields = read_product_form()
        image_file = request.files.get("image")

        errors = validate_product(fields, image_file, require_image=False)

        if errors:
            for message in errors:
                flash(message, "error")
            conn.close()
            return redirect(url_for("edit_product", product_id=product_id))

        old_image = item["image"]
        new_image = old_image

        if image_file and image_file.filename:
            final_name = unique_image_name(image_file.filename)
            image_file.save(UPLOAD_DIR / final_name)
            new_image = f"uploads/{final_name}"

        conn.execute("""
            UPDATE products
            SET name = ?,
                price = ?,
                category = ?,
                colour = ?,
                sizes = ?,
                status = ?,
                description = ?,
                image = ?,
                featured = ?
            WHERE id = ?
        """, (
            fields["name"],
            fields["price"],
            fields["category"],
            fields["colour"],
            fields["sizes"],
            fields["status"],
            fields["description"],
            new_image,
            fields["featured"],
            product_id,
        ))

        conn.commit()
        conn.close()

        if new_image != old_image:
            delete_uploaded_image(old_image)

        flash(f"{fields['name']} was updated successfully.", "success")

        return redirect(url_for("manage"))

    conn.close()

    return render_template("edit_product.html", product=item)


@app.route("/manage/delete/<int:product_id>", methods=["POST"])
@login_required
def delete_product(product_id):
    conn = get_db()

    item = conn.execute("""
        SELECT image
        FROM products
        WHERE id = ?
    """, (product_id,)).fetchone()

    if item:
        delete_uploaded_image(item["image"])

        conn.execute("""
            DELETE FROM products
            WHERE id = ?
        """, (product_id,))

        conn.commit()

    conn.close()

    flash("Dress deleted successfully.", "success")

    return redirect(url_for("manage"))


@app.errorhandler(413)
def file_too_large(error):
    flash(
        "That image is too large. Please use an image under 8 MB.",
        "error",
    )
    return redirect(url_for("manage")), 303


with app.app_context():
    init_db()


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.environ.get("PORT", "5000")),
        debug=os.environ.get("FLASK_DEBUG", "0") == "1",
    )
