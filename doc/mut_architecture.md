# Mut Architecture

**Mut — Managed Unified Tree**
A version management protocol built for AI Agents.

---

## 1. 一句话理解 Mut

```
Git 的树 + SVN 的权威 + Agent 的约束
= 每个 Agent 只看到自己的子树世界，提交时服务器自动嫁接回完整树
```

---

## 2. Mut 和 Git 的三条根本区别

Mut 复用了 Git 的大量设计（哈希、Merkle tree、content-addressable 存储），但在三个地方做了根本性的改变：

```
区别 1:  一个 .git/ → N 个 .mut/
         Git: 项目根目录一个 .git/，管整棵树
         Mut: 每个 Agent 的 scope 根目录一个 .mut/，各管一棵子树

区别 2:  合并在客户端 → 合并在服务器
         Git: 冲突时产生标记 <<<< ==== >>>>，需要人解决
         Mut: 服务器自动合并，commit 永不失败

区别 3:  去中心化 → 中心化
         Git: 每个 clone 都是完整仓库，平等对待
         Mut: 服务器是唯一 Source of Truth，负责嫁接各子树
```

其余的——哈希算法、对象存储、Merkle tree、变更检测——**全部站在 Git 肩膀上，不改。**

---

## 3. 底层基石：Content-Addressable 存储与 Merkle Tree

这一层和 Git 完全一样，是 Mut 的数学基础。

### 3.1 哈希 = 文件内容的指纹

```
文件内容                        SHA-256 哈希值
─────────────────────────────────────────────────
"hello world"              →   b94d27b9934d3e08...
"hello world!"             →   7509e5bda0c762d2...  ← 加了一个 ! 号，完全不同
"hello world"              →   b94d27b9934d3e08...  ← 相同内容，永远相同的哈希

三个关键性质:
  1. 相同内容 → 永远得到相同哈希    (确定性 → 用于去重)
  2. 不同内容 → 几乎不可能相同哈希  (抗碰撞 → 用于变更检测)
  3. 从哈希无法反推出内容           (单向性 → 用于安全)
```

### 3.2 Object Store：按哈希存储文件

```
写入: 内容 → 计算 SHA-256 → 前2字符做目录 → 剩余做文件名

  "print('hello')" → SHA-256 → a3f2b9c1e4d5...
  存储到: objects/a3/f2b9c1e4d5...
  
  内容完全相同的文件只存一份（去重）
```

### 3.3 Merkle Tree：目录结构也是哈希

```
root (hash = 整棵树所有内容的"指纹")
├── config.json  (hash = config.json 内容的指纹)
├── src/         (hash = src 下所有文件内容的复合指纹)
│   ├── main.py  (hash = main.py 内容的指纹)
│   └── utils.py (hash = utils.py 内容的指纹)
└── docs/        (hash = docs 下所有文件内容的复合指纹)
    └── api.md   (hash = api.md 内容的指纹)

关键特性:
  改了 main.py 的一个字节 →
    main.py 的哈希变了 →
    src/ 目录的哈希变了 →
    root 的哈希变了 →
    但 docs/ 的哈希没变（它下面什么都没改）

  比较两棵树的 root hash:
    相同 → 整棵树完全一致，一个字节都没变
    不同 → 某处有变化，顺着哈希不同的路径往下找
```

### 3.4 Mut 赋予 Merkle Tree 的新用途

Git 用 Merkle tree 做去重和完整性校验。Mut 在此基础上多了一层——**tree 节点 = 权限边界**：

```
Git 用 Merkle tree 做什么:
  → content-addressable 去重
  → 快速判断两棵树是否相同
  → 完整性校验

Mut 多了什么:
  → ★ 把 tree 节点作为权限边界 ★
  → ★ 每个子树可以有独立的版本历史 ★
  → ★ Agent 只能 "看到" 被授权的子树 ★
  → ★ 子树的哈希可以独立替换（嫁接）★
```

---

## 4. 分支模型：SVN 式中心化

