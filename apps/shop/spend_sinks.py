"""Credit spend sinks the shop declares to the wallet's economy engine.

The wallet autodiscovers this module. Both sinks are *derived*: the shop passes the
credits of every purchase explicitly (package price or file price in dollars × the one
``WalletSettings.credits_per_currency`` rate, or an optional per-package override), so
the wallet's ``CreditPrice`` default for them is never used.
"""

from __future__ import annotations

from . import wallet_gateway

PACKAGE_PURCHASE = "package.purchase"
FILE_PURCHASE = "file.purchase"

SINKS = (
    (PACKAGE_PURCHASE, "Buy a download package"),
    (FILE_PURCHASE, "Buy a paid or featured file"),
)

for _key, _label in SINKS:
    wallet_gateway.register_sink(_key, _label, derived=True)
