"""Put `src` on the path once, for every layer of the pyramid.

Previously each test file did its own `sys.path.insert`, which is the same line
repeated in three places and one of them would eventually drift.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
