from __future__ import annotations

import re

from rakuten_api import Product
from scoring import ScoredProduct

BANNED_EXPRESSIONS = [
    "使ってみました",
    "買ってよかった",
    "おすすめです",
    "人気です",
    "便利です",
    "役立ちます",
    "取り入れやすい",
    "失敗しにくい",
    "使いやすい",
    "神アイテム",
    "バズっている",
    "バズってる",
    "売れています",
    "話題です",
    "絶対買い",
    "間違いなし",
    "迷ったらこれ",
    "コスパ最高",
    "ランキング上位",
    "プレゼントにもおすすめ",
    "レビュー件数が多いので安心",
    "評価が高いのでおすすめ",
    "満足理由",
    "商品ページ",
    "チェックしてください",
    "容量と価格だけでも確認",
    "口コミで好評",
    "レビューで人気",
    "購入者から評価されています",
    "口コミを見ると",
    "レビューでは",
    "口コミで",
]

BROAD_LOW_INTENT_TAGS = {
    "#子育て",
    "#育児",
    "#ママ",
    "#パパ",
    "#赤ちゃん",
    "#ベビー",
    "#便利",
    "#おすすめ",
    "#人気",
    "#楽天ROOM",
    "#楽天購入品",
}
BRAND_HASHTAG = "#とらパパ厳選"

APPEAL_CONSUMABLE = "consumable"
APPEAL_EDUCATIONAL = "educational"
APPEAL_SHOES = "shoes"
APPEAL_APPLIANCE = "appliance"
APPEAL_GIFT = "gift"
APPEAL_OUTING = "outing"
APPEAL_FEEDING = "feeding"
APPEAL_STORAGE = "storage"
APPEAL_DEFAULT = "default"

APPEAL_LABELS = {
    APPEAL_CONSUMABLE: "まとめ買い安心",
    APPEAL_EDUCATIONAL: "親子遊び",
    APPEAL_SHOES: "通園準備",
    APPEAL_APPLIANCE: "時短家電",
    APPEAL_GIFT: "実用ギフト",
    APPEAL_OUTING: "外出準備",
    APPEAL_FEEDING: "食事サポート",
    APPEAL_STORAGE: "片づけ対策",
    APPEAL_DEFAULT: "使う場面重視",
}

CATEGORY_TAGS = {
    "ベビー用消耗品": "#ベビー消耗品",
    "ベビー用品": "#ベビーグッズ",
    "キッズ用品": "#キッズ用品",
    "知育玩具": "#知育玩具",
    "おうち遊び": "#おうち遊び",
    "本": "#絵本",
    "家電": "#時短家電",
    "子ども靴": "#キッズシューズ",
    "プレゼント向き商品": "#ギフト候補",
    "外出グッズ": "#外出グッズ",
    "離乳食グッズ": "#離乳食グッズ",
    "収納": "#おもちゃ収納",
}

FOCUS_KEYWORDS = {
    "おしりふき": "おしりふき",
    "おむつ": "おむつ",
    "ティッシュ": "ティッシュ",
    "シート": "シート",
    "ミルク": "ミルク",
    "リング": "リング",
    "ブロック": "ブロック",
    "カメラ": "キッズカメラ",
    "積み木": "積み木",
    "木製": "木製玩具",
    "ベビーカー": "ベビーカーグッズ",
    "離乳食": "離乳食グッズ",
    "収納": "収納",
    "絵本": "絵本",
    "パズル": "パズル",
    "靴": "キッズシューズ",
    "シューズ": "キッズシューズ",
    "スニーカー": "スニーカー",
    "サンダル": "サンダル",
    "上履き": "上履き",
    "ブレンダー": "ブレンダー",
    "タイマー": "タイマー",
    "掃除機": "掃除機",
    "ギフト": "ギフト",
}


def build_post_text(scored: ScoredProduct) -> str:
    product = scored.product
    appeal = determine_appeal_category(product)
    title = build_title(product, appeal)

    body_sentences = [
        empathy_sentence(product, appeal),
        solution_sentence(product, appeal),
        review_differentiation_sentence(product, appeal),
        product_specific_sentence(product, appeal),
        review_signal_sentence(product, appeal),
        buy_now_sentence(product, appeal),
        recommendation_sentence(product, appeal),
    ]
    body = compress_body("\n".join(sentence for sentence in body_sentences if sentence), max_length=260)
    text = f"タイトル：\n{title}\n\n投稿文：\n{body}"
    return sanitize_post_text(text)


