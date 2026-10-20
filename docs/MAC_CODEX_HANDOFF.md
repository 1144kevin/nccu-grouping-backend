# 給 Mac 上 Codex：從 Windows 接手、啟動到 iPhone 驗收

更新：2026-10-20。這是一份可直接交給 Mac 上 Codex 閱讀及依序操作的交接指令。

## 先讀這段，再執行

目標：保留既有功能，在 Mac 啟動本機 App／API，沿用 Supabase 開發資料庫，
驗證登入、拼單與偷揪 AI，再準備 iPhone 實測。**不部署、不上傳 TestFlight、
不使用付費 API／建置服務、不 push。** Apple 裝置模型尚未整合。

只操作這兩個專案及指定 Supabase 開發專案：

| 項目 | 值 |
| --- | --- |
| 後端資料夾／分支 | `~/projects/nccu-grouping-backend`／`main` |
| App 資料夾／分支 | `~/projects/nccu-grouping-app`／`kevin_dev` |
| Supabase project ref | `axdwbfhrkbovgrbngrrh` |
| Supabase URL | `https://axdwbfhrkbovgrbngrrh.supabase.co` |
| Mac 本機 API | `http://127.0.0.1:8000/api/v1` |
| Mac Web App | `http://localhost:8081` |
| Mac 本機模型 | Ollama `127.0.0.1:11434` |

接手時先讀兩個專案的 `AGENTS.md`（若存在），檢查 Git 狀態及交接 manifest。
不覆寫既有 Mac 工作、不切換分支、不 reset、不清空資料庫。缺環境先列出實際缺項，
仍可完成的步驟繼續做。安裝鎖檔依賴可以進行；Apple 登入、授權條款、裝置信任與
憑證信任由使用者操作。不要索取或顯示密碼、私鑰、MCP Token、service_role。

**分兩段驗收，不能混寫通過：**

- Mac Web／Simulator：完整本機模型、語義排序與偷揪成團。
- iPhone：可信內網 HTTPS、原生 Google 回跳、拼單、手動需求與規則草案成團。
  `run_device_core.py` 預設停用模型；可依下節的明確設定啟用受保護的本機 AI，
  仍須實機驗證，不代表 Windows 測試已完成手機驗收。

## 1. Windows 要搬哪些東西

最簡單方式是搬交接包，不必 push／重新下載 Git 遠端。

Windows 後端目錄執行（兩邊工作目錄必須乾淨）：

```powershell
cd C:\Users\aup20\projects\nccu-grouping-backend
.venv\Scripts\python.exe scripts\mac_handoff.py export
```

把 `.local/mac-handoff/` 複製到 Mac 的 `~/Downloads/mac-handoff/`。
此目錄可能有歷次舊 bundle；**只要搬以下檔案即可**：

1. `manifest.json`。
2. manifest 裡 `file` 指向的兩份 `.bundle`（以 manifest 為準，不挑舊檔）。
3. `import_mac_handoff.py`。
4. `START_HERE.txt`、`MAC_CODEX_HANDOFF.md`（本文件）。

另外依需要，私下移交設定；**不要搬整個 `.local`**：

| 檔案 | 是否需要／Mac 做法 |
| --- | --- |
| `.local/supabase-dev.env` | 必要設定可重建；最少只需要相同 URL 與 Publishable key，不需資料庫密碼 |
| `.local/media-signing.env` | 測拼單圖片才需要；安全移交既有金鑰，不能另產新金鑰取代舊資料庫設定 |
| `.local/local-ai.env` | 不用搬；依第 4 節在 Mac 建立 |
| `.venv`、`node_modules`、Windows `ollama.exe` | 不搬；Mac 重建／安裝原生版本 |
| MCP 設定／token、Apple 憑證、`.p8`／`.p12`、暫存測試帳密 | 不打包到交接包、Git 或 App |
| 本機舊測試 SQLite、驗收截圖、舊模型快取 | 啟動不需要；雲端資料已在同一 Supabase 開發專案 |

用 AirDrop、USB 或團隊私有傳輸即可。bundle 包含 Git 歷史，只交付自己團隊。
現有 127 示範貼文會直接從開發資料庫讀到；**不要重建 Supabase、重跑 seed 或 migrations**。
期限會自然經過，示範數量不等於目前可加入數量。

## 2. Mac 匯入並檢查環境

