import os
import sqlite3
from datetime import datetime
from flask import Flask, request, jsonify, render_template

# ==========================================
# APP CONFIGURATION
# ==========================================

app = Flask(__name__)

app.config["JSON_SORT_KEYS"] = False

DATABASE = os.environ.get("DATABASE_PATH", "cattle.db")
DEVICE_TOKEN = os.environ.get("DEVICE_TOKEN")


# ==========================================
# DATABASE CONNECTION
# ==========================================

def get_db_connection():
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    conn = get_db_connection()

    conn.executescript("""
        CREATE TABLE IF NOT EXISTS cows (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            breed TEXT,
            age INTEGER,
            weight REAL,
            gender TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS sensors (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            cow_id INTEGER NOT NULL,
            temperature REAL,
            humidity REAL,
            activity REAL,
            heart_rate REAL,
            recorded_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (cow_id) REFERENCES cows(id)
                ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            cow_id INTEGER NOT NULL,
            record_type TEXT,
            description TEXT,
            date TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (cow_id) REFERENCES cows(id)
                ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS milk_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            cow_id INTEGER NOT NULL,
            quantity REAL NOT NULL,
            session TEXT DEFAULT 'morning',
            date TEXT DEFAULT CURRENT_DATE,
            notes TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (cow_id) REFERENCES cows(id)
                ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_sensors_cow_id
        ON sensors(cow_id);

        CREATE INDEX IF NOT EXISTS idx_records_cow_id
        ON records(cow_id);

        CREATE INDEX IF NOT EXISTS idx_milk_cow_date
        ON milk_records(cow_id, date);
    """)

    conn.commit()
    conn.close()


# ==========================================
# HELPER FUNCTIONS
# ==========================================

def error_response(message, status=400):
    return jsonify({
        "success": False,
        "error": message
    }), status


def cow_exists(conn, cow_id):
    cow = conn.execute(
        "SELECT id FROM cows WHERE id = ?",
        (cow_id,)
    ).fetchone()

    return cow is not None


def get_json_data():
    return request.get_json(silent=True) or {}


def require_device_token():
    """
    If DEVICE_TOKEN is configured, sensor requests
    must include the matching X-Device-Token header.
    """
    if DEVICE_TOKEN:
        supplied_token = request.headers.get("X-Device-Token")

        if supplied_token != DEVICE_TOKEN:
            return False

    return True


# ==========================================
# HOME PAGE
# ==========================================

@app.route("/")
def home():
    return render_template("index.html")


# ==========================================
# COW MANAGEMENT API
# ==========================================

@app.route("/api/cows", methods=["GET"])
def get_cows():
    conn = get_db_connection()

    cows = conn.execute("""
        SELECT *
        FROM cows
        ORDER BY id DESC
    """).fetchall()

    conn.close()

    return jsonify([dict(cow) for cow in cows])


@app.route("/api/cows", methods=["POST"])
def add_cow():
    data = get_json_data()

    name = data.get("name")

    if not name or not str(name).strip():
        return error_response("Cow name is required")

    try:
        age = data.get("age")
        weight = data.get("weight")

        if age is not None:
            age = int(age)
            if age < 0:
                return error_response("Age cannot be negative")

        if weight is not None:
            weight = float(weight)
            if weight < 0:
                return error_response("Weight cannot be negative")

    except (ValueError, TypeError):
        return error_response("Age or weight has an invalid value")

    conn = get_db_connection()

    cursor = conn.execute("""
        INSERT INTO cows (name, breed, age, weight, gender)
        VALUES (?, ?, ?, ?, ?)
    """, (
        str(name).strip(),
        data.get("breed"),
        age,
        weight,
        data.get("gender")
    ))

    conn.commit()

    cow_id = cursor.lastrowid

    cow = conn.execute(
        "SELECT * FROM cows WHERE id = ?",
        (cow_id,)
    ).fetchone()

    conn.close()

    return jsonify({
        "success": True,
        "message": "Cow added successfully",
        "cow": dict(cow)
    }), 201


@app.route("/api/cows/<int:cow_id>", methods=["GET"])
def get_cow(cow_id):
    conn = get_db_connection()

    cow = conn.execute(
        "SELECT * FROM cows WHERE id = ?",
        (cow_id,)
    ).fetchone()

    if not cow:
        conn.close()
        return error_response("Cow not found", 404)

    sensors = conn.execute("""
        SELECT *
        FROM sensors
        WHERE cow_id = ?
        ORDER BY id DESC
        LIMIT 20
    """, (cow_id,)).fetchall()

    records = conn.execute("""
        SELECT *
        FROM records
        WHERE cow_id = ?
        ORDER BY id DESC
    """, (cow_id,)).fetchall()

    conn.close()

    return jsonify({
        "cow": dict(cow),
        "sensors": [dict(item) for item in sensors],
        "records": [dict(item) for item in records]
    })


# ==========================================
# MEDICAL AND VACCINATION RECORDS API
# ==========================================

