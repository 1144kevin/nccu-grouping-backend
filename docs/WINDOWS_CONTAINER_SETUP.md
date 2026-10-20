# Windows 本機 Linux 容器：已安裝與驗收

2026-10-20 經使用者授權，已安裝 WSL 3.0.3.0（預設 WSL2）及 Docker Desktop 4.96.0，Engine 29.9.1。Windows build 26300.9711；硬體虛擬化、VirtualMachinePlatform 與 Hypervisor 均可用。安裝程式結束碼皆為0，Linux 引擎實際啟動成功，**本次不需要先重開機，也沒有執行重開機**。

WSL 使用 Microsoft `wsl --install --no-distribution --web-download`，沒有額外安裝 Ubuntu 或建立 Linux 登入帳號。Docker 官方安裝檔下載後驗證 Authenticode 為 Valid、簽署者 Docker Inc，使用 `--user --backend=wsl-2 --no-windows-containers` 安裝至目前使用者，不加入 docker-users 群組。安裝檔與紀錄只存 ignored `.local/installers`，不隨 Git 或 Mac bundle 傳送。

官方說明：[WSL 指令](https://learn.microsoft.com/en-us/windows/wsl/basic-commands)、[Docker Windows 安裝及免費教育使用條件](https://docs.docker.com/desktop/setup/install/windows-install/)。沒有註冊付費方案或部署。

## 之後如何重跑

先從開始功能表開啟 Docker Desktop，等引擎就緒。在後端目錄開啟新的 PowerShell，執行：

```powershell
wsl --version
docker --context default info --format '{{.OSType}}'
.venv\Scripts\python.exe -m scripts.rehearse_container
.venv\Scripts\python.exe -m scripts.rehearse_container --build
```

第二行應為 `linux`。第一個 Python 指令只檢查，第二個會從官方公開 registry 取得建置所需的 image／依賴，建立本機 image，再離線測試並清掉本輪唯一命名的容器與 image。沒有上傳原始碼、發布埠或部署；基底映像與建置快取可能留在 Docker，工具不會 prune 其他資源。

若舊終端機找不到 docker，先重開終端機；或僅在該 PowerShell 視窗補上安裝位置：

```powershell
$env:Path = "$env:LOCALAPPDATA\Programs\DockerDesktop\resources\bin;" + $env:Path
```

不要改成遠端 daemon／TCP socket，也不用提供 Supabase 密碼、MCP Token 或其他真實金鑰。容器只收到腳本內明列的合成設定，`--network none` 阻止對 Supabase 或模型發出請求。

## 本次實際驗收

- Linux x86_64、Python 3.13.16，依 uv.lock 建置成功；asyncpg、Pillow、cryptography、pydantic-core、uvloop、httptools 原生模組可載入。
- UID10001、唯讀根目錄、無 capabilities、no-new-privileges、512MiB／1CPU／128程序限制、無 host mount、無對外埠、無網路。
- ASGI 及真正 Dockerfile 預設啟動指令均驗證 health200、未登入貼文401；Docker HEALTHCHECK healthy。
- SIGTERM 正常關閉，exit0，沒有 OOM；測試容器與唯一標籤 image 已清除。
- 後端完整離線回歸498項通過，兩項既有依賴警告；App 未修改。

已提交的無憑證結果：`docs/evidence/linux-container-20261020.json`。重跑結果寫 `.local/container-rehearsal.json`；指令失敗輸出留 `.local/container-command-failure.log`，不要未檢閱就公開這個私有診斷檔。

目前驗收是 **Windows 上的 Linux amd64 本機容器**；不代表 Mac M1 ARM64、iPhone、公開 HTTPS 代理、真人登入或正式雲端部署已驗收。後續 [映像弱點掃描](IMAGE_VULNERABILITY_SCAN.md) 與 [合成本機還原](POSTGRES_RESTORE_REHEARSAL.md) 已完成；仍有未修補 Debian High，完整 Supabase Auth／Storage 災難復原與更長時間容量測試仍待另做。Docker／WSL 安裝是這台電腦的環境，不會隨 commit 自動安裝到 Mac。
