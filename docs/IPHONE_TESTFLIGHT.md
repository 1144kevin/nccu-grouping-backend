# iPhone 與 TestFlight：目前完成範圍與接續步驟

目前仍是 **Windows 本機程式 + 指定 Supabase 開發專案**。沒有部署 API、開放 LAN、防火牆例外、EAS 雲端建置或上傳。Windows 可以驗證程式與 iOS JavaScript bundle，不能代替 Mac 的 Xcode 簽章或 iPhone 實測。

## 最短接手流程（本輪已備妥；尚未在 Mac 執行）

Windows 的新工具均已離線驗證；沒有部署、開埠、改防火牆、安裝憑證、登入 Apple 或開始付費服務。以下 Mac 指令由你後續執行，原生建置及首次安裝依賴可能需要網路。既有 main／kevin_dev 分支維持，沒有 push。

### 1. 把已提交版本帶到 Mac，不需要先 push

在 Windows 後端專案執行 `.venv\Scripts\python.exe scripts\mac_handoff.py export`。輸出 `.local/mac-handoff/` 內兩份 Git bundle 與 manifest.json；只含指定分支已提交歷史，不會打包 .local、虛擬環境、node_modules 或未提交檔案。bundle 仍含既有 Git 歷史，僅交付自己團隊。

把此資料夾放到 Mac，例如 `~/Downloads/mac-handoff`。資料夾內的 importer 使用標準 Python 3.9+，會先核對兩份 bundle 的 SHA256、分支與 commit；只建立全新的資料夾，不覆寫既有 checkout：

```bash
cd ~/Downloads/mac-handoff
python3 import_mac_handoff.py --destination ~/projects
# 上面是檢查；確認目錄後才真正建立兩份 checkout
python3 import_mac_handoff.py --destination ~/projects --apply
```

若 Mac 已有 checkout，不要覆蓋；先保存 Mac 的未提交工作，再以 bundle 作為 fetch 來源審查／整合。clone bundle 的 origin 會指向本機 bundle，這是預期結果；本輪不替你改遠端或 push。

### 2. Mac 後端：一次準備，以後一條啟動指令

