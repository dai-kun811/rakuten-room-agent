from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Any

from post_generator import (
    BANNED_EXPRESSIONS,
    build_hashtags,
    build_post_text,
    build_benefit,
    determine_appeal_category,
    purchase_checkpoints,
)
from product_type import product_display_name
from scoring import ScoredProduct

LOGGER = logging.getLogger(__name__)
OPENAI_RESPONSES_URL = "https://api.openai.com/v1/responses"
DEFAULT_OPENAI_MODEL = "gpt-5.5"
MAX_REWRITES = 3
MIN_QUALITY_SCORE = 80
EMOJI_PATTERN = re.compile(
    "[\U0001F300-\U0001FAFF\u2600-\u27BF]",
    flags=re.UNICODE,
)

PROHIBITED_EXPRESSIONS = list(
    dict.fromkeys(
        BANNED_EXPRESSIONS
        + [
            "実際に使ってみた",
            "我が家で愛用中",
            "おすすめ",
            "人気",
            "高評価",
            "絶対",
            "神商品",
            "爆売れ",
            "最強",
            "安全",
            "発達に良い",
            "効果がある",
            "ランキング1位",
            "レビュー",
            "口コミ",
            "ランキング",
            "口コミで",
            "レビューで",
        ]
    )
)

STRUCTURE_PATTERNS = [
    "悩み共感型",
    "シーン想起型",
    "不安解消型",
    "口コミ安心型",
    "ギフト提案型",
    "買い忘れ防止型",
    "成長サポート型",
    "ワンオペ負担軽減型",
    "外出準備ラク化型",
    "片付け・収納改善型",
    "コスパ納得型",
    "季節需要型",
]

SEASON_CONTEXT = {
    1: "冬、防寒、乾燥",
    2: "入園準備、花粉、防寒",
    3: "入園・入学、新生活",
    4: "通園、遠足、春",
    5: "外遊び、紫外線",
    6: "梅雨、雨、蒸し暑さ",
    7: "夏、水遊び、暑さ",
    8: "夏休み、帰省、旅行",
    9: "防災、遠足、運動会",
    10: "秋、遠足、運動会",
    11: "冬支度、乾燥、クリスマス準備",
    12: "クリスマス、帰省、冬",
}

PAIN_BY_TYPE = {
    "consumable": "買い忘れ、在庫切れ、買い足しの手間",
    "educational": "家遊びのマンネリ、年齢に合う遊び選び",
    "kids_camera": "思い出を残したいがスマホだけに偏る",
    "sleep": "就寝前の準備、夜の授乳時の明るさ",
    "outing": "子連れ外出の荷物整理と取り出し",
    "feeding": "食事準備、食べこぼし、片づけ",
    "bath": "ワンオペ入浴の準備と片づけ",
    "storage": "床の散らかり、戻す場所が決まらない",
    "shoes": "登園前の履かせにくさ、洗い替え",
    "appliance": "家事の手順と時間",
    "gift": "相手の月齢や生活に合うか不安",
    "default": "買った後の出番が想像しにくい",
}


@dataclass
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


@dataclass
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
    source: str

    @property
    def text(self) -> str:
        return f"タイトル：\n{self.title}\n\n投稿文：\n{self.body}"


@dataclass
class GenerationContext:
    used_titles: list[str] = field(default_factory=list)
    used_openings: list[str] = field(default_factory=list)
    used_closings: list[str] = field(default_factory=list)
    used_patterns: list[str] = field(default_factory=list)
    used_emojis: list[str] = field(default_factory=list)
    used_emoji_sets: list[list[str]] = field(default_factory=list)
    used_hashtag_sets: list[list[str]] = field(default_factory=list)
    past_expressions: list[str] = field(default_factory=list)

    def remember(self, post: GeneratedPost) -> None:
        self.used_titles.append(post.title)
        self.used_openings.append(first_two_sentences(post.body))
        self.used_closings.append(last_sentence(post.body))
        self.used_patterns.append(post.structure_pattern)
        emojis = EMOJI_PATTERN.findall(post.body)
        self.used_emojis.extend(emojis)
        self.used_emoji_sets.append(emojis)
        self.used_hashtag_sets.append(post.hashtags)


