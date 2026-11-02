from pathlib import Path
from docx import Document
from docx.shared import Inches, Pt, RGBColor
from docx.oxml import OxmlElement
from docx.oxml.ns import qn

OUT = Path(__file__).parent
doc = Document()
sec = doc.sections[0]
sec.page_height, sec.page_width = Inches(11.69), Inches(8.27)
sec.top_margin = sec.bottom_margin = Inches(.65)
sec.left_margin = sec.right_margin = Inches(.75)
for name in ['Normal', 'Title', 'Subtitle', 'Heading 1', 'Heading 2']:
    st = doc.styles[name]
    st.font.name = 'Microsoft JhengHei'
    st._element.get_or_add_rPr().rFonts.set(qn('w:eastAsia'), 'Microsoft JhengHei')
    st.font.color.rgb = RGBColor(0, 0, 0)
    st.paragraph_format.space_after = Pt(5)
    for border in list(st._element.xpath('.//w:pBdr')):
        border.getparent().remove(border)
doc.styles['Normal'].font.size = Pt(10)
doc.styles['Normal'].paragraph_format.line_spacing = 1.10
doc.styles['Title'].font.size = Pt(25)
doc.styles['Heading 1'].font.size = Pt(18)
doc.styles['Heading 2'].font.size = Pt(12)

def p(t, style=None): return doc.add_paragraph(t, style)
def h(t): doc.add_heading(t, 2)
def page(t):
    heading = doc.add_heading(t, 1)
    heading.paragraph_format.page_break_before = True
def table(headers, rows, widths=None):
    t = doc.add_table(rows=1, cols=len(headers)); t.autofit = False
    for c, text in zip(t.rows[0].cells, headers): c.text = text
    for row in rows:
        for c, text in zip(t.add_row().cells, row): c.text = text
    for i, row in enumerate(t.rows):
        pr = row._tr.get_or_add_trPr(); pr.append(OxmlElement('w:cantSplit'))
        if i == 0: pr.append(OxmlElement('w:tblHeader'))
        for j,c in enumerate(row.cells):
            if widths: c.width = Inches(widths[j])
            tcpr = c._tc.get_or_add_tcPr()
            shade = OxmlElement('w:shd'); shade.set(qn('w:fill'), 'E8EDF0' if i == 0 else ('F7F8F8' if i%2==0 else 'FFFFFF')); tcpr.append(shade)
            borders = OxmlElement('w:tcBorders')
            for edge in ['top','left','bottom','right']:
                e=OxmlElement('w:'+edge); e.set(qn('w:val'),'single'); e.set(qn('w:sz'),'4'); e.set(qn('w:color'),'D9D9D9'); borders.append(e)
            tcpr.append(borders)
            margins=OxmlElement('w:tcMar')
            for edge in ['top','left','bottom','right']:
                e=OxmlElement('w:'+edge); e.set(qn('w:w'),'90'); e.set(qn('w:type'),'dxa'); margins.append(e)
            tcpr.append(margins)
            c.vertical_alignment=1
            for par in c.paragraphs:
                par.paragraph_format.space_after=Pt(2)
                par.paragraph_format.line_spacing=1.08
                for r in par.runs: r.font.size=Pt(9); r.bold=(i==0)
    gap=p(''); gap.paragraph_format.space_after=Pt(0); gap.paragraph_format.line_spacing=1
    gap.paragraph_format.space_before=Pt(0); gap.add_run(' ').font.size=Pt(2)
    return t

p('政缺一咖', 'Subtitle')
p('設計系統與偷揪功能說明', 'Title')
p('夥伴協作文件   2026 年 10 月 24 日')
h('這個專案解決什麼問題')
p('政缺一咖以政大校園餐飲拼單為核心，讓使用者發布拼單、申請加入、協調取餐並透過聊天室聯絡。「偷揪」進一步承接尚未形成公開拼單的念頭：使用者先說出想吃什麼，分身在授權範圍內整理需求、尋找相容夥伴與建議分工，待雙方或三方願意聊聊後，再協調實際條件。')
p('核心價值是降低開口揪團與填表的成本，並讓需求、優惠資源及順路取餐能力有機會互補。價格與責任仍由本人確認；正式成團需要所有人同意同一版方案。')
h('給夥伴的一段介紹')
p('「你只要說想喝什麼或吃什麼，分身就會幫你找時間、地點和需求相容的人。大家先同意初步安排，進私人聊天室談品項、價格和誰去拿；最後每個人確認同一份條件，才變成正式拼單。」')
h('閱讀方式')
table(['內容', '夥伴可以拿來做什麼'], [
('設計基礎與元件', '建立一致的畫面、互動狀態與介面文案。'),
('偷揪流程與技術', '理解資料如何流動，以及 AI、規則與資料庫各自負責什麼。'),
('特色與應用情境', '向團隊或展示對象說明使用價值，安排示範案例。'),
('限制與交接索引', '掌握已實作範圍，定位後續驗收與相關程式。')], [1.65,4.95])
h('目前版本範圍')
p('本文描述目前工作目錄的實作，以 10 月 22 日的一句話偷揪流程為主，並保留進階需求訪談及正式方案確認的說明。歷史測試紀錄用來標示驗證範圍，不代表已正式上線或已完成新版 iPhone 全流程驗收。')

