"""Sync Garmin Index scale weigh-ins into the "Health Metrics & Diet Log" Google Sheet.

Writes one row per day: the first weigh-in of that day (London time). Later
weigh-ins on a day that already has a row are skipped.

Credentials are read from environment variables or a .env file, never from this file:
  GARMIN_EMAIL, GARMIN_PASSWORD   - Garmin Connect login
  GOOGLE_JSON_PATH (optional)     - service account JSON (default /config/google_creds.json)
"""

import os
import sys
import datetime
import zoneinfo

from dotenv import load_dotenv
from garminconnect import Garmin
import gspread
from google.oauth2.service_account import Credentials

# Load .env if there is one; variables already set in the environment take priority
load_dotenv()

# 1. Garmin credentials (from the environment)
GARMIN_EMAIL = os.environ.get("GARMIN_EMAIL")
GARMIN_PASSWORD = os.environ.get("GARMIN_PASSWORD")

# 2. Google Sheets setup
GOOGLE_JSON_PATH = os.environ.get("GOOGLE_JSON_PATH", "/config/google_creds.json")
SPREADSHEET_NAME = "Health Metrics & Diet Log"
WORKSHEET_NAME = "Weight"

LOCAL_TZ = zoneinfo.ZoneInfo("Europe/London")
DAYS_BACK = 3  # today plus the previous 3 days

# Map Python's .weekday() index numbers to the protocol labels
ROUTINE_MAP = {
    0: "Fast Day 1",  # Monday
    1: "Fast Day 2",  # Tuesday
    2: "Eating",      # Wednesday
    3: "Fasting",     # Thursday
    4: "Eating",      # Friday
    5: "Fasting",     # Saturday
    6: "Eating",      # Sunday
}

# Google Sheets stores dates as a count of days since this date
SHEET_EPOCH = datetime.date(1899, 12, 30)

# Formats to try for column A cells that hold text rather than a real date
TEXT_DATE_FORMATS = (
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d",
    "%d/%b/%y %H:%M",
    "%d/%b/%y",
    "%d/%m/%Y %H:%M:%S",
    "%d/%m/%Y",
)


def sheet_day(cell):
    """Calendar date held in a column A cell, or None if it isn't a date."""
    if isinstance(cell, (int, float)):
        return SHEET_EPOCH + datetime.timedelta(days=int(cell))
    text = str(cell).strip()
    for date_format in TEXT_DATE_FORMATS:
        try:
            return datetime.datetime.strptime(text, date_format).date()
        except ValueError:
            pass
    return None


def metric(log, key, divisor=1):
    """Rounded value, or a blank cell if Garmin didn't send one."""
    value = log.get(key)
    if value is None:
        return ""
    return round(value / divisor, 1)


def main():
    if not GARMIN_EMAIL or not GARMIN_PASSWORD:
        print("Error: set GARMIN_EMAIL and GARMIN_PASSWORD environment variables.")
        sys.exit(1)

    try:
        # Drive scope is needed to open the spreadsheet by name
        scopes = [
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive",
        ]

        creds = Credentials.from_service_account_file(GOOGLE_JSON_PATH, scopes=scopes)
        gc = gspread.authorize(creds)
        sheet = gc.open(SPREADSHEET_NAME).worksheet(WORKSHEET_NAME)

        # Read the stored values, so the check doesn't depend on how the sheet displays dates
        column_a = sheet.col_values(1, value_render_option="UNFORMATTED_VALUE")
        next_row = len(column_a) + 1
        existing_days = set()
        for row, cell in enumerate(column_a, start=1):
            day = sheet_day(cell)
            if day:
                existing_days.add(day)
            elif cell not in ("", None) and row > 1:
                print(f"Warning: couldn't read a date in cell A{row}: {cell!r}")

        client = Garmin(GARMIN_EMAIL, GARMIN_PASSWORD)
        client.login()

        appended_any = False
        today = datetime.date.today()

        for i in range(DAYS_BACK, -1, -1):
            check_date = today - datetime.timedelta(days=i)
            weight_data = client.get_daily_weigh_ins(check_date.isoformat())
            records = weight_data.get("dateWeightList", [])

            # Earliest first, so the first weigh-in of the day is the one kept
            for log in sorted(records, key=lambda record: record["timestampGMT"]):
                # Garmin timestamps are UTC; convert to London time (handles BST/GMT)
                utc_dt = datetime.datetime.fromtimestamp(
                    log["timestampGMT"] / 1000.0, tz=datetime.timezone.utc
                )
                local_dt = utc_dt.astimezone(LOCAL_TZ)
                standard_timestamp = local_dt.strftime("%Y-%m-%d %H:%M:%S")
                routine_text = ROUTINE_MAP[local_dt.weekday()]

                weight_kg = metric(log, "weight", 1000)
                if weight_kg == "":
                    continue  # no usable reading
                bmi = metric(log, "bmi")
                body_fat = metric(log, "bodyFat")
                muscle_mass_kg = metric(log, "muscleMass", 1000)
                bone_mass_kg = metric(log, "boneMass", 1000)
                body_water = metric(log, "bodyWater")

                # One row per day: keep the first reading, skip any others
                if local_dt.date() in existing_days:
                    print(f"Skipping {local_dt.strftime('%d/%b/%y %H:%M')}: that day already has a row.")
                    continue

                new_row = next_row

                # Column C: change in weight since the row above. Row 2 has no earlier
                # weight to compare with, only the header, so its cell stays empty.
                weight_change = ""
                if new_row > 2:
                    prev = new_row - 1
                    weight_change = f'=IF(ISBLANK(B{prev}:B{new_row}),"",B{new_row}-B{prev})'

                sheet.batch_update(
                    [
                        {
                            "range": f"A{new_row}:I{new_row}",
                            "values": [[standard_timestamp, weight_kg, weight_change,
                                        bmi, body_fat, muscle_mass_kg,
                                        bone_mass_kg, body_water, routine_text]],
                        },
                    ],
                    value_input_option="USER_ENTERED",
                )
                print(f"Wrote row {new_row} for {standard_timestamp}")

                # Copy formatting from the row above
                if new_row > 2:
                    try:
                        sheet.copy_range(f"A{prev}:I{prev}", f"A{new_row}:I{new_row}",
                                         paste_type="PASTE_FORMAT")
                    except Exception as fmt_err:
                        print(f"Note: format copy skipped: {fmt_err}")

                existing_days.add(local_dt.date())
                next_row += 1
                appended_any = True

        if not appended_any:
            print("All recent logs are already present in the spreadsheet.")

    except Exception as e:
        print(f"Error executing sync: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
