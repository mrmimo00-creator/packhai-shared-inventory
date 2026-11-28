from __future__ import annotations

import hashlib
import json
from time import monotonic
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


MAX_READ_PAGES = 100
READ_TIMEOUT_SECONDS = 300


def _reject_api_error(payload):
    if not isinstance(payload, dict):
        return
    if any(payload.get(key) in (False, "false", "False", "0") for key in ("success", "isSuccess", "state")) or payload.get("isError") in (True, "true", "True", "1"):
        raise RuntimeError("Packhai 返回业务错误，本轮未完成")
    try:
        code = int(payload.get("code", 0))
    except (TypeError, ValueError):
        code = 0
    if 400 <= code < 600 or code < 0:
        raise RuntimeError("Packhai 返回业务错误，本轮未完成")


def _reported_count(payload, *keys):
    if not isinstance(payload, dict):
        return None
    for key in keys:
        value = payload.get(key)
        if value is not None:
            try:
                result = int(value)
            except (TypeError, ValueError, OverflowError):
                raise RuntimeError("Packhai 分页总数无效，本轮未完成") from None
            if result < 0:
                raise RuntimeError("Packhai 分页总数无效，本轮未完成")
            return result
    return None


def _stock_quantity(item):
    value = item.get("quantityAvailable")
    if value is None or value == "" or isinstance(value, bool):
        raise RuntimeError("Packhai 主仓SKU缺少有效库存数量，本轮未完成")
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        raise RuntimeError("Packhai 主仓SKU库存数量无效，本轮未完成") from None


def _check_page(page, seen, skip, total):
    if not isinstance(page, list) or any(not isinstance(item, dict) for item in page):
        raise RuntimeError("Packhai 分页结构异常，本轮未完成")
    if not page:
        if total is not None and skip < total:
            raise RuntimeError("Packhai 分页未取完整，本轮未完成")
        return
    fingerprint = json.dumps(page, sort_keys=True, ensure_ascii=False, default=str)
    if fingerprint in seen:
        raise RuntimeError("Packhai 重复返回同一页，本轮未完成")
    seen.add(fingerprint)


def _check_read_budget(pages, deadline):
    if pages >= MAX_READ_PAGES or monotonic() >= deadline:
        raise RuntimeError("Packhai 分页读取超出本轮上限，本轮未完成")


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

        pages = 0
        seen = set()
        deadline = monotonic() + READ_TIMEOUT_SECONDS
        while True:
            _check_read_budget(pages, deadline)
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
            _reject_api_error(payload)
            if not isinstance(payload, dict) or "resultData" not in payload:
                raise RuntimeError("Packhai 库存响应结构异常，本轮未完成")
            page = payload["resultData"]
            reported = _reported_count(payload, "resultCount")
            if reported is not None:
                total = max(total or 0, reported)
            _check_page(page, seen, skip, total)
            for item in page:
                if not item.get("isPackhaiWarehouse"):
                    continue
                sku = str(item.get("sku") or "").strip()
                if sku:
                    inventory[sku] = _stock_quantity(item)
            skip += len(page)
            pages += 1
            if (total is not None and skip >= total) or (total is None and len(page) < STOCK_PAGE_SIZE):
                break

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
        total = None
        pages = 0
        seen = set()
        deadline = monotonic() + READ_TIMEOUT_SECONDS

        while True:
            _check_read_budget(pages, deadline)
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
            reported = _reported_count(payload, "resultCount", "totalRows", "searchResultCount")
            if reported is not None:
                total = max(total or 0, reported)
            _check_page(page, seen, skip, total)
            orders.extend(page)
            pages += 1
            has_more = bool(page) and (len(orders) < total if total is not None else len(page) >= take)
            if diagnostics:
                print(
                    f"[Packhai] date={shipped_day.isoformat()} skip={skip} take={take} "
                    f"returned={len(page)} total={total if total is not None else len(orders)} has_more={str(has_more).lower()}"
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
        deadline = monotonic() + 20 * 60
        for shipped_day in days:
            if monotonic() >= deadline:
                raise RuntimeError("Packhai 出库读取超出本轮上限，本轮未完成")
            for order in self.fetch_orders_for_day(shipped_day, diagnostics=diagnostics):
                if monotonic() >= deadline:
                    raise RuntimeError("Packhai 出库读取超出本轮上限，本轮未完成")
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
    def find(value):
        _reject_api_error(value)
        if isinstance(value, list):
            if any(not isinstance(item, dict) for item in value):
                raise RuntimeError("Packhai 明细结构异常，本轮未完成")
            return value
        if not isinstance(value, dict):
            return None
        for key in keys:
            candidate = value.get(key)
            if isinstance(candidate, (list, dict)):
                found = find(candidate)
                if found is not None:
                    return found
            if key in value and candidate is None and _reported_count(value, "resultCount", "totalRows", "searchResultCount") == 0:
                return []
        return None

    result = find(data)
    if result is None:
        raise RuntimeError("Packhai 未返回已支持的订单/明细结构，本轮未完成")
    return result
