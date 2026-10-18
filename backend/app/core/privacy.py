"""PII masking for logs and API responses (spec 08 security checklist)."""


def mask_phone(value: str | None) -> str | None:
    """`9812345621` -> `98xxxxxx21`; also used for account numbers."""
    if value is None:
        return None
    digits = value.strip()
    if len(digits) <= 4:
        return "x" * len(digits)
    return f"{digits[:2]}{'x' * (len(digits) - 4)}{digits[-2:]}"
