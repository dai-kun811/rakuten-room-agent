# 楽天ROOM用 商品選定・投稿文作成ツール

楽天市場商品検索APIから育児ジャンルの商品を取得し、条件に合う商品を最大5件選んでGoogleスプレッドシートへ追記するPythonツールです。GitHub Actionsで毎朝7時JSTに自動実行できます。

Webスクレイピングは行わず、楽天APIとGoogle Sheets APIだけを使います。

## できること

- 楽天市場商品検索APIから育児関連商品を取得
- レビュー300件以上、評価4.3以上、総合スコア80点以上の商品を優先
- 足りない場合はレビュー100件以上、総合スコア70点以上まで対象を拡大
- スプレッドシートに出力済みの商品URLを全履歴で除外
- 100点満点で商品をスコアリング
- 総合スコア80点以上を優先し、不足時は70点以上まで対象を拡大
- 最大5商品をGoogleスプレッドシートへ追記
- 5件未満しか取得できない場合は取得できた件数だけ追記
- 実体験表現を使わない楽天ROOM投稿文を作成
- 商品ごとに訴求カテゴリ、ベネフィット、購入前確認点、ハッシュタグを作成
- GitHub Actionsで毎朝7時JSTに自動実行
- 手動実行にも対応

## スプレッドシート列

スプレッドシートには以下の列で追記します。

| 列 | 内容 |
| --- | --- |
| 日付 | 実行日 |
| カテゴリ | 商品選定ジャンル |
| 商品名 | 楽天APIの商品名 |
| 商品URL | 楽天の商品URL |
| 価格 | 商品価格 |
| レビュー件数 | レビュー件数 |
| 評価 | 平均評価 |
| 商品区分 | Aランク、Bランク、Cランク |
| 訴求カテゴリ | 購入動機に近い切り口 |
| ベネフィット | 商品ごとの一言メリット |
| 購入前確認点 | サイズ、対象年齢、誤飲リスクなど |
| 総合スコア | 100点満点 |
| おすすめ理由 | スコアやレビューに基づく理由 |
| 楽天ROOM投稿文 | 200〜350文字程度の投稿文 |
| おすすめハッシュタグ | 5個固定のハッシュタグ |

## 初期設定

### 1. 楽天APIのアプリIDを取得する

