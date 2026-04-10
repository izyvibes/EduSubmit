from unittest import result

from flask import Flask, request, redirect, session, render_template, send_file, send_from_directory, abort, flash
import psycopg2
from psycopg2.extras import RealDictCursor
import os
import random
import string
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash
import smtplib
from email.mime.text import MIMEText
import time
import zipfile          # to create zip files
from io import BytesIO  # to keep zip in memory
from dotenv import load_dotenv
load_dotenv()
from datetime import datetime, timedelta
import atexit



EMAIL_USER = os.getenv("EMAIL_USER")
EMAIL_PASS = os.getenv("EMAIL_PASS")




# ------------------ CONFIG ------------------
app = Flask(__name__)
app.secret_key = os.getenv('FLASK_SECRET_KEY')

app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SECURE=False,  # set True on HTTPS
    SESSION_COOKIE_SAMESITE='Lax'
)
app.permanent_session_lifetime = timedelta(minutes=30)

UPLOAD_FOLDER = "uploads"
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER

ALLOWED_EXTENSIONS = {"pdf", "doc", "docx", "txt"}
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16 MB

# ------------------ OTP DATABASE ------------------
def store_otp(email, otp_code):
    """Save OTP to database with 5-minute expiry."""
    expiry = datetime.utcnow() + timedelta(minutes=5)

    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("""
        INSERT INTO otps (email, otp_code, expiry)
        VALUES (%s, %s, %s)
        ON CONFLICT (email) DO UPDATE
        SET otp_code = EXCLUDED.otp_code,
            expiry = EXCLUDED.expiry
    """, (email, otp_code, expiry))

    conn.commit()
    cursor.close()
    conn.close()


def verify_otp(email, otp_input):
    """Verify OTP from database."""
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute(
        "SELECT otp_code, expiry FROM otps WHERE email=%s",
        (email,)
    )

    row = cursor.fetchone()
    conn.close()

    if not row:
        return False, "No OTP found"

    otp_code = row["otp_code"]
    expiry = row["expiry"]

    if datetime.utcnow() > expiry:
        return False, "OTP expired"

    if otp_input != otp_code:
        return False, "Invalid OTP"

    return True, "OTP verified"


def delete_otp(email):
    """Remove OTP after successful verification."""
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute(
        "DELETE FROM otps WHERE email=%s",
        (email,)
    )

    conn.commit()
    cursor.close()
    conn.close()

# ------------------ HELPERS ------------------
def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS

def generate_student_code():
    return "STU-" + ''.join(random.choices(string.ascii_uppercase + string.digits, k=5))

def generate_csrf_token():
    if "csrf_token" not in session:
        session["csrf_token"] = ''.join(random.choices(string.ascii_letters + string.digits, k=32))
    return session["csrf_token"]

def validate_csrf():
    token = session.get("csrf_token")
    form_token = request.form.get("csrf_token")
    if not token or token != form_token:
        abort(403)

def get_db_connection():
    return psycopg2.connect(
        os.environ["DATABASE_URL"],
        cursor_factory=RealDictCursor,
        sslmode="require"
    )


#------------------OTP-----------------
def send_otp_email(to_email, otp_code):
    sender_email = os.environ.get("EMAIL_USER")       # your Gmail
    sender_password = os.environ.get("EMAIL_PASS")    # your App Password

    if not sender_email or not sender_password:
        print("❌ EMAIL_USER or EMAIL_PASS not set in .env")
        return

    msg = MIMEText(f"Your OTP code is: {otp_code}")
    msg['Subject'] = "Assignment System OTP Verification"
    msg['From'] = sender_email
    msg['To'] = to_email

    try:
        server = smtplib.SMTP_SSL("smtp.gmail.com", 465)
        server.login(sender_email, sender_password)
        server.sendmail(sender_email, to_email, msg.as_string())
        server.quit()
        print(f"✅ OTP sent to {to_email}")
    except Exception as e:
        print("❌ Failed to send OTP:", e)