```
Git 的分支:                          Mut 的分支:
  去中心化，每个 clone 都有完整历史       中心化，服务器是唯一 source of truth
  分支是本地操作                        分支在服务器上创建
  合并在客户端                          合并在服务器端
  分支间平等，没有"权威"                 main 是权威，workspace 是临时隔离空间

       A───B───C  (main)                    main: ──1──2──3──5──
            ╲                                           ╲    ↗
             D───E  (feature)                  ws-agentA: ──4──
                                               (服务器端合并，永不失败)
```

Mut 不需要 Git 那种去中心化 DAG——Agent 始终在线，不需要离线分支。中心化意味着服务器掌控一切，合并逻辑在服务器端执行。

但在 **scope 内部**，Agent 的本地 `.mut/` 存储了完整的子树历史，具备去中心化的本地操作能力（status、diff、log 全部可在本地完成）。这是一个 **scope 内去中心化 + scope 间中心化** 的分层模型。

---

## 5. 文件存储架构

**核心原则：服务端和 Agent 端存的东西完全不同。**

### 5.1 三方文件对照

以一个具体项目为例：

```
项目有 3 个文件:
  src/main.py      → print("hello")
  src/utils.py     → def add(a,b): return a+b
  docs/readme.md   → # My App

两个 Agent:
  Agent-A 负责 /src/   (读写)
  Agent-B 负责 /docs/  (读写)
```

三台机器上的文件对比：

```
服务器                        Agent-A 的机器             Agent-B 的机器
/mut-server/my-app/           /workspace-A/              /workspace-B/

├── current/                  ├── main.py      ✓         ├── readme.md    ✓
│   ├── src/                  ├── utils.py     ✓         │
│   │   ├── main.py    ✓     │                          └── .mut/  ← B的身份
│   │   └── utils.py   ✓     └── .mut/  ← A的身份           ├── config.json
│   └── docs/                     ├── config.json            ├── token
│       └── readme.md  ✓         ├── token                  ├── HEAD
│                                 ├── HEAD                   └── manifest.json
├── .mut-server/  ← 服务端元数据   └── manifest.json
│   ├── config.json
│   ├── scopes/
│   ├── history/
│   └── keys/

```

**一句话总结**：
- 服务端有全部文件 + `.mut-server/`（权限、历史、密钥）
- Agent 端只有自己 scope 的文件 + `.mut/`（身份、版本、哈希清单）
- `.mut/` 只在 Agent 端，`.mut-server/` 只在服务器上，互不交叉

### 5.2 服务端文件结构

```
/mut-server/my-app/
│
│  ===== 区域1: 当前最新版的项目文件（原样保存）=====
│
├── current/
│   ├── src/
│   │   ├── main.py                  ← 文件内容就是源代码本身
│   │   └── utils.py
│   └── docs/
│       └── readme.md
│
│  ===== 区域2: 服务端元数据（隐藏目录）=====
│
└── .mut-server/
    │
    ├── config.json                  ← 仓库全局配置
    │
    ├── scopes/                      ← 权限配置（每个 scope 一个文件）
    │   ├── scope-a1b2c3.json        ← Agent-A 的 scope 定义
    │   └── scope-d4e5f6.json        ← Agent-B 的 scope 定义
    │
    ├── history/                     ← 版本历史（每个版本一个 JSON 文件）
    │   ├── 000001.json              ← 第1次提交的元信息
    │   ├── 000002.json              ← 第2次提交的元信息
    │   └── latest                   ← 一个文件，内容就是当前最新版本号
    │
    ├── keys/                        ← 密钥
    │   ├── signing.key              ← 私钥（签发 token 用）
    │   └── signing.pub              ← 公钥（验证 token 用）
    │
    └── locks/                       ← 并发控制（临时锁文件）
```

**各文件的具体内容**：

`config.json`：
```json
{
  "project": "my-app",
  "created": "2026-03-11T10:00:00Z"
}
```

`scopes/scope-a1b2c3.json`：
```json
{
  "id": "scope-a1b2c3",
  "path": "/src/",
  "exclude": [],
  "agents": ["agent-A"],
  "mode": "rw",
  "created": "2026-03-11T10:00:00Z"
}
```

