from __future__ import annotations

from scoring import ScoredProduct

BANNED_EXPRESSIONS = [
    "我が家で使っています",
    "実際に買いました",
    "息子が気に入っています",
    "使ってみました",
    "買ってよかった",
    "愛用しています",
    "うちの子",
    "娘が",
    "息子が",
]

REQUIRED_HASHTAGS = ["#楽天ROOM", "#楽天ROOMに載せてます", "#育児便利グッズ"]

CATEGORY_HASHTAGS = {
    "育児便利グッズ": ["#育児グッズ", "#子育て便利グッズ", "#パパ育児"],
    "ベビー用品": ["#ベビー用品", "#赤ちゃんグッズ", "#出産準備"],
    "キッズ用品": ["#キッズ用品", "#子ども用品", "#子育て"],
    "知育玩具": ["#知育玩具", "#知育", "#おもちゃ"],
    "おうち遊び": ["#おうち遊び", "#室内遊び", "#親子時間"],
    "外遊び": ["#外遊び", "#キッズ外遊び", "#公園遊び"],
    "育児時短グッズ": ["#育児時短", "#共働き育児", "#時短グッズ"],
    "子ども靴": ["#子ども靴", "#キッズシューズ", "#通園準備"],
    "絵本": ["#絵本", "#読み聞かせ", "#知育絵本"],
    "育児家電": ["#育児家電", "#時短家電", "#共働き家庭"],
    "プレゼント向け商品": ["#プレゼント", "#キッズギフト", "#出産祝い"],
}


def build_post_text(scored: ScoredProduct) -> str:
    product = scored.product
    gift_sentence = ""
    if "プレゼント" in product.text or "ギフト" in product.text or product.category == "プレゼント向け商品":
        gift_sentence = "ギフトやプレゼント候補として見ても、レビュー数と評価を確認しながら選べる点が安心材料になります。"

    text = (
        "2〜5歳くらいの育児は、遊び、準備、片づけ、移動まで細かい判断が多くて、"
        "共働きだと商品をじっくり比較する時間も取りにくいですよね。"
        f"この商品は「{product.name}」で、レビュー{product.review_count:,}件、"
        f"評価{product.review_average:.2f}と確認できる情報が多いのがポイントです。"
        f"{scored.recommendation_reason}"
        "パパ目線では、家族の予定が詰まっている日でも選びやすく、必要な条件を短時間で見比べやすいところが助かります。"
        f"{gift_sentence}"
        "まずは商品ページでサイズ、対象年齢、配送条件、レビュー内容を確認して、家庭の使い方に合うかチェックしてみてください。"
    )
    return sanitize_post_text(text)


def build_hashtags(category: str, product_rank: str) -> str:
    tags = REQUIRED_HASHTAGS + CATEGORY_HASHTAGS.get(category, ["#子育て", "#育児", "#パパ育児"])
    if product_rank == "Aランク":
        tags.append("#高評価")
    if category != "プレゼント向け商品":
        tags.append("#共働き育児")
    unique_tags = list(dict.fromkeys(tags))
    return " ".join(unique_tags[:10])


def sanitize_post_text(text: str) -> str:
    sanitized = text
    for expression in BANNED_EXPRESSIONS:
        sanitized = sanitized.replace(expression, "")
    return " ".join(sanitized.split())
