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
    "総合スコアは",
    "楽天ROOMで反応されやすい訴求語",
    "比較材料が多い商品です",
    "確認できる情報が多いのがポイントです",
    "家族の予定が詰まっている日でも選びやすい",
    "必要な条件を短時間で見比べやすい",
    "まずは商品ページで",
    "訴求語",
    "総合スコア",
]

BROAD_LOW_INTENT_TAGS = {"#子育て", "#育児", "#ママ", "#パパ"}
BRAND_HASHTAG = "#とらパパ厳選"

CATEGORY_TAGS = {
    "育児便利グッズ": "#育児便利グッズ",
    "ベビー用品": "#ベビーグッズ",
    "キッズ用品": "#キッズ用品",
    "知育玩具": "#知育玩具",
    "おうち遊び": "#おうち遊びグッズ",
    "外遊び": "#外遊びグッズ",
    "育児時短グッズ": "#育児時短グッズ",
    "子ども靴": "#キッズシューズ",
    "絵本": "#絵本",
    "育児家電": "#育児家電",
    "プレゼント向け商品": "#キッズギフト",
}


def build_post_text(scored: ScoredProduct) -> str:
    product = scored.product
    short_name = shorten_product_name(product.name)
    benefit = category_benefit(product.category)
    text = (
        f"【タイトル】\n{short_name}で{benefit} ✨\n\n"
        "【投稿文】\n"
        f"{empathy_sentence(product.category)} "
        f"{short_name}は、{appeal_sentence(product.category)}。"
        "商品ページで確認できる範囲では、毎日の遊びや準備に取り入れやすい候補です🧸 "
        f"親目線では、{parent_benefit(product.category)}のが助かるところ。"
        "購入前は対象年齢、サイズ、素材、配送条件を確認しておきたいですね📝 "
        f"{recommendation_sentence(product.category, product.text)}"
    )
    return sanitize_post_text(text)


def build_hashtags(scored: ScoredProduct) -> str:
    product = scored.product
    tags = [
        purchase_intent_tag(product.category, product.text),
        category_tag(product.category, product.text),
        problem_solving_tag(product.category, product.text),
        target_tag(product.category, product.text),
        BRAND_HASHTAG,
    ]
    return " ".join(tags)


def sanitize_post_text(text: str) -> str:
    sanitized = text
    for expression in BANNED_EXPRESSIONS:
        sanitized = sanitized.replace(expression, "")
    lines = [" ".join(line.split()) for line in sanitized.splitlines()]
    compact_lines: list[str] = []
    for line in lines:
        if line or (compact_lines and compact_lines[-1]):
            compact_lines.append(line)
    return "\n".join(compact_lines).strip()


def shorten_product_name(name: str, max_length: int = 28) -> str:
    cleaned = name
    for token in ["【", "】", "[", "]", "(", ")", "（", "）", " 送料無料", " 送料込"]:
        cleaned = cleaned.replace(token, " ")
    cleaned = " ".join(cleaned.split())
    if len(cleaned) <= max_length:
        return cleaned
    return cleaned[:max_length].rstrip() + "..."


def category_benefit(category: str) -> str:
    benefits = {
        "育児便利グッズ": "毎日の育児を少しラクに",
        "ベビー用品": "赤ちゃんまわりを整えやすく",
        "キッズ用品": "子どもの準備をスムーズに",
        "知育玩具": "遊びながら学びやすく",
        "おうち遊び": "家の中でも楽しく過ごしやすく",
        "外遊び": "外遊びをもっと楽しみやすく",
        "育児時短グッズ": "育児の手間を減らしやすく",
        "子ども靴": "通園や外遊びを歩きやすく",
        "絵本": "親子時間を作りやすく",
        "育児家電": "育児の負担を減らしやすく",
        "プレゼント向け商品": "贈り物を選びやすく",
    }
    return benefits.get(category, "育児の悩みを減らしやすく")


def empathy_sentence(category: str) -> str:
    sentences = {
        "知育玩具": "遊びながら学べるものを選びたいけど、種類が多くて迷いますよね。",
        "おうち遊び": "雨の日や夕方、家の中でどう遊ばせるか悩むことありますよね。",
        "外遊び": "外で思いきり遊ばせたいけど、準備や安全面も気になりますよね。",
        "子ども靴": "子どもの靴選びは、歩きやすさと脱ぎ履きのしやすさで迷いますよね。",
        "絵本": "寝る前や休日に、親子で落ち着ける時間を作りたいですよね。",
        "育児家電": "育児と家事が重なる時間帯は、少しでも手間を減らしたいですよね。",
    }
    return sentences.get(category, "毎日の育児、ちょっとした準備や片づけが積み重なって大変ですよね。")


