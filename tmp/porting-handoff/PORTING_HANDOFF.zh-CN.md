# Contexture 移植交接

日期：2026-09-07

该临时交接文档会提交到 Git 以便恢复工作，之后可以整体删除；它不构成产品等价声明。

## 当前状态

- Python 参考仓库：本地 `release/0.12.0`，比远端分支超前 33 个提交。证据账本只保留在本地，未推送到 Python `master`。
- Go 绑定：`master` 干净且与 `origin/master` 同步。
- TypeScript 绑定：`master` 已在 `8c82275` 与远端同步，但审计补充的两个证据文件仍未提交：`scripts/verify-package-consumer.mjs` 和 `test/system-api.test.ts`。下一位 agent 必须保留它们，跑完整门禁并审计后再提交推送。
- 产品账本：60 个源码模块中，33 个 `implemented`、1 个 `verified`、26 个 `missing`。只有两端都有聚焦执行证据、语言门禁、消费者证据和独立审计后才能计入。

## 最近已推送工作

Go 已包含并审计通过 foundation、identity、errors、Role、Tool、Node、Disclosure API、Execution API、GraphContext 和 server options。最新 options 修复为 `fd9df4a`，完整 Go 门禁和审计均通过。

TypeScript 已包含并审计通过 foundation、identity、errors、Role 和 Tool。Tool 提交 `5cec7be` 已通过串行完整 `npm run check` 及独立审计。Disclosure 源码提交 `8c82275` 已通过完整门禁，但独立审计要求补充 public facade 和 packed consumer 证据；上述两个文件就是未提交的证据改动。

## 恢复后的顺序

1. 完成并验证现有 TypeScript Disclosure 证据改动；独占运行 `npm run check`，提交推送并独立审计完整 Disclosure API。
2. 实现并审计 TypeScript server options。已知缺口包括 auth ownership/stdio 冲突、body-size/413、拒绝 `?`/`#` 路径、listener 安全、打包消费者证据和双语文档。
3. 为 Go 已完成的 Execution API、GraphContext、Node 实现并审计 TypeScript 对应版本，然后才能登记这些 Python 模块。
4. 继续 manifest 中剩余的 CLI、Server/Surface、Web、Demo 和 initializer 模块。不能为了通过移植而修改 golden 文件。

## 必须门禁

```bash
# Python 参考
uv run --extra dev pytest -q
uv run --extra dev pyright
uv run --extra dev ruff check contexture tests scripts
uv run --extra dev validate-pyproject pyproject.toml
uv run python scripts/verify_porting_contract.py
uv run python scripts/product_manifest.py --check

# Go
go run ./internal/conformancecheck
go test -race ./...
go vet ./...

# TypeScript（不得与另一个 TS build/gate 并行）
npm run check
```
