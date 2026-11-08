"""Credit spend sinks the blog declares to the wallet's economy engine.

The wallet autodiscovers this module. The sink is *derived*: the shop passes the
credits of every purchase explicitly (post price in dollars x the one wallet rate),
so staff never edit a separate price for it.
"""

from __future__ import annotations

from apps.wallet.api import SpendSink, register_spend_sink

FEATURED_PURCHASE = "blog.featured_purchase"

register_spend_sink(
    SpendSink(
        key=FEATURED_PURCHASE,
        label="Buy a featured or paid article",
        app="blog",
        default_credits=0,
        derived=True,
    )
)
