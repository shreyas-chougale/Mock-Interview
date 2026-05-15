import json
import os
import re
import sys

import psycopg
from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS
import google.generativeai as genai

try:
    from dotenv import load_dotenv
    load_dotenv()
except:
    pass


# =========================================================
# CONFIG
# =========================================================

DATABASE_URL = os.environ.get("DATABASE_URL")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

if not DATABASE_URL:
    print("DATABASE_URL missing")
    sys.exit(1)

if not GEMINI_API_KEY:
    print("GEMINI_API_KEY missing")
    sys.exit(1)

genai.configure(api_key=GEMINI_API_KEY)

gemini_model = genai.GenerativeModel("gemini-2.0-flash")

PORT = int(os.environ.get("PORT", 5000))
HOST = "0.0.0.0"

FRONTEND_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "frontend")
)


# =========================================================
# DATABASE
# =========================================================

def get_conn():
    return psycopg.connect(
        DATABASE_URL,
        sslmode="require",
        autocommit=False
    )


def init_db():

    schema = """
    CREATE TABLE IF NOT EXISTS interview_sessions (
        id SERIAL PRIMARY KEY,
        name TEXT NOT NULL,
        role TEXT NOT NULL,
        experience_level TEXT NOT NULL,
        status TEXT DEFAULT 'in_progress',
        created_at TIMESTAMP DEFAULT NOW()
    );

    CREATE TABLE IF NOT EXISTS interview_questions (
        id SERIAL PRIMARY KEY,
        session_id INTEGER REFERENCES interview_sessions(id) ON DELETE CASCADE,
        question TEXT NOT NULL,
        category TEXT NOT NULL,
        "order" INTEGER NOT NULL
    );

    CREATE TABLE IF NOT EXISTS interview_answers (
        id SERIAL PRIMARY KEY,
        session_id INTEGER REFERENCES interview_sessions(id) ON DELETE CASCADE,
        question_id INTEGER REFERENCES interview_questions(id) ON DELETE CASCADE,
        answer TEXT NOT NULL,
        score INTEGER NOT NULL,
        feedback TEXT NOT NULL,
        improved_answer TEXT NOT NULL,
        created_at TIMESTAMP DEFAULT NOW()
    );
    """

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(schema)

        conn.commit()


# =========================================================
# GEMINI
# =========================================================

def ai_complete(prompt, max_tokens=1024):

    try:

        response = gemini_model.generate_content(
            prompt,
            generation_config=genai.types.GenerationConfig(
                max_output_tokens=max_tokens,
                temperature=0.7
            )
        )

        if response.text:
            return response.text

        return ""

    except Exception as e:
        print("Gemini Error:", e)
        raise Exception(str(e))


def safe_json_parse(text, fallback):

    try:

        cleaned = text.strip()

        cleaned = cleaned.replace("```json", "")
        cleaned = cleaned.replace("```", "")

        match = re.search(r"\[.*\]", cleaned, re.S)

        if match:
            return json.loads(match.group())

        return fallback

    except:
        return fallback


# =========================================================
# APP
# =========================================================

app = Flask(__name__, static_folder=None)

CORS(app)

app.config["JSON_SORT_KEYS"] = False


# =========================================================
# STATIC
# =========================================================

@app.route("/")
def index():
    return send_from_directory(FRONTEND_DIR, "index.html")


@app.route("/<path:filename>")
def static_files(filename):

    if filename.startswith("api/"):
        return jsonify({"error": "Not found"}), 404

    full = os.path.join(FRONTEND_DIR, filename)

    if os.path.isfile(full):
        return send_from_directory(FRONTEND_DIR, filename)

    return send_from_directory(FRONTEND_DIR, "index.html")


# =========================================================
# HEALTH
# =========================================================

@app.route("/api/healthz")
def health():
    return jsonify({"status": "ok"})


# =========================================================
# CREATE SESSION
# =========================================================

