from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Iterable

from product_type import classify_room_product_type
from rakuten_api import Product
from scoring import ScoredProduct

GENERATION_MODE = "fallback"
MAX_GENERATION_ATTEMPTS = 64
DISTINCTIVE_REWRITE_START = 8
BRAND_TAG = "#とらパパ厳選"

NOISE_PATTERNS = [
    r"【[^】]*】",
    r"\[[^\]]*\]",
    r"[◆☆★]",
    r"!{2,}",
    r"！{2,}",
    r"楽天\s*1位",
    r"ランキング\s*(?:\d+位|上位)?",
    r"\d+\s*冠",
    r"No\.?\s*1",
    r"レビュー\s*[\d,]*\s*件",
    r"口コミ\s*[\d,]*\s*件",
    r"高評価",
    r"芸能人愛用",
    r"専門家推薦",
    r"管理栄養士推薦",
    r"送料無料",
    r"セール",
    r"ポイント\s*\d+\s*倍",
    r"\d{4}[./年-]\d{1,2}[./月-]\d{1,2}日?",
]

BANNED_EXPRESSIONS = [
    "おすすめです",
    "人気です",
    "楽天1位",
    "口コミ",
    "レビュー",
    "ランキング",
    "芸能人愛用",
    "専門家推薦",
    "管理栄養士推薦",
    "安全に遊べる",
    "安全な睡眠場所",
    "確実に",
    "万能",
    "治る",
    "防げる",
    "誰でも",
    "これ一つで完璧",
    "必ず喜ばれる",
    "必ず寝る",
    "泣き止む",
    "絶対",
    "間違いなし",
    "神アイテム",
    "遊びの特徴が伝わるギフト感",
    "一緒に繰り返し次の遊び方を考える時間",
    "親子で相談する",
    "水分量の記載がある",
    "声をかけやすい内容",
    "場面へ遊びを広げる",
    "毎晩の準備を繰り返しやすい",
    "毎晩の準備",
    "コードレスの機器",
    "一度見せ合う時間",
    "子どもが扱える大きさか考えたい",
]

NURSING_SUPPORT_UNSAFE_TERMS = [
    "赤ちゃんを寝かせる場所",
    "一時的に寝かせる場所",
    "寝床",
    "睡眠場所",
    "ベッド代わり",
    "置くだけで寝る",
    "吐き戻し防止",
    "絶壁防止",
    "安眠",
    "夜泣き改善",
    "赤ちゃんを置く場面",
    "赤ちゃんを一時的に寝かせる",
    "寝かせる場所を作る",
]

SOOTHING_PLUSH_UNSAFE_TERMS = [
    "枕元",
    "ベッド内",
    "布団の中",
    "添い寝",
    "そばに置いたまま眠る",
]

CONFIRMATION_REPEAT_MARKERS = [
    "確認",
    "見る",
    "見て",
    "見たい",
    "見比べ",
    "先に",
    "考え",
    "比べ",
]

QUANTITY_MENTION_PATTERN = re.compile(
    r"\d+(?:\.\d+)?\s*(?:個セット|枚|個|本|缶|袋|箱|ピース|パーツ|ポケット|ml|mL|g|kg)",
    flags=re.IGNORECASE,
)

BANNED_INTENTION_PHRASES = [
    "考えたいです",
    "決めたいです",
    "選びたいです",
    "見ておきたいです",
    "確認したいです",
    "判断したいです",
    "候補へ入れておきたいです",
    "比べたいです",
    "確かめたいです",
    "読んでおきたいです",
    "押さえたいです",
    "把握しておきたいです",
]
WEAK_ROOM_COPY_PHRASES = [
    "確認したい",
    "確認しておきたい",
    "確かめておきたい",
    "チェックしたい",
    "見ておきたい",
    "比べたい",
    "比較したい",
    "検討したい",
    "判断したい",
    "選びたい",
    "時間を作れ",
    "動きを試しやすく",
    "使い分けられます",
    "変えられます",
    "続けやすいです",
    "同じ道具でも",
]
MARKETING_REQUIRED_TYPES = {"nursing_support", "swaddle", "baby_bedding", "baby_care", "baby_sleep", "soothing_plush", "baby_walker_toy"}

FORBIDDEN_MARKETING_ENDINGS = [
    "確認したいです",
    "見ておきたいです",
    "比べたいです",
    "選びたいです",
    "候補です",
    "チェックしたいです",
    "確かめたいです",
    "減らせそうです",
    "ラクになりそうです",
    "役立ちそうです",
]

CONCRETE_BENEFIT_ENDINGS = [
    "減らせるアイテムです",
    "減らせる一枚です",
    "減らせるセットです",
    "減らせる商品です",
    "減らせるシートです",
    "まとめられるアイテムです",
    "まとめられる商品です",
    "まとめられます",
    "決められるアイテムです",
    "決められる商品です",
    "準備しやすくなる商品です",
    "準備しやすくなるスワドルです",
    "整えられるアイテムです",
    "シンプルにできる一枚です",
    "増やせるおもちゃです",
    "増やせるアイテムです",
    "遊びやすい知育おもちゃです",
    "減らせるカメラです",
]

TYPE_KEYWORDS = {
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
    "magnetic_blocks": ["マグネットブロック", "磁石ブロック", "磁気ブロック", "マグビルド"],
    "baby_walker_toy": ["手押し車", "ファーストウォーカー", "ベビーウォーカー", "押し車", "カタカタ", "つかまり立ち", "歩行練習"],
    "activity_cube": ["アクティビティキューブ", "ルーピング", "型はめ"],
    "ring_toy": ["リングテン", "ring10", "リング玩具", "紐通し"],
    "kids_camera": ["キッズカメラ", "子ども用カメラ"],
    "sleep_light": ["ホワイトノイズ", "授乳ライト", "寝かしつけライト"],
    "stroller_storage": ["ベビーカーバッグ", "ベビーカー用バッグ", "ベビーカー収納"],
    "wooden_blocks": ["木製積み木", "木の積み木", "積み木", "つみき", "ウッドブロック", "スタッキングブロック"],
}

PROHIBITED_BY_TYPE = {
    "wipes": ["授乳", "お腹を空かせた", "缶", "サイズアップ"],
    "swaddle": ["紙おむつ", "おむつ替え", "パンツタイプ", "テープタイプ", "授乳が楽", "必ず寝る", "泣き止む", "背中スイッチがなくなる"],
    "nursing_support": ["紙おむつ", "おむつ替え", "パンツタイプ", "必ず楽になる", "必ず飲める", "必ず寝る", "背中スイッチを防ぐ", "吐き戻しを防ぐ", "絶壁を防ぐ", "夜泣きを改善する", "安眠できる"],
    "baby_bedding": ["紙おむつ", "パンツタイプ", "授乳サポート", "必ず寝る", "背中スイッチがなくなる", "泣き止む"],
    "baby_care": ["治る", "防げる", "医療効果", "必ず", "紙おむつ", "授乳クッション", "寝かしつけ"],
    "baby_sleep": ["必ず寝る", "泣き止む", "夜泣きが改善する", "安眠できる", "安全な睡眠場所", "紙おむつ", "授乳クッション"],
    "soothing_plush": ["必ず寝る", "泣き止む", "夜泣きが改善する", "安眠できる", "一人で眠れる", "寝かしつけが不要になる", "紙おむつ", "授乳クッション"],
    "diaper": ["授乳", "食後に拭く", "缶", "お腹を空かせた", "スワドル", "おくるみ", "抱っこ布団"],
    "formula": ["おむつ替え", "枚数", "パーツ", "手口ふき"],
    "sound_blocks": ["マグネット", "紐通し", "ルーピング"],
    "wooden_blocks": ["音が鳴る", "マグネット", "ルーピング"],
    "magnetic_blocks": ["木製つみき", "音が鳴る", "紐通し", "ルーピング"],
    "baby_walker_toy": ["マグネットブロック", "歩けるようになる", "成長が早まる", "必ず", "絶対", "万能", "完璧"],
    "activity_cube": ["マグネットブロック", "リングテン", "音が鳴る積み木", "木製つみき"],
    "ring_toy": ["マグネットブロック", "ルーピング", "キッズカメラ"],
    "kids_camera": ["出産祝い", "赤ちゃんの毎日", "消耗品", "ストック"],
    "sleep_light": ["必ず寝る", "泣き止む", "ベビーカーグッズ", "履き心地"],
    "stroller_storage": ["寝かしつけ", "授乳ライト", "知育玩具", "お腹を空かせた"],
}

CHECKPOINTS = {
    "wipes": ["枚数", "個数", "価格", "収納場所"],
    "swaddle": ["サイズ", "素材", "着せ方", "洗濯方法"],
    "nursing_support": ["対象月齢", "本体サイズ", "カバーの洗濯方法", "使用できる場面", "置き場所", "使用上の注意"],
    "baby_bedding": ["サイズ", "素材", "洗濯方法", "置き場所"],
    "baby_care": ["対象月齢", "成分", "使う部位", "手入れ方法"],
    "baby_sleep": ["サイズ", "素材", "洗濯方法", "使う季節"],
    "soothing_plush": ["対象年齢", "投影機能", "音の種類", "音量調整", "タイマー", "電源方式", "洗濯可否", "本体サイズ"],
    "diaper": ["サイズ", "枚数", "1枚あたり価格", "収納場所"],
    "formula": ["容量", "個数", "価格", "賞味期限", "収納場所"],
    "sound_blocks": ["対象年齢", "パーツサイズ", "収納場所", "名入れの有無"],
    "wooden_blocks": ["対象年齢", "パーツサイズ", "個数", "収納場所"],
    "magnetic_blocks": ["対象年齢", "パーツサイズ", "パーツ数", "収納場所"],
    "baby_walker_toy": ["対象年齢", "本体サイズ", "重さ", "遊ぶ場所"],
    "activity_cube": ["対象年齢", "本体サイズ", "置き場所", "遊びの種類"],
    "ring_toy": ["対象年齢", "パーツ数", "パーツサイズ", "収納場所"],
    "kids_camera": ["対象年齢", "転送方法", "充電方式", "SDカード", "ゲーム機能"],
    "sleep_light": ["音量調整", "ライト機能", "電源方式", "設置場所"],
    "stroller_storage": ["サイズ", "取り付け方法", "容量", "対応するベビーカー"],
}

HASHTAGS = {
    "wipes": ["#おしりふき", "#まとめ買い", "#買い忘れ対策", "#おむつ替え", BRAND_TAG],
    "swaddle": ["#おくるみ", "#スワドル", "#モロー反射", "#新生児準備", BRAND_TAG],
    "nursing_support": ["#授乳サポート", "#哺乳瓶ホルダー", "#ミルク育児", "#授乳準備", BRAND_TAG],
    "baby_bedding": ["#抱っこ布団", "#ねんねクッション", "#ベビー布団", "#寝かしつけ準備", BRAND_TAG],
    "baby_care": ["#ベビーケア", "#保湿ケア", "#新生児準備", "#毎日の育児", BRAND_TAG],
    "baby_sleep": ["#スリーパー", "#寝冷え対策", "#夜の育児", "#ベビー寝具", BRAND_TAG],
    "soothing_plush": ["#寝かしつけグッズ", "#おやすみぬいぐるみ", "#プラネタリウム", "#寝室づくり", BRAND_TAG],
    "diaper": ["#紙おむつ", "#大容量パック", "#ストック管理", "#夜のおむつ替え", BRAND_TAG],
    "formula": ["#粉ミルク", "#まとめ買い", "#残量管理", "#夜間授乳", BRAND_TAG],
    "sound_blocks": ["#積み木", "#音の鳴るおもちゃ", "#手先遊び", "#1歳プレゼント", BRAND_TAG],
    "wooden_blocks": ["#木製積み木", "#積み木遊び", "#手先遊び", "#おうち遊び", BRAND_TAG],
    "magnetic_blocks": ["#マグネットブロック", "#立体遊び", "#創造遊び", "#おうち遊び", BRAND_TAG],
    "baby_walker_toy": ["#手押し車", "#つかまり立ち期", "#おうち遊び", "#室内遊び", BRAND_TAG],
    "activity_cube": ["#アクティビティキューブ", "#型はめ", "#手先遊び", "#1歳おもちゃ", BRAND_TAG],
    "ring_toy": ["#紐通し", "#リング遊び", "#指先遊び", "#木のおもちゃ", BRAND_TAG],
    "kids_camera": ["#キッズカメラ", "#スマホ転送", "#子ども目線", "#誕生日プレゼント", BRAND_TAG],
    "sleep_light": ["#ホワイトノイズ", "#授乳ライト", "#夜の育児", "#寝室づくり", BRAND_TAG],
    "stroller_storage": ["#ベビーカーバッグ", "#荷物整理", "#子連れ外出", "#ベビーカー収納", BRAND_TAG],
}
HAND_WIPES_HASHTAGS = ["#手口ふき", "#まとめ買い", "#食後ケア", "#子連れ外出", BRAND_TAG]

SCENE_DETAILS = {
    "wipes": "家族が見ても残量が分かる置き方にすると、補充の声かけもしやすくなります",
    "swaddle": "素材やサイズを先に見ておくと、家族とも準備の流れを共有しやすくなります",
    "nursing_support": "置き場所と使う場面を決めておくと、授乳前の支度をそろえやすくなります",
    "baby_bedding": "使う場所と洗濯後の置き場を決めておくと、寝かしつけ前の準備をまとめやすくなります",
    "baby_care": "使うタイミングを決めておくと、毎日のケアを後回しにしにくくなります",
    "baby_sleep": "季節や洗濯ペースに合う一枚を決めると、夜の準備をそろえやすくなります",
    "soothing_plush": "光や音の機能を先に見ておくと、寝室で使う場面を家族で共有しやすくなります",
    "diaper": "交換場所ごとの残りを見えるようにすると、次に開けるパックも決めやすくなります",
    "formula": "未開封分を同じ場所へまとめると、次の買い足し時期も家族で共有しやすくなります",
    "sound_blocks": "振った音を聞いてから積むなど、12ピースの使い方を変えられます",
    "wooden_blocks": "シンプルな遊びを親子で広げやすいです",
    "magnetic_blocks": "平面に並べたり立体にしたり、形を変えながら遊べます",
    "baby_walker_toy": "リビングで押して進む遊びを楽しめます",
    "activity_cube": "遊ぶ面を一つずつ変えると、子どもが選んだ動きを親も見守りやすくなります",
    "ring_toy": "色や数の言葉を添えながら並べると、親子で同じ動きを共有しやすくなります",
    "kids_camera": "撮った写真は、帰宅後に親子で一緒に見返せます",
    "sleep_light": "授乳時は灯り、寝室ではホワイトノイズと、必要な機能を使い分けられます",
    "stroller_storage": "ポケットごとに小物を分けると、必要な物の位置を決めやすくなります",
}

FEATURE_MARKERS = {
    "thick": ["厚手"],
    "water_rich": ["水分量"],
    "pants": ["パンツタイプ"],
    "tape": ["テープタイプ"],
    "diaper_sheet": ["おむつ替えシート"],
    "diaper_pouch": ["おむつポーチ"],
    "diaper_storage": ["おむつストッカー"],
    "foldable": ["折りたたみ"],
    "divider": ["仕切り"],
    "powder": ["粉ミルク"],
    "liquid": ["液体ミルク"],
    "follow_up": ["フォローアップミルク"],
    "sound": ["音が鳴る", "音の鳴る"],
    "wood": ["木製", "木のおもちゃ"],
    "name_option": ["名入れ"],
    "magnetic": ["マグネット", "磁石", "磁気ブロック", "マグビルド"],
    "shape_sorter": ["型はめ"],
    "looping": ["ルーピング"],
    "ring": ["リング"],
    "lacing": ["紐通し"],
    "smartphone_transfer": ["スマホ転送"],
    "sd_card": ["SDカード"],
    "sd_card_supported": ["SDカード対応", "microSD対応", "microSDカード対応"],
    "sd_card_included": ["SDカード付き", "SDカード付属", "SDカード同梱", "SDカード付"],
    "game_free": ["ゲームなし", "ゲーム機能なし"],
    "white_noise": ["ホワイトノイズ"],
    "nursing_light": ["授乳ライト"],
    "cordless": ["コードレス"],
    "usb_charge": ["USB充電"],
    "waterproof": ["防水"],
    "lightweight": ["軽量"],
    "pockets": ["ポケット"],
    "large_capacity": ["大容量"],
    "drink_holder": ["ドリンクホルダー", "カップホルダー"],
    "water_repellent": ["撥水"],
    "two_way": ["2way", "２way"],
    "quick_detach": ["ワンタッチ", "瞬間着脱"],
    "tote_conversion": ["トートバッグに変身", "トートバックになる", "トートバッグになる"],
    "insulated": ["保冷", "保温"],
    "wipe_access": ["おしりふき内蔵"],
    "storage_bag": ["収納袋"],
    "swaddle": ["おくるみ", "スワドル", "ねくるみ"],
    "moro_reflex": ["モロー反射"],
    "sleeper": ["スリーパー"],
    "gauze": ["ガーゼ", "ガーゼケット"],
    "baby_lotion": ["ベビーローション", "ローション"],
    "baby_cream": ["ベビークリーム", "クリーム"],
    "moisturizing": ["保湿"],
    "nail_care": ["爪切り", "爪やすり"],
    "nasal_aspirator": ["鼻吸い", "鼻水吸引"],
    "thermometer": ["体温計"],
    "night_light": ["ナイトライト", "ライト"],
    "hands_free": ["ハンズフリー"],
    "nursing_cushion": ["授乳クッション"],
    "milk_support": ["ミルクサポート", "ミルク屋さん"],
    "bottle_holder": ["哺乳瓶ホルダー"],
    "c_curve": ["Cカーブ", "C字"],
    "body_pressure_distribution": ["体圧分散"],
    "nursing_support": ["授乳サポートクッション", "授乳用品"],
    "washable_cover": ["洗えるカバー", "カバー洗濯", "カバー 洗濯"],
    "portable": ["持ち運び", "携帯"],
    "multi_function": ["多機能"],
    "incline": ["傾斜"],
    "cushion": ["クッション"],
    "hug_futon": ["抱っこ布団"],
    "sleep_cushion": ["ねんねクッション", "寝かしつけクッション"],
    "baby_futon": ["ベビー布団"],
    "back_switch": ["背中スイッチ"],
    "cotton": ["綿100", "コットン100", "コットン"],
    "double_gauze": ["ダブルガーゼ"],
    "plush": ["ぬいぐるみ", "ヌイグルミ", "縫いぐるみ"],
    "projector": ["投影", "プロジェクター", "プラネタリウム"],
    "star_projection": ["プラネタリウム", "星空投影", "星空"],
    "music": ["音楽", "メロディー", "オルゴール"],
    "heartbeat_sound": ["心音"],
    "timer": ["タイマー"],
    "night_light": ["ライト", "ナイトライト"],
    "washable": ["丸洗い"],
    "battery_power": ["電池", "乾電池"],
    "walker_toy": ["手押し車", "ファーストウォーカー", "ベビーウォーカー", "押し車", "カタカタ"],
    # 「歩行練習」だけでは、つかまり立ち期（対象年齢未満を含み得る）を
    # 訴求する根拠にならない。明示された場合だけ専用表現とタグを使う。
    "standing_support_play": ["つかまり立ち"],
}

TITLE_SCENE_RULES = {
    "雨の日": ("雨の日",),
    "誕生日": ("誕生日",),
    "夜": ("夜", "夜間"),
    "外出": ("外出", "旅行", "散歩"),
    "旅行": ("旅行",),
    "散歩": ("散歩",),
    "食後": ("食後",),
    "おむつ替え": ("おむつ替え", "おむつ交換"),
    "授乳": ("授乳",),
    "寝室": ("寝室",),
}

ENDING_LIMIT_PHRASES = [
    "選びたいです",
    "確認したいです",
    "考えたいです",
    "見たいです",
    "比べたいです",
]


@dataclass(frozen=True)
class ProductAttributes:
    normalized_product_name: str
    short_product_label: str
    product_type: str
    classification_keywords: tuple[str, ...]
    target_age: str
    confirmed_features: tuple[str, ...]
    confirmed_use_cases: tuple[str, ...]
    confirmed_gift_features: tuple[str, ...]
    confirmed_power_features: tuple[str, ...]
    confirmed_quantity_features: tuple[str, ...]
    purchase_checkpoints: tuple[str, ...]
    prohibited_features: tuple[str, ...]
    source_product_name: str = ""
    source_product_text: str = ""
    extraction_errors: tuple[str, ...] = ()


@dataclass(frozen=True)
class PostAnalysis:
    product_type: str = ""
    target: str = ""
    user_pain: str = ""
    search_intent: str = ""
    purchase_anxiety: str = ""
    benefit: str = ""
    usage_scene: str = ""
    appeal_axis: str = ""
    reason_to_check: str = ""
    caution: str = ""


@dataclass(frozen=True)
class QualityScore:
    score: int = 0
    empathy: int = 0
    benefit: int = 0
    naturalness: int = 0
    specificity: int = 0
    room_fit: int = 0
    non_template: int = 0
    compliance: int = 0
    improvement_comment: str = ""


@dataclass
class GeneratedPost:
    title: str
    body: str
    hashtags: list[str]
    analysis: PostAnalysis
    quality: QualityScore
    structure_pattern: str
    rewrite_count: int
    status: str
    source: str = "固定ルール"
    generation_mode: str = GENERATION_MODE
    quality_errors: list[str] = field(default_factory=list)
    attributes: ProductAttributes | None = None
    duplicate_result: str = "重複なし"
    recommendation_reason: str = ""
    sentence_form: str = ""
    title_evidence_result: str = "未確認"
    tag_evidence_result: str = "未確認"
    recommendation_reason_result: str = "未確認"
    structure_similarity: float = 0.0


@dataclass
class GenerationContext:
    used_titles: set[str] = field(default_factory=set)
    used_bodies: list[str] = field(default_factory=list)
    historical_titles: set[str] = field(default_factory=set)
    historical_bodies: list[str] = field(default_factory=list)
    used_openings: set[str] = field(default_factory=set)
    used_structure_signatures: list[str] = field(default_factory=list)
    ending_counts: dict[str, int] = field(default_factory=dict)
    construction_counts: dict[str, int] = field(default_factory=dict)
    sentence_form_counts: dict[str, int] = field(default_factory=dict)

    @classmethod
    def from_history(cls, records: Iterable[dict[str, str]]) -> GenerationContext:
        titles: set[str] = set()
        bodies: list[str] = []
        for record in records:
            status = record.get("ステータス", "").strip()
            if status and status != "ready":
                continue
            title = record.get("タイトル", "").strip()
            body = record.get("投稿文", "").strip()
            if title:
                titles.add(title)
            if body:
                bodies.append(body)
        return cls(historical_titles=titles, historical_bodies=bodies)

    def remember(self, post: GeneratedPost) -> None:
        self.used_titles.add(post.title)
        self.used_bodies.append(post.body)
        sentences = split_sentences(post.body)
        if sentences:
            self.used_openings.add(normalize_text(sentences[0]))
        self.used_structure_signatures.append(structure_signature(post.body))
        ending = ending_family(post.body)
        if ending:
            self.ending_counts[ending] = self.ending_counts.get(ending, 0) + 1
        construction = construction_family(post.body)
        if construction:
            self.construction_counts[construction] = self.construction_counts.get(construction, 0) + 1
        if post.sentence_form:
            self.sentence_form_counts[post.sentence_form] = self.sentence_form_counts.get(post.sentence_form, 0) + 1


@dataclass(frozen=True)
class Pattern:
    pattern_id: str
    title: str
    problem: str
    scene: str
    benefit: str
    closing: str
    title_required: tuple[str, ...]
    title_forbidden: tuple[str, ...] = ()
    sentence_count: int = 4


def _patterns(
    prefix: str,
    titles: list[str],
    problems: list[str],
    scenes: list[str],
    benefits: list[str],
    closings: list[str],
    required: list[tuple[str, ...]],
    forbidden: tuple[str, ...] = (),
    sentence_offset: int = 0,
) -> list[Pattern]:
    return [
        Pattern(
            pattern_id=f"{prefix}_{index + 1:02d}",
            title=title,
            problem=problems[index % len(problems)],
            scene=scenes[index % len(scenes)],
            benefit=benefits[index % len(benefits)],
            closing=closings[index % len(closings)],
            title_required=required[index],
            title_forbidden=forbidden,
            sentence_count=3 if (index + sentence_offset) % 2 == 0 else 4,
        )
        for index, title in enumerate(titles)
    ]


