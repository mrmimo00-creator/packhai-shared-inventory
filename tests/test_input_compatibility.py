import unittest
from datetime import date
from unittest.mock import patch
from scripts.packhai_client import PackhaiClient


class InputCompatibilityTests(unittest.TestCase):
    def setUp(self):self.client=PackhaiClient('test-only',314)

    def test_stock_zero_optional_fields_and_non_main_warehouse(self):
        self.client._post_stock=lambda _:dict(resultData=[dict(sku='A',isPackhaiWarehouse=True,quantityAvailable=0),dict(sku='external',isPackhaiWarehouse=False)],resultCount=2)
        self.assertEqual(self.client.fetch_inventory(),{'A':0})

    def test_missing_stock_and_partial_pages_fail(self):
        self.client._post_stock=lambda _:dict(resultData=[dict(sku='A',isPackhaiWarehouse=True)],resultCount=1)
        with self.assertRaises(RuntimeError):self.client.fetch_inventory()
        with patch.object(self.client,'_post_stock',side_effect=[dict(resultData=[dict(sku='A',isPackhaiWarehouse=True,quantityAvailable=2)],resultCount=2),dict(resultData=[],resultCount=2)]):
            with self.assertRaises(RuntimeError):self.client.fetch_inventory()

    def test_legal_empty_order_shapes(self):
        for payload in [[],dict(orderList=[],searchResultCount=0),dict(data=dict(items=[])),dict(orderList=None,searchResultCount=0)]:
            with self.subTest(payload=payload):
                self.client._post=lambda *a:payload
                self.assertEqual(self.client.fetch_orders_for_day(date(2026,11,28)),[])

    def test_api_error_cannot_be_empty_orders_or_items(self):
        for payload in [dict(code=401,message='not authorized'),dict(success=False,orderList=[]),dict(success=0,orderList=[]),dict(isError=True,orderList=[]),dict(isSuccess=False,data=[]),dict(message='unexpected schema')]:
            self.client._post=lambda *a:payload
            with self.subTest(payload=payload):
                with self.assertRaises(RuntimeError):self.client.fetch_orders_for_day(date(2026,11,28))
                with self.assertRaises(RuntimeError):self.client.fetch_order_items(1)

    def test_short_page_continues_to_reported_total(self):
        requests=[]
        def post(path,body):
            requests.append(body['skip']);return dict(orderList=[dict(id=body['skip']+1)],searchResultCount=3)
        self.client._post=post
        self.assertEqual(len(self.client.fetch_orders_for_day(date(2026,11,28))),3)
        self.assertEqual(requests,[0,1,2])

    def test_empty_page_before_total_and_repeated_page_fail(self):
        with patch.object(self.client,'_post',side_effect=[dict(orderList=[dict(id=1)],searchResultCount=2),dict(orderList=[],searchResultCount=2)]):
            with self.assertRaises(RuntimeError):self.client.fetch_orders_for_day(date(2026,11,28))
        self.client._post=lambda *a:dict(orderList=[dict(id=1)],searchResultCount=2)
        with self.assertRaises(RuntimeError):self.client.fetch_orders_for_day(date(2026,11,28))

    def test_pagination_read_budget_stops_without_extra_requests(self):
        import scripts.packhai_client as client_module
        self.client._post=unittest.mock.Mock(return_value=dict(orderList=[dict(id=1)],searchResultCount=2))
        with patch.object(client_module,'MAX_READ_PAGES',1):
            with self.assertRaises(RuntimeError):self.client.fetch_orders_for_day(date(2026,11,28))
        self.assertEqual(self.client._post.call_count,1)

    def test_existing_nested_detail_shapes_and_optional_name(self):
        for payload in [[],dict(orderItemList=[]),dict(data=dict(OrderItemList=[dict(sku='A',quantity=1)]))]:
            self.client._post=lambda *a:payload
            self.assertIsInstance(self.client.fetch_order_items(1),list)
        self.client.fetch_orders_for_day=lambda *a,**k:[dict(id=1,sendToExpressDatetime='2026-11-28 10:00:00')]
        self.client.fetch_order_items=lambda _: [dict(id=2,sku='A',quantity=1)]
        self.assertEqual(self.client.fetch_shipment_lines([date(2026,11,28)])[0]['product_name'],'')


if __name__=='__main__':unittest.main()
