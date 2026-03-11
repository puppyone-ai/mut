# Agent 版本管理技术选型报告

## 1. 背景与问题

PuppyOne (ContextBase) 的核心场景是：**多个 Agent 通过文件系统（bash / MCP）协同操作一个云端文件夹**。这带来了一个关键的架构问题——

> 当 Agent 修改文件夹时，我们应该用什么版本管理模型？

这不是一个新问题。版本管理已经有 50+ 年的历史，从 1972 年的 SCCS 到 2005 年的 Git，人类积累了丰富的经验。但 Agent 场景有几个独特约束，使得没有任何一个现成方案可以直接套用。

### Agent 与人类开发者的关键差异

| 维度 | 人类开发者 | Agent |
|------|----------|-------|
| 信任程度 | 被信任（签了保密协议） | 不被完全信任（背后的使用者可能窃取数据） |
| 冲突解决能力 | 可以手动解决冲突标记 | 不能（工作流中断 = 任务失败） |
| 数据可见范围 | 通常看到整个 repo | 按文件夹层级做硬隔离 |
| 工作模式 | 交互式（编辑 → 思考 → 提交） | 批量式（读取 → 处理 → 写回） |
| 离线需求 | 有时需要 | 不需要（Agent 始终在线） |
| 并发规模 | 几十人 | 可能几十个 Agent 同时工作 |

---

## 2. 历史方案谱系

版本管理的历史可以用一条线串起来，每一代都是对前一代痛点的直接回应：

```
1972  SCCS    → 发明了增量存储（delta），锁模型，单文件粒度
                "手动拷贝太蠢了，我要记录变更历史"

1982  RCS     → 反向增量（最新版 O(1)），仍是锁模型，单文件
                "SCCS 太慢，而且不是自由软件"

1990  CVS     → copy-modify-merge（范式转移！），多文件，client-server
                "锁模型让团队瘫痪，我们要并发"

2000  SVN     → 原子提交，目录版本管理，全局 revision number
                "CVS 的理念对了，但实现太烂"

2005  Git     → 去中心化，content-addressable，DAG 历史
                "中心化模型扛不住 Linux 内核的开发规模"
```

同时，另一条平行演进线在实时协作领域：

```
1989  OT      → 操作变换，字符级实时协作（Google Docs）
2011  CRDT    → 无冲突复制数据类型，数学保证收敛（Figma）
2016  Notion  → Block + Transaction 模型，LWW 兜底
```

### 并发哲学只有三种

这两条线虽然看起来独立，但底层的并发控制哲学完全相通——所有系统都落入以下三种之一：

| 哲学 | 版本管理代表 | 实时协作代表 | 核心思路 |
|------|------------|------------|---------|
| **悲观锁** | SCCS, RCS | Word 文件锁 | 同一时间只许一个人改 |
| **乐观合并 + 中心裁决** | CVS, SVN | OT, Notion Transaction | 各自改，服务器合并/裁决 |
| **去中心化合并** | Git, Darcs | CRDT, Notion Offline | 各自完整副本，自动收敛 |

### 版本管理单元的演进

```
SCCS/RCS:  单个文件       → 无法表达"一组相关修改"
CVS:       文件集合       → 底层仍 per-file，commit 不原子
SVN:       整棵目录树     → 原子提交，树级快照（关键突破）
Git:       整棵目录树     → 同 SVN，但去中心化
Agent:     per-node + 树  → 按需粒度，兼顾文件级和文件夹级
```

### Git 催化事件

2005 年 4 月，BitKeeper 撤销对 Linux 社区的免费授权。Linus Torvalds 用约两周时间写出 Git 第一版，设计目标：分布式、快、强完整性（SHA-1）、支持大规模非线性开发。

---

## 3. 三种候选架构

### 3.1 Git

**架构模型**：去中心化。每个 clone 持有完整仓库（全部历史、全部文件）。

**核心优势**：
- Content-addressable 存储（SHA-1 去重，极高效）
- 树级原子快照
- Diff/blame/revert 工具链最成熟
- LLM 天然会用 Git

