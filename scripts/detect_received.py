"""Snapshot real Packhai stock and detect receipt only for arrived_packhai items."""
import argparse
import json
import os
from collections import Counter
from datetime import date, datetime, timedelta
from pathlib import Path
from tempfile import NamedTemporaryFile
from zoneinfo import ZoneInfo

from scripts.build_in_transit import ROOT, build
from scripts.packhai_client import PackhaiClient

TZ = ZoneInfo("Asia/Bangkok")


def save_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as file:
        temporary = Path(file.name)
        json.dump(value, file, ensure_ascii=False, indent=2, allow_nan=False)
        file.write("\n")
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def load_snapshot(path):
    snapshot = json.loads(path.read_text(encoding="utf-8"))
    captured = datetime.fromisoformat(snapshot["captured_at"])
    if captured.tzinfo is None:
        raise ValueError("Snapshot timestamp must include timezone")
    if captured.astimezone(TZ).date().isoformat() != snapshot["date"]:
        raise ValueError("Snapshot date mismatch")
    if not isinstance(snapshot["stocks"], dict) or not snapshot["stocks"]:
        raise ValueError("Snapshot stocks missing")
    for sku, stock in snapshot["stocks"].items():
        if not isinstance(sku, str) or not sku or type(stock) is not int:
            raise ValueError("Invalid snapshot stock")
    return snapshot


def detect(root, client, now=None):
    root = Path(root)
    now = now or datetime.now(TZ)
    if now.tzinfo is None:
        raise ValueError("Current time must include timezone")
    now = now.astimezone(TZ)
    # Validate without touching root output before any detection.
    if not (root / "in_transit_batches").is_dir():
        raise ValueError("Missing in_transit_batches directory")
    batches = [(p, json.loads(p.read_text(encoding="utf-8")))
               for p in sorted((root / "in_transit_batches").glob("*.json"))]
    # Use the same validator in an isolated staging tree.
    import tempfile
    with tempfile.TemporaryDirectory() as directory:
        staging = Path(directory)
        (staging / "in_transit_batches").mkdir()
        for path, batch in batches:
            save_json(staging / "in_transit_batches" / path.name, batch)
        build(staging)

    inventory = client.fetch_inventory()
    if not isinstance(inventory, dict) or not inventory:
        raise ValueError("Empty Packhai inventory")
    if any(not isinstance(sku, str) or not sku or type(qty) is not int
           for sku, qty in inventory.items()):
        raise ValueError("Invalid Packhai inventory")
    today = now.date()
    snapshot_path = root / "stock_snapshots" / f"{today.isoformat()}.json"
    snapshot = dict(date=today.isoformat(), captured_at=now.isoformat(), stocks=inventory)
    # First successful snapshot of each day is immutable, including manual reruns.
    if not snapshot_path.exists():
        save_json(snapshot_path, snapshot)
    else:
        load_snapshot(snapshot_path)

    candidates = []
    for path, batch in batches:
        for item in batch["items"]:
            if item.get("status", batch["status"]) == "arrived_packhai":
                candidates.append((path, batch, item))
    counts = Counter(item["sku"] for _, _, item in candidates)
    plans = []
    for path, batch, item in candidates:
        state = item.setdefault("receipt_check", {})
        arrival = item.get("arrived_packhai_date", batch.get("arrived_packhai_date",
                          state.get("arrived_date", today.isoformat())))
        arrived = date.fromisoformat(arrival)
        if arrived > today:
            raise ValueError(f"{item['sku']}: arrival date is in the future")
        state.setdefault("arrived_date", arrival)
        if state["arrived_date"] != arrival:
            raise ValueError("Arrival date changed after baseline lock")
        baseline_date = arrived - timedelta(days=1)
        previous = root / "stock_snapshots" / f"{baseline_date.isoformat()}.json"
        if not previous.exists():
            print(f"WAIT {item['sku']}: missing snapshot {baseline_date}")
            continue
        previous_data = load_snapshot(previous)
        sku = item["sku"]
        if sku not in previous_data["stocks"] or sku not in inventory:
            print(f"WAIT {sku}: missing actual stock")
            continue
        baseline = dict(date=baseline_date.isoformat(),
                        captured_at=previous_data["captured_at"],
                        stock=previous_data["stocks"][sku])
        state.setdefault("baseline", baseline)
        if state["baseline"] != baseline:
            raise ValueError(f"{sku}: locked baseline was changed")
        if counts[sku] != 1:
            print(f"WAIT {sku}: multiple arrived records; cannot attribute receipt")
            continue
        start = datetime.fromisoformat(baseline["captured_at"]).astimezone(TZ)
        # Never reuse one observed stock increase for another shipment.
        overlaps = any(
            other["sku"] == sku
            and other.get("receipt_check", {}).get("received_at")
            and datetime.fromisoformat(other["receipt_check"]["received_at"]) >= start
            for _, other_batch in batches for other in other_batch["items"]
        )
        if overlaps:
            print(f"WAIT {sku}: overlapping previously received shipment")
            continue
        plans.append((item, start, baseline["stock"]))

    if plans:
        first_day = min(start.date() for _, start, _ in plans)
        days = [first_day + timedelta(days=i)
                for i in range((today - first_day).days + 1)]
        lines = client.fetch_shipment_lines(days)
        # Deduplicate by the existing Packhai source_key and respect snapshot cutoffs.
        unique = {}
        for line in lines:
            key = line["source_key"]
            if key in unique and unique[key] != line:
                raise ValueError("Conflicting shipment source_key")
            unique[key] = line
        for item, start, baseline_stock in plans:
            outbound = 0
            for line in unique.values():
                if line["sku"] != item["sku"]:
                    continue
                shipped = datetime.fromisoformat(line["shipped_at"])
                if shipped.tzinfo is None:
                    shipped = shipped.replace(tzinfo=TZ)
                qty = line["quantity"]
                if type(qty) is not int or qty <= 0:
                    raise ValueError("Invalid shipment quantity")
                if start < shipped <= now:
                    outbound += qty
            inferred = inventory[item["sku"]] - baseline_stock + outbound
            print(f"CHECK {item['sku']}: inferred={inferred}, threshold={item['qty'] - 10}")
            if inferred >= item["qty"] - 10:
                item["status"] = "received"
                item["receipt_check"].update(received_at=now.isoformat(),
                                             inferred_qty=inferred, outbound_qty=outbound)

    # No batch writes occur until all API reads and calculations succeed.
    for path, batch in batches:
        if batch["status"] == "arrived_packhai" and all(
                item.get("status", batch["status"]) == "received" for item in batch["items"]):
            batch["status"] = "received"
        original = json.loads(path.read_text(encoding="utf-8"))
        if batch != original:
            save_json(path, batch)
    return build(root)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args()
    token = os.getenv("PACKHAI_TOKEN", "").strip()
    if not token:
        parser.exit(1, "PACKHAI_TOKEN is required; no receipt detection performed\n")
    detect(args.root, PackhaiClient(token, 314))


if __name__ == "__main__":
    main()
