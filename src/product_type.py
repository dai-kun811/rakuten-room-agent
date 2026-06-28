from __future__ import annotations

import re

from rakuten_api import Product

APPEAL_CONSUMABLE = "consumable"
APPEAL_EDUCATIONAL = "educational"
APPEAL_KIDS_CAMERA = "kids_camera"
APPEAL_SLEEP = "sleep"
APPEAL_SHOES = "shoes"
APPEAL_APPLIANCE = "appliance"
APPEAL_GIFT = "gift"
APPEAL_OUTING = "outing"
APPEAL_FEEDING = "feeding"
APPEAL_STORAGE = "storage"
APPEAL_BATH = "bath"
APPEAL_DEFAULT = "default"

ROOM_PRODUCT_TYPE_KEYWORDS = {
    "wipes": ["おしりふき", "手口ふき", "手口拭き"],
    "swaddle": ["おくるみ", "スワドル", "モロー反射", "ねくるみ"],
    "nursing_support": ["授乳サポート", "ハンズフリー授乳", "授乳クッション", "ミルクサポート", "哺乳瓶ホルダー", "おやすみたまご", "Cカーブ", "C字", "授乳用品"],
    "baby_bedding": ["抱っこ布団", "ねんねクッション", "ベビー布団", "背中スイッチ対策", "寝かしつけクッション"],
    "baby_care": ["保湿", "ベビーローション", "ベビークリーム", "ワセリン", "爪切り", "鼻吸い", "鼻水吸引", "体温計", "ケア用品"],
    "baby_sleep": ["スリーパー", "ガーゼケット", "ベビーケット", "ナイトライト", "ベビーベッド", "寝具", "寝冷え"],
    "soothing_plush": ["寝かしつけぬいぐるみ", "プラネタリウム付きぬいぐるみ", "ぬいぐるみ", "プラネタリウム", "オルゴール", "メロディー", "心音", "投影"],
    "diaper": ["紙おむつ", "紙オムツ", "おむつ", "オムツ", "パンツタイプ", "テープタイプ", "新生児用おむつ", "おむつ替え", "おむつポーチ", "おむつストッカー", "おむつ替えシート"],
    "formula": ["粉ミルク", "液体ミルク", "フォローアップミルク"],
    "sound_blocks": ["音が鳴る積み木", "音の鳴る積み木", "音入り積み木"],
    "magnetic_blocks": ["マグネットブロック", "磁石ブロック", "磁気ブロック", "マグビルド", "マグネット"],
    "baby_walker_toy": ["手押し車", "ファーストウォーカー", "ベビーウォーカー", "押し車", "カタカタ", "つかまり立ち", "歩行練習"],
    "activity_cube": ["アクティビティキューブ", "ルーピング", "型はめ"],
    "ring_toy": ["リングテン", "ring10", "リング玩具", "紐通し"],
    "kids_camera": ["キッズカメラ", "子ども用カメラ"],
    "sleep_light": ["ホワイトノイズ", "授乳ライト", "寝かしつけライト"],
    "stroller_storage": ["ベビーカーバッグ", "ベビーカー用バッグ", "ベビーカー収納"],
    "wooden_blocks": ["木製積み木", "木の積み木", "積み木", "つみき", "ウッドブロック", "スタッキングブロック"],
}

DIAPER_RELATED_ACCESSORIES = [
    "おむつポーチ",
    "オムツポーチ",
    "おむつストッカー",
    "オムツストッカー",
    "おむつ替えシート",
    "オムツ替えシート",
]

ROOM_PRODUCT_TYPE_PRIORITY = [
    "swaddle",
    "nursing_support",
    "baby_care",
    "baby_sleep",
    "baby_bedding",
    "soothing_plush",
    "wipes",
    "formula",
    "diaper",
    "sound_blocks",
    "magnetic_blocks",
    "baby_walker_toy",
    "activity_cube",
    "ring_toy",
    "kids_camera",
    "sleep_light",
    "stroller_storage",
    "wooden_blocks",
]

EDUCATIONAL_KEYWORDS = [
    "積み木",
    "つみき",
    "ブロック",
    "レゴ",
    "デュプロ",
    "パズル",
    "知育玩具",
    "知育",
    "木のおもちゃ",
    "木製玩具",
    "型はめ",
    "ルーピング",
    "紐通し",
    "リングテン",
    "ring10",
    "リング",
    "アクティビティキューブ",
]

KIDS_CAMERA_KEYWORDS = [
    "キッズカメラ",
    "カメラ",
    "写真",
    "撮影",
    "スマホ転送",
    "sdカード",
    "sd",
    "ゲームなし",
]