page('一 設計系統基礎')
p('目前設計以白底、深色文字及黃色重點操作為主，使用淺灰表面與細邊界區分區塊。這是一份從現有程式整理的設計基準；跨頁面字級、局部色彩與尺寸仍有各自定義，尚未全部收斂成完整 token 系統。')
h('色彩與用途')
table(['Token', '色碼', '現有用途與協作原則'], [
('background / ink', '#FFFFFF / #171717', '頁面背景及主要文字。'),
('muted / subtle', '#77756F / #A8A59D', '次要說明、弱化資訊及部分停用圖示。'),
('surface / border', '#F6F6F4 / #E6E4DF', '次要按鈕、區塊表面與分隔線。'),
('yellow / yellowSoft', '#FFCC00 / #FFF1A8', '主要動作、選取邊界與淡色選取背景；搭配深色文字。'),
('brown', '#8B6847', '設定欄位等輔助圖示。'),
('danger / success', '#DF4F4F / #327A51', '錯誤與成功的語意色。狀態仍需附文字。')], [1.5,1.55,3.55])
h('間距 圓角與版面')
table(['規格', '實作數值', '使用方式'], [
('間距', '4 / 8 / 12 / 16 / 24 / 32', '對應 xs 到 xxl，新增畫面優先使用既有 tokens。'),
('圓角', '6 / 8 / 999', '小型區塊、按鈕與膠囊；頭像採尺寸的一半。'),
('頁面', '最大寬度 520', '共用 Screen 置中、白底，處理頂部安全區。'),
('標頭', '高度 54', '左右操作與中間標題，底部細分隔線。')], [1.25,1.75,3.6])
h('字體與縮放')
p('iOS 使用 PingFang TC；其他平台使用 sans-serif，強調字體使用 sans-serif-medium。共用元件的基礎字級包括標頭 18／700、區段標題 17／700、主要按鈕 16／700、Pill 14。這些是縮放前數值。')
p('AppText 與 AppTextInput 會套用使用者字級設定：較小 0.82、標準 0.9、較大 1.0，預設為標準。例：16 的按鈕文字預設呈現為 14.4。新增文字應沿用包裝元件；fixedSize 僅用於確實需要固定尺寸的內容，例如頭像符號。')

page('二 元件與互動規範')
table(['元件', '目前規格', '協作使用原則'], [
('PrimaryButton', '最小高度 43，圓角 8，黃底；secondary 為淺灰底', '重要動作用明確動詞；送出中或條件不足時停用並說明原因。'),
('IconButton', '40 × 40，圖示 20，圓角 20', '帶 accessibilityLabel，例如返回或搜尋。'),
('TagChip', '最小高度 36，圓角 8，黃框', '選取時淡黃底及勾號，提供 selected 輔助狀態。'),
('Pill', '最小高度 30，圓角 999，文字 14', '標示狀態或屬性，支援 plain、yellow、danger、success。'),
('SettingField', '上下間距 16，圖示區 32 × 32', '標籤與輸入內容分組，使用底線區分欄位。'),
('EmptyState', '圖示 38，區塊最小高度 300', '說明沒有結果的原因，視情況提供下一步。'),
('GlassTabBar', '首頁、拼單、發佈、訊息、個人檔案', '可用時使用原生玻璃效果；支援透明度與動態減量偏好，鍵盤出現時隱藏。')], [1.2,2.35,3.05])
h('共用狀態')
p('按下主要按鈕或圖示時透明度為 0.62；圖示停用透明度為 0.5；主要按鈕停用底色為 #E5E5E3。TagChip 按下透明度為 0.6。新增元件先沿用既有狀態，避免同一操作在不同頁面有不同回饋。')
h('偷揪畫面的資訊順序')
p('建議依序呈現本人需求與推定來源、配對理由、尚待確認事項、初步分工，以及可執行的下一步。費用未知應顯示「待協調」，不要用 0 元代替；預算上限和實際每人費用分開呈現。')
p('初步同意、正式方案同意與已成團要有清楚差異。「同意初步安排，進聊天室」只表示願意協調；「談好了，確認正式拼單」才進入正式條件確認。方案應顯示版本及同意人數，例如第 2 版、1／3 人。')
h('後續設計檢查建議')
p('重要操作的點擊範圍建議至少 44 × 44；現有 40 的圖示按鈕與 43 的主要按鈕需要逐頁檢查。驗收應包含最大字級、長中文、螢幕閱讀器、鍵盤遮擋及色彩對比。目前不能把已具備的標籤與縮放支援直接視為完整無障礙認證。')