def build_hashtags(scored: ScoredProduct) -> str:
    product = scored.product
    appeal = determine_appeal_category(product)
    tags = [
        category_tag(product.category, product.text, appeal),
        purchase_intent_tag(appeal, product.text),
        problem_solving_tag(appeal),
        target_or_gift_tag(appeal, product.text),
        BRAND_HASHTAG,
    ]
    unique_tags: list[str] = []
    for tag in tags:
        if tag not in unique_tags and tag not in BROAD_LOW_INTENT_TAGS:
            unique_tags.append(tag)
    while len(unique_tags) < 5:
        unique_tags.insert(-1, "#買う前メモ")
        unique_tags = list(dict.fromkeys(unique_tags))
    return " ".join(unique_tags[:5])


def build_room_output(scored: ScoredProduct, *, product_page_checked: bool = False, review_text_available: bool = False) -> str:
    post_text = build_post_text(scored)
    product = scored.product
    appeal = determine_appeal_category(product)
    return (
        f"{post_text}\n\n"
        f"ハッシュタグ：\n{build_hashtags(scored)}\n\n"
        f"狙った感情：\n{psychology_note(product, appeal)}"
    )


def evidence_note(*, product_page_checked: bool, review_text_available: bool) -> str:
    sources = ["API情報"]
    if product_page_checked:
        sources.append("商品ページ情報")
    if review_text_available:
        sources.append("レビュー本文")
    else:
        sources.append("レビュー本文なし")
    return "・".join(sources)


def sanitize_post_text(text: str) -> str:
    sanitized = text
    for expression in BANNED_EXPRESSIONS:
        sanitized = sanitized.replace(expression, "")
    sanitized = re.sub(r"[ 　]+", " ", sanitized)
    sanitized = re.sub(r"\n{3,}", "\n\n", sanitized)
    sanitized = re.sub(r"([。！？])\1+", r"\1", sanitized)
    lines = [line.strip() for line in sanitized.splitlines()]
    return "\n".join(line for line in lines if line).strip()


def shorten_product_name(name: str, max_length: int = 28) -> str:
    cleaned = re.sub(r"[\[\]【】()（）]", " ", name)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if len(cleaned) <= max_length:
        return cleaned
    return cleaned[:max_length].rstrip() + "..."


def build_title(product: Product, appeal: str) -> str:
    options = {
        APPEAL_CONSUMABLE: [
            "気づくと無くなる育児必需品",
            "最後の1パックで焦りたくない",
            "夕方に買いに走りたくない",
        ],
        APPEAL_EDUCATIONAL: [
            "これなら親子で長く遊べる",
            "集中して遊べる時間を作る",
            "遊びながら成長を感じたい日",
        ],
        APPEAL_SHOES: [
            "朝の支度を止めない一足",
            "保育園用は履かせやすさ重視",
            "公園まで歩きたくなる足元",
        ],
        APPEAL_APPLIANCE: [
            "寝る前の家事を少し減らす",
            "共働き家庭の時短候補",
            "家事の負担を明日に残さない",
        ],
        APPEAL_GIFT: [
            "贈った後に使われる実用品",
            "気を使わせにくい育児ギフト",
            "出産祝いは実用性で選ぶ",
        ],
        APPEAL_OUTING: [
            "外出先で慌てたくない",
            "荷物が多い日の味方に",
            "子連れ外出の準備に",
        ],
        APPEAL_FEEDING: [
            "食後の片づけを軽くしたい",
            "自分で食べたい時期に",
            "離乳食準備を整えたい",
        ],
        APPEAL_STORAGE: [
            "床の散らかり対策に",
            "戻す場所を決めたい",
            "リビング整理を始めたい",
        ],
        APPEAL_DEFAULT: [
            "迷う時間を減らしたい育児用品",
            "忙しい家庭の候補に残る理由",
            "買う前に見ておきたい育児品",
        ],
    }
    title = choose_option(options.get(appeal, options[APPEAL_DEFAULT]), product.text)
    return title[:30]


