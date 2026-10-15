# 程式碼去識別實作階段與決策

只做本機 CLI，支援 Windows、macOS、Linux。去識別範圍限於註解、字串、設定檔、Git 資訊與殘存檔，重點是隱藏開發者身分與帳號；不做識別字重命名，私有套件原樣保留。

## 1 分階段交付

| 階段 | 工作 | 完成條件 |
| --- | --- | --- |
| A 測試素材 | 建立 FastAPI、Angular、前後端整合三個 fixture，各含**多位作者的 Git 歷史**、註解／字串／設定中的身分資訊、hardcoded secret、私有套件、殘存檔（`.idea`、`.DS_Store`、`*.log`、`__pycache__`…） | 三個範例在原始狀態都能建立驗證基線 |
| B 輸入、身分蒐集與掃描 | 檔案／資料夾／ZIP 安全展開、快照；以 dulwich 讀取 Git 身分產生 `identities.yaml`；掃描註解、字串、設定、機密、殘存檔；dry run | 不執行來源即可列出所有身分命中、殘存檔與機密位置 |
| C 轉換與差異 | 移除 Git 與殘存檔、替換註解／字串／設定中的身分、機密假值化、產生獨立副本與 diff | 語法有效、原始碼不變、行號不變、再次套用無新增變更、輸出搜尋 Git 作者為 0 筆 |
| D 驗證與匯出 | 隔離 runner（container，無則本機子程序並標示）、經內部 mirror 安裝相依、雙版本 L0–L2（可選 L3）、OpenAPI 比較、輸出殘留掃描、ZIP | 範例通過必要驗證；失敗案例不會標示合格 |
| E 跨平台與發佈 | Windows／macOS／Linux CI 矩陣、CRLF／編碼／長路徑測試、打包為可安裝 CLI（`pipx` 或單一執行檔） | 三平台對同一輸入產出相同內容雜湊 |
| F 擴充 | adapter SDK、其他框架（Spring Boot、React、Vue、.NET…） | 新 adapter 通過契約測試；未知能力不會被宣稱支援 |

建議以 A 至 D 作為第一個可使用版本，E 同步進行。暫不估固定工期，主要變數是實際專案版本、外部服務與測試完整度。

### 目前進度（2026-10-14）

| 階段 | 狀態 | 說明 |
| --- | --- | --- |
| A | 完成 | 三個 fixture 與 `expected.yaml` 驗收標準；原始狀態 pytest、`ng build` 皆通過 |
| B | 完成 | 安全 ingest（ZIP 路徑穿越、symlink、大小寫碰撞、壓縮比）、dulwich 身分蒐集、tree-sitter 語法情境、機密偵測、`plan.json` |
| C | 完成 | 輸出副本、`changes.patch`、`.env.example`；三個 fixture 輸出驗收 0 違規，第二次掃描 0 變更，去識別後 pytest 與 `ng build` 仍通過 |
| D | 完成（隔離除外） | `sanitizer verify`：雙版本 L0–L3 比較、FastAPI OpenAPI 契約比對、smoke；`sanitizer export`：殘留掃描（不可略過）、待確認阻擋、決定性 ZIP、`SANITIZATION-NOTES.md`、`manifest.json`、`report.html`。尚未做容器隔離，目前為本機子程序＋環境變數允許清單 |
| E | 未開始 | Windows／Linux CI 矩陣 |

實作中確立的規則：

- **驗證的基線是「只移除殘存檔」的原始碼**，與輸出只差在文字替換，避免把 `.env` 移除等差異誤判為回歸。
- **OpenAPI 比對只忽略 `description`、`summary`、`servers` 與 `info`（保留 `version`）**；路徑、參數、schema、回應碼、operationId 必須相同。
- **Angular fixture 沒有單元測試**（需無頭瀏覽器），在 test profile 中將前端必要層級設為 L0–L1；未設定時判定為 `incomplete`，不會當作通過。

- **字串若與程式符號同名，視為符號引用，不替換、只報告。** 例如 inline template 的 `class="acme-header"` 與 SCSS class、元件 selector 同名，替換會讓樣式失效或 build 失敗。
- **人員帳號是信箱的 local part 時，整個信箱統一換成 `developer@example.invalid`。**
- **從姓名推導常見帳號格式**（`jdoe`、`john.doe`、`doe.john`…），因為 Git 歷史不一定有開發者在程式中使用的帳號。
- **無法對應到已知人員的信箱列為 `review`**（lockfile 中的第三方信箱除外）。

## 2 建議目錄

```text
code-sanitizer/
  docs/
  sanitizer/
    core/            # 工作模型、狀態、編輯套用
    ingest/          # 安全展開、快照、殘存檔清除
    identity/        # Git 身分蒐集、假名對應
    scanners/        # 註解、字串、設定、機密
    policies/
    transforms/
    adapters/
      fastapi/
      angular/
    validation/
    reporting/
    cli/
  tests/
    fixtures/
    integration/
```

