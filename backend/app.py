"""
PrepForge - AI Mock Interview backend (Flask + PostgreSQL + Google Gemini)

Run:
    python app.py

Environment variables required:
    DATABASE_URL          - PostgreSQL connection string
    GEMINI_API_KEY        - your Google Gemini API key

Optional:
    PORT                  - port to bind (default 5000)
"""

import json
import os
import sys

import psycopg
from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS
import google.generativeai as genai

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass


# ─────────────────────────────────────────────────────────────
# Config
# ─────────────────────────────────────────────────────────────

DATABASE_URL = os.environ.get("DATABASE_URL")

if not DATABASE_URL:
    print("ERROR: DATABASE_URL environment variable is required.", file=sys.stderr)
    sys.exit(1)

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

if not GEMINI_API_KEY:
    print("ERROR: GEMINI_API_KEY environment variable is required.", file=sys.stderr)
    sys.exit(1)

# Configure Gemini
genai.configure(api_key=GEMINI_API_KEY)

# Stable model
gemini_model = genai.GenerativeModel("gemini-2.0-flash")

PORT = int(os.environ.get("PORT", "5000"))
HOST = os.environ.get("HOST", "0.0.0.0")

# Frontend folder
FRONTEND_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "frontend")
)


# ─────────────────────────────────────────────────────────────
# Database
# ─────────────────────────────────────────────────────────────

def get_conn():
    return psycopg.connect(
        DATABASE_URL,
        autocommit=False,
        sslmode="require",
        connect_timeout=10
    )


def init_db():
    """
    Create required PostgreSQL enums + tables.
    """

    schema = """
    DO $$ BEGIN
        CREATE TYPE experience_level AS ENUM ('junior', 'mid', 'senior', 'lead');
    EXCEPTION WHEN duplicate_object THEN NULL;
    END $$;

    DO $$ BEGIN
        CREATE TYPE session_status AS ENUM ('in_progress', 'completed');
    EXCEPTION WHEN duplicate_object THEN NULL;
    END $$;

    CREATE TABLE IF NOT EXISTS interview_sessions (
        id SERIAL PRIMARY KEY,
        name TEXT NOT NULL,
        role TEXT NOT NULL,
        experience_level experience_level NOT NULL,
        status session_status NOT NULL DEFAULT 'in_progress',
        created_at TIMESTAMP NOT NULL DEFAULT NOW()
    );

    CREATE TABLE IF NOT EXISTS interview_questions (
        id SERIAL PRIMARY KEY,
        session_id INTEGER NOT NULL REFERENCES interview_sessions(id) ON DELETE CASCADE,
        question TEXT NOT NULL,
        category TEXT NOT NULL,
        "order" INTEGER NOT NULL
    );

    CREATE TABLE IF NOT EXISTS interview_answers (
        id SERIAL PRIMARY KEY,
        session_id INTEGER NOT NULL REFERENCES interview_sessions(id) ON DELETE CASCADE,
        question_id INTEGER NOT NULL REFERENCES interview_questions(id) ON DELETE CASCADE,
        answer TEXT NOT NULL,
        score INTEGER NOT NULL,
        feedback TEXT NOT NULL,
        improved_answer TEXT NOT NULL,
        created_at TIMESTAMP NOT NULL DEFAULT NOW()
    );
    """

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(schema)

        conn.commit()


# ─────────────────────────────────────────────────────────────
# Gemini Helpers
# ─────────────────────────────────────────────────────────────

def ai_complete(prompt: str, max_tokens: int = 1024) -> str:
    """
    Generate text using Gemini.
    """

    try:
        response = gemini_model.generate_content(
            prompt,
            generation_config=genai.types.GenerationConfig(
                max_output_tokens=max_tokens,
                temperature=0.7,
            ),
            request_options={"timeout": 30},
        )

        if hasattr(response, "text") and response.text:
            return response.text

        return ""

    except Exception as e:
        raise Exception(f"Gemini API error: {str(e)}") from e


def safe_json_parse(text, fallback):
    """
    Parse Gemini JSON safely.
    """

    try:
        cleaned = text.strip()

        if cleaned.startswith("```json"):
            cleaned = cleaned.replace("```json", "").replace("```", "").strip()

        elif cleaned.startswith("```"):
            cleaned = cleaned.replace("```", "").strip()

        return json.loads(cleaned)

    except Exception:
        return fallback


# ─────────────────────────────────────────────────────────────
# Flask App
# ─────────────────────────────────────────────────────────────

app = Flask(__name__, static_folder=None)

CORS(app)

app.config["JSON_SORT_KEYS"] = False
app.config["JSONIFY_PRETTYPRINT_REGULAR"] = False


# ─────────────────────────────────────────────────────────────
# Frontend Serving
# ─────────────────────────────────────────────────────────────

@app.route("/")
def serve_index():
    return send_from_directory(FRONTEND_DIR, "index.html")


@app.route("/<path:filename>")
def serve_static(filename):

    # Prevent API conflicts
    if filename.startswith("api/"):
        return jsonify({"error": "API route not found"}), 404

    full_path = os.path.join(FRONTEND_DIR, filename)

    if os.path.isfile(full_path):
        return send_from_directory(FRONTEND_DIR, filename)

    # React/Vite/SPA fallback
    return send_from_directory(FRONTEND_DIR, "index.html")


# ─────────────────────────────────────────────────────────────
# Health Check
# ─────────────────────────────────────────────────────────────

@app.route("/api/healthz")
def health():
    return jsonify({"status": "ok"})


