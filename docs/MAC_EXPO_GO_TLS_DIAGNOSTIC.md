# Mac Codex：接續 Expo Go 私人 HTTPS 憑證問題

2026-10-22。目標仍是安全連上原 Windows HTTPS API；目前**未接通 Expo Go**。不部署、不使用付費服務、不 push。不能改成 HTTP 傳帳密、接受所有憑證、修改已安裝 Expo Go 的簽章或放寬整段網路。

## 已驗證的事實

- Windows `172.20.10.2:8443`，本人熱點 iPhone `172.20.10.1`；精確 Private firewall，既有業務 API／Supabase 保留。
- 本人確認 iPhone Safari `/health` 無警告且顯示 JSON；Expo Go 的無帳密原生 XHR health 則回 `The certificate for this server is invalid ... 172.20.10.2`。
- 本人確認安裝的根 CA 序號與 Windows 相符，已開完整信任；手機日期2026-10-22正確。
- 真正服務送出的 leaf fingerprint 與檔案相符，IP SAN、EKU、SHA256、有效期與key匹配；OpenSSL系統驗證在指定CA下通過。
- 已改送 leaf + 相同root，共2張公開憑證；TLS1.3實際驗證通過，但Expo Go仍拒絕。不是中繼CA缺檔已修好的結論。
- 完整關閉Expo Go、iPhone重開機、`--no-dev --minify`對照均仍回相同錯誤。
- 原生偵錯CDP可發現裝置、建立socket，但Runtime請求無回覆。不要寫成已經讀到SecTrust底層原因。
- 前端分段檢查與原生錯誤訊息已具備；既有功能、登入資料、全員確認機制未放寬。Windows 526後端測試及App199測試通過，不能代替手機驗收。

## 先做離線 Apple 信任評估

Windows 已建立只含公開資料的 `apple-tls-diagnostic.zip`。解壓後包含 `ca.cer`、`server.cer`、`tls-facts.json`、Swift程式及本文件；不含 `.env`、Token或私鑰。核對 facts 中 SHA256 與 Windows `device_link.py check`，勿使用來路不明的替換檔。

Mac已具備Xcode／Swift時執行：

```sh
cd PATH_TO/apple-tls-diagnostic
swift check_apple_device_tls.swift .
```

程式離線使用 Apple Security 的SSL hostname policy、指定這張已核對的CA、禁用網路補抓，檢查正確IP應通過、錯誤IP必須拒絕。**不安裝系統信任、不存取鑰匙圈、不發網路請求、不修改App驗證政策**。在Windows只能檢查匯出內容，Swift尚未在Mac編譯或執行；若有SDK語法錯誤，可在Mac修正，不得改成忽略驗證來讓測試通過。

若正確IP失敗：保存Apple錯誤domain/code/description，依具體憑證規則修正Windows簽發或配置。若正確IP通過且錯誤IP拒絕：只證明Apple在明確信任這張CA時可驗證，**不證明iPhone系統信任或Expo Go已成功**。

## 再讀 iPhone 原生錯誤

USB接已解鎖且信任此Mac的iPhone；由本人處理信任提示。使用macOS Console選該裝置，聚焦Expo Go `host.exp.Exponent`，讓本人只按無帳密的「檢查目前測試連線」。找NSURLSession／CFNetwork／SecTrust對應時刻的 `NSURLErrorDomain -1202` 與底層OSStatus，記錄僅這個私人health網址的錯誤與公開憑證資訊。

避免匯出全機紀錄、其他App資料、帳密或Token；不測登入以取得TLS錯誤。不要打開全域debug、關閉TLS或重複換CA猜測。

可再用既有正常原生App（依 `MAC_CONNECT_WINDOWS.md`、trial=false）做同一無帳密健康檢查，區分Expo Go與一般原生App的差異；須明確記錄不同build，不把原生App成功當成Expo Go成功。

最後留下具體錯誤原因、修正、實機回歸結果；仍無法定位則如實保留阻塞，不繞過驗證。所有改動依功能commit、不要push。
