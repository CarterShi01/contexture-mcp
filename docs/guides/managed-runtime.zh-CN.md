# Managed 运行时：运行中可变、带版本的上下文树

[English](managed-runtime.md) · [文档](../README.zh-CN.md) · [托管](hosting.zh-CN.md)

静态 Contexture 应用编译一次、冻结服务。Managed 运行时面向**在 Agent 使用过程中仍在变化**的树：
新增 Skill、Role 改名、Tool 退役、项目区域重组。它把四件事放在同一条可审计的版本链上：

| 概念 | 含义 |
| --- | --- |
| **定义（Definition）** | 某个 revision 被接受的图：Role、Skill、Tool、关系、发布。只能通过事务改变。 |
| **历史（History）** | 每个已接受 revision，只凭日志即可重放；单节点历史、稳定 ID diff、revert。 |
| **发布（Release）** | 显式请求服务某个 revision。提交变更不会悄悄改变 Agent 拿到的内容。 |
| **代（Generation）** | 实际在服务的不可变编译快照。每个请求在整个生命周期固定一个代。 |

状态：2.0.0。[`spec/managed`](../../spec/managed/) 中的 managed 规范已作为 v1 基线接受，本 Python 实现为参考；
TypeScript/Go 按其移植义务跟进。

## 运行示例

```bash
uv run python examples/managed_dynamic_tree.py
```

[`examples/managed_dynamic_tree.py`](../../examples/managed_dynamic_tree.py) 走完整闭环：
seed → publish → 在请求打开期间新增 Skill 和新 Tool → publish（旧请求保持原代）→
节点历史与 diff → revert → 单事务升级 Tool 绑定 → 重开存储。

## 组装 Host

`ManagedGraphHost` 是官方组装入口（完整代码见英文版与示例）：

* **存储**：本地 ext4/xfs/btrfs 或 Windows NTFS 上的私有目录（strict 耐久），不在 git
  工作树内；Windows 还要求阻止继承、仅属主可访问的 DACL；单进程独占（`writer.lock` 上的 OS
  锁）。运行中不要删除 `writer.lock`：锁文件一旦被删或替换，原 owner 立即停写。
* **注册表（Registry）**：所有已部署的可执行绑定（你发布的代码）。图编辑只能*引用*已注册绑定；
  新代码属于部署，不是编辑。执行按精确绑定契约认证而非按节点，所以运行中新建、绑定到已部署实现的
  Tool 节点无需重启即可执行。
* **策略（Policy）**：`HostPolicy` 承担全部治理决策（身份、准入、审批、权限 fence、请求级授权）。
  `SingleActorPolicy` 对单个本地 actor 全部放行：**仅限开发**。

## 修改树

`host.change(...)` 原子提交一个事务：要么全部生效要么都不生效，只校验最终图（暂态 detach 合法）。
事务携带期望 base revision（过期报 `revision_conflict`）与幂等键（重试返回原 receipt；同键不同内容报
`idempotency_conflict`）。提交前可用 `host.preview(...)` 查看 `definition_diff`、`impact` 与
`protected_dependents`。

### 可修改性

每个节点创建时声明 `mutable`（默认 `"inherit"`，之后不可改）：

* `mutable: false` 是**屏障**：节点及整棵子树冻结。
* 保持 `inherit` 的根节点是**锁定**的。锁定节点保护自己的定义、显示路径、plane、发布/呈现，
  以及其锁定成员的存在与相对顺序。
* `mutable: true` 开放可编辑区域。锁定父节点下的可修改成员可以改名、改描述、改说明、重排
  （不能越过锁定兄弟）、在非屏障父节点之间移动、删除；锁定父节点只能新增 `mutable: true` 的成员。
* 可修改成员/引用/模板目标变化时，Agent 可见上下文随之变化的锁定节点会列在
  `protected_dependents` 中，并计入准入 `impact`：由你的策略决定，不会被一刀切拒绝。

拒绝时返回 `mutation_protected`，附一条结构化诊断（规则 + 节点）：`locked_deletion`、
`creation_policy`、`barrier_move`、`locked_surface`、`effective_mutability`、`barrier_create`、
`locked_create`、`locked_order`。

## 历史、diff 与 revert

`host.node_history(id)` 按稳定 ID 返回改变该节点的 revision；`host.diff(a, b)` 返回五组 diff；
`host.revert(rev, node_ids=[...])` 生成一个**新** revision，按当前保护规则受审，从不改写过去。
要比较*实际执行*的差异，用 `contexture.core.graph.diff` 中的 `execution_diff`（编译清单）和
`surface_diff`（有效说明、呈现、流程）。