PATTERNS = {
    "wipes": _patterns(
        "wipes",
        ["最後の1個で焦りたくない", "おむつ替えの在庫を整える", "食後にも使う分を備える", "外出分まで切らさない", "買い足す回数を減らす", "置ける量から備える", "残り少ない日に慌てない", "消耗品の補充をまとめる"],
        ["ふき取り用品が残り少ないと、忙しい日に買い足す時間まで気になりますよね。", "おむつ替えが続く時期は、手元の残り枚数を何度も確認しがちです。", "食後の手口ふきにも使う家庭では、想像より早く減ることがありますよね。", "外出用と家用を分けると、どちらかの補充を忘れやすくなります。", "育児の消耗品は、必要な日に限って切らしたくないものです。"],
        ["{feature}なら、おむつ替えや食後に使う分をまとめて管理できます。", "{feature}なら、家用と外出用へ分けた後の残量を把握しやすくなります。", "{feature}なので、毎日の使用量を見ながら補充時期を決められます。", "{feature}を収納場所に合わせて備えると、残量を把握しやすくなります。", "{feature}なら、買い足す単位を先に決めておけます。"],
        ["1パックの枚数とセット総数が分かると、補充する量を決めやすくなります。", "未開封分の収納場所を決めておくと、次に使う分を取り出しやすくなります。", "一度に届く量が分かれば、次に買う時期も組み立てやすくなります。", "食後とおむつ替えの両方で使う家庭でも、在庫の見通しを立てやすいです。", "普段の使用量と収納場所に合う数なら、買い足す単位を決めやすくなります。"],
        ["{checks}を見て、家に置きやすい量か確認したいです。", "{checks}を比べて、使い切れる単位を選びたいです。", "{checks}を確認し、収納を圧迫しないか考えたいです。", "{checks}を見ながら、家用と外出用の配分を決めたいです。", "{checks}を確かめて、次の補充まで無理のないセットを選びたいです。"],
        [("おしりふき",), ("おむつ替え",), ("食後",), ("外出",), ("補充",), ("備え",), ("残り",), ("消耗品",)],
    ),
    "diaper": _patterns(
        "diaper",
        ["夜のおむつ交換に備えたい", "サイズ切れで慌てたくない", "外出分まで先にそろえたい", "紙おむつの残量を整えたい", "買い忘れを減らしたい", "洗い替え動線を崩したくない", "次のサイズを見極めたい", "収納できる量から選びたい"],
        ["夜のおむつ交換が続くと、残り枚数まで気にする余裕がなくなりますよね。", "紙おむつはサイズが変わる時期と買い足す量のバランスに迷います。", "外出用に分けておくと、家の在庫が思ったより早く減ることがあります。", "毎日使う紙おむつは、残り少ない日に気づくと焦りやすいです。", "まとめて備えたい一方で、サイズアウトしない量かも気になります。"],
        ["{feature}なら、夜間交換と日中分をまとめて準備できます。", "{feature}を使う枚数に合わせて分けると、家と外出用を管理しやすいです。", "{feature}なので、交換回数から補充の目安を考えられます。", "{feature}を収納場所ごとに置けば、交換時に取り出しやすくなります。", "{feature}なら、次の買い足しまでの枚数を見通せます。"],
        ["交換の途中で探す時間を減らし、夜の動きを短くしやすくなります。", "サイズと残量を一緒に見ることで、買い過ぎと買い忘れの両方を避けやすいです。", "外出前に必要枚数を移しても、家の残量を把握しやすくなります。", "使う場所を決めておくと、家族も補充に気づきやすくなります。", "交換回数に合う単位なら、次のサイズへ移る時期も考えやすいです。"],
        ["{checks}を見て、今の使用量に合うパックか確認したいです。", "{checks}を比べて、サイズアウト前に使い切れる量を選びたいです。", "{checks}を確認し、収納と交換動線に合うか考えたいです。", "{checks}を見ながら、交換場所ごとの置き方を決めたいです。", "{checks}を確かめて、無理なく補充できる単位を選びたいです。"],
        [("おむつ",), ("サイズ",), ("外出",), ("紙おむつ",), ("買い忘れ",), ("交換",), ("サイズ",), ("収納",)],
    ),
    "formula": _patterns(
        "formula",
        ["最後のミルクで焦りたくない", "夜間授乳の残量を整えたい", "ミルクの買い忘れを減らす", "使い切れる量から備えたい", "授乳回数に合う量を選びたい", "外出用も含めて管理したい", "賞味期限まで見て備えたい", "収納できるミルクを選びたい"],
        ["ミルクの残りが少ない夜は、次の授乳分が足りるか気になりますよね。", "夜間授乳が続く時期は、残量確認と買い足しが後回しになりがちです。", "ミルクは毎日の使用量が変わると、備える個数にも迷います。", "まとめて買いたい一方で、賞味期限までに使い切れる量か気になりますよね。", "外出用を取り分ける家庭では、家に残る量を見失いやすくなります。"],
        ["{feature}なら、授乳回数を目安に残量を管理できます。", "{feature}を夜間用と日中用に分けると、次に開ける分を把握できます。", "{feature}なので、買い足す時期を授乳のペースに合わせられます。", "{feature}を収納場所へまとめれば、未開封の残りを確認しやすくなります。", "{feature}なら、外出用を用意した後の残量も見通せます。"],
        ["次の授乳分を気にして慌てず、家族とも残量を共有しやすくなります。", "開封前の個数が分かれば、夜に足りない不安を減らせます。", "授乳ペースに合う量なら、買い過ぎを避けながら備えられます。", "置き場所を決めると、次に使う分を迷わず取り出せます。", "外出分と家用を分けても、補充のタイミングを決めやすくなります。"],
        ["容量と賞味期限が合えば、夜に足りない不安を減らして備えられるセットです。", "期限内に使い切れる個数なら、買い足しの回数と在庫切れの不安を減らせるセットです。", "夜間に取り出しやすい量なら、眠い時間の授乳準備を短くしやすいセットです。", "家用と外出用の配分を決めやすく、補充のタイミングも家族で共有しやすいセットです。", "収納できる範囲で備えられる量なら、最後の1缶で焦る回数を減らせるセットです。"],
        [("ミルク",), ("夜間授乳",), ("ミルク",), ("量",), ("授乳",), ("外出",), ("賞味期限",), ("ミルク",)],
    ),
    "swaddle": _patterns(
        "swaddle",
        ["夜の{label}準備に", "{label}の仕様を見たい", "夜の布もの準備に", "洗う頻度まで考える", "手が出せる形を比べたい", "新生児期の夜支度に", "{label}を無理なく使う", "退院後の夜支度に"],
        ["夜の育児では、着せる物を迷わず準備できるか気になりますよね。", "おくるみやスワドルは、赤ちゃんの体格に合うサイズか先に知りたいです。", "新生児期に使う布ものは、サイズだけでなく家の洗濯ペースも考えたいです。", "モロー反射の記載がある商品なら、商品ページ上の用途と形を押さえたいです。", "退院後の夜支度は、着せる順番や置き場所も決めておきたいです。"],
        ["{feature}なら、夜に使う布ものを一つにまとめて準備しやすいです。", "{feature}なら、家族とも使う手順を共有しやすくなります。", "{feature}なので、体格に合うサイズか商品ページで比べられます。", "{feature}を使う前提で、寝室や洗濯後の置き場所を決められます。", "{feature}なら、新生児期に必要な枚数を家の洗濯ペースから考えられます。"],
        ["商品ページの仕様が分かると、購入後の支度を具体的に想像できます。", "洗濯後に戻す場所まで決めれば、夜の準備を家族で共有しやすいです。", "商品ページで形を押さえると、使う場面を具体的に想像できます。", "サイズ感を押さえておけば、買い足し候補も絞りやすいです。", "商品情報の範囲で特徴が分かるので、必要な仕様だけを比較できます。"],
        ["{checks}を手がかりに、今の月齢と洗濯ペースへ合うか判断したいです。", "{checks}から、夜の支度に無理なく組み込めるか選びたいです。", "{checks}をもとに、必要な枚数を考えておきたいです。", "{checks}まで含めて、使う手順を商品ページで確かめておきたいです。", "{checks}を確かめて、家の寝室準備に合う候補か比べたいです。"],
        [("夜",), ("着せ方",), ("新生児",), ("洗い替え",), ("手",), ("モロー反射",), ("使う",), ("夜",)],
        ("紙おむつ", "おむつ替え", "パンツタイプ", "テープタイプ"),
    ),
    "nursing_support": _patterns(
        "nursing_support",
        ["授乳まわりの置き場所確認に", "{label}のサイズ感から", "Cカーブの仕様を知りたい", "授乳サポートを比べたい", "普段の授乳場所に合うか", "カバーの扱いまで", "多機能クッションを選ぶ", "使用方法まで読んで選ぶ"],
        ["授乳まわりのクッションは、大人の姿勢と置き場所を一緒に決めたいですよね。", "リビングや寝室で使うなら、本体サイズと普段の授乳場所との相性が大事です。", "Cカーブ表記のあるクッションは、メーカー記載の使用方法まで読んで選びたいです。", "授乳サポート用品を選ぶなら、複数用途の記載が家庭に合うか比較したいです。", "毎日触れるクッションほど、カバーの洗濯方法や戻す場所まで気になります。"],
        ["{feature}は、授乳時の補助として仕様を比較しやすいです。", "{feature}なら、本体サイズやカバーのお手入れ条件を整理できます。", "{feature}なので、普段の授乳場所に置ける大きさか判断しやすいです。", "{feature}を手がかりに、メーカー記載の使い方と注意事項を読めます。", "{feature}なら、授乳以外の用途が必要か商品ページで比べられます。"],
        ["対象月齢が分かると、今の時期に合う候補か落ち着いて選べます。", "本体サイズを押さえておけば、ソファ横や寝室で邪魔になりにくいか想像できます。", "カバーのお手入れ条件まで分かると、日々の扱いやすさを比べられます。", "注意事項を読んでおけば、家庭で使う範囲を決めやすくなります。", "置き場所を決めておくと、授乳前後に動線を迷いにくくなります。"],
        ["{checks}を手がかりに、普段の授乳場所へ置けるか判断したいです。", "{checks}から、使う部屋と収納場所に合うか選びたいです。", "{checks}をもとに、カバーの扱いまで無理がないか考えたいです。", "{checks}まで読んで、複数用途を必要な範囲で比べたいです。", "{checks}を確かめて、メーカー記載の注意事項まで把握しておきたいです。"],
        [("授乳",), ("置き場所",), ("Cカーブ",), ("授乳サポート",), ("リビング",), ("カバー",), ("多機能",), ("場面",)],
        ("紙おむつ", "パンツタイプ", "必ず", "背中スイッチを防ぐ", "吐き戻しを防ぐ", "絶壁を防ぐ", "安眠"),
    ),
    "baby_bedding": _patterns(
        "baby_bedding",
        ["抱っこ布団の置き場まで", "{label}を洗い替え込みで", "寝かしつけ前の準備に", "仕様と置き場所の確認に", "日中のねんね場所を整える", "洗える布団を比べたい", "使う場所を先に決める", "使う場所から寝具を選ぶ"],
        ["寝かしつけ前は、布ものの置き場所と準備が続きますよね。", "抱っこ布団やねんねクッションは、家のどこで使うか決めたいです。", "洗えるベビー寝具を選ぶなら、乾かす場所や洗い替えも気になります。", "寝具系の商品は、仕様と使う場所を押さえておきたいです。", "日中のねんね場所を作るなら、サイズと素材を商品情報で押さえたいです。"],
        ["{feature}なら、寝かしつけ前に使う布ものを準備しやすくなります。", "{feature}なら、置き場所と洗濯後の戻し方を整理できます。", "{feature}なので、家のスペースに合うか商品ページで比べられます。", "{feature}を使う前提で、日中と夜の置き場所を決められます。", "{feature}なら、洗い替えを含めた準備量を想像しやすいです。"],
        ["サイズと素材が分かると、寝室やリビングで使う場面を具体的に想像できます。", "洗濯後の置き場所まで決めれば、使う前後の流れを整えやすいです。", "家のスペースに収まるか分かれば、出しっぱなしになりにくい候補を選べます。", "商品情報にある範囲で比べれば、必要な仕様を落ち着いて選べます。", "使う場所を決めると、購入後の置き方まで想像しやすいです。"],
        ["{checks}を手がかりに、家の置き場所と洗濯ペースに合うか判断したいです。", "{checks}から、寝室やリビングで無理なく使える候補を選びたいです。", "{checks}をもとに、洗い替えや収納まで含めて考えたいです。", "{checks}まで含めて、使う場所に合うサイズか商品ページで確かめたいです。", "{checks}を確かめて、日中と夜の使い分けに合う候補か比べたいです。"],
        [("置き場",), ("洗い替え",), ("寝かしつけ",), ("仕様",), ("日中",), ("洗える",), ("場所",), ("場所",)],
        ("紙おむつ", "パンツタイプ", "授乳サポート", "必ず寝る", "泣き止む"),
    ),
    "baby_care": _patterns(
        "baby_care",
        ["毎日のケアを後回しにしない", "お風呂上がりの保湿準備に", "爪や鼻まわりのケアに", "朝の支度で慌てたくない", "ケア用品を一つ決めたい", "赤ちゃんのケアを続けやすく", "使うタイミングから選ぶ", "家族も使いやすいケア用品を"],
        ["お風呂上がりや朝の支度で、赤ちゃんのケア用品を探す時間があると慌ただしくなりますよね。", "毎日の保湿や身だしなみケアは、使う物が決まっていないと後回しになりがちです。", "爪切りや鼻まわりのケア用品は、必要な時にすぐ出せるかが気になります。", "赤ちゃん用のケア用品は、成分や使う部位を見ながら選びたいですよね。", "家族も使うケア用品ほど、置き場所と使うタイミングをそろえたいです。"],
        ["{feature}なら、毎日のケアで使う物を一つに決めやすくなります。", "{feature}を手元に置けば、お風呂上がりや朝の支度で使う流れを作りやすいです。", "{feature}なので、赤ちゃんのケアに使う場面を商品情報から確認できます。", "{feature}を選べば、家族にも使う物を共有しやすくなります。", "{feature}なら、必要なケアを始める前に道具を探す時間を減らせます。"],
        ["使う物が決まると、毎日のケアで迷う時間を減らせるアイテムです。", "置き場所を決めやすくなり、ケア前に探す手間を減らせるアイテムです。", "使う部位や対象月齢を見て選べば、日々のケアに取り入れやすい商品です。", "家族も同じ物を手に取りやすく、朝晩のケアをそろえやすくなります。", "毎日使う物をまとめて選べるので、支度中の迷いを減らせるアイテムです。"],
        ["{checks}を見て、今の月齢と使う部位に合うか確認したいです。", "{checks}を比べて、お風呂上がりや朝の支度で使いやすいか見たいです。", "{checks}を確認し、家の置き場所に合うか考えたいです。", "{checks}まで含めて、家族も扱いやすいか商品ページで確かめたいです。", "{checks}を手がかりに、毎日のケアに続けやすいか見ておきたいです。"],
        [("ケア",), ("保湿",), ("ケア",), ("朝",), ("ケア用品",), ("ケア",), ("タイミング",), ("家族",)],
        ("治る", "防げる", "医療効果", "必ず", "睡眠"),
    ),
    "baby_sleep": _patterns(
        "baby_sleep",
        ["夜に着る一枚を決めたい", "寝冷えが気になる夜に", "洗い替えまで考える寝具を", "夜のお世話を整えたい", "季節に合う寝具を選ぶ", "スリーパーを準備したい", "夜の布ものを減らしたい", "寝室の準備をそろえたい"],
        ["夜中に布団を蹴っていないか気になると、寝る前の準備で着せる物に迷いますよね。", "季節の変わり目は、寝る時の布ものを何枚用意するか迷いやすいです。", "スリーパーやケットは、肌ざわりと洗いやすさまで見て選びたいですよね。", "夜のお世話が続く時期は、寝室に置く布ものを増やし過ぎたくないです。", "寝具系の商品は、今の月齢と部屋の温度感に合うかが気になります。"],
        ["{feature}なら、夜に使う布ものを一つ決めやすくなります。", "{feature}を選べば、季節に合わせた寝具の候補を絞りやすいです。", "{feature}なので、洗い替えやすさまで含めて夜の準備を考えられます。", "{feature}を使う前提なら、寝室に置く布ものを増やし過ぎずに済みます。", "{feature}なら、掛けものや着る物を毎晩選び直す手間を減らせます。"],
        ["今の月齢に合うサイズと素材を選べば、夜に用意する布ものを減らせる一枚です。", "洗濯ペースに合う枚数を選べば、夜の支度で迷う時間を減らせる寝具です。", "使う季節を決めておくと、寝室の布ものを選び直す手間を減らせる商品です。", "夜に使う物を絞れるので、寝る前に準備する布ものを減らせるアイテムです。", "肌ざわりと洗いやすさを見て選べば、夜の準備をそろえやすい一枚です。"],
        ["{checks}を見て、今の月齢と季節に合うか確認したいです。", "{checks}を比べて、洗い替えしやすい枚数か見たいです。", "{checks}を確認し、寝室の準備に合うか考えたいです。", "{checks}まで含めて、夜のお世話で扱いやすいか確かめたいです。", "{checks}を手がかりに、掛けものを増やし過ぎないか見ておきたいです。"],
        [("夜",), ("寝冷え",), ("洗い替え",), ("夜",), ("季節",), ("スリーパー",), ("布もの",), ("寝室",)],
        ("必ず寝る", "泣き止む", "夜泣き", "安眠", "安全な睡眠場所"),
    ),
    "soothing_plush": _patterns(
        "soothing_plush",
        ["寝る前の声かけ時間に", "光と音を寝室で使う", "寝る前の光と音を一つに", "機能を整理して選ぶ", "音楽まで一つにまとめる", "寝室づくりの候補に", "ぬいぐるみ型を比べたい", "プレゼント前に仕様を見る"],
        ["寝る前の時間は、絵本や声かけと合わせやすい機能か気になりますよね。", "寝室で使うものは、明るさや音の種類が家庭に合うか選びたいです。", "ぬいぐるみ型の寝かしつけグッズは、対象年齢と注意事項まで読んでおきたいです。", "光や音楽付きの商品は、就寝前の流れに入れやすいか比べたいです。", "贈り物候補にするなら、機能が相手の生活に合うか見極めたいです。"],
        ["{feature}なら、就寝前の絵本や声かけと合わせる使い方を想像できます。", "{feature}は、寝る前の機能を一台にまとめたい家庭で比較できます。", "{feature}なので、投影や音楽の内容を商品ページで確かめられます。", "{feature}を手がかりに、対象年齢とメーカーの注意事項を読めます。", "{feature}なら、寝る前の時間に使う機能を整理しやすいです。"],
        ["対象年齢が分かると、今の時期に合う候補か判断しやすくなります。", "音量や電源方式まで押さえると、夜の支度に合うか考えられます。", "機能が一台にまとまると、寝室へ持ち込む物を増やし過ぎずに済みます。", "注意事項を読んでおけば、家庭で使う範囲を決めやすくなります。", "光の出方と音の種類を分けて見ると、必要な機能を選びやすいです。"],
        ["{checks}を手がかりに、寝る前の時間へ合う内容か判断したいです。", "{checks}から、音と光の機能を家庭の寝室に合わせて選びたいです。", "{checks}をもとに、家庭で扱える内容か判断したいです。", "{checks}まで含めて、就寝前の声かけと合わせやすいか比べたいです。", "{checks}を確かめて、贈る相手の生活に合う内容か見ておきたいです。"],
        [("寝る前",), ("光", "音"), ("光", "音"), ("機能",), ("音楽",), ("寝室",), ("ぬいぐるみ",), ("プレゼント",)],
        ("必ず寝る", "泣き止む", "夜泣き", "安眠", "一人で眠れる", "寝かしつけが不要"),
    ),
}


def _toy_patterns(
    product_type: str,
    label: str,
    title_terms: list[str],
    action_phrase: str,
    scene_phrase: str,
    benefit_phrase: str,
) -> list[Pattern]:
    offsets = {
        "sound_blocks": (0, 1, 2, 0),
        "wooden_blocks": (1, 3, 4, 1),
        "magnetic_blocks": (2, 0, 1, 2),
        "activity_cube": (3, 2, 0, 3),
        "ring_toy": (4, 4, 3, 4),
    }
    problem_offset, scene_offset, benefit_offset, closing_offset = offsets[product_type]
    titles = [
        f"{title_terms[0]}遊びを親子で",
        f"{title_terms[1]}遊びを広げる",
        f"{label}でおうち時間を",
        f"雨の日の{title_terms[0]}遊びに",
        f"{label}を親子遊びに",
        f"雨の日の{label}遊びに",
        f"{label}の遊び方を増やす",
        f"雨の日にも{title_terms[0]}遊びを",
    ]
    problems = [
        f"家の中で過ごす時間が長い日は、子どもが手を動かして遊べるものがあるとうれしいですよね。",
        f"雨の日や夕方の家遊びは、親子で一緒に手を動かせるおもちゃがあるとうれしいですよね。",
        f"{title_terms[0]}遊びのおもちゃは、親子で一緒に楽しめるものがあると助かります。",
        f"長く使うおもちゃなら、成長に合わせて遊び方が広がるものを選びたいですよね。",
        f"親子で{title_terms[1]}遊びをするなら、大人も隣で声をかけやすいものがうれしいです。",
        f"雨の日に{label}で遊ぶなら、家の中でも飽きにくい遊び方があると助かります。",
    ]
    scenes = [
        f"{{feature}}なら、{action_phrase}遊びを親子で楽しめます。",
        f"{{feature}}は、{scene_phrase}遊びがしやすく、おうち時間にも出しやすいです。",
        f"{{feature}}なら、親が隣で声をかけながら{action_phrase}遊びを楽しめます。",
        f"{{feature}}があると、{scene_phrase}遊びを家の中でも始めやすいです。",
        f"{{feature}}なので、子どもの反応に合わせて{action_phrase}遊びを楽しめます。",
        f"雨の日に{{feature}}があると、{scene_phrase}遊びで親子の時間を過ごせます。",
    ]
    benefits = [
        f"{benefit_phrase}きっかけになり、親子で会話しながら遊べます。",
        f"{benefit_phrase}楽しさがあり、家遊びの時間が少し豊かになります。",
        f"子どもの選び方を見ながら、{benefit_phrase}遊びを一緒に楽しめます。",
        f"{benefit_phrase}楽しさがあり、おうち時間の遊び方が広がります。",
        f"{benefit_phrase}面白さがあり、親子で手を動かして遊べます。",
    ]
    closings = [
        "親子で一緒に手を動かせるので、雨の日のおうち時間に遊び方を増やせるおもちゃです。",
        "対象年齢とパーツサイズを商品ページで確かめると、今の手先遊びに合うおもちゃか判断しやすくなります。",
        "商品ページにある遊び方を親子で試せるので、はじめての知育おもちゃとして取り入れやすいです。",
        "セット内容と収納場所を先に決めると、遊んだ後の片づけまで親子で進めやすいです。",
        "対象年齢・パーツ数・パーツサイズを確かめると、家庭で扱いやすいおもちゃか判断しやすくなります。",
    ]
    required = [
        (title_terms[0],),
        (title_terms[1],),
        (label,),
        (title_terms[0],),
        (title_terms[1],),
        (label,),
        (label,),
        (title_terms[0],),
    ]
    return [
        Pattern(
            pattern_id=f"{product_type}_{index + 1:02d}",
            title=title,
            problem=problems[5] if index == 7 else problems[(index + problem_offset) % len(problems)],
            scene=scenes[5] if index == 7 else scenes[(index + scene_offset) % len(scenes)],
            benefit=benefits[(index + benefit_offset) % len(benefits)],
            closing=closings[(index + closing_offset) % len(closings)],
            title_required=required[index],
            title_forbidden=tuple(PROHIBITED_BY_TYPE[product_type]),
            sentence_count=3 if (index + problem_offset) % 2 == 0 else 4,
        )
        for index, title in enumerate(titles)
    ]


PATTERNS.update(
    {
        "sound_blocks": _toy_patterns("sound_blocks", "音が鳴る積み木", ["音", "積む"], "振る・積む・並べる", "音や形に触れる", "手先を動かす"),
        "wooden_blocks": _toy_patterns("wooden_blocks", "木製積み木", ["積む", "形"], "積む・並べる・形を作る", "積んだり並べたりする", "形を考える"),
        "magnetic_blocks": _toy_patterns("magnetic_blocks", "マグネットブロック", ["組み立て", "立体"], "平面にも立体にも広げる", "色や形を組み合わせる", "組み合わせを考える"),
        "activity_cube": _toy_patterns("activity_cube", "アクティビティキューブ", ["型はめ", "ルーピング"], "型はめやルーピングを切り替える", "複数の遊びを選ぶ", "指先を使う"),
        "ring_toy": _toy_patterns("ring_toy", "リング玩具", ["リング", "紐通し"], "積む・並べる・紐へ通す", "色分けや数遊びを試す", "指先を使う"),
    }
)

PATTERNS["baby_walker_toy"] = _patterns(
    "baby_walker_toy",
    ["つかまり立ち期の遊びに", "押して遊べる室内おもちゃ", "リビング遊びの相棒に", "ギフトにも選びやすい手押し車", "歩き始め期のおもちゃに", "カタカタ遊びを楽しみたい", "室内で押して遊べるものを", "長く遊べる室内おもちゃに"],
    ["つかまり立ちや歩き始めの時期は、家の中でも体を使って遊べるものが欲しくなりますよね。", "押して遊ぶおもちゃを選ぶなら、リビングに置いてもかわいいものがうれしいです。", "誕生日や出産祝いで選ぶなら、遊ぶ姿がぱっと浮かぶおもちゃが選びやすいですよね。", "カタカタ押して遊ぶ姿は、親がそばで見守りながら一緒に楽しみたい場面です。", "歩き始め期のおもちゃは、今の遊びに楽しく取り入れやすいものがうれしいです。"],
    ["{feature}なら、押して進む楽しさをリビング遊びに足せます。", "{feature}は、部屋に出しても遊ぶ姿を想像しやすいおもちゃです。", "{feature}なら、押して遊ぶ楽しさと見た目のかわいさを両方楽しめます。", "{feature}があると、親が近くで見守りながら一緒に遊べます。", "{feature}なら、体を使う室内遊びのバリエーションが増えます。"],
    ["押して遊ぶおもちゃがあると、おうち時間の遊び方を増やせます。", "リビングで出しやすいものなら、雨の日や外に出にくい日も遊びを切り替えやすいです。", "見た目と遊び方が分かりやすいので、はじめてのおもちゃやギフトにも選びやすいです。", "親がそばで見守りながら、子どもが押して遊ぶ姿を楽しめるおもちゃです。", "今の月齢の遊びとして、家の中でも体を使う時間を増やせます。"],
    ["本体サイズと重さが扱いやすければ、リビングで押して遊ぶ姿を親子で楽しめるおもちゃです。", "おうち時間に体を使えるので、雨の日や外に出にくい日の遊びが広がるアイテムです。", "部屋に置いた時の見た目までかわいいと、誕生日や出産祝いのギフトにも選びやすいおもちゃです。", "本体サイズが部屋に合えば、室内で出し入れしやすい遊び道具になります。", "今の月齢に合うものなら、押して進む楽しさを親子で味わえるおもちゃです。"],
    [("つかまり立ち",), ("押して",), ("リビング",), ("ギフト",), ("歩き始め",), ("カタカタ",), ("室内",), ("木のおもちゃ",)],
    ("歩けるようになる", "成長が早まる", "必ず", "絶対", "万能", "完璧"),
)

PATTERNS.update(
    {
        "kids_camera": _patterns(
            "kids_camera",
            ["子ども目線の写真を残す", "ゲームなしで写真遊び", "外出先を子どもが撮る", "旅行の景色を一緒に残す", "撮った写真を親子で見る", "撮った写真を親子で見返す", "散歩で写真遊びを始める", "写真遊びのきっかけに"],
            ["子ども用カメラは、撮影に使う機能と保存方法が分かりにくいことがありますよね。", "外出先で持たせるなら、充電方式や写真の保存方法も気になりますよね。", "旅行の思い出を子ども目線でも残すなら、対象年齢と扱える機能が気になりますよね。", "キッズカメラは、撮った後に親子で見返せる保存方法かも気になりますよね。", "写真遊びを始める時は、ゲーム機能の有無と充電方法が気になりますよね。"],
            ["{feature}なら、外出先で子ども自身が気になった景色を撮れます。", "{feature}なら、写真を撮る遊びに集中しやすく、家庭でも扱いやすいです。", "{feature}なら、旅行や散歩で写真遊びを始められます。", "{feature}なら、撮った写真を親子で見返せます。", "{feature}なら、子どもが選んだ被写体を写真に残せます。"],
            ["ゲーム機能の有無を商品情報で確かめられます。", "充電方式と付属品を商品情報で確認できます。", "写真の保存方法を商品情報から確かめられます。", "対象年齢と本体サイズを商品情報で確認できます。", "写真の転送方法を商品情報で確かめられます。"],
            ["対象年齢・充電方式・写真の保存方法を確認すれば、外出へ持ち出す前の準備で迷う時間を減らせるカメラです。", "ゲーム機能の有無・充電方式・付属品を確かめれば、写真遊びに必要な機能を選ぶ迷いを減らせるカメラです。", "対象年齢・本体サイズ・保存方法を確認すれば、散歩や旅行で使う前の準備を整えやすいカメラです。", "充電器や記録媒体の付属有無を商品ページで確認でき、撮影後に保存方法で迷う時間を減らせるカメラです。", "対象年齢・充電方式・写真の転送方法を確かめれば、親子で見返すまでの準備を整えやすいカメラです。"],
            [("子ども目線",), ("ゲームなし",), ("外出",), ("旅行",), ("写真",), ("写真",), ("カメラ",), ("写真",)],
            ("消耗品", "ストック"),
        ),
        "sleep_light": _patterns(
            "sleep_light",
            ["夜の授乳環境を整えたい", "寝室の音と灯りをまとめる", "おむつ替えの手元を照らす", "寝かしつけ前の準備を短く", "ホワイトノイズを寝室に", "授乳ライトを夜の手元に", "コードレスで置き場所を選ぶ", "夜の育児動線を整えたい"],
            ["夜の授乳は、部屋を明るくし過ぎず手元を見たいですよね。", "寝かしつけ前は、音と灯りを別々に準備するのが手間になることがあります。", "夜のおむつ替えでは、必要な場所だけ照らせるかが気になります。", "寝室で使う機器は、電源と置き場所が夜の動線に合うか気になります。", "ホワイトノイズを寝室で使うなら、家庭に合う音量へ調整できるとうれしいです。"],
            ["{feature}なら、夜の授乳やおむつ替えで使う音と灯りを整えられます。", "{feature}を寝室へ置き、寝かしつけ前の準備を一か所にまとめられます。", "{feature}なので、夜に移動する場所へ合わせて設置できます。", "{feature}を使い、手元を見たい場面と音を流す場面を分けられます。", "{feature}なら、寝室の環境に合わせて使う機能を選べます。"],
            ["必要な機能を一台にまとめると、夜に探す物を減らしやすくなります。", "手元の準備が整っていると、授乳や交換の動きを始めやすいです。", "置き場所を決めておけば、暗い時間にも操作する位置を迷いにくいです。", "音と灯りを場面で使い分けることで、夜の育児動線を組み立てられます。", "家庭に合う設定を選べれば、夜に必要な機能を迷わず選びやすくなります。"],
            ["音と灯りを一台にまとめられるので、夜に探す物を減らしやすいアイテムです。", "寝室に置く物を増やしすぎず、夜のお世話の準備を整えやすいアイテムです。", "必要な場所だけ照らしやすいので、夜のおむつ替えの手元を整えやすいアイテムです。", "操作する場所を決めやすいので、暗い時間の動きを短くしやすいアイテムです。", "音と灯りをまとめて用意できるので、寝る前の準備を短くしやすいアイテムです。"],
            [("授乳",), ("音", "灯り"), ("おむつ替え",), ("寝かしつけ",), ("ホワイトノイズ",), ("授乳ライト",), ("コードレス",), ("夜",)],
            ("必ず寝る", "泣き止む"),
        ),
        "stroller_storage": _patterns(
            "stroller_storage",
            ["子連れ外出の荷物準備に", "すぐ使う物を手元へまとめる", "ベビーカー周りを整えたい", "飲み物とおむつを分けたい", "外出先で探す時間を減らす", "ベビーカーバッグの中を整えたい", "荷物の定位置を作りたい", "散歩前の準備を短くしたい"],
            ["子連れ外出は、出発前から細かな荷物の確認が続きますよね。", "ベビーカー周りでは、すぐ使う物ほどバッグの奥に入りがちです。", "飲み物やおむつを持つ日は、荷物の定位置を決めておきたいです。", "散歩の途中で必要な物を探すと、ベビーカーを止める時間が増えます。", "収納を足すなら、取り付けた後の大きさと動線が気になります。"],
            ["{feature}なら、外出中に使う小物をベビーカー周りへまとめられます。", "{feature}を使い、飲み物やおむつを取り出す場所を分けられます。", "{feature}なので、散歩前に必要な物を一か所へ準備できます。", "{feature}を取り付け、バッグの奥まで探す場面を減らせます。", "{feature}なら、外出先で使う順番に合わせて荷物を入れられます。"],
            ["物の定位置が決まると、出発前の確認と外出中の取り出しを短くできます。", "使う物を分けておけば、子どもを見ながら探す時間を減らせます。", "散歩ごとに同じ場所へ入れることで、忘れ物にも気づきやすくなります。", "必要な物へ手が届きやすいと、ベビーカーを止めた後の動きがまとまります。", "荷物量に合う収納なら、外出のたびに詰め直す手間を抑えられます。"],
            ["{checks}を見て、普段のベビーカーに合うか確認したいです。", "{checks}を比べ、必要な荷物が収まるかポケットの配置まで先に見ておく候補です。", "{checks}を確認し、取り出しやすい位置へ付けられるか考えたいです。", "{checks}を見ながら、取り付け位置を商品ページで見比べられます。", "{checks}を確かめて、外出動線を崩さない収納を選びたいです。"],
            [("外出",), ("手元",), ("ベビーカー",), ("飲み物", "おむつ"), ("探す",), ("ベビーカーバッグ",), ("荷物",), ("散歩",)],
            ("寝かしつけ", "授乳ライト"),
        ),
    }
)


