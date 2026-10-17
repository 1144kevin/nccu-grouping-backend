# Google 登入設定：本機 Web + Supabase

本輪先支援 `http://localhost:8081` 的瀏覽器版。Google 驗證身分，Supabase 發出 session，本機 API 驗證後沿用既有個人資料與拼單。Google Client Secret 只存 Supabase Provider 設定，不放 App、不貼聊天、不寫本機原始碼。原有信箱登入與離線模式保留。

## 1. 建立 Google Cloud 專案

1. 開啟 https://console.cloud.google.com/ ，登入負責此 App 的 Google 帳號。
2. 上方專案選單 → New project／新增專案，名稱可用 `nccu-grouping-dev`。
3. 建立後切換到這個專案，搜尋並開啟 **Google Auth Platform**。舊介面可能顯示在 **APIs & Services → OAuth consent screen／Credentials**。
4. 如看到 Get started，開始設定 App 資訊。App name 填「政缺一咖」，User support email 與 Developer contact email 填可收信的團隊聯絡信箱。

本次只需要 OAuth 登入設定，不需建立運算主機、啟用 AI API 或購買其他服務。

## 2. 設定 Audience 與資料範圍

1. Audience 選 **External**，讓團隊外 Google 帳號將來也能使用。
2. 開發期間保留 **Testing**，在 **Test users** 加入你與隊友用來測試的 Google 信箱；不要誤選僅限組織內部的 Internal。
3. Data Access 只使用 `openid`、Email、Profile 的基本範圍。不要增加 Gmail 郵件內容、聯絡人、雲端硬碟等權限。
4. 此階段不用進行品牌驗證或選擇正式發布；非必填的 App 網站資訊先留空，不要填入自己沒有管理權的網域。

## 3. 建立 OAuth Client

在 **Clients → Create client**（舊介面：Credentials → Create credentials → OAuth client ID）：

- Application type：**Web application**。
- Name：`nccu-grouping-local-web`。
- Authorized JavaScript origins：

```text
http://localhost:8081
```

- Authorized redirect URIs：

```text
https://axdwbfhrkbovgrbngrrh.supabase.co/auth/v1/callback
```

建立後會取得 **Client ID**（通常以 `.apps.googleusercontent.com` 結尾）與 **Client Secret**。這裡不是填本機 App 的 `/auth/callback`，因為 Google 先回到 Supabase。

## 4. 設定 Supabase Google Provider

開啟開發專案 https://supabase.com/dashboard/project/axdwbfhrkbovgrbngrrh ：

1. Authentication → Sign In / Providers → Google（介面可能簡稱 Providers）。
2. 啟用 Google Provider。
3. 將 Google 的 Client ID 與 Client Secret 直接填入對應欄位並儲存。不要啟用 Skip nonce checks 等降低驗證的選項。
4. 頁面上的 Callback URL 應與上一節 Google redirect URI 完全相同。

接著到 **Authentication → URL Configuration**：

- Site URL：

```text
http://localhost:8081
```

- Redirect URLs 新增：

```text
http://localhost:8081/auth/callback
```

這一段才是 Supabase 回到本機 App。只加需要的精確網址，不使用廣泛萬用字元。本版本請使用 `localhost`，不要混用 `127.0.0.1` 開前端，兩者的瀏覽器 sessionStorage 不共用。

## 5. 需要回報的資訊

完成後回覆「Google Provider 已啟用、URL 已設定、測試帳號已加入」即可。Client ID 可以提供作核對，這個 Web 流程的本機程式不需要保存它。**不要提供 Client Secret、Google 密碼、Supabase secret/service-role key。** 若遇到錯誤，提供錯誤碼或遮住 Secret 的畫面即可。

## 6. 啟動與測試

後端如果正在執行舊程式，先在原視窗按 Ctrl+C，再啟動：

```cmd
cd /d C:\Users\aup20\projects\nccu-grouping-backend
.venv\Scripts\python.exe scripts\run_supabase_core.py
```

前端：

```cmd
cd /d C:\Users\aup20\projects\nccu-grouping-app
npm run dev:supabase
```

1. 開啟 `http://localhost:8081`，按「使用 Google 帳號登入」。
2. 選擇你自己的 Google 測試帳號並自行完成 Google 的登入與授權。
3. 應回到 `/auth/callback`，再進入首頁。Google 帳號首次使用會建立 App 帳號，不需另設 App 密碼或另外寄註冊驗證信。
4. 在個人檔案確認信箱，修改顯示名稱，再發起拼單。另一個允許的 Google 帳號可透過無痕視窗測試申請／核准。
5. 重新整理保留登入；登出應回到登入頁。

常見問題：

- 「Google 登入尚未啟用」：檢查 Supabase Google Provider 是否真的儲存並啟用。
- `redirect_uri_mismatch`：檢查 **Google Cloud** 的 redirect URI 是否為完整 Supabase callback URL。
- Google 顯示拒絕存取：檢查 Audience、Testing／Test users；以 Google 畫面的實際錯誤為準。
- 回到首頁仍未登入：檢查 **Supabase** Redirect URLs 是否包含完整 `http://localhost:8081/auth/callback`。
- 登入已失效：重新按 Google 登入，不重用舊回呼連結。後端重啟會清除暫存流程；授權碼只能用一次且有短效期。

## 程式與驗證界線

- 本機 API：`POST /api/v1/auth/google/start`、`POST /api/v1/auth/google/finish`。
- 使用 SHA-256 PKCE；伺服器只在記憶體保留短效 proof，頁籤 sessionStorage 保留一次性 flow ID，回呼不接受 URL 裡的 access token。
- 回呼開始即移除網址中的 code／錯誤參數；只有 Supabase 驗證完成的 session 能進入既有拼單流程。
- 頁面已處理未設定 Provider、取消、缺少 proof、過期與重複回呼。測試使用 mock，不替使用者登入 Google 或同意授權。
- 實際 Google 授權端到端測試仍需完成上述 Provider 設定並由帳號擁有人操作；不把 mock 通過當成 Google 真實登入已完成。
- 已加入 refresh token 自動續期。更新前登入的頁籤需自行登出並重新登入一次以保存續期憑證；暫時網路問題不清除憑證。詳見 [開發模式與示範資料](CORE_SUPABASE_DEV.md)。
- 尚未完成 iPhone 原生／系統瀏覽器回跳。iPhone 按鈕先停用並明示限制；後續需 Mac development build、App callback 設定與裝置到本機 API 的連線。
- Google 首次登入若未提供目前使用的 display_name 欄位，App 先顯示預設名稱「新朋友」，可在個人資料中修改。

官方參考：

- https://supabase.com/docs/guides/auth/social-login/auth-google
- https://supabase.com/docs/guides/auth/sessions/pkce-flow
- https://developers.google.com/identity/protocols/oauth2/web-server
