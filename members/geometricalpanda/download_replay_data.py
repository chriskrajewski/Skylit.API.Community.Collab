import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests
from requests.exceptions import HTTPError, RequestException

@dataclass
class Window:
    start: str
    end: str

@dataclass
class Boundary:
    """Represents a datetime boundary to start/end pulling data from skylit"""
    year: int # e.g. 2025
    month: int # e.g. 10
    day: int # e.g. 20
    hour: int # e.g. 9
    minute: int # e.g. 30
    second: int # e.g. 0
    tzinfo: ZoneInfo # e.g. ZoneInfo("America/New_York")

# set these values
api_key = 'abc123' # skylit API key
start_boundary = Boundary(
    year=2025,
    month=10,
    day=20,
    hour=9,
    minute=30,
    second=0,
    tzinfo=ZoneInfo("America/New_York")
)
end_boundary = Boundary(
    year=2026,
    month=10,
    day=18,
    hour=16,
    minute=0,
    second=0,
    tzinfo=ZoneInfo("America/New_York")
)

def create_time_windows(input_start_boundary: Boundary, input_end_boundary: Boundary):
    # Setup time zone and start/end boundaries
    tz = ZoneInfo("America/New_York")
    start_boundary = datetime(input_start_boundary.year, input_start_boundary.month, input_start_boundary.day, input_start_boundary['hour'], input_start_boundary['minute'], input_start_boundary['second'], tzinfo=tz)
    end_boundary = datetime(
        year=input_end_boundary.year,
        month=input_end_boundary.month,
        day=input_end_boundary.day,
        hour=input_end_boundary.hour,
        minute=input_end_boundary.minute,
        second=input_end_boundary.second,
        tzinfo=input_end_boundary.tzinfo
    )

    windows = []
    while start_boundary < end_boundary:
        # 1. Skip weekends (5 = Saturday, 6 = Sunday)
        if start_boundary.weekday() in (5, 6):
            start_boundary = start_boundary.replace(hour=9, minute=30, second=0) + timedelta(
                days=1)
            continue

        # 2. Define the 15-minute slot windows
        start_date = start_boundary
        end_date = start_date + timedelta(minutes=15)

        # 3. Check if the end date pushes past the 3:00 PM (15:00) cutoff
        if end_date.time() > datetime.strptime("15:00", "%H:%M").time():
            # Fast-forward to 9:30 AM the next calendar day
            start_boundary = start_date.replace(hour=9, minute=30, second=0) + timedelta(days=1)
            # No 'continue' here; let the loop naturally evaluate the new start_boundary next
        else:
            # 4. Create 15 minute window in RFC3339
            window = Window(start=start_date.isoformat(), end=end_date.isoformat())
            print(f"Window: {window.start} to {window.end}")
            windows.append(window)
            # Move the tracking clock forward to the next 15-minute slot segment
            start_boundary = end_date
    return windows

def fetch_data_from_skylit(tickers: list[str], time_window: Window):
    url = "https://api.skylit.ai/v1/historical/range"
    symbols = ','.join(tickers)
    # Query parameters structured as a dictionary
    params = {
        # "symbols": "SPY,QQQ",
        "symbols":symbols,
        "from": time_window.start,
        "to":time_window.end,
        "metric": "gamma",
        "maxStrikes": 400,
        "maxExpirations": 1,
    }

    # Optional headers: Add your API key here if required (e.g., {"Authorization": "Bearer YOUR_KEY"})
    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {api_key}"
    }

    try:
        response = requests.get(url, params=params, headers=headers, timeout=15)
        response.raise_for_status()

        data = response.json()
        return data

    except HTTPError as http_err:
        print(f"HTTP error occurred: {http_err}")
    except RequestException as req_err:
        print(f"Network error occurred: {req_err}")
    except ValueError:
        print("Error: Response could not be parsed as JSON.")

    return None

def fetch_and_download_data():
    windows = create_time_windows()
    # find tickers
    with open("tickers.json", "r") as file:
        all_ticker_lists = json.load(file)

    # divide into chunks of size 5
    partitioned_ticker_lists = [all_ticker_lists[i:i + 5] for i in range(0, len(all_ticker_lists), 5)]
    for tickers in partitioned_ticker_lists:
        for window in windows:
            fetch_data_from_skylit(tickers, window)
    print('done')

if __name__ == '__main__':
    create_time_windows(start_boundary, end_boundary)
    # fetch_and_download_data()