def appeal_sentence(category: str) -> str:
    appeals = {
        "知育玩具": "子どもが手を動かしながら遊べる知育系アイテム",
        "おうち遊び": "室内でも子どもが集中して過ごしやすいアイテム",
        "外遊び": "公園やお出かけ時間を楽しみやすくするアイテム",
        "育児時短グッズ": "忙しい時間の小さな手間を減らしやすいアイテム",
        "子ども靴": "通園や外遊びで使いやすそうなキッズアイテム",
        "絵本": "親子で会話しながら楽しみやすい一冊",
        "育児家電": "家事と育児が重なる時間の負担を減らしやすいアイテム",
        "プレゼント向け商品": "贈り物にも選びやすい育児アイテム",
    }
    return appeals.get(category, "日常の育児シーンで使いやすそうなアイテム")


def parent_benefit(category: str) -> str:
    benefits = {
        "知育玩具": "遊びのきっかけを作りやすい",
        "おうち遊び": "家で過ごす時間の選択肢が増える",
        "外遊び": "出かける前の準備を考えやすい",
        "育児時短グッズ": "手間をひとつ減らしやすい",
        "子ども靴": "朝の支度や外遊びに使いやすい",
        "絵本": "親子で落ち着く時間を作りやすい",
        "育児家電": "家事と育児の負担を分けやすい",
    }
    return benefits.get(category, "毎日の育児に取り入れる場面を想像しやすい")


def recommendation_sentence(category: str, product_text: str) -> str:
    if "ギフト" in product_text or "プレゼント" in product_text or category == "プレゼント向け商品":
        return "プレゼント候補を探している家庭にもおすすめです🎁"
    if category in {"知育玩具", "絵本", "おうち遊び"}:
        return "おうち時間を親子で楽しみたい家庭におすすめです😊"
    if category in {"子ども靴", "外遊び"}:
        return "通園や外遊びが増えてきた家庭におすすめです😊"
    return "忙しい育児の中で、使いやすさを重視したい家庭におすすめです😊"


def purchase_intent_tag(category: str, product_text: str) -> str:
    if "ギフト" in product_text or "プレゼント" in product_text or category == "プレゼント向け商品":
        return "#プレゼントにおすすめ"
    if "入園" in product_text or "通園" in product_text or category == "子ども靴":
        return "#入園準備におすすめ"
    if category in {"知育玩具", "絵本", "おうち遊び"}:
        return "#買ってよかった"
    if category in {"育児時短グッズ", "育児家電"}:
        return "#おすすめ品"
    return "#買ってよかった"


def category_tag(category: str, product_text: str) -> str:
    if "お風呂" in product_text or "バス" in product_text:
        return "#お風呂グッズ"
    if "靴" in product_text or "シューズ" in product_text:
        return "#キッズシューズ"
    if "絵本" in product_text:
        return "#絵本"
    if "知育" in product_text:
        return "#知育玩具"
    if "ベビー" in product_text or "赤ちゃん" in product_text:
        return "#ベビーグッズ"
    return CATEGORY_TAGS.get(category, "#育児便利グッズ")


def problem_solving_tag(category: str, product_text: str) -> str:
    if "お風呂" in product_text or "バス" in product_text:
        return "#お風呂嫌い対策"
    if "イヤイヤ" in product_text:
        return "#イヤイヤ期対策"
    if "時短" in product_text or category in {"育児時短グッズ", "育児家電"}:
        return "#育児時短"
    if "入園" in product_text or "通園" in product_text:
        return "#通園準備"
    if category in {"知育玩具", "おうち遊び", "絵本"}:
        return "#おうち遊び"
    if category in {"外遊び", "子ども靴"}:
        return "#外遊び準備"
    return "#育児時短"


def target_tag(category: str, product_text: str) -> str:
    if "2歳" in product_text or "2才" in product_text:
        return "#2歳育児"
    if "3歳" in product_text or "3才" in product_text:
        return "#3歳育児"
    if "4歳" in product_text or "4才" in product_text:
        return "#4歳育児"
    if "5歳" in product_text or "5才" in product_text:
        return "#5歳育児"
    if "保育園" in product_text or "通園" in product_text or "入園" in product_text:
        return "#保育園準備"
    if category in {"育児時短グッズ", "育児家電"}:
        return "#共働き育児"
    return "#3歳育児"
