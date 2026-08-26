import os
import sys

if getattr(sys, 'frozen', False):
    BASE_DIR = os.path.dirname(sys.executable)
else:
    BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

DATA_DIR = os.path.join(BASE_DIR, 'data')
os.makedirs(DATA_DIR, exist_ok=True)

DATABASE_PATH = os.path.join(DATA_DIR, 'retailpos.db')

APP_NAME    = "RetailPOSPro"
APP_VERSION = "0.2.0"

# BackOfficePro API default — overridden by settings table at runtime
DEFAULT_API_URL = "http://localhost:5050"

# Touch target sizes (px) — sized for finger input
TOUCH_BTN_HEIGHT  = 52
TOUCH_BTN_HEIGHT_LG = 90
NUMPAD_BTN_SIZE   = 68
BASKET_ROW_HEIGHT = 46
