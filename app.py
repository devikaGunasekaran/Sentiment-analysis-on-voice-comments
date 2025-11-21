from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify
import mysql.connector
from mysql.connector import Error
import os
import io
import base64
from pv_graph import pv_graph 
from pv_process import pv_process
from threading import Thread
import threading
import uuid
import datetime
import traceback  # <-- import this
import google.generativeai as genai
from werkzeug.utils import secure_filename
import time

app = Flask(__name__)
app.secret_key = "supersecretkey"

UPLOAD_FOLDER = "uploads"
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# ---------------- CONFIGURATION ----------------
GEMINI_API_KEY = "AIzaSyANlEs76iicpzEfv4EUo3WQF5zmJRzcya8"
genai.configure(api_key=GEMINI_API_KEY)

db_config = {
    'host': 'localhost',
    'user': 'root',
    'password': '2025',  # your MySQL password
    'database': 'StudentVerificationDB'
}

def get_db_connection():
    try:
        conn = mysql.connector.connect(**db_config)
        return conn
    except Error as e:
        print(f"Error connecting to MySQL: {e}")
        return None

# ---------------- Helper: quick-safe DB wrapper ----------------
def fetchone_dict(query, params=()):
    conn = get_db_connection()
    if not conn:
        return None
    cursor = conn.cursor(dictionary=True)
    cursor.execute(query, params)
    row = cursor.fetchone()
    cursor.close()
    conn.close()
    return row

def fetchall_dict(query, params=()):
    conn = get_db_connection()
    if not conn:
        return []
    cursor = conn.cursor(dictionary=True)
    cursor.execute(query, params)
    rows = cursor.fetchall()
    cursor.close()
    conn.close()
    return rows

