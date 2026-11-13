"""Turn selected warnings into errors for the whole unittest suite."""

from __future__ import annotations

import warnings

warnings.filterwarnings("error", category=RuntimeWarning)