**核心问题**：
- **数据隔离不可能**：clone = 拿到一切，sparse-checkout 可被绕过
- **冲突需要人介入**：merge conflict 会阻塞 Agent 工作流
- **合并在客户端**：客户端可能没有足够上下文（看不到其他 Agent 的 scope）
- 分布式能力（离线、多远端）对 Agent 多余

### 3.2 SVN

**架构模型**：中心化。客户端只持有工作副本 + pristine copy，历史在服务器。

**核心优势**：
- 路径级权限控制（authz 原生支持）
- 部分检出（真正的物理隔离）
- 原子树级提交 + 全局线性版本号
- 客户端持有数据最少

**核心问题**：
- **冲突仍需人介入**：和 Git 一样会产生冲突标记
- **合并仍在客户端**：和 Git 同样的上下文缺失问题
- **全局 revision number 泄露信息**：Agent 可推断出其他 scope 的活动模式
- 生态逐渐衰退，嵌入性差（需要独立服务器进程）

### 3.3 Agent-native（自研）

**架构模型**：中心化 API 服务。Agent 通过 API / MCP / 虚拟文件系统访问，服务器是唯一 source of truth。

**核心设计原则**：
- **commit 永不失败**（Mut Protocol）
- **服务器端合并**（Agent 无需处理冲突）
- **per-node 版本号**（无跨 scope 信息泄露）
- **结构化合并**（JSON path 级 / 行级，非纯文本级）
- **分层冲突防御**（作用域隔离 → 结构合并 → 领域规则 → LWW + 保留 → 可选 LLM 合并）

---

## 4. 版本管理架构技术选型对比

### 一、基础架构

| 维度 | Git | SVN | Agent-native |
|------|-----|-----|-------------|
| 架构模型 | 去中心化（P2P） | 中心化（C/S） | 中心化（C/S） |
| Source of truth | 无唯一权威（每个 clone 都是完整的） | 服务器 | 服务器 |
| 客户端持有的数据 | 全部历史 + 全部文件 | 当前版本 pristine copy + 元数据 | 仅 Agent 有权访问的当前版本 |
| 本地隐藏目录 | `.git/`（包含一切） | `.svn/`（仅元数据 + pristine） | 无（纯 API 驱动，或极薄的本地缓存） |
| 传输协议 | Git protocol / SSH / HTTPS | SVN protocol / HTTP (WebDAV) | REST API / WebSocket / MCP |
| 访问方式 | CLI（git 命令）/ 文件系统 | CLI（svn 命令）/ 文件系统 | API + 可选的虚拟文件系统挂载 |

### 二、版本标识与历史

| 维度 | Git | SVN | Agent-native |
|------|-----|-----|-------------|
| 版本标识 | SHA-1 哈希（40 字符） | 全局递增整数（r1, r2, r3） | per-node 递增整数（每个文件/文件夹独立计数） |
| 历史存储位置 | 客户端本地 | 服务器 | 服务器 |
| 历史结构 | DAG（有向无环图，支持分支合并） | 线性（全局单条线） | per-node 线性 + folder snapshot |
| 历史查询需要网络？ | 不需要 | 需要 | 需要 |
| 信息泄露风险 | 高（clone 含全部数据） | 中（全局 revision number 泄露活动模式） | 低（per-scope 版本号，无跨 scope 信息） |

### 三、提交与快照

| 维度 | Git | SVN | Agent-native |
|------|-----|-----|-------------|
| 提交原子性 | 原子（单个 commit 对象） | 原子（事务） | 原子（Mutation + 事务） |
| 提交粒度 | 整棵树 | 整棵树 | 可选：单文件 / 文件夹 / 整棵树 |
| 提交需要网络？ | 不需要（commit 本地）; push 需要 | 需要 | 需要 |
| Changeset / 变更组 | 天然支持（每个 commit 就是一组） | 天然支持（每个 revision 就是一组） | 需要显式设计（workspace + changeset） |
| 文件夹级快照 | 天然（每个 commit 含 tree 对象） | 天然（每个 revision 是整棵树） | folder_snapshots 表 |
| 内容去重 | 极好（content-addressable, SHA 去重） | 中等（skip-delta） | 取决于实现（可用 content hash 去重） |

