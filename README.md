# garmin_sync

A Python script that copies weigh-ins from Garmin Connect, such as those from a Garmin Index scale, into a Google Sheet.

Each run logs in to Garmin Connect, fetches weigh-ins for today and the previous three days, and adds any missing days to the spreadsheet tab you choose in your settings. It's safe to run repeatedly, because days already in the sheet are skipped.

## What it writes

Each new row uses these columns:

| Column | Value |
|---|---|
| A | Date and time of the weigh-in, in London time |
| B | Weight (kg) |
| C | Formula for the change in weight since the row above, for example `=IF(ISBLANK(B36:B37),"",B37-B36)` in row 37. Left empty in row 2, which has no earlier weight |
| D | BMI |
| E | Body fat (%) |
| F | Muscle mass (kg) |
| G | Bone mass (kg) |
| H | Body water (%) |
| I | Routine label for that weekday (see below) |

Values are rounded to one decimal place. If Garmin doesn't send a value, its cell is left blank, and readings with no weight are ignored. The formatting of columns A to I is also copied from the row above.

### Routine labels

| Day | Label |
|---|---|
| Monday | Fast Day 1 |
| Tuesday | Fast Day 2 |
| Wednesday | Eating |
| Thursday | Fasting |
| Friday | Eating |
| Saturday | Fasting |
| Sunday | Eating |

To change the labels, edit `ROUTINE_MAP` in `garmin_sync.py`.

### One row per day

The sync keeps the first weigh-in of each day, based on London time. Any later weigh-in that day is skipped, including one that arrives on a later run. If a day's first reading is missed, for example because it hadn't reached Garmin Connect yet, a later reading that day may be the one recorded.

Because it only looks back three days, a weigh-in older than that isn't added if the script wasn't run in time.

## Requirements

- Python 3.12 or newer
- A Garmin Connect account
- A Google Cloud service account with the Google Sheets and Google Drive APIs enabled, and its JSON key file
- A Google spreadsheet with a tab for the weigh-ins, shared with the service account's email address

## Setup

1. Install the dependencies, ideally in a virtual environment:

   ```bash
   python -m pip install -r requirements.txt
   ```

2. Copy `.env.example` to `.env` and fill it in:

   | Variable | Purpose |
   |---|---|
   | `GARMIN_EMAIL` | Garmin Connect email |
   | `GARMIN_PASSWORD` | Garmin Connect password |
   | `SPREADSHEET_NAME` | Name of the Google spreadsheet |
   | `WORKSHEET_NAME` | Name of the tab to write to |
   | `GOOGLE_JSON_PATH` | Path to the service account key. Optional, and defaults to `/config/google_creds.json` |

   Variables already set in the environment take priority over `.env`.

3. Keep your secrets out of Git. `.gitignore` covers `.env`, the `config/` folder and any JSON file with `creds` in its name. A key saved under another name outside `config/` isn't covered.

## Usage

```bash
python garmin_sync.py
```

The script prints what it wrote or skipped, and exits with status 1 if the sync fails. It doesn't schedule itself, so use a tool such as cron or Windows Task Scheduler to run it regularly.

## Limitations

- **Two-step verification:** the script has no way to enter a code, so an unattended login may fail if it's turned on for your Garmin account.
- **Fixed time zone:** the time zone is set near the top of `garmin_sync.py`.
