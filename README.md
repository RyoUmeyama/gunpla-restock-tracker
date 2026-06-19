# 🤖 転売検証 在庫トラッカー（小額モデル検証 Q1 用）

転売プロジェクト（resale-arbitrage）の**小額モデル検証**のためのツール。
`docs/07_minimal_validation.md` / `docs/08_broad_screening.md` の **Q1「入手再現性」** を
実測するために、狙いの商品が正規店で定価・在庫ありになった瞬間を検知して即通知する。

> ⚠️ これは利益を取りに行く投資ではなく、「このモデルが回るか（定価で争奪戦に勝てるか）」を
> 最小コストで見極める実験のための入手監視Botです。

## 監視中の商品（2026-06-19時点）
| 商品 | 判定方式 | データ源 | 定価 |
|------|---------|---------|------|
| HG 1/144 ナイチンゲール | gdb_soldout | GunplaDatabase | 7,700円 |
| ワンピカード OP-16 決戦の刻 BOX | toei_stock_status | 東映アニメ公式 | 5,280円 |

> OP-16は網羅スクリーニング(docs/08)で**唯一「入手容易さ=medium（定価枠が現実的に残る）」**と
> 判定された本命候補。最新弾の定価枠を取りに行く検証用。

## 🎯 機能

- ✅ 複数サイト・複数判定方式に対応（`method` で切替）。商品ページを監視し在庫復活を検知
- ✅ 「在庫なし→在庫あり」に変化した時だけ通知（誤通知・連投なし）
- ✅ メール＋Discord(Webhook)の二重通知
- ✅ GitHub Actions で10分ごとに自動実行
- ✅ 取得失敗時は前回状態を維持（誤通知防止）

## 📊 データ源と在庫判定（事前検証で確定）

### 判定方式（method）
- **gdb_soldout**（ガンプラ）: GunplaDatabase の `shop_status_container` ブロックの
  `soldout` クラス/「売切」テキストの有無で店舗別在庫を判定。
- **toei_stock_status**（OP-16等）: 東映アニメ公式の商品ページ埋め込みJSONの
  `stock_status` を読む。`"0"` なら在庫なし、それ以外なら在庫あり。Shift_JIS。

### なぜ GunplaDatabase なのか
- **ヨドバシ.com 直接監視は不可**: Bot対策が強く、自動取得（curl/WebFetch）が接続失敗・タイムアウト。
- **GunplaDatabase は取得可能**で、複数店舗（Amazon等）の在庫状態を集約している。
- → GunplaDatabase の商品個別ページ（`?no=2294`）を監視する。

### 在庫判定ロジック
ページ内の `shop_status_container` ブロックごとに、CSSクラス `soldout` または
「売切」テキストの有無で店舗別の在庫を判定する。
**soldout でない店舗が1つでもあれば「在庫あり」** とし、前回「在庫なし」からの変化を通知する。

### ⚠️ 重要な注意（検証の前提）
- 検知される在庫には **Amazon等の転売価格**が含まれる場合がある。
- **通知が来たら「定価7,700円で買えるか」を必ず人間が確認する**こと（通知文に明記）。
- 定価で買えなければ見送る（＝Q1「定価入手の再現性」の検証にならないため）。

## 🛠️ セットアップ

### ローカル実行
```bash
pip install -r requirements.txt
python check_stock.py
```
※ メール/Discord通知には環境変数が必要。未設定なら検知のみ（送信スキップ）。

### GitHub Secrets（通知用・feiler-teddy-trackerと共通）
| Secret名 | 値 |
|---------|-----|
| `SMTP_SERVER` | `smtp.gmail.com` |
| `SMTP_PORT` | `587` |
| `SMTP_USERNAME` | 完全なGmailアドレス |
| `SMTP_PASSWORD` | Gmailアプリパスワード（16文字・スペースなし） |
| `RECIPIENT_EMAIL` | 通知先メールアドレス |
| `WEBHOOK_URL` | Discord Webhook URL（任意・別チャンネル通知用） |

### GitHub Actions
`.github/workflows/check-stock.yml` が10分ごとに自動実行。状態は cache で永続化。

## 📁 ファイル構成

| ファイル | 役割 |
|---------|------|
| `check_stock.py` | メイン。在庫判定→復活検知→通知 |
| `config.py` | 監視対象・エンドポイント・判定マーカーの設定 |
| `email_utils.py` | SMTP送信（feilerから流用） |
| `webhook_utils.py` | Discord/Slack送信（feilerから流用） |
| `stock_state.json` | 前回在庫状態（自動生成） |

## 🔔 通知が来たら（検証の手順）

1. **まず価格を確認** — 定価7,700円で買えるか？（Amazon等は転売価格の場合あり）
2. 定価なら**即購入手続き**（数分で完売の争奪戦）
3. **記録**（docs/07 のKPI）: 何回目の挑戦で買えたか・所要時間・売り切れまでの分数
4. 確保できたらフリマ出品 → 売却日数・実際の手残りを記録

## 📝 監視対象の追加・変更

`config.py` の `WATCH_ITEMS` に GunplaDatabase の商品ID（`no=`）を追加する。
※対象は方針に合うもの（非酒類・非TCG・正規新品・未開封のまま売れる）に限ること。

## ⚠️ 注意

- サイトへの負荷に配慮し、商品ごとに1.5秒間隔でリクエストしている。
- あくまで個人利用の在庫通知・検証目的。