def appeal_label(appeal: str) -> str:
    return APPEAL_LABELS.get(appeal, APPEAL_LABELS[APPEAL_DEFAULT])


def category_benefit(category: str) -> str:
    benefit_map = {
        "ベビー用消耗品": "まとめ買いしやすさ",
        "ベビー用品": "毎日の扱いやすさ",
        "キッズ用品": "動きやすさ",
        "知育玩具": "遊びの広がり",
        "おうち遊び": "家で過ごす時間の回しやすさ",
        "本": "読み聞かせの始めやすさ",
        "プレゼント向き商品": "贈ったあとに使われやすい実用性",
        "子ども靴": "履かせやすさ",
    }
    return benefit_map.get(category, "毎日の扱いやすさ")


def determine_appeal_category(product: Product) -> str:
    text = product.text
    if is_gift_candidate(product):
        return APPEAL_GIFT
    if any(word in text for word in ["おしりふき", "おむつ", "ティッシュ", "消耗", "詰め替え", "シート", "ミルク"]):
        return APPEAL_CONSUMABLE
    if any(word in text for word in ["靴", "シューズ", "スニーカー", "サンダル", "上履き", "保育園", "通園"]):
        return APPEAL_SHOES
    if any(word in text for word in ["ベビーカー", "抱っこ紐", "マザーズバッグ", "チャイルドシート", "外出", "旅行", "帰省", "扇風機"]):
        return APPEAL_OUTING
    if any(word in text for word in ["離乳食", "食器", "エプロン", "マグ", "フードカッター", "保存容器", "ベビーチェア"]):
        return APPEAL_FEEDING
    if any(word in text for word in ["収納", "絵本棚", "ラック", "ストッカー", "片づけ", "おもちゃ箱"]):
        return APPEAL_STORAGE
    if any(word in text for word in ["ブレンダー", "掃除機", "家電", "タイマー", "時短", "自動", "軽量"]):
        return APPEAL_APPLIANCE
    if any(word in text for word in ["知育", "リング", "ブロック", "絵本", "カメラ", "パズル", "おうち遊び"]):
        return APPEAL_EDUCATIONAL
    if product.category in {"知育玩具", "おうち遊び", "本"}:
        return APPEAL_EDUCATIONAL
    if product.category in {"子ども靴", "キッズ用品"}:
        return APPEAL_SHOES
    if product.category in {"ベビー用消耗品", "ベビー用品"}:
        return APPEAL_CONSUMABLE
    if product.category in {"プレゼント向き商品"}:
        return APPEAL_GIFT
    return APPEAL_DEFAULT


def build_benefit(product: Product, appeal: str) -> str:
    if appeal == APPEAL_CONSUMABLE:
        return "ストック切れを防ぎやすい"
    if appeal == APPEAL_EDUCATIONAL:
        return "親子で遊び方を広げられる"
    if appeal == APPEAL_SHOES:
        return "通園準備でも履かせやすい"
    if appeal == APPEAL_APPLIANCE:
        return "家事の手順を減らしやすい"
    if appeal == APPEAL_GIFT:
        return "贈ったあとに出番を作りやすい"
    if appeal == APPEAL_OUTING:
        return "外出前の準備を整えやすい"
    if appeal == APPEAL_FEEDING:
        return "食後の片づけを考えやすい"
    if appeal == APPEAL_STORAGE:
        return "戻す場所を決めやすい"
    return category_benefit(product.category)