`history/000001.json`：
```json
{
  "id": 1,
  "who": "agent-A",
  "time": "2026-03-11T10:00:00Z",
  "message": "initial project setup",
  "scope": "/src/",
  "changes": [
    { "path": "src/main.py", "action": "add", "hash": "a3f2b9c1..." },
    { "path": "src/utils.py", "action": "add", "hash": "7e8f1a2b..." }
  ]
}
```

`history/latest`：
```
2
```

### 5.3 Agent 端文件结构

Agent 通过 `mut clone` 获得自己 scope 内的文件和一个 `.mut/` 目录。

因为 commit 是本地操作（不联网），`.mut/` 需要存储完整的子树历史：

```
/workspace/                          ← Agent 的工作根目录
│
├── main.py                          ← 从服务器拉下来的工作文件
├── utils.py                         ← （只有 scope 内的文件）
│
└── .mut/                            ← Agent 的"身份证 + 本地仓库"
    ├── config.json                  ← 我连哪个服务器，我的 scope 是什么
    ├── token                        ← 我的身份令牌
    ├── HEAD                         ← 我当前的本地版本指针
    ├── REMOTE_HEAD                  ← 服务器上的最新版本（上次 push/pull 时同步的）
    ├── manifest.json                ← 我 scope 内每个文件的哈希（用于 status）
    ├── objects/                     ← ★ 本地 object store（scope 内文件的所有版本）
    │   ├── a3/f2b9c1...             ← blob / tree 对象
    │   ├── 7e/8f1a2b...
    │   └── ...
    └── snapshots.json               ← ★ 本地提交链（含未 push 的 commits）
```

**各文件的具体内容**：

`config.json`：
```json
{
  "server": "https://server/my-app",
  "scope": "/src/"
}
```

`token`：
```
eyJhbGciOiJFZDI1NTE5In0.eyJhZ2VudCI6ImFnZW50LUEiLCJzY29wZSI6Ii9zcmMvIiwibW9kZSI6InJ3In0.SIGNATURE
```

`HEAD`：
```
4
```

`REMOTE_HEAD`：
```
2
```

HEAD 和 REMOTE_HEAD 不同时，说明有本地 commit 还没 push。

`manifest.json`：
```json
{
  "main.py": "a3f2b9c1e4d5f6a7",
  "utils.py": "7e8f1a2b3c4d5e6f"
}
```

`snapshots.json`（本地提交链）：
```json
[
  {
    "id": 1, "root": "abc123...", "parent": null,
    "who": "agent-A", "message": "initial setup",
    "time": "2026-03-11T10:00:00Z", "pushed": true
  },
  {
    "id": 2, "root": "def456...", "parent": 1,
    "who": "agent-A", "message": "add utils",
    "time": "2026-03-11T10:30:00Z", "pushed": true
  },
  {
    "id": 3, "root": "ghi789...", "parent": 2,
    "who": "agent-A", "message": "fix bug",
    "time": "2026-03-11T11:00:00Z", "pushed": false
  },
  {
    "id": 4, "root": "jkl012...", "parent": 3,
    "who": "agent-A", "message": "refactor",
    "time": "2026-03-11T11:15:00Z", "pushed": false
  }
]
```

`pushed: false` 的 commits 是本地未推送的，下次 `mut push` 时发送到服务器。

### 5.4 为什么 Agent 看不到其他 Agent 的 .mut/

```
Agent-A 的 .mut/  → 只在 Agent-A 的机器上
Agent-B 的 .mut/  → 只在 Agent-B 的机器上
.mut-server/      → 只在服务器上

三台机器，三个不同的隐藏目录:
  不是权限挡住了，是文件压根就不在那里。

Agent-A clone 时从服务器的 current/src/ 拿文件，
服务器的 current/ 里没有任何 .mut/ 目录，
所以 Agent-A 不可能拿到 Agent-B 的身份信息。
```

---

## 6. 权限系统

### 6.1 权限的实现：Scope 文件

服务端 `.mut-server/scopes/` 下的每个 JSON 文件定义一个 scope：

