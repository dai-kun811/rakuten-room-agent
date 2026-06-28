# 楽天ROOM収益化エージェント

## 2026-06 運用メモ

- 通常運用は固定ルール生成のみです。OpenAI API、LLM、Structured Outputsは呼び出しません。
- `GENERATION_MODE` 未設定時も固定ルール生成になり、通常実行のOpenAI API呼び出しは0回です。
- 投稿候補の出力先は `最新版_ROOM投稿_2026-06-19以降` です。ここには本日分以降の `status=ready` 行だけを毎朝追記します。
- `status=needs_review` とERROR行は `ROOM_Posts_Review` へ追記します。既存の旧シートは参照のみで、追記・上書きしません。
- `ready` は商品タイプ、短縮名、タイトル根拠、本文、ハッシュタグ、確認済み特徴、おすすめ理由、重複・類似チェックを通過した行です。
- `needs_review` は商品タイプ不明、分類キーワード矛盾、未確認特徴、本文・タグ・おすすめ理由の型不一致、品質条件未達などがある行です。自動投稿候補として扱わず、エージェント側の改善対象として扱います。
- `swaddle`、`nursing_support`、`baby_bedding` は `diaper` より先に分類します。おくるみ、スワドル、授乳サポート、抱っこ布団などを紙おむつとして扱わないためです。
- 手動実行後はActions artifact `room-generation-report` を確認してください。`reports/room_generation_report.json`、`.csv`、`.md` に、生成タイトル、投稿文、ハッシュタグ、34列相当、書き込み先シート、品質理由が出ます。
- artifactにはAPIキー、Google認証情報、Spreadsheet ID、Authorizationヘッダー、Cookie、GitHub Secrets、環境変数の値を含めません。
- GitHub Actionsの日次実行は `0 22 * * *` UTC、つまり日本時間07:00です。手動実行は `Daily Rakuten ROOM Agent` の `workflow_dispatch` で行います。

楽天市場商品検索APIから育児商品を取得し、最大5商品を選定して、楽天ROOM向け投稿候補をGoogleスプレッドシートへ追記します。

投稿文は固定ルールだけで生成します。通常運用ではOpenAI API、LLM、Structured Outputs、LLMリライトを使用しません。

## 運用方針

- `generation_mode=fallback`
- 未設定時も固定ルール生成
- `OPENAI_API_KEY`が環境に存在しても通常実行では参照しない
- GitHub ActionsからOpenAI関連のSecret・Variableを渡さない
- 通常実行のOpenAI API呼び出しは0回
- API課金は発生しない設計
- Webスクレイピングとレビュー本文取得は行わない
- 投稿文へレビュー数、評価、ランキング、推薦を購入理由として出さない

旧LLMコードは将来検証用として残していますが、`USE_OPENAI=true`と
`GENERATION_MODE=openai`の両方を明示しない限り有効になりません。
`src/main.py`はLLMコードを呼び出しません。

## 日次処理

1. 楽天APIから日替わり5カテゴリの商品を取得
2. URLと近似商品名で既存商品を除外
3. スコアと直近30日の商品タイプを使って最大5商品を選定
4. 商品名をクリーニング
5. 商品タイプを細分類
6. 商品ごとの確認済み属性と禁止属性を作成
7. 同じ`pattern_id`に属するタイトル・悩み・場面・ベネフィット・締めを組み合わせる
8. 商品タイプ、属性、文章、タグ、重複をハードチェック
9. 問題があれば別patternで最大5回再生成
10. `ready`または`needs_review`としてSheetsへ追記
11. `ROOM_AUTO_POST=true`の場合、`ready`だけを楽天ROOMへブラウザ投稿
12. 投稿前に`ROOM_Post_Log`へURLを予約し、同じ商品を二重投稿しない

`needs_review`は自動投稿候補として扱わず、エージェント側で原因を特定して改善する対象にしてください。

## 必須設定

### GitHub Secrets

| 名前 | 用途 |
|---|---|
| `RAKUTEN_APPLICATION_ID` | 楽天APIアプリID |
| `GOOGLE_SERVICE_ACCOUNT_JSON` | GoogleサービスアカウントJSON |
| `SPREADSHEET_ID` | 出力先スプレッドシートID |

### 任意のGitHub Secrets

| 名前 | 用途 |
|---|---|
| `RAKUTEN_ACCESS_KEY` | 楽天最新版APIのアクセスキー |
| `RAKUTEN_REFERER` | 楽天アプリ設定と一致するReferer |
| `ROOM_AUTH_STATE_B64` | 楽天ROOMへログイン済みのPlaywright認証状態（Base64） |

### GitHub Variables