def classify_product_type(product: Product) -> str:
    return classify_room_product_type(product)


def clean_product_name(product: Product) -> tuple[str, str, list[str]]:
    cleaned = product.name
    for pattern in NOISE_PATTERNS:
        cleaned = re.sub(pattern, " ", cleaned, flags=re.IGNORECASE)
    if product.shop_name:
        cleaned = re.sub(re.escape(product.shop_name), " ", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"[<>＜＞|｜]+", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" -_/・,、")
    tokens = list(dict.fromkeys(cleaned.split()))
    cleaned = " ".join(tokens)
    errors: list[str] = []
    if len(cleaned) < 2 or cleaned.endswith(("…", "...", "・", "-", "／", "/")):
        errors.append("short_name_unresolved: 商品名を安全に短縮できない")
    product_type = classify_product_type(product)
    short_label = short_label_for(product_type, product.identity_text)
    if product_type == "unknown" or not short_label:
        errors.append("short_name_unresolved: 商品タイプまたは短縮名を確定できない")
    return cleaned, short_label, errors


def short_label_for(product_type: str, text: str) -> str:
    if product_type == "wipes" and ("手口ふき" in text or "手口拭き" in text):
        return "手口ふき"
    labels = {
        "wipes": "おしりふき",
        "swaddle": "スワドル" if "スワドル" in text else "おくるみ",
        "nursing_support": (
            "ハンズフリー授乳サポート"
            if "ハンズフリー" in text
            else "哺乳瓶ホルダー"
            if "哺乳瓶ホルダー" in text
            else "授乳クッション"
            if "授乳クッション" in text
            else "Cカーブクッション"
            if "Cカーブ" in text or "C字" in text
            else "授乳サポート"
        ),
        "baby_bedding": (
            "抱っこ布団"
            if "抱っこ布団" in text
            else "ねんねクッション"
            if "ねんねクッション" in text
            else "ベビー布団"
        ),
        "baby_care": (
            "ベビーケアセット"
            if is_baby_care_set(text)
            else "ベビー保湿剤"
            if any(keyword in text for keyword in ["保湿", "ローション", "クリーム"])
            else "ベビー爪切り"
            if "爪" in text
            else "鼻吸い器用ノズル"
            if "鼻" in text and "ノズル" in text
            else "鼻吸い器"
            if "鼻" in text
            else "ベビー体温計"
            if "体温計" in text
            else "ベビーケア用品"
        ),
        "baby_sleep": (
            "スリーパー"
            if "スリーパー" in text
            else "ガーゼケット"
            if "ガーゼケット" in text
            else "ナイトライト"
            if "ナイトライト" in text
            else "ベビー寝具"
        ),
        "diaper": (
            "おむつ替えシート"
            if "おむつ替えシート" in text
            else "おむつポーチ"
            if "おむつポーチ" in text
            else "おむつストッカー"
            if "おむつストッカー" in text
            else "紙おむつ"
        ),
        "formula": "ミルク",
        "sound_blocks": "音が鳴る積み木",
        "baby_walker_toy": (
            "ファーストウォーカー"
            if "ファーストウォーカー" in text
            else "ベビーウォーカー"
            if "ベビーウォーカー" in text
            else "つかまり立ちおもちゃ"
            if "つかまり立ち" in text and not any(value in text for value in ["手押し車", "押し車", "カタカタ"])
            else "手押し車"
        ),
        "wooden_blocks": "木製積み木",
        "magnetic_blocks": "マグネットブロック",
        "activity_cube": "アクティビティキューブ",
        "ring_toy": "リング玩具",
        "kids_camera": "キッズカメラ",
        "sleep_light": "ホワイトノイズ付きライト" if "ホワイトノイズ" in text else "授乳ライト",
        "soothing_plush": "プラネタリウムぬいぐるみ" if "プラネタリウム" in text else "寝かしつけぬいぐるみ",
        "stroller_storage": "ベビーカーバッグ",
    }
    return labels.get(product_type, "")


def is_baby_care_set(text: str) -> bool:
    """Identify multi-step wash-and-moisturize sets from product-owned text."""
    has_set_marker = any(
        marker in text
        for marker in ["セット", "トライアル", "3品", "４品", "4品"]
    )
    has_wash_item = any(
        marker in text
        for marker in ["ヘアウォッシュ", "シャンプー", "ボディウォッシュ", "ボディソープ"]
    )
    has_moisturizer = any(
        marker in text
        for marker in ["保湿", "ローション", "リッチミルク", "保湿ミルク", "ベビーミルク"]
    )
    return has_set_marker and has_wash_item and has_moisturizer


def extract_attributes(product: Product) -> ProductAttributes:
    normalized, short_label, errors = clean_product_name(product)
    product_type = classify_product_type(product)
    text = product.identity_text
    classification_keywords = matched_type_keywords(product_type, text)
    confirmed: list[str] = []
    for feature, markers in FEATURE_MARKERS.items():
        if any(marker.lower() in text for marker in markers):
            confirmed.append(feature)
    # A named baby lotion or baby cream is itself a moisturizing care item.
    # Treat the generic moisturizing claim as grounded when the product-owned
    # identity text explicitly names either item, without relying on SEO captions.
    if product_type == "baby_care" and {"baby_lotion", "baby_cream"} & set(confirmed):
        confirmed.append("moisturizing")
    confirmed = list(dict.fromkeys(confirmed))
    target_age_match = re.search(r"(\d+)\s*(?:歳|才)(?:\s*(?:から|以上|頃))?", text)
    target_age = f"{target_age_match.group(1)}歳" if target_age_match else ""
    quantities = list(
        dict.fromkeys(
            re.sub(r"\s*pcs$", "ピース", match.group(0), flags=re.IGNORECASE)
            for match in re.finditer(
                r"\d+(?:\.\d+)?\s*(?:枚|個|本|缶|袋|箱|パック|ピース|パーツ|ポケット|pcs|ml|mL|L|g|kg)",
                product.identity_text,
                flags=re.IGNORECASE,
            )
        )
    )
    use_case_markers = {
        "wipes": [("おむつ替え", ["おむつ替え", "おしりふき", "お尻拭き", "おしり拭き"]), ("食後", ["食後", "手口"]), ("外出", ["外出"])],
        "swaddle": [("夜の準備", ["夜", "夜間"]), ("着せ方", ["着る", "手が出せる", "足が出せる"]), ("洗い替え", ["洗える", "洗濯"])],
        "nursing_support": [("授乳準備", ["授乳"]), ("ミルク時間", ["ミルク", "哺乳瓶"]), ("Cカーブ確認", ["Cカーブ", "C字"]), ("カバー洗濯", ["カバー", "洗える", "洗濯"])],
        "baby_bedding": [("寝かしつけ前", ["寝かしつけ"]), ("日中のねんね", ["日中", "ねんね"]), ("洗い替え", ["洗える", "洗濯"])],
        "baby_care": [("お風呂上がり", ["お風呂", "風呂上がり", "保湿"]), ("朝の支度", ["朝", "支度"]), ("毎日のケア", ["ケア", "爪", "鼻", "体温"])],
        "baby_sleep": [("夜の準備", ["夜", "寝る", "寝冷え"]), ("洗い替え", ["洗える", "洗濯"]), ("寝室準備", ["寝室", "ベッド", "ライト"])],
        "soothing_plush": [("寝室づくり", ["寝室", "寝かしつけ"]), ("投影機能", ["投影", "プラネタリウム"]), ("音楽機能", ["音楽", "メロディー", "オルゴール"])],
        "diaper": [("夜間交換", ["夜間", "夜用"]), ("外出", ["外出"]), ("ストック", ["ストック", "まとめ買い"])],
        "formula": [("授乳", ["授乳"]), ("夜間", ["夜間", "夜"]), ("残量管理", ["残量"])],
        "sound_blocks": [("振る", ["振る"]), ("積む", ["積み木"]), ("並べる", ["並べる"])],
        "wooden_blocks": [("積む", ["積み木"]), ("並べる", ["並べる"]), ("形を作る", ["形"])],
        "magnetic_blocks": [("組み立て", ["組み立て"]), ("平面遊び", ["平面"]), ("立体遊び", ["立体"])],
        "baby_walker_toy": [("つかまり立ち期", ["つかまり立ち"]), ("押して遊ぶ", ["押し車", "手押し車", "カタカタ"]), ("リビング遊び", ["リビング", "室内"])],
        "activity_cube": [("型はめ", ["型はめ"]), ("ルーピング", ["ルーピング"]), ("手先遊び", ["手先"])],
        "ring_toy": [("積む", ["積む"]), ("並べる", ["並べる"]), ("紐通し", ["紐通し"])],
        "kids_camera": [("写真を撮る", ["写真", "撮影"]), ("外出", ["外出"]), ("旅行", ["旅行"])],
        "sleep_light": [("夜の授乳", ["授乳"]), ("おむつ替え", ["おむつ替え"]), ("寝かしつけ前", ["寝かしつけ"])],
        "stroller_storage": [("外出時の荷物整理", ["外出", "荷物整理"]), ("すぐ取り出す", ["取り出し"]), ("ベビーカー周り", ["ベビーカー"])],
    }.get(product_type, [])
    use_cases = [
        label
        for label, markers in use_case_markers
        if any(marker.lower() in text for marker in markers)
    ]
    gift_features = [
        feature
        for feature, markers in {
            "名入れ": ["名入れ"],
            "ギフト包装": ["ギフト包装", "ラッピング"],
            "誕生日向け": ["誕生日"],
        }.items()
        if any(marker in text for marker in markers)
    ]
    power_features = [
        feature
        for feature, markers in {
            "USB充電": ["usb充電", "usb"],
            "コードレス": ["コードレス"],
            "電池式": ["電池式", "乾電池"],
        }.items()
        if any(marker in text for marker in markers)
    ]
    checkpoints = select_checkpoints(product_type, text)
    return ProductAttributes(
        normalized_product_name=normalized,
        short_product_label=short_label,
        product_type=product_type,
        classification_keywords=tuple(classification_keywords),
        target_age=target_age,
        confirmed_features=tuple(confirmed),
        confirmed_use_cases=tuple(use_cases),
        confirmed_gift_features=tuple(gift_features),
        confirmed_power_features=tuple(power_features),
        confirmed_quantity_features=tuple(quantities),
        purchase_checkpoints=tuple(checkpoints),
        prohibited_features=tuple(PROHIBITED_BY_TYPE.get(product_type, [])),
        source_product_name=product.name.lower(),
        source_product_text=text,
        extraction_errors=tuple(errors),
    )


def matched_type_keywords(product_type: str, text: str) -> list[str]:
    if product_type == "unknown":
        return []
    return [
        keyword
        for keyword in TYPE_KEYWORDS.get(product_type, [])
        if keyword.lower() in text
    ]


def select_checkpoints(product_type: str, text: str) -> list[str]:
    if product_type == "baby_care" and any(keyword in text for keyword in ["鼻吸い", "鼻水吸引"]):
        return ["対応機種", "ノズルの長さ", "お手入れ方法"]
    if product_type == "soothing_plush":
        preferred = ["対象年齢"]
        if any(keyword in text for keyword in ["投影", "プラネタリウム", "プロジェクター"]):
            preferred.append("投影機能")
        if any(keyword in text for keyword in ["音楽", "メロディー", "オルゴール", "心音", "ホワイトノイズ"]):
            preferred.append("音の種類")
        if "タイマー" in text:
            preferred.append("タイマー")
        if any(keyword in text for keyword in ["usb", "充電", "電池", "乾電池"]):
            preferred.append("電源方式")
        preferred.extend(["本体サイズ", "洗濯可否"])
        return list(dict.fromkeys(preferred))[:3]
    if product_type == "diaper":
        if "おむつストッカー" in text:
            return ["本体サイズ", "容量", "設置場所"]
        if "おむつポーチ" in text:
            return ["本体サイズ", "容量", "お手入れ方法"]
        if "おむつ替えシート" in text:
            return ["本体サイズ", "防水仕様", "持ち運び方法"]
    if product_type == "formula" and "液体ミルク" in text:
        return ["容量", "本数", "賞味期限"]
    available = CHECKPOINTS.get(product_type, ["仕様", "サイズ", "置き場所"])
    preferred = []
    for checkpoint in available:
        keywords = {
            "枚数": ["枚"],
            "個数": ["個", "セット"],
            "サイズ": ["サイズ", "s", "m", "l"],
            "容量": ["容量", "ml", "g", "kg"],
            "パーツ数": ["ピース", "パーツ"],
            "ライト機能": ["ライト"],
            "電源方式": ["充電", "usb", "電池", "コードレス"],
            "SDカード": ["sdカード", "sd"],
            "ゲーム機能": ["ゲーム"],
            "転送方法": ["転送"],
            "音量調整": ["音量", "ホワイトノイズ"],
            "成分": ["成分", "無添加", "ワセリン", "保湿"],
            "使う部位": ["顔", "体", "全身", "鼻", "爪"],
            "素材": ["素材", "綿", "コットン", "ガーゼ"],
            "洗濯方法": ["洗える", "洗濯"],
            "使う季節": ["春", "夏", "秋", "冬", "通年", "寝冷え"],
        }.get(checkpoint, [])
        if not keywords or any(keyword in text for keyword in keywords):
            preferred.append(checkpoint)
    for checkpoint in available:
        if checkpoint not in preferred:
            preferred.append(checkpoint)
    return preferred[:3]


def confirmed_feature_phrase(attributes: ProductAttributes) -> str:
    features = set(attributes.confirmed_features)
    quantity = attributes.confirmed_quantity_features[0] if attributes.confirmed_quantity_features else ""
    source = attributes.source_product_text
    if attributes.product_type == "wipes":
        prefix = "厚手の" if "thick" in features else ""
        sheet_count = next(
            (value for value in attributes.confirmed_quantity_features if value.endswith("枚")),
            "",
        )
        package_count = next(
            (
                value
                for value in attributes.confirmed_quantity_features
                if value.endswith(("個", "袋", "箱", "パック"))
            ),
            "",
        )
        if sheet_count and package_count:
            quantity_text = f"{sheet_count}入り×{package_count}の"
        else:
            quantity_text = f"{quantity}入りの" if quantity else ""
        return f"{prefix}{quantity_text}{attributes.short_product_label}"
    if attributes.product_type == "swaddle":
        if "綿100" in source and "ファスナー" in source:
            return "綿100％で上下ファスナー式のスワドル"
        parts = []
        if "sleeper" in features:
            parts.append("スリーパー型")
        if "cotton" in features:
            parts.append("コットン")
        if "moro_reflex" in features:
            parts.append("モロー反射の記載がある")
        if parts:
            return f"{'・'.join(parts)}{attributes.short_product_label}"
        return attributes.short_product_label
    if attributes.product_type == "nursing_support":
        if any(term in source for term in ["妊娠枕", "抱き枕", "抱きまくら"]):
            shape = "C字型" if any(term in source for term in ["c型", "c字"]) else ""
            return f"授乳にも使える{shape}ロング抱き枕"
        details = [
            label
            for key, label in [
                ("hands_free", "ハンズフリー"),
                ("bottle_holder", "哺乳瓶ホルダー"),
                ("nursing_cushion", "授乳クッション"),
                ("incline", "傾斜"),
                ("c_curve", "Cカーブ"),
                ("body_pressure_distribution", "体圧分散表記"),
                ("multi_function", "多機能"),
                ("washable_cover", "カバー洗濯対応"),
                ("cushion", "クッション"),
                ("milk_support", "ミルクサポート"),
            ]
            if key in features
        ]
        unique_details = [
            detail
            for detail in dict.fromkeys(details)
            if detail != attributes.short_product_label
            and not (
                detail == "クッション"
                and features & {"nursing_cushion", "c_curve", "body_pressure_distribution"}
            )
        ]
        if not unique_details:
            return attributes.short_product_label
        prefix = "・".join(unique_details[:3])
        return f"{prefix}を備えた{attributes.short_product_label}"
    if attributes.product_type == "baby_bedding":
        # SEO-rich listings often name adjacent bedding categories together.
        # Use the classified product label once, then add only material facts.
        material = (
            "ダブルガーゼの"
            if "double_gauze" in features
            else "コットン素材の"
            if "cotton" in features
            else ""
        )
        return f"{material}{attributes.short_product_label}"
    if attributes.product_type == "baby_care":
        if "moisturizing" in features and ("baby_lotion" in features or "baby_cream" in features):
            components = baby_care_set_components(attributes)
            if len(components) >= 3:
                return f"{'・'.join(components)}の{len(components)}品セット"
            if quantity and "ポンプ" in source and "全身" in source:
                return f"顔と全身に使える{quantity}のポンプ式ベビー保湿剤"
            return "保湿ケアに使うベビー保湿剤"
        if "nail_care" in features:
            return "赤ちゃんの爪まわりに使うケア用品"
        if "nasal_aspirator" in features:
            return attributes.short_product_label
        if "thermometer" in features:
            return "毎日の体調確認に使う体温計"
        return attributes.short_product_label
    if attributes.product_type == "baby_sleep":
        if "6重" in source and "sleeper" in features and "gauze" in features:
            return "6重ガーゼのスリーパー"
        if "sleeper" in features and "gauze" in features:
            return "ガーゼ素材のスリーパー"
        if "sleeper" in features:
            return "夜に着せやすいスリーパー"
        if "gauze" in features:
            return "肌ざわりを見て選べるガーゼケット"
        if "night_light" in features:
            return "夜のお世話で使うナイトライト"
        return attributes.short_product_label
    if attributes.product_type == "diaper":
        if "diaper_sheet" in features:
            return "外出先でも使うおむつ替えシート"
        if "diaper_pouch" in features:
            return "おむつ替え用品をまとめるおむつポーチ"
        if "diaper_storage" in features:
            details = []
            if "foldable" in features:
                details.append("折りたたみ式")
            if "divider" in features:
                details.append("仕切り付き")
            prefix = "・".join(details)
            return f"{prefix}のおむつストッカー" if prefix else "おむつ替え用品をまとめるおむつストッカー"
        style = "パンツタイプ" if "pants" in features else "テープタイプ" if "tape" in features else ""
        quantity_text = f"{quantity}入りの" if quantity else ""
        return f"{style}で{quantity_text}紙おむつ" if style else f"{quantity_text}紙おむつ"
    if attributes.product_type == "formula":
        kind = (
            "粉ミルク"
            if "powder" in features
            else "液体ミルク"
            if "liquid" in features
            else "フォローアップミルク"
        )
        return f"{quantity}入りの{kind}" if quantity else kind
    if attributes.product_type == "sound_blocks":
        material = "木製の" if "wood" in features else ""
        quantity_text = f"{quantity}の" if quantity else ""
        name_option = "名入れ対応で、" if "name_option" in features else ""
        return f"{name_option}{quantity_text}音が鳴る{material}積み木"
    if attributes.product_type == "baby_walker_toy":
        if "standing_support_play" in features:
            return f"つかまり立ち期にも押して遊べる{attributes.short_product_label}"
        if "wood" in features:
            return f"木製の{attributes.short_product_label}"
        return f"押して遊べる{attributes.short_product_label}"
    if attributes.product_type == "wooden_blocks":
        if "storage_bag" in features and quantity:
            return f"{quantity}で収納袋付きの木製積み木"
        storage = "収納袋付きの" if "storage_bag" in features else ""
        return f"{quantity}の木製積み木" if quantity else f"{storage}木製積み木"
    if attributes.product_type == "magnetic_blocks":
        return f"{quantity}のマグネットブロック" if quantity else "マグネットブロック"
    if attributes.product_type == "activity_cube":
        actions = [label for key, label in [("shape_sorter", "型はめ"), ("looping", "ルーピング")] if key in features]
        return f"{'と'.join(actions)}を備えたアクティビティキューブ"
    if attributes.product_type == "ring_toy":
        actions = [label for key, label in [("ring", "リング"), ("lacing", "紐通し")] if key in features]
        quantity_text = f"{quantity}の" if quantity else ""
        return f"{'と'.join(actions)}を含む{quantity_text}リング玩具"
    if attributes.product_type == "kids_camera":
        functions = []
        if "smartphone_transfer" in features:
            functions.append("スマホ転送対応")
        if "sd_card_included" in features:
            functions.append("SDカード付き")
        elif "sd_card_supported" in features:
            functions.append("SDカード対応")
        if "game_free" in features:
            functions.append("ゲーム機能なし")
        if "usb_charge" in features:
            functions.append("USB充電式")
        return f"{'・'.join(functions)}のキッズカメラ" if functions else "キッズカメラ"
    if attributes.product_type == "sleep_light":
        functions = [label for key, label in [("white_noise", "ホワイトノイズ"), ("nursing_light", "授乳ライト")] if key in features]
        if "cordless" in features:
            return f"{'と'.join(functions)}を備え、コードレスで使えるライト"
        return f"{'と'.join(functions)}を備えたライト"
    if attributes.product_type == "soothing_plush":
        functions = [
            label
            for key, label in [
                ("projector", "投影機能"),
                ("star_projection", "プラネタリウム"),
                ("music", "音楽"),
                ("white_noise", "ホワイトノイズ"),
                ("timer", "タイマー"),
                ("night_light", "ライト"),
            ]
            if key in features
        ]
        prefix = "・".join(dict.fromkeys(functions[:3]))
        if prefix:
            return f"{prefix}を備えた{attributes.short_product_label}"
        return attributes.short_product_label
    if attributes.product_type == "stroller_storage":
        descriptors = [label for key, label in [("waterproof", "防水仕様"), ("lightweight", "軽量")] if key in features]
        pocket = f"{quantity}の" if quantity and "ポケット" in quantity else ""
        if descriptors and pocket:
            return f"{'・'.join(descriptors)}で、{pocket}ベビーカーバッグ"
        if descriptors:
            return f"{'・'.join(descriptors)}のベビーカーバッグ"
        return f"{pocket}ベビーカーバッグ"
    return attributes.short_product_label


def baby_care_set_components(attributes: ProductAttributes) -> list[str]:
    """Return explicitly named care items without inferring count from SEO captions."""
    name = attributes.source_product_name
    components: list[str] = []
    for label, markers in [
        ("ヘア洗浄料", ["ヘアウォッシュ", "シャンプー"]),
        ("ボディ洗浄料", ["ボディウォッシュ", "ボディソープ"]),
        ("ローション", ["ローション"]),
        ("ミルク", ["リッチミルク", "保湿ミルク", "ベビーミルク"]),
    ]:
        if any(marker in name for marker in markers):
            components.append(label)
    return components


def formula_package_facts(attributes: ProductAttributes) -> tuple[str, tuple[str, ...]]:
    """Build a formula pack description only from quantities named by the seller."""
    quantities = list(attributes.confirmed_quantity_features)
    capacity = next(
        (value for value in quantities if re.search(r"(?:g|kg|ml|l)$", value, flags=re.IGNORECASE)),
        "",
    )
    unit_capacity = next(
        (
            value
            for value in quantities
            if value != capacity and re.search(r"(?:g|kg|ml|l)$", value, flags=re.IGNORECASE)
        ),
        "",
    )
    count = next((value for value in quantities if value.endswith(("袋", "缶", "本"))), "")
    source_name = attributes.source_product_name
    if not count and "缶" in source_name:
        item_count = re.search(r"(\d+)\s*個セット", source_name)
        if item_count:
            count = f"{item_count.group(1)}缶"

    facts = tuple(value for value in [capacity, unit_capacity, count] if value)
    if capacity and unit_capacity and count:
        return f"{capacity}（{unit_capacity}×{count}）", facts
    if capacity and count:
        return f"{capacity}・{count}", facts
    return capacity or count or confirmed_feature_phrase(attributes), facts


def stroller_storage_feature_phrases(attributes: ProductAttributes) -> list[str]:
    """Return only attachment-bag functions explicitly present in the product name."""
    name = attributes.source_product_name
    phrases: list[str] = []

    def add(label: str, *markers: str) -> None:
        if any(marker in name for marker in markers) and label not in phrases:
            phrases.append(label)

    add("大容量", "大容量")
    add("ドリンクホルダー付き", "ドリンクホルダー", "カップホルダー")
    if "保冷" in name and "保温" in name:
        phrases.append("保冷・保温")
    elif "保冷" in name:
        phrases.append("保冷")
    add("防水仕様", "防水")
    add("撥水仕様", "撥水")
    add("軽量", "軽量")
    add("2WAY", "2way", "２way")
    add("ワンタッチ着脱", "ワンタッチ", "瞬間着脱")
    add("トートバッグへ切り替え可能", "トートバッグに変身", "トートバックになる", "トートバッグになる")
    add("おしりふき内蔵", "おしりふき内蔵")
    add("ポケット付き", "ポケット")
    if not phrases:
        add("多機能", "多機能")
    return phrases


