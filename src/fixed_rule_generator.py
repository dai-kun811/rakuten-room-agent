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
MAX_GENERATION_ATTEMPTS = 5
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

TYPE_KEYWORDS = {
    "wipes": ["おしりふき", "手口ふき", "手口拭き"],
    "swaddle": ["おくるみ", "スワドル", "モロー反射", "新生児スリーパー", "ねくるみ"],
    "nursing_support": ["授乳サポート", "ハンズフリー授乳", "授乳クッション", "ミルクサポート", "哺乳瓶ホルダー"],
    "baby_bedding": ["抱っこ布団", "ねんねクッション", "ベビー布団", "背中スイッチ対策", "寝かしつけクッション"],
    "diaper": ["紙おむつ", "紙オムツ", "おむつ", "オムツ", "パンツタイプ", "テープタイプ", "新生児用おむつ", "おむつ替え"],
    "formula": ["粉ミルク", "液体ミルク", "フォローアップミルク"],
    "sound_blocks": ["音が鳴る積み木", "音の鳴る積み木", "音入り積み木"],
    "magnetic_blocks": ["マグネットブロック", "磁石ブロック", "マグネット"],
    "activity_cube": ["アクティビティキューブ", "ルーピング", "型はめ"],
    "ring_toy": ["リングテン", "ring10", "リング玩具", "紐通し"],
    "kids_camera": ["キッズカメラ", "子ども用カメラ"],
    "sleep_light": ["ホワイトノイズ", "授乳ライト", "寝かしつけライト"],
    "stroller_storage": ["ベビーカーバッグ", "ベビーカー用バッグ", "ベビーカー収納"],
    "wooden_blocks": ["木製積み木", "木の積み木", "積み木", "つみき"],
}

PROHIBITED_BY_TYPE = {
    "wipes": ["授乳", "お腹を空かせた", "缶", "サイズアップ"],
    "swaddle": ["紙おむつ", "おむつ替え", "パンツタイプ", "テープタイプ", "授乳が楽", "必ず寝る", "泣き止む", "背中スイッチがなくなる"],
    "nursing_support": ["紙おむつ", "おむつ替え", "パンツタイプ", "寝かしつけ", "必ず楽になる", "必ず飲める"],
    "baby_bedding": ["紙おむつ", "パンツタイプ", "授乳サポート", "必ず寝る", "背中スイッチがなくなる", "泣き止む"],
    "diaper": ["授乳", "食後に拭く", "缶", "お腹を空かせた", "スワドル", "おくるみ", "抱っこ布団"],
    "formula": ["おむつ替え", "枚数", "パーツ", "手口ふき"],
    "sound_blocks": ["マグネット", "紐通し", "ルーピング"],
    "wooden_blocks": ["音が鳴る", "マグネット", "ルーピング"],
    "magnetic_blocks": ["木製つみき", "音が鳴る", "紐通し", "ルーピング"],
    "activity_cube": ["マグネットブロック", "リングテン", "音が鳴る積み木", "木製つみき"],
    "ring_toy": ["マグネットブロック", "ルーピング", "キッズカメラ"],
    "kids_camera": ["出産祝い", "赤ちゃんの毎日", "消耗品", "ストック"],
    "sleep_light": ["必ず寝る", "泣き止む", "ベビーカーグッズ", "履き心地"],
    "stroller_storage": ["寝かしつけ", "授乳ライト", "知育玩具", "お腹を空かせた"],
}

CHECKPOINTS = {
    "wipes": ["枚数", "個数", "価格", "収納場所"],
    "swaddle": ["サイズ", "素材", "着せ方", "洗濯方法"],
    "nursing_support": ["対応する哺乳瓶", "固定方法", "洗濯方法", "使用場所"],
    "baby_bedding": ["サイズ", "素材", "洗濯方法", "置き場所"],
    "diaper": ["サイズ", "枚数", "1枚あたり価格", "収納場所"],
    "formula": ["容量", "個数", "価格", "賞味期限", "収納場所"],
    "sound_blocks": ["対象年齢", "パーツサイズ", "収納場所", "名入れの有無"],
    "wooden_blocks": ["対象年齢", "パーツサイズ", "個数", "収納場所"],
    "magnetic_blocks": ["対象年齢", "パーツサイズ", "パーツ数", "収納場所"],
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
    "diaper": ["#紙おむつ", "#大容量パック", "#ストック管理", "#夜のおむつ替え", BRAND_TAG],
    "formula": ["#粉ミルク", "#まとめ買い", "#残量管理", "#夜間授乳", BRAND_TAG],
    "sound_blocks": ["#積み木", "#音の鳴るおもちゃ", "#手先遊び", "#1歳プレゼント", BRAND_TAG],
    "wooden_blocks": ["#木製積み木", "#積み木遊び", "#手先遊び", "#おうち遊び", BRAND_TAG],
    "magnetic_blocks": ["#マグネットブロック", "#立体遊び", "#創造遊び", "#おうち遊び", BRAND_TAG],
    "activity_cube": ["#アクティビティキューブ", "#型はめ", "#手先遊び", "#1歳おもちゃ", BRAND_TAG],
    "ring_toy": ["#紐通し", "#リング遊び", "#指先遊び", "#木のおもちゃ", BRAND_TAG],
    "kids_camera": ["#キッズカメラ", "#スマホ転送", "#子ども目線", "#誕生日プレゼント", BRAND_TAG],
    "sleep_light": ["#ホワイトノイズ", "#授乳ライト", "#夜の育児", "#寝室づくり", BRAND_TAG],
    "stroller_storage": ["#ベビーカーバッグ", "#荷物整理", "#子連れ外出", "#ベビーカー収納", BRAND_TAG],
}
HAND_WIPES_HASHTAGS = ["#手口ふき", "#まとめ買い", "#食後ケア", "#子連れ外出", BRAND_TAG]

