"""Docker healthcheck: server is "up" if it answers 200 or 401.

401 is expected when HTTP basic auth is enabled and the probe carries no
credentials — it still proves the server is accepting connections.
"""

import sys
import urllib.error
import urllib.request

URL = "http://127.0.0.1:7860"

try:
    urllib.request.urlopen(URL, timeout=3)
except urllib.error.HTTPError as e:
    sys.exit(0 if e.code in (200, 401) else 1)
except Exception:
    sys.exit(1)
sys.exit(0)
