"""The machine-readable discriminators that ride in an alert's `payload`.

An alert's `kind` is a database enum, and a new value costs a migration and a model change. These
are the finer distinctions inside a kind — why a tracking silence was reported, which channel of
delivery failed — so that the sentence can differ without the schema having to. Both the digest
(`text.py`) and the front read them; neither is allowed to match on English.
"""

from __future__ import annotations

#: TRACKING_DATA_MISSING raised because nobody has typed anything in, not because a provider is quiet.
MANUAL_SILENCE = "MANUAL_NO_ENTRY"
#: TRACKING_DATA_MISSING, by what made the event late: a milestone due at the terminal, an arrival
#: whose ETA has passed, or a box we have no ETA for and have heard nothing about for weeks.
EVENT_OVERDUE = "EVENT_OVERDUE"
ETA_PASSED = "ETA_PASSED"
NO_NEWS = "NO_NEWS"

#: TRACKING_DELIVERY_FAILED about our own e-mail digest rather than about a provider's webhook.
EMAIL_CHANNEL = "email"
