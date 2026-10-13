# 台股每日篩選器

每個交易日收盤後，自動抓取**全部上市＋上櫃股票**的行情，依照你在 `config.yaml` 設定的條件（爆量、突破季線、漲停……）標記股票，然後：

- 📧 寄一封 Email 給你，列出各策略符合的股票
- 🌐 更新一個網頁報表（可排序、搜尋、看近 60 日走勢）

程式跑在 **GitHub Actions**（GitHub 提供的免費雲端主機），你的電腦關機也會照常執行。

## 通知方式

- **預設：GitHub Issue 通知（不需要任何密碼）**：每天有符合的股票時，程式會在這個儲存庫開一則 Issue，GitHub 會寄通知信到你 GitHub 帳號的信箱（需在儲存庫右上角 Watch 設為 **All Activity**）。
- **選用：Gmail**：若想由自己的 Gmail 寄出（附完整網頁報表檔），照下方第 2、3 步設定 `SMTP_PASSWORD` 後會自動改用 Gmail。

---

## 一次性設定（約 15 分鐘）

### 1. 建立 GitHub 帳號與儲存庫

1. 到 <https://github.com> 註冊（已有帳號可略過）。
2. 右上角「＋」→ **New repository**。
   - Repository name：例如 `tw-stock-screener`
   - 選 **Public**（公開才能免費用 GitHub Pages 網頁報表；程式和股票數據本身沒有個人資料。若選 Private，Email 照常可用，只是沒有網頁版）
   - 按 **Create repository**
3. 在新頁面點 **uploading an existing file**，把這個資料夾裡的**所有檔案和資料夾**（包括 `.github` 資料夾）拖進去，按 **Commit changes**。

> ⚠️ `.github` 是隱藏資料夾，Windows 需在檔案總管「檢視」→ 勾選「隱藏的項目」才看得到。上傳後確認儲存庫裡有 `.github/workflows/daily.yml`。

### 2. 產生 Gmail 應用程式密碼（寄信用）

1. Google 帳號需先開啟**兩步驟驗證**。
2. 前往 <https://myaccount.google.com/apppasswords>，名稱輸入 `stock`，按建立。
3. 複製那組 **16 碼密碼**（只會顯示一次）。

### 3. 把帳密存到 GitHub Secrets

儲存庫頁面 → **Settings** → 左側 **Secrets and variables** → **Actions** → **New repository secret**，新增三個：

| Name | Value |
|---|---|
| `SMTP_USER` | 你的 Gmail，例如 `abc@gmail.com` |
| `SMTP_PASSWORD` | 剛剛的 16 碼應用程式密碼 |
| `MAIL_TO` | 收件信箱（多個用逗號分隔；可以跟上面相同） |

Secrets 是加密儲存的，別人（包括公開儲存庫的訪客）看不到。

### 4. 開啟網頁報表（GitHub Pages）

**Settings** → 左側 **Pages** → Source 選 **GitHub Actions**。

報表網址會是 `https://你的帳號.github.io/tw-stock-screener/`

### 5. 第一次手動執行

**Actions** 分頁 →（若出現提示，按「I understand my workflows, go ahead and enable them」）→ 左側「台股每日篩選」→ 右邊 **Run workflow** → **Run workflow**。

第一次要下載約 150 個交易日的歷史資料，大概 **15–20 分鐘**；之後每天只補一天，約 1–2 分鐘。跑完就會收到信。

---

## 之後會自動做什麼

> 完整的每日時間表、出事看哪裡、怎麼補跑：見 [維護手冊 MAINTENANCE.md](MAINTENANCE.md)。

- 週一到週五 **台北時間 15:20** 自動執行；**17:40** 再跑一次補漏（已處理過會自動略過，不會重複寄信）。
- 國定假日休市：官方沒資料就直接結束，不寄信。
- 歷史資料存在 `data/history.csv.gz`，每日結果存在 `data/results/`，網頁存在 `site/`。

---

## 修改篩選條件

直接在 GitHub 網頁上打開 `config.yaml` → 右上角鉛筆圖示編輯 → **Commit changes**。下一次執行就會套用；想馬上看結果，到 Actions 手動 Run workflow 並勾選「已處理過也強制重跑」。

範例——加一個「3 天內曾漲停、今天爆量站上月線」的策略：

```yaml
  - name: 漲停後再爆量
    enabled: true
    conditions:
      - {type: limit_up, within: 3, days_ago: 1}   # 前 3 天內曾漲停（不含今天）
      - {type: volume_vs_prev, min: 1.5}
      - {type: above_ma, period: 20}
```

所有可用條件列在 `config.yaml` 最下方的說明。

> YAML 格式注意：縮排要用**空格**（不能用 Tab），`-` 後面要有空格。

---

## 在自己電腦上執行（選用）

```bash
pip install -r requirements.txt
python -m screener.main            # 抓到今天並篩選
python -m screener.main --no-email # 不寄信
python -m screener.main --date 2026-09-25 --force
```

報表會產生在 `site/index.html`。

---

## 資料來源與限制

- 主要來源：臺灣證券交易所、證券櫃檯買賣中心的每日收盤行情；若連不上會自動改用 Yahoo Finance。
- 價格**未還原除權息**，除權息前後的均線會有些微落差。
- 漲停判斷依前一日收盤價 ×1.1 並依升降單位計算；除權息日、新股上市前 5 日（無漲跌幅限制）可能不準。
- 股票清單第一次執行時自動產生於 `data/stock_list.csv`，官方資料出現新股時會自動加入（產業欄位會是空的）。
- 本工具僅供參考，不構成投資建議。

## 常見問題

- **Actions 顯示紅色失敗**：點進去看 log。若是證交所暫時封鎖（非 JSON 回應），程式會自動改用 Yahoo；若兩者都失敗，隔天會自動補抓缺的日期。
- **沒收到信**：檢查垃圾郵件匣；確認 Secrets 名稱拼寫正確、使用的是「應用程式密碼」。
- **GitHub 60 天沒動靜會暫停排程**：公開儲存庫若 60 天沒有任何 commit 會停用排程；本程式每天都會 commit 資料，所以正常不會發生。若被停用，到 Actions 重新啟用即可。
