from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import date

from rakuten_api import Product
from product_type import (
    APPEAL_APPLIANCE,
    APPEAL_BATH,
    APPEAL_CONSUMABLE,
    APPEAL_DEFAULT,
    APPEAL_EDUCATIONAL,
    APPEAL_FEEDING,
    APPEAL_GIFT,
    APPEAL_KIDS_CAMERA,
    APPEAL_OUTING,
    APPEAL_SHOES,
    APPEAL_SLEEP,
    APPEAL_STORAGE,
    classify_product_type,
    classify_room_product_type,
    product_display_name,
    purchase_checkpoints,
    room_product_label,
)

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
    "送料無料",
    "セット",
    "かわいい",
    "おしゃれ",
    "限定",
    "セール",
    "ポイント",
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
    price_score: int
    seasonal_score: int
    room_score: int
    daily_use_score: int
    pain_solution_score: int
    gift_score: int
    specificity_score: int
    demand_score: int
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
        min_total_score=int(os.getenv("STRICT_MIN_TOTAL_SCORE", "60")),
    )

    if os.getenv("ENABLE_RELAXED_FALLBACK", "true").lower() not in {"1", "true", "yes"}:
        return [strict]

    return [
        SelectionTier(
            name="strict_priority",
            min_review_count=int(os.getenv("STRICT_PRIORITY_MIN_REVIEW_COUNT", "300")),
            min_review_average=strict.min_review_average,
            min_total_score=int(os.getenv("STRICT_PRIORITY_TOTAL_SCORE", "70")),
        ),
        strict,
        SelectionTier(
            name="relaxed",
            min_review_count=int(os.getenv("RELAXED_MIN_REVIEW_COUNT", "30")),
            min_review_average=float(os.getenv("RELAXED_MIN_REVIEW_AVERAGE", "4.0")),
            min_total_score=int(os.getenv("RELAXED_MIN_TOTAL_SCORE", "40")),
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
    target_score = score_terms(product.text, TARGET_WORDS, points_per_match=3, max_score=15)
    price_score = score_price(product.price)
    seasonal_score = score_terms(
        product.text,
        SEASONAL_WORDS_BY_MONTH[today.month],
        points_per_match=2,
        max_score=5,
    )
    room_score = score_terms(product.text, ROOM_WORDS, points_per_match=2, max_score=10)
    daily_use_score = score_terms(
        product.text,
        ["毎日", "日常", "洗い替え", "消耗", "おむつ", "おしりふき", "食事", "通園", "収納"],
        points_per_match=3,
        max_score=10,
    )
    pain_solution_score = score_terms(
        product.text,
        ["時短", "片づけ", "食べこぼし", "寝かしつけ", "持ち運び", "まとめ買い", "防水", "収納", "軽量"],
        points_per_match=3,
        max_score=10,
    )
    gift_score = score_terms(
        product.text,
        ["ギフト", "プレゼント", "名入れ", "出産祝い", "誕生日"],
        points_per_match=3,
        max_score=10,
    )
    specificity_score = score_specificity(product)
    raw_score = (
        sales_score
        + rating_score
        + target_score
        + price_score
        + room_score
        + seasonal_score
        + daily_use_score
        + pain_solution_score
        + gift_score
        + specificity_score
    )
    total_score = round(raw_score * 100 / 110)
    demand_score = min(100, sales_score * 3 + seasonal_score * 4 + daily_use_score * 3)
    product_rank = classify_product(product, seasonal_score)
    return ScoredProduct(
        product=product,
        selection_tier=selection_tier,
        sales_score=sales_score,
        rating_score=rating_score,
        target_score=target_score,
        price_score=price_score,
        seasonal_score=seasonal_score,
        room_score=room_score,
        daily_use_score=daily_use_score,
        pain_solution_score=pain_solution_score,
        gift_score=gift_score,
        specificity_score=specificity_score,
        demand_score=demand_score,
        total_score=total_score,
        product_rank=product_rank,
        recommendation_reason=build_recommendation_reason(
            product, total_score, seasonal_score, room_score
        ),
    )


def score_sales(review_count: int) -> int:
    if review_count >= 5001:
        return 20
    if review_count >= 1001:
        return 17
    if review_count >= 301:
        return 13
    if review_count >= 100:
        return 7
    return 0


def score_rating(review_average: float) -> int:
    if review_average >= 4.7:
        return 15
    if review_average >= 4.5:
        return 12
    if review_average >= 4.3:
        return 8
    return 0


def score_price(price: int) -> int:
    if 3000 <= price <= 8000:
        return 10
    if 1500 <= price <= 12000:
        return 7
    return 3


def score_terms(text: str, terms: list[str], *, points_per_match: int, max_score: int) -> int:
    normalized = text.lower()
    matches = sum(1 for term in terms if term.lower() in normalized)
    return min(max_score, matches * points_per_match)


def score_specificity(product: Product) -> int:
    text = product.text
    signals = [
        any(char.isdigit() for char in text),
        any(word in text for word in ["cm", "枚", "個", "本", "ml", "kg", "g"]),
        any(word in text for word in ["対象年齢", "サイズ", "容量", "素材", "セット", "電源", "充電"]),
    ]
    return min(5, sum(signals) * 2)


def classify_product(product: Product, seasonal_score: int) -> str:
    if product.review_count >= 1000 and product.review_average >= 4.5:
        return "Aランク"
    if seasonal_score >= 5:
        return "Cランク"
    return "Bランク"


def product_reason_anchor(product: Product) -> str:
    detailed_type = classify_room_product_type(product)
    return room_product_label(product, detailed_type) or product_display_name(product)


def product_decision_point(product: Product) -> str:
    product_type = classify_product_type(product)
    if product_type == APPEAL_CONSUMABLE:
        return "切らすとすぐ困る消耗品で、買い忘れ防止とストック需要を作りやすい"
    if product_type == APPEAL_EDUCATIONAL:
        return "手先を使う遊びや色・形への興味を親子で広げやすい"
    if product_type == APPEAL_KIDS_CAMERA:
        return "子どもの誕生日や外出先で写真遊びを楽しみたい家庭に刺さる"
    if product_type == APPEAL_SLEEP:
        return "夜の授乳や寝かしつけ前の音と灯りをまとめたい家庭に刺さる"
    if product_type == APPEAL_SHOES:
        return "登園前や公園前の支度で、履かせやすさ・歩きやすさの悩みに刺さる"
    if product_type == APPEAL_OUTING:
        return "子連れ外出の荷物や移動中の不安を減らしたい家庭に刺さる"
    if product_type == APPEAL_FEEDING:
        return "食後の片づけや自分で食べたい時期の負担を減らしたい家庭に刺さる"
    if product_type == APPEAL_STORAGE:
        return "散らかりや戻す場所に悩む家庭に刺さり、リビング整理の動機を作りやすい"
    if product_type == APPEAL_APPLIANCE:
        return "寝かしつけ後や朝の家事負担を減らしたい家庭に訴求しやすい"
    if product_type == APPEAL_BATH:
        return "ワンオペ入浴の準備と片づけを回しやすくしたい家庭に刺さる"
    if product_type == APPEAL_GIFT:
        return "贈った後に実際の育児で使われる実用性を伝えやすい"
    return "使う場面が具体的に浮かび、購入後の出番を説明しやすい"


def product_purchase_check(product: Product) -> str:
    return purchase_checkpoints(product, classify_product_type(product))


def build_recommendation_reason(
    product: Product, total_score: int, seasonal_score: int, room_score: int
) -> str:
    del total_score, seasonal_score, room_score
    product_type = classify_room_product_type(product)
    text = product.text
    label = room_product_label(product, product_type) or product_reason_anchor(product)
    quantity = first_quantity(text)
    feature = (
        recommendation_feature(product_type, text, quantity)
        if product_type != "unknown"
        else f"{label}の確認済み情報を比較できる"
    )
    wipe_scene = (
        "食後や外出先で手口ふきを使う家庭"
        if label == "手口ふき"
        else "おむつ替えで使う分を切らしたくない家庭"
    )
    scene = {
        "wipes": wipe_scene,
        "diaper": "サイズ変更の時期にストック量で迷う家庭",
        "formula": "授乳回数に合わせて残量を管理したい家庭",
        "sound_blocks": "音や形に触れる家遊びを取り入れたい家庭",
        "wooden_blocks": "積む・並べる遊びを家で楽しみたい家庭",
        "magnetic_blocks": "平面から立体へ組み立てる遊びを楽しみたい家庭",
        "activity_cube": "型はめやルーピングを一台で試したい家庭",
        "ring_toy": "紐通しやリング遊びで指先を使いたい家庭",
        "kids_camera": "散歩や旅行を子ども目線で残したい家庭",
        "sleep_light": "夜の授乳やおむつ替えの環境を整えたい家庭",
        "stroller_storage": "ベビーカー周りの荷物を取り出しやすくしたい家庭",
    }.get(product_type, "使う場面が具体的な家庭")
    checks = recommendation_checkpoints(product_type)
    return f"{scene}に合い、{feature}一方、{checks}は購入前に確認したい。"


def first_quantity(text: str) -> str:
    match = re.search(
        r"\d+(?:\.\d+)?\s*(?:枚|個|本|缶|袋|箱|ピース|パーツ|ポケット|ml|mL|g|kg)",
        text,
        flags=re.IGNORECASE,
    )
    return match.group(0) if match else ""


def recommendation_feature(product_type: str, text: str, quantity: str) -> str:
    quantity_text = f"{quantity}入りの" if quantity else ""
    if product_type == "wipes":
        thickness = "厚手の" if "厚手" in text else ""
        return f"{thickness}{quantity_text}{room_product_label_from_text(product_type, text)}をまとめて置ける"
    if product_type == "diaper":
        style = "パンツタイプ" if "パンツタイプ" in text else "テープタイプ" if "テープタイプ" in text else ""
        return f"{style}{quantity_text}紙おむつを使用量に合わせて管理しやすい"
    if product_type == "formula":
        return f"{quantity_text}{room_product_label_from_text(product_type, text)}の未開封分を把握しやすい"
    if product_type == "sound_blocks":
        material = "木製" if "木製" in text else ""
        quantity_text = f"{quantity}の" if quantity else ""
        base = f"{quantity_text}音が鳴る{material}積み木"
        if "名入れ" in text:
            return f"{base}は名入れにも対応し、振る・積む遊びを変えられる"
        return f"{base}で振る・積む遊びを変えられる"
    if product_type == "wooden_blocks":
        quantity_text = f"{quantity}の" if quantity else ""
        storage = "収納袋へまとめられる" if "収納袋" in text else "形を組み替えられる"
        return f"{quantity_text}木製積み木で積む・並べる遊びができ、{storage}"
    if product_type == "magnetic_blocks":
        quantity_text = f"{quantity}の" if quantity else ""
        return f"{quantity_text}マグネットブロックで平面と立体を組み替えられる"
    if product_type == "activity_cube":
        return "型はめとルーピングを備えたアクティビティキューブで遊びを切り替えられる"
    if product_type == "ring_toy":
        quantity_text = f"{quantity}の" if quantity else ""
        return f"リングと紐通しを含む{quantity_text}リング玩具で遊び方を変えられる"
    if product_type == "kids_camera":
        details = [value for value, marker in [("スマホ転送", "スマホ転送"), ("SDカード対応", "sdカード"), ("ゲームなし", "ゲームなし"), ("USB充電", "usb")] if marker.lower() in text]
        return f"{'・'.join(details)}のキッズカメラで撮影後まで扱いやすい"
    if product_type == "sleep_light":
        details = [value for value, marker in [("ホワイトノイズ", "ホワイトノイズ"), ("授乳ライト", "授乳ライト"), ("コードレス", "コードレス")] if marker in text]
        return f"{'・'.join(details)}を一台で使い分けられる"
    if product_type == "stroller_storage":
        details = [value for value, marker in [("防水", "防水"), ("軽量", "軽量")] if marker in text]
        if quantity and "ポケット" in quantity:
            details.append(f"{quantity}付き")
        return f"{'・'.join(details)}のベビーカーバッグへ小物を分けて入れられる"
    return f"{room_product_label_from_text(product_type, text)}の確認済み仕様を使える"


def room_product_label_from_text(product_type: str, text: str) -> str:
    if product_type == "wipes":
        return "手口ふき" if "手口ふき" in text or "手口拭き" in text else "おしりふき"
    if product_type == "formula":
        return "粉ミルク" if "粉ミルク" in text else "液体ミルク" if "液体ミルク" in text else "ミルク"
    return {
        "diaper": "紙おむつ",
        "sound_blocks": "音が鳴る積み木",
        "wooden_blocks": "木製積み木",
        "magnetic_blocks": "マグネットブロック",
        "activity_cube": "アクティビティキューブ",
        "ring_toy": "リング玩具",
        "kids_camera": "キッズカメラ",
        "sleep_light": "ライト",
        "stroller_storage": "ベビーカーバッグ",
    }.get(product_type, "商品")


def recommendation_checkpoints(product_type: str) -> str:
    return {
        "wipes": "個数・収納場所・1個あたり価格",
        "diaper": "サイズ・枚数・1枚あたり価格",
        "formula": "容量・個数・賞味期限",
        "sound_blocks": "対象年齢・パーツサイズ・名入れ内容",
        "wooden_blocks": "対象年齢・パーツサイズ・収納方法",
        "magnetic_blocks": "対象年齢・パーツサイズ・パーツ数",
        "activity_cube": "対象年齢・本体サイズ・置き場所",
        "ring_toy": "対象年齢・パーツ数・パーツサイズ",
        "kids_camera": "対象年齢・転送方法・充電方式",
        "sleep_light": "音量調整・ライト機能・電源方式",
        "stroller_storage": "サイズ・取り付け方法・容量",
    }.get(product_type, "仕様・サイズ・使う場所")
