from __future__ import annotations

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
    sales_score: int
    rating_score: int
    target_score: int
    seasonal_score: int
    room_score: int
    total_score: int
    product_rank: str
    recommendation_reason: str


def filter_and_score_products(products: list[Product], today: date) -> list[ScoredProduct]:
    scored = [
        score_product(product, today)
        for product in products
        if product.url and product.review_count >= 100 and product.review_average >= 4.3
    ]
    scored.sort(key=lambda item: item.total_score, reverse=True)

    selected = [item for item in scored if item.total_score >= 80][:5]
    if len(selected) < 5:
        seen_urls = {item.product.url for item in selected}
        selected.extend(
            item
            for item in scored
            if item.total_score >= 70 and item.product.url not in seen_urls
        )
    return selected[:5]


def score_product(product: Product, today: date) -> ScoredProduct:
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
