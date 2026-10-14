
import os
from datetime import datetime, timedelta, timezone, date, time
from typing import Optional ,Literal

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Body, Query
from pydantic import BaseModel , EmailStr, Field
from pymongo import MongoClient
from pymongo.errors import DuplicateKeyError

load_dotenv()

MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017")
client = MongoClient(MONGO_URI)
db = client[os.getenv("MONGO_DB", "attendance_db")]


app = FastAPI(title="Employee Attendance & Analytics API")

employees = db["employees"]
attendance_logs = db["attendance_logs"]



def create_indexes():
    employees.create_index("emp_code", unique=True)
    employees.create_index([
        ("department", 1),
        ("joined_on", 1)
    ])

    attendance_logs.create_index(
        [("emp_code", 1), ("date", 1)],
        unique=True
    )
    attendance_logs.create_index([
        ("date", -1),
        ("emp_code", 1)
    ])



@app.on_event("startup")
def startup_event():
    create_indexes()

IST = timezone(timedelta(hours=5, minutes=30))


# -------------------------
# Helper Functions
# -------------------------

def compute_late_minutes(
    punch_in: datetime,
    shift_start: str,
) -> int:
    h, m = map(int, shift_start.split(":"))

    start = punch_in.replace(
        hour=h,
        minute=m,
        second=0,
        microsecond=0,
    )

    minutes = max(
        0,
        int((punch_in - start).total_seconds() // 60),
    )

    if minutes <= 10:
        return 0

    return minutes


def compute_work_hours(
    punch_in: datetime,
    punch_out: datetime,
) -> float:
    seconds = (punch_out - punch_in).total_seconds()
    return round(max(0, seconds / 3600), 2)


def compute_overtime(
    work_hours: float,
    standard_hours: float = 8.0,
) -> float:
    return round(max(0, work_hours - standard_hours), 2)


# -------------------------
# Request Models
# -------------------------

class EmployeeIn(BaseModel):
    emp_code: str = Field(pattern=r"^EMP\d{4,6}$")
    name: str
    email: EmailStr
    department: str
    joined_on: date
    shift_start: str = "09:30"
    shift_end: str = "18:30"



class PunchInIn(BaseModel):
    emp_code: str
    punched_at: Optional[int] = Field(
        default=None,
        strict=True,
        ge=100000000000,
    )
    status: Literal["PRESENT", "WFH", "ON_DUTY"] = "PRESENT"


class PunchOutIn(BaseModel):
    emp_code: str
    punched_at: Optional[int] = Field(
        default=None,
        strict=True,
        ge=100000000000
    )

class RegularizeIn(BaseModel):
    reason: str
    regularized_by: str
    status: Optional[
        Literal["PRESENT", "ABSENT", "LEAVE", "WFH", "ON_DUTY"]
    ] = None
    punch_in: Optional[int] = Field(
        default=None, strict=True, ge=100000000000
    )
    punch_out: Optional[int] = Field(
        default=None, strict=True, ge=100000000000
    )



# -------------------------
# Health Check
# -------------------------


@app.get("/health")
def health():
    try:
        db.command("ping")
        return {"status": "ok"}
    except Exception:
        raise HTTPException(
            status_code=503,
            detail="Database unavailable"
        )



# -------------------------
# Create Employee
# -------------------------

@app.post("/employees", status_code=201)
def create_employee(body: EmployeeIn):
    employee = body.model_dump(mode="json")

    employee["created_at"] = datetime.now(timezone.utc)

    try:
        employees.insert_one(employee)
    except DuplicateKeyError:
        raise HTTPException(
            status_code=409,
            detail="Employee code already exists"
        )

    employee.pop("_id", None)
    return employee


# -------------------------
# Punch In
# -------------------------

@app.post("/attendance/punch-in", status_code=201)
def punch_in(body: PunchInIn):
    emp = employees.find_one({"emp_code": body.emp_code})

    if emp is None:
        raise HTTPException(
            status_code=404,
            detail="Employee not found",
        )

    # Timestamp ko IST mein convert karo
    if body.punched_at is not None:
        ts = datetime.fromtimestamp(
            body.punched_at / 1000,
            tz=timezone.utc,
        ).astimezone(IST)
    else:
        ts = datetime.now(IST)

    # Seconds tak hi timestamp rakho
    ts = ts.replace(microsecond=0)

    shift_start = emp.get("shift_start", "09:30")
    shift_end = emp.get("shift_end", "18:30")

    attendance_date = ts.date()

    # Overnight shift ke liye correct date
    if shift_end <= shift_start and ts.strftime("%H:%M") < shift_end:
        attendance_date -= timedelta(days=1)

    date_str = attendance_date.isoformat()


    # Late minutes calculate karo
    h, m = map(int, shift_start.split(":"))
    shift_start_dt = datetime.combine(
        attendance_date,
        time(h, m),
        tzinfo=IST,
    )

    elapsed_seconds = (ts - shift_start_dt).total_seconds()

    if elapsed_seconds > 10 * 60:
        late_minutes = int(elapsed_seconds // 60)
    else:
        late_minutes = 0


    record = {
        "emp_code": body.emp_code,
        "date": date_str,
        "status": body.status,
        "punch_in": ts.astimezone(timezone.utc),
        "punch_out": None,
        "work_hours": None,
        "late_minutes": late_minutes,
        "overtime_minutes": 0,
        "half_day": False,
        "history": [],
    }

    try:
        attendance_logs.insert_one(record)
    except DuplicateKeyError:
        raise HTTPException(
            status_code=409,
            detail="Attendance already exists for this date",
        )

    record.pop("_id", None)

    return {
        **record,
        "punch_in": int(ts.timestamp() * 1000),
        "punch_out": None
    }




# -------------------------
# List Employees
# -------------------------

@app.get("/employees")
def list_employees(
    department: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
):
    if page < 1 or page_size < 1 or page_size > 100:
        raise HTTPException(
            status_code=422,
            detail="Invalid pagination values",
        )

    query = {}
    if department is not None:
        query["department"] = department

    skip = (page - 1) * page_size
    total = employees.count_documents(query)

    items = list(
        employees.find(query, {"_id": 0})
        .sort("emp_code", 1)
        .skip(skip)
        .limit(page_size)
    )


    for item in items:
        value = item.get("created_at")

        if isinstance(value, datetime):
            if value.tzinfo is None:
                value = value.replace(tzinfo=timezone.utc)

            item["created_at"] = int(value.timestamp() * 1000)

    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
    }

# -------------------------
# List Attandance
# -------------------------

@app.get("/attendance")
def list_attendance(
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    emp_code: Optional[str] = None,
    status: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
):
    if page < 1 or page_size < 1 or page_size > 100:
        raise HTTPException(
            status_code=422,
            detail="Invalid pagination values"
        )

    # Date format validate karo
    for value in (date_from, date_to):
        if value is not None:
            try:
                datetime.strptime(value, "%Y-%m-%d")
            except ValueError:
                raise HTTPException(
                    status_code=422,
                    detail="Dates must use YYYY-MM-DD format"
                )

    # Date range validate karo
    if date_from is not None and date_to is not None:
        if date_from > date_to:
            raise HTTPException(
                status_code=422,
                detail="date_from cannot be after date_to"
            )

    query = {}

    if date_from is not None or date_to is not None:
        query["date"] = {}

        if date_from is not None:
            query["date"]["$gte"] = date_from

        if date_to is not None:
            query["date"]["$lte"] = date_to

    if emp_code is not None:
        query["emp_code"] = emp_code

    if status is not None:
        allowed_statuses = {
            "PRESENT", "WFH", "ON_DUTY", "ABSENT", "LEAVE"
        }

        if status not in allowed_statuses:
            raise HTTPException(
                status_code=422,
                detail="Invalid attendance status"
            )

        query["status"] = status

    total = attendance_logs.count_documents(query)

    items = list(
        attendance_logs.find(query, {"_id": 0})
        .sort([("date", -1), ("emp_code", 1)])
        .skip((page - 1) * page_size)
        .limit(page_size)
    )

    for item in items:
        for field in ("punch_in", "punch_out"):
            value = item.get(field)

            if isinstance(value, datetime):
                if value.tzinfo is None:
                    value = value.replace(tzinfo=timezone.utc)

                item[field] = int(value.timestamp() * 1000)

        for entry in item.get("history", []):
            value = entry.get("at")

            if isinstance(value, datetime):
                if value.tzinfo is None:
                    value = value.replace(tzinfo=timezone.utc)

                entry["at"] = int(value.timestamp() * 1000)

    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
    }

