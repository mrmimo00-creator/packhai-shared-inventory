# Packhai shared inventory

`in_transit_batches/*.json` is the only source of truth. Maintain each shipment
once in its batch file, preserving all existing shipment and item metadata.
`in_transit.json` is automatically generated; never edit it manually.

After batch changes reach main, GitHub Actions validates every batch and commits
the derived root file. Manual build: `python scripts/build_in_transit.py`.
Invalid batches fail without replacing the existing summary.

Required batch fields: unique batch_id, shipment_date, status, total_qty, items.
Items require nonempty sku and positive qty; their sum must equal total_qty.
Optional item status must match batch status; update both together.
Generated date uses shipment_date, and separate batches remain separate records.
received batches stay in the batch directory but are excluded from the summary.

Migration audit: the old root had no corresponding batches for these records:
- 2026-10-27 / in_transit: 11 rows, 6516 units, older than 25 days on migration.
- 2026-11-12 / received: 11 rows, 6516 units.
Both groups contained Blonde, Caffè Verona, Colombia, Espresso, House Blend,
Pike Place, Breakfast, Starbucks vanilla, Italian Style, Starbucks caramel,
and Ristretto. No historical batches were invented; original rows remain in Git history.

Each record contains at least:

```json
{
  "date": "2026-10-10",
  "sku": "Espresso",
  "qty": 300,
  "status": "in_transit"
}
```

Allowed status values:

- `in_transit`: 运输途中
- `arrived_packhai`: 已到 Packhai，但系统尚未入库
- `received`: 已正式入库

The purchase assistant counts `in_transit` and `arrived_packhai` records that
are no more than 25 days old. Shopee sync counts only `arrived_packhai`.
