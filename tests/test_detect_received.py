import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock
from scripts.detect_received import detect, save_json, TZ
from scripts.build_in_transit import build


class ReceiptTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "in_transit_batches").mkdir()
        self.path = self.root / "in_transit_batches/batch.json"
        self.now = datetime(2026, 11, 22, 23, 50, tzinfo=TZ)
        self.client = Mock()
        self.client.fetch_inventory.return_value = {"A": 591, "B": 589, "ordinary": 9999}
        self.client.fetch_shipment_lines.return_value = []
        save_json(self.root / "stock_snapshots/2026-11-21.json",
                  dict(date="2026-11-21", captured_at="2026-11-21T23:50:00+07:00",
                       stocks={"A": 0, "B": 0}))

    def batch(self, status="arrived_packhai"):
        value = dict(batch_id="batch", shipment_date="2026-11-07", status=status,
                     total_qty=1200, arrived_packhai_date="2026-11-22",
                     items=[dict(sku="A", qty=600, status=status),
                            dict(sku="B", qty=600, status=status)])
        save_json(self.path, value)

    def test_only_arrived_checked_and_independent_receipts(self):
        self.batch()
        rows = detect(self.root, self.client, self.now)
        batch = json.loads(self.path.read_text())
        self.assertEqual([x["status"] for x in batch["items"]], ["received", "arrived_packhai"])
        self.assertEqual(batch["status"], "arrived_packhai")
        self.assertEqual([x["sku"] for x in rows], ["B"])

    def test_exact_tolerance_boundary(self):
        self.batch()
        self.client.fetch_inventory.return_value["A"] = 590
        detect(self.root, self.client, self.now)
        self.assertEqual(json.loads(self.path.read_text())["items"][0]["status"], "received")

    def test_all_received_omitted_from_root(self):
        for stock in (591, 596, 600):
            with self.subTest(stock=stock):
                self.batch()
                self.client.fetch_inventory.return_value.update(A=stock, B=stock)
                self.assertEqual(detect(self.root, self.client, self.now), [])
                self.assertEqual(json.loads(self.path.read_text())["status"], "received")

    def test_locked_baseline_change_fails_without_receipt_write(self):
        self.batch()
        self.client.fetch_inventory.return_value.update(A=589, B=589)
        detect(self.root, self.client, self.now)
        before = self.path.read_bytes()
        previous = self.root / "stock_snapshots/2026-11-21.json"
        value = json.loads(previous.read_text())
        value["stocks"]["A"] = -100
        save_json(previous, value)
        with self.assertRaisesRegex(ValueError, "locked baseline"):
            detect(self.root, self.client, self.now)
        self.assertEqual(self.path.read_bytes(), before)

    def test_other_statuses_never_fetch_shipments_or_change_items(self):
        for status in ("in_transit", "received"):
            self.batch(status)
            before = self.path.read_text()
            detect(self.root, self.client, self.now)
            self.assertEqual(self.path.read_text(), before)
        self.client.fetch_shipment_lines.assert_not_called()

    def test_outbound_added_deduplicated_and_timestamp_filtered(self):
        self.batch()
        self.client.fetch_inventory.return_value["A"] = 580
        line = dict(source_key="one", sku="A", quantity=10,
                    shipped_at="2026-11-22 10:00:00")
        old = dict(source_key="old", sku="A", quantity=999,
                   shipped_at="2026-11-21 12:00:00")
        self.client.fetch_shipment_lines.return_value = [line, line, old]
        detect(self.root, self.client, self.now)
        item = json.loads(self.path.read_text())["items"][0]
        self.assertEqual(item["receipt_check"]["inferred_qty"], 590)
        self.assertEqual(item["receipt_check"]["outbound_qty"], 10)

    def test_missing_baseline_waits_and_locks_arrival_date(self):
        self.batch()
        (self.root / "stock_snapshots/2026-11-21.json").unlink()
        rows = detect(self.root, self.client, self.now)
        self.assertEqual(len(rows), 2)
        self.client.fetch_shipment_lines.assert_not_called()
        self.assertEqual(json.loads(self.path.read_text())["items"][0]["receipt_check"]["arrived_date"],
                         "2026-11-22")

    def test_daily_snapshot_is_immutable_and_history_retained(self):
        self.batch("in_transit")
        detect(self.root, self.client, self.now)
        path = self.root / "stock_snapshots/2026-11-22.json"
        first = path.read_bytes()
        self.client.fetch_inventory.return_value["A"] = 100
        detect(self.root, self.client, self.now)
        self.assertEqual(path.read_bytes(), first)
        self.assertTrue((self.root / "stock_snapshots/2026-11-21.json").exists())

    def test_api_failure_preserves_batches_and_summary(self):
        self.batch()
        build(self.root)
        before = self.path.read_bytes()
        summary = (self.root / "in_transit.json").read_bytes()
        self.client.fetch_shipment_lines.side_effect = RuntimeError("Packhai unavailable")
        with self.assertRaises(RuntimeError):
            detect(self.root, self.client, self.now)
        self.assertEqual(before, self.path.read_bytes())
        self.assertEqual(summary, (self.root / "in_transit.json").read_bytes())

    def test_multiple_arrived_batches_same_sku_are_not_double_counted(self):
        self.batch()
        other = json.loads(self.path.read_text())
        other["batch_id"] = "second"
        save_json(self.path.with_name("second.json"), other)
        self.assertEqual(len(detect(self.root, self.client, self.now)), 4)
        self.client.fetch_shipment_lines.assert_not_called()

    def test_received_item_not_checked_again(self):
        self.batch()
        detect(self.root, self.client, self.now)
        self.client.fetch_shipment_lines.reset_mock()
        detect(self.root, self.client, self.now)
        batch = json.loads(self.path.read_text())
        self.assertEqual(batch["items"][0]["status"], "received")
        self.assertEqual(batch["items"][1]["status"], "arrived_packhai")