### 四、权限与数据隔离

| 维度 | Git | SVN | Agent-native |
|------|-----|-----|-------------|
| 路径级读权限 | 不支持（clone = 拿到一切） | 原生支持（authz 配置） | 原生支持（connection_accesses 表） |
| 路径级写权限 | 不支持 | 原生支持 | 原生支持 |
| 部分检出 | 有但不安全（sparse-checkout 可绕过） | 原生支持（真正的物理隔离） | 原生支持（API 只返回有权限的数据） |
| Agent 间硬隔离 | 不可能（除非拆成多个 repo） | 可以（服务器端强制 ACL） | 原生设计目标 |
| 数据最小暴露 | 差（全部数据在本地） | 好（只有 checkout 的部分） | 最好（按需 API 调用，零本地存储） |

### 五、冲突解决与合并

| 维度 | Git | SVN | Agent-native |
|------|-----|-----|-------------|
| 合并发生在哪里 | 客户端 | 客户端 | 服务器 |
| 合并算法 | 行级三方合并（recursive / ort） | 行级三方合并 | 结构化合并（JSON path 级 / 行级） |
| 冲突时行为 | 标记冲突，需要人解决 | 标记冲突，需要人解决 | 自动解决（三方合并 + LWW 兜底），永不失败 |
| Commit 能否失败？ | 能（冲突时阻塞） | 能（冲突时阻塞） | 不能（commit 永远成功） |
| 冲突需要人介入？ | 是 | 是 | 否（自动解决 + 审计日志记录） |
| LWW 支持 | 不原生（需 `-X theirs`） | 不原生 | 原生（冲突原子级 LWW） |
| 领域合并规则 | 不支持 | 不支持 | 可扩展（不同字段不同合并策略） |
| LLM 辅助合并 | 不适用 | 不适用 | 可选（严重冲突时调用 LLM） |

### 六、分支与工作空间

| 维度 | Git | SVN | Agent-native |
|------|-----|-----|-------------|
| 分支代价 | 极低（40 字节指针） | 低（cheap copy） | 低（workspace 记录 = DB 行） |
| 分支模型 | DAG 分支 | 目录拷贝 | 轻量 Workspace（base_snapshot + overlay） |
| 工作隔离 | 完全隔离（每个分支独立） | 部分隔离（分支是目录拷贝） | 完全隔离（workspace 间互不可见） |
| 合并回主线 | git merge（可能冲突） | svn merge（可能冲突） | merge_workspace（永不失败） |

### 七、回滚与恢复

| 维度 | Git | SVN | Agent-native |
|------|-----|-----|-------------|
| 单文件回滚 | `git checkout <sha> -- file` | `svn merge -r N:M file` | rollback_file(node_id, version) |
| 文件夹回滚 | `git checkout <sha> -- dir/` | `svn merge -r N:M dir/` | rollback_folder(folder_id, snapshot_id) |
| 整棵树回滚 | `git reset --hard <sha>` | `svn merge -r N:M .` | 通过 folder snapshot 恢复 |
| 选择性回滚 | `git revert <sha>`（反转特定 commit） | `svn merge -r N:M`（反转特定 revision） | 按 changeset 回滚 |
| 回滚保留历史？ | 是（revert 创建新 commit） | 是（反向 merge 创建新 revision） | 是（回滚创建新版本号，版本号只增不减） |

### 八、审计与可追溯性

| 维度 | Git | SVN | Agent-native |
|------|-----|-----|-------------|
| 谁改了什么 | `git log` / `git blame` | `svn log` / `svn blame` | audit_logs 表（operator_type + operator_id） |
| 操作类型追踪 | commit message 里写 | commit message 里写 | 结构化（checkout / commit / rollback / conflict） |
| 冲突记录 | 无（冲突在本地解决，不记录） | 无 | 有（lww_details, conflict 审计日志） |
| Agent 身份追踪 | 通过 git config user.name | 通过 svn 用户名 | 原生（operator_type=agent, operator_id=agent_uuid） |
| 合并策略记录 | 无 | 无 | 有（merge_strategy 字段：direct / json_path / line_diff3 / lww） |

