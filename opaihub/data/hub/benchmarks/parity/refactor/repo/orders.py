def invoice_total(subtotal: float) -> float:
    return round(subtotal + subtotal * 0.2, 2)


def refund_total(subtotal: float) -> float:
    return round(subtotal + subtotal * 0.2, 2)