SCENE_DETAILS = {
    "wipes": "家族が見ても残量が分かる置き方にすると、補充の声かけもしやすくなります",
    "swaddle": "着せ方や洗い替えを先に決めておくと、夜の準備を家族で共有しやすくなります",
    "nursing_support": "置く場所と使う人を決めておくと、授乳前の準備をそろえやすくなります",
    "baby_bedding": "使う場所と洗濯後の置き場を決めておくと、寝かしつけ前の準備をまとめやすくなります",
    "diaper": "交換場所ごとの残りを見えるようにすると、次に開けるパックも決めやすくなります",
    "formula": "未開封分を同じ場所へまとめると、次の買い足し時期も家族で共有しやすくなります",
    "sound_blocks": "振った音を聞いてから積むなど、12ピースの使い方を変えられます",
    "wooden_blocks": "積む・並べる・形を作る遊びへ使い分けられます",
    "magnetic_blocks": "48ピースを平面に並べたり立体へ組んだり、作る形を変えられます",
    "activity_cube": "遊ぶ面を一つずつ変えると、子どもが選んだ動きを親も見守りやすくなります",
    "ring_toy": "色や数の言葉を添えながら並べると、親子で同じ動きを共有しやすくなります",
    "kids_camera": "撮った後に一枚選ぶ時間を作ると、その日に気になった景色も聞きやすくなります",
    "sleep_light": "授乳時は灯り、寝室ではホワイトノイズと、必要な機能を使い分けられます",
    "stroller_storage": "ポケットごとに小物を分けると、必要な物の位置を決めやすくなります",
}