```json
{
  "id": "scope-a1b2c3",
  "path": "/src/",
  "exclude": [],
  "agents": ["agent-A"],
  "mode": "rw"
}
```

- `path`：这个 scope 管辖的目录路径
- `exclude`：排除的子路径（用于处理嵌套 scope）
- `agents`：被授权的 Agent 列表
- `mode`：`r`（只读）或 `rw`（读写）

### 6.2 权限检查逻辑

服务器收到 Agent 的任何请求时，执行以下检查：

```python
def check_permission(token, file_path, action):
    # 1. 验证 token 签名
    agent_id = verify_token(token, PUBLIC_KEY)
    
    # 2. 找到这个 Agent 的 scope 配置
    scope = find_scope_for_agent(agent_id)
    
    # 3. 检查路径在 scope 内
    if not file_path.startswith(scope.path):
        return DENIED
    
    # 4. 检查路径不在 exclude 内
    for excluded in scope.exclude:
        if file_path.startswith(excluded):
            return DENIED
    
    # 5. 检查操作权限
    if action == "write" and scope.mode == "r":
        return DENIED
    
    return ALLOWED
```

核心就是一个**路径前缀匹配 + 排除列表**。

### 6.3 嵌套 Scope 的处理

当两个 Agent 的 scope 存在父子关系时：

```
场景:
  Agent-A → /src/            (整个 src 目录)
  Agent-B → /src/components/  (src 下的子目录)

问题:
  /src/components/ 在 /src/ 下面
  Agent-A 按前缀匹配也能访问 /src/components/
  → 权限重叠
```

**解决：父 scope 自动排除子 scope**

```json
// Agent-A 的 scope（自动添加 exclude）
{
  "path": "/src/",
  "exclude": ["/src/components/"],
  "agents": ["agent-A"],
  "mode": "rw"
}

// Agent-B 的 scope
{
  "path": "/src/components/",
  "exclude": [],
  "agents": ["agent-B"],
  "mode": "rw"
}
```

效果：

```
Agent-A 能访问:  src/main.py ✓  src/utils.py ✓  src/components/Button.js ✗
Agent-B 能访问:  src/components/Button.js ✓  src/main.py ✗
```

每个 Agent 的视野完全不重叠。

### 6.4 鉴权流程

```
Orchestrator（编排层）
│
│ 1. 创建任务时，签发 token:
│    { agent: "agent-A", scope: "/src/", mode: "rw", expires: "..." }
│    用 signing.key 签名
│
│ 2. 把 token + server URL 注入 Agent 环境
▼
Agent
│
│ 3. mut clone https://server/my-app --token eyJ...
│    → 建立 .mut/ 目录，存入 token 和 config
│
│ 4. 后续联网命令自动携带 token
│    mut push → Authorization: Bearer eyJ...
▼
Server
│
│ 5. 用 signing.pub 验证 token 签名（密码学验证，不查数据库）
│ 6. 从 token 解析出 agent_id + scope + mode
│ 7. 路径前缀匹配 + exclude 检查
│ 8. 通过 → 执行操作；拒绝 → 返回 permission denied
│ 9. 记入审计日志
```

**Token 是自包含的**（JWT 风格）：服务器不需要查数据库就能验证。Token 自带 agent_id、scope、permissions、expiry。

---

## 7. 操作流程详解

### 7.1 mut clone（首次连接，只做一次）

Agent 第一次与服务器建立连接，拉取 scope 内的文件和历史：

```
Agent 端:                              服务器端:

$ mut clone                            收到请求:
  https://server/my-app                  ① 验证 token → agent-A
  --token eyJ...                         ② 读 scopes/ → scope = /src/
                                         ③ 读 current/src/ 下的所有文件
                                         ④ 过滤掉 exclude 路径
                                         ⑤ 返回文件 + 历史 + object 数据

收到响应:
  ① 创建工作目录
  ② 写入文件: main.py, utils.py
  ③ 创建 .mut/:
     config.json ← server + scope
     token ← eyJ...
     HEAD ← 当前版本号
     REMOTE_HEAD ← 同 HEAD
     manifest.json ← 每个文件的哈希
     objects/ ← 从服务器拷贝 scope 内的 object store
     snapshots.json ← 从服务器拷贝 scope 内的提交链
```