### 九、实时协作与通知

| 维度 | Git | SVN | Agent-native |
|------|-----|-----|-------------|
| 实时通知 | 无（需要 poll 或 webhook） | 无（需要 poll） | WebSocket / SSE 推送 |
| 多 Agent 感知 | 不原生 | 不原生 | 可设计（Agent 订阅 scope 内的变更通知） |
| 乐观更新 | 无 | 无 | 可设计（类 Notion 的乐观应用） |

### 十、工程实现

| 维度 | Git | SVN | Agent-native |
|------|-----|-----|-------------|
| 实现复杂度 | 直接用（已有） | 需要搭服务器 | 需要从头构建 |
| 生态成熟度 | 极成熟 | 成熟 | 无现成方案 |
| 存储引擎 | 极优（content-addressable + packfile） | 好（FSFS + skip-delta） | 需要自建（可借用 Git 引擎） |
| Diff 实现 | 极优（内置多种算法） | 好 | 需要自建（可借用 Git diff） |
| Agent 学习成本 | 中（LLM 天然会 Git） | 中 | 低（为 Agent 定制的 API） |
| 可嵌入性 | 好（libgit2 库） | 差（需要独立服务器进程） | 最好（就是 API 服务本身） |

---

## 5. 综合评分

| 维度 | Git | SVN | Agent-native |
|------|:---:|:---:|:-----------:|
| 单 Agent 版本管理 | A | A | A |
| 多 Agent 协作 | C | B | A |
| 数据隔离/安全 | F | B | A |
| 冲突自动解决 | D | D | A |
| 审计追溯 | B | B | A |
| 实时协作通知 | D | D | A |
| 回滚能力 | A | A | A |
| 存储效率 | A | B | B~A |
| Diff 能力 | A | B | B |
| 工程实现成本 | A（现成） | B | D（需要构建） |
| 生态/工具链 | A | B | D |

### 评分说明

- **Git** 在存储引擎、diff 能力、生态成熟度上无可匹敌，但数据隔离是致命伤（F 级），冲突处理也不适合 Agent
- **SVN** 在权限和隔离上远好于 Git，但冲突处理同样需要人介入，且生态在衰退
- **Agent-native** 在所有 Agent 核心需求（隔离、冲突、审计、协作）上都是 A 级，但代价是需要从头构建，没有现成方案

---

## 6. 结论与推荐

### 核心判断

**CRDT / OT 不适合**。Agent 是 batch write 模式（读 → 处理 → 写回），不是字符级实时编辑。且我们有中心服务器，不需要去中心化的数学收敛保证。

**Git 不适合做直接方案**。数据隔离这一项就是致命伤——Agent 不被完全信任，但 Git 的 clone 会暴露全部数据。冲突需要人介入也是硬伤。

**SVN 的模型更接近**。中心化、路径级权限、部分检出都匹配。但冲突处理仍需人介入，合并仍在客户端，不满足 Agent 场景。

**Agent-native 是正确方向**，核心创新点在于：
1. **服务器端合并**（解决客户端上下文缺失问题）
2. **commit 永不失败**（解决 Agent 工作流中断问题）
3. **per-node 版本号**（解决信息泄露问题）

### 推荐架构

```
┌──────────────────────────────────────────────────────────┐
│                   Agent-native 版本管理                    │
│                                                          │
│  设计哲学:  Notion 的合并语义（永不失败）                    │
│  权限模型:  SVN 的 path-level ACL（路径级隔离）             │
│  存储引擎:  可借用 Git 的 content-addressable（高效去重）    │
│  版本标识:  per-node 递增整数（无信息泄露）                  │
│  合并位置:  服务器端（唯一有全局上下文的地方）                │
└──────────────────────────────────────────────────────────┘
```

