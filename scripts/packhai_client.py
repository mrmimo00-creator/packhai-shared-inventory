from __future__ import annotations

import hashlib
from datetime import date, datetime, time
from typing import Any, Iterable

import requests


ORDER_API = "https://order.packhai-api-88.com/Order"
STOCK_API = "https://stock.packhai-api-88.com/Stock/get-stockshop-list-by-warehouse"
STOCK_PAGE_SIZE = 300
WAREHOUSE_IDS = [
    1,
    7997,
    401,
    7917,
    8242,
    8799,
    8800,
    8801,
    8802,
    8803,
    8804,
    8805,
    8806,
    8807,
    8808,
    8809,
    8810,
    8812,
    8824,
]


class PackhaiClient:
    def __init__(self, token: str, shop_id: int, timeout: int = 60):
        token = token.strip()
        self.authorization = token if token.lower().startswith("bearer ") else f"Bearer {token}"
        self.shop_id = shop_id
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update(
            {
                "accept": "application/json, text/plain, */*",
                "content-type": "application/json",
                "authorization": self.authorization,
            }
        )

    def _post(self, path: str, body: dict[str, Any]) -> Any:
        response = self.session.post(
            f"{ORDER_API}/{path}",
            json=body,
            timeout=self.timeout,
        )
        response.raise_for_status()
        return response.json()

    def _post_stock(self, body: dict[str, Any]) -> Any:
        response = self.session.post(
            STOCK_API,
            json=body,
            timeout=self.timeout,
        )
        response.raise_for_status()
        return response.json()

    def fetch_inventory(self) -> dict[str, int]:
        inventory: dict[str, int] = {}
        skip = 0
        total: int | None = None

        while total is None or skip < total:
            payload = self._post_stock(
                {
                    "shopId": self.shop_id,
                    "keyword": {
                        "name": None,
                        "productCode": None,
                        "barcode": None,
                        "sku": None,
                        "showProduct": None,
                        "isActive": True,
                        "isProductSet": None,
                        "isNeedResultCount": True,
                        "mfg_date": None,
                        "expire_date": None,
                        "orderbyName": "id",
                        "isAscending": False,
                    },
                    "isFromShop": True,
                    "skip": skip,
                    "take": STOCK_PAGE_SIZE,
                    "isNeedResultCount": True,
                    "warehouseIDList": WAREHOUSE_IDS,
                }
            )
            if "resultData" not in payload:
                raise RuntimeError(f"Packhai 返回格式异常：{payload}")

            page = payload.get("resultData", [])
            total = int(payload.get("resultCount", len(page)))
            for item in page:
                if not item.get("isPackhaiWarehouse"):
                    continue
                sku = str(item.get("sku") or "").strip()
                if sku:
                    inventory[sku] = int(item.get("quantityAvailable") or 0)

            if not page:
                break
            skip += len(page)

        if not inventory:
            raise RuntimeError("Packhai 没有返回任何有效的主仓 SKU")
        return inventory

    def fetch_orders_for_day(
        self, shipped_day: date, *, diagnostics: bool = False
    ) -> list[dict[str, Any]]:
        start = datetime.combine(shipped_day, time.min).strftime("%Y-%m-%d %H:%M:%S")
        end = datetime.combine(shipped_day, time.max).strftime("%Y-%m-%d %H:%M:%S")
        skip = 0
        take = 200
        orders: list[dict[str, Any]] = []

        while True:
            payload = self._post(
                "shop-get-order-list",
                {
                    "orderIDText": None,
                    "shopID": self.shop_id,
                    "referenceNumberText": None,
                    "dateFrom": None,
                    "dateTo": None,
                    "sendToExpressDatetime_From": start,
                    "sendToExpressDatetime_To": end,
                    "packDateFrom": None,
                    "packDateTo": None,
                    "receiverPhone": None,
                    "receiverName": None,
                    "trackingNo": None,
                    "shippingStatusID": None,
                    "paidStatusID": 0,
                    "parcelStatusID": None,
                    "createdByStaffID": None,
                    "isNeedStatusCount": True,
                    "channelIDList": [],
                    "skip": skip,
                    "take": take,
                    "orderby": 2,
                },
            )
            page = _pick_list(payload, "orderList", "items", "data")
            orders.extend(page)
            total = int(
                _pick(
                    payload,
                    "resultCount",
                    "totalRows",
                    "searchResultCount",
                    default=len(orders),
                )
                or len(orders)
            )
            has_more = bool(page) and len(orders) < total and len(page) == take
            if diagnostics:
                print(
                    f"[Packhai] date={shipped_day.isoformat()} skip={skip} take={take} "
                    f"returned={len(page)} total={total} has_more={str(has_more).lower()}"
                )
            if not has_more:
                break
            skip += len(page)
        return orders

    def fetch_order_items(self, order_id: int | str) -> list[dict[str, Any]]:
        payload = self._post("get-order-item", {"orderID": order_id})
        return _pick_list(payload, "orderItemList", "OrderItemList", "items", "data")

    def fetch_shipment_lines(
        self, days: Iterable[date], *, diagnostics: bool = False
    ) -> list[dict[str, Any]]:
        lines: list[dict[str, Any]] = []
        for shipped_day in days:
            for order in self.fetch_orders_for_day(shipped_day, diagnostics=diagnostics):
                order_id = _pick(order, "id", "orderID", "orderMasterID", "orderMasterId")
                if order_id is None:
                    continue
                shipped_at = str(
                    _pick(
                        order,
                        "sendToExpressDatetime",
                        "sentToExpressDatetime",
                        "shippingDatetime",
                        default=f"{shipped_day.isoformat()} 00:00:00",
                    )
                )
                order_text = str(
                    _pick(order, "orderIDText", "orderMasterIDText", "referenceNumber", default=order_id)
                )
                for item in self.fetch_order_items(order_id):
                    sku = str(_pick(item, "sku", "Sku", "SKU", default="")).strip()
                    quantity = int(_pick(item, "quantity", "Quantity", default=0) or 0)
                    if not sku or quantity <= 0:
                        continue
                    product_name = str(
                        _pick(item, "productName", "ProductName", "name", "Name", default="")
                    ).strip()
                    line_id = str(
                        _pick(
                            item,
                            "id",
                            "orderItemID",
                            "orderItemId",
                            "stockShopId",
                            "StockShopId",
                            default="",
                        )
                    )
                    source_key = hashlib.sha256(
                        f"{order_id}|{line_id}|{order_text}|{sku}|{quantity}|{shipped_at}".encode("utf-8")
                    ).hexdigest()
                    lines.append(
                        {
                            "source_key": source_key,
                            "order_id": str(order_id),
                            "order_number": order_text,
                            "shipped_at": shipped_at,
                            "sku": sku,
                            "product_name": product_name,
                            "quantity": quantity,
                        }
                    )
        return lines


def _pick(data: Any, *keys: str, default: Any = None) -> Any:
    if not isinstance(data, dict):
        return default
    for key in keys:
        if key in data and data[key] is not None:
            return data[key]
    return default


def _pick_list(data: Any, *keys: str) -> list[dict[str, Any]]:
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if not isinstance(data, dict):
        return []
    for key in keys:
        value = data.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
        if isinstance(value, dict):
            nested = _pick_list(value, *keys)
            if nested:
                return nested
    return []
