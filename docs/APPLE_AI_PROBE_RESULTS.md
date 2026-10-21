# Apple AI 最小真機驗證與 Windows 接續（2026-10-21）

結論：iPhone 17／iOS 26.6.1 已實際執行 Apple 裝置端模型與句向量。
**裝置端執行可行，但本輪完整需求欄位抽取品質不足，尚不能直接替代 App 的手動欄位。**
句向量可作下一步候選：保留繁體原文時 9/11 檢索命中，實際為640維，需要調整現有512維資料層。

後續已依本人指示在相鄰 App repo 加入較小的 Apple 訪談模組：只提供待核對摘要與補問，
保留手動表單，明確採用後只更新標題。這不改變本文件的大 schema 抽取結果，也不是向量整合。
App 模組的建置／實機狀態請以 App repo 的 `docs/APPLE_INTAKE.md` 為準；下方範圍是獨立 Probe 階段紀錄。

## 實際環境與範圍

- Mac：M1／8GB，Xcode27.1；只建獨立 SwiftUI Probe，iOS deployment target26.0。
- 真機：iPhone17（iPhone18,3）／iOS26.6.1，Developer Mode及Apple裝置端模型available。
- 使用 `SystemLanguageModel.default` 與 `NLEmbedding.sentenceEmbedding`；未安裝Ollama、未下載Qwen，
  未建現有Expo App、未連Supabase、未呼叫付費API、未部署、未上傳。
- 本人完成Xcode授權、Apple Team與手機開發者信任；只在手機跑固定合成案例。
- Probe未簽署產物約0.79MiB、首次DerivedData約83MiB。這不是未來完整Expo App的建置需求。

## 需求理解：6例／9輪，兩個版本都保留

| 設定 | 原始輸出符合案例預期 | 經既有grounding後 | 每輪中位數 |
|---|---:|---:|---:|
| v1 instructions-only | 1/6 | 2/6 | 2564.52ms |
| v2 guided-fields | 0/6 | 2/6 | 2971.22ms |

v1首個記錄4444.81ms；v2範圍2525.95～3336.26ms。這只是本輪少量合成案例，
不能當延遲SLA、盲測準確率、完整中文能力或Apple模型不可用的普遍結論。

原始案例固定，沒有刪題、更改預期或只挑成功樣本。v2僅調整schema欄位說明及提示詞，
所以已是本專案調整過的回歸集。一次完整案例中有任何一輪schema無效也列失敗。
「通過」只指Fixtures明列的欄位／角色／補問檢查，並非每個輸出欄位都正確。

具體失敗：

- v1補充地點、時間、預算後仍遺漏欄位；預算更正仍沿用舊值。
- 兩版都未可靠移除後來撤回的取餐角色，不能用原始模型輸出代表本人承諾。
- v2未提供費用／明確未知時多次填0，會混淆未知與免費；grounding可清除部分錯誤。
- v2首個缺資訊輸出把持續時間填0，低於現有15分鐘下限，schema拒絕；仍有虛構預算、地點、角色。
- v2 evidence可能引用schema說明或不完整片語，不能當作可核對原句。
- 整團總價與模糊時間可在grounding後通過，但這不代表模型原始抽取正確。

不要直接搬整套大schema、只修改前端provider名稱就上線。
下一步應測較小的原句片段抽取／增量更正，再由確定性程式解析與核對數值，保留手動表單。
這是後續方案，尚未在本輪實作或驗證。

## 繁體中文句向量：11查詢，每題3候選

| 模型／前處理 | 實際維度 | v1 top1 | v2 top1 | v2整組耗時 |
|---|---:|---:|---:|---:|
| zh-Hant／原繁體 | 不可用（revision0） | 未執行 | 未執行 | 不適用 |
| zh-Hant／繁轉簡 | 不可用（revision0） | 未執行 | 未執行 | 不適用 |
| zh-Hans revision1／保留繁體原文 | **640** | **9/11** | **9/11** | 252.61ms |
| zh-Hans revision1／明示繁轉簡 | **640** | 7/11 | 7/11 | 178.83ms |

兩次同機執行，相同設定全部查詢向量最大座標差為0。**尚未驗證跨手機／OS版本一致性。**
11題已是既有回歸題，這個命中率不能當作正式搜尋精準率。
整組耗時包含本機建立／使用模型和44次文字向量計算，不是單次API、全庫搜尋或網路延遲。

保留繁體原文時仍失敗的兩題：