def empathy_sentence(product: Product, appeal: str) -> str:
    focus = focus_phrase(product)
    if appeal == APPEAL_CONSUMABLE:
        if "ミルク" in product.text:
            return "ミルクって「まだある」と思っていたのに、最後の1缶を開けて焦ることありませんか。"
        return choose_option(
            [
                f"{focus}って「まだある」と思っていたのに、最後の1パックを開けて焦ることありませんか。",
                f"{focus}が足りないと気づくのは、だいたい忙しいタイミングです。",
                f"消耗が早い{focus}は、少し多めに置いておくだけで安心感が変わります。",
            ],
            product.text,
        )
    if appeal == APPEAL_EDUCATIONAL:
        return "雨の日や夕方、家遊びのネタがなくなって親子で時間を持て余すことありますよね。"
    if appeal == APPEAL_SHOES:
        return "朝の支度中に靴で止まると、登園前から一気に疲れますよね。"
    if appeal == APPEAL_APPLIANCE:
        return "共働きだと、寝かしつけ後の家事まで残る日が本当にきついですよね。"
    if appeal == APPEAL_GIFT:
        return "出産祝いって何を贈るか迷いますよね。"
    if appeal == APPEAL_OUTING:
        return choose_option(
            [
                "子連れ外出は、家を出る前から荷物の確認で時間を取られがちです。",
                "外出先で「あれがない」と気づくと、親も子どもも落ち着きにくいですよね。",
            ],
            product.text,
        )
    if appeal == APPEAL_FEEDING:
        return choose_option(
            [
                "食後の片づけが増える時期は、食事グッズ選びで差が出ますよね。",
                "自分で食べたい時期は、こぼす前提で準備しておきたいところです。",
            ],
            product.text,
        )
    if appeal == APPEAL_STORAGE:
        return choose_option(
            [
                "おもちゃや絵本は、戻す場所がないとすぐ床に広がりますよね。",
                "リビングに置く育児用品は、片づけやすさまで見て選びたいです。",
            ],
            product.text,
        )
    return f"{focus}は、買ってから出番があるかまで考えて選びたいですよね。"


def solution_sentence(product: Product, appeal: str) -> str:
    text = product.text
    if appeal == APPEAL_CONSUMABLE:
        if "ミルク" in text:
            return "毎日必要なものだから、ストックがあるだけで夜中や夕方のバタバタを減らしやすいです。"
        if "おむつ" in text:
            return "毎日替えるものだから、残り枚数を気にしながら過ごすのは地味にストレスなんですよね。"
        return "毎日使うものだから、切らしてしまうと意外と困るんですよね。"
    if appeal == APPEAL_EDUCATIONAL:
        if "リング" in text:
            return "通す、並べる、色分けする遊びなら、指先を使いながら集中する時間につながります。"
        if "ブロック" in text:
            return "作って壊してまた作る流れがあるので、親子で会話しながら遊びを伸ばせます。"
        if "絵本" in text:
            return "読み聞かせから会話につながるので、寝る前や休日の親子時間に回しやすいです。"
        return "遊び方を変えられるものは、年齢が上がっても出番を作りやすいです。"
    if appeal == APPEAL_SHOES:
        if any(word in text for word in ["面ファスナー", "マジックテープ"]):
            return "面ファスナーなら履かせやすさを確保しやすく、自分で履きたい時期にも合わせられます。"
        return "歩く時間が長い保育園や公園用なら、足入れと歩きやすさを優先したいところです。"
    if appeal == APPEAL_APPLIANCE:
        return "準備や片づけの工程が減ると、子どもが寝た後の自分の時間を少し戻しやすくなります。"
    if appeal == APPEAL_GIFT:
        return "育児ギフトは、かわいいものも素敵ですが、毎日使えるものだと相手の負担になりにくいです。"
    if appeal == APPEAL_OUTING:
        return "必要なものをすぐ出せる形なら、移動中や帰省前の小さな不安を減らせます。"
    if appeal == APPEAL_FEEDING:
        return "食べる前後の動きまで考えると、準備と片づけの手間を少し減らせます。"
    if appeal == APPEAL_STORAGE:
        return "戻す場所が決まると、親だけで片づけ続ける状態を減らしやすいです。"
    return "使う場面がはっきりしているものは、買ったあとにしまい込みにくいです。"


