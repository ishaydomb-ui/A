"""Tests for the public price/promotion feed and the catalog built from it.

The XML fixtures below are trimmed copies of real Shufersal feed files
(branch 009, 2026-08-28), so the field names and quirks — BOM, blank
ItemStatus, promotions priced at the shelf price — are the real ones
rather than invented.
"""
from __future__ import annotations

import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from grocery_bot.prices import (
    _parse_feed_name,
    latest_file,
    parse_prices,
    parse_promotions,
)
from grocery_bot.storage import Storage

PRICES_XML = """<?xml version="1.0" encoding="utf-8"?>
<Root>
  <ChainID>7290027600007</ChainID>
  <StoreID>009</StoreID>
  <Items>
    <Item>
      <ItemCode>111</ItemCode>
      <ItemName>חלב 3% בקרטון 1 ליטר</ItemName>
      <ManufactureName>תנובה</ManufactureName>
      <UnitQty>ליטר</UnitQty>
      <Quantity>1.00</Quantity>
      <UnitOfMeasure>ליטר</UnitOfMeasure>
      <bIsWeighted>0</bIsWeighted>
      <ItemPrice>7.35</ItemPrice>
      <UnitOfMeasurePrice>7.35</UnitOfMeasurePrice>
      <ItemStatus />
    </Item>
    <Item>
      <ItemCode>222</ItemCode>
      <ItemName>שוקולד חלב 100 גרם</ItemName>
      <ManufactureName>עלית</ManufactureName>
      <bIsWeighted>0</bIsWeighted>
      <ItemPrice>8.90</ItemPrice>
      <UnitOfMeasurePrice>8.90</UnitOfMeasurePrice>
    </Item>
    <Item>
      <ItemCode>333</ItemCode>
      <ItemName>פריט שהוסר מהמדף</ItemName>
      <ItemPrice>0.00</ItemPrice>
    </Item>
  </Items>
</Root>
"""

PROMOS_XML = """<?xml version="1.0" encoding="utf-8"?>
<Root>
  <Promotions>
    <Promotion>
      <PromotionID>900</PromotionID>
      <PromotionDescription>תו זהב 5% הנחה</PromotionDescription>
      <PromotionStartDateTime>2026-08-01T00:00:00.000</PromotionStartDateTime>
      <PromotionEndDateTime>2026-09-30T23:59:00.000</PromotionEndDateTime>
      <Groups><Group><PromotionItems>
        <PromotionItem>
          <ItemCode>111</ItemCode>
          <MinQty>1.00</MinQty>
          <DiscountRate>5</DiscountRate>
          <DiscountedPrice>6.98</DiscountedPrice>
        </PromotionItem>
      </PromotionItems></Group></Groups>
    </Promotion>
    <Promotion>
      <PromotionID>901</PromotionID>
      <PromotionDescription>ע. סיבוס קופון 50 ש"ח מתנה</PromotionDescription>
      <PromotionStartDateTime>2026-08-01T00:00:00.000</PromotionStartDateTime>
      <PromotionEndDateTime>2026-09-30T23:59:00.000</PromotionEndDateTime>
      <Groups><Group><PromotionItems>
        <PromotionItem>
          <ItemCode>111</ItemCode>
          <MinQty>1.00</MinQty>
          <DiscountRate>0</DiscountRate>
          <DiscountedPrice>7.35</DiscountedPrice>
        </PromotionItem>
      </PromotionItems></Group></Groups>
    </Promotion>
    <Promotion>
      <PromotionID>902</PromotionID>
      <PromotionDescription>מבצע שהסתיים מזמן</PromotionDescription>
      <PromotionStartDateTime>2014-01-01T00:00:00.000</PromotionStartDateTime>
      <PromotionEndDateTime>2015-01-01T00:00:00.000</PromotionEndDateTime>
      <Groups><Group><PromotionItems>
        <PromotionItem>
          <ItemCode>111</ItemCode>
          <MinQty>1.00</MinQty>
          <DiscountRate>90</DiscountRate>
          <DiscountedPrice>0.99</DiscountedPrice>
        </PromotionItem>
      </PromotionItems></Group></Groups>
    </Promotion>
  </Promotions>
</Root>
"""

NOW = datetime(2026, 8, 29, 12, 0, 0)


