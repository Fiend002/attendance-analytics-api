# Employee Attendance & Analytics API

A FastAPI application for managing employee records, attendance data, and attendance analytics using MongoDB.

## Features

- Employee record management.
- Attendance tracking and regularization.
- Attendance-related calculations, including late minutes, work hours, and overtime.
- Department-wise attendance summaries.
- Monthly late-arrival leaderboard.
- MongoDB indexes for common lookups and duplicate prevention.

## Requirements

- Python 3.10 or a compatible version supported by the installed dependencies.
- MongoDB 6.0 or newer.
- Dependencies listed in `requirements.txt`.

## Setup on Windows

Open Command Prompt in the project directory.

### 1. Create and activate a virtual environment

```cmd
python -m venv .venv
.venv\Scripts\activate
```

### 2. Install dependencies

```cmd
python -m pip install -r requirements.txt
```

### 3. Configure MongoDB

Ensure MongoDB is running locally, or use a MongoDB Atlas connection string.

The application reads these environment variables:

- `MONGO_URI`: MongoDB connection URI. Defaults to `mongodb://localhost:27017`.
- `MONGO_DB`: Database name. Defaults to `attendance_db`.

Set them in the terminal before starting the application if needed:

```cmd
set MONGO_URI=mongodb://localhost:27017
set MONGO_DB=attendance_db
```

Do not commit credentials, secrets, or a `.env` file containing sensitive values.

### 4. Load sample data

If you want to initialize the database with the provided sample dataset, run:

```cmd
python sample_seed.py
```

Run this only when you intend to load the supplied sample data into the configured database.

### 5. Start the API

```cmd
python -m uvicorn app.main:app --port 8000
```

For development with automatic reload:

```cmd
python -m uvicorn app.main:app --port 8000 --reload
```

## API Documentation

After starting the server, open:

- Swagger UI: http://127.0.0.1:8000/docs
- OpenAPI JSON: http://127.0.0.1:8000/openapi.json

Use Swagger UI to inspect endpoints, provide request parameters, and execute API requests.

## Testing

Compile the application modules:

```cmd
python -m compileall -q app
```

Run automated tests, if available:

```cmd
python -m pytest
```

A test command that reports `no tests ran` does not mean the test suite passed. Manual API checks should be supplemented with automated tests and edge-case verification.

## Project Files

- `app/main.py` — FastAPI application and endpoint implementation.
- `requirements.txt` — Python dependencies.
- `openapi.yaml` — API contract and business rules.
- `DATA_MODEL.md` — database model documentation.
- `sample_data/` — sample MongoDB documents.
- `sample_seed.py` — sample-data loading script.
- `DECISIONS.md` — technical decisions.
- `REVIEW.md` — code review findings and verification notes.

## Submission Notes

- Keep `app/main.py`, `requirements.txt`, `README.md`, `DECISIONS.md`, and `REVIEW.md` in the repository.
- Do not commit `.env` files, database credentials, secrets, or the `.venv` directory.
- Follow the assessment instructions for the required Git repository and submission format.