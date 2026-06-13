from __future__ import annotations


def cn_to_yahoo_symbol(code: str) -> str:
    if code.endswith(".SS") or code.endswith(".SZ") or code.endswith(".BJ"):
        return code
    if code.startswith(("0", "2", "3")):
        return f"{code}.SZ"
    if code.startswith(("4", "8")):
        return f"{code}.BJ"
    return f"{code}.SS"