class OpenAIPostGenerator:
    def __init__(
        self,
        api_key: str | None = None,
        *,
        model: str | None = None,
        session: Any | None = None,
        timeout_seconds: int = 60,
    ) -> None:
        if session is None:
            import requests

            session = requests.Session()
        self.api_key = api_key or os.getenv("OPENAI_API_KEY")
        self.model = model or os.getenv("OPENAI_MODEL", DEFAULT_OPENAI_MODEL)
        self.session = session
        self.timeout_seconds = timeout_seconds

    @property
    def enabled(self) -> bool:
        return (
            bool(self.api_key)
            and os.getenv("USE_OPENAI", "false").lower() in {"1", "true", "yes"}
            and os.getenv("GENERATION_MODE", "fallback").lower() == "openai"
        )

    def generate(
        self,
        scored: ScoredProduct,
        *,
        context: GenerationContext,
        season: str,
    ) -> GeneratedPost:
        if not self.enabled:
            post = build_fallback_post(scored, context)
            post.quality, issues = assess_generated_post(post, scored, context)
            post.quality.improvement_comment = fallback_comment(issues)
            context.remember(post)
            return post

        feedback = ""
        last_post: GeneratedPost | None = None
        for rewrite_count in range(MAX_REWRITES + 1):
            try:
                payload = self._request_payload(
                    scored,
                    context=context,
                    season=season,
                    feedback=feedback,
                )
                raw = self._call(payload)
                post = parse_generated_post(raw, rewrite_count=rewrite_count)
                quality, issues = assess_generated_post(post, scored, context)
                post.quality = quality
                if post.quality.score >= MIN_QUALITY_SCORE and not issues:
                    post.status = "通常"
                    context.remember(post)
                    return post
                feedback = build_rewrite_feedback(post, issues)
                last_post = post
            except Exception as exc:
                LOGGER.warning(
                    "LLM投稿生成に失敗しました model=%s attempt=%s error=%s",
                    self.model,
                    rewrite_count + 1,
                    safe_error(exc),
                )
                feedback = "JSON形式と全制約を守り、商品情報に忠実な文章へ作り直してください。"

        if last_post is not None:
            last_post.status = "要確認"
            last_post.rewrite_count = MAX_REWRITES
            context.remember(last_post)
            return last_post

        post = build_fallback_post(scored, context)
        post.status = "フォールバック"
        post.quality, issues = assess_generated_post(post, scored, context)
        post.quality.improvement_comment = fallback_comment(issues)
        context.remember(post)
        return post

    def _call(self, payload: dict[str, Any]) -> dict[str, Any]:
        response = self.session.post(
            OPENAI_RESPONSES_URL,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=self.timeout_seconds,
        )
        response.raise_for_status()
        return json.loads(extract_output_text(response.json()))

    def _request_payload(
        self,
        scored: ScoredProduct,
        *,
        context: GenerationContext,
        season: str,
        feedback: str,
    ) -> dict[str, Any]:
        product = scored.product
        product_type = determine_appeal_category(product)
        input_data = {
            "product_name": product.name,
            "product_url": product.url,
            "price": product.price,
            "review_count": product.review_count,
            "review_average": product.review_average,
            "description": product.caption,
            "catchcopy": product.catchcopy,
            "shop_name": product.shop_name,
            "image_url": product.image_url,
            "product_type": product_type,
            "score": scored.total_score,
            "search_keyword": product.search_keyword,
            "season": season,
            "likely_pain": PAIN_BY_TYPE.get(product_type, PAIN_BY_TYPE["default"]),
            "purchase_checkpoints": purchase_checkpoints(product, product_type),
            "used_titles_today": context.used_titles,
            "used_openings_today": context.used_openings,
            "used_closings_today": context.used_closings,
            "used_patterns_today": context.used_patterns,
            "used_emojis_today": context.used_emojis,
            "used_hashtag_sets_today": context.used_hashtag_sets,
            "past_expressions": context.past_expressions[-30:],
            "prohibited_expressions": PROHIBITED_EXPRESSIONS,
            "rewrite_feedback": feedback,
        }
        prompt = (
            "楽天ROOM向けの育児商品投稿を日本語で作成してください。"
            "出力は指定JSONスキーマのみ。実際に使ったように書かず、レビュー本文も読んだと断定しないこと。"
            "タイトル12〜24文字、本文160〜230文字を基本とし、商品説明に必要な場合のみ260文字まで、3〜4文。"
            "絵文字は必要な場合だけ0〜2個、ハッシュタグは5〜8個。"
            "1文目は悩み・生活シーン・不安・育児あるあるのいずれか。"
            "商品説明に存在する固有情報を最低2つ使い、生活上のベネフィットへつなげること。"
            "売り込みすぎず、最後は比較・確認・候補入りを自然に促すこと。"
            "同日のタイトル、冒頭、締め、構成、絵文字、タグ構成と似せないこと。"
            "構成パターンは候補から1つ選び、直前と同じものを避けること。"
            "品質点は共感20、ベネフィット20、自然さ15、商品固有性15、ROOM適合10、"
            "非テンプレ10、コンプライアンス10の合計100点で厳しく自己採点すること。"
            f"構成パターン候補={STRUCTURE_PATTERNS}\n入力={json.dumps(input_data, ensure_ascii=False)}"
        )
        return {
            "model": self.model,
            "input": prompt,
            "reasoning": {"effort": "low"},
            "text": {
                "verbosity": "low",
                "format": {
                    "type": "json_schema",
                    "name": "rakuten_room_post",
                    "strict": True,
                    "schema": response_schema(),
                }
            },
        }