SLEEP_KEYWORDS = [
    "ホワイトノイズ",
    "寝かしつけ",
    "授乳ライト",
    "夜泣き",
    "胎内音",
    "睡眠",
    "安眠",
    "スピーカー",
    "録音機能",
]


def contains_any(text: str, words: list[str]) -> bool:
    return any(word.lower() in text for word in words)


def classify_room_product_type(product: Product) -> str:
    text = product.identity_text
    if contains_any(text, ROOM_PRODUCT_TYPE_KEYWORDS["sleep_light"]) and not contains_any(
        text, ROOM_PRODUCT_TYPE_KEYWORDS["soothing_plush"]
    ):
        return "sleep_light"
    if contains_any(text, ["ゴミ箱", "ごみ箱", "ダストボックス", "おむつ入れ", "オムツ入れ"]):
        return "unknown"
    if "ベビーカー" in text and ("バッグ" in text or "収納" in text):
        return "stroller_storage"
    if ("積み木" in text or "つみき" in text) and (
        "音が鳴る" in text or "音の鳴る" in text or "音入り" in text
    ):
        return "sound_blocks"
    if contains_any(text, DIAPER_RELATED_ACCESSORIES):
        return "diaper"
    for product_type in ROOM_PRODUCT_TYPE_PRIORITY:
        if contains_any(text, ROOM_PRODUCT_TYPE_KEYWORDS[product_type]):
            return product_type
    return "unknown"


def room_product_label(product: Product, product_type: str | None = None) -> str:
    product_type = product_type or classify_room_product_type(product)
    text = product.identity_text
    if product_type == "wipes":
        return "手口ふき" if contains_any(text, ["手口ふき", "手口拭き"]) else "おしりふき"
    return {
        "diaper": "おむつ替えシート" if "おむつ替えシート" in text else "おむつポーチ" if "おむつポーチ" in text else "おむつストッカー" if "おむつストッカー" in text else "紙おむつ",
        "formula": "粉ミルク" if "粉ミルク" in text else "液体ミルク" if "液体ミルク" in text else "ミルク",
        "swaddle": "スワドル" if "スワドル" in text else "おくるみ",
        "nursing_support": "ハンズフリー授乳サポート" if "ハンズフリー" in text else "授乳サポート",
        "baby_care": "ベビー保湿剤" if "保湿" in text or "ローション" in text or "クリーム" in text else "ベビー爪切り" if "爪" in text else "鼻吸い器用ノズル" if "鼻" in text and "ノズル" in text else "鼻吸い器" if "鼻" in text else "ベビー体温計" if "体温計" in text else "ベビーケア用品",
        "baby_sleep": "スリーパー" if "スリーパー" in text else "ガーゼケット" if "ガーゼケット" in text else "ナイトライト" if "ナイトライト" in text else "ベビー寝具",
        "baby_bedding": "抱っこ布団" if "抱っこ布団" in text else "ねんねクッション" if "ねんねクッション" in text else "ベビー布団",
        "soothing_plush": "プラネタリウムぬいぐるみ" if "プラネタリウム" in text else "寝かしつけぬいぐるみ",
        "sound_blocks": "音が鳴る積み木",
        "baby_walker_toy": "手押し車",
        "wooden_blocks": "木製積み木",
        "magnetic_blocks": "マグネットブロック",
        "activity_cube": "アクティビティキューブ",
        "ring_toy": "リング玩具",
        "kids_camera": "キッズカメラ",
        "sleep_light": "ホワイトノイズ付きライト" if "ホワイトノイズ" in text else "授乳ライト",
        "stroller_storage": "ベビーカーバッグ",
    }.get(product_type, "")


