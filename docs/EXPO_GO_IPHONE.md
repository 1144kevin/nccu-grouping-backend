# Windows + iPhone Expo Go 測試

2026-10-22。先測信箱登入、拼單、文字聊天與偷揪；不需要 Mac 重建。帳號與資料仍在既有 Supabase 開發專案，API 與免費模型在 Windows。沒有部署、EAS build、付費推論或 push。

## 第一次操作

1. Windows 保持連著本人 iPhone 熱點／可信私人 Wi-Fi；目前 API 是 `https://172.20.10.2:8443/api/v1`，只允許手機 `172.20.10.1`。換 IP 後先重新設定，不沿用舊 QR。Windows 不能休眠。
2. 電腦 CMD 在 App 目錄執行 `npx expo login`，iPhone Expo Go 登入相同免費 Expo 帳號。本輪已由本人完成、電腦 whoami 已確認；帳號密碼不放 env／文件／聊天。
3. 使用支援本專案 SDK57 的 Expo Go。若提示 SDK 不相容，先核對 App Store 可用版本；不能為了掃碼任意降級專案依賴，亦不能宣稱所有 Expo Go 版本都能執行。
4. 啟動下列 API、模型、Expo Go。這次若已由代理啟動，不要再啟第二份；看 terminal／連接埠後再決定是否需重啟。
5. 若 iPhone 尚未信任這次開發 CA，用 Safari 開啟終端機列出的 `http://172.20.10.2:8081/device-ca.cer` 下載**單一公開 CA**。先核對 Windows `device_link.py check` 的 SHA256，與下載憑證的詳細資訊比對；再由本人到「設定 → 一般 → VPN與裝置管理」完成安裝，在「設定 → 一般 → 關於本機 → 憑證信任設定」啟用該 CA。不要安裝來源不明憑證、server 私鑰或略過警告。這個 HTTP 入口只在私人網路提供公開 CA／開發 JS，帳密與業務資料一律走另個 HTTPS API。
6. Safari 開 `https://172.20.10.2:8443/health`：無憑證警告且回200才繼續。403是裝置IP名單／來源不符；拒絕信任或連線失敗時先處理設定，不停用 TLS。
7. iPhone 相機掃終端機 QR；或在 Expo Go 輸入 `exp://172.20.10.2:8081`（依版本提供的入口）。允許 Expo Go 的本機網路權限。應看到「Expo Go 測試」及信箱登入；使用你原本的信箱與 App 專用密碼，不能使用 Google 帳號密碼。

## 每次啟動（CMD，三個終端機）

後端：

```cmd
cd /d C:\Users\aup20\projects\nccu-grouping-backend
.venv\Scripts\python.exe scripts\device_link.py check
.venv\Scripts\python.exe scripts\run_device_core.py --serve
```

Windows 模型（已安裝，沒有下載）：

```cmd
cd /d C:\Users\aup20\projects\nccu-grouping-backend
.venv\Scripts\python.exe scripts\run_device_models.py --serve
```

Expo Go：

```cmd
cd /d C:\Users\aup20\projects\nccu-grouping-app
npm run expo-go:check
npm run dev:expo-go
```

不要用 `npm run dev:supabase` 掃手機：那個指令是 localhost Web 版。不要改 API 為手機的 localhost，也不要使用 tunnel。新指令不寫入／覆蓋原生 Apple profile，清掉殘留 trial、Expo public env 與 dotenv 污染，維持 Supabase 與 HTTPS。

## 防火牆與服務範圍

API 仍是 Private、精確 host/client、TCP8443。新增 Metro 規則也只允許同一裝置TCP8081；Metro本身綁 localhost18081，對外中繼只綁該私人 Windows IP，HTTP與WebSocket都有精確來源限制。沒開整個子網路、模型11434、公網介面或路由器轉發。中繼最多32個socket，停止launcher時關閉自己的連線與Metro。

初次需要管理員權限建立規則；完成測試後可移除**這條**規則，保留其他服務：

