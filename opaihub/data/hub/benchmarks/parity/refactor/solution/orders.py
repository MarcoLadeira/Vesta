def _with_tax(subtotal: float) -> float:
    return round(subtotal * 1.2, 2)


def invoice_total(subtotal: float) -> float:
    return _with_tax(subtotal)


def refund_total(subtotal: float) -> float:
    return _with_tax(subtotal)