| 名前 | 既定値 | 用途 |
|---|---|---|
| `SHEET_NAME` | `Sheet1` | 既存URLの参照元 |
| `OUTPUT_SHEET_NAME` | `最新版_ROOM投稿_2026-06-19以降` | 34列形式の最新版出力先 |
| `ENABLE_RELAXED_FALLBACK` | `true` | 商品選定条件の段階緩和 |
| `ROOM_AUTO_POST` | `false` | `true`で`ready`投稿の完全自動投稿を有効化 |
| `ROOM_POST_LOG_SHEET_NAME` | `ROOM_Post_Log` | 投稿予約・成功・失敗を記録するシート |

`OPENAI_API_KEY`と`OPENAI_MODEL`は通常運用に不要です。GitHubに残っていても
workflowからプロセスへ渡さないため、APIは呼ばれません。

## 楽天ROOMへの完全自動投稿

自動投稿は、品質ゲートを通過した`ready`行だけを対象にします。`needs_review`は投稿しません。
GitHub Actionsの同時実行を禁止し、投稿前に正規化URLを`ROOM_Post_Log`へ`reserved`として
記録します。ブラウザ操作が失敗した場合も同じURLを自動再投稿しないため、外部投稿の
重複を避けられます。失敗行を再試行する場合は、原因を確認してから対象URLのログ行を
手動で削除してください。

### 初回ログイン設定

認証状態には楽天アカウントの操作権限が含まれます。ファイル内容を画面・ログへ表示せず、
リポジトリへ追加しないでください。

```powershell
& 'C:\Program Files\LibreOffice\program\python.exe' -m pip install -r requirements.txt
& 'C:\Program Files\LibreOffice\program\python.exe' -m playwright install chromium
& 'C:\Program Files\LibreOffice\program\python.exe' src\room_auth_setup.py
```

開いたブラウザで楽天ROOMへログインすると、次の2ファイルがユーザーディレクトリ配下へ
保存されます。

```text
%USERPROFILE%\.rakuten-room\storage-state.json
%USERPROFILE%\.rakuten-room\storage-state.b64
```

`storage-state.b64`の内容をGitHub Actions Secret `ROOM_AUTH_STATE_B64`へ登録した後、
GitHub Actions Variable `ROOM_AUTO_POST`を`true`にします。認証期限が切れた場合は同じ手順で
Secretを更新してください。

### 投稿結果

- `ROOM_Post_Log`: `reserved`、`posted`、`failed`を追記
- `reports/room_post_report.json`: 実行ID、URL、投稿結果のみを保存
- 認証Cookie、パスワード、Secret値はレポートやログへ出力しない

## 商品属性モデル

生成前に各商品から次を作成します。

- `normalized_product_name`
- `short_product_label`
- `product_type`
- `target_age`
- `confirmed_features`
- `confirmed_use_cases`
- `confirmed_gift_features`
- `confirmed_power_features`
- `confirmed_quantity_features`
- `purchase_checkpoints`
- `prohibited_features`

本文に書ける商品特徴は、商品自身のAPI情報から抽出したconfirmed項目だけです。
別商品や同一カテゴリの別商品の特徴は引き継ぎません。

商品名からランキング、レビュー件数、高評価、推薦、送料無料、セール、ポイント倍率、
店舗名、装飾記号、日付などを除去します。安全な短縮名を作れない商品は
`needs_review`になります。楽天の商品名を本文へそのまま差し込みません。

## 商品タイプ

固定生成器は少なくとも次の11タイプを扱います。

| product_type | 主な判定語 | 主な確認点 |
|---|---|---|
| `wipes` | おしりふき、手口ふき | 枚数、個数、価格、収納場所 |
| `diaper` | 紙おむつ、パンツ、テープ | サイズ、枚数、1枚あたり価格 |
| `formula` | 粉・液体・フォローアップミルク | 容量、個数、価格、賞味期限 |
| `sound_blocks` | 音が鳴る積み木 | 対象年齢、パーツサイズ、収納 |
| `wooden_blocks` | 木製積み木 | 対象年齢、個数、収納 |
| `magnetic_blocks` | マグネットブロック | 対象年齢、パーツ数、収納 |
| `activity_cube` | 型はめ、ルーピング | 対象年齢、本体サイズ、置き場所 |
| `ring_toy` | リングテン、紐通し | 対象年齢、パーツ数、収納 |
| `kids_camera` | キッズカメラ | 転送、充電、SD、ゲーム機能 |
| `sleep_light` | ホワイトノイズ、授乳ライト | 音量、ライト、電源、設置場所 |
| `stroller_storage` | ベビーカーバッグ | サイズ、取り付け、容量、対応車種 |

分類できない商品は無理に生成せず`needs_review`になります。

## 投稿仕様

- タイトルは商品タイプ別patternから選択
- 本文は3〜4文
- 本文は160〜230文字
- 絵文字は強制しない
- 購入前確認点は最大3つ
- 商品固有の確認済み特徴を最低1つ含める
- ハッシュタグは確認済み属性・確認済み使用場面・安全なカテゴリタグから5個生成
- 最後のタグは`#とらパパ厳選`
- 同一実行内では3文型と4文型をそれぞれ40〜60%程度に分散