page('三 偷揪使用流程與產品特色')
h('新版主流程')
p('一句話需求 → 分身整理與找人 → 初步配對與分工建議 → 全員同意聊聊 → 私人協調聊天室 → 全員確認同版正式方案 → 正式拼單與聊天室。')
table(['階段', '使用者操作', '系統行為'], [
('說出需求', '輸入「我今天想喝50嵐」', '首次需本人點「啟用分身並幫我揪」。一句話上限 160 字。'),
('整理與補足', '檢查需求及地點時間來源', '優先本次描述，再參考本人近 30 天同類需求的地點與預算；缺少時暫用正門及接下來兩小時。'),
('尋找機會', '查看適合原因與初步分工', '套用相容條件，使用規則與可用的語義功能找人；沒有志願者時的角色安排仍只是建議。'),
('初步同意', '各自同意初步安排', '全部同意才開私人協調訊息；單人同意仍等待。'),
('協調與確認', '討論品項、費用、下單與取餐，再提出正式方案', '顯示版本及同意人數；任何改版都清除舊同意。'),
('正式成團', '每人確認同一版', '交易建立正式拼單，消耗相關訊號、使重疊方案失效，接續原拼單與聊天流程。')], [1.05,2.05,3.5])
h('特色與價值')
p('低門檻表達：不必先成為主揪或填完整表單，就能讓需求進入配對。進階介面仍可逐欄調整、訪談補問與設定分身。')
p('互補式協作：need 代表需求，offer 代表資源，plan 代表行程；order、pickup、coupon、share 表示願意下單、取餐、提供優惠或分食。系統除了找同樣想吃的人，也能找有資源或願意順路的人。')
p('先協調再承諾：未知價格可以留到私人聊天室討論，降低一開始就報價與指定責任的壓力。最終條件仍須逐人確認。')
p('可解釋且可撤回：說明為何推薦、哪些資訊未知；分身可停用、需求可撤回，並有安靜時段、邀請上限與封鎖限制。配對分數是規則評分，向量相似度是語義指標，兩者都不是成團成功機率。')

page('四 技術架構與 AI 分工')
h('整體資料流')
p('Expo 與 React Native App → FastAPI → 本人 Supabase Auth session → PostgreSQL RPC 與資料表。需求理解、向量及候選判讀在已設定的本機 API 模式下呼叫 Ollama；正式 cloud runner 不等同具備本機 AI。')
p('前端採 Expo Router 管理路由、TanStack React Query 管理伺服器資料、Zustand 管理登入與本機設定。套件清單目前為 Expo 57、React 19.2.3、React Native 0.86.3。這些是專案鎖定與宣告狀態，非對外版本建議。')
table(['技術層', '負責內容', '邊界'], [
('文字理解', 'Ollama Qwen3.5 4B 抽取分類、地點、時間、預算、費用及角色；進階訪談補問', '結果經 schema 與原句依據檢查。新版一句話可退回關鍵字整理，不推定承諾。'),
('規則配對', '檢查時間交集、地點、分類、品牌、預算、授權、封鎖及期限', '不相容者不能因語義相似而繞過條件。現有核心以 2 至 3 人小團為主。'),
('語義檢索', 'qwen3-embedding:0.6b 產生向量；Supabase pgvector 尋找相容候選', '向量使用固定模型與維度；雲端 RPC 再檢查資格，避免慢推論後資料已改變。'),
('候選判讀', '模型在已核對事實內產生理由及待確認資訊', '可回覆資訊不足；理由受允許事實限制，不能憑空宣稱優惠有效。'),
('分工草案', 'offline_rules_v1 依已知費用與志願安排下單和取餐', '不需要 LLM；預算不是售價，未知費用維持 null。初步安排可協調交換。'),
('授權與成團', 'PostgreSQL RPC 驗證登入、版本及同意，完成交易', 'LLM 沒有替人接受、付款或直接建立正式拼單的權限。')], [1.05,2.75,2.8])
h('分身與背景工作的實際意義')
p('目前是共用協調器依每位使用者的設定與需求工作，並非每人都有獨立長駐 LLM。StealthWatch 是程序記憶體內的有界工作清單，預設最多 32 個；規則檢查間隔 60 秒，符合設定時語義探索間隔 120 秒。停用、沒有有效需求或登入失效後取消訂閱。')
p('App 的結果查詢採前景輪詢；一般十秒查詢不反覆呼叫模型。一句話送出會啟動已接入的語義探索與判讀，失敗仍保留已存需求與可用規則結果。這套 watch 不能直接視為可跨重啟恢復或全天候服務的正式工作佇列。')

