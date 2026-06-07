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
    short_name = shorten_product_name(product.name)
    benefit = category_benefit(product.category)
    text = (
        f"【タイトル】\n{short_name}で{benefit}\n\n"
        "【投稿文】\n"
        f"{empathy_sentence(product.category)}"
        f"{short_name}は、{appeal_sentence(product.category)}。"
        "商品ページで確認できる範囲では、毎日の遊びや準備に取り入れやすい候補です。"
        f"パパ目線では、{parent_benefit(product.category)}のが助かるところ。"
        "購入前は対象年齢、サイズ、素材、配送条件を確認しておきたいですね。"
        f"{recommendation_sentence(product.category, product.text)}"
    )
    return sanitize_post_text(text)


def build_hashtags(category: str, product_rank: str) -> str:
    tags = REQUIRED_HASHTAGS + CATEGORY_HASHTAGS.get(category, ["#子育て", "#育児", "#パパ育児"])
    unique_tags = list(dict.fromkeys(tags))
    return " ".join(unique_tags[:5])


def sanitize_post_text(text: str) -> str:
    sanitized = text
    for expression in BANNED_EXPRESSIONS:
        sanitized = sanitized.replace(expression, "")
    return " ".join(sanitized.split())


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
        return "プレゼント候補を探している家庭にもおすすめです。"
    if category in {"知育玩具", "絵本", "おうち遊び"}:
        return "おうち時間を親子で楽しみたい家庭におすすめです。"
    if category in {"子ども靴", "外遊び"}:
        return "通園や外遊びが増えてきた家庭におすすめです。"
    return "忙しい育児の中で、使いやすさを重視したい家庭におすすめです。"