# ---------------- Login route ----------------
@app.route("/", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        volunteerId = request.form.get("volunteerId")
        password = request.form.get("password")

        conn = get_db_connection()
        if conn:
            cursor = conn.cursor(dictionary=True)
            query = "SELECT * FROM Volunteer WHERE volunteerId=%s AND password=%s"
            cursor.execute(query, (volunteerId, password))
            volunteer = cursor.fetchone()
            cursor.close()
            conn.close()

            if volunteer:
                session['volunteerId'] = volunteer['volunteerId']
                session['role'] = volunteer['role']
                flash("Login successful!", "success")
                
                # Redirect based on role
                if volunteer['role'] == 'pv':
                    return redirect(url_for('students_assign'))
                elif volunteer['role'] == 'admin':
                    return redirect(url_for('admin_assign'))
                else:
                    return redirect(url_for('volunteer_dashboard'))
            else:
                flash("Invalid credentials!", "danger")
                return redirect(url_for('login'))
    return render_template("login_page.html")


# ---------------- Volunteer pages & APIs ----------------
@app.route("/students-assign")
def students_assign():
    if 'volunteerId' not in session or session.get('role') != 'pv':
        flash("Unauthorized access!", "danger")
        return redirect(url_for('login'))
    return render_template("students_assign.html")  # PV HTML page

# Return only students assigned to this volunteer that are NOT finished (status NULL / PROCESSING)
@app.route("/api/assigned-students")
def api_assigned_students():
    if 'volunteerId' not in session or session.get('role') != 'pv':
        return jsonify({'error': 'Unauthorized'}), 401

    volunteerId = session['volunteerId']

    conn = get_db_connection()
    if not conn:
        return jsonify({'error': 'Database connection failed'}), 500

    cursor = conn.cursor(dictionary=True)
    # only show if pv.status IS NULL or pv.status = 'PROCESSING'
    query = """
        SELECT s.studentId, s.name AS studentName, s.phone AS phoneNumber, s.district, pv.status
        FROM PhysicalVerification pv
        JOIN Student s ON pv.studentId = s.studentId
        WHERE pv.volunteerId = %s AND (pv.status IS NULL OR pv.status = 'PROCESSING')
    """
    cursor.execute(query, (volunteerId,))
    students = cursor.fetchall()
    cursor.close()
    conn.close()

    return jsonify({'students': students})

@app.route("/student/<student_id>")
def student_details(student_id):
    if 'volunteerId' not in session or session.get('role') != 'pv':
        flash("Unauthorized access!", "danger")
        return redirect(url_for('login'))
    return render_template("students.html", studentId=student_id)

# Return full student details (for PV page)
@app.route("/api/student/<student_id>")
def api_student_details(student_id):
    if 'volunteerId' not in session or session.get('role') != 'pv':
        return jsonify({'error': 'Unauthorized'}), 401

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    cursor.execute("SELECT * FROM Student WHERE studentId=%s", (student_id,))
    student = cursor.fetchone()

    cursor.execute("SELECT * FROM Marks_10th WHERE studentId=%s", (student_id,))
    marks10 = cursor.fetchone()

    cursor.execute("SELECT * FROM Marks_12th WHERE studentId=%s", (student_id,))
    marks12 = cursor.fetchone()

    cursor.execute("""
        SELECT * FROM TeleVerification 
        WHERE studentId=%s ORDER BY verificationDate DESC LIMIT 1
    """, (student_id,))
    latest_tv = cursor.fetchone()

    cursor.execute("""
        SELECT * FROM PhysicalVerification
        WHERE studentId=%s ORDER BY verificationDate DESC LIMIT 1
    """, (student_id,))
    latest_pv = cursor.fetchone()

    cursor.close()
    conn.close()

    return jsonify({
        'student': student,
        'marks10': marks10,
        'marks12': marks12,
        'latest_tv': latest_tv,
        'latest_pv': latest_pv
    })

@app.route("/pv/<student_id>")
def pv_form(student_id):
    if 'volunteerId' not in session or session.get('role') != 'pv':
        flash("Unauthorized access!", "danger")
        return redirect(url_for('login'))
    return render_template("pv.html", studentId=student_id)

def run_pv_ai_pipeline(data, student_id, volunteer_id):
    try:
        import base64
        from pv_process import pv_process

        text_comment = data.get("comments", "")
        is_tanglish = data.get("isTanglish", False)
        audio_base64 = data.get("voiceAudio", "")

        # -----------------------------
        # HANDLE AUDIO FILE SAVING
        # -----------------------------
        audio_path = None
        if audio_base64:
            # Keep FULL base64 uri
            header, encoded = audio_base64.split(",", 1)

            encoded += "=" * ((4 - len(encoded) % 4) % 4)
            audio_bytes = base64.b64decode(encoded)

            audio_path = os.path.join(UPLOAD_FOLDER, f"{student_id}.wav")
            with open(audio_path, "wb") as f:
                f.write(audio_bytes)

        # -----------------------------
        # RUN AI PIPELINE
        # -----------------------------
        result = pv_process(text_comment, audio_path, is_tanglish)

        english_comment = result.get("english_comment", "")
        voice_text = result.get("voice_text", "")

        summary_list = result.get("summary", [])
        decision = result.get("decision", "ON HOLD")
        score = float(result.get("score", 0.0))

        summary_text = "; ".join(summary_list)

        # -----------------------------
        # UPDATE DATABASE ONLY
        # -----------------------------
        conn = mysql.connector.connect(
            host="localhost",
            user="root",
            password="2025",
            database="StudentVerificationDB"
        )
        cursor = conn.cursor()

        cursor.execute("""
            UPDATE PhysicalVerification
            SET 
                comment=%s,
                elementsSummary=%s,
                sentiment=%s,
                sentiment_text=%s,
                voice_comments=%s,
                verificationDate=NOW()
            WHERE studentId=%s AND volunteerId=%s
        """, (
            english_comment,
            summary_text,
            decision,
            score,
            voice_text,
            student_id,
            volunteer_id
        ))

        conn.commit()

        # -----------------------------
        # ALSO UPDATE STUDENT TABLE
        # -----------------------------
        selected_flag = 1 if decision == "SELECT" else 0

        cursor.execute("""
            UPDATE student
            SET status=%s, selected=%s
            WHERE studentId=%s
        """, (decision, selected_flag, student_id))

        conn.commit()
        cursor.close()
        conn.close()

        print("✅ PV async AI pipeline finished successfully.")

    except Exception as e:
        print("❌ ASYNC AI PIPELINE ERROR:", e)
        traceback.print_exc()
        
@app.route("/submit-pv", methods=["POST"])
def submit_pv():
    try:
        data = request.json
        student_id = data.get("studentId")
        volunteer_id = session.get("volunteerId")

        if not student_id or not volunteer_id:
            return jsonify({"success": False, "message": "Missing IDs"}), 400

        conn = mysql.connector.connect(
            host="localhost",
            user="root",
            password="2025",
            database="StudentVerificationDB"
        )
        cursor = conn.cursor()

        # UPDATE only — no insert
        cursor.execute("""
            UPDATE PhysicalVerification
            SET 
                propertyType=%s,
                whatYouSaw=%s,
                status=%s
            WHERE studentId=%s AND volunteerId=%s
        """, (
            data.get("propertyType"),
            data.get("whatYouSaw"),
            data.get("recommendation"),
            student_id,
            volunteer_id
        ))

        conn.commit()
        cursor.close()
        conn.close()

        # START AI in background
        threading.Thread(
            target=run_pv_ai_pipeline, 
            args=(data, student_id, volunteer_id),
            daemon=True
        ).start()

        return jsonify({"success": True, "message": "PV Updated. AI running."})

    except Exception as e:
        print("❌ Error in /submit-pv:", e)
        traceback.print_exc()
        return jsonify({"success": False, "message": str(e)}), 500

# ---------------- PV status API (for polling) ----------------
@app.route("/api/pv-status/<student_id>")
def api_pv_status(student_id):
    # returns latest pv row for this student
    if 'volunteerId' not in session:
        return jsonify({'error': 'Unauthorized'}), 401

    row = fetchone_dict("""
        SELECT studentId, volunteerId, status, elementsSummary, sentiment_text, voice_comments
        FROM PhysicalVerification
        WHERE studentId = %s
        ORDER BY verificationDate DESC LIMIT 1
    """, (student_id,))

    if not row:
        return jsonify({'exists': False, 'pv': None})
    return jsonify({'exists': True, 'pv': row})


# ---------------- admin_assign: show only pending students ----------------
@app.route("/admin/assign")
def admin_assign():
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)

        # Fetch students whose PV is done but final decision not given
        cursor.execute("""
            SELECT 
                s.studentId,
                s.name,
                s.district,
                s.status,
                pv.comment,
                pv.elementsSummary,
                pv.sentiment_text,
                pv.status AS pv_status
            FROM student s
            INNER JOIN PhysicalVerification pv 
                ON s.studentId = pv.studentId
            WHERE 
                (s.status IS NULL OR s.status = 'PENDING')
                AND pv.status IS NOT NULL
        """)

        students = cursor.fetchall()

        cursor.close()
        conn.close()

        return render_template("admin_assign.html", students=students)

    except Exception as e:
        print("Error loading admin_assign:", e)
        return "Error loading page"