@app.route("/api/records", methods=["POST"])
def add_record():
    data = get_json_data()

    cow_id = data.get("cow_id")
    record_type = data.get("record_type")
    description = data.get("description")

    if not cow_id:
        return error_response("cow_id is required")

    try:
        cow_id = int(cow_id)
    except (ValueError, TypeError):
        return error_response("Invalid cow_id")

    conn = get_db_connection()

    if not cow_exists(conn, cow_id):
        conn.close()
        return error_response("Cow not found", 404)

    cursor = conn.execute("""
        INSERT INTO records (
            cow_id,
            record_type,
            description,
            date
        )
        VALUES (?, ?, ?, ?)
    """, (
        cow_id,
        record_type,
        description,
        data.get("date") or datetime.now().isoformat()
    ))

    conn.commit()

    record = conn.execute(
        "SELECT * FROM records WHERE id = ?",
        (cursor.lastrowid,)
    ).fetchone()

    conn.close()

    return jsonify({
        "success": True,
        "message": "Record added successfully",
        "record": dict(record)
    }), 201


# ==========================================
# IOT SENSOR API
# ==========================================

@app.route("/api/sensors", methods=["POST"])
def add_sensor():
    if not require_device_token():
        return error_response("Unauthorized device", 401)

    data = get_json_data()

    cow_id = data.get("cow_id")

    if not cow_id:
        return error_response("cow_id is required")

    try:
        cow_id = int(cow_id)

        temperature = data.get("temperature")
        humidity = data.get("humidity")
        activity = data.get("activity")
        heart_rate = data.get("heart_rate")

        if temperature is not None:
            temperature = float(temperature)

        if humidity is not None:
            humidity = float(humidity)

        if activity is not None:
            activity = float(activity)

        if heart_rate is not None:
            heart_rate = float(heart_rate)

    except (ValueError, TypeError):
        return error_response("Invalid sensor data")

    conn = get_db_connection()

    if not cow_exists(conn, cow_id):
        conn.close()
        return error_response("Cow not found", 404)

    cursor = conn.execute("""
        INSERT INTO sensors (
            cow_id,
            temperature,
            humidity,
            activity,
            heart_rate
        )
        VALUES (?, ?, ?, ?, ?)
    """, (
        cow_id,
        temperature,
        humidity,
        activity,
        heart_rate
    ))

    conn.commit()

    sensor = conn.execute(
        "SELECT * FROM sensors WHERE id = ?",
        (cursor.lastrowid,)
    ).fetchone()

    conn.close()

    return jsonify({
        "success": True,
        "message": "Sensor data saved successfully",
        "sensor": dict(sensor)
    }), 201


# ==========================================
# COW HEALTH API
# ==========================================

@app.route("/api/health/<int:cow_id>", methods=["GET"])
def get_health(cow_id):
    conn = get_db_connection()

    cow = conn.execute(
        "SELECT * FROM cows WHERE id = ?",
        (cow_id,)
    ).fetchone()

    if not cow:
        conn.close()
        return error_response("Cow not found", 404)

    sensor = conn.execute("""
        SELECT *
        FROM sensors
        WHERE cow_id = ?
        ORDER BY id DESC
        LIMIT 1
    """, (cow_id,)).fetchone()

    history = conn.execute("""
        SELECT *
        FROM sensors
        WHERE cow_id = ?
        ORDER BY id DESC
        LIMIT 10
    """, (cow_id,)).fetchall()

    conn.close()

    return jsonify({
        "cow_id": cow_id,
        "cow_name": cow["name"],
        "latest_health_data": dict(sensor) if sensor else None,
        "health_history": [dict(item) for item in history]
    })


# ==========================================
# MILK PRODUCTION API
# ==========================================

@app.route("/api/milk", methods=["POST"])
def add_milk_record():
    data = get_json_data()

    cow_id = data.get("cow_id")
    quantity = data.get("quantity")

    if cow_id is None or quantity is None:
        return error_response("cow_id and quantity are required")

    try:
        cow_id = int(cow_id)
        quantity = float(quantity)

        if quantity < 0:
            return error_response("Milk quantity cannot be negative")

    except (ValueError, TypeError):
        return error_response("Invalid cow_id or quantity")

    session = data.get("session", "morning").lower()

    if session not in ["morning", "evening"]:
        return error_response("Session must be morning or evening")

    milk_date = data.get("date") or datetime.now().strftime("%Y-%m-%d")

    conn = get_db_connection()

    if not cow_exists(conn, cow_id):
        conn.close()
        return error_response("Cow not found", 404)

    cursor = conn.execute("""
        INSERT INTO milk_records (
            cow_id,
            quantity,
            session,
            date,
            notes
        )
        VALUES (?, ?, ?, ?, ?)
    """, (
        cow_id,
        quantity,
        session,
        milk_date,
        data.get("notes")
    ))

    conn.commit()

    record = conn.execute(
        "SELECT * FROM milk_records WHERE id = ?",
        (cursor.lastrowid,)
    ).fetchone()

    conn.close()

    return jsonify({
        "success": True,
        "message": "Milk record saved successfully",
        "record": dict(record)
    }), 201


