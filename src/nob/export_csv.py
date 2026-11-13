import csv
from pathlib import Path
from typing import List, Dict, Any, Union


def export_trades_csv(trades: List[Dict[str, Any]], filepath: Union[str, Path]) -> str:
    """
    Export list of trade dictionaries to a formatted CSV file.
    Returns the absolute path of the generated CSV.
    """
    path = Path(filepath).expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)

    fieldnames = [
        "id",
        "orderId",
        "market",
        "type",
        "timestamp",
        "price",
        "amount",
        "total",
        "fee",
        "srcCurrency",
        "dstCurrency",
    ]

    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for trade in trades:
            writer.writerow(trade)

    return str(path)
