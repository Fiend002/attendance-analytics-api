# Code Review

## Defects Identified and Fixed

| # | Where (function / line) | What is wrong | How you'd notice it (test, input, or symptom) | How you fixed it |
|---|---|---|---|---|
| 1 | `department_summary` | The aggregation started from attendance logs, so departments with eligible employees but no attendance records could be omitted from the summary. | Test a month where an eligible employee has no attendance logs and check whether their department is included in the response. | Changed the aggregation to start from eligible employees and use `$lookup` to retrieve attendance logs for the requested month. Used `preserveNullAndEmptyArrays` so employees without matching logs remain in the aggregation. |

## Manual Verification Performed

- Ran `python -m compileall -q app`; it completed without reporting syntax errors.
- Tested `GET /analytics/departments/summary` for month `2026-10`; it returned HTTP 200.
- Tested the department filter for the department summary; the Engineering result was returned.
- Tested `GET /analytics/leaderboard/late` for month `2026-10`; it returned HTTP 200.
- Tested the department filter for the late-arrival leaderboard; the Engineering employees were returned.
- The department summary's `total_late_minutes` value of 407 matched the sum of the two displayed leaderboard totals (257 and 150).

## Items Requiring Further Verification

- The no-attendance-record scenario should be tested with a controlled dataset.
- The calculations and edge cases should be checked against the requirements in `openapi.yaml` and `PROBLEM_STATEMENT.docx`.
- Manual HTTP 200 responses do not establish correctness for all inputs or the larger grading dataset.
- The starter code should be compared against the final implementation to identify any additional defects.

## Items Considered Not Defects

No additional items are recorded in this section yet. Further review of the starter implementation and requirements is needed before concluding that other behaviours are not defects.