clone 只做一次。之后通过 `mut pull` 获取增量更新。

### 7.2 mut status（本地完成，不需要网络）

检查哪些文件被修改了：

```
$ mut status

① 读 .mut/manifest.json:
   { "main.py": "a3f2b9...", "utils.py": "7e8f1a..." }

② 扫描工作目录，重新计算每个文件的 SHA-256:
   main.py  → "xxx123..."  ← 和 manifest 里的不一样！
   utils.py → "7e8f1a..."  ← 一样

③ 检查是否有未 push 的 commits:
   HEAD(4) != REMOTE_HEAD(2) → 有 2 个未推送的 commit

④ 输出:
   modified  main.py                   ← 未 commit 的修改
   2 commits not pushed (HEAD:4, remote:2)
```

### 7.3 mut commit（本地操作，不需要网络）

在本地记录一次变更，不联网：

```
$ mut commit -m "fix bug"

全部在本地完成:
  ① 扫描工作目录，构建新的 Merkle tree
  ② 新的 blob / tree 对象写入 .mut/objects/
  ③ 创建新的 snapshot 追加到 .mut/snapshots.json:
     { id: 3, root: "ghi789...", parent: 2,
       who: "agent-A", message: "fix bug",
       pushed: false }                         ← 标记为未推送
  ④ 更新 .mut/HEAD → 3
  ⑤ 更新 .mut/manifest.json

此时服务器完全不知道这个变更。
Agent 可以继续工作，继续 commit，攒多个本地 commit。
```

### 7.4 mut push（联网，推送到服务器）

把本地未推送的 commits 发送到服务器：

```
Agent 端:                              服务器端:

$ mut push

① 读 .mut/snapshots.json               收到请求:
② 找出 pushed: false 的 commits          ① 验证 token → agent-A
③ 收集这些 commits 引用的                 ② 读 scopes/ → scope = /src/
   服务器没有的 objects                    ③ 检查所有文件路径在 scope 内
④ 发送:                                  ④ 检查冲突:
   {                                        REMOTE_HEAD(2) == latest(2)
     "token": "eyJ...",                     → 无冲突，直接写入
     "base_version": 2,                   ⑤ 写入新对象到服务端存储
     "commits": [                         ⑥ 更新 current/src/ 下的文件
       { id: 3, message: "fix bug",      ⑦ 写入 history/ 记录
         objects: [...] },                ⑧ 更新 history/latest
       { id: 4, message: "refactor",     ⑨ 执行子树嫁接（更新全局树）
         objects: [...] }                 ⑩ 返回 { ok: true, version: 4 }
     ]
   }

收到响应:
⑤ 更新 .mut/REMOTE_HEAD → 4
⑥ 标记 snapshots.json 中的 commits 为 pushed: true

如果服务器 latest > base_version（有冲突）:
  → 服务器自动三方合并
  → push 永不失败
```

### 7.5 mut pull（联网，从服务器拉取）

获取其他 Agent（或自己在其他 workspace）对同一 scope 的修改：

```
Agent 端:                              服务器端:

$ mut pull

① 读 .mut/REMOTE_HEAD → 2              收到请求:
② 发送请求:                               ① 验证 token
   { "since_version": 2 }                ② 找出 version 2 之后的变更
                                          ③ 返回新的 objects + snapshots

收到响应:
③ 新 objects 写入 .mut/objects/
④ 新 snapshots 合入 .mut/snapshots.json
⑤ 更新工作目录中的文件
⑥ 更新 .mut/REMOTE_HEAD
⑦ 更新 .mut/HEAD
⑧ 更新 .mut/manifest.json
```

### 7.6 mut log（本地完成）

查看版本历史，直接读取本地 snapshots：

