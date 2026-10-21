# 給 Mac Codex：正常 iPhone App 接 Windows 後端

更新：2026-10-22，Asia/Taipei。此文件可直接交給 Mac 上的 Codex 讀取並依序操作。

## 任務與限制

把目前 iPhone 上的「Apple 訪談試用版」重建為正常登入版本，接到 Windows 的既有 Supabase 開發後端，驗收登入、拼單、聊天與偷揪。既有功能及全員同版確認保留。不部署、不用 EAS build／submit、不上傳 TestFlight、不使用付費服務、不 push。先保留 Mac 未提交工作，不能直接覆蓋 checkout 或用 `git reset --hard`。

Mac 只負責前端來源整合、Xcode／Pods、簽章與安裝。**不要在 Mac 啟動後端、安裝 Ollama／Docker、下載模型或索取 Supabase 管理金鑰**。Apple 訪談在 iPhone；跨帳號語義搜尋／判讀在 Windows 的既有免費模型；帳號及資料仍用原 Supabase 開發專案。

## Windows 已處理

- 專用私有 HTTPS、短效葉憑證、精確 iPhone IP 名單、單一私人網路 TCP8443 防火牆規則。CA 私鑰只在簽發時存在記憶體，未存檔；server 私鑰只留 Windows。
- 受保護 API、現有單張圖片設定與語義模型設定；Ollama 僅 loopback11434，明確禁用 cloud、單筆推論與有限等待。
- 經本人當下確認，Supabase Auth 已加入精確的 `nccugrouping://auth/callback`，保留 `http://localhost:8081/auth/callback` 與 Site URL。Google Cloud 的 Web callback 維持 Supabase callback，無須改成手機 scheme。
- 正常 Apple profile 強制 `EXPO_PUBLIC_APPLE_INTAKE_TRIAL=false`，保留 Apple 訪談；私人 device build 移除尚未使用的 APNs entitlement，方便原 Personal Team 本機簽章。release 設定沒有因此放寬。

當次 Windows／iPhone 是本人 iPhone 個人熱點。實際網址與 CA fingerprint 以公開交接包的 `device-connection.json` 為準，不能沿用文件範例、校園140.119開頭的公網位址或舊 `.invalid` 試用網址。換網路／IP後先回 Windows 重做檢查，不能使用 `curl -k`、忽略憑證錯誤或放寬 ATS。

## 1. 搬哪些檔案

需要最新版 App 程式與相鄰 backend 的本文件；另從 Windows 搬 **`.local/iphone-windows-link/`** 的公開交接資料夾／ZIP。當中只有 `ca.cer`、`device-connection.json` 與本文件的副本。原始碼用最新 Git bundle／manifest 整合，不只搬舊 Probe zip 或被忽略的 ios/。

不要搬 `.local/device-test.env`、`.local/device-tls-*`、server-key.pem、任何 `.env`、MCP Token、media signing key、Windows `.venv` 或模型。Mac 和手機均不需要後端金鑰。

若 Mac 已有 checkout：先核對分支、HEAD 與未提交工作，從 Windows bundle fetch 指定分支，檢閱差異後 fast-forward；有 Mac 新工作時先妥善保留，不能強制覆寫。以交接 manifest 的分支／commit 為準，勿切換使用者分支或自行 push。

## 2. iPhone 安裝公開開發 CA（由本人操作）

1. Mac 執行 `shasum -a 256 PATH_TO_LINK/ca.cer`，比對 `device-connection.json` 的 `ca_sha256`，並與 Windows `device_link.py check` 的 fingerprint 核對。
2. 只把 `ca.cer` AirDrop 到本人測試 iPhone，依系統步驟安裝憑證描述檔。**不是 server.pem 或 server-key.pem。** 不透過公開 HTTP 下載伺服器傳檔。
3. 本人在「設定 → 一般 → 關於本機 → 憑證信任設定」對這個專用開發 CA 啟用完整信任。先核對來源及 fingerprint；不讓代理替本人略過系統安全提示。
4. iPhone Safari 開啟 manifest `api` 去掉 `/api/v1`、加上 `/health`，例如 `https://私人IP:8443/health`。沒有憑證警告且 HTTP200 才往下走。403代表裝置名單／來源不符；連線失敗先查 Windows 是否仍在相同熱點且 API 已啟動。

CA 只供這次私人測試；測完移除 iPhone 憑證描述檔／信任。葉憑證期限載於 manifest，過期或 IP 改變必須重新簽發並核對新 CA，不能停用驗證。Windows 自身非手機來源的 `probe` 可能收到403，不能拿它冒充手機已通過。

