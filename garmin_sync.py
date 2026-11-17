"""Sync Garmin Index scale weigh-ins into a Google Sheet.

Writes one row per day: the first weigh-in of that day (London time).

If column A already has a row for that day with no weight in column B (for
example future dates typed in to plan ahead), the reading is written into that
row. If the day's row already has a weight, the reading is skipped. If the day
has no row, a new one is inserted in date order (or added at the end).

Settings are read from environment variables or a .env file, never from this file:
  GARMIN_EMAIL, GARMIN_PASSWORD   - Garmin Connect login
  SPREADSHEET_NAME                - name of the Google spreadsheet
  WORKSHEET_NAME                  - name of the tab to write to
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
SPREADSHEET_NAME = os.environ.get("SPREADSHEET_NAME")
WORKSHEET_NAME = os.environ.get("WORKSHEET_NAME")

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

# Number format applied to column A when filling a placeholder row, so the time
# shows the same way as in rows the script adds
TIMESTAMP_PATTERN = "dd/mmm/yy hh:mm"

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


def change_formula(row):
    """Column C formula: change in weight since the row above."""
    prev = row - 1
    return f'=IF(ISBLANK(B{prev}:B{row}),"",B{row}-B{prev})'


def metric(log, key, divisor=1):
    """Rounded value, or a blank cell if Garmin didn't send one."""
    value = log.get(key)
    if value is None:
        return ""
    return round(value / divisor, 1)


def main():
    if not all((GARMIN_EMAIL, GARMIN_PASSWORD, SPREADSHEET_NAME, WORKSHEET_NAME)):
        print("Error: set GARMIN_EMAIL, GARMIN_PASSWORD, SPREADSHEET_NAME and "
              "WORKSHEET_NAME environment variables.")
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
        column_b = sheet.col_values(2, value_render_option="UNFORMATTED_VALUE")
        last_row = len(column_a)

        dated_rows = []        # (row, day) for every row with a date in column A
        filled_days = set()    # days that already have a weight
        placeholders = {}      # day -> first row for that day with no weight yet
        for row, cell in enumerate(column_a, start=1):
            day = sheet_day(cell)
            if not day:
                if cell not in ("", None) and row > 1:
                    print(f"Warning: couldn't read a date in cell A{row}: {cell!r}")
                continue
            dated_rows.append((row, day))
            weight = column_b[row - 1] if row - 1 < len(column_b) else ""
            if weight not in ("", None):
                filled_days.add(day)
            else:
                placeholders.setdefault(day, row)

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

                day = local_dt.date()
                label = local_dt.strftime('%d/%b/%y %H:%M')

                # One row per day: keep the first reading, skip any others
                if day in filled_days:
                    print(f"Skipping {label}: that day already has a weight.")
                    continue

                values = [[standard_timestamp, weight_kg, "",
                           bmi, body_fat, muscle_mass_kg,
                           bone_mass_kg, body_water, routine_text]]

                if day in placeholders:
                    # Fill the row that's already there, keeping its formatting
                    row = placeholders.pop(day)
                    action = "Filled"
                else:
                    # Insert before the first later date, or add at the end
                    later = [r for r, d in dated_rows if d > day]
                    if later:
                        row = min(later)
                        sheet.insert_row([""], index=row, inherit_from_before=True)
                        # Everything from that row down moves down one
                        dated_rows = [(r + 1 if r >= row else r, d) for r, d in dated_rows]
                        placeholders = {d: (r + 1 if r >= row else r)
                                        for d, r in placeholders.items()}
                        last_row += 1
                        action = "Inserted"
                    else:
                        row = last_row + 1
                        last_row = row
                        action = "Wrote"
                    dated_rows.append((row, day))

                # Column C: change in weight since the row above. Row 2 has no earlier
                # weight to compare with, only the header, so its cell stays empty.
                values[0][2] = change_formula(row) if row > 2 else ""

                updates = [{"range": f"A{row}:I{row}", "values": values}]
                # Keep the next row's change formula pointing at this row
                if row < last_row:
                    updates.append({"range": f"C{row + 1}",
                                    "values": [[change_formula(row + 1)]]})
                sheet.batch_update(updates, value_input_option="USER_ENTERED")
                print(f"{action} row {row} for {standard_timestamp}")

                try:
                    if action == "Filled":
                        # Show the time like other rows, without touching colours or shading
                        sheet.format(f"A{row}", {"numberFormat": {
                            "type": "DATE_TIME", "pattern": TIMESTAMP_PATTERN}})
                    elif row > 2:
                        # Copy formatting from the row above
                        sheet.copy_range(f"A{row - 1}:I{row - 1}", f"A{row}:I{row}",
                                         paste_type="PASTE_FORMAT")
                except Exception as fmt_err:
                    print(f"Note: formatting skipped: {fmt_err}")

                filled_days.add(day)
                appended_any = True

        if not appended_any:
            print("All recent logs are already present in the spreadsheet.")

    except Exception as e:
        print(f"Error executing sync: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
