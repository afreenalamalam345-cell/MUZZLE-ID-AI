import os
import sqlite3
from datetime import datetime
from functools import wraps

from flask import Flask, request, jsonify, render_template, session
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)

app.secret_key = os.environ.get(
    "SECRET_KEY",
    "muzzle-id-ai-change-this-before-deployment"
)

DATABASE = os.environ.get("DATABASE_PATH", "muzzle_id.db")


# ---------------- DATABASE ----------------

def get_db():
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with get_db() as db:

        db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                email TEXT UNIQUE NOT NULL,
                password TEXT NOT NULL,
                created_at TEXT
            )
        """)

        db.execute("""
            CREATE TABLE IF NOT EXISTS cows (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                owner_id INTEGER,
                name TEXT,
                animal_type TEXT,
                breed TEXT,
                age TEXT,
                gender TEXT,
                weight REAL,
                country TEXT,
                photo TEXT,
                public_profile INTEGER DEFAULT 0,
                created_at TEXT
            )
        """)

        db.execute("""
            CREATE TABLE IF NOT EXISTS health_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                cow_id INTEGER,
                description TEXT,
                record_date TEXT
            )
        """)

        db.execute("""
            CREATE TABLE IF NOT EXISTS milk_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                cow_id INTEGER,
                quantity REAL,
                record_date TEXT
            )
        """)

        db.execute("""
            CREATE TABLE IF NOT EXISTS marketplace (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                cow_id INTEGER,
                owner_id INTEGER,
                price REAL,
                location TEXT,
                status TEXT DEFAULT 'available'
            )
        """)


init_db()


# ---------------- HELPERS ----------------

def response(data=None, message="Success", status=200):
    return jsonify({
        "success": True,
        "message": message,
        "data": data
    }), status


def login_required(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        if "owner_id" not in session:
            return jsonify({
                "success": False,
                "message": "Please login first"
            }), 401

        return func(*args, **kwargs)

    return wrapper


# ---------------- HOME ----------------

@app.route("/")
def home():
    return render_template("index.html")


@app.route("/health")
def health():
    return response({
        "status": "Online",
        "project": "MUZZLE-ID AI"
    })


# ---------------- DASHBOARD ----------------

@app.route("/api/dashboard")
def dashboard():

    with get_db() as db:

        total = db.execute(
            "SELECT COUNT(*) FROM cows WHERE public_profile=1"
        ).fetchone()[0]

        cows = db.execute(
            "SELECT COUNT(*) FROM cows WHERE animal_type='Cow' AND public_profile=1"
        ).fetchone()[0]

        buffalo = db.execute(
            "SELECT COUNT(*) FROM cows WHERE animal_type='Buffalo' AND public_profile=1"
        ).fetchone()[0]

        owners = db.execute(
            "SELECT COUNT(DISTINCT owner_id) FROM cows WHERE public_profile=1"
        ).fetchone()[0]

        breeds = db.execute(
            "SELECT COUNT(DISTINCT breed) FROM cows WHERE public_profile=1"
        ).fetchone()[0]

        listings = db.execute(
            "SELECT COUNT(*) FROM marketplace WHERE status='available'"
        ).fetchone()[0]

    return response({
        "total_cattle": total,
        "total_cows": cows,
        "total_buffalo": buffalo,
        "total_owners": owners,
        "total_breeds": breeds,
        "marketplace_listings": listings
    })


# ---------------- REGISTER ----------------

@app.route("/api/register", methods=["POST"])
def register():

    data = request.get_json(silent=True) or {}

    name = data.get("name", "").strip()
    email = data.get("email", "").strip().lower()
    password = data.get("password", "")

    if not name or not email or len(password) < 8:
        return jsonify({
            "success": False,
            "message": "Name, valid email and password of at least 8 characters required"
        }), 400

    try:

        with get_db() as db:

            cursor = db.execute("""
                INSERT INTO users(name,email,password,created_at)
                VALUES(?,?,?,?)
            """, (
                name,
                email,
                generate_password_hash(password),
                datetime.now().isoformat()
            ))

            user_id = cursor.lastrowid

        session.clear()
        session["owner_id"] = user_id
        session["owner_name"] = name

        return response({
            "owner_id": user_id,
            "name": name
        }, "Account created", 201)

    except sqlite3.IntegrityError:

        return jsonify({
            "success": False,
            "message": "Email already registered"
        }), 409


# ---------------- LOGIN ----------------

@app.route("/api/login", methods=["POST"])
def login():

    data = request.get_json(silent=True) or {}

    email = data.get("email", "").strip().lower()
    password = data.get("password", "")

    with get_db() as db:

        user = db.execute(
            "SELECT * FROM users WHERE email=?",
            (email,)
        ).fetchone()

    if not user or not check_password_hash(user["password"], password):

        return jsonify({
            "success": False,
            "message": "Incorrect email or password"
        }), 401

    session.clear()
    session["owner_id"] = user["id"]
    session["owner_name"] = user["name"]

    return response({
        "owner_id": user["id"],
        "name": user["name"]
    }, "Login successful")


# ---------------- LOGOUT ----------------

@app.route("/api/logout", methods=["POST"])
def logout():

    session.clear()

    return response(message="Logged out")


# ---------------- CATTLE REGISTRATION ----------------

@app.route("/api/cows", methods=["POST"])
@login_required
def add_cow():

    data = request.get_json(silent=True) or {}

    breed = data.get("breed", "").strip()

    if not breed:
        return jsonify({
            "success": False,
            "message": "Breed is required"
        }), 400

    animal_type = data.get("animal_type", "Cow")

    if animal_type not in ["Cow", "Buffalo", "Other"]:
        return jsonify({
            "success": False,
            "message": "Invalid animal type"
        }), 400

    with get_db() as db:

        cursor = db.execute("""
            INSERT INTO cows(
                owner_id,name,animal_type,breed,age,
                gender,weight,country,public_profile,created_at
            )
            VALUES(?,?,?,?,?,?,?,?,?,?)
        """, (
            session["owner_id"],
            data.get("name"),
            animal_type,
            breed,
            data.get("age"),
            data.get("gender"),
            data.get("weight"),
            data.get("country"),
            1 if data.get("public_profile") else 0,
            datetime.now().isoformat()
        ))

        cow_id = cursor.lastrowid

    return response({
        "cow_id": cow_id,
        "breed": breed,
        "animal_type": animal_type
    }, "Animal registered", 201)


# ---------------- CATTLE LIST ----------------

@app.route("/api/cows")
def get_cows():

    owner_id = session.get("owner_id")

    with get_db() as db:

        if owner_id:

            rows = db.execute(
                "SELECT * FROM cows WHERE owner_id=?",
                (owner_id,)
            ).fetchall()

        else:

            rows = db.execute(
                "SELECT id,name,animal_type,breed,age,gender,country "
                "FROM cows WHERE public_profile=1"
            ).fetchall()

    return response([dict(row) for row in rows])


# ---------------- SINGLE CATTLE PROFILE ----------------

@app.route("/api/cows/<int:cow_id>")
def cow_profile(cow_id):

    with get_db() as db:

        cow = db.execute(
            "SELECT * FROM cows WHERE id=?",
            (cow_id,)
        ).fetchone()

    if not cow:
        return jsonify({"success": False, "message": "Animal not found"}), 404

    if cow["owner_id"] != session.get("owner_id") and not cow["public_profile"]:
        return jsonify({"success": False, "message": "Private profile"}), 403

    return response(dict(cow))


# ---------------- HEALTH RECORDS ----------------

@app.route("/api/health/<int:cow_id>")
@login_required
def health_records(cow_id):

    with get_db() as db:

        cow = db.execute(
            "SELECT id FROM cows WHERE id=? AND owner_id=?",
            (cow_id, session["owner_id"])
        ).fetchone()

        if not cow:
            return jsonify({"success": False, "message": "Animal not found"}), 404

        rows = db.execute(
            "SELECT * FROM health_records WHERE cow_id=?",
            (cow_id,)
        ).fetchall()

    return response([dict(row) for row in rows])


@app.route("/api/records", methods=["POST"])
@login_required
def add_health_record():

    data = request.get_json(silent=True) or {}

    with get_db() as db:

        cow = db.execute(
            "SELECT id FROM cows WHERE id=? AND owner_id=?",
            (data.get("cow_id"), session["owner_id"])
        ).fetchone()

        if not cow:
            return jsonify({"success": False, "message": "Animal not found"}), 404

        cursor = db.execute("""
            INSERT INTO health_records(cow_id,description,record_date)
            VALUES(?,?,?)
        """, (
            data.get("cow_id"),
            data.get("description"),
            data.get("date", datetime.now().date().isoformat())
        ))

    return response({"record_id": cursor.lastrowid}, "Health record saved", 201)


# ---------------- MILK RECORDS ----------------

@app.route("/api/milk", methods=["POST"])
@login_required
def add_milk_record():

    data = request.get_json(silent=True) or {}

    try:
        quantity = float(data.get("quantity"))
        cow_id = int(data.get("cow_id"))

        if quantity < 0:
            raise ValueError()

    except (TypeError, ValueError):
        return jsonify({"success": False, "message": "Invalid milk quantity or animal ID"}), 400

    with get_db() as db:

        cow = db.execute(
            "SELECT id FROM cows WHERE id=? AND owner_id=?",
            (cow_id, session["owner_id"])
        ).fetchone()

        if not cow:
            return jsonify({"success": False, "message": "Animal not found"}), 404

        cursor = db.execute("""
            INSERT INTO milk_records(cow_id,quantity,record_date)
            VALUES(?,?,?)
        """, (
            cow_id,
            quantity,
            data.get("date", datetime.now().date().isoformat())
        ))

    return response({"record_id": cursor.lastrowid}, "Milk record saved", 201)


@app.route("/api/milk")
@login_required
def get_milk_records():

    with get_db() as db:

        rows = db.execute("""
            SELECT m.* FROM milk_records m
            JOIN cows c ON c.id=m.cow_id
            WHERE c.owner_id=?
            ORDER BY m.id DESC
        """, (session["owner_id"],)).fetchall()

    return response([dict(row) for row in rows])


# ---------------- MARKETPLACE ----------------

@app.route("/api/marketplace")
def marketplace():

    with get_db() as db:

        rows = db.execute("""
            SELECT m.id,m.price,m.location,m.status,
                   c.id AS cow_id,c.name,c.breed,c.animal_type,c.age
            FROM marketplace m
            JOIN cows c ON c.id=m.cow_id
            WHERE m.status='available' AND c.public_profile=1
            ORDER BY m.id DESC
        """).fetchall()

    return response([dict(row) for row in rows])


@app.route("/api/marketplace", methods=["POST"])
@login_required
def add_listing():

    data = request.get_json(silent=True) or {}

    try:
        cow_id = int(data.get("cow_id"))
        price = float(data.get("price", 0))

        if price < 0:
            raise ValueError()

    except (TypeError, ValueError):
        return jsonify({"success": False, "message": "Invalid animal ID or price"}), 400

    with get_db() as db:

        cow = db.execute(
            "SELECT id FROM cows WHERE id=? AND owner_id=?",
            (cow_id, session["owner_id"])
        ).fetchone()

        if not cow:
            return jsonify({"success": False, "message": "Animal not found"}), 404

        cursor = db.execute("""
            INSERT INTO marketplace(cow_id,owner_id,price,location)
            VALUES(?,?,?,?)
        """, (
            cow_id,
            session["owner_id"],
            price,
            data.get("location")
        ))

    return response({"listing_id": cursor.lastrowid}, "Listing created", 201)


# ---------------- BREED CATALOGUE ----------------

BREEDS = {
    "Cow": [
        "Gir", "Sahiwal", "Red Sindhi", "Tharparkar",
        "Kankrej", "Rathi", "Ongole", "Hariana",
        "Deoni", "Hallikar", "Vechur", "Punganur",
        "Holstein Friesian", "Jersey", "Brown Swiss",
        "Angus", "Hereford", "Simmental", "Limousin"
    ],
    "Buffalo": [
        "Murrah", "Jaffarabadi", "Mehsana", "Surti",
        "Nili-Ravi", "Bhadawari", "Nagpuri",
        "Pandharpuri", "Toda", "Mediterranean"
    ]
}


@app.route("/api/breeds")
def get_breeds():

    animal_type = request.args.get("animal_type")

    if animal_type:
        return response(BREEDS.get(animal_type, []))

    return response(BREEDS)


# ---------------- AI ASSISTANT ----------------

@app.route("/api/assistant", methods=["POST"])
def assistant():

    data = request.get_json(silent=True) or {}

    question = data.get("message", "").strip()

    if not question:
        return jsonify({"success": False, "message": "Enter a question"}), 400

    answer = (
        "Welcome to MUZZLE-ID AI. This is the demonstration assistant. "
        "The live AI model is not connected yet. For animal illness or "
        "medical concerns, consult a qualified veterinarian."
    )

    return response({
        "question": question,
        "answer": answer,
        "mode": "demo"
    })


# ---------------- MUZZLE SCAN ----------------

@app.route("/api/scan", methods=["POST"])
@login_required
def muzzle_scan():

    return jsonify({
        "success": True,
        "status": "model_not_connected",
        "message": (
            "Muzzle image upload and individual cattle recognition "
            "require a trained cattle recognition model. "
            "No automatic identity has been generated."
        )
    }), 202


# ---------------- START SERVER ----------------

if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 5000)),
        debug=False
    )