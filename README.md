# Code Sanitizer 程式碼去識別工具

用於授權資安測試的程式碼副本製作工具。預計接受原始碼檔案、專案資料夾或 ZIP，移除來源識別資訊與指定敏感註解，產生獨立副本、差異紀錄與驗證結果，保留原始專案不變。

目前進度：階段 A 至 D 已完成（測試素材、身分蒐集與掃描、轉換與差異、驗證與匯出）；E（跨平台 CI 與發佈）與容器隔離執行尚未完成。

## 文件

- [需求與驗收條件](docs/requirements.md)：處理範圍、預設規則、輸出內容及完成標準。
- [系統架構與驗證設計](docs/architecture.md)：處理流程、擴充介面、隔離執行及資料模型。
- [實作階段與決策](docs/implementation-plan.md)：建議開發順序、測試案例與待確認事項。

## 已確認方向

- 交付對象：內部紅隊與 AI 工具；重點是讓接手者**無法得知或追蹤對應的開發者與帳號**。
- 處理範圍：註解、字串、設定檔、Git commit 資訊與殘存檔（IDE、OS、log、快取、建置產物）。不重命名函式、類別、路由或檔名。
- 開發者身分從 Git 歷史自動蒐集（作者、信箱、GitHub 帳號、org／repo），經人工確認後統一替換為 `developer`；公司名、內部網域、系統代號也一併替換。
- 私有套件原樣保留；驗證時可經內部 npm／pip mirror 安裝相依。
- 僅提供 CLI，支援 Windows、macOS、Linux。

## 使用方式

需要 Python 3.11+ 與 [uv](https://docs.astral.sh/uv/)。

```bash
uv sync
```

逐步執行（建議，可在中間人工確認身分清單）：

```bash
uv run sanitizer identities ./project.zip --job-dir ./jobs/review-001
uv run sanitizer scan --job-dir ./jobs/review-001 --policy ./policy.yaml
uv run sanitizer apply --job-dir ./jobs/review-001 --output ./deliverable
uv run sanitizer verify --job-dir ./jobs/review-001 --profile ./test-profile.yaml
uv run sanitizer export --job-dir ./jobs/review-001 --package ./deliverable.zip
```

- `identities`：建立唯讀快照，從 Git 讀出開發者身分（不呼叫 git 執行檔），寫入 `identities.yaml` 供確認。
- `scan`：產生 `plan.json`（每個命中的位置、語法情境與處置），不修改任何檔案。
- `apply`：輸出去識別副本，差異寫入 `jobs/.../changes.patch`。
- `verify`：原始碼（只移除殘存檔、不改文字）與去識別碼各跑一次相同驗證並比較；FastAPI 比對 OpenAPI 契約。結果寫入 `verify.json`，判定為 `qualified`、`incomplete` 或 `regression`。
- `export`：再掃描一次輸出，仍有敏感詞或殘存檔就阻擋（不可略過）；有待確認項目需加 `--accept-review`；驗證未達 `qualified` 需加 `--allow-unqualified`，且檔名會標示 `UNQUALIFIED`。ZIP 內附 `SANITIZATION-NOTES.md`（機密位置與驗證狀態），內部報告為 `jobs/.../report.html`。
- `test-profile.yaml`：驗證設定（必要層級、Python 版本、smoke 路徑、測試用環境變數），範例見 [tests/fixtures/test-profile.yaml](tests/fixtures/test-profile.yaml)。
- `policy.yaml`：補充公司名、系統代號與允許保留的私有套件／registry，範例見 [tests/fixtures/policy.yaml](tests/fixtures/policy.yaml)。

`jobs/` 內含原始碼快照、身分對照、完整差異與驗證日誌，屬機密，只能留在內部；交付物是 `export` 產生的 ZIP。

> 驗證會執行受測專案的程式（安裝套件、import、測試）。目前以本機子程序執行，只傳入允許清單內的環境變數（PATH、proxy、套件 mirror 設定等），尚未放進容器隔離；報告會標示這一點。

## Git 與內部標記清理

來源 `.git`（含巢狀 repository 與 worktree 指標）一律移除，不能以 `residue.keep` 保留；交付物不含任何 Git 中繼資料，也不建立新的 repository。原始專案與內部快照保留原貌。

掃描預設處理 `TODO (@username)`／`FIXME (@username)`／`XXX (@username)`、RFC1918 IPv4、IPv6 ULA，以及 `.internal`／`.local`／`.corp` 網域。替代 IP 使用 `192.0.2.1`／`2001:db8::1`；僅在註解、字串、文件與設定情境替換，程式識別字與受保護的 import 不變。會影響連線的替換需以測試環境設定驗證。

其他內部網域及 Issue 專案請在 `policy.yaml` 指定：

```yaml
internal_markers:
  domains: [jira.company.example, gitlab.company.example]
  jira_projects: [PAY, OPS]          # PAY-123 → REDACTED_ISSUE
  gitlab_projects: [team/service]   # team/service#123 → REDACTED_ISSUE
```

已知內部主機的 GitLab `/issues/123`、`/-/issues/123` URL 編號會改為 `0`。未指定的裸 Jira 編號、無專案資訊的 `#123`、其他內部網域無法可靠推斷，須補政策詞彙；允許清單仍適用於敏感標記。這些規則不代表已完成階段 D 的交付驗證。

## 測試

```bash
uv run pytest            # 快速測試
uv run pytest -m slow    # 另外安裝相依並實際執行 pytest / ng build
```

測試素材由 [tests/fixtures/build_fixtures.py](tests/fixtures/build_fixtures.py) 產生（含多位作者的 Git 歷史、殘存檔、CRLF/BOM、惡意 `core.fsmonitor` 設定），驗收標準在 [tests/fixtures/expected.yaml](tests/fixtures/expected.yaml)。

「可運行」表示已通過指定環境與測試範圍，不代表所有行為經過證明等價；「去識別」表示降低來源識別線索，不保證無法透過程式結構、公開版本或其他特徵辨識來源。