# ------------------ DATABASE ------------------
def init_db():
    conn = get_db_connection()
    cursor = conn.cursor()

    # ------------------ USERS TABLE ------------------
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS users (
        id SERIAL PRIMARY KEY,
        username TEXT NOT NULL UNIQUE,
        fullname TEXT,
        email TEXT NOT NULL UNIQUE,
        password TEXT NOT NULL,
        role TEXT NOT NULL,
        student_code TEXT,
        matric TEXT UNIQUE,
        is_verified BOOLEAN DEFAULT FALSE
    )
    """)

    # ------------------ SUBMISSIONS TABLE ------------------
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS submissions (
        id SERIAL PRIMARY KEY,
        username TEXT NOT NULL,
        fullname TEXT NOT NULL,
        matric TEXT NOT NULL,
        course TEXT NOT NULL,
        filename TEXT NOT NULL,
        submitted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)

    # ------------------ OTP TABLE ------------------
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS otps (
        email TEXT PRIMARY KEY,
        otp_code TEXT NOT NULL,
        expiry TIMESTAMP NOT NULL
    )
    """)

    # ------------------ DEFAULT TEACHER ACCOUNT ------------------
    cursor.execute("SELECT * FROM users WHERE email=%s", ("teacher@gmail.com",))
    
    if not cursor.fetchone():
        hashed_pass = generate_password_hash("Teacher123")
        cursor.execute("""
        INSERT INTO users (username, fullname, email, password, role, is_verified)
        VALUES (%s, %s, %s, %s, %s, TRUE)
        """, (
            "teacher",
            "Admin Teacher",
            "teacher@gmail.com",
            hashed_pass,
            "teacher"
        ))

    # ------------------ COMMIT & CLOSE ------------------
    conn.commit()
    cursor.close()
    conn.close()

    print("✅ Database initialized successfully")
    # Initialize database on startup
    init_db()


#=------------------ HOME ------------------
@app.route("/")
def home():
    return redirect("/login")


# ------------------ REGISTER ------------------
@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        validate_csrf()
        fullname = request.form['fullname']
        email = request.form['email'].lower()
        password = request.form['password']
        matric = request.form['matric'].upper()
        role = request.form.get('role', 'student')

        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT is_verified FROM users WHERE email=%s", (email,))
        existing = cursor.fetchone()

        # CASE 1: user exists
        if existing:
            if not existing["is_verified"]:
                otp_code = ''.join(random.choices(string.digits, k=6))
                store_otp(email, otp_code)
                send_otp_email(email, otp_code)
                conn.close()
                return redirect(f"/verify?email={email}")
            else:
                conn.close()
                return "Email already registered. Please login ❌"

    # CASE 2: NEW USER → continue registration below
        # New user → create account
        hashed_pass = generate_password_hash(password)
        student_code = generate_student_code() if role == 'student' else None

        cursor.execute("""
        INSERT INTO users (username, fullname, email, password, role, student_code, matric, is_verified)
        VALUES (%s, %s, %s, %s, %s, %s, %s, FALSE)
        """, (fullname, fullname, email, hashed_pass, role, student_code, matric))

        conn.commit()
        conn.close()


    return render_template('register.html', csrf_token=generate_csrf_token())

# ------------------ RESEND OTP ------------------
@app.route("/resend_otp", methods=["POST"])
def resend_otp():
    validate_csrf()
    email = request.form.get("email")

    if not email:
        flash("❌ Email missing")
        return redirect("/register")

    email = email.lower()

    otp_code = ''.join(random.choices(string.digits, k=6))

    store_otp(email, otp_code)
    send_otp_email(email, otp_code)

    flash("🔁 New OTP sent!")
    return redirect(f"/verify?email={email}")


# ------------------ LOGIN ------------------
# ------------------ LOGIN ------------------
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        validate_csrf()
        email = request.form['email'].lower()
        password = request.form['password']

        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT username, role, password, is_verified FROM users WHERE email=%s",
            (email,)
        )
        user = cursor.fetchone()
        conn.close()

        time.sleep(1)

        if user and check_password_hash(user["password"], password):
            if not user["is_verified"]:
                return "❌ Verify your account first"

            session.permanent = True
            session['username'] = user["username"]
            session['role'] = user["role"]

            return redirect('/dashboard')

        return "Invalid email or password ❌"

    return render_template('login.html', csrf_token=generate_csrf_token())