def hashtags_for(
    attributes: ProductAttributes,
    *,
    body: str = "",
    title: str = "",
) -> list[str]:
    features = set(attributes.confirmed_features)
    combined = f"{title}{body}"
    tags: list[str] = []

    def add(tag: str, condition: bool = True) -> None:
        if condition and tag not in tags and len(tags) < 4:
            tags.append(tag)

    product_type = attributes.product_type
    if product_type == "wipes":
        add("#手口ふき" if attributes.short_product_label == "手口ふき" else "#おしりふき")
        add("#厚手", "thick" in features)
        add("#食後ケア", "食後" in combined)
        add("#おむつ替え", "おむつ替え" in combined)
        add("#まとめ買い", len(attributes.confirmed_quantity_features) >= 2 or "まとめ" in combined)
        add("#ストック管理")
    elif product_type == "swaddle":
        add("#スワドル", "swaddle" in features and "スワドル" in attributes.short_product_label)
        add("#おくるみ")
        add("#モロー反射", "moro_reflex" in features)
        add("#新生児準備", "新生児" in attributes.source_product_text)
        add("#夜の育児", "夜" in combined)
        add("#洗い替え準備", "洗い替え" in attributes.source_product_text)
    elif product_type == "nursing_support":
        add("#授乳サポート")
        add("#授乳クッション", "nursing_cushion" in features)
        add("#ハンズフリー授乳", "hands_free" in features)
        add("#哺乳瓶ホルダー", "bottle_holder" in features)
        add("#ミルク育児", "ミルク" in combined)
        add("#新生児準備", "新生児" in attributes.source_product_text)
        add("#育児クッション", "cushion" in features or "c_curve" in features)
        add("#授乳準備")
    elif product_type == "baby_bedding":
        add("#抱っこ布団", "hug_futon" in features)
        add("#ねんねクッション", "sleep_cushion" in features)
        add("#ベビー布団", "baby_futon" in features)
        add("#寝かしつけ準備", "寝かしつけ" in combined)
        add("#洗い替え準備", "洗い替え" in attributes.source_product_text)
        add("#ダブルガーゼ", "double_gauze" in features)
        add("#コットン素材", "cotton" in features)
        add(
            "#洗える寝具",
            any(term in attributes.source_product_text for term in ["洗える", "洗濯"]),
        )
        add("#ベビー寝具")
    elif product_type == "baby_care":
        add("#ベビーケア")
        add("#保湿ケア", "moisturizing" in features or "baby_lotion" in features or "baby_cream" in features)
        add("#爪ケア", "nail_care" in features)
        add("#鼻吸い", "nasal_aspirator" in features)
        add("#体温計", "thermometer" in features)
        add("#新生児準備", "新生児" in attributes.source_product_text)
        add("#ベビーローション", "baby_lotion" in features)
        add("#全身保湿", "全身" in attributes.source_product_text and "moisturizing" in features)
        add("#ポンプ式", "ポンプ" in attributes.source_product_text)
        add("#毎日の育児")
    elif product_type == "baby_sleep":
        add("#スリーパー", "sleeper" in features)
        add("#ガーゼケット", "gauze" in features and "ガーゼケット" in attributes.source_product_text)
        add("#寝冷え対策", "寝冷え" in attributes.source_product_text)
        add("#夜の育児", "夜" in combined)
        add("#ベビー寝具")
        add("#6重ガーゼ", "6重" in attributes.source_product_text and "gauze" in features)
        add("#コットン素材", "cotton" in features)
        add(
            "#洗える寝具",
            any(term in attributes.source_product_text for term in ["洗える", "洗濯"]),
        )
        add("#洗い替え準備", "洗い替え" in attributes.source_product_text)
    elif product_type == "diaper":
        if "diaper_sheet" in features:
            add("#おむつ替え")
            add("#おむつ替えシート")
            add("#持ち運び")
            add("#衛生グッズ")
            add("#防水", "waterproof" in features)
        elif "diaper_pouch" in features:
            add("#おむつ替え")
            add("#おむつ収納")
            add(
                "#子連れ外出",
                "外出" in attributes.source_product_text
                or any("外出" in use_case for use_case in attributes.confirmed_use_cases),
            )
            add("#荷物整理")
            add("#ストック管理")
        elif "diaper_storage" in features:
            add("#おむつ替え")
            add("#おむつ収納")
            add("#家の収納")
            add("#ストック管理")
        else:
            add("#紙おむつ")
            add("#パンツタイプ", "pants" in features)
            add("#テープタイプ", "tape" in features)
            add("#夜のおむつ替え", "夜" in combined)
            add(
                "#外出用おむつ",
                "外出" in combined
                and any("外出" in use_case for use_case in attributes.confirmed_use_cases),
            )
            add("#サイズ選び")
            add("#ストック管理")
    elif product_type == "formula":
        add("#粉ミルク", "powder" in features)
        add("#液体ミルク", "liquid" in features)
        add("#夜間授乳", "夜" in combined)
        add("#残量管理")
        add("#まとめ買い", len(attributes.confirmed_quantity_features) >= 2 or "まとめ" in combined)
        add("#授乳準備")
    elif product_type == "sound_blocks":
        add("#積み木")
        add("#音の鳴るおもちゃ", "sound" in features)
        add("#木製おもちゃ", "wood" in features)
        add("#名入れ", "name_option" in features)
        add("#手先遊び")
    elif product_type == "wooden_blocks":
        add("#木製積み木", "wood" in features)
        add("#収納袋付き", "storage_bag" in features)
        add("#積み木遊び")
        add("#おうち遊び")
        add("#手先遊び")
    elif product_type == "magnetic_blocks":
        add("#マグネットブロック", "magnetic" in features)
        add("#立体遊び", "立体" in attributes.source_product_text)
        add("#組み立て遊び")
        add("#おうち遊び")
        add("#創造遊び")
    elif product_type == "baby_walker_toy":
        add("#手押し車", "手押し車" in attributes.short_product_label or "手押し車" in attributes.source_product_text)
        add("#ファーストウォーカー", "ファーストウォーカー" in attributes.short_product_label or "ファーストウォーカー" in attributes.source_product_text)
        add("#ベビーウォーカー", "ベビーウォーカー" in attributes.short_product_label or "ベビーウォーカー" in attributes.source_product_text)
        add("#つかまり立ち期", "つかまり立ち" in attributes.source_product_text)
        add("#おうち遊び")
        add("#室内遊び")
        add("#ベビーおもちゃ")
    elif product_type == "activity_cube":
        add("#アクティビティキューブ")
        add("#型はめ", "shape_sorter" in features)
        add("#ルーピング", "looping" in features)
        add("#手先遊び")
        add("#おうち遊び")
    elif product_type == "ring_toy":
        add("#リング遊び", "ring" in features)
        add("#紐通し", "lacing" in features)
        add("#木のおもちゃ", "wood" in features)
        add("#指先遊び")
        add("#数遊び", "数遊び" in combined)
        add("#おうち遊び")
    elif product_type == "kids_camera":
        add("#キッズカメラ")
        add("#スマホ転送", "smartphone_transfer" in features)
        add("#SDカード", bool(features & {"sd_card_supported", "sd_card_included"}))
        add("#ゲームなし", "game_free" in features)
        add("#USB充電", "usb_charge" in features)
        add("#誕生日プレゼント", "誕生日向け" in attributes.confirmed_gift_features and "誕生日" in combined)
        add("#子ども目線")
        add("#写真遊び")
    elif product_type == "sleep_light":
        add("#ホワイトノイズ", "white_noise" in features)
        add("#授乳ライト", "nursing_light" in features)
        add("#コードレス", "cordless" in features)
        add("#夜の育児", "夜" in combined)
        add("#寝室づくり", "寝室" in combined)
    elif product_type == "soothing_plush":
        add("#寝かしつけグッズ")
        add("#おやすみぬいぐるみ", "plush" in features)
        add("#プラネタリウム", "projector" in features or "star_projection" in features)
        add("#寝室づくり")
        add("#ベビートイ")
    elif product_type == "stroller_storage":
        add("#ベビーカーバッグ")
        add("#防水", "waterproof" in features)
        add("#軽量", "lightweight" in features)
        add("#ベビーカー収納")
        add("#子連れ外出", "外出" in combined)
        add("#荷物整理")

    safe_fallbacks = {
        "wipes": ["#育児消耗品", "#ストック管理"],
        "swaddle": ["#夜の育児", "#ベビー服"],
        "nursing_support": ["#授乳準備", "#育児クッション", "#ミルク育児"],
        "baby_bedding": ["#ベビー寝具", "#寝かしつけ準備"],
        "baby_care": ["#ベビーケア", "#毎日の育児", "#赤ちゃんケア", "#育児ケア"],
        "baby_sleep": ["#夜の育児", "#ベビー寝具", "#スリーパー"],
        "diaper": ["#おむつ替え", "#サイズ選び"],
        "formula": ["#授乳準備", "#ミルク育児"],
        "sound_blocks": ["#手先遊び", "#おうち遊び"],
        "wooden_blocks": ["#積み木遊び", "#おうち遊び"],
        "magnetic_blocks": ["#組み立て遊び", "#おうち遊び"],
        "baby_walker_toy": ["#おうち遊び", "#室内遊び", "#ベビーおもちゃ"],
        "activity_cube": ["#手先遊び", "#おうち遊び"],
        "ring_toy": ["#指先遊び", "#おうち遊び"],
        "kids_camera": ["#子ども目線", "#写真遊び"],
        "sleep_light": ["#夜の育児", "#寝室づくり"],
        "soothing_plush": ["#寝かしつけグッズ", "#おやすみぬいぐるみ", "#寝室づくり", "#ベビートイ"],
        "stroller_storage": ["#ベビーカー収納", "#荷物整理"],
    }.get(product_type, ["#育児用品"])
    for tag in safe_fallbacks:
        add(tag)
    return tags[:4] + [BRAND_TAG]


class FixedRulePostGenerator:
    def generate(
        self,
        scored: ScoredProduct,
        *,
        context: GenerationContext,
        season: str = "",
    ) -> GeneratedPost:
        del season
        attributes = extract_attributes(scored.product)
        if attributes.extraction_errors or attributes.product_type not in PATTERNS:
            return self._needs_review(
                scored,
                attributes,
                list(attributes.extraction_errors) or ["unsupported_product_type: 対応する固定ルールがない商品タイプ"],
            )
        patterns = PATTERNS[attributes.product_type]
        start = stable_index(scored.product.url or scored.product.name, len(patterns))
        last_post: GeneratedPost | None = None
        for attempt in range(MAX_GENERATION_ATTEMPTS):
            pattern = patterns[(start + attempt) % len(patterns)]
            post = build_candidate(scored, attributes, pattern, attempt)
            errors = validate_post(post, attributes, context)
            if not errors:
                post.status = "ready"
                post.quality = quality_score(post, attributes, [])
                context.remember(post)
                return post
            post.status = "needs_review"
            post.quality_errors = errors
            post.quality = quality_score(post, attributes, errors)
            post.duplicate_result = duplicate_summary(errors)
            last_post = post
        assert last_post is not None
        specific_errors = ", ".join(error_code(error) for error in last_post.quality_errors)
        max_error = (
            f"max_regeneration_exceeded: {specific_errors}"
            if specific_errors
            else "max_regeneration_exceeded"
        )
        last_post.quality_errors = list(
            dict.fromkeys(
                last_post.quality_errors
                + [max_error, f"最大{MAX_GENERATION_ATTEMPTS}回の再生成で品質条件を満たせない"]
            )
        )
        last_post.quality = quality_score(
            last_post,
            attributes,
            last_post.quality_errors,
        )
        return last_post

    def _needs_review(
        self,
        scored: ScoredProduct,
        attributes: ProductAttributes,
        errors: list[str],
    ) -> GeneratedPost:
        analysis = build_analysis(scored, attributes, "")
        return GeneratedPost(
            title="",
            body="",
            hashtags=hashtags_for(attributes),
            analysis=analysis,
            quality=QualityScore(
                score=0,
                compliance=0,
                improvement_comment=" / ".join(errors),
            ),
            structure_pattern="unresolved",
            rewrite_count=0,
            status="needs_review",
            quality_errors=errors,
            attributes=attributes,
            recommendation_reason=scored.recommendation_reason,
        )


def build_candidate(
    scored: ScoredProduct,
    attributes: ProductAttributes,
    pattern: Pattern,
    attempt: int,
) -> GeneratedPost:
    feature = confirmed_feature_phrase(attributes)
    checks = "・".join(attributes.purchase_checkpoints)
    title = pattern.title.format(label=attributes.short_product_label, feature=feature, checks=checks)
    scene = pattern.scene.format(feature=feature, checks=checks, label=attributes.short_product_label)
    closing = pattern.closing.format(feature=feature, checks=checks, label=attributes.short_product_label)
    if pattern.sentence_count == 3:
        feature_benefit = merge_sentences(scene, pattern.benefit)
        body = "".join(
            [
                pattern.problem,
                feature_benefit,
                closing,
            ]
        )
    else:
        body = "".join([pattern.problem, scene, pattern.benefit, closing])
    if len(body) < 160 and pattern.sentence_count == 4:
        expanded_scene = ensure_sentence(
            merge_sentences(scene, SCENE_DETAILS[attributes.product_type])
        )
        body = "".join(
            [
                pattern.problem,
                expanded_scene,
                pattern.benefit,
                closing,
            ]
        )
    if len(body) < 160 and pattern.sentence_count == 3:
        closing = expanded_three_sentence_closing(
            attributes.product_type,
            checks,
        )
        body = "".join(
            [
                pattern.problem,
                feature_benefit,
                closing,
            ]
        )
    title = adapt_wipes_context(title, attributes)
    body = adapt_wipes_context(body, attributes)
    title = remove_intention_phrases(title)
    body = remove_intention_phrases(body)
    marketing_text = marketing_title_body(attributes, pattern)
    if marketing_text is not None:
        title, body = marketing_text
        title = remove_intention_phrases(title)
        body = remove_intention_phrases(body)
    body = add_listing_teaser(body, attributes)
    if attempt < DISTINCTIVE_REWRITE_START and attributes.product_type != "wipes":
        product_specific = product_specific_distinctive_copy(
            attributes,
            teaser=listing_teaser(attributes),
            variant=attempt,
            required_terms=pattern.title_required,
        )
        if product_specific is not None:
            title, body = product_specific
    if attempt >= DISTINCTIVE_REWRITE_START:
        title, body = add_distinctive_product_detail(
            title,
            body,
            scored,
            attributes,
            attempt=attempt,
            required_terms=pattern.title_required,
        )
    title = buyer_clear_title(title, attributes)
    analysis = build_analysis(scored, attributes, pattern.pattern_id)
    post = GeneratedPost(
        title=title,
        body=body,
        hashtags=hashtags_for(attributes, body=body, title=title),
        analysis=analysis,
        quality=QualityScore(),
        structure_pattern=pattern.pattern_id,
        rewrite_count=attempt,
        status="needs_review",
        attributes=attributes,
        recommendation_reason=scored.recommendation_reason,
        sentence_form=f"{len(split_sentences(body))}文型",
    )
    return post


def uses_marketing_copy(attributes: ProductAttributes) -> bool:
    features = set(attributes.confirmed_features)
    if attributes.product_type == "nursing_support":
        return bool(
            features
            & {
                "c_curve",
                "body_pressure_distribution",
                "nursing_cushion",
                "cushion",
                "multi_function",
                "hands_free",
                "milk_support",
                "bottle_holder",
            }
        )
    if attributes.product_type == "diaper":
        return bool(features & {"diaper_sheet", "diaper_pouch", "diaper_storage"})
    if attributes.product_type in {"baby_bedding", "baby_care", "baby_sleep", "baby_walker_toy"}:
        return bool(features)
    return attributes.product_type in {"swaddle", "soothing_plush"}


def marketing_title_body(attributes: ProductAttributes, pattern: Pattern) -> tuple[str, str] | None:
    if not uses_marketing_copy(attributes):
        return None
    pattern_number = int(pattern.pattern_id.rsplit("_", 1)[1])
    features = set(attributes.confirmed_features)
    checks = purchase_check_phrase(attributes)
    feature = marketing_feature_phrase(attributes)

    if attributes.product_type == "nursing_support":
        cushion_like = bool(features & {"c_curve", "body_pressure_distribution", "nursing_cushion", "cushion", "multi_function"})
        if not cushion_like:
            title = "授乳中の手を空けたい"
            problem = "授乳中に哺乳瓶を支えながら、ミルク周りの支度や姿勢まで気にするのは落ち着かないですよね。"
            scene = (
                f"{feature}なら、授乳中に使う補助アイテムを一つに決めやすく、毎回の準備をそろえやすくなります。"
            )
            closing = (
                "使える哺乳瓶サイズと対象月齢を選べば、"
                "授乳中に手で支え続ける手間を減らせるアイテムです。"
            )
            return title, problem + scene + closing
        title = "授乳の支えを寄せ集めたくない"
        problem = "授乳のたびに手持ちのクッションを重ねても、形や沈み方が違うと支え方が毎回変わって手間ですよね。"
        scene = (
            f"{feature}なら、授乳中に使う支えを寄せ集めず、一つのクッションに決めやすくなります。"
        )
        closing = (
            "カバーのお手入れや普段の授乳場所に置けるサイズを選べば、"
            "授乳前に支えを探し直す手間を減らせるアイテムです。"
        )
        if "multi_function" in features:
            title = "授乳用クッションを一つにしたい"
            problem = "授乳に使うクッションを探す時、多機能と書かれていても具体的な使い方が分からないと判断しにくいですよね。購入後の使い道まで確認が必要です。"
            scene = (
                f"{feature}でも、用途の説明が不足している場合は自動投稿には回せません。"
            )
            closing = (
                "エージェント側で対象月齢や使える場面の根拠を確認してから、投稿可否を判断する仮文章です。"
            )
        return title, problem + scene + closing

    if attributes.product_type == "diaper":
        if "diaper_sheet" in features:
            title = "外出先のおむつ替えで慌てない"
            problem = "外出先や車内でおむつ替えをする時、赤ちゃんを待たせながらバッグや荷物の中から敷く物を探す時間があると慌ただしいですよね。"
            scene = f"{feature}なら、防水表記も手がかりに、替える時に使う敷き物を一つに決めやすくなります。"
            closing = "持ち運びやすいサイズを選べば、外出先のおむつ替えで敷く物を毎回探す手間を減らせるシートです。"
        elif "diaper_pouch" in features:
            title = "外出用のおむつを探したくない"
            problem = "子連れ外出でおむつやおしりふきがバッグの中に散らばると、替える前の準備に手間取りますよね。"
            scene = f"{feature}なら、おむつ替えに使う小物を一つにまとめやすくなります。"
            closing = "普段のバッグに収まるサイズを選べば、外出先で必要な物を探す時間を減らせるアイテムです。"
        else:
            title = "おむつ替えの置き場を整えたい"
            problem = "家の中でおむつ替え用品の置き場が分かれると、交換前に必要な物を探しがちですよね。"
            scene = f"{feature}なら、おむつ替えで使う物を一か所にまとめやすくなります。"
            closing = "置けるサイズを選べば、家のおむつ替えで必要な物を探す手間を減らせるアイテムです。"
        return title, problem + scene + closing

    if attributes.product_type == "swaddle":
        title = "夜の一枚を決めたい"
        problem = "眠い中で肌着や掛けものを見ながら「今日は何を着せる？」と考えると、新生児期の夜の着替えが慌ただしくなってしまいますよね。"
        feature_intro = f"モロー反射の表記がある{feature}" if "moro_reflex" in features else feature
        scene = (
            f"{feature_intro}なら、夜用に使う布ものを一枚に絞って考えやすく、洗い替えの枚数も決めやすくなります。"
        )
        closing = (
            "今の月齢に合うサイズと素材を選べば、"
            "夜中に着せる物を毎回選び直す時間を減らせる一枚です。"
        )
        return title, problem + scene + closing

    if attributes.product_type == "baby_bedding":
        title = "寝かしつけ前の寝具を決めたい"
        problem = "寝かしつけ前に使う寝具の置き場所が寝室とリビングに分かれていると、赤ちゃんを抱えたまま取りに戻るのは手間ですよね。"
        scene = f"{feature}なら、ねんね前に使う寝具を一つに絞り、使う場所へ準備しやすくなります。"
        closing = "本体サイズと素材を見て選べば、寝かしつけ前に寝具を探し直す手間を減らせるアイテムです。"
        return title, problem + scene + closing

    if attributes.product_type == "baby_care":
        if "nail_care" in features:
            title = "爪まわりのケアを慌てたくない"
            problem = "赤ちゃんの爪が伸びていると気づいた時に、ケア用品を探すところから始めるのは慌ただしいですよね。"
            scene = f"{feature}なら、爪まわりに使う物を一つに決めやすく、必要な時に手に取りやすくなります。"
            closing = "対象月齢や使う部位を見て選べば、毎日のケア前に道具を探す手間を減らせるアイテムです。"
        elif "nasal_aspirator" in features:
            title = "鼻まわりのケアを後回しにしない"
            problem = "鼻まわりのケアが必要な時に、使う物が決まっていないと支度だけで手間取りますよね。"
            scene = f"{feature}なら、鼻水吸引に使うノズルを一つに決めやすく、必要な時に取り出しやすくなります。"
            closing = "対応機種やノズルの長さ、お手入れ方法を見て選べば、鼻まわりのケア前に部品を探す手間を減らせるアイテムです。"
        else:
            title = "お風呂上がりのケアを迷わない"
            problem = "お風呂上がりは着替えや片づけも重なり、赤ちゃんを待たせながら保湿に使う物を探す時間があると慌ただしいですよね。"
            scene = f"{feature}なら、毎日のケアで使う物を一つに決めやすく、家族にも使うタイミングを伝えやすくなります。"
            closing = "成分や使う部位を見て選べば、お風呂上がりのケアで迷う時間を減らせるアイテムです。"
        return title, problem + scene + closing

    if attributes.product_type == "baby_sleep":
        if "night_light" in features:
            title = "夜のお世話の灯りを決めたい"
            problem = "夜のお世話で部屋を明るくし過ぎたくない時、手元の灯りを毎回探すのは手間ですよね。"
            scene = f"{feature}なら、夜に使う灯りを一つに決めやすくなります。"
            closing = "置き場所と明るさを見て選べば、夜のお世話で灯りを探す時間を減らせるアイテムです。"
        else:
            title = "夜に着る一枚を決めたい"
            problem = "夜中に布団を蹴っていないか気になる時期は、寝る前に何を着せるか毎晩迷いやすいですよね。"
            scene = f"{feature}なら、掛けものを増やし過ぎず、洗い替えも含めて夜に使う布ものを一つ決めやすくなります。"
            closing = "今の月齢に合うサイズと素材を選べば、夜に用意する布ものを減らし、寝る前に着せる物で迷う時間を減らせる一枚です。"
        return title, problem + scene + closing

    if attributes.product_type == "baby_walker_toy":
        if "standing_support_play" in features:
            title = "つかまり立ち期の遊びに"
            problem = "つかまり立ちや歩き始めの時期は、外に出にくい日でも家の中で体を使って遊べるものがあるとうれしいですよね。"
        else:
            title = "押して遊べる室内おもちゃ"
            problem = "歩き始め前後の雨の日や夕方は、リビングでも親の近くで体を使って遊べるおもちゃがひとつあるとうれしいですよね。"
        scene = f"{feature}なら、押して進む楽しさがあり、親がそばで声をかけながら見守れて、リビングで一緒に遊びやすいです。"
        closing = "外に出にくい日も親子で体を動かして遊べるので、いつもの家遊びに変化が出て、室内遊びを増やせるおもちゃです。"
        return title, problem + scene + closing

    if attributes.product_type == "soothing_plush":
        has_projector = bool(features & {"projector", "star_projection"})
        has_music = bool(features & {"music", "heartbeat_sound"})
        has_plush = "plush" in features
        title = "寝る前の準備を一つに"
        if "heartbeat_sound" in features:
            title = "絵本後の音を一つに決めたい"
            problem = "絵本の後に流す音を毎晩スマホで探していると、寝る前の声かけがその日ごとに変わりやすいですよね。"
            scene = (
                f"{feature}なら、絵本後に使う音と投影を一つの商品にまとめられます。"
            )
            closing = (
                "スマホを開いて音を探す回数を減らし、"
                "毎晩の声かけ後に出す音まわりのアイテムを一つに決められる商品です。"
            )
        elif not has_projector and has_music and has_plush:
            title = "寝る前の音を一つに"
            problem = "寝る前に寝室でメロディーを取り入れたいけれど、音のアイテムをいくつも準備して片づけるのは面倒ですよね。"
            scene = (
                f"{feature}なら、絵本後や声かけの時間に使う音のアイテムをぬいぐるみ型の一つにまとめられます。"
            )
            closing = (
                "子どもの年齢に合う商品を選べば、"
                "毎晩の就寝前に用意する物を減らせるアイテムです。"
            )
        elif has_plush:
            title = "寝る前の相棒を一つに"
            problem = "寝る前に使うものが増えると、寝室へ持っていく物を毎回選ぶのも手間になりますよね。"
            scene = f"{feature}なら、寝る前にそばへ置くアイテムを一つ決めやすくなります。"
            closing = "寝室の置き場所と本体サイズを先に決めやすく、就寝前に用意する物を減らせるアイテムです。"
        elif has_music:
            title = "寝る前の音を一つに"
            problem = "寝る前に音を取り入れたい時、毎回スマホや別の機器を探すのは手間になりますよね。"
            scene = f"{feature}なら、寝室で使う音のアイテムを一つ決めやすくなります。"
            closing = "寝室で使う音量やタイマーを先に決めやすく、就寝前に用意する機器を減らせるアイテムです。"
        else:
            title = "寝る前の準備を一つに"
            problem = "寝る前に使うものは、置き場所やサイズが合わないと毎晩の準備が増えやすいですよね。"
            scene = f"{feature}なら、寝室で使う候補を商品情報から選びやすくなります。"
            closing = "対象年齢や本体サイズを見て、寝室で使う場面を想像しながら就寝前に出す物を一つ決められるアイテムです。"
        return title, problem + scene + closing

    return None


def purchase_check_phrase(attributes: ProductAttributes) -> str:
    checks = list(attributes.purchase_checkpoints[:3])
    if attributes.product_type == "nursing_support":
        checks = ["本体サイズ"]
        if "nursing_cushion" in attributes.confirmed_features or "multi_function" in attributes.confirmed_features:
            checks.append("カバーのお手入れ")
    elif attributes.product_type == "swaddle":
        checks = ["対象サイズ", "素材"]
    elif attributes.product_type == "baby_bedding":
        checks = ["本体サイズ", "素材"]
    elif attributes.product_type == "baby_care":
        checks = ["対象月齢", "使う部位"]
    elif attributes.product_type == "baby_sleep":
        checks = ["サイズ", "素材"]
    elif attributes.product_type == "soothing_plush":
        checks = ["対象年齢"]
    elif attributes.product_type == "baby_walker_toy":
        checks = ["対象年齢", "本体サイズ"]
    return "・".join(checks[:2])


def marketing_feature_phrase(attributes: ProductAttributes) -> str:
    features = set(attributes.confirmed_features)
    if attributes.product_type == "nursing_support":
        if "multi_function" in features and "nursing_cushion" in features:
            return "授乳用として案内されたCカーブ形状のクッション"
        if "c_curve" in features and "nursing_cushion" in features:
            return "Cカーブ形状の授乳クッション"
        if "c_curve" in features and "nursing_support" in features:
            return "授乳用品として案内されたCカーブ形状のクッション"
        if "c_curve" in features:
            return "Cカーブ形状のクッション"
        if "nursing_cushion" in features or "cushion" in features:
            return "授乳用として使いやすいクッション"
        if "bottle_holder" in features:
            return "哺乳瓶ホルダーとして案内された授乳サポート"
        if "hands_free" in features:
            return "ハンズフリー授乳をサポートするアイテム"
        if "milk_support" in features:
            return "ミルク時間を支える授乳サポート"
        return "授乳時の補助として使えるアイテム"
    if attributes.product_type == "swaddle":
        if "sleeper" in features and "cotton" in features and "swaddle" in features:
            return "コットン素材のスリーパー型スワドル"
        if "sleeper" in features and "swaddle" in features:
            return "スリーパー型のスワドル"
        if "cotton" in features and ("swaddle" in features or "okurumi" in features):
            return "コットン素材のスワドル"
        return attributes.short_product_label
    if attributes.product_type == "baby_care":
        return confirmed_feature_phrase(attributes)
    if attributes.product_type == "baby_sleep":
        return confirmed_feature_phrase(attributes)
    if attributes.product_type == "baby_walker_toy":
        return confirmed_feature_phrase(attributes)
    if attributes.product_type == "soothing_plush":
        labels = []
        if "projector" in features or "star_projection" in features:
            labels.append("投影")
        if "music" in features:
            labels.append("メロディー")
        if "heartbeat_sound" in features:
            labels.append("心音")
        if "night_light" in features:
            labels.append("ライト")
        labels = list(dict.fromkeys(labels))
        if "心音" in labels:
            base = "メロディーや心音"
        elif labels[:2] == ["投影", "メロディー"]:
            base = "投影機能とメロディー"
        else:
            base = "音のしかけ"
        return base + "が入った" + attributes.short_product_label
    return confirmed_feature_phrase(attributes)