1. [楽天ウェブサービス](https://webservice.rakuten.co.jp/) にログインします。
2. アプリを作成します。
3. `アプリID / デベロッパーID` をコピーします。

この値をGitHub Secretsの `RAKUTEN_APPLICATION_ID` に登録します。

楽天ウェブサービスのアプリ設定で「許可されたWebサイト」に以下のどちらかを登録してください。

```text
github.com
```

または

```text
https://github.com/dai-kun811/rakuten-room-agent
```

この設定と、後述の `RAKUTEN_REFERER` が合っていないと、楽天APIが `REQUEST_CONTEXT_BODY_HTTP_REFERRER_MISSING` で失敗します。

### 2. Googleスプレッドシートを用意する

1. Googleスプレッドシートを作成します。
2. URLの `/d/` と `/edit` の間にある文字列を確認します。
3. 今回使うスプレッドシートIDは以下です。

```text
1INtwfqMUtZA5FNMYTTRWyBilX3MaRbZKm7RpjmHgmJo
```

この値をGitHub Secretsの `SPREADSHEET_ID` に登録します。

### 3. Google CloudでSheets APIを有効にする

1. [Google Cloud Console](https://console.cloud.google.com/) を開きます。
2. プロジェクトを作成、または既存プロジェクトを選びます。
3. `Google Sheets API` を有効化します。
4. サービスアカウントを作成します。
5. サービスアカウントキーをJSON形式で作成します。
6. JSONファイルの中身をすべてコピーします。

コピーしたJSON全文をGitHub Secretsの `GOOGLE_SERVICE_ACCOUNT_JSON` に登録します。

### 4. スプレッドシートに編集権限を付ける

サービスアカウントのメールアドレスを確認します。JSON内の `client_email` に書かれています。

Googleスプレッドシートを開き、そのメールアドレスを編集者として共有してください。これを忘れると、GitHub Actionsから追記できません。

### 5. GitHub Secretsを登録する

GitHubリポジトリで以下を開きます。

`Settings > Secrets and variables > Actions > New repository secret`

登録するSecretsは3つです。

| Secret名 | 入れる値 |
| --- | --- |
| `RAKUTEN_APPLICATION_ID` | 楽天APIのアプリID |
| `GOOGLE_SERVICE_ACCOUNT_JSON` | サービスアカウントJSON全文 |
| `SPREADSHEET_ID` | スプレッドシートID |
| `RAKUTEN_REFERER` | `https://github.com/dai-kun811/rakuten-room-agent` |

任意で以下も登録できます。

| Secret名 | 入れる値 |
| --- | --- |
| `RAKUTEN_ACCESS_KEY` | 楽天APIのAccess Key。登録すると最新版の商品検索APIを使います。未登録の場合は旧版の商品検索APIを使います。 |

## 自動実行

ワークフローは [.github/workflows/daily.yml](.github/workflows/daily.yml) にあります。

```yaml
schedule:
  - cron: "0 22 * * *"
```

GitHub ActionsのcronはUTC基準です。`0 22 * * *` は日本時間の毎朝7:00です。

手動で試す場合は、GitHubの `Actions` タブから `Daily Rakuten ROOM Agent` を選び、`Run workflow` を押します。

## 商品選定ジャンル

- 育児便利グッズ
- ベビー用品
- キッズ用品
- 知育玩具
- おうち遊び
- 外遊び
- 育児時短グッズ
- 子ども靴
- 絵本
- 育児家電
- プレゼント向け商品

## スコアリング

100点満点で評価します。

### 売れやすさ 30点

- レビュー100〜300件: 10点
- レビュー301〜1000件: 20点
- レビュー1001〜5000件: 25点
- レビュー5001件以上: 30点

### 評価 20点

- 4.3〜4.49: 10点
- 4.5〜4.69: 15点
- 4.7以上: 20点

### ターゲット親和性 20点

商品名や説明文に、育児、ベビー、キッズ、知育、子ども、幼児、おもちゃ、絵本、靴、時短、プレゼントなどの関連語がある場合に加点します。

### 価格 15点

育児ROOMで購入されやすい価格帯を優先します。

- 3,000〜8,000円: 15点
- 1,500〜12,000円: 10点
- それ以外: 3点

### 楽天ROOM向き 10点

ギフト、プレゼント、人気、ランキング、送料無料、セット、かわいいなどの訴求語がある場合に加点します。

### 季節需要 5点

実行月に応じた季節ワードに一致する場合に加点します。

## 投稿文ルール

投稿文では実体験のような表現を使いません。

禁止例:

- 我が家で使っています
- 実際に買いました
- 息子が気に入っています
- 使ってみました
- 買ってよかった

商品カテゴリ、商品名、説明文、容量やサイズなどの確認できる情報をもとに、育児中の具体的な生活シーンが見える文章を作ります。レビュー本文がない場合は、口コミやレビューに触れません。

現在の投稿文は、楽天ROOMで読みやすいように以下の方針で作成します。

- タイトル、投稿文、ハッシュタグ、狙った感情の構成
- タイトルは商品名ではなく、悩み、あるある、不安回避を入れる
- 冒頭は育児あるあるへの共感から入る
- 商品が解決する悩みを生活シーンで書く
- 使用後に得られる安心感やラクさを入れる
- セール中なら自然に確認を後押しする
- 商品名が長い場合は自然に短縮する
- レビュー本文がない場合、口コミ、レビュー、満足理由に触れない
- 商品説明やスペックの羅列ではなく、親子の生活シーンを優先する
- パパ目線の自然な口語体にする
- ハッシュタグは楽天ROOM向けに5個

以下のような表現は使いません。

- 口コミで好評
- レビューで人気
- 満足理由
- おすすめです
- 便利です
- 使いやすいです
- チェックしてください
- 容量と価格だけでも確認

投稿文は以下の順で構成します。

1. 育児あるある
2. 共感
3. 商品が解決する悩み
4. 解決後の安心感やラクさ
5. セール中なら自然な後押し

### レビュー本文がない場合の差別化理由

Webスクレイピングは禁止のため、レビュー本文をページから取得することはしません。レビュー本文がない場合は、口コミ、レビュー、購入者の満足理由を読んだような表現を使いません。

たとえば、おしりふきなら「最後の1パックで焦る」、知育玩具なら「雨の日に家遊びのネタがなくなる」、子ども靴なら「登園前に靴で止まる」、育児家電なら「寝かしつけ後も家事が残る」のように、商品カテゴリから想定できる育児シーンを投稿文へ自然に入れます。

## ハッシュタグルール

商品ごとに以下の5個を作成します。

1. 購入意欲タグ: `#まとめ買い候補`, `#おうち遊び見直し`, `#保育園準備` など
2. 商品カテゴリタグ: `#知育玩具`, `#育児便利グッズ`, `#ベビーグッズ` など
3. 悩み解決タグ: `#おうち遊び`, `#イヤイヤ期対策`, `#育児時短` など
4. ターゲットタグ: `#2歳育児`, `#3歳育児`, `#共働き育児` など
5. ブランドタグ: `#とらパパ厳選`

プレゼント訴求が強い商品では、相手が本当に使う理由がある場合だけ `#出産祝い` や `#ギフト候補` などのプレゼントタグを優先します。

`#子育て`, `#育児`, `#ママ`, `#パパ` は広すぎるため、優先して使いません。

## 訴求カテゴリとベネフィット

商品名、カテゴリ、説明文から以下のどれか1つを自動判定します。

- 消耗品
- 知育玩具
- 子ども靴
- 育児家電
- ギフト
- その他育児用品

あわせて、`ストック切れの不安が減る`、`雨の日の親子遊びが回しやすい`、`登園前のバタつきが減る`、`寝かしつけ後の家事負担が軽くなる` のような生活シーンベースの1文を出力します。

## ローカルで動かす場合

Python 3.11以上を用意します。

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

環境変数を設定します。

```powershell
$env:RAKUTEN_APPLICATION_ID="楽天APIのアプリID"
$env:GOOGLE_SERVICE_ACCOUNT_JSON='{"type":"service_account",...}'
$env:SPREADSHEET_ID="1INtwfqMUtZA5FNMYTTRWyBilX3MaRbZKm7RpjmHgmJo"
python src/main.py
```

## エラー確認

GitHub Actionsで失敗した場合は、Actionsの実行ログを開いてください。どの処理で失敗したか分かるようにログへ原因を出します。

商品が0件だった場合でも、処理を黙って終わらせず、スプレッドシートに `ERROR` 行を追加します。`おすすめ理由` 列に、楽天API取得0件、全商品が作成済みURLと重複、条件に合う商品が0件などの原因を記録します。

`REQUEST_CONTEXT_BODY_HTTP_REFERRER_MISSING` または `Referer設定を確認してください。` と出た場合は、以下を確認してください。

- GitHub Secretsに `RAKUTEN_REFERER` が登録されている
- 値が `https://github.com/dai-kun811/rakuten-room-agent` になっている
- 楽天ウェブサービスのアプリ設定で「許可されたWebサイト」に `github.com` または `https://github.com/dai-kun811/rakuten-room-agent` が登録されている

GitHub Actionsのログには、値を出さずに以下を表示します。

- `referer_configured=True`: GitHub Actions上で `RAKUTEN_REFERER` が読み込めています
- `楽天API送信予定ヘッダー`: アプリが `requests.get` に渡す予定のヘッダー名です
- `楽天API実送信ヘッダー`: 実際のHTTPリクエストに入ったヘッダー名です

ヘッダーの値は `***` でマスクされます。
最新版APIでは `accessKey` もHTTPヘッダーで送信します。ログにはヘッダー名だけ表示し、値は表示しません。
楽天側のReferer判定に対応するため、`RAKUTEN_REFERER` から `Origin` も自動生成して送信します。たとえば `RAKUTEN_REFERER=https://github.com/dai-kun811/rakuten-room-agent` の場合、`Origin=https://github.com` を送ります。

## 楽天API取得の方針

楽天市場商品検索APIは、まず公式仕様にある最小構成で呼び出します。

- `applicationId`
- `accessKey`、最新版APIを使う場合のみ
- `keyword`
- `hits`
- `page`
- `format=json`

`sort`、`genreId`、`orFlag`、`field`、`hasReviewFlag` などの追加パラメータは、API取得失敗の原因を切り分けにくくするため現在は使っていません。レビュー件数、評価、スコアリングによる絞り込みは、楽天APIから商品を取得できた後にアプリ側で行います。

初回のAPI取得数を抑えるため、カテゴリは5個まで、各カテゴリのキーワードは1個まで、ページは1ページ目だけ、`hits=30` で取得します。APIリクエスト間には2秒以上待ちます。429のレート制限が出た場合だけ、3秒待って最大3回リトライします。403のRefererエラーはリトライせず、スプレッドシートにERROR行として記録します。

`RAKUTEN_ACCESS_KEY` がある場合は最新版APIを優先します。ただし、最新版APIでRefererエラーが出た場合は、商品取得を止めないために旧APIへ1回だけ自動で切り替えます。同じAPIへの繰り返しリトライは行いません。

もし `wrong_parameter` が出た場合は、GitHub Actionsのログとスプレッドシートの `ERROR` 行に、HTTPステータスと楽天APIのエラー説明が記録されます。楽天の `applicationId` や `accessKey` はログに出しません。

## 商品が0件にならないための段階的な条件緩和

通常は品質条件を優先します。

1. `strict_priority`: レビュー300件以上、評価4.3以上、総合スコア80点以上
2. `strict`: レビュー100件以上、評価4.3以上、総合スコア70点以上
3. `relaxed`: レビュー30件以上、評価4.0以上、総合スコア50点以上
4. `debug_minimum`: レビュー0件以上、評価0以上、総合スコア0点以上

上から順に最大5件まで選びます。これにより、厳格条件で0件の日でも、最低1件は出力されやすくなります。

品質条件を戻したい場合は、GitHub Variablesで調整できます。

| Variable名 | 既定値 |
| --- | --- |
| `ENABLE_RELAXED_FALLBACK` | `true` |
| `STRICT_PRIORITY_MIN_REVIEW_COUNT` | `300` |
| `STRICT_MIN_REVIEW_COUNT` | `100` |
| `STRICT_MIN_REVIEW_AVERAGE` | `4.3` |
| `STRICT_PRIORITY_TOTAL_SCORE` | `80` |
| `STRICT_MIN_TOTAL_SCORE` | `70` |
| `RELAXED_MIN_REVIEW_COUNT` | `30` |
| `RELAXED_MIN_REVIEW_AVERAGE` | `4.0` |
| `RELAXED_MIN_TOTAL_SCORE` | `50` |
| `DEBUG_MIN_REVIEW_COUNT` | `0` |
| `DEBUG_MIN_REVIEW_AVERAGE` | `0` |
| `DEBUG_MIN_TOTAL_SCORE` | `0` |

厳格条件だけに戻したい場合は、`ENABLE_RELAXED_FALLBACK=false` にしてください。