def classify_product_type(product: Product) -> str:
    text = product.text

    if contains_any(text, SLEEP_KEYWORDS):
        return APPEAL_SLEEP
    if contains_any(text, KIDS_CAMERA_KEYWORDS):
        return APPEAL_KIDS_CAMERA
    if contains_any(text, EDUCATIONAL_KEYWORDS):
        return APPEAL_EDUCATIONAL
    if contains_any(text, ["お風呂", "沐浴", "バスチェア", "バスマット", "湯上がり", "ワンオペ入浴"]):
        return APPEAL_BATH
    if contains_any(text, ["ベビーカー", "抱っこ紐", "マザーズバッグ", "チャイルドシート", "外出", "旅行", "帰省"]):
        return APPEAL_OUTING
    if is_gift_candidate(product):
        return APPEAL_GIFT
    if contains_any(text, ["おしりふき", "おむつ", "ティッシュ", "消耗", "詰め替え", "シート", "ミルク"]):
        return APPEAL_CONSUMABLE
    if contains_any(text, ["靴", "シューズ", "スニーカー", "サンダル", "上履き", "保育園", "通園"]):
        return APPEAL_SHOES
    if contains_any(text, ["離乳食", "食器", "エプロン", "マグ", "フードカッター", "保存容器", "ベビーチェア"]):
        return APPEAL_FEEDING
    if contains_any(text, ["収納", "絵本棚", "ラック", "ストッカー", "片づけ", "おもちゃ箱"]):
        return APPEAL_STORAGE
    if contains_any(text, ["ブレンダー", "掃除機", "家電", "タイマー", "時短", "自動"]):
        return APPEAL_APPLIANCE
    if product.category in {"知育玩具", "おうち遊び", "本"}:
        return APPEAL_EDUCATIONAL
    if product.category in {"子ども靴", "キッズ用品"}:
        return APPEAL_SHOES
    if product.category in {"ベビー用消耗品"}:
        return APPEAL_CONSUMABLE
    if product.category in {"ベビー用品"}:
        return APPEAL_DEFAULT
    return APPEAL_DEFAULT


def is_gift_candidate(product: Product) -> bool:
    text = product.text
    has_gift_word = contains_any(text, ["ギフト", "プレゼント", "出産祝い", "誕生日"])
    has_practical_reason = contains_any(
        text,
        ["おむつ", "タオル", "食器", "ブランケット", "実用", "すぐ使える", "セット"],
    )
    return has_gift_word and has_practical_reason