```
$ mut log

读取 .mut/snapshots.json，按时间倒序输出:
  #4  2026-03-11T11:15:00Z  [agent-A]  refactor          (not pushed)
  #3  2026-03-11T11:00:00Z  [agent-A]  fix bug           (not pushed)
  #2  2026-03-11T10:30:00Z  [agent-A]  add utils
  #1  2026-03-11T10:00:00Z  [agent-A]  initial setup
```

### 7.7 mut checkout（本地完成）

恢复到历史版本，从本地 objects 读取：

```
$ mut checkout 1

① 读取 .mut/snapshots.json → snapshot #1 的 root hash
② 从 .mut/objects/ 递归读取该 root hash 对应的整棵子树
③ 覆盖工作目录中的文件
④ 更新 .mut/HEAD → 1
⑤ 更新 .mut/manifest.json
```

### 7.8 命令总结：本地 vs 联网

```
                    需要网络？    作用
─────────────────────────────────────────────
mut clone           是（首次）    首次连接，拉取文件和历史
mut status          否           检查本地修改
mut commit          否           本地记录变更
mut log             否           查看历史
mut checkout        否           回退到历史版本
mut push            是           推送本地 commits 到服务器
mut pull            是           从服务器拉取最新变更
─────────────────────────────────────────────
5 个命令本地完成，2 个命令需要网络。
```

---

## 8. 子树嫁接机制

这是 Mut 服务端最核心的操作——把 Agent 提交的子树替换到完整树中。

### 8.1 嫁接过程

```
当 Agent-A 提交 /src/ 的修改时:

旧的完整树:                     嫁接后的完整树:
root (aaa...)                   root (xxx...) ← 重算
├── config.json (111...)        ├── config.json (111...) ← 不变
├── src/ (bbb...)               ├── src/ (eee...) ← ★ 替换 ★
│   ├── main.py (222...)        │   ├── main.py (444...) ← 变了
│   └── utils.py (333...)       │   └── utils.py (333...) ← 不变
├── docs/ (ccc...)              ├── docs/ (ccc...) ← 不变，哈希复用
└── data/ (ddd...)              └── data/ (ddd...) ← 不变，哈希复用
```

- 没变的子树哈希完全不变，零拷贝复用
- 只有被修改的路径上的哈希需要重新计算（src/ → root）
- 服务器只需做一次子树替换 + 路径上的哈希重算

### 8.2 为什么 Merkle Tree 是实现嫁接的完美结构

```
Agent-A 只知道 /src/ 的子树
Agent-B 只知道 /docs/ 的子树
服务器知道完整树

Agent-A 提交新的 src/ 子树哈希 → 服务器在完整树里替换 src/ 节点
Agent-B 提交新的 docs/ 子树哈希 → 服务器在完整树里替换 docs/ 节点

两个操作互不干扰（修改的是不同节点），可以独立进行
只有在修改同一个子树时才需要合并
```

---

## 9. 冲突解决

### 9.1 分层冲突防御

```
第一层：作用域隔离
  Orchestrator 分配不同 Agent 不同的 scope
  → 从架构上消灭 ~90% 的冲突可能性

第二层：结构化合并
  JSON Path 级 / 行级三方合并
  → 自动解决不同位置的并发修改（~9%）

第三层：领域合并规则
  tags → 并集, counter → 加法, timestamp → max
  → 按数据语义正确合并

第四层：LWW + 保留冲突记录
  真正无法自动解决的 → Last-Writer-Wins 兜底
  保留被覆盖的值，记入审计日志

第五层（可选）：LLM 语义合并
  对高价值字段的真冲突，调用 LLM 理解双方意图做语义合并
```

### 9.2 冲突检测（乐观锁）

```
Agent push 时附带 base_version（我基于哪个版本修改的）
服务器对比 base_version 和 latest:
  相等   → 没有冲突，直接写入
  不相等 → 有人在我之后提交了，需要合并
```

### 9.3 服务器端合并流程

```
Agent-A 基于 v2 修改了 main.py，执行 mut push
但服务器已经到了 v3（Agent-D 也改了 main.py）

服务器:
  base  = v2 的 main.py 内容
  ours  = v3 的 main.py 内容（Agent-D 的修改，已在服务器上）
  theirs = Agent-A push 的 main.py 内容

  执行三方合并:
    如果改的是不同行 → 自动合并 ✓
    如果改的是同一行 → LWW（后提交的覆盖），记入审计日志

  结果: 合并后的内容写入 v4，push 永不失败
```

