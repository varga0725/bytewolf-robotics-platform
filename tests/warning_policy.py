"""Turn selected warnings into errors for the whole unittest process."""

from __future__ import annotations

import warnings

warnings.filterwarnings("error", category=RuntimeWarning)