此為建議結構；目前只建立文件，未產生程式骨架。

## 3 操作範例

以下指令是 CLI 草案，尚未實作：

```sh
sanitizer identities ./input-project --job-dir ./jobs/review-001
sanitizer scan ./input-project --policy ./policy.yaml --job-dir ./jobs/review-001
sanitizer apply ./jobs/review-001 --output ./output-project
sanitizer verify ./jobs/review-001 --profile ./test-profile.yaml
sanitizer export ./jobs/review-001 --output ./deliverables
```

`identities` 從 Git 蒐集開發者身分，產生 `identities.yaml` 供人工確認；`scan` 產生預覽；`apply` 僅套用政策允許或已接受的修改；`verify` 比較兩個版本；`export` 根據交付狀態產生交付包。需要人工決策的 finding 在批次模式也不能默認接受。也提供 `sanitizer run` 一次執行全部步驟（遇到待確認項目即停止）。

## 4 必要測試案例

| 情境 | 預期結果 |
| --- | --- |
| Git 目錄、worktree `.git` 檔及巢狀 repo | 先蒐集身分，再從輸出移除；不讀取外部 Git 路徑 |
| `.git/config` 設定了 `core.fsmonitor` 等可執行指令 | 不呼叫 git 執行檔，指令不被觸發 |
| 多位作者、GitHub noreply 信箱、remote URL 含 org／帳號 | 全部進入 `identities.yaml`；輸出中搜尋皆為 0 筆 |
| 註解與字串中都有作者姓名 | 兩者都替換；字串替換列入報告供抽查，驗證通過 |
| 識別字中含作者帳號（如 `jdoe_helper`） | 不修改，報告列為「識別字命中、未處理」 |
| docstring 含作者資訊並被 OpenAPI 讀取 | 只替換身分；OpenAPI 只在 description 類欄位出現差異 |
| ts-ignore、三斜線 reference、encoding、shebang | 保留且測試／建置結果不受影響 |
| 註解包含 LICENSE 或 SPDX | 保留；命中身分時列出政策衝突 |
| `.idea/workspace.xml`、`.DS_Store`、`*.log`、`*.pyc`、`*.map`、`.coverage` | 全部移除並列入報告 |
| 設定檔中的 `C:\Users\jdoe\`、`/Users/jdoe/` | 替換為匿名路徑 |
| 私有套件 `@acme/*` 與內部 registry | 原樣保留、可經內部 mirror 安裝；不列為殘留 |
| `.npmrc`／`pip.conf` 含 token | token 移除或假值化，registry 位址保留 |
| `.env` 有真實金鑰但啟動需要設定 | 交付包只含鍵名；雙版本使用相同測試設定驗證 |
| CRLF、中文、Unicode、BOM、尾端無換行 | 不造成無關格式變化；不支援的編碼列為未處理 |
| Windows 與 macOS 處理同一輸入 | 產出內容雜湊相同 |
| Angular template、SCSS、i18n、不同 build 設定 | 正確分派解析器並測試實際指定設定 |
| 惡意 ZIP、symlink、超量解壓、大小寫碰撞、巢狀壓縮 | 在寫出目標外或超過資源限制前拒絕 |
| LFS pointer、submodule 未準備完整 | 指出缺漏，不能聲稱驗證成功 |
| baseline 原本失敗、無測試、服務缺失 | 分別記錄原因，交付狀態不為 qualified |
| 三位作者分別出現在註解、字串、設定 | 全部變成 `developer`，輸出無法分辨作者人數 |
| 公司名、內部網域、系統代號出現在註解、字串、設定 | 依類別替換；識別字中的命中只報告 |
| 重跑相同工作或套用過期計畫 | 相同內容具決定性；來源雜湊不符則拒絕套用 |

## 5 已確認決策

| 決策 | 結論 |
| --- | --- |
| 交付對象 | 內部紅隊、AI 工具 |
| 去識別深度 | 註解、字串、設定檔、Git commit 資訊、殘存檔；不重命名 |
| 私有套件 | 原樣保留，不去識別 |
| 驗證網路 | 可連內部 npm／pip mirror |
| 介面 | 僅 CLI |
| 敏感詞來源 | github.com 相關身分（Git 作者、帳號、org／repo）＋使用者補充 |
| 假名粒度 | 全部統一為 `developer` |
| 輸出 Git repo | 不需要；交付物不含 Git 中繼資料，也不提供建立新 repository 的選項 |
| 組織資訊（公司名、內部網域、系統代號） | 要替換 |
| 驗收素材 | 先自建測試專案 |
| 平台 | Windows、macOS、Linux |

## 6 仍待確認

| 問題 | 暫定方案 |
| --- | --- |
| 驗證要做到 L3（啟動與 smoke test）嗎？ | 預設 L0–L2，有啟動方式時加跑 L3 |
| 何時以真實專案驗收？ | 自建 fixture 通過後再試跑一份真實專案 |