3文型:

1. 具体的な悩みや迷い
2. 確認済み特徴と具体的なベネフィット
3. 最大3つの購入前確認点と自然な締め

4文型:

1. 具体的な悩みや迷い
2. 確認済み特徴と使用場面
3. 親または子どもの具体的なベネフィット
4. 最大3つの購入前確認点と自然な締め

すべてを同じ文面へ固定しません。各タイプは8個以上の完全なpatternを持ち、
タイトルだけ別patternへ交換することはありません。

## 品質ゲート

品質点は参考値です。次のハードエラーを優先します。

- タイトル・本文・patternの商品タイプ不一致
- タイトルの場面・用途・対象に商品情報または本文の根拠がない
- タイトルが商品名の言い換えだけで、悩み・場面・ベネフィットがない
- confirmed項目にない特徴
- 別商品の特徴
- ハッシュタグに未確認用途・素材・対象が含まれる
- おすすめ理由の商品タイプ不一致
- おすすめ理由の商品名断片
- 商品名ノイズ
- 文頭の読点や記号
- 同一タイトル
- 本文または正規化本文の完全一致
- 書き出し完全一致
- 先頭2文一致
- 4文中3文以上一致
- 本文または正規化構文の類似度0.75以上
- 同じ接続構文が同一実行内で3回以上
- 同じ締め語尾が同一実行内で4回以上
- 消耗品文脈の取り違え
- 購入前確認点が4つ以上
- レビュー、ランキング、推薦の訴求
- 安全性や効果の断定
- 文法崩壊
- 同じ意味の繰り返し
- 商品タイプとハッシュタグの不一致

ハードエラーがあれば、点数に関係なく不合格です。別patternで最大5回再生成し、
解消しなければ`status=needs_review`になります。

正常投稿の品質点上限は95点です。固定形式を満たしただけで99〜100点にはしません。
ハードエラーがある投稿は59点以下です。

## 重複防止

同一実行内と過去30日のSheets行を対象に、次を検査します。

- タイトル完全一致
- 本文完全一致
- 正規化本文完全一致
- 先頭2文一致
- 4文中3文以上一致
- 本文類似度0.75以上
- 文数、文の役割、書き出し、接続詞、文長、締めを使った構文類似度0.75以上
- 同じ接続構文の3回以上使用
- 同じ締め語尾の4回以上使用

完全一致系はSHA-256でも検出します。重複時は別の`pattern_id`へ移り、
最大5回で解消しない場合は`needs_review`です。

## 禁止表現

例:

- おすすめです
- 人気です
- 楽天1位
- 口コミ、レビュー、ランキング
- 芸能人愛用、専門家推薦、管理栄養士推薦
- 安全に遊べる
- 必ず喜ばれる、必ず寝る、泣き止む
- 絶対、間違いなし、神アイテム
- 遊びの特徴が伝わるギフト感
- 一緒に繰り返し次の遊び方を考える時間
- 親子で相談する

商品タイプごとの禁止特徴も別に定義されています。

## Sheets出力

出力はA〜AHの34列です。主な生成関連列:

- 商品タイプ
- 訴求軸
- 構成パターン
- 確認済みの使用シーン・ベネフィット
- 購入前確認点
- 投稿品質スコア
- 再生成回数
- ステータス
- タイトル
- 投稿文
- ハッシュタグ
- 品質エラー
- 生成モード

正常:

```text
status=ready
generation_mode=fallback
```

不合格:

```text
status=needs_review
generation_mode=fallback
quality_errors=具体的な理由
```

## ローカル検証

```powershell
& 'C:\Program Files\LibreOffice\program\python.exe' -m unittest discover -s tests -v
```

全Pythonファイルの構文検査:

```powershell
Get-ChildItem src -Filter *.py | ForEach-Object {
  & 'C:\Program Files\LibreOffice\program\python.exe' -m py_compile $_.FullName
}
```

テストでは次を含めて確認します。

- `OPENAI_API_KEY`が存在してもAPI通信0回
- workflowがOpenAI設定を渡さない
- 11商品タイプの分類と確認済み属性連動タグ
- 別商品特徴と未確認属性の拒否
- 根拠のないタイトル場面・用途・タグの拒否
- おすすめ理由の商品タイプ不一致と商品名断片の拒否
- 商品名ノイズと不正文頭の拒否
- タイトル、本文、書き出し、先頭2文、文章骨格の重複再生成
- 3文型と4文型の混在
- 締め語尾の使用回数上限
- 最大5回後の`needs_review`
- 購入前確認点の上限

## セキュリティ

- APIキー、Secrets、サービスアカウントJSONをログへ出さない
- 認証情報をソース、README、レポート、テストデータへ書かない
- 楽天APIのReferer、Origin、accessKey値はマスクする
- OpenAI APIを通常実行から呼び出さない