@app.route("/api/interview/sessions", methods=["POST"])
def create_session():

    body = request.get_json() or {}

    name = body.get("name", "").strip()
    role = body.get("role", "").strip()
    level = body.get("experienceLevel")

    if not name or not role:
        return jsonify({"error": "Missing fields"}), 400

    prompt = f"""
Generate 5 interview questions for a {level} {role}.

Return ONLY JSON array.

Example:
[
 {{
   "question":"What is React?",
   "category":"Technical"
 }}
]
"""

    fallback_questions = [
        {
            "question": f"Tell me about yourself as a {role}.",
            "category": "Behavioral"
        },
        {
            "question": "What are your strengths?",
            "category": "Behavioral"
        },
        {
            "question": "Explain a challenging project.",
            "category": "Technical"
        },
        {
            "question": "How do you solve problems?",
            "category": "Problem Solving"
        },
        {
            "question": "Why should we hire you?",
            "category": "Communication"
        }
    ]

    try:
        ai_text = ai_complete(prompt, 2048)
        questions = safe_json_parse(ai_text, fallback_questions)

    except:
        questions = fallback_questions

    try:

        with get_conn() as conn:

            with conn.cursor() as cur:

                cur.execute("""
                    INSERT INTO interview_sessions
                    (name, role, experience_level)
                    VALUES (%s,%s,%s)
                    RETURNING id,status,created_at
                """, (name, role, level))

                row = cur.fetchone()

                session_id = row[0]
                status = row[1]
                created_at = row[2]

                saved_questions = []

                for idx, q in enumerate(questions):

                    cur.execute("""
                        INSERT INTO interview_questions
                        (session_id,question,category,"order")
                        VALUES (%s,%s,%s,%s)
                        RETURNING id
                    """, (
                        session_id,
                        q["question"],
                        q["category"],
                        idx + 1
                    ))

                    qid = cur.fetchone()[0]

                    saved_questions.append({
                        "id": qid,
                        "sessionId": session_id,
                        "question": q["question"],
                        "category": q["category"],
                        "order": idx + 1
                    })

            conn.commit()

        return jsonify({
            "id": session_id,
            "name": name,
            "role": role,
            "experienceLevel": level,
            "status": status,
            "questions": saved_questions,
            "answers": [],
            "createdAt": created_at.isoformat()
        }), 201

    except Exception as e:
        print(e)
        return jsonify({"error": "DB error"}), 500


# =========================================================
# GET SESSION
# =========================================================

@app.route("/api/interview/sessions/<int:session_id>")
def get_session(session_id):

    try:

        with get_conn() as conn:

            with conn.cursor() as cur:

                cur.execute("""
                    SELECT *
                    FROM interview_sessions
                    WHERE id=%s
                """, (session_id,))

                session = cur.fetchone()

                if not session:
                    return jsonify({"error": "Not found"}), 404

                cur.execute("""
                    SELECT id,question,category,"order"
                    FROM interview_questions
                    WHERE session_id=%s
                    ORDER BY "order"
                """, (session_id,))

                questions = []

                for q in cur.fetchall():

                    questions.append({
                        "id": q[0],
                        "sessionId": session_id,
                        "question": q[1],
                        "category": q[2],
                        "order": q[3]
                    })

                cur.execute("""
                    SELECT id,question_id,answer,
                           score,feedback,
                           improved_answer,
                           created_at
                    FROM interview_answers
                    WHERE session_id=%s
                """, (session_id,))

                answers = []

                for a in cur.fetchall():

                    answers.append({
                        "id": a[0],
                        "sessionId": session_id,
                        "questionId": a[1],
                        "answer": a[2],
                        "score": a[3],
                        "feedback": a[4],
                        "improvedAnswer": a[5],
                        "createdAt": a[6].isoformat()
                    })

        return jsonify({
            "id": session[0],
            "name": session[1],
            "role": session[2],
            "experienceLevel": session[3],
            "status": session[4],
            "questions": questions,
            "answers": answers,
            "createdAt": session[5].isoformat()
        })

    except Exception as e:
        print(e)
        return jsonify({"error": "Fetch failed"}), 500


# =========================================================
# SUBMIT ANSWER
# =========================================================

