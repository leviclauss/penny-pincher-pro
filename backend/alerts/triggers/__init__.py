"""Alert trigger families.

Each module in this package owns one class of trigger (digests, position
management, intraday setups, etc.) and produces structured payloads ready
for ``alerts.dispatcher.dispatch``. Trigger modules are pure with respect
to channel selection — fan-out lives in the dispatcher.
"""
