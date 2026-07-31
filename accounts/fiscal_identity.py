"""Pure fiscal identity helpers shared by models, forms and services."""

from __future__ import annotations


def normalize_fiscal_document(value) -> str:
    return "".join(char for char in str(value or "") if char.isdigit())


def is_valid_cuit(value) -> bool:
    digits = normalize_fiscal_document(value)
    if len(digits) != 11:
        return False
    weights = (5, 4, 3, 2, 7, 6, 5, 4, 3, 2)
    check = 11 - (
        sum(int(digit) * weight for digit, weight in zip(digits[:10], weights)) % 11
    )
    if check == 11:
        check = 0
    elif check == 10:
        check = 9
    return check == int(digits[-1])