# -------------------------
# Punch out Attandance
# -------------------------


@app.post("/attendance/punch-out")
def punch_out(body: PunchOutIn):
    emp = employees.find_one({"emp_code": body.emp_code})
    if emp is None:
        raise HTTPException(status_code=404, detail="Employee not found")

    if body.punched_at is not None:
        ts = datetime.fromtimestamp(
            body.punched_at / 1000, tz=timezone.utc
        ).astimezone(IST)
    else:
        ts = datetime.now(IST)

    ts = ts.replace(microsecond=0)

    ts_utc = ts.astimezone(timezone.utc)

    record = attendance_logs.find_one(
        {
            "emp_code": body.emp_code,
            "punch_in": {"$lte": ts_utc},
            "punch_out": None,
        },
        sort=[("punch_in", -1)],
    )

    if record is None:
        raise HTTPException(
            status_code=404,
            detail="No open attendance record found"
        )

    punch_in_utc = record["punch_in"]
    if punch_in_utc.tzinfo is None:
        punch_in_utc = punch_in_utc.replace(tzinfo=timezone.utc)

    duration_seconds = (ts_utc - punch_in_utc).total_seconds()

    if duration_seconds <= 0 or duration_seconds > 24 * 3600:
        raise HTTPException(
            status_code=422,
            detail="Invalid punch-out time"
        )


    from decimal import Decimal, ROUND_HALF_UP

    work_hours = float(
        Decimal(str(duration_seconds / 3600)).quantize(
            Decimal("0.01"),
            rounding=ROUND_HALF_UP
        )
    )


    shift_end = emp.get("shift_end", "18:30")
    h, m = map(int, shift_end.split(":"))

    shift_end_date = datetime.fromisoformat(record["date"]).date()
    shift_start = emp.get("shift_start", "09:30")

    if shift_end <= shift_start:
        shift_end_date += timedelta(days=1)

    shift_end_dt = datetime.combine(
        shift_end_date, time(h, m), tzinfo=IST
    ).astimezone(timezone.utc)

    overtime_seconds = max(0, (ts_utc - shift_end_dt).total_seconds())
    overtime_minutes = int(overtime_seconds // 60)
    if overtime_minutes < 30:
        overtime_minutes = 0

    updated = attendance_logs.find_one_and_update(
        {"_id": record["_id"], "punch_out": None},
        {
            "$set": {
                "punch_out": ts_utc,
                "work_hours": work_hours,
                "overtime_minutes": overtime_minutes,
                "half_day": work_hours < 4.50,
            }
        },
        return_document=True,
    )

    if updated is None:
        raise HTTPException(
            status_code=409,
            detail="Attendance already punched out"
        )

    updated.pop("_id", None)
    updated["punch_in"] = int(punch_in_utc.timestamp() * 1000)
    updated["punch_out"] = int(ts_utc.timestamp() * 1000)

    return updated

# -------------------------
# Regularized Attandance
# -------------------------

@app.patch("/attendance/{emp_code}/{date}")
def regularize_attendance(
    emp_code: str,
    date: str,
    body: RegularizeIn,
):
    emp = employees.find_one({"emp_code": emp_code})
    if emp is None:
        raise HTTPException(status_code=404, detail="Employee not found")

    record = attendance_logs.find_one({
        "emp_code": emp_code,
        "date": date,
    })
    if record is None:
        raise HTTPException(status_code=404, detail="Attendance not found")

    changes = {}

    if body.status is not None and body.status != record["status"]:
        changes["status"] = body.status

    for field in ("punch_in", "punch_out"):
        value = getattr(body, field)
        if value is not None:
            new_value = datetime.fromtimestamp(
                value / 1000, tz=timezone.utc
            ).replace(microsecond=0)

            old_value = record.get(field)
            if old_value is not None and old_value.tzinfo is None:
                old_value = old_value.replace(tzinfo=timezone.utc)

            if old_value != new_value:
                changes[field] = new_value

    if not changes:
        raise HTTPException(
            status_code=422,
            detail="No changes provided"
        )

    new_status = changes.get("status", record["status"])
    new_punch_in = changes.get("punch_in", record.get("punch_in"))
    new_punch_out = changes.get("punch_out", record.get("punch_out"))

    if new_status in ("ABSENT", "LEAVE"):
        if "punch_in" in changes or "punch_out" in changes:
            raise HTTPException(
                status_code=422,
                detail="ABSENT or LEAVE cannot have punch times"
            )
        new_punch_in = None
        new_punch_out = None
        changes["punch_in"] = None
        changes["punch_out"] = None
    elif new_punch_in is None:
        raise HTTPException(
            status_code=422,
            detail="Presence status requires punch_in"
        )

    if new_punch_in is not None:
        if new_punch_in.astimezone(IST).date().isoformat() != date:
            raise HTTPException(
                status_code=422,
                detail="punch_in must be on the attendance date"
            )

    if new_punch_out is not None:
        if new_punch_in is None:
            raise HTTPException(status_code=422, detail="punch_in is required")
        seconds = (new_punch_out - new_punch_in).total_seconds()
        if seconds <= 0 or seconds > 24 * 3600:
            raise HTTPException(status_code=422, detail="Invalid punch_out")

    work_hours = None
    overtime_minutes = 0
    half_day = False
    late_minutes = 0

    if new_punch_in is not None:
        shift_start = emp.get("shift_start", "09:30")
        shift_end = emp.get("shift_end", "18:30")

        local_in = new_punch_in.astimezone(IST)
        shift_date = local_in.date()

        h, m = map(int, shift_start.split(":"))
        start_dt = datetime.combine(
            shift_date, time(h, m), tzinfo=IST
        )
        elapsed = int((local_in - start_dt).total_seconds() // 60)
        late_minutes = elapsed if elapsed > 10 else 0

        if new_punch_out is not None:
            seconds = (new_punch_out - new_punch_in).total_seconds()
            from decimal import Decimal, ROUND_HALF_UP

            work_hours = float(
                Decimal(str(seconds / 3600)).quantize(
                    Decimal("0.01"),
                    rounding=ROUND_HALF_UP
                )
            )
            half_day = work_hours < 4.50

            h, m = map(int, shift_end.split(":"))
            end_date = shift_date
            if shift_end <= shift_start:
                end_date += timedelta(days=1)
            shift_end_dt = datetime.combine(
                end_date, time(h, m), tzinfo=IST
            ).astimezone(timezone.utc)
            overtime = int(
               max(
    0,
        (
            new_punch_out.astimezone(timezone.utc)
                - shift_end_dt.astimezone(timezone.utc)
                ).total_seconds()
        ) // 60
            )
            overtime_minutes = overtime if overtime >= 30 else 0

    changes.update({
        "work_hours": work_hours,
        "late_minutes": late_minutes,
        "overtime_minutes": overtime_minutes,
        "half_day": half_day,
    })

    history_changes = {
    key: changes[key]
    for key in changes
    if key in ("status", "punch_in", "punch_out")
}

    history_entry = {
        "at": datetime.now(timezone.utc),
        "reason": body.reason,
        "regularized_by": body.regularized_by,
        "changes": history_changes,
    }

    result = attendance_logs.update_one(
    {
        "_id": record["_id"],
        "history": record.get("history", []),
        "status": record["status"],
        "punch_in": record.get("punch_in"),
        "punch_out": record.get("punch_out"),
    },
    {
        "$set": changes,
        "$push": {"history": history_entry},
    },
)

    if result.modified_count == 0:
        raise HTTPException(
        status_code=409,
        detail="Attendance record changed concurrently; please retry",
    )

    updated = attendance_logs.find_one({"_id": record["_id"]})
    updated.pop("_id", None)

    for field in ("punch_in", "punch_out"):
        value = updated.get(field)
        if isinstance(value, datetime):
            if value.tzinfo is None:
                value = value.replace(tzinfo=timezone.utc)
            updated[field] = int(value.timestamp() * 1000)

    for entry in updated.get("history", []):
        value = entry.get("at")
        if isinstance(value, datetime):
            if value.tzinfo is None:
                value = value.replace(tzinfo=timezone.utc)
            entry["at"] = int(value.timestamp() * 1000)
        for key, value in entry.get("changes", {}).items():
            if isinstance(value, datetime):
                if value.tzinfo is None:
                    value = value.replace(tzinfo=timezone.utc)
                entry["changes"][key] = int(value.timestamp() * 1000)

    return updated

# -------------------------
# Employee monthly analytics
# -------------------------

@app.get("/analytics/employees/{emp_code}/monthly")
def employee_monthly(emp_code: str, month: str):
    import re
    from calendar import monthrange

    if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", month):
        raise HTTPException(
            status_code=422,
            detail="month must be in YYYY-MM format"
        )

    employee = employees.find_one({"emp_code": emp_code})
    if not employee:
        raise HTTPException(status_code=404, detail="Employee not found")

    year, month_number = map(int, month.split("-"))
    days_in_month = monthrange(year, month_number)[1]

    start_date = datetime(year, month_number, 1)
    end_date = datetime(year, month_number, days_in_month, 23, 59, 59, 999999)

    pipeline = [
        {
            "$match": {
                "emp_code": emp_code,
                "date": {"$gte": start_date, "$lte": end_date}
            }
        },
        {
            "$group": {
                "_id": None,
                "present_days": {
                    "$sum": {
                        "$cond": [
                            {"$in": ["$status", ["PRESENT", "WFH", "ON_DUTY"]]},
                            {
                                "$cond": [
                                    "$half_day",
                                    0.5,
                                    1
                                ]
                            },
                            0
                        ]
                    }
                },
                "leave_days": {
                    "$sum": {
                        "$cond": [{"$eq": ["$status", "LEAVE"]}, 1, 0]
                    }
                },
                "late_count": {
                    "$sum": {
                        "$cond": [{"$gt": ["$late_minutes", 0]}, 1, 0]
                    }
                }
            }
        }
    ]

    result = list(attendance_logs.aggregate(pipeline))
    summary = result[0] if result else {
        "present_days": 0,
        "leave_days": 0,
        "late_count": 0
    }

    working_days = sum(
        1 for day in range(1, days_in_month + 1)
        if date(year, month_number, day).weekday() < 5
    )

    attendance_pct = (
        round(summary["present_days"] / working_days * 100, 2)
        if working_days else None
    )

    return {
        "emp_code": emp_code,
        "month": month,
        "working_days": working_days,
        "present_days": summary["present_days"],
        "leave_days": summary["leave_days"],
        "late_count": summary["late_count"],
        "attendance_pct": attendance_pct
    }

# -------------------------
# Department Summary API
# -------------------------

@app.get("/analytics/departments/summary")

def department_summary(month: str, department: Optional[str] = None):
    import re
    from calendar import monthrange

    if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", month):
        raise HTTPException(
            status_code=422,
            detail="month must be in YYYY-MM format"
        )

    year, month_number = map(int, month.split("-"))
    days_in_month = monthrange(year, month_number)[1]

    start_date = f"{month}-01"
    end_date = f"{year:04d}-{month_number:02d}-{days_in_month:02d}"

    employee_match = {
        "joined_on": {"$lte": end_date}
    }

    if department is not None:
        employee_match["department"] = department

    pipeline = [
        {"$match": employee_match},
        {
            "$lookup": {
                "from": "attendance_logs",
                "let": {"employee_code": "$emp_code"},
                "pipeline": [
                    {
                        "$match": {
                            "$expr": {
                                "$and": [
                                    {
                                        "$eq": [
                                            "$emp_code",
                                            "$$employee_code"
                                        ]
                                    },
                                    {"$gte": ["$date", start_date]},
                                    {"$lte": ["$date", end_date]}
                                ]
                            }
                        }
                    },
                    {
                        "$project": {
                            "_id": 0,
                            "status": 1,
                            "half_day": 1,
                            "work_hours": 1,
                            "late_minutes": 1
                        }
                    }
                ],
                "as": "logs"
            }
        },
        {
            "$unwind": {
                "path": "$logs",
                "preserveNullAndEmptyArrays": True
            }
        },
        {
            "$group": {
                "_id": "$department",
                "employee_codes": {"$addToSet": "$emp_code"},
                "present_days": {
                    "$sum": {
                        "$cond": [
                            {
                                "$in": [
                                    "$logs.status",
                                    ["PRESENT", "WFH", "ON_DUTY"]
                                ]
                            },
                            {
                                "$cond": [
                                    {"$ifNull": ["$logs.half_day", False]},
                                    0.5,
                                    1
                                ]
                            },
                            0
                        ]
                    }
                },
                "avg_work_hours": {
                    "$avg": {
                        "$cond": [
                            {
                                "$and": [
                                    {
                                        "$in": [
                                            "$logs.status",
                                            ["PRESENT", "WFH", "ON_DUTY"]
                                        ]
                                    },
                                    {
                                        "$ne": [
                                            {"$ifNull": ["$logs.work_hours", None]},
                                            None
                                        ]
                                    }
                                ]
                            },
                            "$logs.work_hours",
                            None
                        ]
                    }
                },
                "late_count": {
                    "$sum": {
                        "$cond": [
                            {
                                "$gt": [
                                    {"$ifNull": ["$logs.late_minutes", 0]},
                                    0
                                ]
                            },
                            1,
                            0
                        ]
                    }
                },
                "total_late_minutes": {
                    "$sum": {"$ifNull": ["$logs.late_minutes", 0]}
                },
                "leave_count": {
                    "$sum": {
                        "$cond": [
                            {"$eq": ["$logs.status", "LEAVE"]},
                            1,
                            0
                        ]
                    }
                },
                "on_duty_count": {
                    "$sum": {
                        "$cond": [
                            {"$eq": ["$logs.status", "ON_DUTY"]},
                            1,
                            0
                        ]
                    }
                }
            }
        },
        {
            "$project": {
                "_id": 0,
                "department": "$_id",
                "month": {"$literal": month},
                "headcount": {"$size": "$employee_codes"},
                "present_days": 1,
                "avg_work_hours": {
                    "$cond": [
                        {"$eq": ["$avg_work_hours", None]},
                        None,
                        {"$round": ["$avg_work_hours", 2]}
                    ]
                },
                "late_count": 1,
                "total_late_minutes": 1,
                "leave_count": 1,
                "on_duty_count": 1
            }
        },
        {"$sort": {"department": 1}}
    ]

    return list(employees.aggregate(pipeline))


# -------------------------
# Late Leaderboard
# -------------------------

@app.get("/analytics/leaderboard/late")
def late_leaderboard(
    month: str,
    limit: int = 10,
    department: Optional[str] = None,
):
    import re
    from calendar import monthrange

    if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", month):
        raise HTTPException(
            status_code=422,
            detail="month must be in YYYY-MM format"
        )

    if limit < 1 or limit > 50:
        raise HTTPException(
            status_code=422,
            detail="limit must be between 1 and 50"
        )

    year, month_number = map(int, month.split("-"))
    days_in_month = monthrange(year, month_number)[1]

    start_date = datetime(year, month_number, 1).date().isoformat()
    end_date = datetime(
        year, month_number, days_in_month
    ).date().isoformat()

    pipeline = [
        {
            "$match": {
                "date": {"$gte": start_date, "$lte": end_date},
                "late_minutes": {"$gt": 0},
            }
        },
        {
            "$group": {
                "_id": "$emp_code",
                "total_late_minutes": {"$sum": "$late_minutes"},
            }
        },
        {"$sort": {"total_late_minutes": -1, "_id": 1}},
        {
            "$lookup": {
                "from": "employees",
                "localField": "_id",
                "foreignField": "emp_code",
                "as": "employee",
            }
        },
        {"$unwind": "$employee"},
    ]

    if department is not None:
        pipeline.append({
            "$match": {"employee.department": department}
        })

    pipeline.extend([
        {
            "$setWindowFields": {
                "sortBy": {
                    "total_late_minutes": -1,
                },
                "output": {
                    "rank": {"$rank": {}},
                },
            }
        },
        {"$match": {"rank": {"$lte": limit}}},
        {"$sort": {"total_late_minutes": -1, "_id": 1}},
        {
            "$project": {
                "_id": 0,
                "emp_code": "$_id",
                "name": "$employee.name",
                "department": "$employee.department",
                "total_late_minutes": 1,
                "rank": 1,
            }
        },
    ])

    return list(attendance_logs.aggregate(pipeline))


# -------------------------
# Department Trend API
# -------------------------

@app.get("/analytics/departments/{department}/trend")
def department_trend(
    department: str,
    from_date: str = Query(..., alias="from"),
    to_date: str = Query(..., alias="to"),
):
    import re
    from datetime import date as date_type
    from decimal import Decimal, ROUND_HALF_UP

    # Validate date format
    date_pattern = r"\d{4}-\d{2}-\d{2}"

    if not re.fullmatch(date_pattern, from_date):
        raise HTTPException(
            status_code=422,
            detail="'from' must use YYYY-MM-DD format"
        )

    if not re.fullmatch(date_pattern, to_date):
        raise HTTPException(
            status_code=422,
            detail="'to' must use YYYY-MM-DD format"
        )

    try:
        start_day = date_type.fromisoformat(from_date)
        end_day = date_type.fromisoformat(to_date)
    except ValueError:
        raise HTTPException(
            status_code=422,
            detail="Invalid calendar date"
        )

    if end_day < start_day:
        raise HTTPException(
            status_code=422,
            detail="'to' must be on or after 'from'"
        )

    if (end_day - start_day).days + 1 > 92:
        raise HTTPException(
            status_code=422,
            detail="Date range cannot exceed 92 days"
        )

    if not employees.find_one({"department": department}):
        raise HTTPException(
            status_code=404,
            detail="Department not found"
        )

    start_dt = datetime(
        start_day.year, start_day.month, start_day.day
    )
    end_exclusive = datetime(
        end_day.year, end_day.month, end_day.day
    ) + timedelta(days=1)

    pipeline = [
        # Seed document lets MongoDB generate every calendar day.
        {"$documents": [{"date": start_dt}]},

        {
            "$densify": {
                "field": "date",
                "range": {
                    "step": 1,
                    "unit": "day",
                    "bounds": [start_dt, end_exclusive]
                }
            }
        },

        {
            "$set": {
                "day_string": {
                    "$dateToString": {
                        "format": "%Y-%m-%d",
                        "date": "$date"
                    }
                },
                "is_working_day": {
                    "$in": [
                        {"$dayOfWeek": "$date"},
                        [2, 3, 4, 5, 6]
                    ]
                }
            }
        },

        {
            "$lookup": {
                "from": "attendance_logs",
                "let": {"day": "$day_string"},
                "pipeline": [
                    {
                        "$match": {
                            "$expr": {
                                "$eq": ["$date", "$$day"]
                            }
                        }
                    },
                    {
                        "$lookup": {
                            "from": "employees",
                            "localField": "emp_code",
                            "foreignField": "emp_code",
                            "as": "employee"
                        }
                    },
                    {"$unwind": "$employee"},
                    {
                        "$match": {
                            "employee.department": department
                        }
                    }
                ],
                "as": "daily_logs"
            }
        },

        {
            "$lookup": {
                "from": "employees",
                "let": {"day": "$day_string"},
                "pipeline": [
                    {
                        "$match": {
                            "$expr": {
                                "$and": [
                                    {
                                        "$eq": [
                                            "$department",
                                            department
                                        ]
                                    },
                                    {
                                        "$lte": [
                                            "$joined_on",
                                            "$$day"
                                        ]
                                    }
                                ]
                            }
                        }
                    }
                ],
                "as": "department_employees"
            }
        },

        {
            "$set": {
                "headcount": {
                    "$size": "$department_employees"
                },
                "present_count": {
                    "$sum": {
                        "$map": {
                            "input": "$daily_logs",
                            "as": "log",
                            "in": {
                                "$cond": [
                                    {
                                        "$in": [
                                            "$$log.status",
                                            ["PRESENT", "WFH", "ON_DUTY"]
                                        ]
                                    },
                                    {
                                        "$cond": [
                                            {
                                                "$eq": [
                                                    "$$log.half_day",
                                                    True
                                                ]
                                            },
                                            0.5,
                                            1
                                        ]
                                    },
                                    0
                                ]
                            }
                        }
                    }
                },
                "late_count": {
                    "$size": {
                        "$filter": {
                            "input": "$daily_logs",
                            "as": "log",
                            "cond": {
                                "$gt": [
                                    "$$log.late_minutes",
                                    0
                                ]
                            }
                        }
                    }
                }
            }
        },

        {
            "$set": {
                "attendance_rate": {
                    "$cond": [
                        {
                            "$and": [
                                "$is_working_day",
                                {"$gt": ["$headcount", 0]}
                            ]
                        },
                        {
                            "$multiply": [
                                {
                                    "$divide": [
                                        "$present_count",
                                        "$headcount"
                                    ]
                                },
                                100
                            ]
                        },
                        None
                    ]
                }
            }
        },

        {
            "$setWindowFields": {
                "sortBy": {"date": 1},
                "output": {
                    "moving_avg_7d": {
                        "$avg": "$attendance_rate",
                        "window": {
                            "documents": [-6, 0]
                        }
                    }
                }
            }
        },

        {
            "$project": {
                "_id": 0,
                "date": "$day_string",
                "is_working_day": 1,
                "headcount": 1,
                "present_count": 1,
                "late_count": 1,
                "attendance_rate": 1,
                "moving_avg_7d": 1
            }
        },

        {"$sort": {"date": 1}}
    ]

    # $documents must be the first stage, so use database.aggregate.
    rows = list(db.aggregate(pipeline))

    # Apply exact half-up rounding to returned rates.
    for row in rows:
        for field in ("attendance_rate", "moving_avg_7d"):
            value = row.get(field)

            if value is not None:
                row[field] = float(
                    Decimal(str(value)).quantize(
                        Decimal("0.0001"),
                        rounding=ROUND_HALF_UP
                    )
                )

    return rows


@app.get("/admin/explain/{endpoint}")
def admin_explain(
    endpoint: str,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    emp_code: Optional[str] = None,
    status: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
    month: Optional[str] = None,
    limit: int = 10,
    department: Optional[str] = None,
    from_date: Optional[str] = Query(None, alias="from"),
    to_date: Optional[str] = Query(None, alias="to"),
):
    allowed_endpoints = {
        "attendance_list",
        "employee_monthly",
        "department_summary",
        "late_leaderboard",
        "department_trend",
    }

    if endpoint not in allowed_endpoints:
        raise HTTPException(
            status_code=422,
            detail="Invalid endpoint name"
        )



    if endpoint == "attendance_list":
        if page < 1 or page_size < 1 or page_size > 100:
            raise HTTPException(status_code=422, detail="Invalid pagination values")

        query = {}
        if date_from is not None or date_to is not None:
            query["date"] = {}
            if date_from is not None:
                query["date"]["$gte"] = date_from
            if date_to is not None:
                query["date"]["$lte"] = date_to
        if emp_code is not None:
            query["emp_code"] = emp_code
        if status is not None:
            query["status"] = status

        result = db.command({
            "explain": {
                "find": attendance_logs.name,
                "filter": query,
                "sort": {"date": -1, "emp_code": 1},
                "skip": (page - 1) * page_size,
                "limit": page_size,
            },
            "verbosity": "executionStats",
        })
        return result


    if endpoint == "employee_monthly":
        import re
        from calendar import monthrange

        if not emp_code:
            raise HTTPException(status_code=422, detail="emp_code is required")
        if not month or not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", month):
            raise HTTPException(status_code=422, detail="month must be in YYYY-MM format")

        year, month_number = map(int, month.split("-"))
        days_in_month = monthrange(year, month_number)[1]
        start_date = f"{year:04d}-{month_number:02d}-01"
        end_date = f"{year:04d}-{month_number:02d}-{days_in_month:02d}"

        pipeline = [
            {
                "$match": {
                    "emp_code": emp_code,
                    "date": {"$gte": start_date, "$lte": end_date},
                }
            },
            {
                "$group": {
                    "_id": None,
                    "present_days": {
                        "$sum": {
                            "$cond": [
                                {"$in": ["$status", ["PRESENT", "WFH", "ON_DUTY"]]},
                                {"$cond": ["$half_day", 0.5, 1]},
                                0,
                            ]
                        }
                    },
                    "leave_days": {
                        "$sum": {"$cond": [{"$eq": ["$status", "LEAVE"]}, 1, 0]}
                    },
                    "late_count": {
                        "$sum": {"$cond": [{"$gt": ["$late_minutes", 0]}, 1, 0]}
                    },
                }
            },
        ]

        result = db.command({
            "explain": {
                "aggregate": attendance_logs.name,
                "pipeline": pipeline,
                "cursor": {},
            },
            "verbosity": "executionStats",
        })
        return result


    if endpoint == "department_summary":
        import re
        from calendar import monthrange

        if not month or not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", month):
            raise HTTPException(status_code=422, detail="month must be in YYYY-MM format")

        year, month_number = map(int, month.split("-"))
        days_in_month = monthrange(year, month_number)[1]
        start_date = f"{year:04d}-{month_number:02d}-01"
        end_date = f"{year:04d}-{month_number:02d}-{days_in_month:02d}"

        match_stage = {
            "date": {"$gte": start_date, "$lte": end_date}
        }
        pipeline = [{"$match": match_stage}]

        pipeline.extend([
            {
                "$lookup": {
                    "from": employees.name,
                    "localField": "emp_code",
                    "foreignField": "emp_code",
                    "as": "employee",
                }
            },
            {"$unwind": "$employee"},
        ])

        if department is not None:
            pipeline.append({"$match": {"employee.department": department}})

        pipeline.extend([
            {
                "$group": {
                    "_id": "$employee.department",
                    "present_days": {
                        "$sum": {
                            "$cond": [
                                {"$in": ["$status", ["PRESENT", "WFH", "ON_DUTY"]]},
                                {"$cond": ["$half_day", 0.5, 1]},
                                0,
                            ]
                        }
                    },
                    "late_count": {
                        "$sum": {"$cond": [{"$gt": ["$late_minutes", 0]}, 1, 0]}
                    },
                }
            },
            {"$sort": {"_id": 1}},
        ])

        result = db.command({
            "explain": {
                "aggregate": attendance_logs.name,
                "pipeline": pipeline,
                "cursor": {},
            },
            "verbosity": "executionStats",
        })
        return result


    if endpoint == "late_leaderboard":
        import re
        from calendar import monthrange

        if not month or not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", month):
            raise HTTPException(status_code=422, detail="month must be in YYYY-MM format")

        if limit < 1 or limit > 50:
            raise HTTPException(status_code=422, detail="limit must be between 1 and 50")

        year, month_number = map(int, month.split("-"))
        days_in_month = monthrange(year, month_number)[1]
        start_date = f"{year:04d}-{month_number:02d}-01"
        end_date = f"{year:04d}-{month_number:02d}-{days_in_month:02d}"

        pipeline = [
            {
                "$match": {
                    "date": {"$gte": start_date, "$lte": end_date},
                    "late_minutes": {"$gt": 0},
                }
            },
            {
                "$group": {
                    "_id": "$emp_code",
                    "total_late_minutes": {"$sum": "$late_minutes"},
                }
            },
            {"$sort": {"total_late_minutes": -1, "_id": 1}},
            {
                "$lookup": {
                    "from": employees.name,
                    "localField": "_id",
                    "foreignField": "emp_code",
                    "as": "employee",
                }
            },
            {"$unwind": "$employee"},
        ]

        if department is not None:
            pipeline.append({"$match": {"employee.department": department}})

        pipeline.extend([
            {
                "$setWindowFields": {
                    "sortBy": {"total_late_minutes": -1},
                    "output": {"rank": {"$rank": {}}},
                }
            },
            {"$match": {"rank": {"$lte": limit}}},
            {"$sort": {"total_late_minutes": -1, "_id": 1}},
            {
                "$project": {
                    "_id": 0,
                    "emp_code": "$_id",
                    "name": "$employee.name",
                    "department": "$employee.department",
                    "total_late_minutes": 1,
                    "rank": 1,
                }
            },
        ])

        result = db.command({
            "explain": {
                "aggregate": attendance_logs.name,
                "pipeline": pipeline,
                "cursor": {},
            },
            "verbosity": "executionStats",
        })
        return result

    if endpoint == "department_trend":
        import re
        from datetime import date as date_type

        if not department:
            raise HTTPException(
                status_code=422,
                detail="department is required"
            )

        if not from_date or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", from_date):
            raise HTTPException(
                status_code=422,
                detail="'from' must use YYYY-MM-DD format"
            )

        if not to_date or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", to_date):
            raise HTTPException(
                status_code=422,
                detail="'to' must use YYYY-MM-DD format"
            )

        try:
            start_day = date_type.fromisoformat(from_date)
            end_day = date_type.fromisoformat(to_date)
        except ValueError:
            raise HTTPException(
                status_code=422,
                detail="Invalid calendar date"
            )

        if end_day < start_day:
            raise HTTPException(
                status_code=422,
                detail="'to' must be on or after 'from'"
            )

        if (end_day - start_day).days + 1 > 92:
            raise HTTPException(
                status_code=422,
                detail="Date range cannot exceed 92 days"
            )

        if not employees.find_one({"department": department}):
            raise HTTPException(
                status_code=404,
                detail="Department not found"
            )

        start_dt = datetime(
            start_day.year, start_day.month, start_day.day
        )
        end_exclusive = datetime(
            end_day.year, end_day.month, end_day.day
        ) + timedelta(days=1)

        # Use the same pipeline as the real department_trend endpoint.
        # Copy the pipeline from that function here, unchanged.
        pipeline = [
            {"$documents": [{"date": start_dt}]},
            {
                "$densify": {
                    "field": "date",
                    "range": {
                        "step": 1,
                        "unit": "day",
                        "bounds": [start_dt, end_exclusive]
                    }
                }
            },
            {
                "$set": {
                    "day_string": {
                        "$dateToString": {
                            "format": "%Y-%m-%d",
                            "date": "$date"
                        }
                    },
                    "is_working_day": {
                        "$in": [
                            {"$dayOfWeek": "$date"},
                            [2, 3, 4, 5, 6]
                        ]
                    }
                }
            },
            {
                "$lookup": {
                    "from": "attendance_logs",
                    "let": {"day": "$day_string"},
                    "pipeline": [
                        {
                            "$match": {
                                "$expr": {
                                    "$eq": ["$date", "$$day"]
                                }
                            }
                        },
                        {
                            "$lookup": {
                                "from": "employees",
                                "localField": "emp_code",
                                "foreignField": "emp_code",
                                "as": "employee"
                            }
                        },
                        {"$unwind": "$employee"},
                        {
                            "$match": {
                                "employee.department": department
                            }
                        }
                    ],
                    "as": "daily_logs"
                }
            },
            {
                "$lookup": {
                    "from": "employees",
                    "let": {"day": "$day_string"},
                    "pipeline": [
                        {
                            "$match": {
                                "$expr": {
                                    "$and": [
                                        {"$eq": ["$department", department]},
                                        {"$lte": ["$joined_on", "$$day"]}
                                    ]
                                }
                            }
                        }
                    ],
                    "as": "department_employees"
                }
            },
            {
                "$set": {
                    "headcount": {"$size": "$department_employees"},
                    "present_count": {
                        "$sum": {
                            "$map": {
                                "input": "$daily_logs",
                                "as": "log",
                                "in": {
                                    "$cond": [
                                        {
                                            "$in": [
                                                "$$log.status",
                                                ["PRESENT", "WFH", "ON_DUTY"]
                                            ]
                                        },
                                        {
                                            "$cond": [
                                                {"$eq": ["$$log.half_day", True]},
                                                0.5,
                                                1
                                            ]
                                        },
                                        0
                                    ]
                                }
                            }
                        }
                    },
                    "late_count": {
                        "$size": {
                            "$filter": {
                                "input": "$daily_logs",
                                "as": "log",
                                "cond": {"$gt": ["$$log.late_minutes", 0]}
                            }
                        }
                    }
                }
            },
            {
                "$set": {
                    "attendance_rate": {
                        "$cond": [
                            {
                                "$and": [
                                    "$is_working_day",
                                    {"$gt": ["$headcount", 0]}
                                ]
                            },
                            {
                                "$multiply": [
                                    {
                                        "$divide": [
                                            "$present_count",
                                            "$headcount"
                                        ]
                                    },
                                    100
                                ]
                            },
                            None
                        ]
                    }
                }
            },
            {
                "$setWindowFields": {
                    "sortBy": {"date": 1},
                    "output": {
                        "moving_avg_7d": {
                            "$avg": "$attendance_rate",
                            "window": {"documents": [-6, 0]}
                        }
                    }
                }
            },
            {
                "$project": {
                    "_id": 0,
                    "date": "$day_string",
                    "is_working_day": 1,
                    "headcount": 1,
                    "present_count": 1,
                    "late_count": 1,
                    "attendance_rate": 1,
                    "moving_avg_7d": 1
                }
            },
            {"$sort": {"date": 1}}
        ]

        result = db.command({
            "explain": {
                "aggregate": 1,
                "pipeline": pipeline,
                "cursor": {}
            },
            "verbosity": "executionStats"
        })
        return result


    raise HTTPException(
        status_code=501,
        detail=f"Actual aggregation explain is not implemented yet for {endpoint}"
    )
