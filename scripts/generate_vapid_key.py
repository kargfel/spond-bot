#!/usr/bin/env python3
"""Generates the VAPID key that signs Web Push notifications.

Run once and put the output line in .env:
    python scripts/generate_vapid_key.py

Keep the key: replacing it later makes every device's existing notification
subscription invalid, so members have to turn notifications on again.
"""
import base64

from py_vapid import Vapid02


def generate() -> str:
    vapid = Vapid02()
    vapid.generate_keys()
    raw = vapid.private_key.private_numbers().private_value.to_bytes(32, "big")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


if __name__ == "__main__":
    print(f"VAPID_PRIVATE_KEY={generate()}")
