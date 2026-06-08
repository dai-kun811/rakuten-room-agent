from __future__ import annotations

from scoring import ScoredProduct
from rakuten_api import Product

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
    "レビュー件数が多いので人気です",
    "評価が高いのでおすすめです",
    "レビューで好評です",
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
    appeal = determine_appeal_category(product)
    benefit = build_benefit(product, appeal)
    child_reason = child_fit_sentence(product, appeal)
    differentiation_reason = review_differentiation_sentence(product, appeal)
    child_benefit = child_benefit_sentence(product, appeal, benefit)
    parent_value = parent_benefit_sentence(product, appeal)
    check_sentence = purchase_check_sentence(product, appeal)
    text = (
        f"【{benefit}◎ {short_name}】\n\n"
        "【投稿文】\n"
        f"{empathy_sentence(appeal)}\n\n"
        f"{child_reason}\n\n"
        f"{differentiation_reason}\n\n"
        f"{child_benefit}\n\n"
        f"親目線では、{parent_value}のが助かるところ✨\n\n"
        f"{check_sentence}"
        f"{recommendation_sentence(appeal, product.text)}"
    )
    return sanitize_post_text(text)


def build_hashtags(scored: ScoredProduct) -> str:
    product = scored.product
    appeal = determine_appeal_category(product)
    tags = [
        purchase_intent_tag(appeal, product.text),
        category_tag(product.category, product.text, appeal),
        problem_solving_tag(appeal, product.text),
        target_or_gift_tag(appeal, product.text),
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


def empathy_sentence(appeal: str) -> str:
    sentences = {
        "おうち遊び": "雨の日や夕方、家の中でどう遊ばせるか悩むことありますよね🧸",
        "お風呂嫌い対策": "お風呂の時間、毎回スムーズにいかないと親もぐったりしますよね。",
        "イヤイヤ期対策": "イヤイヤ期の声かけ、何を選ぶかで少しでもラクにしたいですよね。",
        "知育玩具": "遊びながら学べるものを選びたいけど、すぐ飽きないか心配ですよね🧸",
        "プレゼント": "子ども向けのプレゼント、喜ばれそうで長く使えるものを選びたいですよね🎁",
        "育児時短": "育児と家事が重なる時間帯は、少しでも手間を減らしたいですよね。",
        "通園準備": "通園や朝の支度、できるだけバタバタを減らしたいですよね。",
        "食事サポート": "食事まわりは、こぼす・飽きる・片づけるで毎日大変ですよね。",
        "外遊び": "外で思いきり遊ばせたいけど、準備や安全面も気になりますよね。",
        "睡眠サポート": "寝かしつけや夜の環境づくり、少しでも落ち着く形にしたいですよね。",
    }
    return sentences.get(appeal, "毎日の育児、ちょっとした準備や片づけが積み重なって大変ですよね。")


def recommendation_sentence(appeal: str, product_text: str) -> str:
    if appeal == "プレゼント" or "ギフト" in product_text or "プレゼント" in product_text:
        return "「プレゼントで失敗したくない」家庭にもおすすめです🎁"
    if appeal in {"知育玩具", "おうち遊び"}:
        return "おうち時間を親子で楽しみたい家庭におすすめです😊"
    if appeal in {"通園準備", "外遊び"}:
        return "通園や外遊びが増えてきた家庭におすすめです😊"
    if appeal == "育児時短":
        return "忙しい育児の中で、手間を減らしたい家庭におすすめです😊"
    return "子どもの成長や毎日の使いやすさを大事にしたい家庭におすすめです😊"


def purchase_intent_tag(appeal: str, product_text: str) -> str:
    if "ギフト" in product_text or "プレゼント" in product_text or appeal == "プレゼント":
        return "#プレゼントにおすすめ"
    if appeal == "通園準備":
        return "#入園準備におすすめ"
    if appeal in {"知育玩具", "おうち遊び"}:
        return "#買ってよかった"
    if appeal == "育児時短":
        return "#おすすめ品"
    return "#買ってよかった"


def category_tag(category: str, product_text: str, appeal: str) -> str:
    if "お風呂" in product_text or "バス" in product_text:
        return "#お風呂グッズ"
    if appeal == "食事サポート":
        return "#食事サポート"
    if "靴" in product_text or "シューズ" in product_text:
        return "#キッズシューズ"
    if "絵本" in product_text:
        return "#絵本"
    if "知育" in product_text:
        return "#知育玩具"
    if "ベビー" in product_text or "赤ちゃん" in product_text:
        return "#ベビーグッズ"
    return CATEGORY_TAGS.get(category, "#育児便利グッズ")


def problem_solving_tag(appeal: str, product_text: str) -> str:
    if appeal == "プレゼント":
        if any(word in product_text for word in ["知育", "絵本", "おうち遊び", "おもちゃ"]):
            return "#おうち遊び"
        if any(word in product_text for word in ["通園", "入園", "保育園"]):
            return "#通園準備"
        if any(word in product_text for word in ["食事", "離乳食", "食器"]):
            return "#食事の悩み対策"
    if appeal == "お風呂嫌い対策":
        return "#お風呂嫌い対策"
    if appeal == "イヤイヤ期対策":
        return "#イヤイヤ期対策"
    if appeal == "育児時短":
        return "#育児時短"
    if appeal == "通園準備":
        return "#通園準備"
    if appeal in {"知育玩具", "おうち遊び"}:
        return "#おうち遊び"
    if appeal == "食事サポート":
        return "#食事の悩み対策"
    if appeal == "睡眠サポート":
        return "#寝かしつけ対策"
    if appeal == "外遊び":
        return "#外遊び準備"
    return "#育児時短"


def target_or_gift_tag(appeal: str, product_text: str) -> str:
    if appeal == "プレゼント":
        if "出産" in product_text or "ベビー" in product_text or "赤ちゃん" in product_text:
            return "#出産祝い"
        return "#1歳誕生日プレゼント"
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
    if appeal == "育児時短":
        return "#共働き育児"
    return "#3歳育児"


def determine_appeal_category(product: Product) -> str:
    text = product.text
    rules = [
        ("プレゼント", ["ギフト", "プレゼント", "誕生日", "出産祝い"]),
        ("お風呂嫌い対策", ["お風呂", "バス", "湯船", "シャンプー"]),
        ("イヤイヤ期対策", ["イヤイヤ", "トイトレ", "しつけ"]),
        ("食事サポート", ["離乳食", "食事", "ごはん", "スプーン", "フォーク", "食器", "エプロン"]),
        ("睡眠サポート", ["寝かしつけ", "睡眠", "おやすみ", "ベッド", "布団", "枕"]),
        ("通園準備", ["保育園", "通園", "入園", "名前", "シューズ", "靴"]),
        ("育児時短", ["時短", "家電", "自動", "簡単", "片づけ", "収納"]),
        ("知育玩具", ["知育", "積み木", "パズル", "ブロック", "リング", "型はめ"]),
        ("外遊び", ["外遊び", "公園", "水遊び", "三輪車", "ボール", "砂場"]),
        ("おうち遊び", ["おうち遊び", "室内", "絵本", "おもちゃ", "ぬりえ"]),
    ]
    for appeal, keywords in rules:
        if product.category == appeal or any(keyword.lower() in text for keyword in keywords):
            return appeal
    category_map = {
        "知育玩具": "知育玩具",
        "おうち遊び": "おうち遊び",
        "外遊び": "外遊び",
        "育児時短グッズ": "育児時短",
        "子ども靴": "通園準備",
        "絵本": "おうち遊び",
        "育児家電": "育児時短",
        "プレゼント向け商品": "プレゼント",
    }
    return category_map.get(product.category, "育児時短")


def build_benefit(product: Product, appeal: str) -> str:
    text = product.text
    if appeal == "知育玩具":
        if "リング" in text or "紐" in text or "ひも" in text:
            return "遊びながら指先を使える"
        if "ブロック" in text or "積み木" in text:
            return "自分で作りたい欲を満たせる"
        return "遊びながら考える力を育てやすい"
    if appeal == "お風呂嫌い対策":
        return "お風呂時間を楽しい時間に変えやすい"
    if appeal == "プレゼント":
        return "贈る相手の育児に役立ちやすい"
    if appeal == "育児時短":
        return "親の準備や片づけの負担を減らしやすい"
    if appeal == "通園準備":
        return "朝の支度や通園準備をスムーズにしやすい"
    if appeal == "食事サポート":
        return "食事まわりの負担を減らしやすい"
    if appeal == "外遊び":
        return "外で体を動かすきっかけを作りやすい"
    if appeal == "睡眠サポート":
        return "寝る前の環境を整えやすい"
    if appeal == "イヤイヤ期対策":
        return "子どものやりたい気持ちに寄り添いやすい"
    return "親子で過ごす時間を楽しくしやすい"


def child_fit_sentence(product: Product, appeal: str) -> str:
    age = age_phrase(product.text)
    if appeal == "知育玩具":
        return f"{age}は、つまむ・並べる・考える遊びが増えてくる時期。"
    if appeal == "おうち遊び":
        return f"{age}は、家の中でも集中して遊べるものがあると助かる時期。"
    if appeal == "通園準備":
        return f"{age}は、自分でやりたい気持ちが少しずつ出てくる時期。"
    if appeal == "食事サポート":
        return f"{age}は、食べる練習や食事のリズムを作りたい時期。"
    if appeal == "睡眠サポート":
        return f"{age}は、寝る前の流れを整えたい時期。"
    return f"{age}の育児に取り入れやすい候補です。"


def choice_reason_sentence(product: Product, appeal: str) -> str:
    if appeal == "知育玩具":
        return "遊び方が1つで終わらず、成長に合わせて使い方を変えやすいところ。"
    if appeal == "プレゼント":
        return "育児中の家庭で使う場面を想像しやすく、贈り物として選びやすいところ。"
    if appeal == "育児時短":
        return "毎日の小さな手間を減らす目的がはっきりしているところ。"
    if appeal == "通園準備":
        return "朝の支度や通園まわりで使う場面が分かりやすいところ。"
    if appeal == "お風呂嫌い対策":
        return "苦手になりがちな時間を楽しい方向に変えやすいところ。"
    return "育児の悩みに対して使う場面が分かりやすいところ。"


def review_differentiation_sentence(product: Product, appeal: str) -> str:
    text = product.text
    if any(word in text for word in ["ring10", "ring 10", "リング10"]) or (
        "リング" in text and appeal == "知育玩具"
    ):
        return "遊び方が1つではなく、成長に合わせて長く遊べるところが選ばれている理由のひとつ✨"
    if any(word in text for word in ["マグビルド", "マグネット"]) and any(
        word in text for word in ["スロープ", "ボール", "コース"]
    ):
        return "マグネットブロックはたくさんありますが、スロープ遊びまで楽しめるので長く遊びやすいのが魅力😊"
    if "音いっぱい" in text or ("音" in text and any(word in text for word in ["積み木", "つみき"])):
        return "積むだけでなく音の違いも楽しめるので、はじめての積み木として選ばれているようです🎵"
    if any(word in text for word in ["キッズカメラ", "子どもカメラ", "トイカメラ"]):
        return "ゲーム機能よりも「写真を撮る楽しさ」を重視したい家庭に人気のアイテム📸"
    if appeal == "プレゼント":
        return "贈る相手の生活に取り入れやすく、失敗しにくいギフト候補として選ばれやすい印象です🎁"
    if appeal == "育児時短":
        return "似た便利グッズの中でも、準備や片づけの手間を減らす目的が分かりやすいところが魅力です✨"
    if appeal == "通園準備":
        return "通園まわりで毎日使う場面を想像しやすく、買い足し候補にしやすいところが選ばれる理由です😊"
    if appeal == "食事サポート":
        return "食事の見守りや片づけまで考えやすく、親の負担を減らしやすいところが選ばれやすいポイントです✨"
    if appeal == "お風呂嫌い対策":
        return "お風呂を嫌がる時間を、遊びやすい時間に変えられるところが選ばれている理由のひとつです🛁"
    if appeal == "外遊び":
        return "外で体を動かすきっかけを作りやすく、休日や公園遊びに使いやすいところが魅力です😊"
    if appeal == "睡眠サポート":
        return "寝る前の流れを整えたい家庭にとって、毎日の習慣にしやすいところが選ばれる理由です🌙"
    if appeal in {"知育玩具", "おうち遊び"}:
        return "似たおもちゃの中でも、遊び方を変えながら長く使いやすいところが選ばれている理由のひとつ✨"
    return "育児の悩みに対して使う場面が分かりやすく、親子の生活に取り入れやすいところが魅力です✨"


def child_benefit_sentence(product: Product, appeal: str, benefit: str) -> str:
    if appeal in {"知育玩具", "おうち遊び"}:
        return f"ただ遊ぶだけでなく、{benefit}のが魅力です。"
    if appeal == "外遊び":
        return f"{benefit}ので、体を動かす時間を増やしたい時にも選びやすいです。"
    return f"子どもにとっては、{benefit}のがうれしいポイントです。"


def parent_benefit_sentence(product: Product, appeal: str) -> str:
    if appeal == "育児時短":
        return "準備や片づけの負担を減らしやすい"
    if appeal == "通園準備":
        return "朝のバタバタを少し整えやすい"
    if appeal == "プレゼント":
        return "相手の家庭で使う場面を想像しやすい"
    if appeal == "食事サポート":
        return "食後の片づけや見守りを考えやすい"
    return "遊びや生活に取り入れる場面を想像しやすい"


def purchase_checkpoints(product: Product, appeal: str) -> str:
    checks: list[str] = []
    text = product.text
    if appeal in {"通園準備", "外遊び"} or any(word in text for word in ["靴", "シューズ", "服", "帽子"]):
        checks.append("サイズ")
    if appeal in {"知育玩具", "プレゼント", "おうち遊び"}:
        checks.append("対象年齢")
    if any(word in text for word in ["小さな部品", "ビーズ", "リング", "ブロック", "パーツ"]):
        checks.append("誤飲リスク")
    if any(word in text for word in ["大型", "マット", "収納", "ベッド", "家具", "滑り台"]):
        checks.append("設置・収納スペース")
    if any(word in text for word in ["大型", "メーカー直送", "予約", "冷蔵", "冷凍"]):
        checks.append("配送条件")
    if not checks:
        checks.append("対象年齢や素材")
    return "、".join(dict.fromkeys(checks))


def purchase_check_sentence(product: Product, appeal: str) -> str:
    checkpoints = purchase_checkpoints(product, appeal)
    if should_include_purchase_checkpoints(product, appeal):
        return f"購入前は{checkpoints}だけ確認しておくと安心です📝\n\n"
    return ""


def should_include_purchase_checkpoints(product: Product, appeal: str) -> bool:
    text = product.text
    important_words = [
        "靴",
        "シューズ",
        "服",
        "帽子",
        "小さな部品",
        "ビーズ",
        "リング",
        "ブロック",
        "パーツ",
        "大型",
        "マット",
        "収納",
        "ベッド",
        "家具",
        "滑り台",
        "メーカー直送",
        "予約",
        "冷蔵",
        "冷凍",
    ]
    age_words = [
        "0歳",
        "1歳",
        "2歳",
        "3歳",
        "4歳",
        "5歳",
        "0才",
        "1才",
        "2才",
        "3才",
        "4才",
        "5才",
        "対象年齢",
        "ヶ月",
    ]
    return any(word in text for word in important_words + age_words)


def age_phrase(product_text: str) -> str:
    for age in ["1歳", "2歳", "3歳", "4歳", "5歳"]:
        if age in product_text:
            return f"{age}頃"
    for age in ["1才", "2才", "3才", "4才", "5才"]:
        if age in product_text:
            return f"{age.replace('才', '歳')}頃"
    return "2〜5歳頃"