page('五 資料模型與一致性保護')
table(['物件', '重要欄位或意義', '可見範圍或規則'], [
('Agent 分身', 'enabled、quiet hours、daily limit、policy version', '本人管理，啟用需明確操作。'),
('Signal 訊號', 'need／offer／plan、分類、地點、時間、預算、已知費用、角色、版本', '本人原始需求；active、revoked、consumed 控制生命週期。'),
('Match 提案', '必要摘要、成員代號、理由、未知資訊、本人意願', '限參與者；不向其他參與者公開私人需求原文。'),
('Coordination room', 'coordination_opened、成員訊息', '全員初步同意才開啟；最近 100 則。撤回或到期後唯讀，封鎖後不再讀寫。'),
('Terms 與 Consent', 'terms_revision、expected_revision、agree、acceptances', '改版清空舊同意；提交方案本身不算同意。'),
('Formal post', 'formed_post_id、formed_at、公開內容與確認後費用', '全員同版同意才建立；不公開私人原文或本組改價原因。')], [1.25,2.7,2.65])
h('為什麼不會因重試或改價而誤成團')
p('重要寫入攜帶 Idempotency-Key。新版需求可依原 key 找回，聊天室訊息也有冪等處理；同 key 但不同需求內容會被拒絕。這讓斷線重試可以恢復操作，而不重複建立需求或訊息。')
p('正式方案送出使用 expected_revision，防止兩人同時修改時覆蓋他人新版。修改後，所有人必須重新同意；最後一人同意時，資料庫交易再次檢查有效期、參與資格與同版同意，避免前端畫面過時造成錯誤成團。')
h('權限與隱私')
p('後端以目前使用者的 Supabase session 呼叫 RPC，不把管理金鑰放進 App。私人協調表啟用 RLS 且禁止一般使用者直接查表，由 RPC 控制成員權限；LLM 推論之後仍需重新檢查版本與資格。原始需求文字可經本機模型處理，確認的需求與向量等資料則可能存入 Supabase，不能稱為所有資料都留在手機。')
h('核心 API 對照')
p('共同前綴 /api/v1/stealth-cloud。GET /quick-status 查可用性；POST /quick-start 建立一句話需求；GET /matches 查看配對；POST /matches/{id}/decision 表達初步意願；GET 與 POST /matches/{id}/room 讀寫協調訊息；PUT /matches/{id}/terms 提出正式版本；POST /matches/{id}/consent 確認同版條件。')

