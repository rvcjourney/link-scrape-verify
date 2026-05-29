import os

BROWSER_HEADLESS = os.environ.get('HEADLESS', 'false').lower() == 'true'
SEARCH_ENGINE_URL = "https://www.google.com"
MAX_PAGES = 3
OUTPUT_DIR = "output"
DELAY_BETWEEN_REQUESTS = 2

CHROME_USER_DATA = os.environ.get("LOCALAPPDATA", "")
CHROME_PROFILE   = "Default"
BROWSER_PROFILE_DIR = os.path.join(os.path.dirname(__file__), "browser_profile")