def response_schema() -> dict[str, Any]:
    analysis_properties = {
        name: {"type": "string"}
        for name in PostAnalysis.__dataclass_fields__
    }
    quality_properties: dict[str, Any] = {
        name: {"type": "string" if name == "improvement_comment" else "integer"}
        for name in QualityScore.__dataclass_fields__
    }
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "title": {"type": "string"},
            "body": {"type": "string"},
            "hashtags": {"type": "array", "items": {"type": "string"}},
            "structure_pattern": {"type": "string", "enum": STRUCTURE_PATTERNS},
            "analysis": {
                "type": "object",
                "additionalProperties": False,
                "properties": analysis_properties,
                "required": list(analysis_properties),
            },
            "quality": {
                "type": "object",
                "additionalProperties": False,
                "properties": quality_properties,
                "required": list(quality_properties),
            },
        },
        "required": ["title", "body", "hashtags", "structure_pattern", "analysis", "quality"],
    }


def extract_output_text(response: dict[str, Any]) -> str:
    if isinstance(response.get("output_text"), str):
        return response["output_text"]
    for item in response.get("output", []):
        for content in item.get("content", []):
            if content.get("type") == "output_text" and isinstance(content.get("text"), str):
                return content["text"]
    raise ValueError("OpenAI response did not contain output text")


def parse_generated_post(payload: dict[str, Any], *, rewrite_count: int) -> GeneratedPost:
    quality_data = payload.get("quality", {})
    quality = QualityScore(
        **{name: quality_data.get(name, default.default) for name, default in QualityScore.__dataclass_fields__.items()}
    )
    analysis_data = payload.get("analysis", {})
    analysis = PostAnalysis(
        **{name: str(analysis_data.get(name, "")) for name in PostAnalysis.__dataclass_fields__}
    )
    hashtags = [normalize_hashtag(tag) for tag in payload.get("hashtags", [])]
    return GeneratedPost(
        title=str(payload.get("title", "")).strip(),
        body=str(payload.get("body", "")).strip(),
        hashtags=list(dict.fromkeys(tag for tag in hashtags if tag)),
        analysis=analysis,
        quality=quality,
        structure_pattern=str(payload.get("structure_pattern", "")).strip(),
        rewrite_count=rewrite_count,
        status="要確認",
        source="OpenAI Responses API",
    )


def build_fallback_post(scored: ScoredProduct, context: GenerationContext) -> GeneratedPost:
    text = build_post_text(scored)
    title, body = split_post_text(text)
    product = scored.product
    product_type = determine_appeal_category(product)
    hashtags = build_hashtags(scored).split()
    pattern = choose_fallback_pattern(product_type, context.used_patterns)
    analysis = PostAnalysis(
        product_type=product_type,
        target="育児中のママ・パパ",
        user_pain=PAIN_BY_TYPE.get(product_type, PAIN_BY_TYPE["default"]),
        search_intent=product.search_keyword or product.category,
        purchase_anxiety=purchase_checkpoints(product, product_type),
        benefit=build_benefit(product, product_type),
        usage_scene="商品タイプから想定した日常の使用場面",
        appeal_axis=pattern,
        reason_to_check=product_display_name(product, product_type),
        caution="楽天APIの商品情報のみを使用",
    )
    quality = local_quality_score(title, body, hashtags, context)
    return GeneratedPost(
        title=title,
        body=body,
        hashtags=hashtags,
        analysis=analysis,
        quality=quality,
        structure_pattern=pattern,
        rewrite_count=0,
        status="フォールバック",
        source="固定ルール",
    )


