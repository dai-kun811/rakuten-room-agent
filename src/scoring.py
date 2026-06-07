from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date

from rakuten_api import Product

TARGET_WORDS = [
    "育児",
    "ベビー",
    "キッズ",
    "知育",
    "子ども",
    "子供",
    "幼児",
    "おもちゃ",
    "玩具",
    "絵本",
    "靴",
    "シューズ",
    "時短",
    "プレゼント",
    "ギフト",
    "保育園",
    "通園",
    "入園",
    "赤ちゃん",
]

ROOM_WORDS = [
    "ギフト",
    "プレゼント",
    "人気",
    "ランキング",
    "送料無料",
    "セット",
    "かわいい",
    "おしゃれ",
    "限定",
    "高評価",
]

SEASONAL_WORDS_BY_MONTH = {
    1: ["冬", "防寒", "保温", "乾燥", "加湿"],
    2: ["入園", "入学", "準備", "花粉", "防寒"],
    3: ["入園", "入学", "通園", "新生活", "名前"],
    4: ["入園", "通園", "遠足", "春", "水筒"],
    5: ["外遊び", "紫外線", "uv", "帽子", "水筒"],
    6: ["梅雨", "雨", "防水", "レイン", "通気"],
    7: ["夏", "冷感", "水遊び", "プール", "虫よけ"],
    8: ["夏", "冷感", "水遊び", "帰省", "旅行"],
    9: ["防災", "遠足", "運動会", "秋", "通園"],
    10: ["運動会", "秋", "遠足", "ハロウィン", "保温"],
    11: ["冬", "防寒", "保温", "乾燥", "クリスマス"],
    12: ["クリスマス", "冬", "防寒", "ギフト", "帰省"],
}


@dataclass(frozen=True)
class ScoredProduct:
    product: Product
    selection_tier: str
    sales_score: int
    rating_score: int
    target_score: int
    seasonal_score: int
    room_score: int
    total_score: int
    product_rank: str
    recommendation_reason: str


def filter_and_score_products(products: list[Product], today: date) -> list[ScoredProduct]:
    return select_products(products, today, build_selection_tiers_from_env())


@dataclass(frozen=True)
class SelectionTier:
    name: str
    min_review_count: int
    min_review_average: float
    min_total_score: int


def build_selection_tiers_from_env() -> list[SelectionTier]:
    strict = SelectionTier(
        name="strict",
        min_review_count=int(os.getenv("STRICT_MIN_REVIEW_COUNT", "100")),
        min_review_average=float(os.getenv("STRICT_MIN_REVIEW_AVERAGE", "4.3")),
        min_total_score=int(os.getenv("STRICT_MIN_TOTAL_SCORE", "70")),
    )

    if os.getenv("ENABLE_RELAXED_FALLBACK", "true").lower() not in {"1", "true", "yes"}:
        return [strict]

    return [
        SelectionTier(
            name="strict_priority",
            min_review_count=strict.min_review_count,
            min_review_average=strict.min_review_average,
            min_total_score=int(os.getenv("STRICT_PRIORITY_TOTAL_SCORE", "80")),
        ),
        strict,
        SelectionTier(
            name="relaxed",
            min_review_count=int(os.getenv("RELAXED_MIN_REVIEW_COUNT", "30")),
            min_review_average=float(os.getenv("RELAXED_MIN_REVIEW_AVERAGE", "4.0")),
            min_total_score=int(os.getenv("RELAXED_MIN_TOTAL_SCORE", "50")),
        ),
        SelectionTier(
            name="debug_minimum",
            min_review_count=int(os.getenv("DEBUG_MIN_REVIEW_COUNT", "0")),
            min_review_average=float(os.getenv("DEBUG_MIN_REVIEW_AVERAGE", "0")),
            min_total_score=int(os.getenv("DEBUG_MIN_TOTAL_SCORE", "0")),
        ),
    ]


def select_products(
    products: list[Product], today: date, tiers: list[SelectionTier], limit: int = 5
) -> list[ScoredProduct]:
    selected: list[ScoredProduct] = []
    selected_urls: set[str] = set()
    for tier in tiers:
        scored = [
            score_product(product, today, selection_tier=tier.name)
            for product in products
            if product.url
            and product.url not in selected_urls
            and product.review_count >= tier.min_review_count
            and product.review_average >= tier.min_review_average
        ]
        scored = [item for item in scored if item.total_score >= tier.min_total_score]
        scored.sort(key=lambda item: item.total_score, reverse=True)
        for item in scored:
            selected.append(item)
            selected_urls.add(item.product.url)
            if len(selected) >= limit:
                return selected
    return selected


def count_filter_results(products: list[Product], tiers: list[SelectionTier]) -> dict[str, int]:
    results: dict[str, int] = {}
    for tier in tiers:
        results[tier.name] = sum(
            1
            for product in products
            if product.url
            and product.review_count >= tier.min_review_count
            and product.review_average >= tier.min_review_average
        )
    return results


def score_all_products(products: list[Product], today: date) -> list[ScoredProduct]:
    scored = [score_product(product, today, selection_tier="diagnostic") for product in products if product.url]
    scored.sort(key=lambda item: item.total_score, reverse=True)
    return scored


def score_product(
    product: Product, today: date, *, selection_tier: str = "strict"
) -> ScoredProduct:
    sales_score = score_sales(product.review_count)
    rating_score = score_rating(product.review_average)
    target_score = score_terms(product.text, TARGET_WORDS, points_per_match=4, max_score=20)
    seasonal_score = score_terms(
        product.text,
        SEASONAL_WORDS_BY_MONTH[today.month],
        points_per_match=3,
        max_score=10,
    )
    room_score = score_terms(product.text, ROOM_WORDS, points_per_match=2, max_score=10)
    total_score = sales_score + rating_score + target_score + seasonal_score + room_score
    product_rank = classify_product(product, seasonal_score)
    return ScoredProduct(
        product=product,
        selection_tier=selection_tier,
        sales_score=sales_score,
        rating_score=rating_score,
        target_score=target_score,
        seasonal_score=seasonal_score,
        room_score=room_score,
        total_score=total_score,
        product_rank=product_rank,
        recommendation_reason=build_recommendation_reason(
            product, total_score, seasonal_score, room_score
        ),
    )


def score_sales(review_count: int) -> int:
    if review_count >= 5001:
        return 40
    if review_count >= 1001:
        return 30
    if review_count >= 301:
        return 20
    if review_count >= 100:
        return 10
    return 0


def score_rating(review_average: float) -> int:
    if review_average >= 4.7:
        return 20
    if review_average >= 4.5:
        return 15
    if review_average >= 4.3:
        return 10
    return 0


def score_terms(text: str, terms: list[str], *, points_per_match: int, max_score: int) -> int:
    normalized = text.lower()
    matches = sum(1 for term in terms if term.lower() in normalized)
    return min(max_score, matches * points_per_match)


def classify_product(product: Product, seasonal_score: int) -> str:
    if product.review_count >= 1000 and product.review_average >= 4.5:
        return "Aランク"
    if seasonal_score >= 8:
        return "Cランク"
    return "Bランク"


def build_recommendation_reason(
    product: Product, total_score: int, seasonal_score: int, room_score: int
) -> str:
    reasons = [
        f"レビュー{product.review_count:,}件、評価{product.review_average:.2f}で比較材料が多い商品です。",
        f"総合スコアは{total_score}点です。",
    ]
    if seasonal_score >= 8:
        reasons.append("今月の季節需要ワードにも合っています。")
    if room_score >= 6:
        reasons.append("楽天ROOMで反応されやすい訴求語も含まれています。")
    return "".join(reasons)