---

## 10. 安全模型

### 10.1 三层安全边界

```
第一层: 物理隔离
  Agent 的 .mut/ 只在自己的机器上
  Agent clone 时服务器只发 scope 内的文件
  其他 scope 的数据物理上不存在于 Agent 的机器

第二层: Token 验证
  每次请求都验证签名（密码学验证，不查数据库）
  Token 自带 scope + mode + expiry
  Token 过期或被撤销 → Agent 失去所有能力

第三层: 路径检查
  服务器在每次写入前检查: 路径在 scope 内 && 不在 exclude 内
  即使 Token 合法，写 scope 外的路径仍被拒绝
```

### 10.2 Token 生命周期

```
签发: Orchestrator 用 signing.key 签发，绑定 agent_id + scope + expiry
使用: Agent 在 .mut/token 中存储，每次请求自动携带
验证: 服务器用 signing.pub 验证签名，解析权限，无需查库
过期: Token 自带过期时间，到期自动失效
撤销: Orchestrator 可以将 token 加入撤销列表（服务器定期同步）
```

---

## 11. Agent 命令集

### 核心命令（7 个）

```
首次连接:
  mut clone <url> --token <token>     第一次连接服务器，拉取文件和历史

日常工作（本地，不需要网络）:
  mut status                          我改了什么？
  mut commit -m "message"             本地记录变更
  mut log                             查看历史
  mut checkout <version>              回到之前某个版本

同步（需要网络）:
  mut push                            推送本地 commits 到服务器
  mut pull                            从服务器拉取最新变更
```

### 典型工作流

```
$ mut clone https://server/my-app --token eyJ...    ← 只做一次
  ...Agent 工作...
$ mut status                                        ← 看看改了什么
$ mut commit -m "fix parser bug"                    ← 本地记录
  ...继续工作...
$ mut commit -m "add error handling"                ← 再记一笔
$ mut push                                          ← 一次性推送到服务器
```

### 辅助命令

```
mut diff [<id1> <id2>]                对比两个版本差异（本地）
mut show <id>:<path>                  查看某版本中某文件内容（本地）
mut tree <id>                         查看某版本的树结构（本地）
mut stats                             仓库统计
```

---

## 12. 对比总览

```
                    Git              SVN              Mut
─────────────────────────────────────────────────────────────
架构模型            去中心化          中心化             scope 内去中心化
                                                      scope 间中心化

Source of Truth     无（每个clone平等） 服务器            服务器

客户端隐藏目录      .git/             .svn/             .mut/
                   (完整仓库)         (pristine+元数据)  (scope子树的完整仓库)

服务端存储          bare repo         revs + authz      current/ + .mut-server/

树结构              Merkle tree       全局 rev 快照      Merkle tree
                                                      (tree 节点 = 权限边界)

权限粒度            repo 级           path 级           path 级
                   (clone=全部)      (authz 配置)       (scope + exclude)

合并位置            客户端            客户端             服务器

冲突时行为          标记冲突           标记冲突           自动解决
                   需要人解决         需要人解决          push 永不失败

版本标识            SHA-1 全局哈希     全局递增整数        per-scope 递增整数
                                    (泄露活动模式)      (无跨 scope 泄露)

Agent 间隔离        不可能            可以               天然设计目标
                   (clone=全部)      (authz + 部分检出)  (物理上只拿到 scope 的文件)
```

---

## 附录 A：完整文件清单示例

### 服务端

