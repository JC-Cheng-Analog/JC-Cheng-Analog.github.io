import json
import os
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from google.analytics.data_v1beta import BetaAnalyticsDataClient
from google.analytics.data_v1beta.types import DateRange, Metric, RunReportRequest
from google.oauth2 import service_account


PROPERTY_ID = os.environ["GA4_PROPERTY_ID"]
CREDENTIALS_JSON = os.environ["GA4_CREDENTIALS"]
TAIPEI_TZ = ZoneInfo("Asia/Taipei")
OUTPUT_FILE = Path("stats-data.json")


def get_taipei_today() -> date:
    """Return today's date in Taiwan time."""
    return datetime.now(TAIPEI_TZ).date()


def get_month_bounds(day: date) -> tuple[date, date]:
    """Return the first and last date of the calendar month containing day."""
    first_day = day.replace(day=1)
    if first_day.month == 12:
        first_next_month = first_day.replace(
            year=first_day.year + 1,
            month=1,
        )
    else:
        first_next_month = first_day.replace(month=first_day.month + 1)

    return first_day, first_next_month - timedelta(days=1)


def get_previous_month_bounds(day: date) -> tuple[date, date]:
    """Return the first and last date of the previous calendar month."""
    first_this_month = day.replace(day=1)
    last_previous_month = first_this_month - timedelta(days=1)
    return get_month_bounds(last_previous_month)


def run_metrics_report(
    client: BetaAnalyticsDataClient,
    start_date: date,
    end_date: date,
    *,
    require_row: bool = False,
) -> dict[str, int]:
    """Fetch users, sessions, and views for an inclusive date range."""
    request = RunReportRequest(
        property=f"properties/{PROPERTY_ID}",
        date_ranges=[
            DateRange(
                start_date=start_date.isoformat(),
                end_date=end_date.isoformat(),
            )
        ],
        metrics=[
            Metric(name="activeUsers"),
            Metric(name="sessions"),
            Metric(name="screenPageViews"),
        ],
    )

    response = client.run_report(request)

    if not response.rows:
        if require_row:
            raise RuntimeError(
                "GA4 returned no aggregate row for "
                f"{start_date.isoformat()} to {end_date.isoformat()}."
            )
        return {"users": 0, "sessions": 0, "views": 0}

    row = response.rows[0]
    return {
        "users": int(row.metric_values[0].value or 0),
        "sessions": int(row.metric_values[1].value or 0),
        "views": int(row.metric_values[2].value or 0),
    }


def make_period_record(
    month: str,
    start_date: date | None,
    end_date: date | None,
    metrics: dict[str, int] | None = None,
) -> dict:
    """Create one JSON record in the format expected by stats.html."""
    values = metrics or {"users": 0, "sessions": 0, "views": 0}
    return {
        "month": month,
        "start_date": start_date.isoformat() if start_date else None,
        "end_date": end_date.isoformat() if end_date else None,
        "users": int(values.get("users", 0)),
        "sessions": int(values.get("sessions", 0)),
        "views": int(values.get("views", 0)),
    }


def load_existing_data() -> dict:
    """Load current JSON and migrate the previous one-record format if needed."""
    if not OUTPUT_FILE.exists():
        return {"months": []}

    try:
        with OUTPUT_FILE.open("r", encoding="utf-8") as file:
            data = json.load(file)
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError(f"Unable to read {OUTPUT_FILE}: {error}") from error

    if not isinstance(data, dict):
        raise RuntimeError(f"{OUTPUT_FILE} must contain a JSON object.")

    if isinstance(data.get("months"), list):
        return data

    # Compatibility with the old flat format:
    # {"month": "2026-07", "users": 3, ...}
    if data.get("month"):
        return {"months": [data]}

    return {"months": []}


def upsert_completed_month(existing_data: dict, completed: dict) -> list[dict]:
    """Insert or replace one completed month while preserving older archives."""
    by_month: dict[str, dict] = {}

    for record in existing_data.get("months", []):
        if not isinstance(record, dict) or not record.get("month"):
            continue
        by_month[record["month"]] = record

    # Preserve optional fields already present in the record, such as topPage.
    old_record = by_month.get(completed["month"], {})
    by_month[completed["month"]] = {**old_record, **completed}

    return [by_month[key] for key in sorted(by_month)]


def main() -> None:
    today = get_taipei_today()
    current_month_start, _ = get_month_bounds(today)
    previous_month_start, previous_month_end = get_previous_month_bounds(today)

    credentials_info = json.loads(CREDENTIALS_JSON)
    credentials = service_account.Credentials.from_service_account_info(
        credentials_info
    )
    client = BetaAnalyticsDataClient(credentials=credentials)

    # 1. Archive the latest fully completed calendar month.
    completed_metrics = run_metrics_report(
        client,
        previous_month_start,
        previous_month_end,
        require_row=True,
    )
    completed = make_period_record(
        previous_month_start.strftime("%Y-%m"),
        previous_month_start,
        previous_month_end,
        completed_metrics,
    )

    # 2. Current month-to-date uses completed days only (through yesterday).
    current_end = today - timedelta(days=1)
    if current_end >= current_month_start:
        current_metrics = run_metrics_report(
            client,
            current_month_start,
            current_end,
        )
        current = make_period_record(
            current_month_start.strftime("%Y-%m"),
            current_month_start,
            current_end,
            current_metrics,
        )

        completed_day_count = (current_end - current_month_start).days + 1
        comparison_end = min(
            previous_month_start + timedelta(days=completed_day_count - 1),
            previous_month_end,
        )
        comparison_metrics = run_metrics_report(
            client,
            previous_month_start,
            comparison_end,
        )
        comparison = make_period_record(
            previous_month_start.strftime("%Y-%m"),
            previous_month_start,
            comparison_end,
            comparison_metrics,
        )
    else:
        # On the first day of a month, no day in the new month is complete yet.
        current = make_period_record(
            current_month_start.strftime("%Y-%m"),
            None,
            None,
        )
        comparison = make_period_record(
            previous_month_start.strftime("%Y-%m"),
            None,
            None,
        )

    existing_data = load_existing_data()
    output = {
        "months": upsert_completed_month(existing_data, completed),
        "current": current,
        "comparison": comparison,
        "updated_at": datetime.now(TAIPEI_TZ).isoformat(timespec="seconds"),
    }

    with OUTPUT_FILE.open("w", encoding="utf-8") as file:
        json.dump(output, file, indent=2, ensure_ascii=False)
        file.write("\n")

    print("GA4 statistics exported successfully:")
    print(json.dumps(output, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