```cmd
cd /d C:\Users\aup20\projects\nccu-grouping-backend
.venv\Scripts\python.exe scripts\device_link.py expo-firewall-check
.venv\Scripts\python.exe scripts\device_link.py expo-firewall-add
rem 測完在管理員終端機移除：
.venv\Scripts\python.exe scripts\device_link.py expo-firewall-remove
```

add 不覆寫既有同名規則；移除不要求憑證仍有效。API規則的增刪仍使用原 `firewall-*` 指令。換網路依 [Windows連線文件](WINDOWS_IPHONE_CONNECTION.md) 重新簽發／設定。

## 驗收順序

| 項目 | 操作與通過條件 |
| --- | --- |
| 連線／登入 | 登入前按「檢查目前測試連線」；信箱登入、重開App恢復、登出切帳號；斷線錯誤保留可重試，不洩漏密碼 |
| 拼單 | A建未來拼單，B申請，A接受；B截止前退出釋出名額；滿團／截止拒絕加入 |
| 聊天 | 接受後可傳文字、重開讀取；切帳號不讀到舊帳號私人內容；退出後失去存取、取消後唯讀 |
| 偷揪 | 各帳號建立自己的分身及相容需求，可手填或使用 Windows 本機模型訪談；核對後親自儲存 |
| 語義配對 | 各人按「AI搜尋新配對機會」建立本人索引；查看理由與待確認事項；首次索引／CPU推論可能較慢 |
| 成團 | 修改費用／分工後全部重新同意，同版全員同意才建立拼單與聊天室，重試不重複建團 |
| 模型離線 | 保留輸入、可重試；手動需求與一般配對仍可用，沒有付費fallback |

至少需要兩個真實測試帳號才能驗全員確認；可以同一支手機依序登出切帳號。示範資料中的人物不會自動代為同意。既有全員確認及權限沒有放寬。

## Safari 成功但 Expo Go 仍失敗時

完整從多工畫面關閉 Expo Go，再掃 App QR，確保重新建立連線並載入最新 JS。「檢查目前測試連線」會顯示實際 API，先用不帶帳密／Cookie、不讀 SecureStore 的原生 XHR 檢查 `/health`（最多10秒），再檢查原有 API client 的 readiness。前者200、後者失敗時畫面會明確區分；HTTP403表示後端已收到但拒絕來源，`ERR_NETWORK`只代表尚無HTTP回應，不能單憑代碼判定是憑證。原生請求失敗時，另顯示僅來自無帳密 health 請求、最多300字的系統連線錯誤；不顯示任意HTTP回應本文。回報診斷代碼與「手機系統回報」即可，不需提供帳密或Token。檢查不刪登入資料、不修改TLS信任、不建立帳號。

2026-10-22 實機進度：本人已確認 iPhone Safari 的 HTTPS health 無警告且正常回 JSON。Expo Go 原生 XHR 明確回報「The certificate for this server is invalid」，不是登入／資料庫錯誤；本人確認已安裝 CA 序號與 Windows 相同、日期正確。服務補上同一個 CA 的完整鏈後，實際 TLS1.3 送出2張憑證並通過 Windows 驗證，手機仍回報相同錯誤。具體根因尚未確認，接續排除 iOS 系統信任快取／Expo Go 相容性；不能標示 Expo Go 已連通或全流程通過。沒有改成 HTTP API、忽略憑證驗證或放寬來源名單。

**Apple AI 保留在原生版**。Expo Go 自動使用 Windows 本機訪談與語義模型；Google 按鈕不顯示。Apple／Google原生回跳、HEIC與背景切換、推播／語音等仍以各功能原來限制為準。不能把電腦自動測試、manifest與iOS bundle產生成功當成手機實際驗收；記錄本人實測結果再標完成。

測完 Ctrl+C 停 Expo、API及模型；移除測試 CA 信任和這次的精確規則。未部署、不收費、不 push。

參考：[Expo SDK57](https://docs.expo.dev/versions/v57.0.0/)、[Expo Go啟動與同帳號登入](https://docs.expo.dev/get-started/start-developing/)、[Apple手動CA信任](https://support.apple.com/en-us/102390)。
