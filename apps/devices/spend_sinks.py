"""Credit spend sinks the devices app declares to the wallet's economy engine.

The wallet autodiscovers this module. The price is admin-editable in the wallet
(Credits economy → Prices); ``DEFAULT`` only seeds it.
"""

from __future__ import annotations

from . import credits as wallet_credits

DEVICE_SLOT_RESET = wallet_credits.SLOT_RESET_SINK

wallet_credits.register_sink()
