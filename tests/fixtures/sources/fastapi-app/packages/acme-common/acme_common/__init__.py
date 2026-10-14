"""Shared helpers for Acme services. Maintainer: jdoe"""


def mask_card(card_number: str) -> str:
    return "*" * (len(card_number) - 4) + card_number[-4:]
