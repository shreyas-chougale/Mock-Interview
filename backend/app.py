"""
PrepForge - AI Mock Interview backend (Flask + PostgreSQL + Google Gemini).

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


# ─── Config ──────────────────────────────────────────────────────────────────

DATABASE_URL = os.environ.get("DATABASE_URL")
if not DATABASE_URL:
    print("ERROR: DATABASE_URL environment variable is required.", file=sys.stderr)
    sys.exit(1)

# Gemini API configuration
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
if not GEMINI_API_KEY:
    print(
        "ERROR: GEMINI_API_KEY is required.",
        file=sys.stderr,
    )
    sys.exit(1)

genai.configure(api_key=GEMINI_API_KEY)
gemini_model = genai.GenerativeModel("gemini-2.0-flash")

PORT = int(os.environ.get("PORT", "5000"))
HOST = os.environ.get("HOST", "0.0.0.0")

# Frontend lives one level up from backend/
FRONTEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "frontend"))


# ─── Database ────────────────────────────────────────────────────────────────

def get_conn():
    return psycopg.connect(
        DATABASE_URL,
        autocommit=False,
        sslmode="require"
    )


def init_db():
    """Create tables and enums if they don't already exist."""
    schema = """
    DO $$ BEGIN
        CREATE TYPE experience_level AS ENUM ('junior', 'mid', 'senior', 'lead');
    EXCEPTION WHEN duplicate_object THEN NULL; END $$;

    DO $$ BEGIN
        CREATE TYPE session_status AS ENUM ('in_progress', 'completed');
    EXCEPTION WHEN duplicate_object THEN NULL; END $$;

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
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(schema)
        conn.commit()


# ─── Gemini helpers ──────────────────────────────────────────────────────────

def ai_complete(prompt: str, max_tokens: int = 1024) -> str:
    """Call Gemini API and return raw text."""
    try:
response = gemini_model.generate_content(
    prompt,
    generation_config=genai.types.GenerationConfig(
        max_output_tokens=max_tokens,
        temperature=0.7,
    ),
    request_options={"timeout": 30}
)
        return response.text or ""
    except Exception as e:
        raise Exception(f"Gemini API error: {str(e)}") from e


def safe_json_parse(text: str, fallback):
    try:
        cleaned = text.strip()

        if cleaned.startswith("```json"):
            cleaned = cleaned.replace("```json", "").replace("```", "").strip()

        elif cleaned.startswith("```"):
            cleaned = cleaned.replace("```", "").strip()

        return json.loads(cleaned)

    except Exception:
        return fallback


# ─── Flask app ───────────────────────────────────────────────────────────────

app = Flask(__name__, static_folder=None)
CORS(app)

app.config["JSON_SORT_KEYS"] = False


# ─── Static frontend ─────────────────────────────────────────────────────────

@app.route("/")
def serve_index():
    return send_from_directory(FRONTEND_DIR, "index.html")


@app.route("/<path:filename>")
def serve_static(filename):
    # API routes are handled below; this only serves frontend files
    full_path = os.path.join(FRONTEND_DIR, filename)
    if os.path.isfile(full_path):
        return send_from_directory(FRONTEND_DIR, filename)
    # Fallback to index.html for client-side routing
    return send_from_directory(FRONTEND_DIR, "index.html")


# ─── Health ──────────────────────────────────────────────────────────────────

@app.route("/api/healthz")
def health():
    return jsonify({"status": "ok"})


# ─── POST /api/interview/sessions — Create session + generate questions ──────

@app.route("/api/interview/sessions", methods=["POST"])
def create_session():
    body = request.get_json(silent=True) or {}
    name = (body.get("name") or "").strip()
    role = (body.get("role") or "").strip()
    experience_level = body.get("experienceLevel")

    if not name or not role or experience_level not in ("junior", "mid", "senior", "lead"):
        return jsonify({"error": "Invalid request: name, role and experienceLevel are required."}), 400

    # Generate 5 tailored questions via AI
    prompt = f"""You are an expert interviewer. Generate exactly 5 interview questions for a {experience_level}-level {role} candidate named {name}.

Return a JSON array with exactly 5 objects. Each object must have:
- "question": the interview question text
- "category": one of "Technical", "Behavioral", "Problem Solving", "System Design", or "Communication"