def adapt_wipes_context(text: str, attributes: ProductAttributes) -> str:
    if attributes.product_type != "wipes" or attributes.short_product_label != "手口ふき":
        return text
    replacements = [
        ("おむつ替えや食後", "食後や外出先"),
        ("食後とおむつ替え", "食後や外出先"),
        ("おむつ替えの途中", "食後の片付け中"),
        ("おむつ替えが続く時期", "食後や外出先で使う時期"),
        ("おむつ替え", "手口ふき"),
    ]
    for before, after in replacements:
        text = text.replace(before, after)
    return text


def remove_intention_phrases(text: str) -> str:
    replacements = [
        ("候補へ入れておきたいです", "候補にできます"),
        ("見ておきたいです", "見落としを減らせます"),
        ("確認したいです", "確認できます"),
        ("判断したいです", "判断しやすくなります"),
        ("考えたいです", "判断しやすくなります"),
        ("決めたいです", "決めやすくなります"),
        ("選びたいです", "選べます"),
        ("比べたいです", "比べやすくなります"),
        ("確かめたいです", "確かめやすくなります"),
        ("読んでおきたいです", "読み進めやすくなります"),
        ("押さえたいです", "押さえやすくなります"),
        ("把握しておきたいです", "把握しやすくなります"),
    ]
    for before, after in replacements:
        text = text.replace(before, after)
    return text

def listing_teaser(attributes: ProductAttributes) -> str:
    contexts = {
        "wipes": "食後やおむつ替えの補充",
        "swaddle": "夜の着替え準備",
        "nursing_support": "授乳まわり",
        "baby_bedding": "寝かしつけ前の寝具準備",
        "baby_care": "毎日のケア",
        "baby_sleep": "夜のお世話",
        "soothing_plush": "寝る前時間",
        "diaper": "毎日のおむつ補充",
        "formula": "夜の授乳ストック準備",
        "sound_blocks": "おうちで音遊び",
        "wooden_blocks": "はじめての遊び",
        "magnetic_blocks": "組み立て遊び",
        "baby_walker_toy": "押して遊ぶ室内遊び",
        "activity_cube": "手先を使う室内遊び",
        "ring_toy": "指先を使う室内遊び",
        "kids_camera": "子ども目線の思い出",
        "sleep_light": "夜のお世話準備",
        "stroller_storage": "外出荷物の整理",
    }
    label = attributes.short_product_label
    context = contexts.get(attributes.product_type, "使う場面")
    if attributes.product_type == "wipes" and label == "手口ふき":
        context = "食後や外出先"
    if attributes.product_type == "baby_walker_toy" and "standing_support_play" in attributes.confirmed_features:
        context = "つかまり立ち期の室内遊び"
    if attributes.product_type == "diaper":
        features = set(attributes.confirmed_features)
        if "diaper_storage" in features:
            context = "家のおむつ替え収納"
        elif "diaper_pouch" in features:
            context = "外出用のおむつ収納"
        elif "diaper_sheet" in features:
            context = "外出先のおむつ替え準備"
    return f"【{label}｜{context}】"


def add_listing_teaser(body: str, attributes: ProductAttributes) -> str:
    if body.startswith("【"):
        return body
    return f"{listing_teaser(attributes)}{body}"


def buyer_clear_title(title: str, attributes: ProductAttributes) -> str:
    """Keep the product category visible in a ROOM list before the hook.

    Public ROOM research showed that strong posts make the item type and the
    reader's situation understandable at a glance. The hook still carries the
    situation; this guard adds only the already-verified short product label.
    """
    label = attributes.short_product_label.strip()
    # A label merely appearing at the end does not help a first-time visitor
    # identify the item while scanning a ROOM list.  Keep the existing hook,
    # but put the verified product label before it consistently.
    if not label or title.startswith(label):
        return title
    return f"{label}｜{title}"


def strip_listing_teaser(text: str) -> str:
    return re.sub(r"^【[^】]{1,40}】", "", text)

def merge_sentences(left: str, right: str) -> str:
    left_clause = left.rstrip("。")
    right_clause = right.strip()
    for suffix, replacement in [
        ("しやすいです", "しやすく"),
        ("やすいです", "やすく"),
        ("できます", "でき"),
        ("られます", "られ"),
        ("なります", "なり"),
        ("使えます", "使え"),
        ("試せます", "試せ"),
        ("選べます", "選べ"),
        ("作れます", "作れ"),
        ("まとめられます", "まとめられ"),
        ("です", "で"),
    ]:
        if left_clause.endswith(suffix):
            left_clause = left_clause[: -len(suffix)] + replacement
            break
    else:
        left_clause = re.sub(r"ます$", "", left_clause)
    return f"{left_clause}、{right_clause}"


def ensure_sentence(value: str) -> str:
    return value if value.endswith(("。", "！", "？")) else value + "。"


def expanded_three_sentence_closing(product_type: str, checks: str) -> str:
    return {
        "wipes": "1パックの枚数・セット総数・未開封分の収納場所を確かめれば、家庭で使い切れる量を選びやすいセットです。",
        "swaddle": "サイズ・素材・着せ方を商品ページで確かめると、家庭の夜支度に合う一枚か判断しやすくなります。",
        "nursing_support": "普段の授乳場所へ置きやすいクッションなら、授乳前に姿勢を整える手間を減らせます。",
        "baby_bedding": "本体サイズ・素材・洗濯方法を商品ページで確かめると、使う部屋に合う寝具か判断しやすくなります。",
        "baby_care": "毎日のケアで使う物を一つにまとめやすく、必要な時に探す手間を減らせるアイテムです。",
        "baby_sleep": "夜に用意する布ものを増やし過ぎず、寝る前の支度をシンプルにしやすいアイテムです。",
        "soothing_plush": "寝る前の音と光を一つにまとめられるので、寝室へ持ち込む物を減らしやすいアイテムです。",
        "diaper": "適応体重・サイズ・総枚数を確かめると、サイズアウト前に使い切れる購入量か判断しやすくなります。",
        "formula": "容量と賞味期限が家庭の授乳ペースに合えば、夜に足りない不安と買い足しに焦る回数をまとめて減らせるセットです。",
        "sound_blocks": "対象年齢・パーツサイズ・セット内容を確かめると、家庭で扱いやすい音遊びのおもちゃか判断しやすくなります。",
        "wooden_blocks": "対象年齢・パーツサイズ・収納方法を確かめると、家庭で片づけまで続けやすい積み木か判断しやすくなります。",
        "magnetic_blocks": "雨の日のおうち時間に、親子で一緒に遊びやすい知育おもちゃです。",
        "baby_walker_toy": "リビングで出しやすいサイズなら、親がそばで見守りながらおうち時間の遊び方を増やせるおもちゃです。",
        "activity_cube": "置き場所を取りにくい一台で複数の遊びを用意でき、室内で子どもの手先遊びを増やせるおもちゃです。",
        "ring_toy": "対象年齢・パーツ数・パーツサイズを確かめると、今の遊び方に合うリング玩具か判断しやすくなります。",
        "kids_camera": "対象年齢・充電方式・写真の保存方法を確認すれば、外出へ持ち出す前の準備で迷う時間を減らせるカメラです。",
        "sleep_light": "音量調整・明るさ・電源方式を確かめると、寝室や授乳場所で使う機能を選び分けやすくなります。",
        "stroller_storage": "よく使う荷物の定位置を作れるので、外出中にバッグを探る時間を減らしやすい収納です。",
    }[product_type]