準備完整 Xcode 26.4+、Node 22.13+（建議24 LTS）、Git 與 [uv](https://docs.astral.sh/uv/getting-started/installation/)。Xcode 請先手動開啟、接受授權並安裝 iOS Simulator。執行：

```bash
cd ~/projects/nccu-grouping-backend
bash scripts/setup_mac.sh
```

這會依 uv.lock 建立 Mac 專用 .venv，且只在不存在時建立 `.local/supabase-dev.env`。以文字編輯器填入開發專案 Publishable key；URL 已填妥。不要放 MCP Token、service_role、Apple 密碼，也不要複製 Windows 的 .venv。

```bash
.venv/bin/python scripts/run_supabase_core.py --check
.venv/bin/python scripts/run_supabase_core.py
```

`--check` 不監聽、不呼叫 Supabase。真正啟動仍只綁 localhost。預設無模型也可手動建立需求及產生規則費用草案。圖片若要在 Mac 啟用，需安全移交獨立 `.local/media-signing.env`，並確保其驗證金鑰與既有開發資料庫一致；不能重新隨機生成取代現有金鑰或放進 App。沒有金鑰時先測文字拼單。

### 3. Mac App：先模擬器

另一個 Terminal：

```bash
cd ~/projects/nccu-grouping-app
npm ci
npm run apple:configure -- --profile simulator
npm run apple:check
npm run apple:simulator
```

連線只保存在忽略於 Git 的 `.local/apple-dev.json`，不改原 .env 或既有 dev:supabase。simulator／device 指令使用 Release configuration，將 JavaScript 打進原生 App，減少 Metro 網路設定，不提供熱更新；修改程式或連線網址後需重建。初次原生 build 可能安裝 CocoaPods 依賴，不會使用 EAS 或上傳 TestFlight。Windows 呼叫這些原生動作會明確拒絕。

### 4. iPhone：準備好後才手動啟用內網 TLS

後端新增 `scripts/run_device_core.py`，預設只有檢查；舊啟動指令強制保持 loopback。內網測試需與電腦同網路、固定／確認 IP、精確裝置名單、含電腦 IP SAN 的 TLS 憑證，且 iPhone 信任該簽發者。`config/device-test.env.example` 提供範本；複製為 `.local/device-test.env`，填入真實測試值。

憑證及 iPhone 開發 CA 信任屬人工環境設定，不會由工具自動生成、安裝或關閉驗證。可使用團隊既有開發憑證；沒有可信憑證就先停在模擬器，或日後使用有效公開 HTTPS API。不得忽略瀏覽器憑證錯誤、把 ATS 全域關閉或公開 Windows 模型埠。

```bash
# Mac 後端：先驗證設定及證書／私鑰能配對，不建立監聽
.venv/bin/python scripts/run_device_core.py
# 準備完成後，才由你明確啟動；Ctrl+C 停止
.venv/bin/python scripts/run_device_core.py --serve
```

只綁指定 RFC1918 IPv4、8443 TLS、最多8個精確 iPhone IP；拒絕 HTTP、未列入裝置、錯誤 Host、代理標頭與非 localhost 瀏覽器 Origin。不是公開雲端服務，不支援 tunnel／反向代理。IP 名單是額外限制，不取代 Supabase 登入授權；不要在不信任的共用 Wi-Fi 測試。沒有自動修改防火牆或路由器。

前端例子（請換成測試電腦實際 IP）：

```bash
npm run apple:configure -- --profile device --api https://192.168.1.10:8443/api/v1
npm run apple:check
npm run apple:device
```

Xcode 選對 Team、接上 iPhone、依裝置提示啟用 Developer Mode／信任電腦。App 的「帳號與隱私 → 檢查目前測試連線」可確認 API 設定狀態；它不代替登入／資料庫／圖片功能驗證。內網模式預設手動需求及免費規則草案，未啟動／公開筆電模型。

### 5. 未來雲端：App 端只換設定，後端仍須另做部署驗收

```bash
npm run apple:configure -- --profile release --api https://你的API網域/api/v1
npm run apple:check
npm run apple:prepare-release
```

上述只準備 App 設定／Mac 原生工程，不部署 API、不上傳。真實 HTTPS API 存在後，可用 release profile 執行 apple:device 驗證，再在 Xcode Archive。release profile 會拒絕 localhost、內網 IP、HTTP、網址內憑證與 ATS 放寬。

**不能把內網測試 runner 當成雲端入口。** 現有後端刻意限指定 Supabase 開發專案；獨立雲端 API runtime、TLS代理驗證、單實例限流、CORS及Dockerfile已備妥，見CORE_SUPABASE_DEV.md的雲端部署準備。主機實際TLS／代理、防直連、容器build、監控備份與休眠行為仍需驗收；本輪沒有部署。更換 Supabase 專案也需有序 migrations／Auth redirect／私人 Storage 金鑰與資料遷移，不能只換一把 key。本輪已把手機端 URL 切換及交接做成固定入口，沒有宣稱雲端已就緒。

### 6. Google、權限與驗收

依下方既有「原生 Google 回呼設定」完成 Supabase 精確 allowlist，再將 Mac 的 CORE_GOOGLE_IOS_ENABLED 設 true，重啟後端。Google Cloud callback 維持 Supabase URL。首次真機登入、取消、回跳與重開續期仍由你實測。

連線版本不提供語音、拍照、聊天附件或手機推播入口；原展示錄音保留。`npm run test:native-permissions` 會離線解析三種 profile 的實際原生 Info.plist，確認權限及 ATS；不產生原生工程。原生配置停用相機／麥克風與背景音訊權限，保留照片選取及本人觸發定位。照片說明改為拼單附圖，通知偏好明確是 App 內狀態；開 App 不應自動要求定位／麥克風／通知權限。原生配置改動必須重建才能生效。

逐項驗收請填寫 `docs/DEVICE_ACCEPTANCE.csv`：每項記錄裝置、build、結果及截圖；未執行不可寫通過。完成裝置驗收與未完成功能／政策補齊後才安排 TestFlight。新工具檢查成功只代表設定有效，不代表證書受手機信任或 API／資料庫可達。

## 已備妥的程式

- iPhone Google 登入使用已安裝的 `expo-web-browser`，透過 `ASWebAuthenticationSession` 開啟系統登入視窗。
- PKCE proof 只放在記憶體，後端十分鐘內只接受一次交換。App 只接受固定回呼 `nccugrouping://auth/callback`，拒絕其他網址、重複 code、URL fragment token；取消登入不交換 token。
- Web 原本的 `http://localhost:8081/auth/callback` 保留。Google Cloud Web OAuth 的 Supabase provider callback 不需要改成 App scheme。
- 原生登入預設未啟用，後端回傳 `google_ios_not_configured`。需先完成下節設定再啟用。
- 原生 session 沿用 SecureStore；登入交換與錯誤不會把 code 或 proof 寫入資料庫。
- 前端 `app.config.js` 在 production EAS profile 或 `CORE_RELEASE_CHECK=true` 時檢查正式 API，拒絕 demo 模式、HTTP、localhost、IP 位址及 ATS 全域放寬。一般 localhost 開發不受影響。
- `EXPO_PUBLIC_EAS_PROJECT_ID` 若提供，會驗證 UUID 並放入 Expo `extra.eas.projectId`。只有需要 EAS 時才必須設定；本機 Xcode archive 不依賴 EAS 專案。

## 現在可以自行執行的檢查

前端專案 CMD：

```cmd
npm run ios:preflight
npm run test:release-config
npm run typecheck
npm test -- --runInBand
```

`npm run ios:preflight:release` 在目前沒有可供手機使用的 HTTPS API 時 **應該失敗**。這是預期的未完成條件，不應移除檢查來通過。檢查只讀取本機檔案與當前程序的環境變數，不登入、不建置、不上傳，也不讀取或列印任何憑證。

## 原生 Google 回呼設定

由專案管理者到 **指定 Supabase 開發專案 → Authentication → URL Configuration → Redirect URLs** 加入單一精確網址：

```text
nccugrouping://auth/callback
```

保留原本 localhost 網址，不使用萬用字元。Google Cloud 的 Authorized redirect URI 仍為：

```text
https://axdwbfhrkbovgrbngrrh.supabase.co/auth/v1/callback
```

確認 Supabase allowlist 完成後，才在後端忽略於 Git 的 `.local/supabase-dev.env` 加入 `CORE_GOOGLE_IOS_ENABLED=true`，重啟 API。這個開關僅代表你已完成設定，不會自行修改 Supabase 或驗證實機登入。Expo Go 沒有本 App 的專屬 scheme，需原生開發版本。

## 連線限制：必須先解決再做 iPhone 全流程

目前 `run_supabase_core.py` 綁定 `127.0.0.1`，API 額外拒絕非 loopback 與代理標頭。iPhone 中的 `127.0.0.1` 指手機自己，不是 Windows；把網址改成 Windows IP 也仍會被目前的隔離設計阻擋。沒有修改這些保護。

可先在 **Mac 同時執行後端與 iOS Simulator**，以 localhost 測試基本 API。Mac 需自行準備 Python 環境與同一專案的 Publishable key，不需 MCP Token、service-role key 或資料庫密碼。不要複製 Windows `.venv`；依後端 `pyproject.toml` 建立 Mac 環境。Mac 若未安裝本機模型，手動需求表單與費用確認仍可用。

實體 iPhone 與異地 TestFlight 測試必須另有經授權的可達 API 方案。後續需準備 HTTPS API、伺服器端的環境與 CORS／代理／登入限流設計，或明確限定的安全裝置開發通道；不能只把目前 localhost adapter 監聽改為 `0.0.0.0` 就當正式後端。本輪不部署，因此尚未完成這一項。

Windows Ollama 也僅供 localhost API 使用。打包 App 不會把 3.3 GB 模型或筆電算力搬到手機；沒有可達的 AI 後端時，TestFlight 使用者不能使用這台筆電的自然語言功能。Apple Foundation Models 的 Swift 原生整合尚未實作，不能宣稱已使用 Apple 裝置模型。

## Mac 建置與上傳（本輪未執行）

圖片發布另外需要後端私人 `.local/media-signing.env`，並與開發資料庫的圖片驗證金鑰一致；這不是 Supabase 管理金鑰，也不能放入 EXPO_PUBLIC、App bundle 或 Git。未安全設定此金鑰時，圖片 API 會停用，純文字拼單保留。iPhone 照片權限、裁切、HEIC 轉 JPEG、選圖取消與上傳中切換頁面仍須實機驗證；目前只驗證瀏覽器選取合成 PNG 與 iOS JavaScript bundle。

依 [Expo SDK 57 官方支援表](https://docs.expo.dev/versions/v57.0.0/)，需 Xcode 26.4+，App 支援 iOS 16.4+。先更新／安裝 Mac 的 Xcode 並完成團隊登入；不要把 Apple 密碼、`.p8`、`.p12` 或 provisioning profile 貼到對話或 Git。

1. 取得目前兩個分支的程式變更，安裝前端鎖檔指定的依賴。先完成 API 與 Google 回呼設定。
2. 按 [Expo 本機原生開發文件](https://docs.expo.dev/guides/local-app-development/) 在 Mac 執行 `npx expo run:ios` 測 Simulator；實機選擇裝置與正確簽章 Team。這些步驟會產生被 Git 忽略的 `ios/`，不要在 Windows 假造原生建置成功。
3. 實機驗證 Google 登入／取消／重啟續期、兩帳號拼單審核、退出、聊天室、偷揪同版全員確認，並驗證斷線重試。API 與原生權限需在實機驗證。
4. 正式建置前設定 `EXPO_PUBLIC_CORE_API_ENABLED=true`、`EXPO_PUBLIC_CORE_DATA_MODE=supabase` 與真正的 `EXPO_PUBLIC_API_BASE_URL=https://你的API網域/api/v1`。此處是說明佔位字串，不能直接拿來連線。
5. 先跑 release preflight，再於 Xcode 選正確 Bundle ID `com.nccugrouping.nccuGrouping`、Apple Team、唯一 build number，Archive 後經 Organizer 驗證並上傳 App Store Connect。已有 Mac 時可走本機 Xcode，不必使用付費建置服務。
6. App Store Connect 建立對應 App 記錄，完成測試資訊及 export compliance 問題，再設內部測試群組。外部測試還有 Beta App Review；程式匯出成功不保證審查通過。詳見 [Apple TestFlight 說明](https://developer.apple.com/help/app-store-connect/test-a-beta-version/testflight-overview/)。

如日後選 EAS，需先綁定 Expo 專案並自行確認當時免費額度；`eas.json` 已有 production profile，但本輪沒有 `eas init`、build、submit 或任何額度消耗。

## 公開測試前仍需補的產品工作

單張拼單圖片已串接；語音上傳與遠端推播未串接；圖示仍是 Expo 預設素材。回報已可儲存，不等於已有管理者處理流程。雲端跨帳號封鎖與 App 內刪除帳號已串接，仍需實機驗收；內容過濾、管理回報與可聯繫的營運資訊仍需實作／確認。Apple 對 UGC 與帳號刪除有明確要求，見 [App Review 1.2](https://developer.apple.com/app-store/review/guidelines/#user-generated-content) 與 [帳號刪除指引](https://developer.apple.com/help/app-review/guideline-reference/5-1-1-account-deletion)。使用 Google 等第三方主帳號登入，也需對照 [4.8 Login Services](https://developer.apple.com/app-store/review/guidelines/#login-services) 評估提供符合條件的登入選項，通常會採用 Sign in with Apple。

這些是送測準備缺口，不能把目前離線檢查通過寫成已完成 TestFlight。

本輪 Windows 驗證：後端既有與新增測試共347項（完整回歸345項後再通過2項readiness端點測試）、前端120項、Apple profile 2項、release設定4項、三種profile原生權限解析、TypeScript／ESLint／Ruff及iOS離線JS匯出通過。Mac／iPhone的21項驗收仍全部標示未執行。沒有改Supabase資料或網路／系統信任設定。
