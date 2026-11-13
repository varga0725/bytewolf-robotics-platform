"""Suite hooks for deferred RuntimeWarning and unawaited-coroutine detection."""

from __future__ import annotations

import warnings

from tests import unawaited_coroutine_guard

warnings.filterwarnings("error", category=RuntimeWarning)
unawaited_coroutine_guard.install()