## 导出、导入与合并

三者都是框架级操作，只涉及图对象（节点、关系、发布、呈现、binding *引用*），不搬运任何业务数据。
导入与合并都生成普通事务：preview、准入、可修改性保护、CAS 与幂等全部生效。

* `host.export_bundle()` 导出整图，`export_bundle(node_ids=[...])` 导出包含子树；包带自摘要与来源
  （graph id、revision、定义摘要）。离开导出范围的引用列为 `external_refs`，导入时须映射到已有节点。
* `host.bootstrap_from(bundle)` 用整图包初始化新图；`host.import_bundle(bundle, target={"parent_id": ...})`
  导入子树。身份策略默认 `preserve`（与目标图中存活或已删除保留的 ID 冲突即拒绝）；`identity="remap"`
  按 `remap` 映射表和/或 `prefix` 重命名（前缀同时作用于发布名），并改写包内所有引用。
* 可执行部分只带 binding 引用，目标注册表不认可即拒绝（`binding_contract_required`）。
* `host.merge(bundle, base_revision=...)` 三方合并：按稳定 ID 对齐；单侧修改采纳、相同修改接受，
  不同修改与"删除/修改"为冲突；有序关系三方合并成员并保留顺序变化的一侧（两侧都变且不同为冲突）；
  合并结果须通过完整校验，否则报 `structure` 冲突。用 `resolutions={"node:s1:description": "theirs"}`
  显式裁决后重新合并。`base_revision` 是本图历史中的分叉点。
* `host.export_history()` / `fresh.import_history(...)`：历史包经纯重放校验；导入需宿主 `graph.bootstrap`
  权限、对包摘要的审批，且目标图尚不存在。
* CLI：`contexture managed` 读命令 `export`、`export-history`、`plan-import`、`plan-merge`（plan 返回事务，
  再走 `preview`/`commit`），写命令 `import-history`。

## 发布与热切换

`await host.publish()` 用注册表编译 head，在 owner 锁外准备资源，原子切换本地指针。切换前打开的请求
直到结束都保持原代（handler、schema、Catalog）；旧代资源在最后一个请求排空后恰好关闭一次。准备失败
保留最后可用代，原因见 `host.releases.status().last_error`。

升级 Tool 代码：把新代码作为新绑定版本与旧版本并存部署，单事务切换 Tool 的 binding 后 publish。
注册表证据只校验新候选；日志重放不依赖注册表，以后下线 v1 也不会让存储无法打开。

## 运维

* **日志**（标准库 `logging`，不含 payload/密钥）：`contexture.managed.host`、`.owner`、`.journal`、
  `.serving`、`.release`。
* **停写错误**：`commit_unknown`（结果不确定）与 `store_in_use`（另一写者、锁被删）。关闭后重开，
  由恢复确定耐久后缀；不要换幂等键盲目重试。
* **启动与快照**：每 `snapshot_every` 条记录（默认 1000）及正常关闭时写入 owner 快照和日志封印索引
  （`GraphOwner.checkpoint()`）。打开时检查记录序号连续，完整校验封印锚点及其后的全部记录，前缀的
  哈希与回执键取自封印；owner 恢复快照只重放尾部。两者都是可丢弃的 hint，任何不一致即全量校验与重放；
  前缀记录正文在被加载时会按封印哈希重新校验。
* **有界内存**：日志每条记录只常驻一个哈希（及 batch id、回执键）和一个近期正文 LRU；每张图只保留最近
  `history_window` 个 revision（默认 256）。更早 revision、完整历史、节点历史与 revert 按需从日志重建且不常驻；
  watch 游标早于窗口时返回 `resync_required`。
* **离线工具**（服务停止时）：`contexture store doctor --root STORE` 原地只读校验：目录与权限、完整记录链与
  receipt、所有被引用 blob，以及图语义的纯重放（忽略快照）；`--isolated` 额外做隔离恢复（需约 2–3 倍空间）。
  `backup` / `restore --allow-write` 生成并恢复隔离副本，`--max-store-mib` 可放宽备份大小上限。

## 当前限制

* 每个存储单写进程；不支持多副本 rollout 或跨图原子事务。
* 有封印时启动为 O(尾部)+目录列举；无封印（或 `trust_index=False`）时逐条读取校验（约 1ms/条）。
  磁盘随历史增长（每条记录一个文件，暂无压缩归档）。备份是受 `--max-store-mib` 约束的单个内存信封。
* 内置执行 ABI 仅 `python-managed-call/v1`；其他 ABI 需显式提供 `ExecutionAdapter`。
