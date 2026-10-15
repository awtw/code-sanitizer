# 程式碼去識別需求與驗收條件

本工具為授權資安測試建立可交付的獨立程式碼副本。核心目標是**讓接手的內部紅隊與 AI 工具無法得知、也無法追蹤對應的開發者與帳號**，同時保留測試標的的功能及安全行為。首版支援 FastAPI 與 Angular，核心須可擴充其他框架。

本文件為 2026 年 10 月 14 日更新版，已納入使用者確認的決策（見第 0 節）。

## 0 已確認決策

| 項目 | 決策 |
| --- | --- |
| 交付對象 | 內部紅隊、AI 工具 |
| 主要去識別目標 | **開發者身分與帳號**：姓名、信箱、GitHub 帳號、員工編號、本機使用者路徑等 |
| 處理範圍 | 註解、字串、設定檔、Git commit 資訊、殘存檔；**不重命名**識別字、函式、類別、檔名、路由 |
| 私有套件（如 `@公司/xxx`、內部 Python 套件） | **原樣保留，不去識別**；其名稱與 registry 位址列入允許清單 |
| 驗證環境網路 | 可連內部 npm / pip mirror 安裝相依 |
| 介面 | **僅 CLI**，不做 Web UI |
| 敏感詞來源 | 以 **github.com** 相關資訊為主：Git commit 作者／提交者、remote URL 中的 organization 與 repository、GitHub 帳號、noreply 信箱；再加上使用者補充的詞彙 |
| 假名粒度 | **所有開發者統一替換為 `developer`**，接手者無法分辨哪幾段是同一人所寫 |
| 輸出 Git repo | **不需要**，交付包不含任何 Git 中繼資料，也不建立新的 repository |
| 組織資訊 | **公司名、內部網域、系統代號也要替換** |
| 驗收素材 | 先自建測試專案 |
| 執行平台 | Windows、macOS、Linux 皆須支援 |

## 1 使用情境與邊界

使用者提供有權處理的程式碼。系統**先從 Git 歷史與設定中自動蒐集開發者身分**（作者、信箱、帳號、remote），合併使用者詞彙後掃描全專案，說明哪些可以處理、哪些牽涉功能，產生可比對且可驗證的副本。

工具不以規避資安偵測為目標。不修改存取控制、驗證、加密、稽核、漏洞邏輯或套件版本以通過測試。資安測試需要的原始弱點不應被自動修復或隱藏：寫死的機密以**同格式假值替換並在內部報告中標記位置**，讓紅隊仍看得到「此處有 hardcoded secret」，但拿不到真實值（交付對象包含 AI 工具，真實機密絕不可外送）。

「獨立」定義為不含原始 Git 歷史、在另一個路徑輸出且可依說明重建的副本，不表示消除外部服務或套件相依。多檔專案輸出資料夾或 ZIP，不合併成單一原始碼檔。

## 2 輸入與操作流程

| 編號 | 需求 | 驗收方式 |
| --- | --- | --- |
| FR01 | 接受單一原始碼檔、專案資料夾、ZIP | 三種輸入都能建立工作副本；單檔缺少專案相依時僅提供靜態驗證 |
| FR02 | 保留原始輸入 | 處理前後原始檔內容雜湊相同；輸出不可位於輸入內或覆蓋既有路徑 |
| FR03 | 支援使用者詞彙、明確替代值、保留清單與排除規則 | 報告可追溯命中的規則、位置、原因與處置 |
| FR04 | 自動辨識 FastAPI、Angular、多專案結構與未知類型 | 顯示辨識依據及信心；不確定時不強制選擇 adapter |
| FR05 | 預覽變更再套用 | 可逐項接受、略過、保留；批次模式只接受政策允許的動作 |
| FR06 | 產生新副本及差異 | 顯示新增、刪除、修改、未處理與失敗項目；文字檔提供 unified diff |
| FR07 | 比較處理前後驗證 | 每項驗證具有指令、環境、狀態、時間及受保護的日誌 |
| FR08 | 再次掃描輸出 | 所有已知命中均有處置；未解決項目阻擋合格交付 |
| FR09 | 支援框架擴充 | 新 adapter 不需改動核心工作流程即可註冊、測試和使用 |
| FR10 | **自動蒐集開發者身分** | 從 `.git` 讀出所有 commit 的 author / committer 姓名與信箱、remote URL、分支與 tag 名稱中的帳號，產生候選詞彙清單供確認 |
| FR11 | **殘存檔清除** | 依第 3.3 節清單移除 IDE、作業系統、log、快取等殘存檔，並列入報告 |
| FR12 | **跨平台** | 同一份輸入在 Windows、macOS、Linux 產出相同內容（換行風格依原檔保留） |

