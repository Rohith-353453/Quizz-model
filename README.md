# FLUX Quiz Platform

FLUX is a Flask-based quiz and exam platform for Flux Labs. Masters create timed quizzes, launch live sessions, review analytics, and monitor leaderboards. Students browse available quizzes, submit timed attempts, review results, and view podium standings.

Production domain: **https://fluxlabs.apestree.xyz**

## Stack

- Python 3.11
- Flask, Flask-Login, Flask-SocketIO, and Eventlet
- MongoDB via PyMongo
- Jinja templates with the shared stylesheet in `static/styles.css`
- Render web service deployment

## Local development

1. Create a virtual environment and install dependencies:

   ```powershell
   py -m venv .venv
   .\.venv\Scripts\Activate.ps1
   pip install -r requirements.txt
   ```

2. Copy `.env.example` to `.env` and set a real `SECRET_KEY` and `MONGO_URI`. Never commit `.env`.

3. Start the app:

   ```powershell
   python app.py
   ```

The health endpoint is available at `/health`.

## Render deployment

`render.yaml` is the source of truth for the Render Blueprint:

- Build: `pip install -r requirements.txt`
- Start: Gunicorn with one Eventlet worker for Socket.IO support
- Health check: `/health`
- Required Render environment variables: `MONGO_URI` and the generated `SECRET_KEY`

Render should manage secrets in the service Environment settings. Use a managed MongoDB connection with network access restricted to the required sources, and keep `SECRET_KEY` stable between deploys so existing sessions remain valid.

## User documentation

Signed-in users can open **User Guide** from the dashboard or visit `/docs`. The guide covers:

- Master and Student capabilities
- Manual quiz creation
- JSON quiz import
- The 1–50 question and question-type rules
- The exact JSON shape accepted by the importer

## Repository hygiene

The repository ignores environment files, Python caches, virtual environments, build output, logs, IDE metadata, and stale Vercel deployment metadata. The old Vercel configuration was removed because this application is deployed on Render.

If a secret has ever been committed, rotate it first. Removing it from the latest commit is not enough; use `git filter-repo` or BFG only after confirming that rewriting shared history is acceptable.