```
/mut-server/my-app/
├── current/                             ← 当前最新版的项目文件
│   ├── src/
│   │   ├── main.py
│   │   ├── utils.py
│   │   └── components/
│   │       ├── Button.js
│   │       └── Header.js
│   └── docs/
│       └── readme.md
└── .mut-server/                         ← 服务端元数据
    ├── config.json                      ← 仓库配置
    ├── scopes/                          ← 权限定义
    │   ├── scope-a1b2c3.json            ← { path: "/src/", exclude: ["/src/components/"] }
    │   ├── scope-d4e5f6.json            ← { path: "/src/components/" }
    │   └── scope-g7h8i9.json            ← { path: "/docs/" }
    ├── history/                         ← 版本历史
    │   ├── 000001.json
    │   ├── 000002.json
    │   ├── 000003.json
    │   └── latest                       ← 3
    ├── keys/                            ← 签名密钥
    │   ├── signing.key
    │   └── signing.pub
    └── locks/                           ← 并发锁（临时文件）
```

### Agent-A（scope = /src/, exclude = /src/components/）

```
/workspace-A/
├── main.py
├── utils.py
└── .mut/
    ├── config.json       ← { server: "...", scope: "/src/" }
    ├── token             ← eyJ...
    ├── HEAD              ← 5 (本地最新)
    ├── REMOTE_HEAD       ← 3 (上次 push 后的服务端版本)
    ├── manifest.json     ← { "main.py": "a3f2...", "utils.py": "7e8f..." }
    ├── objects/           ← /src/ 下文件的所有版本
    │   ├── a3/f2b9c1...
    │   ├── 7e/8f1a2b...
    │   └── ...
    └── snapshots.json    ← 提交链 (含 pushed: false 的本地 commits)
```

### Agent-B（scope = /src/components/）

```
/workspace-B/
├── Button.js
├── Header.js
└── .mut/
    ├── config.json       ← { server: "...", scope: "/src/components/" }
    ├── token             ← eyX...
    ├── HEAD              ← 3
    ├── REMOTE_HEAD       ← 3
    ├── manifest.json     ← { "Button.js": "0d1e...", "Header.js": "9c4b..." }
    ├── objects/
    │   └── ...
    └── snapshots.json
```

### Agent-C（scope = /docs/）

```
/workspace-C/
├── readme.md
└── .mut/
    ├── config.json       ← { server: "...", scope: "/docs/" }
    ├── token             ← eyY...
    ├── HEAD              ← 3
    ├── REMOTE_HEAD       ← 3
    ├── manifest.json     ← { "readme.md": "5a6b..." }
    ├── objects/
    │   └── ...
    └── snapshots.json
```

---

## 附录 B：设计哲学溯源

```
┌──────────────────────────────────────────────────────────────┐
│                                                              │
│   Git 贡献了什么:  Merkle tree（content-addressable 子树结构） │
│                   → 高效去重、子树嫁接、完整性校验              │
│                                                              │
│   SVN 贡献了什么:  中心化 + path-level ACL                    │
│                   → 服务器掌控一切、路径级权限                  │
│                                                              │
│   Mut 的创新:     把两者组合 + Agent 特化                      │
│                   → Merkle tree 节点 = 权限边界               │
│                   → 子树级提交 + 服务器端嫁接合并               │
│                   → per-scope 版本历史（无信息泄露）            │
│                   → commit 本地完成，push 永不失败             │
│                                                              │
│   一句话:  Git 的树 + SVN 的权威 + Agent 的约束               │
│           = 每个 Agent 只看到自己的子树世界                     │
│             提交时服务器自动嫁接回完整树                        │
│                                                              │
└──────────────────────────────────────────────────────────────┘
```

这个设计没有发明新的数据结构。它把 Git 已经证明过的 Merkle tree 用在了一个 Git 自己做不到的场景——因为 Git 是去中心化的，clone 必须拿到一切，树结构再好也无法做权限隔离。Mut 加了一个中心化服务器，Merkle tree 的每个节点就变成了天然的权限切割点。

而在操作模型上，Mut 兼取了 Git 和 SVN 的优势：commit 像 Git 一样是本地操作（快速、离线可用），push/pull 像 SVN 一样通过中心服务器协调（权限管控、自动合并）。Agent 在自己的 scope 内拥有完整的本地仓库，享受 Git 级别的本地操作体验；跨 scope 的协调由服务器负责，享受 SVN 级别的中心化管控。