def add_distinctive_product_detail(
    title: str,
    body: str,
    scored: ScoredProduct,
    attributes: ProductAttributes,
    *,
    attempt: int = DISTINCTIVE_REWRITE_START,
    required_terms: tuple[str, ...] = (),
) -> tuple[str, str]:
    sentences = split_sentences(body)
    if len(sentences) < 3:
        return title, body
    teaser = distinct_listing_teaser(listing_teaser(attributes), scored)
    feature = confirmed_feature_phrase(attributes)
    label = attributes.short_product_label
    required_term = next((term for term in required_terms if term), label)
    variant = max(0, attempt - DISTINCTIVE_REWRITE_START)
    product_specific = product_specific_distinctive_copy(
        attributes,
        teaser=teaser,
        variant=variant,
        required_terms=required_terms,
    )
    if product_specific is not None:
        return product_specific
    default_use_cases = {
        "wipes": "おむつ替え",
        "swaddle": "夜の準備",
        "nursing_support": "授乳準備",
        "baby_bedding": "寝かしつけ前",
        "baby_care": "毎日のケア",
        "baby_sleep": "夜の準備",
        "soothing_plush": "寝る前",
        "diaper": "おむつ替え",
        "formula": "授乳準備",
        "baby_walker_toy": "室内遊び",
        "activity_cube": "手先遊び",
        "sleep_light": "夜のお世話",
    }
    use_case = next(
        (value for value in attributes.confirmed_use_cases if value),
        default_use_cases.get(attributes.product_type, required_term),
    )
    # Verb-like use-case labels are useful metadata, but appending the case
    # particle "で" to them creates copy such as "押して遊ぶで使う".  Use a
    # natural noun phrase in sentence templates for these product types.
    if attributes.product_type in {"baby_walker_toy", "activity_cube", "baby_bedding"}:
        use_case = default_use_cases[attributes.product_type]
    required_phrase = "と".join(term for term in required_terms if term)
    scene_term = required_phrase or (required_term if required_term != label else use_case)
    if attributes.product_type == "baby_walker_toy" and scene_term in {"押して", "押して遊ぶ"}:
        scene_term = "押して遊ぶ場面"
    if attributes.product_type == "activity_cube" and scene_term in {"型はめ", "ルーピング"}:
        scene_term = f"{scene_term}遊び"
    if attributes.product_type == "magnetic_blocks" and scene_term in {
        "組み立て", "平面", "立体", "形", "磁石", "マグネット"
    }:
        scene_term = f"{scene_term}遊び"
    if attributes.product_type == "wipes":
        scene_term = {
            "残り": "残り枚数",
            "備え": "備える場面",
            "消耗品": "消耗品の補充",
        }.get(scene_term, scene_term)
    checkpoint = next(
        (value for value in attributes.purchase_checkpoints if value),
        "使う場所",
    )
    title = distinct_title(
        scored,
        attributes,
        variant=variant,
        required_term=scene_term,
        use_case=use_case,
    )
    openings = [
        f"{teaser}{label}を{use_case}へ取り入れるなら、{feature}が暮らしに合うか気になりますよね",
        f"{teaser}{label}は、{scene_term}で使う時間と{checkpoint}を一緒に思い浮かべたいですよね",
        f"{teaser}{feature}という商品情報があれば、毎日の{use_case}へ無理なく足せるか具体的に考えられます",
        f"{teaser}{label}を暮らしへ足す前に、{checkpoint}と使う頻度を整理したくなりますよね",
        f"{teaser}{use_case}で使う候補を探すときは、{feature}が必要な動きに合うかが気になりますよね",
        f"{teaser}{scene_term}に使う{label}だからこそ、{checkpoint}を先に押さえておきたいですよね",
        f"{teaser}{feature}を選べる{label}なら、{use_case}のどこへ置くかまで具体的に想像できます",
        f"{teaser}{feature}を選べる{label}は、{scene_term}の準備を増やし過ぎないか見極めたいですよね",
        f"{teaser}{checkpoint}で迷いやすい{label}は、{feature}を見ると使う場面が浮かびます",
        f"{teaser}{use_case}を整えたい日は、{label}が毎日の流れへ合うか気になりますよね",
        f"{teaser}{scene_term}の道具を増やすなら、{feature}を使う場所と{checkpoint}を先に決めたいですよね",
        f"{teaser}{feature}は、{use_case}で探す手間を減らせる置き方まで考えたくなります",
    ]
    if attributes.product_type == "baby_sleep":
        openings = [
            f"{teaser}夜中に布団を蹴っていないか気になると、寝る前に何を着せるか迷いますよね",
            f"{teaser}寝冷えが気になる夜は、布団だけでよいか着せる物にも迷いますよね",
            f"{teaser}夜に布団を掛け直すことが続くと、寝る前に着せる一枚を決めたくなりますよね",
            f"{teaser}夜中の寝冷えが気になる時期は、布団と着せる物の組み合わせに迷いますよね",
            f"{teaser}寝る前に布団と着せる物を毎晩選び直すと、夜の支度に迷いやすいですよね",
            f"{teaser}夜の洗い替えが足りないと、寝る前に着せる物を探す時間が増えますよね",
            f"{teaser}布団を蹴る夜が続くと、寝冷えを考えて何を着せるか迷いますよね",
            f"{teaser}夜中のお世話に備える時、布団に加えて着せる物まで決めるのは手間ですよね",
            f"{teaser}寝冷えを考えて着せる物を増やすと、夜に使う布団との組み合わせに迷いますよね",
            f"{teaser}夜の布団と着せる一枚を毎回選び直すと、寝る前の支度が長くなりますよね",
            f"{teaser}夜中に布団を直す回数が増えると、寝冷えに備えて何を着せるか迷いますよね",
            f"{teaser}寝る前に着せる物が決まらないと、夜の布団準備まで慌ただしくなりますよね",
        ]
    middles = [
        f"{feature}なら、{use_case}で必要な時に取り出し、使い終わった後に戻す流れをまとめやすくなります",
        f"{checkpoint}を決めておくと、{label}を準備してから片づけるまでの動きを家族で共有しやすくなります",
        f"{checkpoint}を手がかりにすれば、{scene_term}で使う物を増やし過ぎずに整えられます",
        f"{use_case}で使う場所へ{label}の定位置を作ると、必要な時に探す手間を抑えやすくなります",
        f"{feature}を使う場面が決まれば、{checkpoint}で迷う時間を減らして準備へ移りやすくなります",
        f"{label}の置き場所を{use_case}の近くに決めると、準備と片づけを同じ流れにまとめられます",
        f"{scene_term}を手がかりに、{use_case}で使う{feature}を整理すると、家族も必要な時に手に取りやすくなります",
        f"{label}でも、{checkpoint}と{use_case}が合えば毎日の動線へ無理なく置きやすくなります",
    ]
    # The price used to make otherwise-similar rewrites look different.  Use
    # a product-URL-seeded, reader-facing scene instead, so prices never
    # become the list cue while repeated candidates still get distinct copy.
    replenish_scenes = [
        "使う前に", "外出前に", "週末の補充時に", "収納へ戻す時に",
        "残量を確認する時に", "家族で分ける時に", "買い足し前に", "次を開ける時に",
    ]
    sleep_scenes = [
        "寝る前に", "夜の支度中に", "お風呂上がりに", "洗濯物を戻す時に",
        "着替えを用意する時に", "寝室を整える時に", "洗い替えを選ぶ時に", "家族で夜支度をする時に",
    ]
    sleep_light_scenes = [
        "夜の授乳前に", "寝かしつけ前に", "寝室を整える時に", "音量を決める時に",
        "充電後に", "手元の灯りを使う時に", "置き場所を決める時に", "夜のお世話前に",
    ]
    play_scenes = [
        "遊び始める前に", "片づける時に", "雨の日に", "休日の遊び時間に",
        "リビングへ出す時に", "親子で遊ぶ時に", "遊び方を変える時に", "収納へ戻す時に",
    ]
    care_scenes = [
        "お風呂上がりに", "朝のケア時に", "外出前に", "週末の補充時に",
        "着替えの後に", "家族で使う時に", "置き場所へ戻す時に", "使う量を確認する時に",
    ]
    nursing_scenes = [
        "授乳前に", "夜の支度中に", "外出前に", "授乳後に",
        "哺乳瓶を用意する時に", "家族で交代する時に", "洗浄後に", "授乳場所を整える時に",
    ]
    stroller_scenes = [
        "外出前に", "帰宅後に", "荷物を戻す時に", "週末の補充時に",
        "ベビーカーへ付ける時に", "持ち物を分ける時に", "玄関で支度する時に", "家族で外出する時に",
    ]
    camera_scenes = [
        "外出前に", "帰宅後に", "写真を見返す時に", "休日の外出時に",
        "親子で撮る時に", "充電する時に", "保存先を決める時に", "持ち物へ入れる時に",
    ]
    closing_scenes_by_type = {
        "wipes": replenish_scenes,
        "diaper": replenish_scenes,
        "formula": replenish_scenes,
        "baby_care": care_scenes,
        "nursing_support": nursing_scenes,
        "stroller_storage": stroller_scenes,
        "swaddle": sleep_scenes,
        "baby_bedding": sleep_scenes,
        "baby_sleep": sleep_scenes,
        "soothing_plush": sleep_scenes,
        "sleep_light": sleep_light_scenes,
        "sound_blocks": play_scenes,
        "wooden_blocks": play_scenes,
        "magnetic_blocks": play_scenes,
        "baby_walker_toy": play_scenes,
        "activity_cube": play_scenes,
        "ring_toy": play_scenes,
        "kids_camera": camera_scenes,
    }
    closing_scenes = closing_scenes_by_type.get(
        attributes.product_type,
        ["朝の支度中に", "帰宅後に", "外出前に", "家族で使う時に"],
    )
    closing_angles = [
        "使う順番を思い浮かべて",
        "置く場所になじむか考えて",
        "手に取る瞬間を想像して",
        "準備の流れへ合わせて",
        "使った後まで見通して",
        "次の支度を考えて",
        "家の動線に合わせて",
        "使う回数を見ながら",
    ]
    opening = ensure_sentence(openings[variant % len(openings)])
    middle = ensure_sentence(middles[(variant * 3 + variant // len(openings)) % len(middles)])
    closing_index = (stable_index(scored.product.url, len(closing_scenes) * len(closing_angles)) + variant) % (len(closing_scenes) * len(closing_angles))
    closing_scene = closing_scenes[closing_index % len(closing_scenes)]
    closing_angle = closing_angles[closing_index // len(closing_scenes)]
    closing_benefits = {
        "wipes": "必要な時に探す手間を減らせるアイテムです",
        "swaddle": "夜の準備で迷う時間を減らせる一枚です",
        "nursing_support": "授乳前の支度を整えられるアイテムです",
        "baby_bedding": "寝かしつけ前の準備を整えられるアイテムです",
        "baby_care": "毎日のケアで探す手間を減らせるアイテムです",
        "baby_sleep": "夜のお世話で探す時間を減らせるアイテムです",
        "soothing_plush": "寝る前に用意する物を減らせるアイテムです",
        "diaper": "おむつ替えで探す手間を減らせるアイテムです",
        "formula": "夜の授乳準備を整えられるアイテムです",
        "sound_blocks": "おうち遊びを増やせるおもちゃです",
        "wooden_blocks": "親子の遊びを増やせるおもちゃです",
        "magnetic_blocks": "室内遊びを増やせるおもちゃです",
        "baby_walker_toy": "室内遊びを増やせるおもちゃです",
        "activity_cube": "手先を使う遊びを増やせるおもちゃです",
        "ring_toy": "指先を使う遊びを増やせるおもちゃです",
        "kids_camera": "子ども目線の思い出を増やせるアイテムです",
        "sleep_light": "夜のお世話で探す時間を減らせるアイテムです",
        "stroller_storage": "外出前の荷物を整えられるアイテムです",
    }
    closing = ensure_sentence(
        f"{closing_scene}{closing_angle}{label}を選べば、"
        f"{closing_benefits[attributes.product_type]}"
    )
    # For heavily repeated candidates, switch to a four-sentence structure.
    # This keeps the copy readable while genuinely changing its construction,
    # instead of exposing a changing price merely to evade similarity checks.
    if variant >= 48:
        direct_openings = [
            f"{teaser}{scene_term}で{label}を探す時間が重なると、支度が慌ただしくなりがちです",
            f"{teaser}{use_case}の前に{label}の置き場所が決まらないと、必要な時に手が止まりやすくなります",
            f"{teaser}{scene_term}で使う物が増えると、{label}を選ぶ順番にも迷いやすくなります",
            f"{teaser}{use_case}で{label}を取り出す場面を想像できないと、準備が後回しになりがちです",
            f"{teaser}{scene_term}の支度で{label}が見つからないと、家族の流れが慌ただしくなります",
            f"{teaser}{use_case}に使う{label}は、忙しい時ほど置き場と使い方を決めておきたいです",
            f"{teaser}{scene_term}で必要な{label}は、使う時間を決めると選びやすくなります",
            f"{teaser}{use_case}に{label}を足すなら、毎日の動きに合うか先に整理できます",
        ]
        if attributes.product_type == "baby_sleep":
            direct_openings = [
                f"{teaser}夜中に布団を蹴っていないか気になると、寝る前に何を着せるか迷いますよね",
                f"{teaser}寝冷えが気になる夜は、布団だけでよいか着せる物にも迷いますよね",
                f"{teaser}夜に布団を掛け直すことが続くと、寝る前に着せる一枚を決めたくなりますよね",
                f"{teaser}夜中の寝冷えが気になる時期は、布団と着せる物の組み合わせに迷いますよね",
                f"{teaser}寝る前に布団と着せる物を毎晩選び直すと、夜の支度に迷いやすいですよね",
                f"{teaser}夜の洗い替えが足りないと、寝る前に着せる物を探す時間が増えますよね",
                f"{teaser}布団を蹴る夜が続くと、寝冷えを考えて何を着せるか迷いますよね",
                f"{teaser}夜中のお世話に備える時、布団に加えて着せる物まで決めるのは手間ですよね",
            ]
        direct_opening = ensure_sentence(direct_openings[variant % len(direct_openings)])
        direct_middle = ensure_sentence(
            f"{feature}という商品情報から、{checkpoint}が暮らしに合うか商品ページで見分けられます"
        )
        direct_scene = ensure_sentence(
            f"{closing_scene}{closing_angle}選ぶと、使う流れを想像しやすくなります"
        )
        candidate_sentences = [
            direct_opening,
            direct_middle,
            direct_scene,
            ensure_sentence(closing_benefits[attributes.product_type]),
        ]
    elif variant >= 32:
        scene_sentence = ensure_sentence(
            f"{closing_scene}{closing_angle}{label}を選べます"
        )
        candidate_sentences = [
            opening,
            middle,
            scene_sentence,
            ensure_sentence(closing_benefits[attributes.product_type]),
        ]
    else:
        candidate_sentences = [opening, middle, closing]
    candidate_body = "".join(candidate_sentences)
    if 150 <= len(candidate_body) <= 260:
        return title, candidate_body
    return title, distinct_listing_teaser(body, scored)


def product_specific_distinctive_copy(
    attributes: ProductAttributes,
    *,
    teaser: str,
    variant: int,
    required_terms: tuple[str, ...] = (),
) -> tuple[str, str] | None:
    """Use product-specific pain, evidence, scene, and value rewrites."""
    product_type = attributes.product_type
    features = set(attributes.confirmed_features)
    label = attributes.short_product_label
    feature = confirmed_feature_phrase(attributes)

    if product_type == "wipes":
        use_scene = "食後" if label == "手口ふき" else "おむつ替え"
        titles = [
            f"{label}｜家用と外出用を分ける",
            f"{label}｜枚数とパック数を確認",
            f"{label}｜{use_scene}の分まで備える",
            f"{label}｜小分けの補充を整える",
            f"{label}｜残りを把握しやすく",
            f"{label}｜収納場所に合う量を選ぶ",
            f"{label}｜買い足し時期をそろえる",
            f"{label}｜消耗品の在庫を整える",
        ]
        pains = [
            f"{use_scene}や外出先で使う{label}は、家用と持ち出す分を分けると残量が見えにくくなりますよね。",
            f"小分けの{label}は、1パックの枚数だけでなく一度に届く個数も気になりますよね。",
            f"{use_scene}で{label}を使う家庭では、外出用を分けた後に家へ残る量も気になりますよね。",
            f"外出先でも{label}を使うなら、家・玄関・持ち物へ何パックずつ置くか迷いますよね。",
            f"残り少ない日に{label}を買い足すと、収納できる量まで落ち着いて考えにくいですよね。",
            f"毎日使う{label}は、家族それぞれの使用量が違うと補充時期を合わせにくいですよね。",
            f"まとめて届く{label}は便利でも、開封前のパックを置く場所が足りるか気になりますよね。",
            f"{label}の買い足し回数を減らす時は、使い切るまでの期間と保管場所が気になりますよね。",
        ]
        wipe_contexts = [
            "補充量を考える時、",
            "外出用を分ける前、",
            "収納場所を決める時、",
            "買い足す量を決める時、",
            "未開封分をしまう前、",
            "家族の使用量を見直す時、",
            "残りのパック数を数える時、",
            "次の注文前、",
        ]
        wipe_inventory_steps = [
            "シート寸法と1パックの枚数は、家庭で使う場面に合う物を選ぶ基準になります。",
            "セット総数と未開封時のパッケージ寸法は、収納に必要な場所の目安になります。",
            "成分表示と使用できる部位は、家族で使い分ける時の基準になります。",
            "1パックの大きさと枚数は、外出用に分ける量の目安になります。",
            "取り出し口やふたの仕様は、普段使う場所へ置く形を選ぶ基準になります。",
            "1パックの枚数とセット総数は、買い足すまでの消費期間の目安になります。",
            "普段の使用量と一度に届く個数は、補充する間隔の目安になります。",
            "未開封時の大きさとセット総数は、置き場所ごとの保管量の目安になります。",
        ]
        scenes = [
            f"{feature}なら、家用と外出用に分ける量をパック単位で具体的に考えられます。",
            f"{feature}なら、1回に持ち出す枚数と次の補充時期を家族で共有しやすくなります。",
            f"{feature}なら、{use_scene}用と外出用へ分けた後の残りを把握しやすくなります。",
            f"{feature}なら、家と持ち物へ置く数を先に決めて、補充の単位をそろえられます。",
            f"{feature}なら、普段の使用量から次に開けるパックと買い足す時期を見通せます。",
            f"{feature}なら、収納場所ごとに置く数を分け、未開封分の残りも管理しやすくなります。",
            f"{feature}なら、家族が使う場所へ必要な分を置き、補充する場所を決められます。",
            f"{feature}なら、届く総量と毎週の使用量を照らし合わせて在庫を整えられます。",
        ]
        closings = [
            f"1パックの枚数・セット総数・未開封分の収納場所を確かめれば、{label}を家庭で使い切れる量か判断しやすいセットです。",
            "1パックの枚数とセット総数を商品ページで確認すると、家庭で使い切れる量を判断しやすいセットです。",
            f"1パックの枚数とセット総数を確かめ、{use_scene}用と外出用へ分けても使い切れる量なら無理なく備えられるセットです。",
            "パック数と収納場所を商品ページで確認すると、使う場所ごとの補充を組み立てやすいセットです。",
            "セット総数と未開封分の収納場所を確かめれば、次のパックを置ける量だけ備えやすいセットです。",
            "家族の使用量・セット総数・買い足す間隔を照らし合わせれば、補充量を決めやすいセットです。",
            "届く個数と収納場所を先に照らし合わせると、収納を圧迫せず必要量を備えられるセットです。",
            "毎週の使用量とセット総数が合えば、買い忘れを防ぎながら未開封分を管理しやすいセットです。",
        ]
    elif product_type == "formula":
        age_match = re.search(r"(\d+)\s*[〜~～\-]\s*(\d+)\s*(?:カ月|か月|ヶ月)", attributes.source_product_text)
        age_text = f"{age_match.group(1)}〜{age_match.group(2)}カ月" if age_match else ""
        age_prefix = f"{age_text}向け・" if age_text else ""
        pack_text, package_facts = formula_package_facts(attributes)
        package_count = next((value for value in package_facts if value.endswith(("袋", "缶", "本"))), "")
        package_unit = (
            "袋" if package_count.endswith("袋")
            else "缶" if package_count.endswith("缶")
            else "本" if package_count.endswith("本")
            else "容器"
        )
        formula_kind = (
            "液体ミルク" if "liquid" in features
            else "フォローアップミルク" if "follow_up" in features
            else "粉ミルク"
        )
        overseas = "海外通販" in attributes.source_product_text
        titles = [
            f"{label}｜{age_text}向けを補充" if age_text else f"{label}｜容量と本数を確認",
            f"{label}｜{pack_text}を使い切れる量か確認",
            f"{label}｜授乳ペースに合うまとめ買い",
            f"{label}｜賞味期限までに使える量を選ぶ",
        ]
        pains = [
            f"{formula_kind}をまとめて買う時は、月齢に合う種類か、賞味期限までに使い切れる量かが気になりますよね。",
            f"夜間授乳のストックは安心ですが、{package_unit}数が多いほど保管場所と使い切る時期まで考えたいですよね。",
            "買い足し回数を減らしたくても、今の授乳ペースより多過ぎるセットは避けたいですよね。",
            f"未開封の{formula_kind}を備えるなら、次の段階へ移る前に使える量か確かめたいですよね。",
        ]
        scenes = [
            f"{age_prefix}{pack_text}の{formula_kind}なら、授乳回数から一{package_unit}を使う日数と未開封分の残りを家族で見通せます。",
            f"{pack_text}のセットなら、一{package_unit}を開けた日と次の買い足し時期をそろえて管理しやすくなります。",
            f"{age_text}向けの{feature}なら、今の授乳量を基に必要な{package_unit}数を具体的に考えられます。" if age_text else f"{feature}なら、今の授乳量を基に必要な{package_unit}数を具体的に考えられます。",
            f"{pack_text}というセット内容が分かるので、普段の消費量と収納場所に収まるかを先に比べられます。",
        ]
        delivery_check = "海外通販の配送条件・" if overseas else ""
        closings = [
            f"{delivery_check}賞味期限・保管場所を商品ページで確認し、家庭の消費ペースに合えば夜の授乳ストックをまとめて整えられるセットです。",
            f"{delivery_check}賞味期限と一度に届く{package_unit}数を確かめれば、使い切れる量だけを備えて買い足し忘れを減らせます。",
            f"対象月齢・賞味期限・保管場所を確認すれば、次の段階へ移る時期までに使える量を選びやすいセットです。",
            f"{delivery_check}賞味期限までに消費できる量なら、夜間授乳用の未開封ストックを切らしにくくできます。",
        ]
    elif product_type == "sound_blocks":
        quantity = next(
            (value for value in attributes.confirmed_quantity_features if value.endswith(("ピース", "パーツ"))),
            "",
        )
        titles = [
            f"{label}｜音と形で遊ぶ",
            f"{label}｜対象年齢を確かめる",
            f"{label}｜{quantity or 'セット内容'}を確認",
            f"{label}｜親子の音遊びに",
        ]
        pains = [
            "家の中で遊ぶ時間が長い日は、積むだけでなく音にも反応できるおもちゃがあると遊び方を変えやすいですよね。",
            "音が鳴る積み木を選ぶ時は、対象年齢とパーツの大きさが今の遊び方に合うか気になりますよね。",
            f"{quantity or '複数'}のパーツがあるおもちゃは、遊び方だけでなく片づける場所も先に考えたいですよね。",
            "親子で音遊びをするなら、どのパーツが鳴るか商品情報から確かめておきたいですよね。",
        ]
        scenes = [
            f"{feature}なら、振って音を聞く遊びと、積んだり並べたりする遊びを切り替えられます。",
            f"{feature}なら、商品ページの対象年齢とパーツサイズを見ながら、家庭で扱えるか判断できます。",
            f"{feature}なら、セット内容を確認して、遊ぶ場所と収納場所を具体的に考えられます。",
            f"{feature}なら、音が鳴るパーツの仕様を確かめながら、親子で振る・積む遊びを始められます。",
        ]
        closings = [
            "対象年齢・パーツサイズ・セット内容を確かめると、家庭で扱いやすい音遊びのおもちゃか判断しやすくなります。",
            "音が鳴るパーツの数と収納方法を確認すると、遊び終わった後まで無理なく扱える積み木か判断しやすくなります。",
            "対象年齢とパーツの大きさを商品ページで確認すると、今の手先遊びに合う積み木か判断しやすくなります。",
            "セット内容と名入れの有無を確かめると、家庭で使う目的に合う積み木か具体的に比較できます。",
        ]
    elif product_type == "magnetic_blocks":
        quantity = next(iter(attributes.confirmed_quantity_features), "")
        quantity_text = quantity or "セット内容"
        has_3d = "立体" in attributes.source_product_text
        build_angle = "平面から立体へ組み立てる" if has_3d else "色と形を組み合わせる"
        titles = [
            f"{label}｜{build_angle}",
            f"{label}｜{quantity_text}で形を作る",
            f"{label}｜雨の日の組み立て遊び",
            f"{label}｜親子で形を組み立てる",
        ]
        pains = [
            "雨の日や夕方に同じ遊びが続くと、家の中で次に出すおもちゃを考える時間が増えますよね。",
            "ブロック遊びに慣れてくると、組み合わせを変えて別の形にも挑戦できるおもちゃが気になりますよね。",
            "雨の日に出す細かなパーツのおもちゃは、遊び方だけでなく対象年齢や片づける場所も気になりますよね。",
            "親子で一緒に遊ぶなら、色や形を相談しながら組み立てられるセットに目が向きますよね。",
        ]
        scenes = [
            f"{feature}なら、色や形を選び、子どもの反応に合わせて作る物を広げられます。",
            f"{feature}なら、色や形を選びながら親子で一つの作品を組み立てられます。",
            f"{feature}なら、雨の日も組み合わせを試しながら親子で手を動かして遊べます。",
            f"{feature}なら、磁石でつながるピースを組み替え、色や形を相談しながら親子で作品を作れます。",
        ]
        if has_3d:
            scenes[0] = f"{feature}なら、床へ並べる平面遊びから立体の形作りへ、子どもの反応に合わせて取り組めます。"
        closings = [
            f"購入前に対象年齢・パーツサイズ・{quantity_text}の内訳を確かめると、子どもの今の遊び方に合う形作りを増やせるおもちゃです。",
            f"対象年齢・{quantity_text}の内訳・収納場所を商品ページで比べると、遊び終わった後まで親子で取り組める形を増やせるおもちゃです。",
            "購入前に対象年齢・パーツの大きさ・手持ちブロックとの互換性を確認し、家庭で無理なく試せる組み立て遊びを増やせるおもちゃです。",
            f"対象年齢・パーツサイズ・{quantity_text}の内訳を確認すれば、親子で作りたい形を少しずつ増やせるおもちゃです。",
        ]
    elif product_type == "baby_care" and len(baby_care_set_components(attributes)) >= 2 and "セット" in attributes.source_product_name:
        care_components = baby_care_set_components(attributes)
        care_count = len(care_components)
        care_list = "、".join(care_components)
        care_step = "保湿" if "moisturizing" in features else "ローション・ミルク"
        explicit_count = re.search(r"(\d+)\s*品", attributes.source_product_name)
        count_text = f"{explicit_count.group(1)}品" if explicit_count else "セット内容"
        titles = [
            f"{label}｜洗う物と{care_step}をまとめる",
            f"{label}｜お風呂上がりまで一式で",
            f"{label}｜ヘア・ボディ・{care_step}をまとめる",
            f"{label}｜入浴後のケアを一式に",
        ]
        pains = [
            f"赤ちゃんの入浴用品を初めてそろえる時は、髪・体・{care_step}に何を用意するか迷いますよね。",
            f"お風呂で使う物と着替え前の{care_step}が別々だと、家族へ手順を伝える時にも確認が増えますよね。",
            "ベビーケア用品を買い足すなら、セットの中身が毎日の入浴の流れに合うか気になりますよね。",
            f"お風呂上がりは着替えも重なるので、洗う物から{care_step}まで使う順番をそろえたいですよね。",
        ]
        scenes = [
            f"{feature}なら、{care_list}を同じシリーズでそろえられます。",
            f"{feature}なら、浴室と着替え場所に置く物を分け、家族で使う順番を共有しやすくなります。",
            f"{feature}なら、{care_list}を一度に準備できます。",
            f"{feature}なら、お風呂から着替え前までに使うケア用品のセット内容をまとめて把握できます。",
        ]
        closings = [
            f"{count_text}の使う順番を一つにまとめられるので、入浴前後に別々のケア用品を探す手間を減らせるセットです。",
            f"明記された{count_text}を一度に準備でき、家族で交代する時もお風呂の支度をまとめられるセットです。",
            f"{count_text}ごとの使う部位と容量を確かめられ、初めての入浴用品を選ぶ手間を減らせるセットです。",
            f"対象年齢と成分表示を先に見られ、お風呂から{care_step}までに用意する物を一式にまとめられるセットです。",
        ]
    elif product_type == "baby_care" and "nasal_aspirator" in features:
        titles = [
            f"{label}｜対応機種を確かめる",
            f"{label}｜交換用ノズルを選ぶ",
            f"{label}｜長さと手入れ方法を見る",
            f"{label}｜鼻吸い器に合うか確認",
        ]
        pains = [
            "鼻吸い器の交換ノズルは、手元の本体に取り付けられる型か分かりにくいことがありますよね。",
            "交換用ノズルを選ぶ時は、対応機種だけでなく長さや形も今の使い方に合うか気になりますよね。",
            "毎回手入れする部品は、洗い方と乾かし方を商品情報で把握しておきたいですよね。",
            "同じメーカーの鼻吸い器でも、型番によって使える交換部品が違わないか気になりますよね。",
        ]
        scenes = [
            f"{feature}なら、対応機種と型番を照らし合わせて交換用ノズルを選べます。",
            f"{feature}なら、ノズルの長さと形を商品ページで把握し、手元の本体に合うか判断できます。",
            f"{feature}なら、使用後の洗い方まで確認し、普段の手入れに取り入れられます。",
            f"{feature}なら、本体名と対応機種を照らし合わせ、買い直す迷いを減らせます。",
        ]
        closings = [
            "対応機種・ノズルの長さ・お手入れ方法を確認すると、合わない交換部品を選ぶ迷いを減らせるアイテムです。",
            "本体の型番と取り付け方法を照らし合わせると、交換時に部品を探し直す手間を減らせるアイテムです。",
            "洗い方と乾かし方を商品ページで把握でき、使用後の手入れ手順をそろえられるアイテムです。",
            "対応する本体と部品の形を先に確認でき、買い直しを避けやすくなるアイテムです。",
        ]
    elif product_type == "kids_camera":
        functions = []
        for key, value in [
            ("game_free", "ゲームなし"),
            ("smartphone_transfer", "スマホ転送"),
            ("sd_card_supported", "SDカード対応"),
            ("sd_card_included", "SDカード付き"),
            ("usb_charge", "USB充電"),
        ]:
            if key in features:
                functions.append(value)
        function_text = "・".join(functions[:3]) or "写真撮影"
        titles = [
            f"{label}｜子ども目線の写真を残す",
            f"{label}｜散歩の景色を子どもが撮る",
            f"{label}｜撮った写真を親子で見る",
            f"{label}｜{function_text}で写真遊び",
        ]
        pains = [
            "散歩や旅行で子どもが何を見ているか、親のスマホ写真だけでは気づきにくいことがありますよね。",
            "子どもが写真を撮りたがるたびに大人のスマホを渡すのは、操作や持ち歩きも気になりますよね。",
            "外出先の思い出を残すなら、親が撮る写真だけでなく子ども自身が選んだ景色も見てみたいですよね。",
            "写真遊びを始める時は、子どもが扱える機能と撮った後の保存方法が分かりにくいことがありますよね。",
        ]
        scenes = [
            f"{feature}なら、散歩や旅行で子どもが気になった景色を自分で撮る遊びを始められます。",
            f"{feature}なら、大人のスマホを渡さずに、散歩や旅行で子どもが自分でシャッターを押して写真を撮れます。",
            f"{feature}なら、散歩や旅行の外出先で子どもが気になった物へ自分でカメラを向けられます。",
            f"{feature}なら、散歩や旅行へ持ち出し、子どもが撮った景色を親子で見返せます。",
        ]
        closings = [
            "購入前に対象年齢・充電方式・SDカードの付属有無を確認すると、持ち出す前の充電や保存で迷う時間を減らせるカメラです。",
            "対象年齢・充電方式・撮った写真の転送方法を商品ページで確かめると、散歩や旅行へ持ち出す前の準備時間を減らせるカメラです。",
            "購入前に対象年齢・ゲーム機能の有無・SDカードの仕様を確認すると、写真遊びに必要な機能を選ぶ迷いを減らせるカメラです。",
            "対象年齢・充電方式・写真の保存方法を確認すれば、撮影から親子で見返すまでに迷う時間を減らせるカメラです。",
        ]
    elif product_type == "sleep_light":
        titles = [
            f"{label}｜音量と明るさを確認",
            f"{label}｜夜のお世話で使う",
            f"{label}｜電源方式まで確かめる",
            f"{label}｜音と灯りを一台で",
        ]
        pains = [
            "夜のお世話で音と灯りを使うなら、寝室に合う音量と明るさへ調整できるか気になりますよね。",
            "授乳や寝室で使うライトは、必要な場所へ置ける電源方式か気になりますよね。",
            "ホワイトノイズと灯りを一台で使う時は、それぞれを個別に調整できるか商品情報を見たいですよね。",
            "夜に使う機器は、充電方法とコードレスで使える時間が家庭の動線に合うか気になりますよね。",
        ]
        scenes = [
            f"{feature}なら、音と灯りを別々に操作できるかを把握し、夜に使う機能を選べます。",
            f"{feature}なら、授乳場所と寝室のどちらで使うかを考えながら置き場所を決められます。",
            f"{feature}なら、ホワイトノイズとライトの操作方法を確認して、必要な機能を使い分けられます。",
            f"{feature}なら、充電方法とコードレス利用の仕様を確かめて、夜の動線に合うか判断できます。",
        ]
        closings = [
            "充電方法・連続使用時間・置き場所を確かめると、夜のお世話の動線に合う一台か判断しやすくなります。",
            "操作方法と置き場所を先に確認すると、夜のお世話で必要な機能へ切り替えやすくなります。",
            "充電方法と連続使用時間を商品ページで確かめると、家庭の夜支度に合うライトか判断しやすくなります。",
            "音と灯りを別々に調整できるか確認すると、夜に使う場面へ合う一台か具体的に比較できます。",
        ]
    elif product_type == "activity_cube":
        actions = [
            value
            for key, value in [("shape_sorter", "型はめ"), ("looping", "ルーピング")]
            if key in features
        ]
        action_text = "や".join(actions) or "手先遊び"
        titles = [f"{label}｜{action_text}を一台で", f"{label}｜手先遊びを切り替える", f"{label}｜おうち遊びを増やす", f"{label}｜親子で{action_text}遊び"]
        pains = [
            "雨の日や夕方に同じ遊びが続くと、次に出すおもちゃを考える時間が増えますよね。",
            "家の中で遊ぶ時間が長い日は、子どもの反応を見ながら手先遊びを切り替えたくなりますよね。",
            "おうち遊びの道具が増えるほど、遊ぶたびに別のおもちゃを出すのは手間になりますよね。",
            "親子で手を動かして遊びたい時、ひとつの遊びだけでは間が持たない日もありますよね。",
        ]
        scenes = [
            f"{feature}なら、子どもの反応に合わせて一台の遊び方を切り替えられます。",
            f"{feature}なら、親が隣で声をかけながら{action_text}に取り組めます。",
            f"{feature}なら、一台を出したまま{action_text}へ遊びを切り替えられます。",
            f"{feature}なら、雨の日も{action_text}で親子一緒に手を動かせます。",
        ]
        closings = [
            "別のおもちゃを次々に出さずに手先遊びを変えられるので、おうち時間の遊び方を増やせるおもちゃです。",
            "遊びを選び直す時間を短くしながら、親子で手先を使う時間を増やせるおもちゃです。",
            "その日の反応に合わせて遊びを切り替えられるので、室内遊びの選択肢を増やせるおもちゃです。",
            "一台で始める遊びを変えやすく、雨の日に親子で過ごす時間を組み立てやすいおもちゃです。",
        ]
    elif product_type == "baby_walker_toy":
        titles = [f"{label}｜押して遊べる室内おもちゃ", f"{label}｜リビングで体を動かす", f"{label}｜雨の日の室内遊びに", f"{label}｜親子で押して遊ぶ"]
        pains = [
            "外に出にくい日が続くと、子どもが家の中で体を使える遊びを考えたくなりますよね。",
            "雨の日や夕方は、リビングで親の近くにいながら体を動かせる遊びがあるとうれしいですよね。",
            "座って遊ぶおもちゃが続く日は、押して進む遊びも取り入れたくなりますよね。",
            "家の中で過ごす日は、親が見守れる場所で子どもが自分から動ける遊びを増やしたいですよね。",
        ]
        scenes = [
            f"{feature}なら、リビングで親がそばにつきながら押して進む遊びを楽しみ、子どもの動きに合わせて声をかけられます。",
            f"{feature}なら、外へ出られない日も室内で体を使う遊びへ切り替え、遊ぶ様子を近くで見守れます。",
            f"{feature}なら、子どもが押して進む様子を見ながら親子で一緒に遊び、止まる場所や向きを見守れます。",
            f"{feature}なら、本体を出したリビングで押して遊ぶ時間を作り、動く範囲を見ながら付き添えます。",
        ]
        closings = [
            "対象年齢・本体サイズ・遊ぶ場所を商品ページで確認でき、子どもの様子を見ながら室内遊びを増やせるおもちゃです。",
            "遊ぶ場所と対象年齢を先に確かめられ、雨の日にも親子で家の中の遊び方を増やせるおもちゃです。",
            "本体の重さと対象年齢を確かめられ、座る遊びから押して遊ぶ時間へ切り替えて遊び方を増やせるおもちゃです。",
            "対象年齢と遊ぶ場所を確認でき、外に出にくい日もリビングで親子の遊び方を増やせるおもちゃです。",
        ]
    elif product_type == "stroller_storage":
        grounded_features = stroller_storage_feature_phrases(attributes)
        feature = (
            f"{'・'.join(grounded_features[:3])}のベビーカーバッグ"
            if grounded_features
            else feature
        )
        titles = [f"{label}｜外出荷物を分けて収納", f"{label}｜外出中の小物を手元に", f"{label}｜ベビーカー周りを整理", f"{label}｜散歩中に使う物を手元へ"]
        pains = [
            "散歩の途中で飲み物やおむつを探すと、ベビーカーを止める時間が増えますよね。",
            "子連れ外出は細かな荷物が多く、すぐ使う物ほどバッグの奥へ入りがちですよね。",
            "ベビーカー周りへ収納を足すなら、必要な物の位置を家族でそろえたいですよね。",
            "大容量のバッグでも、取り付け位置が合わないと出し入れしにくくなりますよね。",
        ]
        scenes = [
            f"{feature}なら、用途ごとに小物を分け、必要な物の位置を決めやすくなります。",
            f"{feature}なら、散歩中に使う物をベビーカーの手元へまとめやすくなります。",
            f"{feature}なら、おむつや飲み物など外出荷物の定位置を作れます。",
            f"{feature}なら、取り付け後の位置を見ながら出し入れの動きを考えられます。",
        ]
        closings = [
            "取り付け方法・容量・対応するベビーカーを確認すれば、外出中に荷物を探す時間を減らせるバッグです。",
            "本体サイズと取り付け位置が合えば、散歩中に必要な物を取り出しやすくできるバッグです。",
            "容量・取り付け方法・対応機種を商品ページで確かめれば、散歩中に必要な物を手元へまとめやすいバッグです。",
            "耐荷重・取り付け方法・対応機種を確認すれば、外出中に荷物を探す時間を減らせるバッグです。",
        ]
    elif product_type == "diaper" and "diaper_storage" in features:
        source = attributes.source_product_text
        storage_facts = []
        for marker, label_text in [
            ("大容量", "大容量"),
            ("布", "布製"),
            ("折りたたみ", "折りたたみ"),
            ("lサイズ", "Lサイズ"),
        ]:
            if marker in source and label_text not in storage_facts:
                storage_facts.append(label_text)
        fact_text = "・".join(storage_facts[:3]) or "収納ボックス型"
        titles = [
            f"{label}｜交換用品の定位置に",
            f"{label}｜家のおむつ替えを整える",
            f"{label}｜収納場所と容量を確認",
            f"{label}｜おむつとおしりふきをまとめる",
        ]
        pains = [
            "家のおむつ替えで使う物が別々の場所にあると、交換前に必要な物を探しがちですよね。",
            "おむつやおしりふきをまとめるなら、交換場所の近くに置ける大きさか気になりますよね。",
            "収納ボックスを選ぶ時は、入れたい物の量と家のおむつ替え動線の両方に合う大きさか気になりますよね。",
            "おむつ替え用品の定位置が決まらないと、補充する時にも残量を確認しにくいですよね。",
        ]
        scenes = [
            f"{fact_text}のおむつストッカーなら、おむつとおしりふきを交換場所の近くへまとめられます。",
            f"{fact_text}の収納ボックスなので、家で使うおむつ替え用品の定位置を作れます。",
            f"{fact_text}という商品情報を手がかりに、入れたい物と設置場所に合うか具体的に比べられます。",
            f"{fact_text}のおむつストッカーなら、補充する物を一か所で確認しやすくなります。",
        ]
        closings = [
            "本体サイズと収納場所を商品ページで確認すれば、家のおむつ替えで必要な物を探す手間を減らせるストッカーです。",
            "入れたい物の量と置き場所を照らし合わせると、おむつ替え用品を一か所へまとめられるアイテムです。",
            "本体サイズと折りたたみ方を確認すると、家の交換動線に合わない収納を選ぶ迷いを減らせるアイテムです。",
            "容量と設置場所を先に決めると、おむつとおしりふきの補充前に探す手間を減らせるアイテムです。",
        ]
    elif product_type == "diaper" and not (features & {"diaper_sheet", "diaper_pouch", "diaper_storage"}):
        titles = [f"{label}｜サイズと枚数を確認", f"{label}｜買い足す量を見極める", f"{label}｜外出分まで備える", f"{label}｜サイズアウト前に使い切る"]
        pains = [
            "まとめて買う時は、今のサイズを使い切れる量か気になりますよね。",
            "毎日使う消耗品は、残りが少なくなってから買い足すと慌ただしくなりますよね。",
            "外出用へ取り分ける家庭では、家に残る枚数も把握しておきたいですよね。",
            "成長の早い時期は、まとめ買いしてもサイズアウト前に使い切れるか迷いますよね。",
        ]
        scenes = [
            f"{feature}なら、交換回数から家で使う分と外出分の目安を考えられます。",
            f"{feature}なら、買い足す時期を普段の使用枚数に合わせて考えられます。",
            f"{feature}なら、外出用へ分けた後に家へ残る量を見通しやすくなります。",
            f"{feature}なら、今のサイズで使う期間と一緒に購入量を考えられます。",
        ]
        closings = [
            "適応体重・サイズ・セット総数に加えて収納場所に置ける量か確認すれば、サイズアウト前に使い切れる量か判断しやすくなります。",
            "テープ式かパンツ式か、パッケージ記載の適応体重と総枚数まで見れば、家庭の毎日の交換ペースに合うか選びやすくなります。",
            "サイズとセット内容、家庭の収納場所に入る量かを商品ページで確かめれば、家用と外出用へ分ける量を具体的に決めやすくなります。",
            "適応体重・サイズ・総枚数と一度に届く箱数が家庭の収納と使用量に合えば、買い過ぎと買い忘れの両方を避けやすくなります。",
        ]
    elif product_type == "nursing_support":
        cushion_like = bool(features & {"c_curve", "body_pressure_distribution", "nursing_cushion", "cushion", "multi_function"})
        if not cushion_like:
            titles = [f"{label}｜授乳中の手を支える", f"{label}｜哺乳瓶の対応を確認", f"{label}｜ミルク準備をそろえる", f"{label}｜授乳場所で使えるか確認"]
            pains = [
                "授乳中に哺乳瓶を支えながら、ミルク周りの支度や姿勢まで気にするのは落ち着かないですよね。",
                "授乳のたびに哺乳瓶を支え続けるなら、手元で使う補助用品がボトルに合うか気になりますよね。",
                "夜のミルク授乳では、哺乳瓶と一緒に使う物を毎回そろえる時間も短くしたいですよね。",
                "リビングと寝室で授乳するなら、同じ補助用品をそれぞれの場所で使えるか確認したいですよね。",
            ]
            scenes = [
                f"{feature}なら、授乳中に使う補助用品を一つに決め、毎回のミルク準備をそろえやすくなります。",
                f"{feature}なら、対応する哺乳瓶と取り付け方を商品情報から具体的に比べられます。",
                f"{feature}なら、夜の授乳前に用意する物と使う順番を家族で共有しやすくなります。",
                f"{feature}なら、普段の授乳場所でどのように哺乳瓶を支えるか考えられます。",
            ]
            closings = [
                "使える哺乳瓶サイズ・対象月齢・取り付け方を確認すれば、授乳中に手で支え続ける手間を減らせるアイテムです。",
                "対応ボトルとメーカー記載の使い方が分かるので、授乳前に補助用品を選び直す時間を減らせるアイテムです。",
                "対象月齢と使用上の注意を家族で確認でき、夜のミルク準備を整えられるアイテムです。",
                "本体サイズと対応する哺乳瓶を確かめられ、授乳場所ごとの準備を整えられるアイテムです。",
            ]
        else:
            support_detail = "傾斜と硬さ" if "incline" in features else "硬さとサイズ"
            care_detail = "カバーの手入れ" if "washable_cover" in features else "使用上の注意"
            titles = [f"{label}｜授乳姿勢を支える", f"{label}｜{support_detail}を確認", f"{label}｜授乳場所に合うか確認", f"{label}｜{care_detail}まで見る"]
            pains = [
                "授乳のたびに腕や姿勢を整えるなら、クッションの硬さや高さが使う場所に合うか気になりますよね。",
                "リビングと寝室で使うなら、授乳クッションの大きさと持ち運びやすさも見ておきたいですよね。",
                "毎日使う授乳クッションは、支え方だけでなくカバーの手入れ方法も選ぶ基準になりますよね。",
                f"{support_detail}など複数の仕様があると、普段の授乳姿勢に必要な機能か迷いますよね。",
            ]
            scenes = [
                f"{feature}なら、普段の授乳場所でどのように体を支えるか具体的に考えられます。",
                f"{feature}なら、ソファや床など使う場所に合う高さか商品情報から比べられます。",
                f"{feature}なら、授乳前の支度と使用後の手入れを一緒に考えられます。",
                f"{feature}なら、必要な支え方と商品の仕様を照らし合わせやすくなります。",
            ]
            closings = [
                "本体サイズ・硬さ・使用上の注意が分かるので、授乳前に姿勢を整える手間を減らせるアイテムです。",
                f"{support_detail}を商品情報で見比べられ、授乳場所ごとに支えを探す手間を減らせるアイテムです。",
                f"対象月齢・本体サイズ・{care_detail}を確かめられ、授乳前後の準備を整えられるアイテムです。",
                "メーカー記載の使い方と注意事項を読めるので、授乳前に支え方で迷う時間を減らせるアイテムです。",
            ]
    elif product_type == "baby_care" and (features & {"moisturizing", "baby_lotion", "baby_cream"}):
        titles = [f"{label}｜お風呂上がりの保湿に", f"{label}｜毎日の保湿を一つに", f"{label}｜着替え前の保湿準備", f"{label}｜家族で使う保湿ケア"]
        pains = [
            "お風呂上がりは着替えや片づけが重なり、赤ちゃんを待たせながら保湿に使う物を探すと慌ただしいですよね。",
            "毎日の保湿で使う物が日によって変わると、家族へ頼む時にも説明から始めることになりますよね。",
            "着替え前に保湿したい時、使う物が決まっていないとケアを始めるまでに時間がかかりますよね。",
            "お風呂上がりの保湿は、赤ちゃんを抱えたままケア用品を選び直したくないですよね。",
        ]
        scenes = [
            f"{feature}なら、お風呂上がりに使う保湿アイテムを一つに決め、着替えのそばへ準備できます。",
            f"{feature}なら、毎日の保湿で使う物とタイミングを家族でそろえやすくなります。",
            f"{feature}なら、着替え前の流れに保湿を組み込み、同じ場所からケアを始められます。",
            f"{feature}なら、お風呂上がりに迷わず手に取る保湿用品を決めやすくなります。",
        ]
        closings = [
            "成分と使う部位を見て選べば、お風呂上がりのケアで使う物を探す手間を減らせるアイテムです。",
            "家族も同じ物を使いやすくなり、毎日の保湿で迷う時間を減らせるアイテムです。",
            "着替え前に使う物を一つに絞れるので、赤ちゃんのケアを始めるまでの手間を減らせるアイテムです。",
            "お風呂上がりの流れに置き場所を合わせやすく、保湿前に探す手間を減らせるアイテムです。",
        ]
    elif product_type == "baby_sleep":
        titles = [f"{label}｜寝る前に着せる一枚", f"{label}｜サイズと素材を確かめる", f"{label}｜夜の着替え準備に", f"{label}｜洗濯方法まで確認"]
        pains = [
            "夜中に布団を蹴っていないか気になる時期は、寝る前に何を着せるか毎晩迷いやすいですよね。",
            "季節の変わり目は、掛けものと着せる物の組み合わせを夜ごとに考えるのが手間ですよね。",
            "寝る前の着替えでは、今の体格に合うサイズと素材を商品情報から見極めたいですよね。",
            "毎晩使う布ものは、素材だけでなく洗濯方法も家の手入れに合うか気になりますよね。",
        ]
        scenes = [
            f"{feature}なら、商品ページのサイズと素材を照らし合わせ、今の体格に合う一枚か判断できます。",
            f"{feature}なら、今の体格と季節に合うかを確認して、夜の着替えへ取り入れられます。",
            f"{feature}なら、着せ方と本体サイズを確かめて、寝る前の支度に合うか判断できます。",
            f"{feature}なら、素材と洗濯表示を読み、家庭で手入れを続けられるか比べられます。",
        ]
        closings = [
            "サイズ・素材・洗濯方法を先に確かめ、家族にも着せ方を共有すると、夜の着替え前に一枚を選び直す時間を減らせる一枚です。",
            "対象サイズと素材を商品ページで確認でき、今の体格に合わない一枚を選び直す時間を減らせるアイテムです。",
            "着せ方と洗濯方法を家族で共有でき、夜の支度で扱い方を確認し直す手間を減らせる一枚です。",
            "サイズ表と素材表示を読んで用途を絞れるため、寝る前に着せる物で迷う時間を減らせる一枚です。",
        ]
    elif product_type == "baby_bedding":
        titles = [f"{label}｜寝かしつけ前の寝具に", f"{label}｜本体サイズを確かめる", f"{label}｜素材と手入れ方法を見る", f"{label}｜使う場所から選ぶ"]
        pains = [
            "寝かしつけ前に使う寝具は、本体サイズが普段使う場所に合うか気になりますよね。",
            "赤ちゃん用の布ものを選ぶ時は、素材と洗濯方法が家庭の手入れに合うか気になりますよね。",
            "日中のねんねで使う寝具は、置く場所へ収まる大きさか先に見ておきたいですよね。",
            "抱っこから寝具へ移る場面では、商品の使い方と注意事項も判断材料になりますよね。",
        ]
        scenes = [
            f"{feature}なら、本体サイズと商品記載の使い方を見ながら、家庭で使う場面を具体的に考えられます。",
            f"{feature}なら、素材と洗濯表示から、家庭の手入れ方法に合うか判断できます。",
            f"{feature}なら、普段使う場所の広さと本体サイズを照らし合わせられます。",
            f"{feature}なら、メーカー記載の用途と注意事項を読んで、使う場面に合うか判断できます。",
        ]
        closings = [
            "本体サイズ・素材・洗濯方法を先に確かめると、使う場所に合わず寝具を探し直す手間を減らせるアイテムです。",
            "商品記載の用途と注意事項を読めるため、ねんね前に別の寝具を探し直す手間を減らせるアイテムです。",
            "置く場所の広さと本体サイズを照らし合わせると、使う直前に寝具を探し直す手間を減らせるアイテムです。",
            "素材表示と手入れ方法が分かり、日常の寝具準備で別の寝具を探し直す手間を減らせるアイテムです。",
        ]
    elif product_type == "swaddle":
        titles = [f"{label}｜夜に着せる一枚に", f"{label}｜サイズと素材を確かめる", f"{label}｜ファスナー仕様を見る", f"{label}｜着せ方まで確認"]
        pains = [
            "夜の着替えで使う一枚は、今の体格に合うサイズと素材か気になりますよね。",
            "ファスナー付きの一枚を選ぶ時は、開閉方法と着せ方が家庭の支度に合うか気になりますよね。",
            "寝る前に着せる一枚は、素材だけでなく商品記載の使用方法と注意事項も気になりますよね。",
            "毎晩使う布ものは、サイズ表と洗濯方法が家庭の手入れに合うか気になりますよね。",
        ]
        scenes = [
            f"{feature}なら、商品ページのサイズ表と着せ方から、夜の着替えに合う一枚か判断できます。",
            f"{feature}なら、ファスナーの開閉方法と素材を確かめて、家族が扱えるか判断できます。",
            f"{feature}なら、メーカー記載の使用方法と注意事項を読んで、使う場面に合うか比べられます。",
            f"{feature}なら、サイズ・素材・洗濯方法を確認して、家庭で手入れを続けられるか判断できます。",
        ]
        closings = [
            "対象サイズ・素材・着せ方を先に確かめると、夜の着替えで一枚を選び直す時間を減らせる一枚です。",
            "ファスナー仕様と洗濯方法を商品ページで確認でき、家族が扱い方で迷う時間を減らせる一枚です。",
            "メーカー記載の使用方法と注意事項を読めるため、今の月齢で使う布ものを選び直す時間を減らせる一枚です。",
            "サイズ表と素材表示を照らし合わせ、家族も着せ方を共有できると、家庭の夜支度で迷う時間を減らせる一枚です。",
        ]
    else:
        return None

    required = next((term for term in required_terms if term), "")
    title = titles[variant % len(titles)]
    if product_type == "wipes":
        topic = required if required and required != label else "在庫"
        title_actions = [
            "を切らさない備え",
            "の補充量を決める",
            "を家と外出用に分ける",
            "の残量を把握する",
            "の収納単位を整える",
            "の買い足し時期をそろえる",
            "を使い切れる量で備える",
            "の置き場所を決める",
        ]
        title = f"{label}｜{topic}{title_actions[(variant // len(titles)) % len(title_actions)]}"
    if product_type == "wipes":
        body = "".join([
            teaser + wipe_contexts[(variant // len(pains)) % len(wipe_contexts)] + pains[variant % len(pains)],
            scenes[(variant * 3 + variant // len(pains)) % len(scenes)],
            closings[(variant * 5 + variant // (len(pains) * len(scenes))) % len(closings)],
            wipe_inventory_steps[(variant * 7 + variant // len(pains)) % len(wipe_inventory_steps)],
        ])
    elif product_type == "magnetic_blocks":
        magnetic_index = variant % len(pains)
        magnetic_cycle = variant // len(pains)
        body = "".join([
            teaser + pains[magnetic_index],
            scenes[(magnetic_index + magnetic_cycle) % len(scenes)],
            closings[(magnetic_index + 2 * magnetic_cycle) % len(closings)],
        ])
    else:
        body = "".join([
            teaser + pains[variant % len(pains)],
            scenes[(variant // len(pains)) % len(scenes)],
            closings[(variant // (len(pains) * len(scenes))) % len(closings)],
        ])
    extra_sentences = {
        "formula": "未開封分を置く場所も決めておくと、家族が次に開ける分を把握しやすくなります。",
        "sound_blocks": "遊び終わった後に戻す場所も決めておくと、パーツの残りを把握しやすくなります。",
    }
    extra_sentence = extra_sentences.get(product_type, "")
    if extra_sentence and variant % 2 == 0 and len(body + extra_sentence) <= 260:
        body += extra_sentence
    if required and required not in f"{title}{body}":
        if product_type == "stroller_storage":
            stroller_titles = {
                "外出": f"{label}｜外出荷物を分けて収納",
                "手元": f"{label}｜外出中の小物を手元に",
                "ベビーカー": f"{label}｜ベビーカー周りを整理",
                "飲み物": f"{label}｜飲み物と小物をまとめる",
                "探す": f"{label}｜探す時間を減らす収納",
                "荷物": f"{label}｜荷物の定位置を作る",
                "散歩": f"{label}｜散歩中の荷物を整理",
            }
            title = stroller_titles.get(required, f"{label}｜{required}を生かして荷物整理")
        elif product_type == "diaper" and not (
            features & {"diaper_sheet", "diaper_pouch", "diaper_storage"}
        ):
            title = f"{label}｜{required}を確認"
        elif not uses_marketing_copy(attributes) and required in attributes.source_product_text:
            title = f"{label}｜{required}を楽しむ"
    return title, body

def distinct_listing_teaser(body: str, scored: ScoredProduct) -> str:
    # The first words are what a visitor sees in ROOM.  Keep the existing
    # product-type and problem/scene label intact; a volatile price is not a
    # useful primary cue for browsing.
    return body


def distinct_title(
    scored: ScoredProduct,
    attributes: ProductAttributes,
    *,
    variant: int = 0,
    required_term: str = "",
    use_case: str = "",
) -> str:
    label = attributes.short_product_label
    detail = required_term if required_term and required_term != label else use_case or label
    required_feature = {
        "ゲームなし": "game_free",
        "音が鳴る": "sound",
        "木製": "wood",
        "名入れ": "name_option",
        "コードレス": "cordless",
        "防水": "waterproof",
        "軽量": "lightweight",
        "モロー反射": "moro_reflex",
        "ハンズフリー": "hands_free",
        "プラネタリウム": "projector",
        "投影": "projector",
        "音楽": "music",
    }.get(detail)
    if required_feature and required_feature not in attributes.confirmed_features:
        detail = use_case or label
    feature = confirmed_feature_phrase(attributes)
    checkpoint = next(
        (value for value in attributes.purchase_checkpoints if value),
        "使う場所",
    )
    titles = [
        f"{label}｜{detail}に",
        f"{label}｜{use_case}で使う",
        f"{label}｜{feature}",
        f"{label}｜{detail}の準備に",
        f"{label}｜{detail}向け",
        f"{label}｜{checkpoint}を確認",
        f"{label}｜{checkpoint}と使う場面",
        f"{label}｜{use_case}から選ぶ",
        f"{label}｜{detail}で役立つ",
        f"{label}｜{use_case}のそばに",
        f"{label}｜{feature}を選ぶ",
        f"{label}｜{detail}の前に",
        f"{label}｜{use_case}の支度に",
        f"{label}｜{checkpoint}から考える",
        f"{label}｜{detail}を整える",
        f"{label}｜{use_case}に備える",
        f"{label}｜{checkpoint}と使い方",
        f"{label}｜{detail}のために",
        f"{label}｜{use_case}へ取り入れる",
        f"{label}｜{checkpoint}に合わせる",
        f"{label}｜{detail}を支える",
        f"{label}｜{use_case}で迷わない",
        f"{label}｜{feature}を暮らしに",
        f"{label}｜{detail}に使いやすい",
        f"{label}｜{use_case}を見直す",
        f"{label}｜{checkpoint}を決める",
        f"{label}｜{detail}の候補に",
        f"{label}｜{use_case}を助ける",
        f"{label}｜{feature}の仕様から選ぶ",
        f"{label}｜{detail}で使う",
        f"{label}｜{use_case}に合う",
        f"{label}｜{checkpoint}も確認",
    ]
    return titles[variant % len(titles)]


def build_analysis(
    scored: ScoredProduct,
    attributes: ProductAttributes,
    pattern_id: str,
) -> PostAnalysis:
    return PostAnalysis(
        product_type=attributes.product_type,
        target=attributes.target_age or "育児中の家庭",
        user_pain="・".join(attributes.confirmed_use_cases[:2]),
        search_intent=scored.product.search_keyword or scored.product.category,
        purchase_anxiety="・".join(attributes.purchase_checkpoints),
        benefit="確認済み属性を使い、生活場面へつなげる",
        usage_scene="・".join(attributes.confirmed_use_cases),
        appeal_axis=pattern_id,
        reason_to_check=attributes.short_product_label,
        caution="未確認属性は本文へ使用しない",
    )


def validate_post(
    post: GeneratedPost,
    attributes: ProductAttributes,
    context: GenerationContext | None = None,
) -> list[str]:
    errors: list[str] = []
    combined = f"{post.title}{post.body}"
    if not post.title or not post.body:
        errors.append("タイトルまたは本文が空")
        return errors
    if attributes.product_type not in PATTERNS:
        errors.append("unsupported_product_type: 商品タイプ不一致")
        return errors
    errors.extend(classification_consistency_errors(attributes, combined))
    pattern = next(
        (
            candidate
            for candidate in PATTERNS[attributes.product_type]
            if candidate.pattern_id == post.structure_pattern
        ),
        None,
    )
    if pattern is None:
        errors.append("pattern_idが商品タイプと不一致")
    elif not uses_marketing_copy(attributes):
        if not all(term in combined for term in pattern.title_required):
            errors.append("content_type_mismatch: タイトルと本文の商品タイプ不一致")
        if any(term in combined for term in pattern.title_forbidden):
            errors.append("content_type_mismatch: タイトルと本文の禁止語が混入")
    title_errors = title_evidence_errors(post, attributes)
    tag_errors = tag_evidence_errors(post, attributes)
    reason_errors = recommendation_reason_errors(post, attributes)
    errors.extend(title_errors)
    errors.extend(tag_errors)
    errors.extend(reason_errors)
    post.title_evidence_result = "OK" if not title_errors else " / ".join(title_errors)
    post.tag_evidence_result = "OK" if not tag_errors else " / ".join(tag_errors)
    post.recommendation_reason_result = "OK" if not reason_errors else " / ".join(reason_errors)
    if any(term in combined for term in attributes.prohibited_features):
        errors.append("content_type_mismatch: 別商品または商品タイプ違いの特徴が混入")
        errors.append("別商品または商品タイプ違いの特徴が混入")
    errors.extend(external_safety_errors(post, attributes))
    if attributes.product_type == "wipes":
        if attributes.short_product_label == "手口ふき" and "おむつ替え" in combined:
            errors.append("手口ふきにおむつ替え文脈を使用")
        if attributes.short_product_label == "おしりふき" and "手口ふき" in combined:
            errors.append("おしりふきに手口ふき文脈を使用")
    if "誕生日" in combined and "誕生日向け" not in attributes.confirmed_gift_features:
        errors.append("未確認の誕生日用途を使用")
    if f"{attributes.short_product_label}の{attributes.short_product_label}" in combined:
        errors.append("duplicate_short_label: 短縮商品名が重複")
    if any(term in combined for term in BANNED_EXPRESSIONS):
        errors.append("禁止表現を使用")
    if any(term in combined for term in BANNED_INTENTION_PHRASES):
        errors.append("marketing_weak_cta: 投稿文に一人称の検討・確認表現を使用")
    if any(term in post.body for term in WEAK_ROOM_COPY_PHRASES):
        errors.append("marketing_weak_cta: 投稿文が確認・比較中心の弱い表現を含む")
    if "です、" in post.body or "ます、" in post.body:
        errors.append("不自然な文接続を使用")
    if any(value in post.title for value in ["暮らしへ足す", "｜ケアに", "｜ケア用品向け", "｜サイズに", "｜場面に使いやすい", "スリーパー｜ガーゼ素材のスリーパー"]):
        errors.append("title_content_mismatch: 商品と利用場面が伝わらない曖昧タイトル")
    title_parts = [part.strip() for part in post.title.split("｜", 1)]
    if len(title_parts) == 2 and normalize_text(title_parts[0]) == normalize_text(title_parts[1]):
        errors.append("title_content_mismatch: 区切り前後で商品名だけを反復")
    if "を楽しむ" in post.title and attributes.product_type not in {
        "sound_blocks",
        "wooden_blocks",
        "magnetic_blocks",
        "baby_walker_toy",
        "activity_cube",
        "ring_toy",
    }:
        errors.append("title_content_mismatch: 遊び商品ではないのに楽しむ訴求を使用")
    awkward_copy = [
        "押して遊ぶで探す手間",
        "押して遊ぶで使う",
        "ピース数があるタイプなら",
        "キッズカメラを選べるキッズカメラ",
        "写真を撮るのどこへ置く",
        "写真を撮るの近く",
    ]
    if any(value in post.body for value in awkward_copy):
        errors.append("marketing_awkward_condition: 不自然または根拠の弱い定型表現を使用")
    if attributes.product_type == "baby_walker_toy" and "対象年齢" not in post.body:
        errors.append("purchase_checkpoint_missing: 手押し車の対象年齢が本文にない")
    if attributes.product_type in {
        "activity_cube",
        "baby_walker_toy",
        "stroller_storage",
        "baby_care",
        "diaper",
        "nursing_support",
        "baby_sleep",
    } and any(
        value in post.body
        for value in [
            "のどこへ置く",
            "必要な時に取り出し、使い終わった後に戻す",
            "使った後まで見通して",
            "必要な動きに合うか",
            "対象月齢で迷う時間を減らして",
            "場面を手がかりに",
            "使う回数を見ながら",
            "取り付け、バッグの奥まで探す場面を減らせ",
        ]
    ):
        errors.append("marketing_awkward_condition: 商品価値につながらない汎用の収納・動線表現を使用")
    if attributes.product_type in {"activity_cube", "baby_walker_toy", "baby_care", "baby_sleep"}:
        if post.body.count(attributes.short_product_label) >= 3:
            errors.append("duplicate_short_label: 本文で商品名を3回以上反復")
    if attributes.product_type == "baby_bedding" and any(
        value in post.body
        for value in [
            "抱っこ布団やねんねクッションは、家のどこで使うか",
            "抱っこ布団・ねんねクッション",
            "ねんねクッション・ベビー布団",
        ]
    ):
        errors.append("marketing_generic_copy: 寝具の商品名列挙または一般論を使用")
    noise_check_text = f"{post.title}{strip_listing_teaser(post.body)}"
    if any(re.search(pattern, noise_check_text, flags=re.IGNORECASE) for pattern in NOISE_PATTERNS):
        errors.append("商品名ノイズが残っている")
    if not re.match(r"^【[^】]{10,35}】", post.body):
        errors.append("listing_teaser_missing: 投稿文冒頭の一覧見出しがない")
    if combined.startswith(("、", "!", "！", "<", "＜")) or post.body.startswith(("、", "!", "！", "<", "＜")):
        errors.append("文頭が読点または記号")
    sentences = split_sentences(post.body)
    body_for_claims = strip_listing_teaser(post.body)
    if len(sentences) not in {3, 4}:
        errors.append("本文が3〜4文ではない")
    if not 150 <= len(post.body) <= 260:
        errors.append("本文が150〜260文字の目安から大きく外れる")
    if len(attributes.purchase_checkpoints) > 3:
        errors.append("purchase_checkpoint_mismatch: 購入前確認点が4つ以上")
        errors.append("購入前確認点が4つ以上")
    if not any(
        marker in body_for_claims
        for feature in attributes.confirmed_features
        for marker in FEATURE_MARKERS.get(feature, [])
    ) and not any(quantity in body_for_claims for quantity in attributes.confirmed_quantity_features):
        errors.append("confirmed_feature_missing: 商品固有の確認済み特徴がない")
    source_text = attributes.source_product_text
    if attributes.product_type == "formula":
        age_match = re.search(r"(\d+)\s*[〜~～\-]\s*(\d+)\s*(?:カ月|か月|ヶ月)", source_text)
        if age_match:
            age_text = f"{age_match.group(1)}〜{age_match.group(2)}カ月"
            if age_text not in body_for_claims:
                errors.append("product_specific_fact_missing: 粉ミルクの対象月齢が本文にない")
        quantities = list(attributes.confirmed_quantity_features[:2])
        if len(quantities) >= 2 and not all(value in body_for_claims for value in quantities):
            errors.append("product_specific_fact_missing: 粉ミルクの容量と缶数が本文にない")
        if "海外通販" in source_text and "海外通販" not in body_for_claims:
            errors.append("product_specific_fact_missing: 海外通販の配送確認が本文にない")
    if attributes.product_type == "baby_sleep" and "6重" in source_text and "6重" not in body_for_claims:
        errors.append("product_specific_fact_missing: スリーパーの6重ガーゼ仕様が本文にない")
    if attributes.product_type == "baby_care" and "ポンプ" in source_text:
        quantity = next(iter(attributes.confirmed_quantity_features), "")
        if "ポンプ" not in body_for_claims or (quantity and quantity not in body_for_claims):
            errors.append("product_specific_fact_missing: 保湿剤の容量とポンプ仕様が本文にない")
    if attributes.product_type == "swaddle" and "綿100" in source_text and "ファスナー" in source_text:
        if "綿100" not in body_for_claims or "ファスナー" not in body_for_claims:
            errors.append("product_specific_fact_missing: スワドルの素材と開閉仕様が本文にない")
    used_features = {
        feature
        for feature, markers in FEATURE_MARKERS.items()
        if any(marker in body_for_claims for marker in markers)
    }
    # Specific cushion attributes contain the generic word 「クッション」.
    # Do not treat that overlap as an additional, unconfirmed generic claim.
    if set(attributes.confirmed_features) & {"nursing_cushion", "sleep_cushion"}:
        used_features.discard("cushion")
    unconfirmed = used_features - set(attributes.confirmed_features)
    if unconfirmed:
        errors.append(f"unsupported_product_claim: 未確認属性を使用: {','.join(sorted(unconfirmed))}")
    if not hashtags_match_type(post.hashtags, attributes):
        errors.append("hashtag_product_type_mismatch: 商品タイプとハッシュタグが不一致")
    if "平面と立体の違いにも気づきやすく" in post.body:
        errors.append("confirmed_featuresにない効果を使用")
    if has_repeated_meaning(sentences):
        errors.append("duplicate_phrase: 文章内で同じ意味を繰り返している")
    if has_repeated_long_phrase(sentences) or post.body.count("変えられ") >= 3:
        errors.append("duplicate_phrase: 文章内で同じ表現を繰り返している")
    if confirmation_repeat_count(post.body) >= 3:
        errors.append("duplicate_phrase: 確認系表現が同一投稿内で3回以上")
    if has_semantic_repetition(sentences):
        errors.append("duplicate_phrase: 同一ベネフィットの意味重複")
    errors.extend(quantity_consistency_errors(post, attributes))
    if has_duplicate_benefit_repetition(sentences):
        errors.append("duplicate_phrase: 同じベネフィットを言い換えて繰り返している")
    errors.extend(marketing_quality_errors(post, attributes, sentences))
    if context is not None:
        errors.extend(duplicate_errors(post, context))
    return list(dict.fromkeys(errors))


def marketing_quality_errors(
    post: GeneratedPost,
    attributes: ProductAttributes,
    sentences: list[str],
) -> list[str]:
    if not uses_marketing_copy(attributes):
        return []
    errors: list[str] = []
    body = post.body
    ending = sentences[-1] if sentences else body
    combined = f"{post.title}{body}"

    if any(ending.endswith(value) for value in FORBIDDEN_MARKETING_ENDINGS):
        errors.append("marketing_weak_cta: 最後が確認・比較・弱い推量で終わっている")
    if not any(value in ending for value in CONCRETE_BENEFIT_ENDINGS):
        errors.append("marketing_weak_cta: 最後が具体的なベネフィットで言い切れていない")
    if attributes.product_type == "nursing_support" and "multi_function" in attributes.confirmed_features:
        errors.append("confirmed_use_cases_insufficient: 多機能の具体用途を商品情報から十分に抽出できない")
    if not has_reader_pain(attributes.product_type, body):
        errors.append("marketing_missing_pain: 読者が自分事化できる具体的な悩みが弱い")
    if not has_daily_scene(attributes.product_type, body):
        errors.append("marketing_missing_scene: 時間帯・場所・動作の具体性が弱い")
    if not has_life_change(attributes.product_type, body):
        errors.append("marketing_missing_life_change: 購入後に減る手間・迷い・不安が弱い")
    if has_marketing_benefit_overlap(sentences):
        errors.append("marketing_duplicate_benefit: 2文目と3文目が同じベネフィットを繰り返している")
    if has_user_workaround_as_main_benefit(sentences):
        errors.append("marketing_weak_causality: 商品を買わなくてもできる工夫が主なベネフィット")
    if has_templated_purchase_condition(ending):
        errors.append("marketing_overweighted_check: 購入前確認点がテンプレ化している")
    awkward_conditions = [
        "対象年齢が家庭で使える範囲なら",
        "家庭に合えば",
        "基準に選べば",
        "使い方を見極めやすい",
        "流れを見直せ",
        "商品ページにある使い方",
    ]
    if any(value in combined for value in awkward_conditions):
        errors.append("marketing_awkward_condition: 不自然な条件表現を使用")
    if confirmation_repeat_count(body) >= 2 and not any(value in ending for value in CONCRETE_BENEFIT_ENDINGS):
        errors.append("marketing_overweighted_check: 購入前確認が文章の中心になっている")
    if "比較しやす" in ending or "選びやす" in ending or "判断しやす" in ending:
        errors.append("marketing_weak_benefit: ベネフィットが比較・選択のしやすさだけ")
    if attributes.short_product_label not in combined and not any(
        marker in body
        for feature in attributes.confirmed_features
        for marker in FEATURE_MARKERS.get(feature, [])
    ):
        errors.append("marketing_generic_copy: 他商品へ流用できる汎用文になっている")
    return errors


def has_marketing_benefit_overlap(sentences: list[str]) -> bool:
    if len(sentences) < 3:
        return False
    second = sentences[1]
    third = sentences[2]
    overlap_groups = [
        ("迷いを減らす", ("迷う", "減ら")),
        ("手間を減らす", ("手間", "減ら")),
        ("一つにまとめる", ("一つ", "まとめ")),
        ("用意を減らす", ("用意", "減ら")),
        ("準備しやすい", ("準備", "しやす")),
    ]
    for _, markers in overlap_groups:
        if all(marker in second for marker in markers) and all(marker in third for marker in markers):
            return True
    return False


def has_user_workaround_as_main_benefit(sentences: list[str]) -> bool:
    if not sentences:
        return False
    ending = sentences[-1]
    workaround_terms = [
        "戻す場所",
        "家族で同じ流れ",
        "毎晩同じ場所",
        "使用量",
        "収納ルール",
        "置き場所を決め",
    ]
    return any(term in ending for term in workaround_terms)


def has_templated_purchase_condition(ending: str) -> bool:
    if "が家庭に合えば" in ending and ending.count("・") >= 2:
        return True
    if "対象月齢・本体サイズ・カバー" in ending:
        return True
    return False


def has_reader_pain(product_type: str, body: str) -> bool:
    required = {
        "nursing_support": ["授乳", "クッション", "置き場所", "集め", "哺乳瓶", "ミルク", "支え"],
        "swaddle": ["夜", "何を着せる", "洗い替え", "着替え"],
        "baby_bedding": ["寝かしつけ前", "寝具", "取りに戻る", "ねんね前", "探し直す"],
        "baby_care": ["ケア", "保湿", "お風呂上がり", "爪", "鼻", "体温"],
        "baby_sleep": ["夜", "布団", "着せる", "寝冷え", "灯り"],
        "diaper": ["おむつ替え", "外出", "交換", "探す", "敷く物"],
        "soothing_plush": ["寝る前", "寝室", "用意する物", "投影", "メロディー", "スマホ", "ライト"],
        "baby_walker_toy": ["つかまり立ち", "歩き始め", "押して", "体を使って", "体を使える", "外に出にくい", "雨の日"],
    }[product_type]
    return sum(1 for value in required if value in body) >= 2


def has_daily_scene(product_type: str, body: str) -> bool:
    required = {
        "nursing_support": ["授乳", "いつもの授乳場所", "クッション"],
        "swaddle": ["新生児期", "夜", "着替え", "家族"],
        "baby_bedding": ["寝かしつけ前", "寝室", "リビング", "ねんね前"],
        "baby_care": ["お風呂上がり", "朝", "毎日", "必要な時"],
        "baby_sleep": ["夜", "寝る前", "夜中", "夜のお世話"],
        "diaper": ["外出先", "おむつ替え", "家の中", "交換前"],
        "soothing_plush": ["寝る前", "寝室", "絵本", "声かけ"],
        "baby_walker_toy": ["家の中", "リビング", "室内", "おうち時間"],
    }[product_type]
    return any(value in body for value in required)


def has_life_change(product_type: str, body: str) -> bool:
    required = {
        "nursing_support": ["手間を減らせる", "迷う時間を減らせます", "迷いを減らせる", "選び直す時間を減らせる", "準備しやすく", "準備を整えられる"],
        "swaddle": ["迷う時間を減らせる", "探す時間を減らせる", "考える時間を減らせる", "選び直す時間を減らせる", "シンプルにできる"],
        "baby_bedding": ["探し直す手間を減らせる", "取りに戻る手間を減らせる", "迷いを減らせる", "準備しやすく", "準備を整えられる"],
        "baby_care": ["迷う時間を減らせる", "探す手間を減らせる", "手間を減らせる", "取り入れやすい", "そろえやすく"],
        "baby_sleep": ["布ものを減らせる", "探す時間を減らせる", "迷う時間を減らせる", "選び直す時間を減らせる", "手間を減らせる", "一つ決めやすく"],
        "diaper": ["探す手間を減らせる", "探す時間を減らせる", "まとめやすく", "決めやすく"],
        "soothing_plush": ["一つにまとめられる", "用意する物を減らせる", "手間を減らせる", "一つ決められる", "一つに決められる", "片づける物を減らせる"],
        "baby_walker_toy": ["遊び方を増やせる", "室内遊びを増やせます", "押して遊ぶ時間", "遊びを増やせる", "体を使う時間を増やせる"],
    }[product_type]
    return any(value in body for value in required)


def hashtags_match_type(hashtags: list[str], attributes: ProductAttributes) -> bool:
    return len(hashtags) == 5 and hashtags[-1] == BRAND_TAG


def classification_consistency_errors(
    attributes: ProductAttributes,
    generated_text: str,
) -> list[str]:
    errors: list[str] = []
    source_text = attributes.source_product_text
    conflict_types = {
        "swaddle": TYPE_KEYWORDS["swaddle"],
        "nursing_support": TYPE_KEYWORDS["nursing_support"],
        "baby_care": TYPE_KEYWORDS["baby_care"],
        "baby_sleep": TYPE_KEYWORDS["baby_sleep"],
        "baby_bedding": TYPE_KEYWORDS["baby_bedding"],
        "soothing_plush": TYPE_KEYWORDS["soothing_plush"],
        "baby_walker_toy": TYPE_KEYWORDS["baby_walker_toy"],
    }
    for expected_type, keywords in conflict_types.items():
        if expected_type == attributes.product_type:
            continue
        # Exact sound-block evidence is more specific than a generic plush
        # keyword.  Fabric blocks can be described as plush toys while still
        # requiring block copy rather than bedtime-plush copy.
        if attributes.product_type == "sound_blocks" and expected_type == "soothing_plush":
            continue
        # Rakuten category/caption text can call formula a generic "授乳用品".
        # Exact formula evidence is more specific and must not be mistaken for
        # a nursing pillow or bottle-holder product.
        if (
            attributes.product_type == "formula"
            and expected_type == "nursing_support"
            and any(term in source_text for term in ["粉ミルク", "液体ミルク", "フォローアップミルク"])
        ):
            continue
        # "Sleeper" is often included alongside an explicitly named swaddle.
        # Strong swaddle identity wins; generic "okurumi" alone does not get
        # this exception and remains review-only when the identity is mixed.
        if (
            attributes.product_type == "swaddle"
            and expected_type == "baby_sleep"
            and any(term in source_text for term in ["スワドル", "モロー反射", "ねくるみ"])
        ):
            continue
        # A named sleeper sometimes also uses the generic word "okurumi" in
        # its listing. Without explicit swaddle evidence, the more precise
        # sleeper identity should win.
        if (
            attributes.product_type == "baby_sleep"
            and expected_type == "swaddle"
            and "スリーパー" in source_text
            and not any(term in source_text for term in ["スワドル", "モロー反射", "ねくるみ"])
        ):
            continue
        # White-noise/nursing-light products legitimately include generic
        # night-light or bedtime words. The dedicated light identity is more
        # specific than the broad baby-sleep bucket.
        if (
            attributes.product_type == "sleep_light"
            and expected_type == "baby_sleep"
            and any(term in source_text for term in TYPE_KEYWORDS["sleep_light"])
        ):
            continue
        # Lotion-infused wipes can truthfully mention baby lotion or moisture
        # while the named product remains a disposable wipe.
        if (
            attributes.product_type == "wipes"
            and expected_type == "baby_care"
            and any(term in attributes.normalized_product_name for term in TYPE_KEYWORDS["wipes"])
        ):
            continue
        if any(keyword.lower() in source_text for keyword in keywords):
            errors.append(
                f"product_type_keyword_conflict: {expected_type}系キーワードを含む商品が{attributes.product_type}に分類されています"
            )
    content_forbidden = {
        "swaddle": TYPE_KEYWORDS["diaper"] + TYPE_KEYWORDS["nursing_support"] + TYPE_KEYWORDS["baby_bedding"] + TYPE_KEYWORDS["baby_care"],
        "nursing_support": TYPE_KEYWORDS["diaper"] + TYPE_KEYWORDS["swaddle"] + TYPE_KEYWORDS["soothing_plush"] + TYPE_KEYWORDS["baby_care"],
        "baby_bedding": TYPE_KEYWORDS["diaper"] + TYPE_KEYWORDS["nursing_support"] + ["スワドル"],
        "baby_care": TYPE_KEYWORDS["diaper"] + TYPE_KEYWORDS["nursing_support"] + TYPE_KEYWORDS["swaddle"],
        "baby_sleep": TYPE_KEYWORDS["diaper"] + TYPE_KEYWORDS["nursing_support"],
        "soothing_plush": TYPE_KEYWORDS["diaper"] + TYPE_KEYWORDS["nursing_support"],
        "diaper": TYPE_KEYWORDS["swaddle"] + TYPE_KEYWORDS["nursing_support"] + TYPE_KEYWORDS["baby_bedding"] + TYPE_KEYWORDS["baby_care"] + TYPE_KEYWORDS["baby_sleep"],
        "wooden_blocks": TYPE_KEYWORDS["baby_walker_toy"] + TYPE_KEYWORDS["magnetic_blocks"] + ["ままごと", "パズル", "楽器"],
        "magnetic_blocks": TYPE_KEYWORDS["wooden_blocks"] + TYPE_KEYWORDS["baby_walker_toy"],
        "baby_walker_toy": TYPE_KEYWORDS["wooden_blocks"] + TYPE_KEYWORDS["magnetic_blocks"],
    }.get(attributes.product_type, [])
    if any(term in generated_text for term in content_forbidden):
        errors.append("content_type_mismatch: タイトル・本文・タグに別商品タイプの表現が混入")
    return errors


def external_safety_errors(post: GeneratedPost, attributes: ProductAttributes) -> list[str]:
    external_text = f"{post.title}{post.body}{post.recommendation_reason}{' '.join(post.hashtags)}"
    errors: list[str] = []
    if "背中スイッチ" in external_text:
        errors.append("unsupported_product_claim: back_switchを外部出力")
    if attributes.product_type == "nursing_support":
        manual_review_terms = ("自分で飲む", "セルフミルク", "ママ代行")
        matched = next(
            (term for term in manual_review_terms if term in attributes.source_product_text),
            "",
        )
        if matched:
            errors.append(
                f"manual_review_required: 授乳補助商品の安全な使用判断が必要: {matched}"
            )
        if any(term in external_text for term in NURSING_SUPPORT_UNSAFE_TERMS):
            errors.append("content_type_mismatch: nursing_supportに睡眠場所と誤解される表現")
        if body_pressure_claim_is_unsafe(external_text):
            errors.append("unsupported_product_claim: 体圧分散を安全性や睡眠効果へ接続")
    if attributes.product_type == "soothing_plush":
        if any(term in external_text for term in SOOTHING_PLUSH_UNSAFE_TERMS):
            errors.append("content_type_mismatch: soothing_plushに枕元・添い寝系表現")
    return errors


def body_pressure_claim_is_unsafe(text: str) -> bool:
    if "体圧分散" not in text:
        return False
    unsafe_terms = ["安全", "睡眠", "安眠", "寝る", "寝かせ", "身体効果", "負担を減ら"]
    return any(term in text for term in unsafe_terms)


def confirmation_repeat_count(text: str) -> int:
    return sum(text.count(marker) for marker in CONFIRMATION_REPEAT_MARKERS)


def quantity_mentions(text: str) -> list[str]:
    mentions = []
    for match in QUANTITY_MENTION_PATTERN.finditer(text):
        following = text[match.end() : match.end() + 3]
        if following.startswith("あたり"):
            continue
        mentions.append(re.sub(r"\s+", "", match.group(0)))
    return mentions


def quantity_consistency_errors(post: GeneratedPost, attributes: ProductAttributes) -> list[str]:
    body_quantities = list(dict.fromkeys(quantity_mentions(post.body)))
    if not body_quantities:
        return []

    source_quantities = {
        re.sub(r"\s+", "", quantity)
        for quantity in attributes.confirmed_quantity_features
    }
    errors: list[str] = []
    unsupported = [quantity for quantity in body_quantities if quantity not in source_quantities]
    if unsupported:
        errors.append(
            "unsupported_quantity_claim: 商品情報にない数量を本文で使用: "
            + ",".join(unsupported)
        )

    count_quantities = [
        quantity
        for quantity in body_quantities
        if re.search(r"(?:個セット|個|ピース|パーツ)$", quantity)
    ]
    count_numbers = {
        re.match(r"\d+(?:\.\d+)?", quantity).group(0)
        for quantity in count_quantities
        if re.match(r"\d+(?:\.\d+)?", quantity)
    }
    if len(count_numbers) >= 2:
        errors.append("quantity_conflict: 同じ商品本文内で異なる個数・ピース数が混在")
    return errors


def has_duplicate_benefit_repetition(sentences: list[str]) -> bool:
    groups = [
        (2, ("撮った写真",)),
        (2, ("帰宅後", "見返", "振り返", "写真を選ぶ")),
        (3, ("家の中", "おうち時間", "室内遊び")),
        (3, ("遊び方が広が", "遊び方を増や", "遊び方が増", "遊びが広が", "遊びを増や")),
        (2, ("使いやす", "扱いやす", "始めやす")),
    ]
    for threshold, markers in groups:
        count = sum(1 for sentence in sentences if any(marker in sentence for marker in markers))
        if count >= threshold:
            return True
    return False


def has_semantic_repetition(sentences: list[str]) -> bool:
    groups = [
        ("光音", ("光", "音")),
        ("使う場面", ("使う場面", "場面", "使い方")),
        ("着せ方洗い替え", ("着せ方", "洗い替え")),
        ("家族共有", ("家族", "共有")),
    ]
    for _, markers in groups:
        count = sum(1 for sentence in sentences if all(marker in sentence for marker in markers))
        if count >= 2:
            return True
    return False


def title_evidence_errors(
    post: GeneratedPost,
    attributes: ProductAttributes,
) -> list[str]:
    errors: list[str] = []
    body = post.body
    title = post.title
    for title_term, body_terms in TITLE_SCENE_RULES.items():
        if title_term not in title:
            continue
        if title_term == "誕生日" and "誕生日向け" not in attributes.confirmed_gift_features:
            errors.append("title_content_mismatch: タイトルの誕生日訴求に商品情報の根拠がない")
            errors.append("タイトルの誕生日訴求に商品情報の根拠がない")
        if not any(term in body for term in body_terms):
            errors.append(f"title_content_mismatch: タイトルの使用場面「{title_term}」が本文にない")
            errors.append(f"タイトルの使用場面「{title_term}」が本文にない")
    feature_title_rules = {
        "ゲームなし": "game_free",
        "スマホへ移せる": "smartphone_transfer",
        "スマホ転送": "smartphone_transfer",
        "SDカード": "sd_card",
        "USB充電": "usb_charge",
        "音が鳴る": "sound",
        "木製": "wood",
        "名入れ": "name_option",
        "コードレス": "cordless",
        "防水": "waterproof",
        "軽量": "lightweight",
        "モロー反射": "moro_reflex",
        "ハンズフリー": "hands_free",
        "プラネタリウム": "projector",
        "投影": "projector",
        "音楽": "music",
    }
    for title_term, feature in feature_title_rules.items():
        if title_term in title and feature not in attributes.confirmed_features:
            errors.append(f"title_content_mismatch: タイトルの特徴「{title_term}」に商品情報の根拠がない")
            errors.append(f"タイトルの特徴「{title_term}」に商品情報の根拠がない")
    simplified = normalize_text(title.replace(attributes.short_product_label, ""))
    if not simplified or simplified in {"を選ぶ", "を選びたい", "選ぶ", "選びたい"}:
        errors.append("title_content_mismatch: タイトルが商品名の言い換えだけ")
        errors.append("タイトルが商品名の言い換えだけ")
    return errors


def tag_evidence_errors(
    post: GeneratedPost,
    attributes: ProductAttributes,
) -> list[str]:
    errors: list[str] = []
    features = set(attributes.confirmed_features)
    combined = f"{post.title}{post.body}"
    evidence_rules = {
        "#木のおもちゃ": "wood" in features,
        "#木製おもちゃ": "wood" in features,
        "#木製積み木": "wood" in features,
        "#誕生日プレゼント": (
            "誕生日向け" in attributes.confirmed_gift_features
            and "誕生日" in combined
        ),
        "#夜のおむつ替え": "夜" in combined,
        "#夜間授乳": "夜" in combined and "授乳" in combined,
        "#子連れ外出": (
            "外出" in combined
            and (
                any("外出" in use_case for use_case in attributes.confirmed_use_cases)
                or attributes.product_type == "stroller_storage"
            )
        ),
        "#外出用おむつ": (
            "外出" in combined
            and any("外出" in use_case for use_case in attributes.confirmed_use_cases)
        ),
        "#名入れ": "name_option" in features,
        "#防水": "waterproof" in features,
        "#軽量": "lightweight" in features,
        "#収納袋付き": "storage_bag" in features,
        "#ゲームなし": "game_free" in features,
        "#USB充電": "usb_charge" in features,
        "#スマホ転送": "smartphone_transfer" in features,
        "#SDカード": bool(features & {"sd_card_supported", "sd_card_included"}),
        "#スワドル": "swaddle" in features,
        "#モロー反射": "moro_reflex" in features,
        "#ハンズフリー授乳": "hands_free" in features,
        "#哺乳瓶ホルダー": "bottle_holder" in features,
        "#抱っこ布団": "hug_futon" in features,
        "#ねんねクッション": "sleep_cushion" in features,
        "#ベビー布団": "baby_futon" in features,
        "#ダブルガーゼ": "double_gauze" in features,
        "#コットン素材": "cotton" in features,
        "#洗える寝具": any(
            term in attributes.source_product_text for term in ["洗える", "洗濯"]
        ),
        "#授乳クッション": "nursing_cushion" in features,
        "#保湿ケア": "moisturizing" in features or "baby_lotion" in features or "baby_cream" in features,
        "#ベビーローション": "baby_lotion" in features,
        "#全身保湿": "全身" in attributes.source_product_text and "moisturizing" in features,
        "#ポンプ式": "ポンプ" in attributes.source_product_text,
        "#爪ケア": "nail_care" in features,
        "#鼻吸い": "nasal_aspirator" in features,
        "#体温計": "thermometer" in features,
        "#ガーゼケット": "gauze" in features,
        "#スリーパー": "sleeper" in features,
        "#6重ガーゼ": "6重" in attributes.source_product_text and "gauze" in features,
        "#プラネタリウム": "projector" in features or "star_projection" in features,
        "#おやすみぬいぐるみ": "plush" in features,
        "#手押し車": "walker_toy" in features,
        "#ファーストウォーカー": "walker_toy" in features or "ファーストウォーカー" in attributes.source_product_text,
        "#ベビーウォーカー": "walker_toy" in features or "ベビーウォーカー" in attributes.source_product_text,
        "#つかまり立ち期": "standing_support_play" in features,
    }
    for tag in post.hashtags:
        if tag in evidence_rules and not evidence_rules[tag]:
            errors.append(f"hashtag_unconfirmed_attribute: ハッシュタグの根拠がない: {tag}")
            errors.append(f"ハッシュタグの根拠がない: {tag}")
    expected = hashtags_for(attributes, body=post.body, title=post.title)
    if len(post.hashtags) != 5:
        errors.append("hashtag_count_below_5: ハッシュタグが5件ではない")
    if post.hashtags != expected:
        errors.append("hashtag_product_type_mismatch: 確認済み属性から生成したハッシュタグと不一致")
        errors.append("確認済み属性から生成したハッシュタグと不一致")
    return errors


def recommendation_reason_errors(
    post: GeneratedPost,
    attributes: ProductAttributes,
) -> list[str]:
    reason = post.recommendation_reason
    if not reason:
        return ["confirmed_feature_missing: おすすめ理由が空"]
    errors: list[str] = []
    required_by_type = {
        "wipes": [attributes.short_product_label],
        "swaddle": [attributes.short_product_label],
        "nursing_support": [
            "ハンズフリー"
            if "hands_free" in attributes.confirmed_features
            else "哺乳瓶ホルダー"
            if "bottle_holder" in attributes.confirmed_features
            else "授乳"
        ],
        "baby_bedding": [attributes.short_product_label],
        "baby_care": [
            "ケア"
            if attributes.short_product_label in {"ベビーケア用品", "ベビーケアセット"}
            else attributes.short_product_label
        ],
        "baby_sleep": [attributes.short_product_label],
        "diaper": [attributes.short_product_label],
        "formula": ["ミルク"],
        "sound_blocks": ["積み木", "音"],
        "wooden_blocks": ["木製積み木"],
        "magnetic_blocks": ["マグネットブロック"],
        "baby_walker_toy": [attributes.short_product_label],
        "activity_cube": ["アクティビティキューブ"],
        "ring_toy": ["リング", "紐通し"],
        "kids_camera": ["キッズカメラ"],
        "sleep_light": ["ライト"],
        "soothing_plush": ["ぬいぐるみ"],
        "stroller_storage": ["ベビーカーバッグ"],
    }.get(attributes.product_type, [])
    if not all(term in reason for term in required_by_type):
        errors.append("content_type_mismatch: おすすめ理由の商品タイプが本文と一致しない")
        errors.append("おすすめ理由の商品タイプが本文と一致しない")
    forbidden_reason_terms = {
        "wipes": ["ベビーカーバッグ", "持ち運び・収納"],
        "diaper": ["ベビーカーバッグ", "持ち運び・収納"],
        "swaddle": ["紙おむつ", "おむつ", "授乳サポート", "ベビーカーバッグ"],
        "nursing_support": ["紙おむつ", "おむつ", "スワドル", "抱っこ布団"],
        "baby_bedding": ["紙おむつ", "おむつ", "授乳サポート", "スワドル"],
        "baby_care": ["紙おむつ", "授乳サポート", "スワドル", "必ず", "治る"],
        "baby_sleep": ["紙おむつ", "授乳サポート", "必ず寝る", "泣き止む", "安眠"],
        "soothing_plush": ["紙おむつ", "授乳クッション", "スワドル"],
        "formula": ["パーツ", "ベビーカーバッグ"],
        "sound_blocks": ["消耗品", "ストック需要"],
        "wooden_blocks": ["消耗品", "ストック需要", "手押し車", "ファーストウォーカー", "ベビーウォーカー"],
        "magnetic_blocks": ["消耗品", "ストック需要", "手押し車", "ファーストウォーカー", "ベビーウォーカー"],
        "baby_walker_toy": ["消耗品", "ストック需要", "積み木", "マグネットブロック", "歩けるようになる", "成長が早まる"],
        "activity_cube": ["消耗品", "ストック需要"],
        "ring_toy": ["消耗品", "ストック需要"],
    }.get(attributes.product_type, [])
    if any(term in reason for term in forbidden_reason_terms):
        errors.append("content_type_mismatch: おすすめ理由に別商品タイプの訴求が混入")
        errors.append("おすすめ理由に別商品タイプの訴求が混入")
    if re.search(r"(?:サイズ|枚|個|ピース|ポケット)\s*\d(?=\D|$)", reason):
        errors.append("short_name_unresolved: おすすめ理由の商品名が途中で切れている")
        errors.append("おすすめ理由の商品名が途中で切れている")
    if f"{attributes.short_product_label}の{attributes.short_product_label}" in reason:
        errors.append("duplicate_short_label: おすすめ理由で短縮商品名が重複")
    return errors


def duplicate_errors(post: GeneratedPost, context: GenerationContext) -> list[str]:
    errors: list[str] = []
    titles = context.used_titles | context.historical_titles
    bodies = context.used_bodies + context.historical_bodies
    if post.title in titles:
        errors.append("同一タイトル")
    post_hash = text_hash(post.body)
    normalized = normalize_text(post.body)
    opening = first_two_sentences(post.body)
    sentences = split_sentences(post.body)
    if sentences and normalize_text(sentences[0]) in context.used_openings:
        errors.append("書き出し完全一致")
    similarities = [
        syntax_similarity(post.body, previous)
        for previous in context.used_bodies
    ]
    post.structure_similarity = max(similarities, default=0.0)
    if post.structure_similarity >= 0.75:
        errors.append("正規化構文類似度0.75以上")
    construction = construction_family(post.body)
    if construction and context.construction_counts.get(construction, 0) >= 2:
        errors.append("同一接続構文が同一実行内で3回以上")
    ending = ending_family(post.body)
    if ending and context.ending_counts.get(ending, 0) >= 3:
        errors.append(f"締め語尾「{ending}」が同一実行内で4回以上")
    for body in bodies:
        if text_hash(body) == post_hash:
            errors.append("本文完全一致")
        if normalize_text(body) == normalized:
            errors.append("正規化本文完全一致")
        if first_two_sentences(body) == opening:
            errors.append("先頭2文一致")
        other_sentences = split_sentences(body)
        if len(set(sentences) & set(other_sentences)) >= 3:
            errors.append("4文中3文以上一致")
        if similarity(post.body, body) >= 0.75:
            errors.append("本文類似度0.75以上")
    return list(dict.fromkeys(errors))


def quality_score(
    post: GeneratedPost,
    attributes: ProductAttributes,
    errors: list[str],
) -> QualityScore:
    specificity = 15 if confirmed_feature_phrase(attributes) in post.body else 8
    naturalness = 10 if len(split_sentences(post.body)) in {3, 4} else 4
    non_template = max(0, 10 - post.rewrite_count * 2)
    compliance = 0 if errors else 20
    score = 15 + 15 + naturalness + specificity + 10 + non_template + compliance
    if errors:
        score = min(score, 59)
    return QualityScore(
        score=score,
        empathy=15,
        benefit=15,
        naturalness=naturalness,
        specificity=specificity,
        room_fit=10,
        non_template=non_template,
        compliance=compliance,
        improvement_comment=" / ".join(errors),
    )


def split_sentences(body: str) -> list[str]:
    return [part.strip() for part in re.findall(r"[^。！？]+[。！？]", body) if part.strip()]


def first_two_sentences(body: str) -> str:
    return "".join(split_sentences(body)[:2])


def normalize_text(value: str) -> str:
    return re.sub(r"[\s、。！？,.#]", "", value).lower()


def structure_signature(body: str) -> str:
    sentences = split_sentences(body)
    sentence_parts: list[str] = []
    connector_words = [
        "なら",
        "なので",
        "使い",
        "ため",
        "一方",
        "すると",
        "し",
        "ながら",
        "合わせて",
        "含めて",
    ]
    for index, sentence in enumerate(sentences):
        connectors = "+".join(word for word in connector_words if word in sentence) or "none"
        length_bucket = min(5, len(sentence) // 20)
        role = (
            "problem"
            if index == 0
            else "closing"
            if index == len(sentences) - 1
            else "feature"
            if index == 1
            else "benefit"
        )
        sentence_parts.append(f"{role}:{connectors}:L{length_bucket}")
    return f"{len(sentences)}|" + "|".join(sentence_parts) + f"|end:{ending_family(body) or 'other'}"


def syntax_similarity(left: str, right: str) -> float:
    left_sentences = split_sentences(left)
    right_sentences = split_sentences(right)
    if not left_sentences or not right_sentences:
        return 0.0
    score = 0.0
    if len(left_sentences) == len(right_sentences):
        score += 0.25
    if normalize_text(left_sentences[0]) == normalize_text(right_sentences[0]):
        score += 0.30
    left_connectors = connector_set(left)
    right_connectors = connector_set(right)
    union = left_connectors | right_connectors
    if union:
        score += 0.20 * (len(left_connectors & right_connectors) / len(union))
    left_lengths = [len(sentence) // 20 for sentence in left_sentences]
    right_lengths = [len(sentence) // 20 for sentence in right_sentences]
    if len(left_lengths) == len(right_lengths):
        distance = sum(abs(a - b) for a, b in zip(left_lengths, right_lengths))
        score += 0.15 * max(0.0, 1.0 - distance / max(1, len(left_lengths) * 3))
    if ending_family(left) and ending_family(left) == ending_family(right):
        score += 0.15
    if construction_family(left) and construction_family(left) == construction_family(right):
        score += 0.10
    return round(min(1.0, score), 3)


def connector_set(body: str) -> set[str]:
    return {
        word
        for word in [
            "なら",
            "なので",
            "を使い",
            "ため",
            "一方",
            "すると",
            "し",
            "ながら",
            "合わせて",
            "含めて",
        ]
        if word in body
    }


def construction_family(body: str) -> str:
    second = split_sentences(body)[1] if len(split_sentences(body)) >= 2 else ""
    if "なら" in second and "し" in second and "すると" in second:
        return "なら-し-すると"
    if "なので" in second and "し" in second:
        return "なので-し"
    return ""


def ending_family(body: str) -> str:
    sentences = split_sentences(body)
    closing = sentences[-1] if sentences else ""
    for phrase in ENDING_LIMIT_PHRASES:
        if closing.endswith(phrase + "。") or closing.endswith(phrase):
            return phrase
    for phrase in [
        "見ておきたいです",
        "確かめておきたいです",
        "候補です",
        "見比べられます",
        "決めやすくなります",
    ]:
        if closing.endswith(phrase + "。") or closing.endswith(phrase):
            return phrase
    return ""


def text_hash(value: str) -> str:
    return hashlib.sha256(normalize_text(value).encode("utf-8")).hexdigest()


def similarity(left: str, right: str) -> float:
    return SequenceMatcher(None, normalize_text(left), normalize_text(right)).ratio()


def has_repeated_meaning(sentences: list[str]) -> bool:
    for index, sentence in enumerate(sentences):
        for other in sentences[index + 1 :]:
            if similarity(sentence, other) >= 0.82:
                return True
    return False


def has_repeated_long_phrase(sentences: list[str]) -> bool:
    for index, sentence in enumerate(sentences):
        for other in sentences[index + 1 :]:
            match = SequenceMatcher(None, normalize_text(sentence), normalize_text(other)).find_longest_match()
            if match.size >= 16:
                return True
    return False


def stable_index(value: str, size: int) -> int:
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()
    return int(digest[:8], 16) % size


def error_code(error: str) -> str:
    if ":" in error and re.match(r"^[a-z_]+:", error):
        return error.split(":", 1)[0]
    mappings = [
        ("ハッシュタグが5件", "hashtag_count_below_5"),
        ("ハッシュタグの根拠がない", "hashtag_unconfirmed_attribute"),
        ("ハッシュタグ", "hashtag_product_type_mismatch"),
        ("同じ意味", "duplicate_phrase"),
        ("同じ表現", "duplicate_phrase"),
        ("授乳クッションの授乳クッション", "duplicate_short_label"),
        ("対応する固定ルールがない", "unsupported_product_type"),
        ("商品タイプ不一致", "content_type_mismatch"),
        ("タイトル", "title_content_mismatch"),
        ("確認済み特徴がない", "confirmed_feature_missing"),
        ("購入前確認点", "purchase_checkpoint_mismatch"),
    ]
    for marker, code in mappings:
        if marker in error:
            return code
    return "quality_error"


def duplicate_summary(errors: list[str]) -> str:
    duplicates = [
        error
        for error in errors
        if any(term in error for term in ["一致", "類似度", "重複"])
    ]
    return "重複なし" if not duplicates else " / ".join(duplicates)
