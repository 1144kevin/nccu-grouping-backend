# iPhone 與 TestFlight：目前完成範圍與接續步驟

目前仍是 **Windows 本機程式 + 指定 Supabase 開發專案**。沒有部署 API、開放 LAN、防火牆例外、EAS 雲端建置或上傳。Windows 可以驗證程式與 iOS JavaScript bundle，不能代替 Mac 的 Xcode 簽章或 iPhone 實測。

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

目前圖片、語音上傳與遠端推播未串接；圖示仍是 Expo 預設素材。回報已可儲存，不等於已有管理者處理流程。帳號刪除、真正的跨帳號封鎖、內容過濾、管理回報與可聯繫的營運資訊仍需實作／確認。Apple 對 UGC 與帳號刪除有明確要求，見 [App Review 1.2](https://developer.apple.com/app-store/review/guidelines/#user-generated-content) 與 [帳號刪除指引](https://developer.apple.com/help/app-review/guideline-reference/5-1-1-account-deletion)。使用 Google 等第三方主帳號登入，也需對照 [4.8 Login Services](https://developer.apple.com/app-store/review/guidelines/#login-services) 評估提供符合條件的登入選項，通常會採用 Sign in with Apple。

這些是送測準備缺口，不能把目前離線檢查通過寫成已完成 TestFlight。
