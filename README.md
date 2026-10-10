# Packhai shared inventory

`in_transit.json` is the single shared source of truth for inventory that is
not yet reflected in Packhai's formal stock.

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