### 冲突解决架构：三种模式

对于 Agent 场景，冲突解决有三种架构选择：

| 架构 | 合并位置 | Agent 感知 | 语义正确性 | 延迟 | 成本 |
|------|---------|-----------|-----------|------|------|
| A: 服务器自动合并 | 服务器 | 不知道冲突 | 低（LWW） | 零 | 零 |
| B: Agent 重做 (Rebase) | Agent 端 | 知道，重新执行 | 高 | 高（重做） | 高（LLM 调用） |
| C: Agent 语义合并 | Agent 端 | 知道，LLM 合并 | 最高 | 中 | 中（LLM 调用） |

**推荐组合**：默认走 A（服务器自动），严重冲突升级到 B（通知 Agent 重做）。

### 分层冲突防御策略

```
第一层：作用域隔离
  Orchestrator 分配不同 Agent 不同的可写范围
  → 从架构上消灭 ~90% 的冲突可能性

第二层：结构化合并
  JSON Path 级 / Markdown 行级三方合并
  → 自动解决不同位置的并发修改（~9%）

第三层：领域合并规则
  tags → 并集, counter → 加法, timestamp → max
  → 按数据语义正确合并

第四层：LWW + 保留冲突记录
  真正无法自动解决的 → LWW 兜底
  但保留被覆盖的值，记入审计日志

第五层（可选）：LLM 语义合并
  对高价值字段的真冲突，调用 LLM 理解双方意图做语义合并
```

---

## 7. 我们现有系统的状态

| 组件 | 状态 | 对应能力 |
|------|------|---------|
| Mut Protocol（单一写入入口） | ✅ 已实现 | commit 永不失败 |
| 乐观锁（current_version） | ✅ 已实现 | 并发检测 |
| 三方合并（JSON path / 行级） | ✅ 已实现 | 结构化合并 |
| LWW 兜底 | ✅ 已实现 | 冲突自动解决 |
| per-file 版本历史（file_versions） | ✅ 已实现 | 文件级版本管理 |
| 文件夹快照（folder_snapshots） | ✅ 已实现 | 树级快照 |
| 审计日志（audit_logs） | ✅ 已实现 | 操作可追溯 |
| 节点级权限（connection_accesses） | ✅ 已实现 | 数据隔离 |
| Workspace 隔离 | ❌ 待建设 | Agent 独立工作空间 |
| Changeset 分组 | ❌ 待建设 | 多文件原子回滚 |
| 领域合并规则 | ❌ 待建设 | 按数据语义合并 |
| Content-addressable 存储 | ❌ 待建设 | 高效去重 |
| 实时变更通知 | ❌ 待建设 | Agent 感知他人修改 |

### 下一步优先级建议

1. **Workspace 隔离**（高优先）— Agent 在独立空间工作，完成后 merge 回主线
2. **Changeset 分组**（高优先）— 多文件修改作为原子单元，支持整组回滚
3. **领域合并规则**（中优先）— 为不同字段类型定义合并策略
4. **实时变更通知**（中优先）— Agent 订阅 scope 内的变更推送
5. **Content-addressable 存储**（低优先）— 优化存储效率和 diff 性能

---

## 附录 A：并发哲学演进全景

版本管理和实时协作看似两个独立世界，但底层并发哲学完全相通：

```
                    版本管理                    实时协作
                    (文件粒度, 低频)             (字符粒度, 高频)

悲观锁              SCCS / RCS                 Word 文件锁
                    "锁住, 排队等"              SharePoint Check-out

中心裁决            CVS / SVN                  OT / Notion Transaction
                    "各自改, 服务器合并"         "各自改, 服务器变换/排序"

去中心化            Git / BitKeeper            CRDT / Notion Offline
                    "各自完整副本, 平等合并"      "各自完整状态, 自动收敛"
```

### 对应关系