Mix different categories appropriately for the role and level.
Return ONLY the JSON array, no other text."""

    try:
        ai_text = ai_complete(prompt, max_tokens=2048)
    except Exception as e:
        app.logger.exception("AI question generation failed: %s", e)
        ai_text = "[]"

    fallback_questions = [
        {"question": f"Tell me about your experience as a {role}.", "category": "Behavioral"},
        {"question": f"What are the key technical skills for a {role}?", "category": "Technical"},
        {"question": "Describe a challenging project you worked on.", "category": "Problem Solving"},
        {"question": "How do you stay updated with industry trends?", "category": "Communication"},
        {"question": "Where do you see yourself in 5 years?", "category": "Behavioral"},
    ]
    questions = safe_json_parse(ai_text, fallback_questions)
    if not isinstance(questions, list) or len(questions) == 0:
        questions = fallback_questions

    try:
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO interview_sessions (name, role, experience_level) "
                "VALUES (%s, %s, %s) RETURNING id, status, created_at",
                (name, role, experience_level),
            )
            row = cur.fetchone()
            session_id, status, created_at = row

            saved_questions = []
            for idx, q in enumerate(questions):
                cur.execute(
                    'INSERT INTO interview_questions (session_id, question, category, "order") '
                    'VALUES (%s, %s, %s, %s) RETURNING id',
                    (session_id, q.get("question", ""), q.get("category", "Technical"), idx + 1),
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
        return jsonify({"error": "Failed to create interview session"}), 500

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


# ─── GET /api/interview/sessions/<id> ────────────────────────────────────────

@app.route("/api/interview/sessions/<int:session_id>", methods=["GET"])
def get_session(session_id):
    try:
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT id, name, role, experience_level, status, created_at "
                "FROM interview_sessions WHERE id = %s",
                (session_id,),
            )
            session = cur.fetchone()
            if not session:
                return jsonify({"error": "Session not found"}), 404

            cur.execute(
                'SELECT id, session_id, question, category, "order" '
                'FROM interview_questions WHERE session_id = %s ORDER BY "order"',
                (session_id,),
            )
            questions = [
                {"id": r[0], "sessionId": r[1], "question": r[2], "category": r[3], "order": r[4]}
                for r in cur.fetchall()
            ]

            cur.execute(
                "SELECT id, session_id, question_id, answer, score, feedback, improved_answer, created_at "
                "FROM interview_answers WHERE session_id = %s",
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
        return jsonify({"error": "Failed to fetch session"}), 500


# ─── POST /api/interview/sessions/<id>/answers — Submit answer + AI eval ─────

@app.route("/api/interview/sessions/<int:session_id>/answers", methods=["POST"])
def submit_answer(session_id):
    body = request.get_json(silent=True) or {}
    question_id = body.get("questionId")
    answer = (body.get("answer") or "").strip()

    if not isinstance(question_id, int) or not answer:
        return jsonify({"error": "questionId (int) and answer (string) are required."}), 400

    try:
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT role, experience_level FROM interview_sessions WHERE id = %s",
                (session_id,),
            )
            sess = cur.fetchone()
            if not sess:
                return jsonify({"error": "Session not found"}), 404
            role, experience_level = sess

            cur.execute(
                "SELECT question, category FROM interview_questions WHERE id = %s AND session_id = %s",
                (question_id, session_id),
            )
            q = cur.fetchone()
            if not q:
                return jsonify({"error": "Question not found"}), 404
            question_text, category = q

        eval_prompt = f"""You are an expert interviewer evaluating a candidate's answer for a {experience_level}-level {role} position.

Question: {question_text}
Category: {category}
Candidate's Answer: {answer}

Evaluate this answer and return a JSON object with exactly these fields:
- "score": integer from 0 to 10 (0=terrible, 5=acceptable, 10=perfect)
- "feedback": string with 2-3 sentences of specific, constructive feedback
- "improvedAnswer": string with a better version of the answer (2-4 sentences)

