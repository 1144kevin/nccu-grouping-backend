# Windows 作為正常 iPhone App 的私人 HTTPS 後端

2026-10-22。Mac 接續文件：[MAC_CONNECT_WINDOWS.md](MAC_CONNECT_WINDOWS.md)。本輪只連原 Supabase 開發專案，不部署、不使用付費服務；既有loopback模式保留。

## 目前已設定

本人 iPhone 開個人熱點；Windows 位址及手機閘道已實際核對。`device_link.py check` 顯示的設定才是最新值。Windows僅此熱點介面設Private，防火牆規則 `NCCUGrouping-PrivateDevice-8443` 只允許精確 host／client IP及TCP8443；沒有開整個子網路、修改路由器或關閉防火牆。

Supabase Auth已經本人當下確認並透過dashboard保存精確native回跳 `nccugrouping://auth/callback`，保留Web回跳與Site URL。沒有改Google Client Secret／其他Provider、擴權MCP或更動既有資料。Management API因原權限403，沒有放寬權限；以dashboard核對結果為證。

## 日常啟動

以下每個服務用獨立終端機；保持 Windows不休眠、iPhone熱點開啟。不要安裝Windows PowerShell全域執行政策或停用防火牆。

```powershell
cd C:\Users\aup20\projects\nccu-grouping-backend
.venv\Scripts\python.exe scripts\device_link.py check
.venv\Scripts\python.exe scripts\run_device_core.py
.venv\Scripts\python.exe scripts\run_device_core.py --serve
```

檢查不建立listener；`--serve`才監聽精確私人IP／8443TLS，且拒絕變更後的IP、公網或Public分類。runner固定Supabase mode、單程序，載入現有圖片／管理設定，明確禁用paid/admin金鑰、cloud／SQLite adapter及shell殘留設定。

需要Windows語義模型時，另一個終端機：

```powershell
.venv\Scripts\python.exe scripts\run_device_models.py
.venv\Scripts\python.exe scripts\run_device_models.py --serve
```

只使用已安裝 `.local/ollama/runtime` 與 `.local/ollama/models`，不下載。明確NO_CLOUD／NOHISTORY、localhost11434、一筆推論／有限model queue。手機不能直連Ollama；Apple訪談本身不需要Windows推論，但現有跨帳號語義搜尋／判讀需要它。CPU模式可能較慢，沒有自動安裝顯示卡／NPU驅動。

```powershell
.venv\Scripts\python.exe scripts\device_link.py probe
```

probe驗證真實CA與hostname，不使用verify=false。Windows來源未列入手機名單，收到403是正常邊界；TLS成功不能宣稱iPhone已連通。iPhone須本人安裝公開CA並用Safari／正常App驗收。

## 第一次或換網路

優先使用本人可信私人Wi-Fi／個人熱點；不要直接在校園公網IP服務。先看 `device_link.py networks`，再以實際值執行，例如：

```powershell
.venv\Scripts\python.exe scripts\device_link.py prepare --host 192.168.1.10 --clients 192.168.1.20 --ai
```

此為範例，prepare會核對Windows目前是否真的有該host。建立新專用目錄並限制ACL，產生30天公開CA及7天server葉憑證；CA私鑰只在記憶體、永不存檔。私鑰留Windows；不自動安裝系統信任。

`prepare`不覆寫既有profile。IP變動／憑證到期時：先停止本輪服務，在管理員終端機移除本工具規則，妥善保留舊 `.local/device-test.env` 為ignored備份，再以新host/client重新prepare。新CA需重新交付並由本人在iPhone核對／信任；App URL改變也需Mac重建。不能用wildcard、忽略SAN或直接沿用舊URL。

確認dashboard精確回跳已存在後才執行（本次已完成）：

```powershell
.venv\Scripts\python.exe scripts\device_link.py confirm-google
```

這只記錄明確確認，不謊稱API自動驗證或Google登入已成功。

只有在可信私人網路、憑證有效後，於**管理員終端機**執行下列明確動作：

```powershell
.venv\Scripts\python.exe scripts\device_link.py firewall-check
.venv\Scripts\python.exe scripts\device_link.py firewall-add
# 完成測試或換IP後移除：
.venv\Scripts\python.exe scripts\device_link.py firewall-remove
```

既有同名規則不會被覆寫；其他規則／全域設定不動。iPhone名單最多8個精確private IPv4；新增第二支實機需核對其IP，編輯profile並重新建立本工具規則／重啟API，不可用CIDR開整段。

## 給Mac的檔案

`device_link.py export` 只準備 `.local/iphone-windows-link/ca.cer` 與 `device-connection.json`。本輪另加入Mac交接文件副本及公開ZIP；不搬server-key.pem、私有env、模型或Windows.venv。原始碼另用已提交Git bundle整合，bundle不含這些ignored檔。

Windows檢查覆蓋實際TLS正反例、錯誤SAN／key、public地址拒絕、環境污染、公開export無private內容、Google一次性PKCE、正常profile關閉trial、Personal Team所需權限解析及原有功能回歸。手機端登入、Safari信任、HEIC、背景切換與跨帳號全流程仍以實機紀錄為準。

測完以Ctrl+C停止自己開的API／模型終端機；不停止無關服務。若由本輪代理啟動，可先用 `Get-NetTCPConnection -LocalPort 8443,11434 -State Listen` 核對PID與程式路徑，再只停止本輪程序。最後移除本工具firewall規則及測試iPhone的開發CA信任。