- SVN 就是低频、粗粒度的 Notion Transaction
- OT 就是高频、细粒度的 CVS copy-modify-merge
- CRDT 就是高频、细粒度的 Git
- 它们不是独立的两个世界，是同一个哲学谱系在不同粒度和频率上的表达

### 主要产品的协议选择

| 产品 | 协议 | 粒度 | 中心服务器 | 离线支持 |
|------|------|------|-----------|---------|
| Google Docs | OT | 字符级 | 是（必须） | 不支持 |
| Notion (早期) | Transaction + LWW | Block 属性级 | 是（权威） | 不支持 |
| Notion (离线) | CRDT | 字符级 + Block 结构级 | 是（同步中心） | 支持 |
| Figma | CRDT | 对象属性级 | 是（同步中心） | 支持 |
| Linear | Sync Engine | 记录级 | 是 | 支持 |

## 附录 B：Git vs SVN 详细架构对比

### 存储对比

```
Git 本地:
  .git/
  ├── objects/     ← 全部历史（blob + tree + commit）
  ├── refs/        ← 分支/标签指针
  ├── HEAD         ← 当前分支
  └── index        ← 暂存区

SVN 本地:
  .svn/
  ├── wc.db        ← SQLite 元数据
  ├── pristine/    ← 当前版本的干净副本（仅一份）
  └── tmp/         ← 临时文件

SVN 服务器:
  /var/svn/repos/
  ├── db/revs/     ← 每个 revision 的数据（全部历史在这里）
  ├── db/revprops/ ← 每个 revision 的属性
  └── conf/authz   ← 路径级权限配置
```

### 网络依赖对比

```
操作              Git          SVN          Agent-native
─────────────────────────────────────────────────────────
提交              本地          需要网络      需要网络
查看历史          本地          需要网络      需要网络
Diff              本地          本地          需要网络
Revert            本地          本地          需要网络
分支              本地          需要网络      需要网络
合并              本地          本地          服务器端
Push/同步         需要网络      N/A          N/A
```

### Git 能做 SVN 的一切吗？

```
                    Git 能做？  原生程度    对 Agent 重要？
原子提交            ✅          原生       是
树级快照            ✅          原生       是
分支/合并           ✅          原生       中等
历史/diff/blame     ✅          原生       是
离线工作            ✅          原生       不重要
分布式              ✅          原生       不需要
──────────────────────────────────────────────
部分检出            ⚠️          别扭       非常重要
路径级权限          ❌          不支持     非常重要
全局 revision 号    ⚠️          可模拟     有用但有泄露风险
文件锁              ⚠️          需 LFS     某些场景有用
大二进制文件        ⚠️          需 LFS     中等
```

## 附录 C：关键术语

| 术语 | 含义 |
|------|------|
| LWW | Last-Writer-Wins，冲突时后写入的值覆盖先写入的值 |
| OT | Operational Transformation，通过变换并发操作保证一致性 |
| CRDT | Conflict-free Replicated Data Type，数学保证无冲突的数据结构 |
| DAG | Directed Acyclic Graph，Git 的历史结构 |
| Pristine copy | SVN 客户端保存的文件"干净"副本，用于本地 diff |
| Content-addressable | 按内容哈希存储和寻址，相同内容只存一份 |
| Three-way merge | 三方合并：base（共同祖先）、ours（我的修改）、theirs（对方修改） |
| Mut Protocol | PuppyOne 的统一写入协议，所有变更通过 commit(mutation) |
| Workspace | Agent 的隔离工作空间，完成后合并回主线 |
| Changeset | 一组相关的多文件修改，作为原子单元提交和回滚 |
| Sparse checkout | Git 的部分检出功能（可被绕过，不是安全边界） |
| authz | SVN 的路径级权限配置文件 |
| Skip-delta | SVN 的增量存储格式（每隔 N 个版本存一个完整快照） |
| Packfile | Git 的压缩存储格式（多个 object 打包压缩） |
| Weave | SCCS 的交织增量存储格式（所有版本的行交织在一个文件中） |
| Reverse delta | RCS 的反向增量存储（最新版存全文，旧版存反向 diff） |