def assess_generated_post(
    post: GeneratedPost,
    scored: ScoredProduct,
    context: GenerationContext,
) -> tuple[QualityScore, list[str]]:
    issues: list[str] = []
    product = scored.product
    product_type = determine_appeal_category(product)
    if not 10 <= len(post.title) <= 24:
        issues.append("タイトルを12〜24文字目安にし、短い定型タイトルでも10文字以上にする")
    if not 160 <= len(post.body) <= 260:
        issues.append("本文を160〜230文字、説明が必要な場合のみ260文字以内にする")
    sentence_count = len([part for part in re.split(r"[。！？]", post.body) if part.strip()])
    if not 3 <= sentence_count <= 4:
        issues.append("本文を3〜4文にする")
    emoji_count = len(EMOJI_PATTERN.findall(post.body))
    if emoji_count > 2:
        issues.append("絵文字は必要な場合だけ0〜2個にする")
    if not 5 <= len(post.hashtags) <= 8:
        issues.append("ハッシュタグを5〜8個にする")
    banned = [expression for expression in PROHIBITED_EXPRESSIONS if expression in f"{post.title}{post.body}"]
    if banned:
        issues.append(f"禁止表現を使わない: {', '.join(banned[:5])}")
    if similar_to_any(post.title, context.used_titles, 0.72):
        issues.append("既存タイトルと異なる切り口にする")
    if similar_to_any(first_two_sentences(post.body), context.used_openings, 0.72):
        issues.append("本文の先頭2文と構文を変える")
    if similar_to_any(last_sentence(post.body), context.used_closings, 0.72):
        issues.append("締め文を変える")
    if context.used_patterns and post.structure_pattern == context.used_patterns[-1]:
        issues.append("直前と異なる構成パターンにする")
    emojis = EMOJI_PATTERN.findall(post.body)
    if emojis and context.used_emoji_sets and emojis == context.used_emoji_sets[-1]:
        issues.append("直前と異なる絵文字の組み合わせにする")
    if context.used_hashtag_sets and hashtag_similarity(
        post.hashtags,
        context.used_hashtag_sets[-1],
    ) >= 0.8:
        issues.append("直前と異なるハッシュタグ構成にする")
    if similar_to_any(post.body, context.past_expressions, 0.82):
        issues.append("過去30日の投稿と異なる表現にする")
    for repeated_phrase in ["迷いますよね", "確認しておきたいです"]:
        if post.body.count(repeated_phrase) > 1:
            issues.append(f"「{repeated_phrase}」を繰り返さない")
    specific_terms = extract_product_specific_terms(product)
    used_specific_terms = [term for term in specific_terms if term.lower() in post.body.lower()]
    if len(used_specific_terms) < 2:
        issues.append("商品情報から確認できる固有ワードを2つ以上入れる")
    checkpoint_count = count_checkpoints(post.analysis.purchase_anxiety)
    if checkpoint_count > 3:
        issues.append("購入前確認点を最大3つにする")
    if not hashtags_match_product_type(post.hashtags, product_type, product.text):
        issues.append("商品タイプに合うハッシュタグを入れる")
    unsupported = [
        expression
        for expression in ["安全", "効果がある", "必ず", "絶対", "寝てくれる", "泣き止む", "発達に良い"]
        if expression in f"{post.title}{post.body}"
    ]
    if unsupported:
        issues.append(f"商品情報から確認できない安全性・効果を断定しない: {', '.join(unsupported)}")

    empathy = 20 if any(
        word in first_sentence(post.body)
        for word in ["よね", "がち", "困", "慌", "迷", "バタバタ", "気になる"]
    ) else 10
    benefit = 20 if post.analysis.benefit and any(
        word in post.body
        for word in ["減ら", "整え", "余裕", "時間", "手間", "楽し", "把握", "取り出"]
    ) else 10
    naturalness = 0
    naturalness += 8 if 3 <= sentence_count <= 4 else 2
    naturalness += 5 if 160 <= len(post.body) <= 230 else (3 if len(post.body) <= 260 else 0)
    naturalness += 2 if emoji_count <= 2 else 0
    specificity = 15 if len(used_specific_terms) >= 2 else (8 if used_specific_terms else 0)
    room_fit = 0
    room_fit += 3 if 12 <= len(post.title) <= 24 else (2 if 10 <= len(post.title) <= 11 else 0)
    room_fit += 2 if 5 <= len(post.hashtags) <= 8 else 0
    room_fit += 3 if hashtags_match_product_type(post.hashtags, product_type, product.text) else 0
    room_fit += 2 if any(
        word in last_sentence(post.body)
        for word in ["確認", "比べ", "候補", "見ておきたい", "選びたい"]
    ) else 0
    non_template = 10
    if similar_to_any(post.title, context.used_titles, 0.72):
        non_template -= 3
    if similar_to_any(first_two_sentences(post.body), context.used_openings, 0.72):
        non_template -= 4
    if context.used_patterns and post.structure_pattern == context.used_patterns[-1]:
        non_template -= 3
    compliance = 10
    if banned or unsupported:
        compliance = 0
    elif checkpoint_count > 3:
        compliance = 5
    score = empathy + benefit + naturalness + specificity + room_fit + max(0, non_template) + compliance
    if issues:
        score = min(score, MIN_QUALITY_SCORE - 1)
    improvement = " / ".join(issues) or post.quality.improvement_comment
    return QualityScore(
        score=score,
        empathy=empathy,
        benefit=benefit,
        naturalness=naturalness,
        specificity=specificity,
        room_fit=room_fit,
        non_template=max(0, non_template),
        compliance=compliance,
        improvement_comment=improvement,
    ), issues