def review_differentiation_sentence(product: Product, appeal: str) -> str:
    text = product.text
    if "ring10" in text or ("リング" in text and "知育" in text):
        return "ただの知育玩具ではなく、年齢に合わせて遊び方を増やせるのが選ぶ理由です。"
    if "マグネット" in text and "ブロック" in text:
        return "平面遊びで終わらず、立体やコース作りまで広げられる点が他と違います。"
    if "カメラ" in text:
        return "撮って終わりではなく、見返して話す流れまで作れるのがこの商品の良さです。"
    if appeal == APPEAL_CONSUMABLE:
        if "ミルク" in text:
            return "まとめて置けるタイプなら、寝る前に残量を見てヒヤッとする回数を減らせそう。"
        return "まとめてストックできるタイプなら、買い足しを気にする回数を減らせそう。"
    if appeal == APPEAL_SHOES:
        return "見た目より、保育園や公園で何度も脱ぎ履きする場面まで想像できる点を重視したい一足です。"
    if appeal == APPEAL_APPLIANCE:
        return "性能だけでなく、出してすぐ使えるかまで見られると、結局使う回数が変わります。"
    if appeal == APPEAL_GIFT:
        if "おむつ" in text:
            return "おむつ系なら赤ちゃんの毎日に出番があり、サイズが合う時期にしっかり使ってもらいやすいです。"
        return "飾って終わるものより、受け取った家庭でそのまま出番がある理由を見たいです。"
    if appeal == APPEAL_OUTING:
        return "外で使うものは、デザインより先に持ち運びや取り出しやすさまで想像したいです。"
    if appeal == APPEAL_FEEDING:
        return "食事まわりは、食べている時だけでなく片づけるところまで含めて選びたいです。"
    if appeal == APPEAL_STORAGE:
        return "収納系は、入る量だけでなく家族が戻す場所を迷わないかまで見たいです。"
    return "商品名だけでなく、どの育児シーンで助かるかまで想像できるのが候補に残る理由です。"


def review_signal_sentence(product: Product, appeal: str) -> str:
    text = product.text
    if appeal == APPEAL_CONSUMABLE:
        if "ミルク" in text:
            return "子どもがお腹を空かせたタイミングで慌てず準備できるだけでも、親の気持ちに余裕が出ます◎"
        if "おむつ" in text:
            return "子どもを待たせず替えられて、親も残り枚数に追われにくくなるのが助かります。"
        return "夕方の忙しい時間に慌てて買いに行かなくて済むだけでも気持ちがラクになります◎"
    if appeal == APPEAL_EDUCATIONAL:
        return "子どもは手を動かして遊びに入りやすく、親も横で声をかけながら一緒に過ごせます。"
    if appeal == APPEAL_SHOES:
        return "子どもが自分で履きたい気持ちを邪魔しにくく、親も玄関で待つ時間を減らせます。"
    if appeal == APPEAL_APPLIANCE:
        return "子どもと向き合う時間を削らず、親の家事だけを少し軽くできるのが助かります。"
    if appeal == APPEAL_GIFT:
        return "赤ちゃんの毎日に出番があるものなら、贈られた側も置き場所や好みで悩みにくいです。"
    if appeal == APPEAL_OUTING:
        return "親は荷物を探す時間を減らせて、子どもを待たせる場面も少し減らせます。"
    if appeal == APPEAL_FEEDING:
        return "子どもの食べたい気持ちを見守りつつ、親の片づけ負担も考えられます。"
    if appeal == APPEAL_STORAGE:
        return "子どもにも見える場所を作れると、片づけに参加するきっかけになります。"
    return "確認したいのは、どの家庭のどんな場面で出番があるかです。"


def buy_now_sentence(product: Product, appeal: str) -> str:
    text = product.text
    if any(word in text for word in ["セール", "ポイント", "クーポン", "買い回り", "送料無料"]):
        return "セール中のうちに、容量と価格を確認しておきたいアイテムです。"
    if appeal == APPEAL_CONSUMABLE:
        return "切れてから探すと割高でも買いがちなので、余裕があるうちに候補へ入れておきたいです。"
    if appeal == APPEAL_SHOES:
        return "サイズ欠けが出る前に、園用と洗い替え候補を先に見ておくと動きやすいです。"
    if appeal == APPEAL_GIFT:
        return "贈る前に、内容量と相手の月齢が合うかだけ見ておくと選びやすいです。"
    if appeal == APPEAL_OUTING:
        return "外出が増える前に、使う場面とバッグ内の置き場所を想像して見ておきたいです。"
    if appeal == APPEAL_FEEDING:
        return "食事回数が増える前に、洗う手間や置き場所だけ先に比べておきたいです。"
    if appeal == APPEAL_STORAGE:
        return "置く場所と入れたい量が合うか、家に合わせて見ておきたい商品です。"
    return "必要になってから比較すると雑になりやすいので、今のうちに候補だけ見ておくと判断しやすいです。"