## 3. Mac 重建正常 App

先讀 App 根目錄 `AGENTS.md`，使用 Node24、完整 Xcode27+（目前模組使用 SDK27 API；之前已在27.1編譯）。不要啟動 Simulator；接 USB 與已信任且解鎖的 iPhone，選原 Personal Team。

```sh
cd PATH_TO_APP
npm ci
npm run apple:configure -- --connection PATH_TO_LINK/device-connection.json
npm run apple:check
npm run apple:prepare-device
npm run apple:device
```

`--connection` 會驗證 CA 檔 fingerprint、期限、私人 HTTPS URL、固定 callback 與公開欄位，將 profile 存進 Mac 自己的 ignored `.local/apple-dev.json`。不要另外手動 export 先前 trial=true 的環境；這組指令已明確覆蓋為正常模式。

`prepare-device` 在同一個 profile 環境跑 iOS prebuild（不 clean、不安裝 Pods），套用本機網路用途、嚴格 ATS 與不使用 APNs 的 device 設定；`device` 用 Release configuration 重建，內嵌 JS，不必依賴 Metro。若 Pods 需要更新，依現有 CocoaPods／Expo 流程處理；不要自行換 SDK 或加入商店推播能力。首次授權、選 Team 與系統提示由本人完成。

裝好後應看到登入頁，**不是「本機需求訪談」單頁**。若仍只有訪談，先核對內嵌 JS 的 trial=false／API URL 與實際安裝 build，不能宣稱後端出錯。Google 須使用此原生 build，Expo Go 不包含專屬回跳與 Apple 模組。

## 4. 按順序驗收

使用本人同意的專用測試帳號／隊友 Google 帳號，勿用保留的展示人物代替真人同意。Google OAuth consent 若仍是 Testing，新隊友信箱可能須由擁有者加入 test users，不能因此關閉驗證或改用管理 Token。

| 項目 | 實際操作與通過條件 |
| --- | --- |
| HTTPS／權限 | Safari health200；App允許本機網路後可連線。拒絕權限時提示可重試，不能悄悄登出 |
| Google | 成功、取消、回跳；關App重開續期；登出／切帳號不回到舊身分。登入期間勿重啟 Windows API，PKCE proof 是單程序10分鐘有效、一次性 |
| 拼單 | A發未來文字拼單，B申請，A接受；B截止前退出釋出名額；滿團／截止不能加入。需要兩人可兩支手機，或同機依序切帳號 |
| 聊天 | 接受後雙方可發訊息／重開讀取、未讀已讀正確；退出後失去存取，取消拼單後唯讀 |
| Apple訪談 | 摘要與補問出現；更正預算／撤回取餐意願後人工核對；採用只改標題，不自動更改費用、角色或儲存 |
| 偷揪 | 各人建立分身與本人確認的相容需求，儲存後配對；需要語義時各按「AI搜尋新配對機會」，首次對方未索引可等30秒再試 |
| 全員同意 | 修改費用／分工後全部重新同意；同版全員同意才建立拼單與聊天室。重試不重複建立；示範人不能代同意 |
| 異常 | 取消登入、背景返回、斷線重試、模型停止時手動表單仍可用；不改用付費 API |
| 圖片／安全 | 單張圖片與HEIC另驗；用隔離帳號驗封鎖／刪除，勿刪本人正式帳號或隊友資料 |

不需要先做 Apple640維向量遷移才能跑現有流程；它尚未與既有 Qwen512維混用。費用草案仍為既有免費規則規劃器，不宣稱模型自動談判或代本人承諾。

## 回報與停止點

記錄兩個 repo commit、build／裝置與OS、實際 URL、逐項通過／失敗／未執行及錯誤階段。更新 `docs/DEVICE_ACCEPTANCE.csv` 或另存本輪紀錄；截圖不要含 token、私人聊天或真實帳密。

Windows 已有524項後端、App189項回歸及9項profile／公開連線契約檢查通過，原生權限introspection三種profile通過；這些不代替本輪手機登入到成團驗收。先前手機 build 的安裝成功與 Apple摘要可見，也不能當作這個正常連線版已驗收。

到此停止，不上傳、不部署、不使用付費服務、不 push。發現問題可修正並依功能 commit；若需改其他雲端設定／換私人網路／換 callback，先說明範圍，不刪舊設定或直接開放整個子網路。

官方參考：[iPhone手動憑證信任](https://support.apple.com/en-us/102390)、[Supabase Google](https://supabase.com/docs/guides/auth/social-login/auth-google)、[Redirect URLs](https://supabase.com/docs/guides/auth/redirect-urls)。