流程：選擇來源 → **蒐集開發者身分** → 確認詞彙 → 掃描 → 預覽計畫 → 套用副本 → 驗證 → 檢視結果 → 匯出。

首版不含直接 clone GitHub URL、Web UI 與識別字重命名。大型二進位、圖片內文字、PDF 附件、混淆程式、巢狀壓縮檔均不宣稱可自動去識別；需列為未處理或依政策排除。

## 3 去識別規則

### 3.1 偵測目標（開發者身分）

| 類別 | 範例 | 來源 |
| --- | --- | --- |
| 姓名 | `John Doe`、`王小明` | Git author / committer、`@author`、`Created by` |
| 信箱 | `john.doe@corp.example.com`、`12345+jdoe@users.noreply.github.com` | Git、註解、`package.json` |
| GitHub 帳號 | `@jdoe`、`github.com/jdoe` | remote URL、CODEOWNERS、註解中的 mention |
| GitHub organization / repository | `github.com/acme-org/payment-api` | remote URL、README badge、`package.json` repository |
| 員工編號、內部帳號 | 依使用者提供的正則 | 註解、字串、設定 |
| 本機使用者路徑 | `C:\Users\jdoe\`、`/Users/jdoe/`、`/home/jdoe/` | 設定檔、log、`.coverage`、source map |
| 分支或 tag 中的帳號 | `feature/jdoe-login` | Git refs（只用於蒐集詞彙，refs 本身隨 `.git` 移除） |

所有開發者身分**統一替換**為單一匿名值，接手者無法分辨人數或哪幾段是同一人所寫：

| 原始 | 替換為 |
| --- | --- |
| 姓名、GitHub 帳號、員工編號 | `developer` |
| 信箱 | `developer@example.invalid` |
| 本機使用者路徑 | `C:\Users\developer\`、`/home/developer/` |

### 3.1.1 組織資訊

公司名、內部網域、系統代號同樣要替換。候選詞來源：使用者提供的詞彙、作者信箱網域、remote URL 的 host 與 organization（GitHub Enterprise 主機名稱）、`package.json`／`pyproject.toml` 的名稱與描述。

| 類別 | 範例 | 替換為 |
| --- | --- | --- |
| 公司名／品牌 | `Acme`、`acme bank` | `company` |
| 內部網域與主機 | `api.corp.acme.com` | 保留子網域層次：`api.corp.example.internal` |
| GitHub organization／repository | `acme-org/payment-api` | `org/repo` |
| 系統代號 | `PAYGW`、`Phoenix` | `system01`、`system02`…（保留系統之間的區別，以利紅隊理解架構；系統代號不指向個人） |

與身分相同，**識別字中的命中只報告不修改**（例如 class `AcmeClient`）；私有套件名稱（如 `@acme/ui-kit`）與私有 registry 位址依允許清單保留。

### 3.2 處理位置與預設

| 目標 | 預設 | 限制與原因 |
| --- | --- | --- |
| `.git` 目錄或 worktree 的 `.git` 檔 | 先蒐集身分（FR10）後，從輸出移除，含巢狀 repository | 不修改原始 Git 紀錄；不執行 hook |
| Git author／committer、GPG／SSH 簽章、remote／config／hooks | 來源 `.git` 強制移除，包含物件、tag、reflog 與設定中的 token；選擇重建時僅以處理後內容建立匿名、無簽章提交 | 不複製舊歷史、不繼承全域設定；身分固定為 `developer <developer@example.invalid>` |
| TODO 帳號、內部 IP、Jira／GitLab Issue、內部網域 | 預設偵測括號式 TODO／FIXME／XXX 帳號、RFC1918、IPv6 ULA 及 `.internal`／`.local`／`.corp`；其他網域與專案鍵由 `internal_markers` 政策補充 | 遵循語法情境與允許清單；未知裸 Issue 編號不推斷 |
| 一般註解中的身分資訊 | 只替換命中的文字，保持語法與換行（行號不變） | 不使用全檔字串取代 |
| **字串常數**中的身分資訊 | 替換命中的文字，保持引號、跳脫與長度無關的語法 | 可能影響執行行為 → 須通過第 5 節驗證；位置列入報告供抽查 |
| **設定檔**（JSON / YAML / TOML / INI / `.env.example` / XML） | 替換命中值，保留格式與其他鍵 | 用格式感知的方式修改，不破壞結構 |
| `package.json` / `pyproject.toml` 的 `author`、`contributors`、`maintainers`、`repository`、`bugs`、`homepage` | 移除或改為匿名值 | 不影響建置 |
| `.github/`、CODEOWNERS、issue template、`FUNDING.yml`、badge | 預設移除 `CODEOWNERS` 與 template；workflows 移除或去識別 | CI 設定不影響本地執行 |
| `.gitignore`、`.gitattributes` | 保留並掃描 | 可能影響 LFS 或換行行為 |
| `.gitmodules` | 保留路徑、去識別 URL；未展開的 submodule 回報缺漏 | |
| README、文件 | 定點替換身分資訊 | 保留安裝與啟動說明 |
| Python docstring、JSDoc、API 描述 | 只替換身分資訊，其餘保留 | docstring 會進入 FastAPI OpenAPI；比較時允許這些欄位變更 |
| shebang、encoding、type ignore、noqa、ts-ignore、三斜線指令、工具 pragma | 保留 | 影響執行、型別檢查、打包與分析 |
| LICENSE、NOTICE、SPDX、第三方版權註解 | 保留 | 若含內部開發者身分，回報衝突由人決定 |
| import、套件名、class、函式、路徑、檔名、路由、DB schema | **不修改**，只偵測並報告 | 已確認不做重命名 |
| 私有套件名稱與私有 registry 位址（`.npmrc`、`pip.conf`、lockfile） | **原樣保留**，列入允許清單 | 已確認；但 `.npmrc` / `pip.conf` 中的 token 屬機密，須移除 |
| 金鑰、token、密碼、私人憑證、`.env` | 以同格式假值替換（如 `REDACTED_SECRET_007`）並在內部報告標記類型與位置；`.env` 只保留鍵名 | 真實機密不得進入交付包；驗證以測試設定注入 |
| lockfile、相依版本與 checksum | 保留 | 不為去識別而更動版本或完整性欄位 |

### 3.3 殘存檔（預設移除）

| 類別 | 檔案 |
| --- | --- |
| 版控 | `.git/`、`.svn/`、`.hg/`、`*.orig`、`*.rej`、`*.patch`（非專案用途者） |
| IDE / 編輯器 | `.idea/`（`workspace.xml` 含使用者名稱與路徑）、`.vscode/`（`settings.json` 可選擇保留但去識別）、`.history/`、`*.swp`、`*~`、`.project`、`.settings/` |
| 作業系統 | `.DS_Store`、`Thumbs.db`、`desktop.ini`、`__MACOSX/` |
| Log 與錯誤報告 | `*.log`、`npm-debug.log*`、`yarn-error.log`、`pip-log.txt` |
| 測試與覆蓋率 | `.pytest_cache/`、`.coverage`、`coverage/`、`htmlcov/`、`.nyc_output/`、`junit*.xml` |
| Python 產物 | `__pycache__/`、`*.pyc`（內嵌原始絕對路徑）、`.venv/`、`venv/`、`*.egg-info/`、`.mypy_cache/`、`.ruff_cache/` |
| Angular / Node 產物 | `node_modules/`、`dist/`、`.angular/`、`*.map`（source map 含原始路徑與內容） |
| 本機設定 | `.env`、`.env.local`、`.env.*.local`（改產生只含鍵名的 `.env.example`） |

vendored 原始碼不得誤當快取刪除；清單可由設定覆寫。

### 3.4 比對規則

匹配規則支援精確詞、大小寫選項、詞界及受限制的正則表示式。比對結果須附語法情境：註解、字串、識別字、設定或文件。**命中在識別字中時只報告不修改**（已確認不重命名）。保留規則優先於自動修改；衝突不能靜默略過。

不對每段程式碼進行公開搜尋驗證，避免將私有來源送至外部搜尋引擎。去識別效果以蒐集到的身分、使用者詞彙、已知來源模式及輸出掃描衡量。

## 4 輸出與差異比對

每個工作包含兩組獨立產物：

1. **交付包**：`sanitized-source.zip`（或資料夾），內含處理後程式碼、必要授權文件與去識別後的重建說明。不包含原始碼快照、完整 diff、映射或原始來源識別。ZIP 內的檔案時間戳一律固定、不帶擁有者資訊。
2. **內部報告**：`report.html`、`report.json`、`changes.patch`、`manifest.json`、身分對照表、機密位置清單、驗證摘要及必要日誌。完整 diff 與對照表含原始身分，只能留在受控工作目錄，不隨交付包匯出。

報告記錄規則版本、輸入與輸出雜湊、檔案處置、未解決項目、框架辨識、環境與驗證結果。因為**行號與檔案路徑不變**，紅隊回報的「檔案:行號」可直接對應原始碼。

## 5 可運行的判定

原始版本和處理版本使用相同 runtime、lockfile、測試資料、測試服務與測試指令。原始版本的驗證僅在獨立暫存副本執行，不污染輸入。兩者使用相同的測試設定替換機密，另行記錄這與正式環境的差異。相依安裝可連線內部 npm / pip mirror。

| 層級 | 檢查 | 能支持的結論 |
| --- | --- | --- |
| L0 | 解析、編碼及檔案結構 | 修改後仍可解析，不代表可執行 |
| L1 | 相依安裝、編譯、型別或框架 build | 專案在指定環境可建置 |
| L2 | 原有測試、API 契約與 UI 關鍵流程 | 已覆蓋情境未出現新增回歸 |
| L3 | 啟動、健康檢查、前後端整合、關鍵業務 smoke test | 在指定測試環境可運行 |

預設合格交付要求 L0 至 L2 必要項全部通過、無新增失敗、無未處理的阻擋項；L3 在專案提供啟動方式與測試服務時納入。沒有測試、缺資料庫、依賴無法下載或未提供啟動方式時標示「驗證不完整」，不能把 skip 當作 pass。僅單檔可產生「已處理但未驗證可運行」的審查產物。

基線本來就失敗時，須區分既有問題和新增問題；即使沒有新增失敗，也不能標記可運行。允許輸出供除錯的非合格副本，但清楚標示狀態，且不得繞過機密交付阻擋。

## 6 非功能需求與完成標準

- 去識別處理全程本機、離線，不傳原始碼至外部 AI 或搜尋服務；相依下載只允許內部 mirror。
- **跨平台**：Windows、macOS、Linux。路徑處理不依賴 `/`；保留原檔換行（LF / CRLF）與編碼；偵測大小寫不同但名稱相同的檔案碰撞；不依賴 bash，外部指令以 argv 呼叫（Windows 上正確解析 `npm.cmd`、`npx.cmd`）。
- 解析、套用、測試分階段執行；單檔失敗可回報，但整體不能被誤標成功。
- 相同輸入、規則及工具版本須產生相同程式內容；時間戳等報告欄位不計入內容決定性。
- 再處理一次相同副本不得持續新增修改，規則須具冪等性。
- 暫定單一 ZIP 上限 200 MiB、解壓總量 1 GiB、20,000 檔、單檔 10 MiB；超限拒絕並提示調整。這些是待實測的預設，不是效能承諾。
- 工作目錄、報告與日誌採限制存取權限；本機資料由使用者明確清除。

首版完成須以一個 FastAPI 範例、一個 Angular 範例與一個前後端整合範例（皆含多位作者的 Git 歷史）證明：原始碼不變、Git 中繼資料與殘存檔移除、**輸出中搜尋任一 commit 作者姓名／信箱／GitHub 帳號皆為 0 筆**、功能性註解保留、私有套件可正常安裝、diff 可追溯、輸出再次掃描及 L0 至 L2 驗證通過，並在 Windows 與 macOS 上產出相同內容。未知框架與缺環境案例也須顯示正確的不完整狀態。