def recommendation_sentence(product: Product, appeal: str) -> str:
    focus = focus_phrase(product)
    if appeal == APPEAL_CONSUMABLE:
        if "ミルク" in product.text:
            return "ミルクの残量で焦りたくない家庭に向いています。"
        return "毎日使う消耗品を切らしたくない家庭に向いています。"
    if appeal == APPEAL_EDUCATIONAL:
        return "雨の日や休日の家遊びを親子で回したい家庭に向いています。"
    if appeal == APPEAL_SHOES:
        return "登園前のバタつきを減らしたい家庭に向いています。"
    if appeal == APPEAL_APPLIANCE:
        return "夜の家事を少しでも減らしたい共働き家庭に向いています。"
    if appeal == APPEAL_GIFT:
        return "見た目だけでなく、相手の育児で出番がある贈り物を選びたい家庭に向いています。"
    if appeal == APPEAL_OUTING:
        return "子連れ外出や帰省前に、準備の抜けを減らしたい家庭に向いています。"
    if appeal == APPEAL_FEEDING:
        return "食事の準備から片づけまで、少しでも回しやすくしたい家庭に向いています。"
    if appeal == APPEAL_STORAGE:
        return "散らかりやすい場所に、戻す定位置を作りたい家庭に向いています。"
    return f"{focus}の出番が具体的に浮かぶ家庭に向いています。"


def psychology_note(product: Product, appeal: str) -> str:
    if appeal == APPEAL_CONSUMABLE:
        return "共感・不安回避・お得感"
    if appeal == APPEAL_EDUCATIONAL:
        return "共感・安心感・購入後の未来"
    if appeal == APPEAL_SHOES:
        return "共感・時短・不安回避"
    if appeal == APPEAL_APPLIANCE:
        return "時短・負担軽減・睡眠時間確保"
    if appeal == APPEAL_GIFT:
        return "安心感・不安回避"
    return "共感・安心感"


def purchase_intent_tag(appeal: str, product_text: str) -> str:
    if appeal == APPEAL_CONSUMABLE:
        return "#まとめ買い候補"
    if appeal == APPEAL_EDUCATIONAL:
        return "#おうち遊び見直し"
    if appeal == APPEAL_SHOES:
        return "#保育園準備"
    if appeal == APPEAL_APPLIANCE:
        return "#時短したい日"
    if appeal == APPEAL_GIFT:
        return "#実用ギフト"
    if appeal == APPEAL_OUTING:
        return "#外出準備"
    if appeal == APPEAL_FEEDING:
        return "#買い足し候補"
    if appeal == APPEAL_STORAGE:
        return "#片づけ対策"
    return "#購入前チェック"


def category_tag(category: str, product_text: str, appeal: str) -> str:
    if "おしりふき" in product_text:
        return "#おしりふき"
    if "おむつ" in product_text:
        return "#おむつ"
    if "絵本" in product_text:
        return "#絵本"
    if "リング" in product_text:
        return "#知育リング"
    if "ブロック" in product_text:
        return "#ブロック遊び"
    if "カメラ" in product_text:
        return "#キッズカメラ"
    if "上履き" in product_text:
        return "#上履き"
    if "シューズ" in product_text or "靴" in product_text:
        return "#キッズシューズ"
    if "ブレンダー" in product_text:
        return "#ブレンダー"
    if "離乳食" in product_text:
        return "#離乳食グッズ"
    if "ベビーチェア" in product_text:
        return "#ベビーチェア"
    if "ベビーカー" in product_text:
        return "#ベビーカーグッズ"
    if "収納" in product_text or "ラック" in product_text:
        return "#おもちゃ収納"
    if "積み木" in product_text:
        return "#積み木"
    return CATEGORY_TAGS.get(category, "#育児グッズ選び")


def problem_solving_tag(appeal: str) -> str:
    if appeal == APPEAL_CONSUMABLE:
        return "#買い忘れ防止"
    if appeal == APPEAL_EDUCATIONAL:
        return "#親子遊び"
    if appeal == APPEAL_SHOES:
        return "#履かせやすい"
    if appeal == APPEAL_APPLIANCE:
        return "#家事負担を軽く"
    if appeal == APPEAL_GIFT:
        return "#贈って使える"
    if appeal == APPEAL_OUTING:
        return "#荷物整理"
    if appeal == APPEAL_FEEDING:
        return "#食べこぼし対策"
    if appeal == APPEAL_STORAGE:
        return "#リビング整理"
    return "#育児グッズ選び"