class ParseFeedNameTests(unittest.TestCase):
    def test_parses_type_store_and_timestamp(self):
        feed_file = _parse_feed_name(
            "PriceFull7290027600007-001-009-20260828-030000.gz", "https://example/x"
        )
        assert feed_file is not None
        self.assertEqual(feed_file.file_type, "PriceFull")
        self.assertEqual(feed_file.store_id, "009")
        self.assertEqual(feed_file.published_at, datetime(2026, 8, 28, 3, 0, 0))

    def test_rejects_unrelated_filenames(self):
        self.assertIsNone(_parse_feed_name("index.html", "https://example/x"))

    def test_latest_file_picks_newest_for_that_branch_and_type(self):
        files = [
            _parse_feed_name(name, "u")
            for name in (
                "PriceFull7290027600007-001-009-20260827-030000.gz",
                "PriceFull7290027600007-001-009-20260828-030000.gz",
                "PriceFull7290027600007-001-001-20260829-030000.gz",  # other branch
                "PromoFull7290027600007-001-009-20260829-030000.gz",  # other type
            )
        ]
        newest = latest_file(files, "PriceFull", "9")
        assert newest is not None
        self.assertEqual(newest.published_at, datetime(2026, 8, 28, 3, 0, 0))

    def test_store_id_is_zero_padded_for_lookup(self):
        files = [_parse_feed_name("PriceFull7290027600007-001-009-20260828-030000.gz", "u")]
        self.assertIsNotNone(latest_file(files, "PriceFull", "9"))


class ParsingTests(unittest.TestCase):
    def test_parses_products_and_drops_zero_priced_rows(self):
        products = parse_prices(PRICES_XML)
        self.assertEqual([p.item_code for p in products], ["111", "222"])
        self.assertEqual(products[0].name, "חלב 3% בקרטון 1 ליטר")
        self.assertEqual(products[0].price, 7.35)
        self.assertEqual(products[0].manufacturer, "תנובה")

    def test_flattens_promotions_to_one_row_per_item(self):
        rows = parse_promotions(PROMOS_XML)
        self.assertEqual(len(rows), 3)
        self.assertEqual({r.promotion_id for r in rows}, {"900", "901", "902"})
        gold = next(r for r in rows if r.promotion_id == "900")
        self.assertEqual(gold.discounted_price, 6.98)
        self.assertEqual(gold.item_code, "111")


class CatalogTests(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.storage = Storage(str(Path(self._dir.name) / "test.sqlite3"))
        self.storage.replace_catalog(
            parse_prices(PRICES_XML), parse_promotions(PROMOS_XML), {"branch": "9"}
        )

    def tearDown(self):
        self._dir.cleanup()

    def test_search_ranks_real_milk_above_milk_chocolate(self):
        results = self.storage.search_products("חלב")
        self.assertEqual(results[0].item_code, "111", "actual milk should outrank שוקולד חלב")

    def test_search_is_empty_for_blank_query(self):
        self.assertEqual(self.storage.search_products("   "), [])

    def test_best_deal_ignores_blanket_promotions_priced_at_shelf_price(self):
        milk = self.storage.search_products("חלב")[0]
        deal = self.storage.best_deal_for(milk, now=NOW)
        assert deal is not None
        self.assertEqual(
            deal.promotion_id, "900", "the סיבוס row is not a discount and must not win"
        )

    def test_best_deal_ignores_expired_promotions(self):
        milk = self.storage.search_products("חלב")[0]
        deal = self.storage.best_deal_for(milk, now=NOW)
        assert deal is not None
        self.assertNotEqual(deal.promotion_id, "902", "expired promo must not be offered")

    def test_item_with_no_real_deal_reports_none(self):
        chocolate = next(p for p in self.storage.search_products("שוקולד") if p.item_code == "222")
        self.assertIsNone(self.storage.best_deal_for(chocolate, now=NOW))

    def test_refresh_replaces_rather_than_merges(self):
        self.storage.replace_catalog(parse_prices(PRICES_XML)[:1], [], {"branch": "9"})
        self.assertEqual(self.storage.catalog_meta()["product_count"], "1")
        self.assertEqual(self.storage.search_products("שוקולד"), [])


if __name__ == "__main__":
    unittest.main()
