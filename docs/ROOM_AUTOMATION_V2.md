# 楽天ROOM 自動運用 v2 契約

この文書は段階移行中の新システム契約を定義する。Phase 1では契約とテストだけを追加し、既存のGitHub Actions、Windowsタスク、投稿worker、台帳には接続しない。

## 不変条件

- 運用日は必ず`Asia/Tokyo`で決定する。
- slotは`morning`、`noon`、`evening`だけとする。
- 同じ日・slot・URL・本文hashの外部投稿命令は一度しか発行しない。
- `POSTED`と`EXPIRED_UNPOSTED`は終端状態とする。
- `SUBMITTING`以降の結果が確定しない場合は`UNCERTAIN`にする。
- `UNCERTAIN`を自動で未投稿状態へ戻さない。
- CodexはROOMへ直接投稿せず、状態を`POSTED`へ変更しない。
- 品質基準を緩和してslotを埋めない。
- manifestはslot単位で`ready`または`blocked`を表現する。
- 投稿開始済み・投稿済みslotのmanifest revisionを後着runで差し替えない。

## slot状態

```text
PENDING
  -> READY
  -> BLOCKED
  -> EXPIRED_UNPOSTED

READY
  -> CLAIMED
  -> BLOCKED
  -> EXPIRED_UNPOSTED

CLAIMED
  -> SUBMITTING
  -> FAILED_PRE_SUBMIT
  -> EXPIRED_UNPOSTED

SUBMITTING
  -> SUBMITTED_UNCONFIRMED
  -> UNCERTAIN

SUBMITTED_UNCONFIRMED
  -> POSTED
  -> UNCERTAIN
```

`UNCERTAIN -> FAILED_PRE_SUBMIT`は、ROOM上に投稿が存在しないことを人間が明示確認した場合だけ許可する。

## incident routing

- `AUTO_RETRY`: 通信一時障害、rate limit、Actions・artifact遅延、投稿前の確定失敗。
- `CODEX_RECOMMENDED`: 品質候補枯渇、未対応分類、文章検証回帰、schema・API互換不具合。
- `USER_ACTION_REQUIRED`: 認証、CAPTCHA、2FA、投稿結果不明、二重投稿リスク、ROOMと台帳の矛盾、git不整合。
- `OPERATIONS_REQUIRED`: DB破損、disk full、Windowsタスク停止、heartbeat欠落。

## 再試行上限

| 対象 | 上限 |
|---|---:|
| 一時通信障害 | 2回 |
| Actions dispatch | 1回 |
| artifact再取得 | 2回 |
| 確定的な投稿前失敗 | 1回 |
| DB restore | 1回 |
| Codex修正 | 2サイクル |
| Codex後のActions再生成 | 2回 |
| Codex実行時間 | 1incident 45分、1日60分 |
| 同一fingerprintのCodex起動 | 24時間に1回 |

## 投稿品質の確認ゲート

投稿内容の手動品質確認は次の順で行う。

1. **Phase 3完了後・Phase 4開始前（必須）**: manifest v2の朝・昼・晩候補を商品ページ一次情報と照合する。商品種類、対象、悩み、確認済み特徴、購入前確認点、自然な日本語、URL一致を確認する。
2. **Phase 4 shadow期間（必須）**: 最低3営業日分を監査し、旧artifactとの候補差、直近7日・30日の偏り、本文類似を確認する。
3. **Phase 7 canary（必須）**: morning、noon、eveningの外部投稿権限を開く直前に各候補を再確認し、投稿後は実ROOM表示とDB・互換台帳を照合する。
4. **本番移行後**: コード変更後、品質incident発生時、定期サンプル監査時に確認する。正常日の全投稿をCodexが毎回再監査しない。

品質確認で不合格になったslotだけを`blocked`とし、品質合格済みの別slotは停止しない。

## Phase 1の非目標

- SQLite DBを作らない。
- GitHub Actionsを変更しない。
- Windowsタスクを登録・変更しない。
- ROOM、Google Sheets、公開進捗へ書き込まない。
- 既存workerからこの契約コードを呼ばない。
