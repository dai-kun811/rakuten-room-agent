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
APPEAL_DEFAULT = "default"

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


def classify_product_type(product: Product) -> str:
    text = product.text

    if contains_any(text, SLEEP_KEYWORDS):
        return APPEAL_SLEEP
    if contains_any(text, KIDS_CAMERA_KEYWORDS):
        return APPEAL_KIDS_CAMERA
    if contains_any(text, EDUCATIONAL_KEYWORDS):
        return APPEAL_EDUCATIONAL
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
            return "知育ブロック"
        return "知育玩具"

    cleaned = re.sub(r"[\[\]【】()（）]", " ", product.name)
    cleaned = re.sub(r"[★☆◆◇■□●○◎※♪]", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if not cleaned:
        return "この商品"
    if len(cleaned) <= 18:
        return cleaned
    return keyword_display_name(cleaned, product_type)


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
    return name[:18].rstrip()


def purchase_checkpoints(product: Product, product_type: str | None = None) -> str:
    text = product.text
    if product_type is None:
        product_type = classify_product_type(product)
    checks: list[str] = []

    if product_type == APPEAL_EDUCATIONAL:
        checks.append("対象年齢")
        if contains_any(text, ["大きめ", "パーツ", "誤飲", "サイズ", "cm", "型はめ"]):
            checks.append("パーツの大きさ")
        else:
            checks.append("遊ぶスペース")
        if contains_any(text, ["収納", "箱", "ケース"]):
            checks.append("収納場所")
        if "名入れ" in text:
            checks.append("名入れ対応")
        if contains_any(text, ["ギフト", "プレゼント", "誕生日", "出産祝い"]):
            checks.append("ギフト包装")
        if contains_any(text, ["電池", "充電", "ライト", "音"]):
            checks.append("電池の有無")
        if "収納場所" not in checks:
            checks.append("収納場所")
        return "・".join(dict.fromkeys(checks[:4]))

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
        return "・".join(dict.fromkeys(checks[:4]))

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
        return "・".join(dict.fromkeys(checks[:4]))

    if product_type == APPEAL_CONSUMABLE:
        if contains_any(text, ["セット", "まとめ買い", "大容量", "配送", "送料無料"]):
            checks.append("購入単位")
        checks.append("容量")
        checks.append("価格")
        return "・".join(dict.fromkeys(checks[:3]))

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
