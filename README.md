# Code Sanitizer 程式碼去識別工具

用於授權資安測試的程式碼副本製作工具。預計接受原始碼檔案、專案資料夾或 ZIP，移除來源識別資訊與指定敏感註解，產生獨立副本、差異紀錄與驗證結果，保留原始專案不變。

目前進度：階段 A（測試素材）、B（身分蒐集與掃描）、C（轉換與差異）已完成；D（隔離驗證與匯出）、E（跨平台 CI 與發佈）尚未完成。

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
```

- `identities`：建立唯讀快照，從 Git 讀出開發者身分（不呼叫 git 執行檔），寫入 `identities.yaml` 供確認。
- `scan`：產生 `plan.json`（每個命中的位置、語法情境與處置），不修改任何檔案。
- `apply`：輸出去識別副本，差異寫入 `jobs/.../changes.patch`。
- `policy.yaml`：補充公司名、系統代號與允許保留的私有套件／registry，範例見 [tests/fixtures/policy.yaml](tests/fixtures/policy.yaml)。

`jobs/` 內含原始碼快照、身分對照與完整差異，屬機密，只能留在內部；只有 `--output` 的資料夾可以交付。

## 測試

```bash
uv run pytest            # 快速測試
uv run pytest -m slow    # 另外安裝相依並實際執行 pytest / ng build
```

測試素材由 [tests/fixtures/build_fixtures.py](tests/fixtures/build_fixtures.py) 產生（含多位作者的 Git 歷史、殘存檔、CRLF/BOM、惡意 `core.fsmonitor` 設定），驗收標準在 [tests/fixtures/expected.yaml](tests/fixtures/expected.yaml)。

「可運行」表示已通過指定環境與測試範圍，不代表所有行為經過證明等價；「去識別」表示降低來源識別線索，不保證無法透過程式結構、公開版本或其他特徵辨識來源。