FEATURE_MARKERS = {
    "thick": ["厚手"],
    "water_rich": ["水分量"],
    "pants": ["パンツタイプ"],
    "tape": ["テープタイプ"],
    "powder": ["粉ミルク"],
    "liquid": ["液体ミルク"],
    "follow_up": ["フォローアップミルク"],
    "sound": ["音が鳴る", "音の鳴る"],
    "wood": ["木製", "木のおもちゃ"],
    "name_option": ["名入れ"],
    "magnetic": ["マグネット", "磁石"],
    "shape_sorter": ["型はめ"],
    "looping": ["ルーピング"],
    "ring": ["リング"],
    "lacing": ["紐通し"],
    "smartphone_transfer": ["スマホ転送"],
    "sd_card": ["SDカード"],
    "game_free": ["ゲームなし"],
    "white_noise": ["ホワイトノイズ"],
    "nursing_light": ["授乳ライト"],
    "cordless": ["コードレス"],
    "usb_charge": ["USB充電"],
    "waterproof": ["防水"],
    "lightweight": ["軽量"],
    "pockets": ["ポケット"],
    "storage_bag": ["収納袋"],
    "swaddle": ["おくるみ", "スワドル", "ねくるみ"],
    "moro_reflex": ["モロー反射"],
    "sleeper": ["スリーパー"],
    "hands_free": ["ハンズフリー"],
    "nursing_cushion": ["授乳クッション"],
    "milk_support": ["ミルクサポート", "ミルク屋さん"],
    "bottle_holder": ["哺乳瓶ホルダー"],
    "hug_futon": ["抱っこ布団"],
    "sleep_cushion": ["ねんねクッション", "寝かしつけクッション"],
    "baby_futon": ["ベビー布団"],
    "back_switch": ["背中スイッチ"],
    "cotton": ["綿100", "コットン100", "コットン"],
    "double_gauze": ["ダブルガーゼ"],
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
        ["最後の1個で焦りたくない", "おむつ替えの在庫を整えたい", "食後にも使う分を備えたい", "外出分まで切らしたくない", "買い足す回数を減らしたい", "置ける量から備えたい", "残り少ない日に慌てない", "消耗品の補充をまとめたい"],
        ["おしりふきが残り少ないと、忙しい日に買い足す時間まで気になりますよね。", "おむつ替えが続く時期は、手元の残り枚数を何度も確認しがちです。", "食後の手口ふきにも使う家庭では、想像より早く減ることがありますよね。", "外出用と家用を分けると、どちらかの補充を忘れやすくなります。", "育児の消耗品は、必要な日に限って切らしたくないものです。"],
        ["{feature}なら、おむつ替えや食後に使う分をまとめて管理できます。", "{feature}を家と外出用に分けて置けば、必要な場所から取り出せます。", "{feature}なので、毎日の使用量を見ながら補充時期を決められます。", "{feature}を収納場所に合わせて備えると、残量を把握しやすくなります。", "{feature}なら、買い足す単位を先に決めておけます。"],
        ["補充のタイミングを家族で共有しやすくなり、買い忘れへの焦りを減らせます。", "使う場所ごとに分けておくと、おむつ替えの途中で探す手間を抑えられます。", "一度に届く量が分かれば、次に買う時期も組み立てやすくなります。", "食後とおむつ替えの両方で使う家庭でも、在庫の見通しを立てやすいです。", "残り少ない日に慌てて選ばず、普段の使用量に合わせて備えられます。"],
        ["{checks}を見て、家に置きやすい量か確認したいです。", "{checks}を比べて、使い切れる単位を選びたいです。", "{checks}を確認し、収納を圧迫しないか考えたいです。", "{checks}を見ながら、家用と外出用の配分を決めたいです。", "{checks}を確かめて、次の補充まで無理のないセットを選びたいです。"],
        [("おしりふき",), ("おむつ替え",), ("食後",), ("外出",), ("買い足",), ("備え",), ("残り",), ("消耗品",)],
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
        ["ミルクの残りが少ない夜は、次の授乳分が足りるか気になりますよね。", "夜間授乳が続く時期は、残量確認と買い足しが後回しになりがちです。", "ミルクは毎日の使用量が変わると、備える個数にも迷います。", "まとめて買いたい一方で、賞味期限までに使い切れるかも確認したいです。", "外出用を取り分ける家庭では、家に残る量を見失いやすくなります。"],
        ["{feature}なら、授乳回数を目安に残量を管理できます。", "{feature}を夜間用と日中用に分けると、次に開ける分を把握できます。", "{feature}なので、買い足す時期を授乳のペースに合わせられます。", "{feature}を収納場所へまとめれば、未開封の残りを確認しやすくなります。", "{feature}なら、外出用を用意した後の残量も見通せます。"],
        ["次の授乳分を気にして慌てず、家族とも残量を共有しやすくなります。", "開封前の個数が分かれば、夜に足りない不安を減らせます。", "授乳ペースに合う量なら、買い過ぎを避けながら備えられます。", "置き場所を決めると、次に使う分を迷わず取り出せます。", "外出分と家用を分けても、補充のタイミングを決めやすくなります。"],
        ["{checks}を見て、飲む量に合うセットか確認したいです。", "{checks}を比べて、期限内に使い切れる個数を選びたいです。", "{checks}を確認し、夜間に取り出しやすい量か考えたいです。", "{checks}を見ながら、家用と外出用の配分を決めたいです。", "{checks}を確かめて、収納できる範囲で備えたいです。"],
        [("ミルク",), ("夜間授乳",), ("ミルク",), ("量",), ("授乳",), ("外出",), ("賞味期限",), ("ミルク",)],
    ),
    "swaddle": _patterns(
        "swaddle",
        ["夜の{label}準備に", "{label}の着せ方を見たい", "新生児期の夜支度に", "洗い替えまで考えたい", "手が出せる形を比べたい", "モロー反射期の準備に", "{label}を無理なく使う", "退院後の夜支度に"],
        ["夜の育児では、着せる物を迷わず準備できるか気になりますよね。", "おくるみやスワドルは、赤ちゃんの体格に合うサイズか先に見たいです。", "新生児期に使う布ものは、洗い替えや着せ方まで含めて考えたいです。", "モロー反射の時期に使うなら、商品情報にある形と素材を確認したいです。", "退院後の夜支度は、着せる順番や洗濯後の戻し方も決めておきたいです。"],
        ["{feature}なら、夜の支度で使う布ものを一つに決めやすくなります。", "{feature}を確認しておくと、着せ方と洗い替えの準備を考えられます。", "{feature}なので、体格に合うサイズか商品ページで見比べられます。", "{feature}を使う前提で、寝室や洗濯後の置き場所を決められます。", "{feature}なら、新生児期に必要な枚数を家の洗濯ペースから考えられます。"],
        ["素材と形が分かると、家庭の夜の流れに合うか判断しやすくなります。", "洗濯後に戻す場所まで決めれば、夜の準備を家族で共有しやすいです。", "着せ方を事前に見ておくと、使う場面を具体的に想像できます。", "サイズ感を確認しておけば、買い足しや洗い替えの候補も絞りやすいです。", "商品情報の範囲で特徴を見られるので、必要な仕様だけを比較できます。"],
        ["{checks}を見て、今の月齢と洗濯ペースに合うか確認したいです。", "{checks}を比べ、夜の支度に無理なく組み込めるか見ておきたいです。", "{checks}を確認し、洗い替えを含めて必要な枚数を考えたいです。", "{checks}を見ながら、着せ方を商品ページで確かめておきたいです。", "{checks}を確かめて、家の寝室準備に合う候補か比べたいです。"],
        [("夜",), ("着せ方",), ("新生児",), ("洗い替え",), ("手",), ("モロー反射",), ("使う",), ("夜",)],
        ("紙おむつ", "おむつ替え", "パンツタイプ", "テープタイプ"),
    ),
    "nursing_support": _patterns(
        "nursing_support",
        ["授乳前の準備を整えたい", "{label}を置く場所から", "哺乳瓶まわりを整える", "ミルク時間の支度に", "使う人で条件をそろえる", "固定方法まで見て選ぶ", "授乳サポートを比べたい", "洗濯後の戻し方まで"],
        ["ミルクの準備中は、哺乳瓶まわりの置き方まで迷うことがありますよね。", "ハンズフリー授乳系の商品は、使う場所と固定方法を先に確認したいです。", "授乳サポートを選ぶなら、哺乳瓶やクッションの仕様が家庭に合うか気になります。", "夜や日中に使う物ほど、洗濯後に戻す場所まで決めておきたいです。", "複数人で授乳をする家庭では、使い方を共有しやすい形か見たいです。"],
        ["{feature}なら、授乳前に使う物の置き方を考えやすくなります。", "{feature}を確認しておくと、哺乳瓶や使う場所との相性を見られます。", "{feature}なので、固定方法や洗濯後の扱いを事前に比べられます。", "{feature}を使う前提で、日中と夜の置き場所を決められます。", "{feature}なら、家族で使う時の準備手順をそろえやすくなります。"],
        ["対応する哺乳瓶や設置場所が分かると、使う場面を具体的に想像できます。", "固定方法を先に確認すれば、家庭の授乳場所に合うか判断しやすいです。", "洗濯や手入れの条件まで見れば、続けて使えるか比べやすくなります。", "置く場所を決めておくと、授乳前に探す物を減らしやすいです。", "使う人が変わっても準備を共有できるか、商品情報から確認できます。"],
        ["{checks}を見て、家の授乳場所に合う仕様か確認したいです。", "{checks}を比べ、哺乳瓶や使う姿勢に合うか見ておきたいです。", "{checks}を確認し、洗濯や手入れまで無理がないか考えたいです。", "{checks}を見ながら、夜と日中の置き場所を決めたいです。", "{checks}を確かめて、使う人が共有しやすい候補か比べたいです。"],
        [("授乳",), ("置く場所",), ("哺乳瓶",), ("ミルク",), ("使う人",), ("固定方法",), ("授乳サポート",), ("洗濯",)],
        ("紙おむつ", "パンツタイプ", "寝かしつけ", "必ず"),
    ),
    "baby_bedding": _patterns(
        "baby_bedding",
        ["抱っこ布団の置き場まで", "{label}を洗い替え込みで", "寝かしつけ前の準備に", "背中スイッチ期の候補に", "日中のねんね場所を整える", "洗える布団を比べたい", "赤ちゃんの置き場を決める", "使う場所から寝具を選ぶ"],
        ["寝かしつけ前は、赤ちゃんを置く場所と布ものの準備が続きますよね。", "抱っこ布団やねんねクッションは、家のどこで使うか先に考えたいです。", "洗えるベビー寝具を選ぶなら、乾かす場所や洗い替えも気になります。", "背中スイッチ対策と書かれた商品でも、まず仕様と使う場所を確認したいです。", "日中のねんね場所を作るなら、サイズと素材を商品情報で見たいです。"],
        ["{feature}なら、寝かしつけ前に使う布ものを準備しやすくなります。", "{feature}を確認しておくと、置き場所と洗濯後の戻し方を考えられます。", "{feature}なので、家のスペースに合うか商品ページで見比べられます。", "{feature}を使う前提で、日中と夜の置き場所を決められます。", "{feature}なら、洗い替えを含めた準備量を想像しやすいです。"],
        ["サイズと素材が分かると、寝室やリビングで使う場面を具体的に考えられます。", "洗濯後の置き場所まで決めれば、使う前後の流れを整えやすいです。", "家のスペースに収まるか見ておくことで、出しっぱなしになりにくい候補を選べます。", "商品情報にある範囲で比べれば、必要な仕様を落ち着いて確認できます。", "使う場所を先に決めると、購入後の置き方まで想像しやすいです。"],
        ["{checks}を見て、家の置き場所と洗濯ペースに合うか確認したいです。", "{checks}を比べ、寝室やリビングで無理なく使えるか見ておきたいです。", "{checks}を確認し、洗い替えや収納まで含めて考えたいです。", "{checks}を見ながら、使う場所に合うサイズか商品ページで確かめたいです。", "{checks}を確かめて、日中と夜の使い分けに合う候補か比べたいです。"],
        [("置き場",), ("洗い替え",), ("寝かしつけ",), ("背中スイッチ",), ("日中",), ("洗える",), ("置き場",), ("場所",)],
        ("紙おむつ", "パンツタイプ", "授乳サポート", "必ず寝る", "泣き止む"),
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
        f"{title_terms[0]}遊びを楽しむ時間に",
        f"{title_terms[1]}遊びから広げる",
        f"{label}で手先遊びを",
        f"親子で{title_terms[0]}を試したい",
        f"{label}で{title_terms[1]}を試したい",
        f"今の成長に合う{label}を",
        f"{label}の遊び方を増やしたい",
        f"雨の日にも{title_terms[0]}を楽しむ",
    ]
    problems = [
        f"{label}は、今の成長に合う遊び方があるか気になりますよね。",
        f"家遊びに{label}を選ぶなら、具体的に何をして遊べるか比べたいです。",
        f"{title_terms[0]}を楽しむおもちゃは、最初の遊び方が分かりやすいか迷います。",
        f"長く置く{label}ほど、遊びを変えられるか確認したいですよね。",
        f"親子で{title_terms[1]}を試すなら、大人が遊び方を見せながら取り組めるかも気になります。",
        f"雨の日に{label}で遊ぶなら、同じ動きだけで終わらない工夫が欲しいです。",
    ]
    scenes = [
        f"{{feature}}で、{action_phrase}遊びを家の中で試せます。",
        f"{{feature}}を使い、{scene_phrase}遊びへ切り替えられます。",
        f"{{feature}}なら、親が見本を見せながら{action_phrase}動きを試せます。",
        f"{{feature}}を机や床に出し、{scene_phrase}時間を作れます。",
        f"{{feature}}なので、子どもの反応に合わせて{action_phrase}遊びを選べます。",
        f"雨の日に{{feature}}を広げ、{scene_phrase}時間を作れます。",
    ]
    benefits = [
        f"{benefit_phrase}きっかけを作りやすく、親も隣で言葉を添えられます。",
        f"遊び方を一つずつ見せることで、{benefit_phrase}動きを試しやすくなります。",
        f"子どもの選び方を見ながら、{benefit_phrase}場面を親子で共有できます。",
        f"同じ道具でも動かし方を変えられ、{benefit_phrase}遊びを続けやすいです。",
        f"同じパーツでも置き方を変えられ、{benefit_phrase}遊びを続けられます。",
    ]
    closings = [
        "{checks}を見て、今の月齢で扱いやすい条件か商品ページで確かめておきたいです。",
        "{checks}を比べ、家の遊ぶ場所へ無理なく置けるか見ておきたいです。",
        "{checks}を確認して、最初に試す遊びを決めやすい候補です。",
        "{checks}まで含めて、本体やパーツを出し入れしやすいか確かめておきたいです。",
        "{checks}を手がかりに、今の遊びへ取り入れやすいか商品ページで見比べられます。",
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
            scene=scenes[5].replace("雨の日に", "") if index == 7 else scenes[(index + scene_offset) % len(scenes)],
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
        "wooden_blocks": _toy_patterns("wooden_blocks", "木製積み木", ["積む", "形"], "積む・並べる・形を作る", "組み方を変える", "形を考える"),
        "magnetic_blocks": _toy_patterns("magnetic_blocks", "マグネットブロック", ["組み立て", "立体"], "平面から立体へ組み立てる", "形を組み替える", "組み合わせを考える"),
        "activity_cube": _toy_patterns("activity_cube", "アクティビティキューブ", ["型はめ", "ルーピング"], "型はめやルーピングを切り替える", "複数の遊びを選ぶ", "指先を使う"),
        "ring_toy": _toy_patterns("ring_toy", "リング玩具", ["リング", "紐通し"], "積む・並べる・紐へ通す", "色分けや数遊びを試す", "指先を使う"),
    }
)