page('六 適用情境與展示案例')
table(['情境', '怎麼發揮作用', '成立前還要確認'], [
('想喝飲料但不想先發文', '使用者說「我今天想喝50嵐」，與相同品牌、地點和時間相容的人配對。', '飲品品項、實際售價與誰下單；不同已識別品牌不直接混配。'),
('一份披薩吃不完', '需求者找人分食；其他人可提供同類需求、已知報價或取餐能力。', '份量、每人金額與集合時間，三人仍須全員同意。'),
('有人有優惠券', 'offer 訊號與想買同類餐點的人互補，呈現可能的優惠資源。', '優惠券效期、門檻與適用商品；系統不自行折抵未驗證優惠。'),
('有人剛好順路取餐', 'plan 與 pickup 角色讓取餐能力進入配對，減少所有人各跑一趟的需要。', '實際路線、時間與本人是否接受本次取餐責任。'),
('還不知道價格', '先進私人協調室談條件，費用保留未知，不必先填零元。', '談妥真實費用與分工，提出正式方案後全員再確認。'),
('臨時需求且不想被打擾', '需求有期限，分身有安靜時段及每日上限；可停用或撤回。', '是否仍在有效窗口；不是任何時間都會持續邀請。')], [1.3,2.8,2.5])
h('什麼時候最有價值')
p('同一校園、集合地點集中、時間窗口相近，且使用者各有少量需求或資源時最容易看出價值。偷揪依賴有效需求的密度；若同時沒有相容的人，系統可以保存需求與說明等待，不能保證一定有人加入。')
h('建議的雙帳號展示')
p('兩位夥伴分別說「我今天想喝50嵐」，檢查相同集合地點與重疊時間。第一人同意初步安排後仍等待；第二人同意後開啟私人協調室，討論品項、價格和取餐者。提出第一版正式條件，先讓一人同意，再修改費用並填原因，展示同意歸零。最後兩人確認第二版，只建立一筆正式拼單。')
p('這個案例能同時展示低門檻輸入、配對可解釋、私人協調及同版全員授權。示範時使用隔離測試帳號；虛構展示人物不能代替真人同意。')
h('未來可延伸的方向')
p('共同採買、活動揪伴與其他互助需求可沿用「需求與資源互補、先聊再確認」的概念；目前分類與流程仍以餐飲為主。非餐飲用途需要重設欄位、條件與責任規則，屬於後續擴充，不能當成現有支援。')

page('七 目前限制與協作交接')
h('已具備與仍需完成的部分')
p('目前程式已具備一句話啟用與需求建立、規則與本機語義流程、私人協調室、版本化正式方案及全員確認。10 月 22 日交接紀錄記載相關元件、後端及隔離 PostgreSQL／Supabase 交易測試通過；不代表本文件製作時重新執行了這些測試。')
p('新版雙人 iPhone 完整流程仍待驗收；不能用 Web 測試或 iOS JavaScript 編譯代替。私人協調室入口目前在偷揪頁，尚未整合一般收件匣、未讀計數與推播；邀請到期後協調室轉唯讀。')
p('本機模型可能誤判或漏資訊，進階訪談仍需本人核對。Apple 裝置訪談模組已有實作，但不等於新版一句話流程已由 Apple 模型完整驅動；Apple 向量與跨平台模型替換也不能直接宣稱完成。')
p('目前地點主要為正門、東側門與南門，分類為披薩、正餐、飲料、麵包與零食。已識別飲料品牌約束涵蓋 50嵐／五十嵐、清心、可不可、迷客夏。付款、商家下單與配送整合尚未實作；正式部署、APNs 與 TestFlight 仍需另行完成。')
h('建議下一輪分工')
table(['協作角色', '接續項目', '驗收重點'], [
('設計與產品', '完善需求來源、待協調、版本改動及過期狀態文案', '使用者能分清初步同意與正式承諾。'),
('App 工程', '雙帳號實機全流程及字級／鍵盤檢查', '斷線草稿保留、切換帳號隔離、同意數同步。'),
('後端工程', '長時間 watch、候選規模與同時修改驗證', '不重複建團、過期拒絕、封鎖隔離及程序重啟行為。'),
('共同討論', '協調室是否進收件匣及如何通知', '定義有效期、未讀及推播後，再安排實作。')], [1.15,2.75,2.7])
h('專案來源索引')
p('App 根目錄 nccu-grouping-app：src/theme/tokens.ts；src/components/primitives.tsx、app-text.tsx、tag-chip.tsx、glass-tab-bar.tsx；src/store/font-size-store.ts；src/components/quick-stealth.tsx、stealth-coordination-chat.tsx、core-stealth-formation.tsx；src/lib/stealth-api.ts。')
p('Backend 根目錄 nccu-grouping-backend：app/core_cloud/quick_stealth.py、planner.py、discovery.py、stealth_watch.py；app/stealth/matching.py、provider_registry.py；supabase/core_stealth_quick.sql 與 core_stealth_formation.sql。')
p('進一步閱讀：docs/STEALTH_ONE_SENTENCE.md 為新版流程與限制；docs/STEALTH_AI_UI_ACCEPTANCE.md 為兩人、三人及撤回過期驗收；docs/RELEASE_READINESS.md 與 App docs/APPLE_INTAKE.md 為發行及裝置交接。遇到歷史文件差異，先核對最新程式與對應 migration。')

target=OUT/'政缺一咖_設計系統與偷揪功能說明.docx'
doc.save(target)
print('Document created')
