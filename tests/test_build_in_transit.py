import json
import tempfile
import unittest
from pathlib import Path
from scripts.build_in_transit import build, ROOT


class BuildTests(unittest.TestCase):
    def batch(self, status="in_transit", batch_id="batch-a"):
        return dict(batch_id=batch_id, shipment_date="2026-11-07", status=status,
                    total_qty=10, items=[dict(sku="Espresso", qty=10)])

    def run_build(self, batches):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "in_transit_batches").mkdir()
            output = root / "in_transit.json"
            output.write_text("[]\n")
            for index, batch in enumerate(batches):
                (root / "in_transit_batches" / f"{index}.json").write_text(json.dumps(batch))
            try:
                return build(root)
            except ValueError:
                self.assertEqual(output.read_text(), "[]\n")
                raise

    def test_statuses(self):
        for status in ("in_transit", "arrived_packhai", "received"):
            rows = self.run_build([self.batch(status)])
            self.assertEqual(len(rows), 0 if status == "received" else 1)

    def test_same_sku_preserves_batches_and_dates(self):
        other = self.batch(batch_id="batch-b")
        other["shipment_date"] = "2026-11-08"
        rows = self.run_build([self.batch(), other])
        self.assertEqual([r["date"] for r in rows], ["2026-11-07", "2026-11-08"])
        self.assertEqual(len({r["batch_id"] for r in rows}), 2)

    def test_invalid_batches_preserve_output(self):
        for change in (dict(total_qty=11), dict(status="unknown"),
                       dict(items=[dict(sku="Espresso", qty=0)]),
                       dict(items=[dict(sku="Espresso", qty=10, status="received")])):
            batch = self.batch()
            batch.update(change)
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.run_build([batch])

    def test_duplicate_batch_id(self):
        with self.assertRaises(ValueError):
            self.run_build([self.batch(), self.batch("received")])

    def test_invalid_json_preserves_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "in_transit_batches").mkdir()
            (root / "in_transit_batches" / "bad.json").write_text("{")
            (root / "in_transit.json").write_text("unchanged")
            with self.assertRaises(ValueError):
                build(root)
            self.assertEqual((root / "in_transit.json").read_text(), "unchanged")

    def test_november_seventh_batch(self):
        batch = json.loads((ROOT / "in_transit_batches/2026-11-07_8000049456954.json").read_text())
        rows = self.run_build([batch])
        self.assertEqual(sum(r["qty"] for r in rows), 3912)
        self.assertTrue(all(r["status"] == "in_transit" for r in rows))