@app.route("/api/milk", methods=["GET"])
def get_milk_records():
    cow_id = request.args.get("cow_id")
    milk_date = request.args.get("date")

    conn = get_db_connection()

    query = """
        SELECT milk_records.*, cows.name AS cow_name
        FROM milk_records
        JOIN cows ON cows.id = milk_records.cow_id
        WHERE 1 = 1
    """

    params = []

    if cow_id:
        try:
            cow_id = int(cow_id)
        except ValueError:
            conn.close()
            return error_response("Invalid cow_id")

        query += " AND milk_records.cow_id = ?"
        params.append(cow_id)

    if milk_date:
        query += " AND milk_records.date = ?"
        params.append(milk_date)

    query += " ORDER BY milk_records.date DESC, milk_records.id DESC"

    records = conn.execute(query, params).fetchall()

    conn.close()

    return jsonify([dict(item) for item in records])


# ==========================================
# DASHBOARD STATISTICS API
# ==========================================

@app.route("/api/dashboard", methods=["GET"])
def dashboard():
    conn = get_db_connection()

    total_cows = conn.execute("""
        SELECT COUNT(*) AS total FROM cows
    """).fetchone()["total"]

    total_records = conn.execute("""
        SELECT COUNT(*) AS total FROM records
    """).fetchone()["total"]

    total_sensors = conn.execute("""
        SELECT COUNT(*) AS total FROM sensors
    """).fetchone()["total"]

    today = datetime.now().strftime("%Y-%m-%d")

    today_milk = conn.execute("""
        SELECT COALESCE(SUM(quantity), 0) AS total
        FROM milk_records
        WHERE date = ?
    """, (today,)).fetchone()["total"]

    total_milk = conn.execute("""
        SELECT COALESCE(SUM(quantity), 0) AS total
        FROM milk_records
    """).fetchone()["total"]

    conn.close()

    return jsonify({
        "success": True,
        "total_cows": total_cows,
        "total_medical_records": total_records,
        "total_sensor_readings": total_sensors,
        "today_milk_liters": today_milk,
        "total_milk_liters": total_milk
    })


# ==========================================
# AI ASSISTANT API
# ==========================================

@app.route("/api/assistant", methods=["POST"])
def assistant():
    data = get_json_data()

    question = data.get("question", "").strip()

    if not question:
        return error_response("Please enter a question")

    q = question.lower()

    if any(word in q for word in [
        "temperature", "बुखार", "तापमान"
    ]):
        answer = (
            "गाय का तापमान सामान्य से अलग दिखाई दे तो "
            "दोबारा सही तरीके से मापें और पशु चिकित्सक "
            "से सलाह लें।"
        )

    elif any(word in q for word in [
        "milk", "दूध", "production"
    ]):
        answer = (
            "दूध उत्पादन का रिकॉर्ड रोज सुबह और शाम दर्ज करें। "
            "इससे आप हर गाय के दूध उत्पादन में बदलाव देख सकते हैं।"
        )

    elif any(word in q for word in [
        "food", "feed", "चारा", "खाना"
    ]):
        answer = (
            "गाय को साफ पानी, संतुलित चारा और उचित पोषण दें। "
            "उसकी उम्र और स्वास्थ्य के अनुसार पशु चिकित्सक "
            "या पशु पोषण विशेषज्ञ से आहार की सलाह लें।"
        )

    elif any(word in q for word in [
        "health", "बीमारी", "स्वास्थ्य"
    ]):
        answer = (
            "गाय के व्यवहार, खान-पान, गतिविधि और सेंसर डेटा "
            "पर नजर रखें। बीमारी के संकेत दिखने पर पशु "
            "चिकित्सक से संपर्क करें।"
        )

    else:
        answer = (
            "नमस्ते! मैं आपके डेयरी फार्म मैनेजमेंट में मदद "
            "करने वाला डेमो असिस्टेंट हूँ। आप गाय के स्वास्थ्य, "
            "दूध उत्पादन, चारे और रिकॉर्ड के बारे में पूछ सकते हैं।"
        )

    return jsonify({
        "success": True,
        "question": question,
        "answer": answer
    })


# ==========================================
# API HEALTH CHECK
# ==========================================

@app.route("/api/status", methods=["GET"])
def api_status():
    return jsonify({
        "success": True,
        "message": "Dairy Management API is running",
        "timestamp": datetime.now().isoformat()
    })


# ==========================================
# ERROR HANDLERS
# ==========================================

@app.errorhandler(404)
def not_found(error):
    if request.path.startswith("/api/"):
        return error_response("API endpoint not found", 404)

    return "Page not found", 404


@app.errorhandler(500)
def internal_error(error):
    if request.path.startswith("/api/"):
        return error_response("Internal server error", 500)

    return "Internal server error", 500


# ==========================================
# START APPLICATION
# ==========================================

init_db()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))

    app.run(
        host="0.0.0.0",
        port=port,
        debug=False
    )