PATTERNS.update(
    {
        "kids_camera": _patterns(
            "kids_camera",
            ["子ども目線を写真に残したい", "ゲームなしで写真遊びを", "外出先を子どもが撮る", "旅行の景色を一緒に残す", "撮った写真を親子で見る", "スマホへ移せるカメラを", "子ども用カメラを選びたい", "誕生日に写真のきっかけを"],
            ["子ども用カメラは、撮ることに集中できる機能か気になりますよね。", "外出先で持たせるなら、撮った後の扱い方まで確認したいです。", "旅行の思い出は、大人とは違う子ども目線でも残してみたいですよね。", "キッズカメラは、家庭で写真を移す方法が合うか迷います。", "誕生日の贈り物なら、遊ぶ場面が具体的に浮かぶものを選びたいです。"],
            ["{feature}なら、外出先で子ども自身が気になった景色を撮れます。", "{feature}を使い、撮った写真を帰宅後に親子で見返せます。", "{feature}なので、旅行や散歩で写真遊びを始められます。", "{feature}なら、旅行で撮った写真の保存や移動を家庭の方法に合わせられます。", "{feature}を持たせ、子どもが選んだ被写体を一緒に見られます。"],
            ["何を撮ったか聞く時間ができ、子どもの見ていた景色を知るきっかけになります。", "帰宅後に写真を選ぶことで、外出の出来事を親子で振り返れます。", "大人が構図を決めず、子ども自身が残したいものを選べます。", "保存方法が家庭に合えば、撮ったままにせず写真を整理しやすいです。", "誕生日後も散歩や旅行へ持ち出すなど、使う場面を増やせます。"],
            ["{checks}を見て、家庭で扱いやすい仕様か確認したいです。", "{checks}を比べて、外出先で使う流れに合うか見たいです。", "{checks}を確認し、撮影後の保存方法まで考えたいです。", "{checks}を見ながら、持ち歩きやすいサイズか確かめたいです。", "{checks}を確かめて、写真を見返しやすい一台を選びたいです。"],
            [("子ども目線",), ("ゲームなし",), ("外出",), ("旅行",), ("写真",), ("スマホ",), ("カメラ",), ("写真",)],
            ("消耗品", "ストック"),
        ),
        "sleep_light": _patterns(
            "sleep_light",
            ["夜の授乳環境を整えたい", "寝室の音と灯りをまとめる", "おむつ替えの手元を照らす", "寝かしつけ前の準備を短く", "ホワイトノイズを寝室に", "授乳ライトを夜の手元に", "コードレスで置き場所を選ぶ", "夜の育児動線を整えたい"],
            ["夜の授乳は、部屋を明るくし過ぎず手元を見たいですよね。", "寝かしつけ前は、音と灯りを別々に準備するのが手間になることがあります。", "夜のおむつ替えでは、必要な場所だけ照らせるかが気になります。", "寝室で使う機器は、電源と置き場所が夜の動線に合うか確認したいです。", "ホワイトノイズを寝室で使うなら、家庭に合う音量へ調整できるか気になります。"],
            ["{feature}なら、夜の授乳やおむつ替えで使う音と灯りを整えられます。", "{feature}を寝室へ置き、寝かしつけ前の準備を一か所にまとめられます。", "{feature}なので、夜に移動する場所へ合わせて設置できます。", "{feature}を使い、手元を見たい場面と音を流す場面を分けられます。", "{feature}なら、寝室の環境に合わせて使う機能を選べます。"],
            ["必要な機能を一台にまとめると、夜に探す物を減らしやすくなります。", "手元の準備が整っていると、授乳や交換の動きを始めやすいです。", "置き場所を決めておけば、暗い時間にも操作する位置を迷いにくいです。", "音と灯りを場面で使い分けることで、夜の育児動線を組み立てられます。", "家庭に合う設定を選べれば、夜に必要な機能を迷わず選びやすくなります。"],
            ["{checks}を見て、寝室に置きやすい仕様か確認したいです。", "{checks}を比べて、夜の動線に合うものを選びたいです。", "{checks}を確認し、授乳と交換の両方で使えるか考えたいです。", "{checks}を見ながら、操作しやすい設置場所を決めたいです。", "{checks}を確かめて、必要な機能に絞って選びたいです。"],
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
    short_label = short_label_for(product_type, product.text)
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
            else "授乳サポート"
        ),
        "baby_bedding": (
            "抱っこ布団"
            if "抱っこ布団" in text
            else "ねんねクッション"
            if "ねんねクッション" in text
            else "ベビー布団"
        ),
        "diaper": "紙おむつ",
        "formula": "ミルク",
        "sound_blocks": "音が鳴る積み木",
        "wooden_blocks": "木製積み木",
        "magnetic_blocks": "マグネットブロック",
        "activity_cube": "アクティビティキューブ",
        "ring_toy": "リング玩具",
        "kids_camera": "キッズカメラ",
        "sleep_light": "ホワイトノイズ付きライト" if "ホワイトノイズ" in text else "授乳ライト",
        "stroller_storage": "ベビーカーバッグ",
    }
    return labels.get(product_type, "")


def extract_attributes(product: Product) -> ProductAttributes:
    normalized, short_label, errors = clean_product_name(product)
    product_type = classify_product_type(product)
    text = product.text
    classification_keywords = matched_type_keywords(product_type, text)
    confirmed: list[str] = []
    for feature, markers in FEATURE_MARKERS.items():
        if any(marker.lower() in text for marker in markers):
            confirmed.append(feature)
    target_age_match = re.search(r"(\d+)\s*(?:歳|才)(?:\s*(?:から|以上|頃))?", text)
    target_age = f"{target_age_match.group(1)}歳" if target_age_match else ""
    quantities = list(
        dict.fromkeys(
            match.group(0)
            for match in re.finditer(
                r"\d+(?:\.\d+)?\s*(?:枚|個|本|缶|袋|箱|ピース|パーツ|ポケット|ml|mL|g|kg)",
                product.text,
                flags=re.IGNORECASE,
            )
        )
    )
    use_case_markers = {
        "wipes": [("おむつ替え", ["おむつ替え"]), ("食後", ["食後", "手口"]), ("外出", ["外出"])],
        "swaddle": [("夜の準備", ["夜", "夜間"]), ("着せ方", ["着る", "手が出せる", "足が出せる"]), ("洗い替え", ["洗える", "洗濯"])],
        "nursing_support": [("授乳準備", ["授乳"]), ("ミルク時間", ["ミルク", "哺乳瓶"]), ("固定して使う", ["固定", "ホルダー"])],
        "baby_bedding": [("寝かしつけ前", ["寝かしつけ"]), ("日中のねんね", ["日中", "ねんね"]), ("洗い替え", ["洗える", "洗濯"])],
        "diaper": [("夜間交換", ["夜間", "夜用"]), ("外出", ["外出"]), ("ストック", ["ストック", "まとめ買い"])],
        "formula": [("授乳", ["授乳"]), ("夜間", ["夜間", "夜"]), ("残量管理", ["残量"])],
        "sound_blocks": [("振る", ["振る"]), ("積む", ["積み木"]), ("並べる", ["並べる"])],
        "wooden_blocks": [("積む", ["積み木"]), ("並べる", ["並べる"]), ("形を作る", ["形"])],
        "magnetic_blocks": [("組み立て", ["組み立て"]), ("平面遊び", ["平面"]), ("立体遊び", ["立体"])],
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
    if attributes.product_type == "wipes":
        prefix = "厚手の" if "thick" in features else ""
        quantity_text = f"{quantity}入りの" if quantity else ""
        return f"{prefix}{quantity_text}{attributes.short_product_label}"
    if attributes.product_type == "swaddle":
        details = [
            label
            for key, label in [
                ("moro_reflex", "モロー反射期"),
                ("sleeper", "スリーパー型"),
                ("cotton", "コットン素材"),
            ]
            if key in features
        ]
        prefix = "・".join(details)
        return f"{prefix}の{attributes.short_product_label}" if prefix else attributes.short_product_label
    if attributes.product_type == "nursing_support":
        details = [
            label
            for key, label in [
                ("hands_free", "ハンズフリー"),
                ("bottle_holder", "哺乳瓶ホルダー"),
                ("nursing_cushion", "授乳クッション"),
                ("milk_support", "ミルクサポート"),
            ]
            if key in features
        ]
        prefix = "・".join(details)
        return f"{prefix}の{attributes.short_product_label}" if prefix else attributes.short_product_label
    if attributes.product_type == "baby_bedding":
        details = [
            label
            for key, label in [
                ("hug_futon", "抱っこ布団"),
                ("sleep_cushion", "ねんねクッション"),
                ("baby_futon", "ベビー布団"),
                ("double_gauze", "ダブルガーゼ"),
                ("cotton", "コットン素材"),
            ]
            if key in features
        ]
        if "back_switch" in features:
            details.append("背中スイッチ対策表記")
        prefix = "・".join(dict.fromkeys(details))
        return prefix or attributes.short_product_label
    if attributes.product_type == "diaper":
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
        functions = [
            label
            for key, label in [
                ("smartphone_transfer", "スマホ転送"),
                ("sd_card", "SDカード"),
                ("game_free", "ゲームなし"),
                ("usb_charge", "USB充電"),
            ]
            if key in features
        ]
        suffix = "に対応した" if functions else ""
        return f"{'・'.join(functions)}{suffix}キッズカメラ"
    if attributes.product_type == "sleep_light":
        functions = [label for key, label in [("white_noise", "ホワイトノイズ"), ("nursing_light", "授乳ライト")] if key in features]
        if "cordless" in features:
            return f"{'と'.join(functions)}を備え、コードレスで使えるライト"
        return f"{'と'.join(functions)}を備えたライト"
    if attributes.product_type == "stroller_storage":
        descriptors = [label for key, label in [("waterproof", "防水仕様"), ("lightweight", "軽量")] if key in features]
        pocket = f"{quantity}の" if quantity and "ポケット" in quantity else ""
        if descriptors and pocket:
            return f"{'・'.join(descriptors)}で、{pocket}ベビーカーバッグ"
        if descriptors:
            return f"{'・'.join(descriptors)}のベビーカーバッグ"
        return f"{pocket}ベビーカーバッグ"
    return attributes.short_product_label


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
        add("#新生児準備")
        add("#夜の育児", "夜" in combined)
        add("#洗い替え準備", "洗い替え" in combined or "洗濯" in combined)
    elif product_type == "nursing_support":
        add("#授乳サポート")
        add("#ハンズフリー授乳", "hands_free" in features)
        add("#哺乳瓶ホルダー", "bottle_holder" in features)
        add("#ミルク育児", "ミルク" in combined)
        add("#授乳準備")
    elif product_type == "baby_bedding":
        add("#抱っこ布団", "hug_futon" in features)
        add("#ねんねクッション", "sleep_cushion" in features)
        add("#ベビー布団", "baby_futon" in features)
        add("#寝かしつけ準備", "寝かしつけ" in combined)
        add("#洗い替え準備", "洗い替え" in combined or "洗濯" in combined)
        add("#ベビー寝具")
    elif product_type == "diaper":
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
        add("#立体遊び", "立体" in combined)
        add("#組み立て遊び")
        add("#おうち遊び")
        add("#創造遊び")
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
        add("#SDカード", "sd_card" in features)
        add("#ゲームなし", "game_free" in features)
        add("#誕生日プレゼント", "誕生日向け" in attributes.confirmed_gift_features and "誕生日" in combined)
        add("#子ども目線")
        add("#写真遊び")
    elif product_type == "sleep_light":
        add("#ホワイトノイズ", "white_noise" in features)
        add("#授乳ライト", "nursing_light" in features)
        add("#コードレス", "cordless" in features)
        add("#夜の育児", "夜" in combined)
        add("#寝室づくり", "寝室" in combined)
    elif product_type == "stroller_storage":
        add("#ベビーカーバッグ")
        add("#防水", "waterproof" in features)
        add("#軽量", "lightweight" in features)
        add("#ベビーカー収納")
        add("#子連れ外出", "外出" in combined)
        add("#荷物整理")

    safe_fallbacks = {
        "wipes": ["#育児消耗品", "#ストック管理"],
        "swaddle": ["#新生児準備", "#夜の育児"],
        "nursing_support": ["#授乳準備", "#ミルク育児"],
        "baby_bedding": ["#ベビー寝具", "#寝かしつけ準備"],
        "diaper": ["#おむつ替え", "#サイズ選び"],
        "formula": ["#授乳準備", "#ミルク育児"],
        "sound_blocks": ["#手先遊び", "#おうち遊び"],
        "wooden_blocks": ["#積み木遊び", "#おうち遊び"],
        "magnetic_blocks": ["#組み立て遊び", "#おうち遊び"],
        "activity_cube": ["#手先遊び", "#おうち遊び"],
        "ring_toy": ["#指先遊び", "#おうち遊び"],
        "kids_camera": ["#子ども目線", "#写真遊び"],
        "sleep_light": ["#夜の育児", "#寝室づくり"],
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
                list(attributes.extraction_errors) or ["対応する固定ルールがない商品タイプ"],
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
        last_post.quality_errors = list(
            dict.fromkeys(last_post.quality_errors + ["最大5回の再生成で品質条件を満たせない"])
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
        "wipes": f"{checks}を見て、普段の使用量と収納場所に無理がなく、次の補充まで使い切れるセットか見ておきたいです。",
        "swaddle": f"{checks}を見て、今の月齢や洗濯ペースに合い、夜の支度へ無理なく入れられるか確認しておきたいです。",
        "nursing_support": f"{checks}を比べ、哺乳瓶や使う場所に合い、授乳前の準備を共有しやすいか見ておきたいです。",
        "baby_bedding": f"{checks}を確認し、寝室やリビングの置き場所に合い、洗い替えまで用意しやすいか比べたいです。",
        "diaper": f"{checks}を比べ、収納場所も想像しながら、サイズアウト前に使い切れる量を選びたいです。",
        "formula": f"{checks}を見て、授乳ペースと置き場所に合い、期限内に使い切れる個数か確認しておきたいです。",
        "sound_blocks": f"{checks}を比べ、遊ぶ場所と片づけ方の両方に無理がなく、今の月齢で扱いやすいか見ておきたいです。",
        "wooden_blocks": f"{checks}を見て、遊ぶ場所と収納方法に合い、今の月齢から取り入れやすいか確かめる候補です。",
        "magnetic_blocks": f"{checks}を確認し、作りたい形に足りる内容か、手持ちの遊び方と合わせて比べたいです。",
        "activity_cube": f"{checks}を見て、家の置き場所に収まり、今の月齢で試せる遊びがあるか確認しておきたいです。",
        "ring_toy": f"{checks}を比べ、出し入れや片づけまで含めて、今の月齢で扱いやすいか確かめておきたいです。",
        "kids_camera": f"{checks}を見て、外出時に持ち歩きやすく、撮影後も家庭で扱える仕様か確認したいです。",
        "sleep_light": f"{checks}を見て、夜の動線に置きやすく、必要な音と灯りを使い分けられるか見比べたいです。",
        "stroller_storage": f"{checks}を比べ、普段持つ荷物が無理なく収まり、取り出しやすい位置へ付けられるかまでが購入前の判断材料です。",
    }[product_type]


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
        errors.append("商品タイプ不一致")
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
    else:
        if not all(term in combined for term in pattern.title_required):
            errors.append("タイトルと本文の商品タイプ不一致")
        if any(term in combined for term in pattern.title_forbidden):
            errors.append("タイトルと本文の禁止語が混入")
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
    if attributes.product_type == "wipes":
        if attributes.short_product_label == "手口ふき" and "おむつ替え" in combined:
            errors.append("手口ふきにおむつ替え文脈を使用")
        if attributes.short_product_label == "おしりふき" and "手口ふき" in combined:
            errors.append("おしりふきに手口ふき文脈を使用")
    if "誕生日" in combined and "誕生日向け" not in attributes.confirmed_gift_features:
        errors.append("未確認の誕生日用途を使用")
    if any(term in combined for term in BANNED_EXPRESSIONS):
        errors.append("禁止表現を使用")
    if "です、" in post.body or "ます、" in post.body:
        errors.append("不自然な文接続を使用")
    if any(re.search(pattern, combined, flags=re.IGNORECASE) for pattern in NOISE_PATTERNS):
        errors.append("商品名ノイズが残っている")
    if combined.startswith(("、", "!", "！", "<", "＜")) or post.body.startswith(("、", "!", "！", "<", "＜")):
        errors.append("文頭が読点または記号")
    sentences = split_sentences(post.body)
    if len(sentences) not in {3, 4}:
        errors.append("本文が3〜4文ではない")
    if not 160 <= len(post.body) <= 230:
        errors.append("本文が160〜230文字の目安から大きく外れる")
    if len(attributes.purchase_checkpoints) > 3:
        errors.append("購入前確認点が4つ以上")
    if not any(
        marker in post.body
        for feature in attributes.confirmed_features
        for marker in FEATURE_MARKERS.get(feature, [])
    ) and not any(quantity in post.body for quantity in attributes.confirmed_quantity_features):
        errors.append("商品固有の確認済み特徴がない")
    used_features = {
        feature
        for feature, markers in FEATURE_MARKERS.items()
        if any(marker in post.body for marker in markers)
    }
    unconfirmed = used_features - set(attributes.confirmed_features)
    if unconfirmed:
        errors.append(f"unsupported_product_claim: 未確認属性を使用: {','.join(sorted(unconfirmed))}")
    if not hashtags_match_type(post.hashtags, attributes):
        errors.append("商品タイプとハッシュタグが不一致")
    if "平面と立体の違いにも気づきやすく" in post.body:
        errors.append("confirmed_featuresにない効果を使用")
    if has_repeated_meaning(sentences):
        errors.append("文章内で同じ意味を繰り返している")
    if has_repeated_long_phrase(sentences) or post.body.count("変えられ") >= 3:
        errors.append("文章内で同じ表現を繰り返している")
    if context is not None:
        errors.extend(duplicate_errors(post, context))
    return list(dict.fromkeys(errors))


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
        "baby_bedding": TYPE_KEYWORDS["baby_bedding"],
    }
    for expected_type, keywords in conflict_types.items():
        if expected_type == attributes.product_type:
            continue
        if any(keyword.lower() in source_text for keyword in keywords):
            errors.append(
                f"product_type_keyword_conflict: {expected_type}系キーワードを含む商品が{attributes.product_type}に分類されています"
            )
    content_forbidden = {
        "swaddle": TYPE_KEYWORDS["diaper"] + TYPE_KEYWORDS["nursing_support"] + TYPE_KEYWORDS["baby_bedding"],
        "nursing_support": TYPE_KEYWORDS["diaper"] + TYPE_KEYWORDS["swaddle"] + TYPE_KEYWORDS["baby_bedding"],
        "baby_bedding": TYPE_KEYWORDS["diaper"] + TYPE_KEYWORDS["nursing_support"] + ["スワドル"],
        "diaper": TYPE_KEYWORDS["swaddle"] + TYPE_KEYWORDS["nursing_support"] + TYPE_KEYWORDS["baby_bedding"],
    }.get(attributes.product_type, [])
    if any(term in generated_text for term in content_forbidden):
        errors.append("content_type_mismatch: タイトル・本文・タグに別商品タイプの表現が混入")
    return errors


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
            errors.append("タイトルの誕生日訴求に商品情報の根拠がない")
        if not any(term in body for term in body_terms):
            errors.append(f"タイトルの使用場面「{title_term}」が本文にない")
    feature_title_rules = {
        "ゲームなし": "game_free",
        "音が鳴る": "sound",
        "木製": "wood",
        "名入れ": "name_option",
        "コードレス": "cordless",
        "防水": "waterproof",
        "軽量": "lightweight",
        "モロー反射": "moro_reflex",
        "ハンズフリー": "hands_free",
    }
    for title_term, feature in feature_title_rules.items():
        if title_term in title and feature not in attributes.confirmed_features:
            errors.append(f"タイトルの特徴「{title_term}」に商品情報の根拠がない")
    simplified = normalize_text(title.replace(attributes.short_product_label, ""))
    if not simplified or simplified in {"を選ぶ", "を選びたい", "選ぶ", "選びたい"}:
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
        "#スマホ転送": "smartphone_transfer" in features,
        "#SDカード": "sd_card" in features,
        "#スワドル": "swaddle" in features,
        "#モロー反射": "moro_reflex" in features,
        "#ハンズフリー授乳": "hands_free" in features,
        "#哺乳瓶ホルダー": "bottle_holder" in features,
        "#抱っこ布団": "hug_futon" in features,
        "#ねんねクッション": "sleep_cushion" in features,
        "#ベビー布団": "baby_futon" in features,
    }
    for tag in post.hashtags:
        if tag in evidence_rules and not evidence_rules[tag]:
            errors.append(f"ハッシュタグの根拠がない: {tag}")
    expected = hashtags_for(attributes, body=post.body, title=post.title)
    if post.hashtags != expected:
        errors.append("確認済み属性から生成したハッシュタグと不一致")
    return errors


def recommendation_reason_errors(
    post: GeneratedPost,
    attributes: ProductAttributes,
) -> list[str]:
    reason = post.recommendation_reason
    if not reason:
        return ["おすすめ理由が空"]
    errors: list[str] = []
    required_by_type = {
        "wipes": [attributes.short_product_label],
        "swaddle": [attributes.short_product_label],
        "nursing_support": [
            "ハンズフリー"
            if "hands_free" in attributes.confirmed_features
            else "哺乳瓶ホルダー"
            if "bottle_holder" in attributes.confirmed_features
            else "授乳クッション"
            if "nursing_cushion" in attributes.confirmed_features
            else "授乳"
        ],
        "baby_bedding": [attributes.short_product_label],
        "diaper": ["紙おむつ"],
        "formula": ["ミルク"],
        "sound_blocks": ["積み木", "音"],
        "wooden_blocks": ["木製積み木"],
        "magnetic_blocks": ["マグネットブロック"],
        "activity_cube": ["アクティビティキューブ"],
        "ring_toy": ["リング", "紐通し"],
        "kids_camera": ["キッズカメラ"],
        "sleep_light": ["ライト"],
        "stroller_storage": ["ベビーカーバッグ"],
    }.get(attributes.product_type, [])
    if not all(term in reason for term in required_by_type):
        errors.append("おすすめ理由の商品タイプが本文と一致しない")
    forbidden_reason_terms = {
        "wipes": ["ベビーカーバッグ", "持ち運び・収納"],
        "diaper": ["ベビーカーバッグ", "持ち運び・収納"],
        "swaddle": ["紙おむつ", "おむつ", "授乳サポート", "ベビーカーバッグ"],
        "nursing_support": ["紙おむつ", "おむつ", "スワドル", "抱っこ布団"],
        "baby_bedding": ["紙おむつ", "おむつ", "授乳サポート", "スワドル"],
        "formula": ["パーツ", "ベビーカーバッグ"],
        "sound_blocks": ["消耗品", "ストック需要"],
        "wooden_blocks": ["消耗品", "ストック需要"],
        "magnetic_blocks": ["消耗品", "ストック需要"],
        "activity_cube": ["消耗品", "ストック需要"],
        "ring_toy": ["消耗品", "ストック需要"],
    }.get(attributes.product_type, [])
    if any(term in reason for term in forbidden_reason_terms):
        errors.append("おすすめ理由に別商品タイプの訴求が混入")
    if re.search(r"(?:サイズ|枚|個|ピース|ポケット)\s*\d(?=\D|$)", reason):
        errors.append("おすすめ理由の商品名が途中で切れている")
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


def duplicate_summary(errors: list[str]) -> str:
    duplicates = [
        error
        for error in errors
        if any(term in error for term in ["一致", "類似度", "重複"])
    ]
    return "重複なし" if not duplicates else " / ".join(duplicates)
