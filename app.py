import os, sqlite3
from functools import wraps
from datetime import date, datetime
from flask import Flask, request, jsonify, session, render_template, g
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "change-this-before-production")
DB_PATH = os.environ.get("DATABASE_PATH", "muzzle_id.db")

def db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys=ON")
    return g.db

@app.teardown_appcontext
def close_db(exc):
    conn = g.pop("db", None)
    if conn: conn.close()

def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS users(
      id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL,
      email TEXT UNIQUE NOT NULL, password_hash TEXT NOT NULL,
      role TEXT NOT NULL DEFAULT 'owner', created_at TEXT DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS animals(
      id INTEGER PRIMARY KEY AUTOINCREMENT, owner_id INTEGER NOT NULL,
      name TEXT NOT NULL, animal_type TEXT NOT NULL DEFAULT 'Cow',
      breed TEXT DEFAULT 'Unknown', age REAL DEFAULT 0, gender TEXT DEFAULT 'Unknown',
      weight REAL DEFAULT 0, tag_id TEXT, notes TEXT DEFAULT '',
      public_profile INTEGER DEFAULT 0, created_at TEXT DEFAULT CURRENT_TIMESTAMP,
      FOREIGN KEY(owner_id) REFERENCES users(id) ON DELETE CASCADE
    );
    CREATE TABLE IF NOT EXISTS health_records(
      id INTEGER PRIMARY KEY AUTOINCREMENT, animal_id INTEGER NOT NULL,
      owner_id INTEGER NOT NULL, record_date TEXT NOT NULL, description TEXT NOT NULL,
      status TEXT DEFAULT 'Recorded', vet_name TEXT DEFAULT '',
      FOREIGN KEY(animal_id) REFERENCES animals(id) ON DELETE CASCADE
    );
    CREATE TABLE IF NOT EXISTS vaccinations(
      id INTEGER PRIMARY KEY AUTOINCREMENT, animal_id INTEGER NOT NULL,
      owner_id INTEGER NOT NULL, vaccine TEXT NOT NULL, due_date TEXT NOT NULL,
      status TEXT DEFAULT 'Upcoming', notes TEXT DEFAULT '',
      FOREIGN KEY(animal_id) REFERENCES animals(id) ON DELETE CASCADE
    );
    CREATE TABLE IF NOT EXISTS milk_records(
      id INTEGER PRIMARY KEY AUTOINCREMENT, animal_id INTEGER NOT NULL,
      owner_id INTEGER NOT NULL, record_date TEXT NOT NULL, quantity REAL NOT NULL,
      session TEXT DEFAULT 'Daily', notes TEXT DEFAULT '',
      FOREIGN KEY(animal_id) REFERENCES animals(id) ON DELETE CASCADE
    );
    CREATE TABLE IF NOT EXISTS marketplace(
      id INTEGER PRIMARY KEY AUTOINCREMENT, animal_id INTEGER NOT NULL,
      owner_id INTEGER NOT NULL, price REAL NOT NULL, location TEXT DEFAULT '',
      description TEXT DEFAULT '', status TEXT DEFAULT 'Active',
      created_at TEXT DEFAULT CURRENT_TIMESTAMP,
      FOREIGN KEY(animal_id) REFERENCES animals(id) ON DELETE CASCADE
    );
    """)
    # Optional first administrator, configured in Render environment variables.
    email, password = os.getenv("ADMIN_EMAIL"), os.getenv("ADMIN_PASSWORD")
    if email and password:
        row = conn.execute("SELECT id FROM users WHERE email=?", (email.lower(),)).fetchone()
        if not row:
            conn.execute("INSERT INTO users(name,email,password_hash,role) VALUES(?,?,?,?)",
                         ("System Administrator", email.lower(), generate_password_hash(password), "admin"))
    conn.commit()
    conn.close()

def ok(data=None, message="Success", status=200):
    return jsonify({"success": True, "message": message, "data": data}), status

def fail(message, status=400):
    return jsonify({"success": False, "message": message}), status

def current_user():
    if not session.get("user_id"): return None
    return db().execute("SELECT id,name,email,role FROM users WHERE id=?", (session["user_id"],)).fetchone()

def login_required(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        if not current_user(): return fail("Please log in first.", 401)
        return fn(*args, **kwargs)
    return wrapped

def admin_required(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        u = current_user()
        if not u: return fail("Please log in first.", 401)
        if u["role"] != "admin": return fail("Administrator access required.", 403)
        return fn(*args, **kwargs)
    return wrapped

def animal_access(animal_id, owner_id=None):
    sql = "SELECT * FROM animals WHERE id=?"
    args = [animal_id]
    if owner_id is not None:
        sql += " AND owner_id=?"; args.append(owner_id)
    return db().execute(sql, args).fetchone()

@app.route("/")
def home():
    return render_template("index.html")

@app.get("/health")
def health_check():
    return jsonify({"status":"ok","service":"MUZZLE-ID AI","time":datetime.utcnow().isoformat()+"Z"})

@app.post("/api/register")
def register():
    d=request.get_json(silent=True) or {}
    name=(d.get("name") or "").strip(); email=(d.get("email") or "").strip().lower()
    password=d.get("password") or ""
    if not name or not email or len(password)<8: return fail("Name, email and password (minimum 8 characters) are required.")
    try:
        cur=db().execute("INSERT INTO users(name,email,password_hash,role) VALUES(?,?,?,?)",
                         (name,email,generate_password_hash(password),"owner"))
        db().commit(); session["user_id"]=cur.lastrowid
        return ok({"user":{"id":cur.lastrowid,"name":name,"email":email,"role":"owner"}},"Account created.",201)
    except sqlite3.IntegrityError: return fail("This email is already registered.",409)

@app.post("/api/login")
def login():
    d=request.get_json(silent=True) or {}; email=(d.get("email") or "").strip().lower()
    u=db().execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
    if not u or not check_password_hash(u["password_hash"],d.get("password") or ""): return fail("Incorrect email or password.",401)
    session.clear(); session["user_id"]=u["id"]
    return ok({"user":{"id":u["id"],"name":u["name"],"email":u["email"],"role":u["role"]}},"Logged in.")

@app.post("/api/logout")
@login_required
def logout():
    session.clear(); return ok(message="Logged out.")

@app.get("/api/me")
def me():
    u=current_user()
    return ok(dict(u) if u else None)

@app.get("/api/dashboard")
def dashboard():
    u=current_user()
    if u and u["role"]=="admin":
        counts={
          "total_cattle":db().execute("SELECT COUNT(*) FROM animals").fetchone()[0],
          "total_owners":db().execute("SELECT COUNT(*) FROM users WHERE role='owner'").fetchone()[0],
          "total_breeds":db().execute("SELECT COUNT(DISTINCT breed) FROM animals").fetchone()[0],
          "marketplace_listings":db().execute("SELECT COUNT(*) FROM marketplace WHERE status='Active'").fetchone()[0],
          "total_health_records":db().execute("SELECT COUNT(*) FROM health_records").fetchone()[0],
          "total_milk_liters":db().execute("SELECT COALESCE(SUM(quantity),0) FROM milk_records").fetchone()[0],
          "total_cows":db().execute("SELECT COUNT(*) FROM animals WHERE animal_type='Cow'").fetchone()[0],
          "total_buffalo":db().execute("SELECT COUNT(*) FROM animals WHERE animal_type='Buffalo'").fetchone()[0]}
    elif u:
        oid=u["id"]
        counts={
          "total_cattle":db().execute("SELECT COUNT(*) FROM animals WHERE owner_id=?",(oid,)).fetchone()[0],
          "total_owners":1,
          "total_breeds":db().execute("SELECT COUNT(DISTINCT breed) FROM animals WHERE owner_id=?",(oid,)).fetchone()[0],
          "marketplace_listings":db().execute("SELECT COUNT(*) FROM marketplace WHERE owner_id=? AND status='Active'",(oid,)).fetchone()[0],
          "total_health_records":db().execute("SELECT COUNT(*) FROM health_records WHERE owner_id=?",(oid,)).fetchone()[0],
          "total_milk_liters":db().execute("SELECT COALESCE(SUM(quantity),0) FROM milk_records WHERE owner_id=?",(oid,)).fetchone()[0],
          "total_cows":db().execute("SELECT COUNT(*) FROM animals WHERE owner_id=? AND animal_type='Cow'",(oid,)).fetchone()[0],
          "total_buffalo":db().execute("SELECT COUNT(*) FROM animals WHERE owner_id=? AND animal_type='Buffalo'",(oid,)).fetchone()[0]}
    else: counts={"total_cattle":0,"total_owners":0,"total_breeds":0,"marketplace_listings":0,"total_health_records":0,"total_milk_liters":0,"total_cows":0,"total_buffalo":0}
    return ok(counts)

@app.get("/api/cows")
@login_required
def list_animals():
    u=current_user()
    rows=db().execute("SELECT * FROM animals WHERE owner_id=? ORDER BY id DESC",(u["id"],)).fetchall()
    return ok([dict(r) for r in rows])

@app.post("/api/cows")
@login_required
def add_animal():
    u=current_user(); d=request.get_json(silent=True) or {}
    name=(d.get("name") or "").strip()
    if not name: return fail("Animal name is required.")
    try:
        cur=db().execute("""INSERT INTO animals(owner_id,name,animal_type,breed,age,gender,weight,tag_id,notes,public_profile)
          VALUES(?,?,?,?,?,?,?,?,?,?)""",
          (u["id"],name,d.get("animal_type","Cow"),d.get("breed","Unknown"),
           float(d.get("age") or 0),d.get("gender","Unknown"),float(d.get("weight") or 0),
           d.get("tag_id",""),d.get("notes",""),1 if d.get("public_profile") else 0))
        db().commit(); return ok({"animal_id":cur.lastrowid},"Animal registered.",201)
    except (ValueError,sqlite3.Error): return fail("Please check the animal details.")

@app.get("/api/cows/<int:animal_id>")
@login_required
def animal_profile(animal_id):
    u=current_user(); a=animal_access(animal_id,u["id"])
    if not a: return fail("Animal not found.",404)
    return ok(dict(a))

@app.delete("/api/cows/<int:animal_id>")
@login_required
def delete_animal(animal_id):
    u=current_user(); a=animal_access(animal_id,u["id"])
    if not a: return fail("Animal not found.",404)
    db().execute("DELETE FROM animals WHERE id=?",(animal_id,)); db().commit()
    return ok(message="Animal deleted.")

@app.get("/api/health/<int:animal_id>")
@login_required
def health_list(animal_id):
    u=current_user()
    if not animal_access(animal_id,u["id"]): return fail("Animal not found.",404)
    rows=db().execute("SELECT * FROM health_records WHERE animal_id=? ORDER BY record_date DESC",(animal_id,)).fetchall()
    return ok([dict(r) for r in rows])

@app.post("/api/records")
@login_required
def add_health():
    u=current_user(); d=request.get_json(silent=True) or {}
    aid=d.get("animal_id") or d.get("cow_id")
    if not aid or not animal_access(aid,u["id"]): return fail("Select one of your registered animals.")
    desc=(d.get("description") or "").strip()
    if not desc: return fail("Health description is required.")
    cur=db().execute("INSERT INTO health_records(animal_id,owner_id,record_date,description,status,vet_name) VALUES(?,?,?,?,?,?)",
      (aid,u["id"],d.get("date") or date.today().isoformat(),desc,d.get("status","Recorded"),d.get("vet_name","")))
    db().commit(); return ok({"record_id":cur.lastrowid},"Health record saved.",201)

@app.get("/api/vaccinations")
@login_required
def vaccination_list():
    u=current_user()
    rows=db().execute("""SELECT v.*,a.name animal_name FROM vaccinations v JOIN animals a ON a.id=v.animal_id
      WHERE v.owner_id=? ORDER BY v.due_date""",(u["id"],)).fetchall()
    return ok([dict(r) for r in rows])

@app.post("/api/vaccinations")
@login_required
def add_vaccination():
    u=current_user(); d=request.get_json(silent=True) or {}; aid=d.get("animal_id")
    if not aid or not animal_access(aid,u["id"]): return fail("Select one of your animals.")
    vaccine=(d.get("vaccine") or "").strip(); due=d.get("due_date") or ""
    if not vaccine or not due: return fail("Vaccine and due date are required.")
    cur=db().execute("INSERT INTO vaccinations(animal_id,owner_id,vaccine,due_date,notes) VALUES(?,?,?,?,?)",
                     (aid,u["id"],vaccine,due,d.get("notes","")))
    db().commit(); return ok({"vaccination_id":cur.lastrowid},"Vaccination reminder added.",201)

@app.post("/api/milk")
@login_required
def add_milk():
    u=current_user(); d=request.get_json(silent=True) or {}; aid=d.get("animal_id") or d.get("cow_id")
    if not aid or not animal_access(aid,u["id"]): return fail("Select one of your animals.")
    try: qty=float(d.get("quantity"))
    except (TypeError,ValueError): return fail("Enter a valid milk quantity.")
    if qty<0: return fail("Quantity cannot be negative.")
    cur=db().execute("INSERT INTO milk_records(animal_id,owner_id,record_date,quantity,session,notes) VALUES(?,?,?,?,?,?)",
      (aid,u["id"],d.get("date") or date.today().isoformat(),qty,d.get("session","Daily"),d.get("notes","")))
    db().commit(); return ok({"milk_record_id":cur.lastrowid},"Milk record saved.",201)

@app.get("/api/milk")
@login_required
def milk_list():
    u=current_user()
    rows=db().execute("""SELECT m.*,a.name animal_name FROM milk_records m JOIN animals a ON a.id=m.animal_id
      WHERE m.owner_id=? ORDER BY m.record_date DESC,m.id DESC""",(u["id"],)).fetchall()
    return ok([dict(r) for r in rows])

@app.get("/api/marketplace")
def marketplace_list():
    rows=db().execute("""SELECT m.*,a.name animal_name,a.breed,a.animal_type,u.name owner_name
      FROM marketplace m JOIN animals a ON a.id=m.animal_id JOIN users u ON u.id=m.owner_id
      WHERE m.status='Active' AND a.public_profile=1 ORDER BY m.id DESC""").fetchall()
    return ok([dict(r) for r in rows])

@app.post("/api/marketplace")
@login_required
def marketplace_add():
    u=current_user(); d=request.get_json(silent=True) or {}; aid=d.get("animal_id") or d.get("cow_id")
    if not aid or not animal_access(aid,u["id"]): return fail("Select one of your animals.")
    try: price=float(d.get("price"))
    except (TypeError,ValueError): return fail("Enter a valid price.")
    if price<=0: return fail("Price must be greater than zero.")
    cur=db().execute("INSERT INTO marketplace(animal_id,owner_id,price,location,description) VALUES(?,?,?,?,?)",
      (aid,u["id"],price,d.get("location",""),d.get("description","")))
    db().commit(); return ok({"listing_id":cur.lastrowid},"Marketplace listing created.",201)

@app.get("/api/breeds")
def breeds():
    return ok({"Cow":["Gir","Sahiwal","Tharparkar","Red Sindhi","Holstein Friesian","Jersey","Hariana","Ongole"],
               "Buffalo":["Murrah","Jaffarabadi","Surti","Mehsana","Nili-Ravi","Bhadawari"]})

@app.post("/api/assistant")
def assistant():
    d=request.get_json(silent=True) or {}; q=(d.get("message") or "").strip()
    if not q: return fail("Please enter a question.")
    return ok({"question":q,"answer":"यह अभी demo assistant है। पशु की गंभीर समस्या में स्थानीय पशु-चिकित्सक से सलाह लें। वास्तविक AI उत्तर के लिए सत्यापित AI सेवा/API जोड़नी होगी।","mode":"demo"})

@app.post("/api/scan")
@login_required
def scan_placeholder():
    return jsonify({"success":False,"message":"Muzzle recognition model is not connected yet.","code":"MODEL_NOT_CONNECTED"}),501

@app.get("/api/admin/users")
@admin_required
def admin_users():
    rows=db().execute("SELECT id,name,email,role,created_at FROM users ORDER BY id DESC").fetchall()
    return ok([dict(r) for r in rows])

@app.get("/api/admin/animals")
@admin_required
def admin_animals():
    rows=db().execute("""SELECT a.*,u.name owner_name,u.email owner_email FROM animals a
      JOIN users u ON u.id=a.owner_id ORDER BY a.id DESC""").fetchall()
    return ok([dict(r) for r in rows])

@app.post("/api/admin/users/<int:user_id>/role")
@admin_required
def admin_role(user_id):
    d=request.get_json(silent=True) or {}; role=d.get("role")
    if role not in ("owner","admin"): return fail("Role must be owner or admin.")
    if user_id==session.get("user_id") and role!="admin": return fail("You cannot remove your own admin role.")
    cur=db().execute("UPDATE users SET role=? WHERE id=?",(role,user_id)); db().commit()
    if not cur.rowcount: return fail("User not found.",404)
    return ok(message="User role updated.")

@app.post("/api/admin/marketplace/<int:listing_id>/status")
@admin_required
def admin_listing_status(listing_id):
    d=request.get_json(silent=True) or {}; status=d.get("status")
    if status not in ("Active","Hidden","Removed"): return fail("Invalid listing status.")
    cur=db().execute("UPDATE marketplace SET status=? WHERE id=?",(status,listing_id)); db().commit()
    if not cur.rowcount: return fail("Listing not found.",404)
    return ok(message="Listing status updated.")

@app.errorhandler(404)
def not_found(e):
    if request.path.startswith("/api/"): return fail("API endpoint not found.",404)
    return "Page not found",404

init_db()
if __name__=="__main__":
    app.run(host="0.0.0.0",port=int(os.environ.get("PORT",5000)),debug=False)
