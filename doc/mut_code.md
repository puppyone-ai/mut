---

## 附录 C：代码实现架构

### C.1 Git 的代码架构（我们借鉴的对象）

Git 的代码库经过 20 年演进，约 **40 万行 C 代码**（含测试约 100 万行），分为两大层：

```
┌─────────────────────────────────────────────┐
│  Porcelain（瓷层）— 用户直接使用的高级命令     │
│  git add, git commit, git push, git merge    │
│  每个命令一个文件: builtin/add.c 等            │
│  约 60+ 个 builtin 命令                       │
├─────────────────────────────────────────────┤
│  Plumbing（管道层）— 底层原子操作              │
│  hash-object, cat-file, update-index         │
│  提供给 porcelain 层和脚本调用的基础能力         │
├─────────────────────────────────────────────┤
│  Core Libraries — 核心库                      │
│  object store, tree walk, diff, merge,       │
│  pack/unpack, refs, config, transport        │
└─────────────────────────────────────────────┘
```

Git 的模块划分：

```
模块                 职责                            对应源文件
─────────────────────────────────────────────────────────────────
Object Store        blob/tree/commit/tag 的存储和读取   object.c, blob.c, tree.c,
                    content-addressable 存储引擎        commit.c, tag.c, sha1-file.c

Index (Staging)     暂存区管理                         read-cache.c, index.c

Refs                分支/标签指针管理                   refs.c, refs/files-backend.c

Diff                文件差异计算                        diff.c, diffcore-*.c,
                    支持多种算法 (Myers, patience,      xdiff/
                    histogram)

Merge               三方合并引擎                       merge-recursive.c,
                                                      merge-ort.c, ll-merge.c

Tree Walk           目录树遍历                         tree-walk.c, unpack-trees.c

Pack                对象打包压缩 (delta + zlib)         pack-objects.c, pack-*.c

Transport           网络传输协议                        transport.c, remote.c,
                    (git://, ssh://, https://)          connect.c, fetch-pack.c,
                                                      send-pack.c

Config              配置文件读写                        config.c

Builtin Commands    60+ 个用户命令                     builtin/*.c
                    每个命令一个 .c 文件
                    统一入口: cmd_xxx(argc, argv, prefix)

CLI Dispatcher      命令分发器                         git.c
                    解析子命令 → 查找 builtin 表
                    → 调用对应 cmd_xxx 函数
```

Git 的关键架构特征：

```
1. Dispatcher 模式:
   git.c 有一个 commands[] 数组，存所有 builtin 命令的函数指针
   用户输入 "git add" → 查表 → 调用 cmd_add()
   约 100 个命令，线性查找即可

2. 每个命令是独立的翻译单元:
   builtin/add.c → cmd_add()
   builtin/commit.c → cmd_commit()
   builtin/push.c → cmd_push()
   共用底层库，但命令之间独立

3. 命令间互相调用:
   cmd_merge() 内部可以直接调用 cmd_reset()
   传参方式: 构造 argv 数组，调用对方的 cmd_xxx()

4. Plumbing / Porcelain 分离:
   底层命令（plumbing）是稳定的 API
   高层命令（porcelain）可以随时改 UI
   脚本和工具应该调用 plumbing，不依赖 porcelain 的输出格式
```

### C.2 libgit2 的模块划分（更适合 Mut 参考）

libgit2 是 Git 的纯 C 库实现（非命令行工具），模块更清晰：

```
模块                  对应 API               职责
────────────────────────────────────────────────────────
git_repository        repository.h          仓库初始化/打开/关闭
git_blob              blob.h                文件内容对象
git_tree              tree.h                目录树对象
git_commit            commit.h              提交对象
git_tag               tag.h                 标签对象
git_reference         refs.h                分支/标签引用
git_index             index.h               暂存区
git_diff              diff.h                差异计算
git_merge             merge.h               合并
git_checkout          checkout.h            检出
git_clone             clone.h               克隆
git_remote            remote.h              远端管理
git_transport         transport.h           网络传输
git_credential        credential.h          认证
git_config            config.h              配置
git_blame             blame.h               逐行追溯
git_odb               odb.h                 底层对象数据库
git_refdb             refdb.h               底层引用数据库
```

### C.3 Mut 的代码架构设计

Mut 比 Git 简单得多——不需要 DAG 分支、不需要暂存区（index）、不需要多种传输协议。但核心的分层思想完全适用。

#### 分层架构

