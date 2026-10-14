# Technical Decisions

## 1. Application Framework

FastAPI is used to build the API. Pydantic models are used to validate requests. When errors occur the API returns HTTP errors using FastAPIs HTTPException.

## 2. Database

MongoDB is used to store employee records and attendance logs. The application reads the database connection string from MONGO_URI. The database name from MONGO_DB. Default values are provided for development.

## 3. Data Validation

Employee and attendance request models define the expected input types and constraints. Employee codes are checked against a pattern. Attendance statuses are limited to allowed values where applicable.

## 4. Database Indexes

Indexes are created when the application starts up.

- A unique index is created on the employee code field.

- A compound index is created on department and joining date.

- A unique compound index is created on employee code and attendance date.

- A compound index is created on attendance date and employee code.

## 5. Attendance Calculations

Attendance calculations are handled using helper functions for minutes worked and overtime. Late arrival times are calculated with a ten-minute grace period. Minutes after the grace period count as late.

## 6. Department Summary

Department summary metrics are computed using a MongoDB aggregation pipeline. Employees who joined by the end of the requested month are included. Attendance logs are matched to the requested month. The aggregation keeps employees even if they have no attendance logs so they still count in the department headcount.

## 7. Late‑arrival Leaderboard

The late-arrival leaderboard groups minutes by employee for the requested month. The endpoint allows a limit on the number of results. Supports an optional department filter.

## 8. API Testing

The analytics endpoints were tested manually using the FastAPI Swagger UI. All tested requests returned HTTP responses. The department filter was verified to work for both the department summary and the late-arrival leaderboard.

## 9. Known Verification Limits

Manual testing through the Swagger UI shows that the tested requests run successfully. However this does not confirm correctness for all edge cases or, for the grading dataset. Automated tests and more boundary-case checks are needed before submission.