Return ONLY the JSON object, no other text."""

        try:
            ai_text = ai_complete(eval_prompt, max_tokens=1024)
        except Exception as e:
            app.logger.exception("AI evaluation failed: %s", e)
            ai_text = "{}"

        fallback = {
            "score": 5,
            "feedback": "Your answer shows some understanding. Consider providing more specific examples and technical details.",
            "improvedAnswer": f"A strong answer would include specific examples from your experience, demonstrate technical depth appropriate for a {experience_level} {role}, and clearly articulate your thought process.",
        }
        evaluation = safe_json_parse(ai_text, fallback)

        # Sanitize score
        try:
            score = int(round(float(evaluation.get("score", 5))))
        except (TypeError, ValueError):
            score = 5
        score = max(0, min(10, score))

        feedback = str(evaluation.get("feedback") or fallback["feedback"])
        improved_answer = str(evaluation.get("improvedAnswer") or fallback["improvedAnswer"])

        with get_conn() as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO interview_answers (session_id, question_id, answer, score, feedback, improved_answer) "
                "VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
                (session_id, question_id, answer, score, feedback, improved_answer),
            )
            answer_id = cur.fetchone()[0]
            conn.commit()

        return jsonify({
            "answerId": answer_id,
            "score": score,
            "feedback": feedback,
            "improvedAnswer": improved_answer,
        })
    except Exception as e:
        app.logger.exception("Submit answer failed: %s", e)
        return jsonify({"error": "Failed to evaluate answer"}), 500


# ─── POST /api/interview/sessions/<id>/complete — Finalize + summary ─────────

@app.route("/api/interview/sessions/<int:session_id>/complete", methods=["POST"])
def complete_session(session_id):
    try:
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT role, experience_level FROM interview_sessions WHERE id = %s",
                (session_id,),
            )
            sess = cur.fetchone()
            if not sess:
                return jsonify({"error": "Session not found"}), 404
            role, experience_level = sess

            cur.execute(
                "SELECT id, session_id, question_id, answer, score, feedback, improved_answer, created_at "
                "FROM interview_answers WHERE session_id = %s ORDER BY id",
                (session_id,),
            )
            rows = cur.fetchall()

        if not rows:
            return jsonify({"error": "No answers submitted yet"}), 400

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
            for r in rows
        ]
        total_score = sum(a["score"] for a in answers)
        max_score = len(answers) * 10
        percentage = round((total_score / max_score) * 100) if max_score > 0 else 0

        feedback_lines = "\n".join(
            f"Answer {i + 1}: Score {a['score']}/10 — {a['feedback']}" for i, a in enumerate(answers)
        )
        summary_prompt = f"""You are an expert interviewer. A {experience_level}-level {role} candidate just completed a mock interview.

Their scores and feedback:
{feedback_lines}

Overall percentage: {percentage}%

Return a JSON object with exactly these fields:
- "strengths": array of 2-3 short strings (specific strengths demonstrated)
- "weaknesses": array of 2-3 short strings (areas needing improvement)
- "overallFeedback": string with 2-3 sentences of encouraging overall assessment

Return ONLY the JSON object, no other text."""

        try:
            ai_text = ai_complete(summary_prompt, max_tokens=512)
        except Exception as e:
            app.logger.exception("AI summary failed: %s", e)
            ai_text = "{}"

        fallback = {
            "strengths": ["Completed all interview questions", "Engaged with technical topics"],
            "weaknesses": ["Could provide more specific examples", "Consider expanding on technical depth"],
            "overallFeedback": f"You scored {percentage}% overall. Keep practicing to improve your interview skills!",
        }
        summary = safe_json_parse(ai_text, fallback)
        strengths = summary.get("strengths") or fallback["strengths"]
        weaknesses = summary.get("weaknesses") or fallback["weaknesses"]
        overall_feedback = summary.get("overallFeedback") or fallback["overallFeedback"]

        with get_conn() as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE interview_sessions SET status = 'completed' WHERE id = %s",
                (session_id,),
            )
            conn.commit()

        return jsonify({
            "sessionId": session_id,
            "totalScore": total_score,
            "maxScore": max_score,
            "percentage": percentage,
            "strengths": strengths,
            "weaknesses": weaknesses,
            "overallFeedback": overall_feedback,
            "answers": answers,
        })
    except Exception as e:
        app.logger.exception("Complete session failed: %s", e)
        return jsonify({"error": "Failed to complete session"}), 500


# ─── Entrypoint ──────────────────────────────────────────────────────────────

# Initialize DB on startup
try:
    init_db()
    print("Database initialized successfully.")
except Exception as e:
    print(f"Database initialization failed: {e}")


if __name__ == "__main__":
    app.run(host=HOST, port=PORT, debug=False, threaded=True)