def product_display_name(product: Product, product_type: str | None = None) -> str:
    text = product.text
    if product_type is None:
        product_type = classify_product_type(product)

    if product_type == APPEAL_KIDS_CAMERA:
        if "ゲームなし" in text:
            return "ゲームなしのキッズカメラ"
        if "スマホ転送" in text:
            return "スマホ転送できるキッズカメラ"
        return "キッズカメラ"
    if product_type == APPEAL_SLEEP:
        if "授乳ライト" in text and "ホワイトノイズ" in text:
            return "ホワイトノイズ付きの寝かしつけライト"
        if "授乳ライト" in text:
            return "授乳ライト"
        return "ホワイトノイズマシン"
    if product_type == APPEAL_EDUCATIONAL:
        if "アクティビティキューブ" in text:
            if "名入れ" in text:
                return "名入れ対応のアクティビティキューブ"
            return "アクティビティキューブ"
        if "紐通し" in text or "リングテン" in text or "ring10" in text or "リング" in text:
            return "遊び方を変えられるリング玩具"
        if "積み木" in text or "つみき" in text:
            if "音" in text and ("木製" in text or "木のおもちゃ" in text):
                return "音が鳴る木製つみき"
            return "木製つみき"
        if "木のおもちゃ" in text or "木製" in text:
            return "木のおもちゃ"
        if "ブロック" in text:
            if "マグネット" in text or "磁石" in text:
                return "マグネット知育ブロック"
            return "形を変えて遊べるブロック"
        return "知育玩具"
    if product_type == APPEAL_CONSUMABLE:
        for keyword in ["夜用おむつ", "おむつ", "おしりふき", "粉ミルク", "ミルク", "ティッシュ"]:
            if keyword in text:
                return keyword

    cleaned = strip_promotional_claims(product.name)
    cleaned = re.sub(r"[\[\]【】()（）]", " ", cleaned)
    cleaned = re.sub(r"[★☆◆◇■□●○◎※♪]", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if not cleaned:
        return "この商品"
    if len(cleaned) <= 18:
        return cleaned
    return keyword_display_name(cleaned, product_type)


def strip_promotional_claims(text: str) -> str:
    cleaned = text
    for pattern in [
        r"口コミ\s*[\d,]+\s*件",
        r"レビュー\s*[\d,]+\s*件",
        r"楽天\s*1位",
        r"\d+\s*冠",
        r"No\.?\s*1",
        r"ランキング\s*(?:1位|上位)",
    ]:
        cleaned = re.sub(pattern, " ", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"(?:高評価|売れている|大人気|人気)", " ", cleaned)
    return re.sub(r"\s+", " ", cleaned).strip()


def keyword_display_name(name: str, product_type: str) -> str:
    if product_type == APPEAL_CONSUMABLE:
        for keyword in ["夜用おむつ", "おむつ", "おしりふき", "粉ミルク", "ミルク", "ティッシュ"]:
            if keyword in name:
                return keyword
    if product_type == APPEAL_SHOES:
        if "上履き" in name:
            return "園用の上履き"
        if "シューズ" in name or "靴" in name:
            return "キッズシューズ"
    if product_type == APPEAL_OUTING:
        if "ベビーカー" in name and "バッグ" in name:
            return "ベビーカー用バッグ"
        if "ベビーカー" in name:
            return "ベビーカーグッズ"
    if product_type == APPEAL_FEEDING:
        if "エプロン" in name:
            return "離乳食エプロン"
        if "ベビーチェア" in name:
            return "ベビーチェア"
        return "離乳食グッズ"
    if product_type == APPEAL_STORAGE:
        if "ラック" in name:
            return "おもちゃ収納ラック"
        return "おもちゃ収納"
    if product_type == APPEAL_BATH:
        if "バスチェア" in name:
            return "ベビーバスチェア"
        if "バスマット" in name:
            return "ベビーバスマット"
        return "お風呂グッズ"
    return name[:18].rstrip()


def purchase_checkpoints(product: Product, product_type: str | None = None) -> str:
    text = product.text
    if product_type is None:
        product_type = classify_product_type(product)
    checks: list[str] = []

    if product_type == APPEAL_EDUCATIONAL:
        if "アクティビティキューブ" in text:
            return "対象年齢・サイズ・置き場所"
        if contains_any(text, ["紐通し", "リングテン", "ring10", "リング"]):
            return "対象年齢・パーツ数・収納場所"
        return "対象年齢・パーツの大きさ・収納場所"

    if product_type == APPEAL_KIDS_CAMERA:
        checks.append("対象年齢")
        if contains_any(text, ["サイズ", "小型"]):
            checks.append("サイズ")
        if contains_any(text, ["軽量", "重さ", "g"]):
            checks.append("重さ")
        checks.append("充電方式")
        if contains_any(text, ["sd", "sdカード"]):
            checks.append("SDカード容量")
        if contains_any(text, ["スマホ転送", "転送"]):
            checks.append("スマホ転送の方法")
        if contains_any(text, ["ゲームなし", "ゲーム"]):
            checks.append("ゲーム機能の有無")
        if "保証" in text:
            checks.append("保証内容")
        preferred = ["対象年齢"]
        preferred.append("スマホ転送の方法" if "スマホ転送の方法" in checks else "SDカード容量")
        preferred.append("充電方式")
        return "・".join(dict.fromkeys(preferred[:3]))

    if product_type == APPEAL_SLEEP:
        checks.extend(["音の種類", "音量調整"])
        if contains_any(text, ["ライト", "授乳ライト"]):
            checks.append("ライト機能")
        if contains_any(text, ["電源", "usb", "充電", "コードレス"]):
            checks.append("電源方式")
        else:
            checks.append("設置場所")
        if contains_any(text, ["持ち運び", "携帯", "小型"]):
            checks.append("持ち運びやすさ")
        if contains_any(text, ["サイズ", "cm"]):
            checks.append("サイズ")
        preferred = ["音量調整"]
        if "ライト機能" in checks:
            preferred.append("ライト機能")
        preferred.append("電源方式" if "電源方式" in checks else "設置場所")
        return "・".join(dict.fromkeys(preferred[:3]))

    if product_type == APPEAL_CONSUMABLE:
        return "容量・価格・置き場所"

    if product_type == APPEAL_SHOES:
        checks.append("サイズ")
        if contains_any(text, ["1歳", "2歳", "3歳", "対象年齢", "年齢"]):
            checks.append("対象年齢")
        checks.append("履き心地")
        return "・".join(dict.fromkeys(checks))

    if product_type == APPEAL_OUTING:
        checks.append("持ち運び")
        if contains_any(text, ["収納", "バッグ", "置き場所"]):
            checks.append("収納しやすさ")
        checks.append("取り出しやすさ")
        return "・".join(dict.fromkeys(checks))

    if product_type == APPEAL_FEEDING:
        checks.append("洗う手間")
        if contains_any(text, ["収納", "置き場所"]):
            checks.append("置き場所")
        checks.append("食卓での使い方")
        return "・".join(dict.fromkeys(checks))

    if product_type == APPEAL_STORAGE:
        checks.append("置き場所")
        checks.append("収納量")
        checks.append("子どもの戻しやすさ")
        return "・".join(dict.fromkeys(checks))

    if product_type == APPEAL_BATH:
        return "対象月齢・設置場所・手入れ"

    if product_type == APPEAL_APPLIANCE:
        checks.append("電源方式")
        checks.append("置き場所")
        checks.append("手入れ")
        return "・".join(dict.fromkeys(checks))

    if product_type == APPEAL_GIFT:
        checks.append("対象年齢")
        checks.append("内容量")
        checks.append("ギフト包装")
        return "・".join(dict.fromkeys(checks))

    return "使う場面"