def target_or_gift_tag(appeal: str, product_text: str) -> str:
    if appeal == APPEAL_GIFT:
        if "出産" in product_text:
            return "#出産祝い"
        return "#ギフト候補"
    if "1歳" in product_text or "1才" in product_text:
        return "#1歳向け"
    if "2歳" in product_text or "2才" in product_text:
        return "#2歳向け"
    if "3歳" in product_text or "3才" in product_text:
        return "#3歳向け"
    if "保育園" in product_text:
        return "#保育園用"
    if appeal == APPEAL_CONSUMABLE:
        return "#ストック管理"
    if appeal == APPEAL_EDUCATIONAL:
        return "#雨の日遊び"
    if appeal == APPEAL_SHOES:
        return "#通園準備"
    if appeal == APPEAL_APPLIANCE:
        return "#家事時短"
    if appeal == APPEAL_OUTING:
        return "#子連れ外出"
    if appeal == APPEAL_FEEDING:
        return "#離乳食準備"
    if appeal == APPEAL_STORAGE:
        return "#子ども部屋準備"
    return "#買う前メモ"


def focus_phrase(product: Product) -> str:
    text = product.text
    for keyword, label in FOCUS_KEYWORDS.items():
        if keyword in text:
            return label
    return shorten_product_name(product.name, max_length=14)


def product_anchor(product: Product) -> str:
    return shorten_product_name(product.name, max_length=22)


def product_specific_sentence(product: Product, appeal: str) -> str:
    anchor = product_anchor(product)
    focus = focus_phrase(product)
    checkpoints = purchase_checkpoints(product, appeal)
    if appeal == APPEAL_CONSUMABLE:
        return f"{anchor}は、{focus}を切らした時の焦りを減らしたい家庭向き。買う前は{checkpoints}を見ておくと、ストック候補にしやすいです。"
    if appeal == APPEAL_EDUCATIONAL:
        return f"{anchor}は、ただ時間をつぶすより親子で会話が増える候補。買う前は{checkpoints}を見ると、年齢に合うか判断しやすいです。"
    if appeal == APPEAL_SHOES:
        return f"{anchor}は、玄関で止まりがちな朝を短くしたい家庭向き。買う前は{checkpoints}を見て、園用に回せるか確認したいです。"
    if appeal == APPEAL_APPLIANCE:
        return f"{anchor}は、家事の手間をひとつ減らしたい日に候補になります。買う前は{checkpoints}を見て、出しっぱなしで使えるか考えたいです。"
    if appeal == APPEAL_GIFT:
        return f"{anchor}は、贈った後にしまわれにくい実用寄りの候補。買う前は{checkpoints}を見て、相手の月齢や生活に合うか確認したいです。"
    if appeal == APPEAL_OUTING:
        return f"{anchor}は、外出中に必要なものを探す時間を減らしたい家庭向き。買う前は{checkpoints}を見て、バッグやベビーカーに合うか考えたいです。"
    if appeal == APPEAL_FEEDING:
        return f"{anchor}は、食後の片づけまで含めて整えたい家庭向き。買う前は{checkpoints}を見て、食卓や外出先で使えるか考えたいです。"
    if appeal == APPEAL_STORAGE:
        return f"{anchor}は、散らかるものの戻し場所を決めたい家庭向き。買う前は{checkpoints}を見て、リビングに置けるか考えたいです。"
    return f"{anchor}は、{focus}の出番が生活の中で浮かぶ家庭向き。買う前は{checkpoints}を見ると判断しやすいです。"