# ------------------ VERIFY ------------------
@app.route("/verify", methods=["GET", "POST"])
def verify():
    email = request.args.get("email", "").lower()

    if not email:
        return redirect("/register")

    if request.method == "POST":
        validate_csrf()
        otp_input = request.form.get("otp")

        valid, msg = verify_otp(email, otp_input)

        if valid:
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE users SET is_verified=TRUE WHERE email=%s",
                (email,)
            )
            conn.commit()
            cursor.close()
            conn.close()

            delete_otp(email)
            flash("✅ Account Verified!")
            return redirect("/login")

        flash(f"❌ {msg}")

    return render_template(
        "verify.html",
        email=email,
        csrf_token=generate_csrf_token()
    )



# ------------------ DASHBOARD ------------------
@app.route("/dashboard")
def dashboard():
    if "username" not in session:
        return redirect("/login")
    return render_template("dashboard.html", username=session["username"], role=session.get("role"))

# ------------------ LOGOUT ------------------
@app.route("/logout")
def logout():
    session.clear()
    return redirect("/login")

# ------------------ ASSIGNMENT ------------------
@app.route("/assignment", methods=["GET", "POST"])
def assignment():
    if "username" not in session or session.get("role") != "student":
        return redirect("/login")

    message = None
    if request.method == "POST":
        validate_csrf()
        username = session["username"]
        matric_input = request.form["matric"].upper()

        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT matric, fullname FROM users WHERE username=%s", (username,))
        result = cursor.fetchone()
        correct_matric = result["matric"]
        fullname = result["fullname"]
        if matric_input != correct_matric:
            conn.close()
            return "❌ Matric mismatch"
        conn.close()

        file = request.files.get("assignment")
        if not file or file.filename == "":
            return "❌ No file selected"
        if not allowed_file(file.filename):
            return "❌ Invalid file type"

        safe_filename = secure_filename(file.filename)
        filename = f"{username}_{safe_filename}"
        file.save(os.path.join(app.config["UPLOAD_FOLDER"], filename))

        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO submissions (username, fullname, matric, course, filename) VALUES (%s, %s, %s, %s, %s)",
            (username, fullname, matric_input, request.form["course"], filename)
        )
        conn.commit()
        conn.close()

        message = "✅ Submitted!"

    return render_template("assignment.html", username=session["username"], message=message, csrf_token=generate_csrf_token())

# ------------------ SUBMISSIONS ------------------
@app.route("/submissions")
def submissions():
    if "username" not in session or session.get("role") != "teacher":
        return "Access Denied ❌"

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id, username, fullname, matric, course, filename FROM submissions")
    rows = cursor.fetchall()
    conn.close()

    submissions_list = [
    {
        "id": r["id"],
        "username": r["username"],
        "fullname": r["fullname"],
        "matric": r["matric"],
        "course": r["course"],
        "filename": r["filename"]
    }
    for r in rows
]
    return render_template("submissions.html", submissions=submissions_list, csrf_token=generate_csrf_token())

# ------------------ DOWNLOAD ALL SUBMISSIONS ------------------
@app.route("/download_all")
def download_all():
    if "username" not in session or session.get("role") != "teacher":
        return redirect("/login")

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT filename FROM submissions")
    files = cursor.fetchall()
    conn.close()

    if not files:
        return "No submissions to download ❌"

    memory_file = BytesIO()
    with zipfile.ZipFile(memory_file, 'w') as zf:
        for f in files:
            file_path = os.path.join(app.config["UPLOAD_FOLDER"], f[0])
            if os.path.exists(file_path):
                zf.write(file_path, arcname=f[0])
    memory_file.seek(0)

    return send_file(
        memory_file,
        mimetype='application/zip',
        download_name='all_submissions.zip',
        as_attachment=True
    )


# ------------------ DELETE ------------------
@app.route("/delete/<int:id>", methods=["POST"])
def delete_submission(id):
    if "username" not in session or session.get("role") != "teacher":
        return redirect("/login")
    validate_csrf()

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT filename FROM submissions WHERE id=%s", (id,))
    file = cursor.fetchone()
    if file and file[0]:
        file_path = os.path.join(app.config["UPLOAD_FOLDER"], file[0])
        if os.path.exists(file_path):
            os.remove(file_path)
    cursor.execute("DELETE FROM submissions WHERE id=%s", (id,))
    conn.commit()
    conn.close()
    return redirect("/submissions")

# ------------------ SERVE FILES ------------------
@app.route('/uploads/<path:filename>')
def uploads(filename):
    filename = os.path.basename(filename)
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)

# ------------------ RUN ------------------
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)