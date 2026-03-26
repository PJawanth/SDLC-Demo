# Incident Dashboard — Release Checklist

## 1. Pre-Release Configuration Checks

| # | Check | Status |
|---|-------|--------|
| 1 | `debug=True` is **removed** or gated behind an env-var before deploy | ☐ |
| 2 | `SECRET_KEY` is set to a strong random value (`os.urandom(32)`) via env-var | ☐ |
| 3 | `app.run()` is **not** used in production — use Gunicorn / uWSGI | ☐ |
| 4 | In-memory stores replaced with a database (SQLite minimum, PostgreSQL recommended) | ☐ |
| 5 | CSRF protection enabled (Flask-WTF `CSRFProtect`) | ☐ |
| 6 | Input validation & sanitisation on all `/incidents` POST payloads | ☐ |
| 7 | Rate limiting on API routes (Flask-Limiter) | ☐ |
| 8 | CORS policy defined if served behind a separate front-end | ☐ |
| 9 | `requirements.txt` locked with hashes (`pip-compile --generate-hashes`) | ☐ |
| 10 | `.env` file is listed in `.gitignore` | ☐ |

---

## 2. Deployment Steps

### A. Prepare the Release Branch

```bash
# Create a release branch from main
git checkout main
git pull origin main
git checkout -b release/v1.0.0

# Verify all 87 tests pass
python -m pytest tests/ -v --tb=short

# Tag the release
git tag -a v1.0.0 -m "v1.0.0 — Incident Dashboard initial release"
git push origin release/v1.0.0 --tags
```

### B. Production Server Setup

```bash
# Install production dependencies
pip install gunicorn

# Run with Gunicorn (Linux / container)
gunicorn -w 4 -b 0.0.0.0:8000 app:app

# Or with Waitress (Windows)
pip install waitress
waitress-serve --port=8000 app:app
```

### C. Environment Variables (set before start)

```
FLASK_ENV=production
SECRET_KEY=<random-32-byte-hex>
PORT=8000
```

### D. Container Deployment (optional)

```dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt gunicorn
COPY . .
EXPOSE 8000
CMD ["gunicorn", "-w", "4", "-b", "0.0.0.0:8000", "app:app"]
```

---

## 3. GitHub Actions Pipeline

The CI workflow at `.github/workflows/ci.yml` runs automatically on every push
and pull request to `main` / `master`. It includes three jobs:

| Job | Purpose |
|-----|---------|
| **lint** | `flake8` style & error check |
| **test** | `pytest` on Python 3.11, 3.12, 3.13 |
| **security** | `safety check` for known CVEs in dependencies |

**Branch protection rules to enable on GitHub:**

1. Go to **Settings → Branches → Add rule** for `main`.
2. Enable **Require a pull request before merging**.
3. Enable **Require status checks to pass** — select `lint`, `test`, `security`.
4. Enable **Require branches to be up to date before merging**.
5. Optionally enable **Require review approval (≥ 1)**.

---

## 4. Post-Deployment Verification

| # | Step |
|---|------|
| 1 | Hit `GET /` — dashboard renders without errors |
| 2 | Create a new incident via form → verify it appears in the table |
| 3 | Update status to Closed → verify escalation clears |
| 4 | Check `/notifications` returns `200` |
| 5 | Check `/roster` returns seeded on-call data |
| 6 | Verify timeline expand/collapse works in browser |
| 7 | Monitor application logs for unhandled exceptions |

---

## 5. Known Limitations Before Production

| Risk | Mitigation |
|------|------------|
| **In-memory data** — all data lost on restart | Migrate to SQLite/PostgreSQL |
| **No authentication** — anyone can create/update incidents | Add Flask-Login or OAuth |
| **No HTTPS** — traffic is unencrypted | Terminate TLS at reverse proxy (Nginx / cloud LB) |
| **No logging** — print statements only | Add Python `logging` module with structured JSON output |
| **Single process seed data** — Gunicorn workers each get separate copies | Move seed data to DB migration |