# ─────────────────────────────────────────────────────────────
# Create Interview Session
# ─────────────────────────────────────────────────────────────

@app.route("/api/interview/sessions", methods=["POST"])
def create_session():

    body = request.get_json(silent=True) or {}

    name = (body.get("name") or "").strip()
    role = (body.get("role") or "").strip()
    experience_level = body.get("experienceLevel")

    if not name or not role or experience_level not in (
        "junior",
        "mid",
        "senior",
        "lead",
    ):
        return jsonify({
            "error": "Invalid request: name, role and experienceLevel are required."
        }), 400

    prompt = f"""
You are an expert interviewer.

Generate exactly 5 interview questions for a
{experience_level}-level {role} candidate named {name}.

Return ONLY a JSON array.

Each object must contain:
- question
- category

Allowed categories:
Technical
Behavioral
Problem Solving
System Design
Communication
"""

    try:
        ai_text = ai_complete(prompt, max_tokens=2048)

    except Exception as e:
        app.logger.exception("AI question generation failed: %s", e)
        ai_text = "[]"

    fallback_questions = [
        {
            "question": f"Tell me about yourself as a {role}.",
            "category": "Behavioral",
        },
        {
            "question": f"What are the core technical skills required for a {role}?",
            "category": "Technical",
        },
        {
            "question": "Describe a difficult challenge you solved.",
            "category": "Problem Solving",
        },
        {
            "question": "How do you stay updated with technology?",
            "category": "Communication",
        },
        {
            "question": "Where do you see yourself in 5 years?",
            "category": "Behavioral",
        },
    ]

    questions = safe_json_parse(ai_text, fallback_questions)

    if not isinstance(questions, list) or len(questions) == 0:
        questions = fallback_questions

    try:

        with get_conn() as conn:

            with conn.cursor() as cur:

                cur.execute(
                    """
                    INSERT INTO interview_sessions
                    (name, role, experience_level)
                    VALUES (%s, %s, %s)
                    RETURNING id, status, created_at
                    """,
                    (name, role, experience_level),
                )

                row = cur.fetchone()

                session_id, status, created_at = row

                saved_questions = []

                for idx, q in enumerate(questions):

                    cur.execute(
                        """
                        INSERT INTO interview_questions
                        (session_id, question, category, "order")
                        VALUES (%s, %s, %s, %s)
                        RETURNING id
                        """,
                        (
                            session_id,
                            q.get("question", ""),
                            q.get("category", "Technical"),
                            idx + 1,
                        ),
                    )

                    qid = cur.fetchone()[0]

                    saved_questions.append({
                        "id": qid,
                        "sessionId": session_id,
                        "question": q.get("question", ""),
                        "category": q.get("category", "Technical"),
                        "order": idx + 1,
                    })

            conn.commit()

    except Exception as e:
        app.logger.exception("DB insert failed: %s", e)

        return jsonify({
            "error": "Failed to create interview session"
        }), 500

    return jsonify({
        "id": session_id,
        "name": name,
        "role": role,
        "experienceLevel": experience_level,
        "status": status,
        "questions": saved_questions,
        "answers": [],
        "createdAt": created_at.isoformat(),
    }), 201


# ─────────────────────────────────────────────────────────────
# Get Session
# ─────────────────────────────────────────────────────────────

@app.route("/api/interview/sessions/<int:session_id>", methods=["GET"])
def get_session(session_id):

    try:

        with get_conn() as conn:

            with conn.cursor() as cur:

                cur.execute(
                    """
                    SELECT id, name, role, experience_level,
                           status, created_at
                    FROM interview_sessions
                    WHERE id = %s
                    """,
                    (session_id,),
                )

                session = cur.fetchone()

                if not session:
                    return jsonify({"error": "Session not found"}), 404

                cur.execute(
                    """
                    SELECT id, session_id, question,
                           category, "order"
                    FROM interview_questions
                    WHERE session_id = %s
                    ORDER BY "order"
                    """,
                    (session_id,),
                )

                questions = [
                    {
                        "id": r[0],
                        "sessionId": r[1],
                        "question": r[2],
                        "category": r[3],
                        "order": r[4],
                    }
                    for r in cur.fetchall()
                ]

                cur.execute(
                    """
                    SELECT id, session_id, question_id,
                           answer, score, feedback,
                           improved_answer, created_at
                    FROM interview_answers
                    WHERE session_id = %s
                    """,
                    (session_id,),
                )

                answers = [
                    {
                        "id": r[0],
                        "sessionId": r[1],
                        "questionId": r[2],
                        "answer": r[3],
                        "score": r[4],
                        "feedback": r[5],
                        "improvedAnswer": r[6],
                        "createdAt": r[7].isoformat(),
                    }
                    for r in cur.fetchall()
                ]

        return jsonify({
            "id": session[0],
            "name": session[1],
            "role": session[2],
            "experienceLevel": session[3],
            "status": session[4],
            "questions": questions,
            "answers": answers,
            "createdAt": session[5].isoformat(),
        })

    except Exception as e:
        app.logger.exception("Fetch session failed: %s", e)

        return jsonify({
            "error": "Failed to fetch session"
        }), 500


# ─────────────────────────────────────────────────────────────
# Startup
# ─────────────────────────────────────────────────────────────

try:
    init_db()
    print("Database initialized successfully.")

except Exception as e:
    print(f"Database initialization failed: {e}")


# ─────────────────────────────────────────────────────────────
# Run App
# ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    app.run(
        host=HOST,
        port=PORT,
        debug=False,
        threaded=True,
    )
````
