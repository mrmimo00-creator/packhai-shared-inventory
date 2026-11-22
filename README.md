# Packhai shared inventory

`in_transit_batches/*.json` is the only source of truth. Maintain each shipment
once in its batch file, preserving all existing shipment and item metadata.
`in_transit.json` is automatically generated; never edit it manually.

After batch changes reach main, GitHub Actions validates every batch and commits
the derived root file. Manual build: `python scripts/build_in_transit.py`.
Invalid batches fail without replacing the existing summary.

Required batch fields: unique batch_id, shipment_date, status, total_qty, items.
Items require nonempty sku and positive qty; their sum must equal total_qty.
Item status normally matches batch status. An arrived_packhai batch may also
contain received items to represent independent SKU receipt.
Generated date uses shipment_date, and separate batches remain separate records.
received batches stay in the batch directory but are excluded from the summary.

Migration audit: the old root had no corresponding batches for these records:
- 2026-10-27 / in_transit: 11 rows, 6516 units, older than 25 days on migration.
- 2026-11-12 / received: 11 rows, 6516 units.
Both groups contained Blonde, Caffè Verona, Colombia, Espresso, House Blend,
Pike Place, Breakfast, Starbucks vanilla, Italian Style, Starbucks caramel,
and Ristretto. No historical batches were invented; original rows remain in Git history.

## Automatic formal receipt detection

Configure the repository Actions secret PACKHAI_TOKEN (the same Packhai token
used by the purchase assistant). No credentials are stored in source.
The receipt workflow runs daily at 23:50 Asia/Bangkok, on batch changes, or
manually. It reuses an unchanged copy of the existing purchase assistant client
(9647c47), shop 314, current warehouse selection and sendToExpressDatetime
shipment-line quantity semantics.

Daily snapshots are retained in stock_snapshots/YYYY-MM-DD.json, with date,
captured_at and a SKU-to-real-stock map. The first successful snapshot each day
is immutable; reruns do not replace it. There is no history pruning.

Only items whose effective status is arrived_packhai enter detection.
Set arrived_packhai_date (YYYY-MM-DD) on the item or batch when known; otherwise
the first detection day is recorded and locked in item.receipt_check.arrived_date.
The baseline is the exact previous calendar day's snapshot, locked with its
capture time and SKU stock. Missing baseline or missing actual SKU stock means
WAIT; no historical stock is invented and no other date is substituted.
For a preexisting arrival without a date, supply the actual arrival date;
an unavailable historical snapshot requires manual verification.

Inferred receipt = current stock - locked baseline stock + actual outbound
quantity since that snapshot, up to the current stock observation.
Shipment lines are deduplicated by source_key. Timestamp cutoffs prevent counting
shipments already reflected in the baseline. At inferred receipt >= item.qty - 10,
only that item becomes received, with calculation evidence retained in receipt_check.
The batch becomes received only when all items are received.

Multiple pending arrival records for the same SKU, or an overlapping previously
auto-received shipment, wait for manual attribution to avoid double-counting.
All API reads must succeed before batch status writes. The daily snapshot may
still be retained locally when a later API call fails; a failed workflow commits
nothing. Existing snapshots and ordinary in_transit/received items are not altered.
After successful detection the root summary is rebuilt and committed together
with batches and snapshots. Ordinary summary generation remains independent
of the Packhai credential.

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
