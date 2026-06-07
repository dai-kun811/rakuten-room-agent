from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any, Iterable

LOGGER = logging.getLogger(__name__)

RAKUTEN_ITEM_SEARCH_URL = (
    "https://app.rakuten.co.jp/services/api/IchibaItem/Search/20170706"
)
DEFAULT_GENRE_ID = "100533"


@dataclass(frozen=True)
class Product:
    category: str
    name: str
    url: str
    price: int
    review_count: int
    review_average: float
    caption: str
    catchcopy: str
    shop_name: str
    image_url: str

    @property
    def text(self) -> str:
        return " ".join(
            [self.category, self.name, self.caption, self.catchcopy, self.shop_name]
        ).lower()


CATEGORY_KEYWORDS = {
    "育児便利グッズ": ["育児 便利 グッズ", "育児 時短"],
    "ベビー用品": ["ベビー用品", "赤ちゃん グッズ"],
    "キッズ用品": ["キッズ用品", "子ども グッズ"],
    "知育玩具": ["知育玩具", "知育 おもちゃ"],
    "おうち遊び": ["おうち遊び 子ども", "室内遊び 子ども"],
    "外遊び": ["外遊び 子ども", "キッズ 外遊び"],
    "育児時短グッズ": ["育児 時短 グッズ", "子育て 時短"],
    "子ども靴": ["子ども 靴", "キッズ シューズ"],
    "絵本": ["絵本 子ども", "知育 絵本"],
    "育児家電": ["育児 家電", "ベビー 家電"],
    "プレゼント向け商品": ["子ども プレゼント", "ベビー ギフト"],
}


class RakutenApiClient:
    def __init__(
        self,
        application_id: str,
        *,
        genre_id: str = DEFAULT_GENRE_ID,
        session: Any | None = None,
    ) -> None:
        if session is None:
            import requests

            session = requests.Session()
        self.application_id = application_id
        self.genre_id = genre_id
        self.session = session

    def fetch_products(self, *, pages_per_keyword: int = 2) -> list[Product]:
        import requests

        products_by_url: dict[str, Product] = {}
        for category, keywords in CATEGORY_KEYWORDS.items():
            for keyword in keywords:
                for page in range(1, pages_per_keyword + 1):
                    try:
                        for product in self._search(category, keyword, page):
                            products_by_url.setdefault(product.url, product)
                    except requests.HTTPError as exc:
                        LOGGER.warning(
                            "楽天API取得に失敗しました category=%s keyword=%s page=%s status=%s body=%s",
                            category,
                            keyword,
                            page,
                            exc.response.status_code if exc.response else "unknown",
                            exc.response.text[:300] if exc.response else "",
                        )
                    except requests.RequestException as exc:
                        LOGGER.warning(
                            "楽天APIへの接続に失敗しました category=%s keyword=%s page=%s error=%s",
                            category,
                            keyword,
                            page,
                            exc,
                        )
                    time.sleep(0.2)
        return list(products_by_url.values())

    def _search(self, category: str, keyword: str, page: int) -> Iterable[Product]:
        params = {
            "applicationId": self.application_id,
            "format": "json",
            "keyword": keyword,
            "genreId": self.genre_id,
            "hits": 30,
            "page": page,
            "sort": "-reviewCount",
            "availability": 1,
            "imageFlag": 1,
        }
        response = self.session.get(RAKUTEN_ITEM_SEARCH_URL, params=params, timeout=30)
        if response.status_code == 404:
            LOGGER.info("楽天APIに該当商品がありません category=%s keyword=%s", category, keyword)
            return []
        response.raise_for_status()
        payload = response.json()
        return [self._to_product(category, item["Item"]) for item in payload.get("Items", [])]

    def _to_product(self, category: str, item: dict) -> Product:
        image_url = ""
        image_urls = item.get("mediumImageUrls") or []
        if image_urls:
            image_url = str(image_urls[0].get("imageUrl", ""))

        return Product(
            category=category,
            name=str(item.get("itemName", "")),
            url=str(item.get("itemUrl", "")),
            price=int(item.get("itemPrice") or 0),
            review_count=int(item.get("reviewCount") or 0),
            review_average=float(item.get("reviewAverage") or 0),
            caption=str(item.get("itemCaption", "")),
            catchcopy=str(item.get("catchcopy", "")),
            shop_name=str(item.get("shopName", "")),
            image_url=image_url,
        )