- `rainy-pickup`：偏向選同樣無法出門的人，沒有選能順路取餐的人。
- `budget-topic`：偏向存錢討論，沒有選合併訂餐分攤運費。

這顯示語意相似不等於資源互補；硬條件、志願角色與全員確認仍由程式處理。
不能靠提高相似度自動推定同意、飲食安全、可接受費用或成功率。

## 可回 Windows 完成的工作

| 優先順序 | Windows工作 | 驗收重點 |
|---|---|---|
| 1 | 根據本次原始失敗輸出，設計較小的原句抽取／增量修正合約；拆分模型呼叫與核對流程 | 未知不當免費、撤回不沿用舊角色、未提欄位不補預設；補問保留有效資訊 |
| 2 | App新增可替換的裝置AI介面、Web測試替身、型別、取消／背景／切帳號處理 | Windows測試替身不冒充Apple品質；模型不可用時手動輸入 |
| 3 | 依選定的合約準備Swift bridge原始碼與共享fixture | 可在Windows撰寫；只有Apple SDK編譯與真機效果驗證留Mac／iPhone |
| 4 | 設計640維向量資料層與提交／搜尋API | 模型、revision、語言、前處理、文件格式綁定；拒絕混算Qwen512與Apple640 |
| 5 | 設計舊向量遷移／並存及重建流程 | 現有primary key只有signal_id，一筆需求只能存一個向量；不可在未審查下覆蓋舊索引 |
| 6 | 本人需求向量與版本／撤回失效；候選摘要與讀取權限 | 驗session、擁有者、版本、狀態、有限長度、有限維度與有限座標；客戶端不能替他人索引 |
| 7 | 配對判讀資料合約與快照失效；沿用成團交易 | 不外洩他人需求原文；模型判讀不改費用或同意；版本改動清除舊判讀 |
| 8 | PostgreSQL回滾測試、API回歸、容器／掃描／還原、Windows HTTPS入口 | 不需Mac承擔後端、Docker或模型服務；資料庫migration另行審查與授權套用 |

向量建議選擇 `zh-Hans revision1 + original-zh-Hant-v1 + 640D` 作下一輪候選，
不是直接採用為production模型。若先讓不同維度並存，建議獨立640維表／RPC版本，
而非破壞既有512維欄位；所有模型與前處理需穩定識別，不能補零、截斷或沿用舊0.2門檻。
搜尋維持伺服器硬條件檢查。客戶端產生的向量是未受信任資料，不能因聲稱model_id就取得權限。

待確認App原句是否傳至自己的後端核對；如要對話完全留在手機，須把必要核對邏輯移至App，
再以這些fixture驗證。Apple推論在手機，不自動代表整段資料流程完全離線。

## 必須留在 Mac／iPhone 的下一輪

1. Windows把較小的抽取合約、核對、App狀態與API測試準備好後，Mac只編譯原生bridge與安裝。
2. iPhone重跑這次失敗題，再加未參與調整的新案例；檢查繁中輸入與取消／背景行為。
3. 再找另一台相容手機核對同revision向量；若revision／語言不可用，要明確降級，不混算。
4. 最後才安排完整Expo iOS編譯與Google回跳、照片、聊天等整合驗收。

這輪沒有飛航模式測試、跨手機測試、模型記憶體峰值、完整App原生整合或Supabase向量migration。
不需要繼續在這台8GB Mac上跑Docker、Ollama或大批後端回歸。

## 證據與重現

- 原始固定案例：`tools/apple_ai_probe/Fixtures.json`。
- 可攜式分析器：`tools/apple_ai_probe/analyze.py`，Windows使用既有`.venv\Scripts\python.exe`執行。
- 兩輪完整原始輸出與分析：ignored `.local/apple-ai-probe-baseline-{report,analysis}.json`、
  `.local/apple-ai-probe-v2-{report,analysis}.json`；均只含合成案例，無帳號憑證。
- 可提交的摘要：`docs/evidence/apple-ai-probe-20261021.json`。
- 分析器8項測試、Ruff通過；其中包含不完整／Simulator不算真機通過、非法向量拒絕、
  原始錯誤與grounding修正分開，以及640維品質不誤報512維相容。
- `tools/apple_ai_probe/README.md`有本人設定、編譯與報告取回步驟。

官方API：[Foundation Models](https://developer.apple.com/documentation/foundationmodels)、
[NLEmbedding](https://developer.apple.com/documentation/naturallanguage/nlembedding)。