# API for admin page to fetch pending students (for auto-refresh)
@app.route("/api/admin/pending-students")
def api_admin_pending_students():
    # require admin role
    if 'role' not in session or session.get('role') != 'admin':
        return jsonify({'error': 'Unauthorized'}), 401

    rows = fetchall_dict("""
        SELECT 
            s.studentId,
            s.name,
            s.district,
            s.status,
            pv.comment,
            pv.elementsSummary,
            pv.sentiment_text,
            pv.status AS pv_status
        FROM student s
        INNER JOIN PhysicalVerification pv 
            ON s.studentId = pv.studentId
        WHERE 
            (s.status IS NULL OR s.status = 'PENDING')
            AND pv.status IS NOT NULL
    """)
    return jsonify({'students': rows})


# ---------------- admin_decision: show one student with PV & TV ----------------
@app.route("/admin/decision/<student_id>")
def admin_decision(student_id):
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True, buffered=True)

        # fetch student row
        cursor.execute("SELECT * FROM student WHERE studentId = %s", (student_id,))
        student = cursor.fetchone()

        # fetch physical verification row
        cursor.execute("SELECT * FROM PhysicalVerification WHERE studentId = %s ORDER BY verificationDate DESC LIMIT 1", (student_id,))
        pv = cursor.fetchone()

        # fetch tele verification row
        cursor.execute("SELECT * FROM TeleVerification WHERE studentId = %s ORDER BY verificationDate DESC LIMIT 1", (student_id,))
        tv = cursor.fetchone()

        cursor.close()
        conn.close()

        if not student:
            flash("Student not found", "danger")
            return redirect(url_for("admin_assign"))

        return render_template("admin_view.html",
                                student=student,
                                pv=pv,
                                tv=tv)

    except Exception as e:
        print("Error in admin_decision:", e)
        return "Something went wrong.", 500


# ---------------- admin_update_status: POST to update final decision ----------------
@app.route("/admin/final_status_update/<student_id>", methods=["POST"])
def final_status_update(student_id):
    admin_status = request.form.get("admin_status")

    print("Received admin status:", admin_status)

    if not admin_status:
        flash("Please select a final decision!", "danger")
        return redirect(url_for("admin_view", student_id=student_id))

    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute("""
            UPDATE student 
            SET status = %s 
            WHERE studentId = %s
        """, (admin_status, student_id))

        conn.commit()
        cursor.close()
        conn.close()

        flash("Final decision saved successfully!", "success")
        return redirect(url_for("admin_assign"))

    except Exception as e:
        print("Error in final_status_update:", e)
        flash("Error saving decision!", "danger")
        return redirect(url_for("admin_decision", student_id=student_id))

@app.route("/logout")
def logout():
    # Clear all session data
    session.clear()

    # Redirect to login page
    return redirect(url_for("login"))

if __name__ == "__main__":
    app.run(debug=True)

