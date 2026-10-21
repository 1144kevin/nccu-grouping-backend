# Apple 原生 AI：獨立 iPhone 最小驗證

目標裝置：iPhone 17／iOS 26.6.1。這是獨立 SwiftUI App，不引用原 React Native App，
不安裝 npm／CocoaPods／Ollama，不啟動後端、不連 Supabase，不呼叫付費模型。
只使用 iOS 26 已提供的 SystemLanguageModel 與 Natural Language API，不要求升級 iOS 27。

**已完成真機測試：**見 [實際結果與Windows接續](../../docs/APPLE_AI_PROBE_RESULTS.md)。
兩輪需求抽取均未達整合門檻；句向量為640維，保留繁體原文9/11，現有512維欄位不能直接使用。
v1原始結果與v2欄位說明調整後結果都保留，沒有把後處理修正當成模型原始通過。

## 驗證範圍

- Foundation Models：6個合成案例、9輪原始抽取；缺項補充、預算更正、撤回取餐、
  整團總價、模糊時間、未知不是免費。逐輪重送已發生的使用者陳述，測跨輪整合；
  不依賴前輪生成的答案，不把模型輸出自動儲存成需求。
- 句向量：11個繁體中文查詢，每例3個候選，比較正確候選的cosine排序。
  分別測 `zh-Hant`／`zh-Hans` 的系統句向量，以及原繁體輸入／明示的繁轉簡前處理。
  不支援的設定記錄 unavailable，不靜默換語言，不混用空間，也不自行下載替代模型。
- 記錄每輪理解耗時、每組向量總耗時、實際維度、revision、查詢向量與原始錯誤。
  RAM欄位只是裝置實體記憶體總量，不是模型記憶體占用、峰值或最低需求。

案例來自專案既有回歸集與一組補問情境，並非盲測準確率或跨使用者成團率。
本機cosine測試不代表已驗證Supabase、跨手機向量一致性或全員同意流程。

## 先由本人完成環境

1. 開啟Xcode，完成條款與首次啟動設定；不需要下載Simulator來做這個測試。
2. Xcode Settings → Accounts登入Apple帳號。密碼／驗證碼由本人輸入。
3. 用傳輸線連接iPhone，解鎖、信任電腦，依提示啟用Developer Mode。
4. 手機啟用Apple Intelligence，等待系統模型準備好。程式會再次檢查availability。
5. Xcode開啟此目錄的 `AppleAIProbe.xcodeproj`，只在此probe target的Signing & Capabilities
   選自己的Team。沒有付費會員時可先嘗試Personal Team；以Xcode實際簽署結果為準。

`DEVELOPMENT_TEAM`預設空白，不猜測Team、不代替本人接受條款、不自動建憑證。
若改Team的本機專案設定，提交前移除Team識別碼；不要提交provisioning或帳號資料。

## 只編譯這個小專案

從後端根目錄執行。先做不簽署的iPhone編譯，僅一個build job，產物留在ignored `.local`：

```bash
xcodebuild -project tools/apple_ai_probe/AppleAIProbe.xcodeproj \
  -scheme AppleAIProbe -configuration Debug \
  -destination 'generic/platform=iOS' -jobs 1 \
  -derivedDataPath .local/apple-ai-probe-build \
  CODE_SIGNING_ALLOWED=NO build
```

這不是手機安裝成功。簽章仍需本人先選Team，再於Xcode選接上的iPhone按Run。
不要選原App工程、不要建Simulator、不要Archive或上傳。
若從命令列建置已選Team的probe，也把DerivedData放到上面的ignored目錄。

## 手機操作與取回報告

1. 開啟 **Apple AI Probe**，確認畫面是「實體裝置」，檢查模型availability。
2. 點「1. 測繁體中文句向量」，等待完成。
3. 點「2. 測需求理解與跨輪更正」，保持App前景，等待9輪結果。
4. 點「匯出JSON報告」，用AirDrop／檔案等私人方式存到後端
   `.local/apple-ai-probe-report.json`。可從Xcode下載此App container的Documents取回。
5. 完成後可在飛航模式重跑，獨立確認裝置模型可離線使用；每次匯出另存檔名。

一般啟動不會自動測試；明確以 `--run-fixtures` 啟動時，才會依序執行固定句向量及理解案例。
這個開發參數不接受真人輸入，也不執行帳號或資料庫操作。
離開前景會要求取消，已完成項目會寫入App自己的Documents。
Share sheet可能使App變非active，因此匯出按鈕只在測試結束後啟用。
若取消時模型尚未釋放，等當前呼叫結束後才可再測；不並行載入多份模型。
所有JSON只含合成案例結果，未要求真人對話或帳號。

## Windows／Mac離線分析

Mac：

```bash
.venv/bin/python tools/apple_ai_probe/analyze.py .local/apple-ai-probe-report.json \
  --output .local/apple-ai-probe-analysis.json
```

Windows將Python改成 `.venv\Scripts\python.exe`，其餘參數相同。
只使用既有Python依賴，沒有網路／資料庫操作。

分析分開列出原始模型錯誤和套用現有grounding後的結果，防止程式修正掩蓋模型錯誤。
不完整、checksum不同、非法向量、未支援模型、Simulator結果不能當成真機通過。
初步進入App整合的門檻：6個案例原始及核對後都通過，至少一組512維向量設定達9/11；
這是本probe的開發門檻，不是產品準確率保證。失敗時先分析具體案例，不自動改題重跑到通過。
尚須驗證多台同revision手機產生的空間一致、向量與需求版本綁定、舊模型隔離及門檻校準。

## 接著移回Windows的工作

取得真機報告後，Windows可完成：

- App AI provider介面、Web測試替身、模型缺席／取消／帳號切換狀態與元件測試。
- 拆分後端模型抽取與grounding，處理App草稿的版本、資料格式和權限驗證。
- 受限候選摘要／版本核對、Apple候選判讀結果核對、原成團交易回歸。
- 本人需求向量提交／搜尋API、模型revision與前處理識別、正規化和大小限制。
- 若維度符合512，評估沿用現有pgvector欄位；規劃每需求單向量主鍵的切換／並存方式，
  不混算舊Qwen向量、不讓客戶端聲稱的model_id取得任何權限。
- 完整測試、資料庫回滾交易驗證、Docker／掃描／還原、Windows HTTPS測試入口。

Mac只留Apple SDK原生編譯與簽章／安裝。實際AI品質、速度、HEIC與iOS行為由iPhone驗收。
如果probe尚未完成，這份清單是接續方案，不能寫成Apple原生整合或向量搜尋已通過。

官方依據：[Foundation Models](https://developer.apple.com/documentation/foundationmodels)、
[NLEmbedding](https://developer.apple.com/documentation/naturallanguage/nlembedding)。
