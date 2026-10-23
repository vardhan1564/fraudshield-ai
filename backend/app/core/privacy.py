"""PII masking for logs and API responses (spec 08 security checklist)."""


def mask_phone(value: str | None) -> str | None:
    """`9812345621` -> `98xxxxxx21`; also used for account numbers."""
    if value is None:
        return None
    digits = value.strip()
    if len(digits) <= 4:
        return "x" * len(digits)
    return f"{digits[:2]}{'x' * (len(digits) - 4)}{digits[-2:]}"


def mask_vpa(value: str) -> str:
    """`9812345621@ybl` -> `98xxxxxx21@ybl`, `ravi.kumar@okaxis` -> `raxxxxxxar@okaxis`."""
    local, at, handle = value.partition("@")
    return f"{mask_phone(local)}{at}{handle}"


def mask_ip(value: str) -> str:
    """`49.36.10.7` -> `49.36.x.x`; IPv6 keeps its first two groups."""
    sep = "." if "." in value else ":"
    parts = value.split(sep)
    return sep.join(parts[:2] + ["x"] * (len(parts) - 2))