```
┌─────────────────────────────────────────────────────────┐
│  Layer 4: CLI（命令行接口）                               │
│  mut clone, status, commit, push, pull, log, checkout    │
│  每个命令一个函数，统一入口                                 │
│  入口: main() → dispatch → cmd_xxx()                     │
├─────────────────────────────────────────────────────────┤
│  Layer 3: Operations（业务操作层）                         │
│  clone_op, commit_op, push_op, pull_op                   │
│  组合底层模块完成一个完整业务动作                            │
├─────────────────────────────────────────────────────────┤
│  Layer 2: Core Modules（核心模块层）                       │
│  object_store, tree, snapshot, diff, merge, scope, auth  │
│  每个模块一个职责，互相独立                                 │
├─────────────────────────────────────────────────────────┤
│  Layer 1: Foundation（基础层）                             │
│  hash, fs (文件系统), config, error, transport (HTTP)     │
│  最底层的工具函数，无业务逻辑                               │
└─────────────────────────────────────────────────────────┘
```

#### 模块清单

```
Layer 1: Foundation（基础层）
───────────────────────────────────────────────────────────
hash.py           SHA-256 哈希计算
                  hash_bytes(data) → str
                  hash_file(path) → str

fs.py             文件系统操作封装
                  read_file, write_file, atomic_write
                  mkdir_p, rmtree, walk_dir
                  lock_acquire, lock_release（文件锁）

config.py         配置读写
                  读写 .mut/config.json 和 .mut-server/config.json

error.py          错误类型定义
                  MutError, PermissionDenied, ConflictError,
                  NotARepoError, NetworkError

transport.py      HTTP 客户端
                  post_json, get_json, upload_objects, download_objects
                  处理 token 自动附加、重试、超时
```

```
Layer 2: Core Modules（核心模块层）
───────────────────────────────────────────────────────────
object_store.py   Content-addressable 对象存储
                  put(data) → hash
                  get(hash) → data
                  exists(hash) → bool
                  count() → (n, size)
                  ← 和 Git 的 object database 完全对应

tree.py           Merkle tree 操作
                  scan_dir(path) → tree_hash     扫描目录构建树
                  read_tree(hash) → entries       读取树结构
                  write_tree(entries) → hash       写入树结构
                  tree_to_dict(hash) → dict        展开为嵌套字典
                  restore_tree(hash, target_dir)   从树恢复文件
                  ← 和 Git 的 tree object 完全对应

snapshot.py       快照（提交）管理
                  create(root_hash, parent, who, message) → snapshot
                  load_all() → list[snapshot]
                  get(id) → snapshot
                  get_unpushed() → list[snapshot]  获取未推送的快照
                  mark_pushed(ids)                  标记为已推送
                  ← 和 Git 的 commit object 类似，但线性链而非 DAG

diff.py           差异计算
                  diff_trees(hash1, hash2) → list[change]
                  diff_working(manifest, workdir) → list[change]
                  ← 树级 diff（added/deleted/modified）
                  ← 文件级 diff（行级差异，用于展示和合并）

merge.py          合并引擎
                  three_way_merge(base, ours, theirs) → result
                  line_merge(base_lines, our_lines, their_lines)
                  json_merge(base_obj, our_obj, their_obj)
                  lww_resolve(ours, theirs, timestamp) → winner
                  ← Mut 特有：多策略合并 + LWW 兜底

scope.py          Scope 权限管理
                  load_scope(agent_id) → scope_config
                  check_permission(scope, path, action) → bool
                  filter_by_scope(file_list, scope) → filtered
                  ← Mut 特有：路径前缀匹配 + exclude 排除

auth.py           认证模块
                  sign_token(agent_id, scope, mode, expiry) → token
                  verify_token(token, public_key) → claims
                  ← JWT 风格的签名/验证

manifest.py       哈希清单管理
                  generate(workdir) → dict          扫描目录生成清单
                  compare(old, new) → changes       对比两个清单
                  load() → dict                     读取 manifest.json
                  save(manifest)                    写入 manifest.json
```

```
Layer 3: Operations（业务操作层）
───────────────────────────────────────────────────────────
clone_op.py       mut clone 的完整流程
                  1. 验证 token
                  2. 从服务器下载 scope 内的文件和历史
                  3. 创建工作目录和 .mut/
                  4. 初始化 object_store, snapshots, manifest

commit_op.py      mut commit 的完整流程
                  1. 扫描工作目录 → 构建 Merkle tree
                  2. 新对象写入 object_store
                  3. 创建 snapshot（pushed: false）
                  4. 更新 HEAD 和 manifest

push_op.py        mut push 的完整流程
                  1. 找出未推送的 snapshots
                  2. 收集服务器缺少的 objects（哈希协商）
                  3. 发送 objects + snapshots 到服务器
                  4. 服务器验证权限 → 写入 → 嫁接子树
                  5. 如有冲突 → 服务器自动合并
                  6. 更新 REMOTE_HEAD

pull_op.py        mut pull 的完整流程
                  1. 向服务器请求 REMOTE_HEAD 之后的变更
                  2. 下载新 objects
                  3. 合入 snapshots
                  4. 更新工作目录
                  5. 更新 HEAD, REMOTE_HEAD, manifest

status_op.py      mut status 的完整流程
                  1. 读取 manifest
                  2. 扫描工作目录计算哈希
                  3. 对比输出变更列表

log_op.py         mut log 的完整流程
                  读取本地 snapshots.json，格式化输出

checkout_op.py    mut checkout 的完整流程
                  1. 找到目标 snapshot 的 root hash
                  2. 从 object_store 恢复文件
                  3. 更新 HEAD 和 manifest
```