@app.route("/api/interview/sessions/<int:session_id>/answers", methods=["POST"])
def submit_answer(session_id):

    try:

        body = request.get_json()

        question_id = body.get("questionId")
        answer = body.get("answer", "").strip()

        if not answer:
            return jsonify({"error": "Answer required"}), 400

        with get_conn() as conn:

            with conn.cursor() as cur:

                cur.execute("""
                    SELECT question
                    FROM interview_questions
                    WHERE id=%s
                """, (question_id,))

                q = cur.fetchone()

                if not q:
                    return jsonify({"error": "Question not found"}), 404

                question_text = q[0]

                # ====================================
                # AI EVALUATION
                # ====================================

                try:

                    prompt = f"""
Question:
{question_text}

Candidate Answer:
{answer}

Evaluate this answer.

Return ONLY JSON:

{{
 "score":8,
 "feedback":"Good answer",
 "improvedAnswer":"Better structured answer"
}}
"""

                    ai_text = ai_complete(prompt)

                    cleaned = ai_text.replace("```json", "").replace("```", "")

                    match = re.search(r"\{.*\}", cleaned, re.S)

                    if match:
                        result = json.loads(match.group())
                    else:
                        raise Exception("Invalid JSON")

                    score = int(result.get("score", 6))
                    feedback = result.get("feedback", "Good attempt.")
                    improved = result.get(
                        "improvedAnswer",
                        "Could be improved."
                    )

                except Exception as e:

                    print("Evaluation fallback:", e)

                    score = 6
                    feedback = "Answer submitted successfully."
                    improved = "Add more technical depth and examples."

                cur.execute("""
                    INSERT INTO interview_answers
                    (
                        session_id,
                        question_id,
                        answer,
                        score,
                        feedback,
                        improved_answer
                    )
                    VALUES (%s,%s,%s,%s,%s,%s)
                    RETURNING id
                """, (
                    session_id,
                    question_id,
                    answer,
                    score,
                    feedback,
                    improved
                ))

                answer_id = cur.fetchone()[0]

            conn.commit()

        return jsonify({
            "answerId": answer_id,
            "score": score,
            "feedback": feedback,
            "improvedAnswer": improved
        })

    except Exception as e:
        print(e)

        return jsonify({
            "error": str(e)
        }), 500


# =========================================================
# COMPLETE SESSION
# =========================================================

@app.route("/api/interview/sessions/<int:session_id>/complete", methods=["POST"])
def complete_session(session_id):

    try:

        with get_conn() as conn:

            with conn.cursor() as cur:

                cur.execute("""
                    SELECT answer,score,
                           feedback,
                           improved_answer
                    FROM interview_answers
                    WHERE session_id=%s
                """, (session_id,))

                rows = cur.fetchall()

                if not rows:
                    return jsonify({"error": "No answers"}), 400

                answers = []

                total_score = 0

                for r in rows:

                    total_score += r[1]

                    answers.append({
                        "answer": r[0],
                        "score": r[1],
                        "feedback": r[2],
                        "improvedAnswer": r[3]
                    })

                max_score = len(rows) * 10

                percentage = round((total_score / max_score) * 100)

                overall_feedback = (
                    "Excellent performance!"
                    if percentage >= 80 else
                    "Good performance with improvement areas."
                    if percentage >= 60 else
                    "Needs more practice."
                )

                strengths = [
                    "Communication",
                    "Problem solving",
                    "Technical understanding"
                ]

                weaknesses = [
                    "Answer structure",
                    "Technical depth"
                ]

                cur.execute("""
                    UPDATE interview_sessions
                    SET status='completed'
                    WHERE id=%s
                """, (session_id,))

            conn.commit()

        return jsonify({
            "totalScore": total_score,
            "maxScore": max_score,
            "percentage": percentage,
            "overallFeedback": overall_feedback,
            "strengths": strengths,
            "weaknesses": weaknesses,
            "answers": answers
        })

    except Exception as e:
        print(e)

        return jsonify({
            "error": "Completion failed"
        }), 500


# =========================================================
# STARTUP
# =========================================================

try:
    init_db()
    print("Database initialized successfully.")

except Exception as e:
    print("DB INIT ERROR:", e)


# =========================================================
# RUN
# =========================================================

if __name__ == "__main__":

    app.run(
        host=HOST,
        port=PORT,
        debug=False,
        threaded=True
    )