def purchase_checkpoints(product: Product, appeal: str) -> str:
    checks: list[str] = []
    text = product.text
    if any(word in text for word in ["サイズ", "cm", "大きさ", "幅", "高さ"]) or appeal == APPEAL_SHOES:
        checks.append("サイズ")
    if any(word in text for word in ["1歳", "2歳", "3歳", "対象年齢", "年齢"]):
        checks.append("対象年齢")
    if any(word in text for word in ["収納", "折りたたみ", "軽量", "置き場所"]):
        checks.append("収納しやすさ")
    if appeal == APPEAL_OUTING or any(word in text for word in ["ベビーカー", "バッグ", "外出", "旅行", "帰省"]):
        checks.append("持ち運び")
    if appeal == APPEAL_FEEDING or any(word in text for word in ["洗", "食器", "エプロン", "マグ", "保存容器"]):
        checks.append("洗う手間")
    if appeal == APPEAL_STORAGE or any(word in text for word in ["収納", "ラック", "ストッカー", "絵本棚"]):
        checks.append("置き場所")
    if any(word in text for word in ["電池", "充電", "コードレス", "コード"]):
        checks.append("電源まわり")
    if any(word in text for word in ["面ファスナー", "マジックテープ", "滑り", "つま先", "ソール"]):
        checks.append("履き心地")
    if any(word in text for word in ["配送", "送料無料", "まとめ買い", "セット"]):
        checks.append("購入単位")
    if not checks:
        checks.append("使う場面")
    return "・".join(dict.fromkeys(checks))


def purchase_check_sentence(product: Product, appeal: str) -> str:
    checkpoints = purchase_checkpoints(product, appeal)
    if should_include_purchase_checkpoints(product, appeal):
        return f"購入前は{checkpoints}も見ておくと選びやすいです。"
    return ""


def should_include_purchase_checkpoints(product: Product, appeal: str) -> bool:
    if appeal in {APPEAL_SHOES, APPEAL_APPLIANCE, APPEAL_GIFT}:
        return True
    return any(word in product.text for word in ["対象年齢", "サイズ", "収納", "電池", "セット"])


def age_phrase(product_text: str) -> str:
    for age in ["1歳", "2歳", "3歳", "4歳", "5歳"]:
        if age in product_text:
            return age
    for age in ["1才", "2才", "3才", "4才", "5才"]:
        if age in product_text:
            return age.replace("才", "歳")
    return "2〜3歳"


def child_fit_sentence(product: Product, appeal: str) -> str:
    age = age_phrase(product.text)
    if appeal == APPEAL_EDUCATIONAL:
        return f"{age}ごろから遊び方を増やしやすい流れを作りやすいです。"
    if appeal == APPEAL_SHOES:
        return f"{age}前後の毎日使いでも回しやすさを見やすいです。"
    return f"{age}前後の使い方もイメージしやすいです。"


def choice_reason_sentence(product: Product, appeal: str) -> str:
    return review_differentiation_sentence(product, appeal)


def child_benefit_sentence(product: Product, appeal: str, benefit: str) -> str:
    if appeal == APPEAL_EDUCATIONAL:
        return f"子どもにとっては、{benefit}のが続けやすさにつながります。"
    return f"{benefit}のが毎日の負担を増やしにくいです。"


def parent_benefit_sentence(product: Product, appeal: str) -> str:
    if appeal == APPEAL_CONSUMABLE:
        return "在庫切れの不安を減らしやすい"
    if appeal == APPEAL_EDUCATIONAL:
        return "親子時間を作りやすい"
    if appeal == APPEAL_SHOES:
        return "朝の支度を短くしやすい"
    if appeal == APPEAL_APPLIANCE:
        return "家事の段取りを軽くしやすい"
    if appeal == APPEAL_GIFT:
        return "相手に気を使わせにくい"
    return "日常に入れやすい"


def is_gift_candidate(product: Product) -> bool:
    text = product.text
    has_gift_word = any(word in text for word in ["ギフト", "プレゼント", "出産祝い", "誕生日"])
    has_practical_reason = any(
        word in text
        for word in ["おむつ", "タオル", "食器", "ブランケット", "実用", "すぐ使える", "セット"]
    )
    return has_gift_word and has_practical_reason


def choose_option(options: list[str], text: str) -> str:
    index = sum(ord(char) for char in text[:20]) % len(options)
    return options[index]


def compress_body(body: str, *, max_length: int) -> str:
    sentences = [sentence.strip() for sentence in re.split(r"(?<=。)", body) if sentence.strip()]
    compressed = ""
    for sentence in sentences:
        candidate = f"{compressed}{sentence}" if compressed else sentence
        if len(candidate) > max_length and compressed:
            break
        compressed = candidate
    return compressed[:max_length].rstrip()