def local_quality_score(
    title: str,
    body: str,
    hashtags: list[str],
    context: GenerationContext,
) -> QualityScore:
    empathy = 20 if any(word in first_sentence(body) for word in ["よね", "がち", "困", "慌", "迷"]) else 10
    benefit = 20 if any(word in body for word in ["減ら", "整え", "余裕", "時間", "手間", "楽し"]) else 10
    naturalness = 15 if 160 <= len(body) <= 280 and 3 <= body.count("。") <= 6 else 8
    specificity = 15 if any(char.isdigit() for char in body) or product_word_count(body) >= 2 else 8
    room_fit = 10 if len(hashtags) >= 5 else 5
    non_template = 10 if not similar_to_any(first_sentence(body), context.used_openings, 0.72) else 3
    compliance = 10 if not any(word in f"{title}{body}" for word in PROHIBITED_EXPRESSIONS) else 0
    score = empathy + benefit + naturalness + specificity + room_fit + non_template + compliance
    return QualityScore(
        score=score,
        empathy=empathy,
        benefit=benefit,
        naturalness=naturalness,
        specificity=specificity,
        room_fit=room_fit,
        non_template=non_template,
        compliance=compliance,
        improvement_comment="OPENAI_API_KEY未設定のため固定ルールで生成",
    )


def split_post_text(text: str) -> tuple[str, str]:
    title_part, body_part = text.split("投稿文：", 1)
    return title_part.replace("タイトル：", "").strip(), body_part.strip()


def choose_fallback_pattern(product_type: str, used_patterns: list[str]) -> str:
    preferred = {
        "consumable": "買い忘れ防止型",
        "educational": "成長サポート型",
        "kids_camera": "シーン想起型",
        "sleep": "不安解消型",
        "outing": "外出準備ラク化型",
        "feeding": "ワンオペ負担軽減型",
        "bath": "ワンオペ負担軽減型",
        "storage": "片付け・収納改善型",
        "gift": "ギフト提案型",
    }.get(product_type, "悩み共感型")
    if not used_patterns or preferred != used_patterns[-1]:
        return preferred
    return next(pattern for pattern in STRUCTURE_PATTERNS if pattern != preferred)


def build_rewrite_feedback(post: GeneratedPost, issues: list[str]) -> str:
    comments = list(issues)
    if post.quality.score < MIN_QUALITY_SCORE:
        comments.append(
            f"品質スコア{post.quality.score}点。{post.quality.improvement_comment or '固有性と自然さを高める'}"
        )
    return " / ".join(comments)


