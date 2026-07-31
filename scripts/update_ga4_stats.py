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


def main():
    today = get_taipei_today()

    # GitHub Actions may run this workflow every day at 02:00 Taiwan time.
    # Only the first day of each month should actually collect GA4 data.
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

    data = {
        "month": start_date.strftime("%Y-%m"),
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "users": int(row.metric_values[0].value),
        "sessions": int(row.metric_values[1].value),
        "views": int(row.metric_values[2].value),
    }

    output_file = "stats-data.json"

    with open(output_file, "w", encoding="utf-8") as file:
        json.dump(data, file, indent=2, ensure_ascii=False)

    print("GA4 monthly statistics exported successfully:")
    print(json.dumps(data, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
