def currency(amount: float) -> str:
    """Format a dollar amount for display: $1,234.56"""
    return f"${amount:,.2f}"


def gst_from_total(total: float, rate: float = 10.0) -> float:
    """Extract GST from a GST-inclusive total (Australian GST method)."""
    return round(total * rate / (100 + rate), 2)