def fallback_comment(issues: list[str]) -> str:
    base = "OPENAI_API_KEY未設定またはLLM障害のため固定ルールで生成"
    return f"{base}。{' / '.join(issues)}" if issues else base


def similar_to_any(value: str, candidates: list[str], threshold: float) -> bool:
    normalized = normalize_text(value)
    return any(
        SequenceMatcher(None, normalized, normalize_text(candidate)).ratio() >= threshold
        for candidate in candidates
        if candidate
    )


def normalize_text(value: str) -> str:
    return re.sub(r"[\s、。！？!?,#]", "", value).lower()


def first_sentence(body: str) -> str:
    return re.split(r"(?<=[。！？])", body.strip(), maxsplit=1)[0]


def first_two_sentences(body: str) -> str:
    sentences = [part for part in re.split(r"(?<=[。！？])", body.strip()) if part]
    return "".join(sentences[:2])


def last_sentence(body: str) -> str:
    sentences = [part for part in re.split(r"(?<=[。！？])", body.strip()) if part]
    return sentences[-1] if sentences else body


def normalize_hashtag(tag: Any) -> str:
    value = str(tag).strip().replace(" ", "")
    if not value:
        return ""
    return value if value.startswith("#") else f"#{value}"


def hashtag_similarity(left: list[str], right: list[str]) -> float:
    left_set = set(left)
    right_set = set(right)
    union = left_set | right_set
    if not union:
        return 0.0
    return len(left_set & right_set) / len(union)


def product_word_count(body: str) -> int:
    return sum(
        word in body
        for word in ["サイズ", "容量", "充電", "ライト", "パーツ", "収納", "セット", "素材", "枚", "個"]
    )


def extract_product_specific_terms(product: Any) -> list[str]:
    text = product.text
    known_terms = [
        "おしりふき", "おむつ", "ミルク", "厚手", "大容量", "積み木", "ブロック",
        "木製", "音が鳴る", "名入れ", "型はめ", "ルーピング", "紐通し", "リング",
        "キッズカメラ", "スマホ転送", "SDカード", "ゲームなし", "ホワイトノイズ",
        "授乳ライト", "コードレス", "ベビーカー", "軽量", "防水", "離乳食",
        "食べこぼし", "洗える", "収納", "ラック", "上履き", "スニーカー",
        "ブレンダー", "タイマー", "バスチェア", "バスマット",
    ]
    terms = [term for term in known_terms if term.lower() in text]
    terms.extend(
        match.group(0)
        for match in re.finditer(
            r"\d+(?:\.\d+)?(?:枚|個|本|色|段|台|cm|mm|ml|L|kg|g|パーツ)",
            product.text,
            flags=re.IGNORECASE,
        )
    )
    return list(dict.fromkeys(terms))


def count_checkpoints(value: str) -> int:
    parts = [part.strip() for part in re.split(r"[・、,／/]", value) if part.strip()]
    return len(parts)


def hashtags_match_product_type(hashtags: list[str], product_type: str, product_text: str) -> bool:
    joined = " ".join(hashtags)
    expected = {
        "consumable": ["おしりふき", "おむつ", "まとめ買い", "ストック", "買い忘れ"],
        "educational": ["知育", "積み木", "ブロック", "手先", "木のおもちゃ", "親子遊び"],
        "kids_camera": ["キッズカメラ", "写真", "親子時間"],
        "sleep": ["ホワイトノイズ", "授乳ライト", "寝かしつけ"],
        "outing": ["外出", "ベビーカー", "荷物整理"],
        "feeding": ["離乳食", "食べこぼし", "食事"],
        "bath": ["お風呂", "沐浴", "ワンオペ入浴"],
        "storage": ["収納", "片づけ", "リビング整理"],
        "shoes": ["シューズ", "上履き", "通園", "保育園"],
        "appliance": ["時短", "家事"],
        "gift": ["ギフト", "出産祝い", "誕生日"],
        "default": ["育児用品", "買う前メモ", "比較検討"],
    }
    terms = expected.get(product_type, expected["default"])
    return any(term in joined for term in terms)


def safe_error(exc: Exception) -> str:
    text = str(exc)
    text = re.sub(r"sk-[A-Za-z0-9_-]+", "***", text)
    return text[:300]