新 Terminal：

```bash
cd ~/Downloads/mac-handoff
python3 import_mac_handoff.py --destination ~/projects
python3 import_mac_handoff.py --destination ~/projects --apply
```

第一條只檢查 checksum／目錄，第二條才建立兩份 checkout。Mac 若已有同名目錄，
不要刪掉或覆蓋；改用新的 destination，例如 `~/projects/nccu-handoff`，並同步改下文路徑。
importer 不替你改遠端；origin 指向本機 bundle 是正常的，無須 push。

必要環境：完整 Xcode 26.4+、Node 22.13+（建議 24 LTS）、Git、uv、Python 3.12／3.13。
匯入工具本身可用 Python 3.9+；專案執行環境另由 uv 建立。
Xcode 先由使用者開啟一次、完成條款、Apple Team 登入與所需 Simulator 安裝。

```bash
xcodebuild -version
node --version
git --version
uv --version
```

缺少軟體時從官方安裝：
[Xcode](https://developer.apple.com/xcode/)、[Node](https://nodejs.org/)、
[uv](https://docs.astral.sh/uv/getting-started/installation/)。不要盲目升級鎖檔。
版本依據：[本專案 Expo SDK 57 支援表](https://docs.expo.dev/versions/v57.0.0/)。

## 3. 最快先跑起來：後端＋瀏覽器

Terminal A（第一次準備）：

```bash
cd ~/projects/nccu-grouping-backend
bash scripts/setup_mac.sh
```

編輯它建立的 `.local/supabase-dev.env`，最少填：

```dotenv
SUPABASE_URL=https://axdwbfhrkbovgrbngrrh.supabase.co
SUPABASE_PUBLISHABLE_KEY=填入開發專案的sb_publishable開頭金鑰
CORE_GOOGLE_IOS_ENABLED=false
```

上面中文是佔位字，請換成實際值。已安全移交此檔時保留既有有效內容；不要覆寫金鑰。

```bash
.venv/bin/python scripts/run_supabase_core.py --check
.venv/bin/python scripts/run_supabase_core.py
```

保持 Terminal A 開著。`--check` 不建立監聽，也不代表雲端帳號已實際登入。

Terminal B：

```bash
cd ~/projects/nccu-grouping-app
npm ci
npm run dev:supabase
```

用瀏覽器開 `http://localhost:8081`，用專用測試 Google 帳號登入。
這一步不需要 Mac Codex 連 MCP；App 執行只需要 Publishable key。
沒有圖片簽章設定時，先驗收文字拼單。

## 4. Mac 免費本機 AI（先在 Web／Simulator 測）

Windows 的 `setup_local_model.py`／`run_local_model.py` 使用 `ollama.exe`，
**不要在 Mac 執行這兩支 Windows 工具**。
依 [Ollama macOS 官方文件](https://docs.ollama.com/macos) 安裝 Mac 版本，確認 `ollama --version` 可執行。
M1 可作本機測試，記憶體不足／速度慢時如實記錄，不偷偷換雲端模型。

先退出已啟動的 Ollama 選單列 App，避免另一個既有服務占用 11434；不要強制殺掉未知程序。
Terminal C 啟動一次專用服務（保持開著）：

```bash
cd ~/projects/nccu-grouping-backend
mkdir -p .local/ollama/models
OLLAMA_HOST=127.0.0.1:11434 \
OLLAMA_MODELS="$PWD/.local/ollama/models" \
OLLAMA_NO_CLOUD=1 OLLAMA_NUM_PARALLEL=1 OLLAMA_MAX_LOADED_MODELS=1 \
OLLAMA_MAX_QUEUE=1 OLLAMA_CONTEXT_LENGTH=4096 \
OLLAMA_DEBUG_LOG_REQUESTS=false OLLAMA_NOHISTORY=1 ollama serve
```

另一個 Terminal 下載這兩個明確指定的本機模型（有網路與磁碟用量，無模型呼叫費）：

```bash
OLLAMA_HOST=127.0.0.1:11434 ollama pull qwen3.5:4b
OLLAMA_HOST=127.0.0.1:11434 ollama pull qwen3-embedding:0.6b
OLLAMA_HOST=127.0.0.1:11434 ollama list
```

後端建立 `.local/local-ai.env`：

```dotenv
STEALTH_AI_PROVIDER=ollama_local
STEALTH_OLLAMA_MODEL=qwen3.5:4b
STEALTH_OLLAMA_LOCAL_ONLY_CONFIRMED=true
STEALTH_SEMANTIC_ENABLED=true
STEALTH_EMBEDDING_MODEL=qwen3-embedding:0.6b
```

Terminal A 用 Ctrl+C 停止後端，再重跑 `scripts/run_supabase_core.py` 載入設定。
Ollama 不需要帳號、雲端 API key，不開放到區網。
[官方雲端停用與監聽設定](https://docs.ollama.com/faq)。

## 5. 再跑 iOS Simulator

後端與 Ollama 維持開著。可以停止 Web Metro，再於 App 目錄執行：

```bash
cd ~/projects/nccu-grouping-app
npm run apple:configure -- --profile simulator
npm run apple:check
npm run apple:simulator
```

這是原生 App 本機 Release configuration 建置，JS 內嵌，**不是 Expo Go**；
不使用 EAS、沒有上傳。修改網址／程式後要重建，不依賴 Metro 熱更新。
Simulator 可連同一台 Mac 的 localhost API，模型也由 Mac 執行。
原生 Google 登入仍需完成下一節的 callback 開關；Web 已成功不代表原生回跳成功。

## 6. iPhone 真機：只差這段人工環境

先由使用者備妥：Mac 與 iPhone 連受信任的同一內網、確認雙方 IP、用傳輸線配對，
在 Xcode 選 Apple Team，依提示開啟 iPhone Developer Mode／信任電腦。
**傳輸線安裝 App 不代表手機能連到 Mac 的 localhost。**

還需要：含 Mac 內網 IP SAN 的開發 TLS 憑證／對應私鑰，以及 iPhone 信任其簽發者。
沒有可信開發憑證時先停在 Simulator，列為待辦；不要忽略安全警告、放寬 ATS、
關閉 TLS 驗證、用 tunnel、設 0.0.0.0，或自行改防火牆。憑證信任交使用者處理。
這些條件沒有完成，就不能聲稱真機已連線。

Google 原生回跳需要在同一 Supabase 專案的 Authentication → URL Configuration
保留既有 localhost，另外加入精確 Redirect URL（不要萬用字元）：

```text
nccugrouping://auth/callback
```

Google Cloud 原本的 redirect URI **維持**：

```text
https://axdwbfhrkbovgrbngrrh.supabase.co/auth/v1/callback
```

設定確認後才把後端 `.local/supabase-dev.env` 的 `CORE_GOOGLE_IOS_ENABLED` 改成 `true`。
詳細原生登入合約見 `docs/IPHONE_TESTFLIGHT.md`；不要重建 OAuth client 或搬 Google secret 到 App。

在後端目錄複製範本（若目的檔已存在，先檢查、不覆寫）：

```bash
cd ~/projects/nccu-grouping-backend
test -f .local/device-test.env || cp config/device-test.env.example .local/device-test.env
```

編輯 `.local/device-test.env`，以下 **IP 只是例子，必須換成自己的**：

```dotenv
DEVICE_HOST=192.168.1.10
DEVICE_CLIENTS=192.168.1.20,192.168.1.21
DEVICE_TLS_CERT=.local/tls/device-cert.pem
DEVICE_TLS_KEY=.local/tls/device-key.pem
```

`DEVICE_HOST` 是 Mac，`DEVICE_CLIENTS` 是每台測試 iPhone 的精確 IP，最多八台。
先停止 8000 的一般 API，避免混淆；改用內網入口（檢查成功後才啟動）：

```bash
.venv/bin/python scripts/run_device_core.py
.venv/bin/python scripts/run_device_core.py --serve
```

`--serve` 才啟動指定內網 IP 的 8443 HTTPS；不修改防火牆。預設檢查只驗設定與金鑰配對，
不驗證手機信任／SAN／實際網路可達。讓使用者完成信任後，在手機 App 驗證連線。

App 目錄：

```bash
cd ~/projects/nccu-grouping-app
npm run apple:configure -- --profile device --api https://192.168.1.10:8443/api/v1
npm run apple:check
npm run apple:device
```

上面網址同樣要換成 Mac 實際 IP。選擇接上的 iPhone，處理 Xcode 簽章後安裝。
手機首次要求本機網路權限時由使用者選擇；拒絕應顯示連線錯誤，不能假裝登入成功。
保持 Mac 醒著、API 開著、同一 Wi-Fi；此版本不能離開內網使用。

**AI 限制再提醒：**device runner 會強制設定 `offline_rules`／關閉語義排序，
不會讀取 `local-ai.env`。手機這段先測手動需求、規則草案及全員確認；完整本機 AI
在第 4～5 節驗收。Apple Foundation Models 尚未接入，不是啟用 Apple Intelligence 就會自動有。

## 7. 實際怎麼驗收（A／B 兩個測試帳號）

優先用專用測試 Google 帳號，不在真人資料上練習刪除／封鎖／申請。
同一 Web 瀏覽器的分頁可能共用登入，請登出再換帳號；真機可用兩台 iPhone。
原 127 筆示範貼文用來瀏覽即可，不對不可登入的示範主揪送出驗收申請。

| 驗收 | 操作與通過條件 |
| --- | --- |
| 登入 | Google 成功回 App；取消登入不出現假帳號；重開續期正常；登出再開不殘留前帳號 |
| 核心拼單 | A 新增「隔離驗證」文字拼單、未來期限、容量 2；B 申請仍是 1/2；A 接受才變 2/2；雙方能聊天；B 截止前退出恢復 1/2 |
| 推薦 | 切「依我的偏好」，有理由；滿團／截止／取消不進推薦；「最新拼單」仍可保留這些貼文供查看 |
| 分身／需求 | A、B 各建立分身，明確開啟，測試時取消安靜時段，不接受示範配對；填相容餐點、地點、時間與預算 |
| 本機 AI（Mac） | A 先說缺資訊需求，看補問；補齊後套用核對再儲存；不得把預算當價格、不擅自勾取餐、不自動儲存 |
| 語義判讀（Mac） | 先有合法配對，再點 AI 語義排序與判讀；看理由／待確認事項；不得把相似度說成成功率，判讀也不能自行同意 |
| 全員確認 | A、B 各表示意願；提交第一版後單人同意不建團；另一人改費用／分工後同意歸零；雙方確認第二版才建立一筆拼單和聊天室 |
| 成團結果 | 費用與分工採用最新版；雙方收發訊息；重試不多建一團；第三個未參與帳號不能讀聊天室或私人配對 |
| iPhone 模型 | 預設停用時可回手動；明確啟用後測需求理解、語義判讀、超額限制及斷線重試；規則草案與全員同意保留，沒有付費 fallback |

可直接使用以下合成情境；實際時間以服務現在為準，別複製舊日期：

```text
A：我想找人吃正餐熱湯。
補充：政大東側門，從現在60分鐘後開始，持續120分鐘，每人預算150元，費用未知。
我願意下單和分食，不取餐。

B：我有正餐熱湯與優惠券可分享，政大東側門，從現在60分鐘後開始，持續120分鐘，
每人預算150元，已確認每人90元。我願意提供優惠券、分食、下單和順路取餐。
```

手動核對兩人的分類為正餐、標籤可填「隔離熱湯」。先以 90 元第一版測一人同意，
再改 95 元並填費用調整原因，改下單角色，檢查同意歸零與重新確認。
本機小模型可能再次補問或摘要不準；檢查表單後才儲存，不把流暢文字當驗收通過。

若需要自動建立隔離帳號且 Mac 已有本專案限定的 Supabase MCP，可先閱讀
`scripts/smoke_stealth_cloud.py`：`prepare` 只產生本機 scope SQL，審閱後才執行限定
開發專案的 `prepare.sql`。使用正常帳號登入操作 App，不執行 `run` 來冒充畫面驗收。
完成後登出、檢查並執行同一 scope 的 `cleanup.sql`，確認 0 剩餘帳號才移除帳密檔。
未連 MCP 時用專用 Google 測試帳號即可，不阻擋專案啟動。不能刪除真實帳號或保留的示範批次。

驗收記錄填 `docs/DEVICE_ACCEPTANCE.csv`：裝置、OS、commit、結果與證據。
未執行寫未執行，缺環境寫阻擋原因；測完取消／整理自己新建的測試團，不破壞原資料。
既有 Windows Web 證據參考 `docs/STEALTH_AI_UI_ACCEPTANCE.md` 與
`docs/RECOMMENDATIONS_UI_ACCEPTANCE.md`，不能抄成 Mac／iPhone 通過。
最新訪談跨輪保留、三人併發／撤回／過期／重試的 API 與模型驗證見
`docs/STEALTH_INTERVIEW_RESILIENCE.md`；這也不代表已在真機驗收。
2026-10-20 另已補做 Windows Web 畫面驗收：兩輪訪談資訊保留、API 中斷後重試、
三人方案改版同意歸零、全員重新同意後建立單一拼單及三人聊天室。
實際步驟、清理及限制見上述兩份偷揪文件的新補驗章節；Mac／iPhone 仍須各自驗收。
後續另完成兩位隔離帳號同版修改、成團前撤回、方案截止及邀請自然到期的 Web 驗收。
App 已修正衝突刷新時草稿與錯誤遺失；新版需要重新勾選同意，未送出草稿不會自動覆蓋他人。
完整 App 回歸為 139 tests；請依上述文件，在 Mac／iPhone 另行測試同版修改與到期畫面。

## 8. 每次重新開發的最短指令

回報查核已套用至同一 Supabase 開發專案。若需使用已授權的管理帳號，在 Mac
建立 `.local/moderation.env`，內容為 `CORE_MODERATION_ENABLED=true`，重新啟動本機 API；
登入 App 後由「幫助與回報 → 管理回報」進入。不要新增或猜測管理者；授權 30 天到期後
需由專案擁有者明確續期。一般帳號不顯示入口，資料庫也會拒絕查核操作。
操作與剩餘產品缺口見 `docs/CORE_SUPABASE_DEV.md` 的回報查核章節。
競賽情境、重跑指令與效能限制見 `docs/COMPETITION_DEMO.md`。

### 選用：iPhone 使用 Mac 本機模型

先完成前述 Ollama 模型、HTTPS 憑證信任、Mac 主機 IP 與最多八個精確裝置 IP 設定。
Ollama 仍僅監聽 `127.0.0.1:11434`，手機只呼叫 `https://Mac私有IP:8443/api/v1`。
在 Mac 的 `.local/device-test.env` 加入 `DEVICE_AI_ENABLED=true`；模型名稱、
`STEALTH_AI_PROVIDER=ollama_local`、`STEALTH_OLLAMA_LOCAL_ONLY_CONFIRMED=true`
與語義啟用設定讀自 Mac 的 `.local/local-ai.env`，不繼承任意 shell 模型開關。

先執行 `python scripts/run_device_core.py` 做檢查；確認憑證 SAN／信任及授權裝置後，
才由本人執行 `python scripts/run_device_core.py --serve`。不自動改防火牆、憑證或穿透網路。
API 驗證 Supabase session；裝置入口拒絕非 HTTPS、非名單 IP、代理標頭及重複敏感標頭。
AI 請求最多 32 KiB，每來源 IP 每分鐘 12 次，整體每分鐘 240 次；模型忙碌可稍後重試。
原始模型輸入不寫入伺服器日誌，無 API 金鑰放到手機，不能據此宣稱可公開提供服務。

實機驗收：登入後兩輪訪談 → 語義判讀 → 手動核對表單 → 全員同意成團。
登出不能推論；未授權 IP、HTTP、超額請求被拒；停止 Ollama 只顯示可重試錯誤，
不得偷偷呼叫付費模型。Apple 原生 Foundation Models 仍未整合。

**Mac Web／AI：**先啟動第 4 節 Ollama → 後端 Terminal 執行
`.venv/bin/python scripts/run_supabase_core.py` → App Terminal 執行 `npm run dev:supabase`。
不改原生設定時，不需要每次重跑 `npm ci`／`uv sync`／下載模型。

**iPhone：**確認 Wi-Fi／IP／憑證未變 → 後端 `scripts/run_device_core.py` 檢查 →
`--serve` 啟動 → 開手機已安裝 App。App 程式或 API 網址改變才重建；改 IP 也需檢查憑證 SAN。

測試完用 Ctrl+C 停止自己啟動的 API／Ollama／Metro，不停止無關程序。
請 Mac Codex 最後回報「已執行項目／待本人操作／實際失敗與修正／尚未驗收」；
若修改程式，先跑相關測試，依功能分 commit 且不 push。
到此停止，不執行 release profile、Archive 上傳、EAS build／submit 或 TestFlight 發布。
