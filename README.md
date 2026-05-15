# Interview Coach AI

PrepForge AI Mock Interview is a simple interview coaching project with a Flask backend and a static frontend.

## Project structure

- `backend/` — Flask API server, database setup, Google Gemini integration
- `frontend/` — static HTML/CSS/JS user interface
- `pyproject.toml` — Python dependency metadata

## Local setup

### 1. Create a Python virtual environment

```powershell
cd backend
python -m venv .venv
. .venv\Scripts\Activate.ps1
```

### 2. Install dependencies

```powershell
pip install -r requirements.txt
```

### 3. Configure environment variables

Create `backend/.env` with the following values:

```dotenv
DATABASE_URL=postgresql://<user>:<password>@<host>:<port>/<database>
GEMINI_API_KEY=<your_google_gemini_api_key>
PORT=5000
```

> Do not commit secrets like `GEMINI_API_KEY` or `DATABASE_URL` to GitHub.

### 4. Run the backend server

```powershell
python app.py
```

The backend starts on `http://localhost:5000` and serves the frontend from the `frontend/` folder.

## Frontend

Open `frontend/index.html` in your browser, or visit the backend URL after the server is running.

## Render deployment

### Backend service

- Build command:

```bash
pip install -r backend/requirements.txt --no-cache-dir
```

- Start command:

```bash
cd backend && gunicorn -w 4 -b 0.0.0.0:$PORT app:app
```

- Environment variables required on Render:
  - `DATABASE_URL`
  - `GEMINI_API_KEY`

### Frontend static site

- Publish directory: `frontend`
- Build command: leave blank if the frontend is static

## Notes

- The backend auto-creates database tables on startup using `init_db()`.
- If you need a PostgreSQL database, use Render-managed Postgres or a Docker container.
- Keep secrets out of Git history and use Render environment variables or secrets for production.

## Dependencies

Python dependencies are defined in `backend/requirements.txt` and `pyproject.toml`.

## Contact

If you want help deploying or debugging the app, open an issue or contact the repository owner.