```
Layer 4: CLI（命令行接口）
───────────────────────────────────────────────────────────
cli.py            命令行入口 + dispatcher
                  解析子命令 → 调用对应 cmd_xxx()

                  def main():
                      parser = argparse.ArgumentParser(prog="mut")
                      sub = parser.add_subparsers(dest="command")
                      # 注册所有子命令...
                      args = parser.parse_args()
                      dispatch(args)

                  def dispatch(args):
                      commands = {
                          "clone": cmd_clone,
                          "status": cmd_status,
                          "commit": cmd_commit,
                          "push": cmd_push,
                          "pull": cmd_pull,
                          "log": cmd_log,
                          "checkout": cmd_checkout,
                      }
                      commands[args.command](args)
```

```
服务端额外模块:
───────────────────────────────────────────────────────────
server.py         HTTP API 服务
                  POST /clone      → clone 处理
                  POST /push       → push 处理（含嫁接）
                  POST /pull       → pull 处理
                  中间件: token 验证 + scope 权限检查

graft.py          子树嫁接引擎（服务端专用）
                  1. 读取当前全局 root tree
                  2. 替换指定 scope 路径的子树哈希
                  3. 重算路径上的所有 tree 哈希
                  4. 更新全局 root
```

#### 文件结构

```
mut/
├── cli.py              ← Layer 4: 命令行入口 + dispatcher
│
├── ops/                ← Layer 3: 业务操作层
│   ├── clone_op.py
│   ├── commit_op.py
│   ├── push_op.py
│   ├── pull_op.py
│   ├── status_op.py
│   ├── log_op.py
│   └── checkout_op.py
│
├── core/               ← Layer 2: 核心模块层
│   ├── object_store.py
│   ├── tree.py
│   ├── snapshot.py
│   ├── diff.py
│   ├── merge.py
│   ├── scope.py
│   ├── auth.py
│   └── manifest.py
│
├── foundation/         ← Layer 1: 基础层
│   ├── hash.py
│   ├── fs.py
│   ├── config.py
│   ├── error.py
│   └── transport.py
│
├── server/             ← 服务端（独立部署）
│   ├── server.py       ← HTTP API
│   └── graft.py        ← 子树嫁接引擎
│
└── tests/              ← 测试
    ├── test_object_store.py
    ├── test_tree.py
    ├── test_snapshot.py
    ├── test_diff.py
    ├── test_merge.py
    ├── test_scope.py
    ├── test_clone.py
    ├── test_commit.py
    ├── test_push_pull.py
    └── test_e2e.sh
```

#### 代码量估算

```
                    Git (C)           Mut (Python)
─────────────────────────────────────────────────────
基础层               ~10,000 行         ~300 行
核心模块             ~80,000 行         ~1,500 行
业务操作             ~50,000 行         ~800 行
CLI                 ~20,000 行         ~200 行
服务端               N/A               ~500 行
测试                 ~300,000 行        ~1,000 行
─────────────────────────────────────────────────────
总计                 ~400,000 行        ~4,300 行

Mut 是 Git 的 ~1%。原因:
  1. Python vs C（表达力差 5-10 倍）
  2. 砍掉了: DAG 分支、暂存区、多协议、packfile、submodule 等
  3. 只实现 7 个命令 vs Git 的 60+
  4. 合并逻辑简化（LWW 兜底 vs Git 的复杂合并策略）
```

#### 实现优先级

```
Phase 1: 本地版本管理（可以不联网使用）
  ✅ foundation/hash.py
  ✅ foundation/fs.py
  ✅ core/object_store.py
  ✅ core/tree.py
  ✅ core/snapshot.py
  ✅ core/manifest.py
  ✅ core/diff.py
  ✅ ops/commit_op.py
  ✅ ops/status_op.py
  ✅ ops/log_op.py
  ✅ ops/checkout_op.py
  ✅ cli.py (init, commit, status, log, checkout)
  → 这就是当前 mut.py 已经实现的部分，需要重构为分层结构

Phase 2: 客户端-服务端通信
  foundation/transport.py
  foundation/config.py
  core/auth.py
  server/server.py
  server/graft.py
  ops/clone_op.py
  ops/push_op.py
  ops/pull_op.py
  cli.py (clone, push, pull)

Phase 3: 权限与多 Agent
  core/scope.py
  server.py 中间件（权限检查）
  嵌套 scope 的 exclude 逻辑

Phase 4: 合并引擎
  core/merge.py（三方合并 + LWW）
  server/graft.py（冲突时的自动合并）

Phase 5: 生产化
  错误处理完善
  日志和审计
  性能优化（大文件、大量文件）
  packfile 压缩（可选）
```
