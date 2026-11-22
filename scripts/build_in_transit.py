"""Generate in_transit.json exclusively from validated shipment batches."""
import argparse
import json
import math
import os
from datetime import date
from pathlib import Path
from tempfile import NamedTemporaryFile

ROOT = Path(__file__).resolve().parents[1]
STATUSES = {"in_transit", "arrived_packhai", "received"}


def positive(value):
    return type(value) in (int, float) and math.isfinite(value) and value > 0


def build(root=ROOT):
    root = Path(root)
    directory = root / "in_transit_batches"
    if not directory.is_dir():
        raise ValueError("Missing in_transit_batches directory")
    records, seen = [], set()
    for path in sorted(directory.glob("*.json")):
        batch = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(batch, dict):
            raise ValueError(f"{path.name}: batch must be an object")
        batch_id = batch.get("batch_id")
        if not isinstance(batch_id, str) or not batch_id.strip() or batch_id in seen:
            raise ValueError(f"{path.name}: missing or duplicate batch_id")
        seen.add(batch_id)
        status = batch.get("status")
        if not isinstance(status, str) or status not in STATUSES:
            raise ValueError(f"{batch_id}: unknown status")
        shipment_date = batch.get("shipment_date")
        if not isinstance(shipment_date, str):
            raise ValueError(f"{batch_id}: missing shipment_date")
        date.fromisoformat(shipment_date)
        items = batch.get("items")
        if not isinstance(items, list) or not items:
            raise ValueError(f"{batch_id}: items must be a nonempty array")
        total = 0
        for item in items:
            if not isinstance(item, dict):
                raise ValueError(f"{batch_id}: item must be an object")
            sku, qty = item.get("sku"), item.get("qty")
            if not isinstance(sku, str) or not sku.strip() or not positive(qty):
                raise ValueError(f"{batch_id}: invalid sku or qty")
            if "status" in item and item["status"] != status:
                raise ValueError(f"{batch_id}: contradictory item status")
            total += qty
            if status != "received":
                records.append(dict(date=shipment_date, sku=sku, qty=qty,
                                    status=status, batch_id=batch_id))
        if not positive(batch.get("total_qty")) or batch["total_qty"] != total:
            raise ValueError(f"{batch_id}: total_qty differs from items sum")
    # All batches must pass before the existing output can be replaced.
    content = json.dumps(records, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    destination = root / "in_transit.json"
    if destination.exists() and destination.read_text(encoding="utf-8") == content:
        return records
    with NamedTemporaryFile(mode="w", encoding="utf-8", dir=root, delete=False) as file:
        temporary = Path(file.name)
        file.write(content)
    try:
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
    return records


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args()
    try:
        rows = build(args.root)
    except (ValueError, TypeError, OSError) as error:
        parser.exit(1, f"Inventory build failed: {error}\n")
    print(f"Generated {len(rows)} records; total qty {sum(row['qty'] for row in rows)}")
