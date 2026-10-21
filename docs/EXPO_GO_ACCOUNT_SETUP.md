# 電腦 Google 登入設定 App 密碼，手機信箱驗證回跳

適用目前 Windows 私人熱點、Supabase 開發專案 `axdwbfhrkbovgrbngrrh`。不部署、不使用付費模型、不 push。原 Google 帳號、拼單與聊天室保留。

## 先儲存 Supabase URL Configuration

到開發專案 Authentication → URL Configuration：

| 欄位 | 值 |
| --- | --- |
| Site URL | `https://172.20.10.2:8443/api/v1/auth/email-confirmed` |
| Redirect URLs 新增 | `https://172.20.10.2:8443/api/v1/auth/email-confirmed` |
| Redirect URLs 新增 | `http://localhost:8082/auth/callback` |

保留既有 Web `http://localhost:8081/auth/callback`、原生 `nccugrouping://auth/callback` 及其他已使用的精確回跳。不要加萬用字元，也不要關閉信箱驗證。電腦 Google 使用明確 callback，因此 Site URL 改為手機驗證頁不應改變既有 Google 的回跳。

**目前雲端設定尚待本人儲存／確認**：管理 API GET config/auth 回403；電腦操作工具無 Chrome 授權，未修改雲端設定，不能把程式完成當作 allowlist 已生效。

## 原本只用 Google 的帳號

1. 電腦開啟 [帳號入口](http://localhost:8082/sign-in)，按「使用 Google 帳號登入」，本人完成 Google 登入。
2. 新的電腦帳號入口會回到「設定 App 密碼」。若已在首頁，開 [密碼設定](http://localhost:8082/app-password)，或「帳號與隱私 → 設定 App 密碼（Expo Go 登入）」。
3. 核對畫面上的信箱，輸入兩次 10–128 字的 App 專用密碼，再按「確認設定 App 密碼」。不要用 Google 密碼，也不要把密碼傳到聊天或文件。
4. 顯示設定成功後，在 iPhone Expo Go 的「登入」頁輸入同一信箱與此 App 密碼。不要按註冊。已驗證的 Google 帳號無須再次收驗證信。

這是用本人已驗證 session 呼叫 Supabase 的使用者更新接口，不使用 service_role、管理員改密碼或 SQL 密碼修改。API 拒絕匿名、失效、未驗證及待刪除帳號；`expected_user_id` 必須等於 session 的本人，用來拒絕切換帳號後的延遲請求。成功後清空密碼輸入，不把密碼放進 URL、持久儲存或回應。

若 Auth 要求重新驗證，先登出，再用 Google 登入後設定；不略過 Supabase 密碼政策。設定畫面也能更新既有 App 密碼，操作前會明確說明。

## 新信箱註冊與 iPhone 驗證

裝置 API 註冊會明確送 `redirect_to=https://目前WindowsIP:8443/api/v1/auth/email-confirmed` 給 Supabase。電腦 API 在存在裝置 profile 時也使用同一回跳；客戶端不能指定任意 redirect。

1. 使用能收信的新測試信箱註冊。
2. 在 iPhone 用最新驗證信開啟連結。Supabase 先處理驗證，再回到 Windows HTTPS 網頁。
3. 網頁會清除 URL 中的 query／fragment，不保存回傳的 session，不發第三方請求，也不會自動登入 Expo Go。畫面提示回 Expo Go 用信箱與 App 密碼登入。
4. 失效連結顯示通用錯誤，不回顯信箱、Token 或上游錯誤本文。最終是否已驗證，以 Supabase 登入結果為準。

舊信件若已嵌入 localhost redirect，不能靠改 App 回寫舊信件。儲存新 allowlist 後使用新驗證信；已完成驗證者直接登入。同信箱重複註冊不會替 Google 帳號新增密碼，也不保證重寄信。

HTTPS 網頁仍只允許既定 iPhone 私人 IP，要求原 CA 完整信任。Windows 和 iPhone 要維持同一私人網路；換 IP 時重新準備裝置 profile、憑證及精確回跳。`localhost:8082` 只給電腦開，不要在手機開。

## 之後重新啟動

既有 Expo Go 流程不變。額外兩個 CMD 視窗提供電腦入口：

```cmd
cd /d C:\Users\aup20\projects\nccu-grouping-backend
.venv\Scripts\python.exe scripts\run_supabase_core.py
```

```cmd
cd /d C:\Users\aup20\projects\nccu-grouping-app
npm run dev:account
```

電腦 API 只綁 `127.0.0.1:8000`，Web 用 localhost8082；iPhone 維持 HTTPS8443、Expo Go8081，不開新防火牆或 tunnel。此入口的 `EXPO_PUBLIC_ACCOUNT_WEB=true` 只在該啟動程序使用；一般 Web、Expo Go、原生 iOS callback 各自保留原行為。

## 驗收與目前界線

- 後端認證、裝置、OAuth 與雲端邊界 118 項回歸通過；前端完整202項通過，新增密碼重試及 OAuth 選定測試7項通過。TypeScript與變更檔 lint 通過。
- 全新臨時瀏覽器、攔截 Auth 的隔離畫面驗證通過：設定密碼僅送出一次、清空輸入、驗證頁成功／失敗結果、清除 URL fragment、不使用 local/session storage。找到同頁新 fragment 未更新結果的問題並修復。真實 Auth 寫入0次。
- 本機 HTTP 健康檢查、驗證頁及電腦 Web 登入頁回200；手機 TLS 與健康檢查已由本人先前回報連通。
- 尚待本人實際儲存 Supabase allowlist、Google 登入與設定自己的密碼，再用 iPhone 登入。測試程式不替本人登入或設定真實密碼，也沒有發送新的驗證信。

依據：[Supabase Redirect URLs](https://supabase.com/docs/guides/auth/redirect-urls)、[OAuth 帳號加入密碼](https://supabase.com/docs/guides/auth/auth-identity-linking)、[更新已登入使用者](https://supabase.com/docs/reference/javascript/auth-updateuser)。
