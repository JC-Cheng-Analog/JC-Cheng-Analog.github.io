import json
import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from google.analytics.data_v1beta import BetaAnalyticsDataClient
from google.analytics.data_v1beta.types import (
    DateRange,
    Metric,
    RunReportRequest,
)
from google.oauth2 import service_account


PROPERTY_ID = os.environ["GA4_PROPERTY_ID"]
CREDENTIALS_JSON = os.environ["GA4_CREDENTIALS"]
TAIPEI_TZ = ZoneInfo("Asia/Taipei")
OUTPUT_FILE = "stats-data.json"


def get_taipei_today():
    """Return today's date in Taiwan time."""
    return datetime.now(TAIPEI_TZ).date()


def get_previous_month():
    """Return the first and last date of the previous calendar month."""
    today = get_taipei_today()

    first_this_month = today.replace(day=1)
    last_previous_month = first_this_month - timedelta(days=1)
    first_previous_month = last_previous_month.replace(day=1)

    return first_previous_month, last_previous_month


def load_existing_data():
    """Load existing monthly history, supporting the old single-month format too."""
    if not os.path.exists(OUTPUT_FILE):
        return {"months": []}

    with open(OUTPUT_FILE, "r", encoding="utf-8") as file:
        existing = json.load(file)

    # New format
    if isinstance(existing, dict) and isinstance(existing.get("months"), list):
        return existing

    # Backward compatibility with the old single-month format
    if isinstance(existing, dict) and "month" in existing:
        return {
            "months": [
                {
                    "month": existing["month"],
                    "start_date": existing.get("start_date"),
                    "end_date": existing.get("end_date"),
                    "users": int(existing.get("users", 0)),
                    "sessions": int(existing.get("sessions", 0)),
                    "views": int(existing.get("views", 0)),
                }
            ]
        }

    return {"months": []}


def save_month(history, month_data):
    """Insert or replace one month, then keep the history sorted chronologically."""
    months = history.setdefault("months", [])

    replaced = False
    for index, item in enumerate(months):
        if item.get("month") == month_data["month"]:
            months[index] = month_data
            replaced = True
            break

    if not replaced:
        months.append(month_data)

    months.sort(key=lambda item: item.get("month", ""))

    with open(OUTPUT_FILE, "w", encoding="utf-8") as file:
        json.dump(history, file, indent=2, ensure_ascii=False)


def main():
    today = get_taipei_today()

    # GitHub Actions may run every day at 02:00 Taiwan time.
    # Only the first day of each month should collect the previous month's data.
    if today.day != 1:
        print(
            f"Today is {today.isoformat()} in Asia/Taipei. "
            "Not the first day of the month, so GA4 export is skipped."
        )
        return

    credentials_info = json.loads(CREDENTIALS_JSON)

    credentials = service_account.Credentials.from_service_account_info(
        credentials_info
    )

    client = BetaAnalyticsDataClient(credentials=credentials)

    start_date, end_date = get_previous_month()

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
        raise RuntimeError(
            f"GA4 returned no data for {start_date.isoformat()} "
            f"to {end_date.isoformat()}."
        )

    row = response.rows[0]

    month_data = {
        "month": start_date.strftime("%Y-%m"),
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "users": int(row.metric_values[0].value),
        "sessions": int(row.metric_values[1].value),
        "views": int(row.metric_values[2].value),
    }

    history = load_existing_data()
    save_month(history, month_data)

    print("GA4 monthly statistics updated successfully:")
    print(json.dumps(month_data, indent=2, ensure_ascii=False))
    print(f"Total stored months: {len(history['months'])}")


if __name__ == "__main__":
    main()
