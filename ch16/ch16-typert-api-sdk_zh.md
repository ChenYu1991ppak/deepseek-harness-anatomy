# 第 16 章 typert / api / sdk：跨进程类型化 RPC 运行时

> 过界的只有契约，不是对象；类型编译成图，调用按图校验。

## 本章回答的问题

- 第 5 章的 capability seam 解决了进程内复用——进程外的消费者（CLI、Web、自动化脚本）怎么调用宿主进程里的能力？
- 一个方法的类型契约（收什么参数、返回什么），怎么跨进程、并在边界上被校验？
- 新增一个导出方法，为什么不用手写路由、序列化器和客户端桩？
- `typert`、`api`（网关）、`sdk`、`acp` 在这条链路里各管什么？

第 15 章把散件装配成了可复用的产品形态，也预告了下一站是 typert——看装配好的服务如何被安全地取值与调用。本章把这句话往前推一步：不只是进程内安全取值，而是**跨进程安全调用**。

第 2 章搭出了最小 agent-loop 闭环：一条消息进、一条回复出，Session 与 AgentLoop 全在同一进程里跑。本章在闭环之上新增的是**边界**——把闭环里那些能力（执行 shell、读会话、调模型）经一份类型化契约交给子进程、浏览器与编辑器。闭环本身一行不改：变的只是「谁来调、从哪儿调」。

本章依赖第 1、2、5 章：第 1 章的 `Context` / `Service`（本章教学代码继续经 `sys.path` 复用 `ch01/code/cordis.py`）；第 2 章的最小闭环，是本章要送出去的那些能力的宿主；第 5 章的 capability seam 三角色——**提供者、实现者、消费者**（详见第 5 章）。seam 解决了「一个能力如何在进程内被定义、实现、消费」，本章把「消费」这一角色延伸到进程外。下文调用描述符里的 `service` 字段，正是第 5 章 Service Definition 里那个 `ctx.<key>`。

本章 `ch16/code/` 五个文件：`typert.py`（类型图 + 注册表）、`gateway.py`（RPC 网关）、`sdk.py`（JSON-RPC SDK）、`main.py`（可运行入口）、`bad_example.py`（反例）。

## 1. 能力在进程里，调用方在进程外

harness 宿主进程里装配好了一批服务：执行 shell、管理会话、调用模型。可这些能力的消费者往往不在进程里——CLI 工具、Web 后端、自动化脚本都想调 `shellExecutor.run`。没有专门机制时，最直觉的做法是手写一座桥（`ch16/code/bad_example.py`，可运行）：

```python
class ShellHost:
    """宿主进程里的一个服务（业务方）。"""

    def run(self, request):
        return {"exitCode": 0, "stdout": f"[stub] 执行: {request['command']}"}


def hand_written_bridge(host, endpoint, payload):
    """手写转发：一个方法一个分支，没有任何校验 —— 契约只在作者脑子里。"""
    if endpoint == "/api/shell/run":
        return host.run(payload)   # payload 原样当 request：形状错了要到业务代码才炸
    raise KeyError(endpoint)       # 新增方法必须同步在这里加分支
```

运行 `python3 bad_example.py` 输出：

```text
== 反例：手写跨进程桥接的三个痛 ==
痛 1：拼错参数名不被边界拦下，业务代码深处才炸 -> KeyError('command')
痛 2：非 JSON 值不被边界拦下，序列化时才炸 -> TypeError: Object of type function is not JSON serializable
痛 3：新增方法没同步到桥接，调用时才发现 -> KeyError('/api/shell/start')
```

三个痛：**契约只在作者脑子里**——方法收什么参数只有作者知道，拼错参数名不被边界拦下，要到业务代码深处才炸（`KeyError('command')`）；**没有边界校验**——不可 JSON 序列化的值（比如一个函数）能混进参数，直到要序列化传输时才炸；**手写转发一对一**——新增方法必须同步在桥上加一条分支，忘了就调用时才发现。

三个痛的根源相同：**方法的类型契约没有变成数据**。契约（参数名、类型、结果形状）若能被编译成可序列化、可查询的结构，边界就能照着它校验，路由也不用手写。这正是本章机制链要做的事。

## 2. 整体地图

**先看整体再拆解**：本章的源模块连成一条链——

```mermaid
flowchart LR
    SRC["服务源码"] --> GEN["typert/generator<br/>类型分析"]
    GEN -->|类型图 + 调用描述符| REG["typert/registry<br/>运行时注册表"]
    REG --> GW["api/gateway<br/>RPC 网关：拦截 /api"]
    GW --> SDK["sdk<br/>stdio 上的 JSON-RPC"]
    GW --> REM["Remote 服务实例<br/>officeToPdf（§7.1）"]
    AG["宿主 agent / Session"] -.->|独立插件，不经网关| ACP["acp<br/>自动化桥（§7.2）"]
```

图中：生成器把服务契约编译成**类型图**并注册进注册表；网关对照注册表校验并派发每一次跨进程调用。网关有两个下游：SDK 把它搬上 stdio（§6），`officeToPdf` 则是一个直接继承基类、用 `@Remote` 导出方法的服务实例（§7.1）。**ACP 连的是 agent 与 Session，不是网关**——它是独立插件，用自己的协议把 agent 整段接管（§7.2），图里用虚线区分。下面按 `typert → 网关 → SDK → Remote 实例 → ACP` 的顺序逐节拆解；`@Remote` 与 `typertRemote` 绑定在 §7.1 给出定义，§5.3 先用到它们时会标出前向指引。

## 3. typert 类型图：把契约编译成载体无关的模型

### 3.1 类型图是什么

**typert** 是负责「运行时类型 + 对象服务」的源模块，核心产物是**类型图**：类型分析产出的、与载体无关的中间模型，由 `TypeGraph`（图整体，`packages/typert/generator/src/model.ts:440`）与 `TypeNodeModel`（图中节点，`model.ts:356`）构成。`TypeNodeModel` 是一个 20 个成员的联合类型：`keyword` / `literal` / `object` / `union` / `mapped` / `template-literal` 等，基本覆盖了 TS 类型表达式的全部形状（逐条见 `model.ts:356-394`）。

为什么不直接用源语言的类型？因为源语言类型只活在编译期与进程内：跨不了进程、运行时查不到、边界没法校验。类型图把类型从源码里抽出来、固化成**数据**——是数据，就能被注册、传输、校验。这是问题 1 的第一步消解：契约从作者脑子里搬进可查询的表。

负责这一步的是生成器 `WorkspaceTypertGenerator`（`packages/typert/generator/src/workspace.ts:39`）：`discover` 发现源文件（`workspace.ts:60`）、`generate` 做类型分析（`workspace.ts:77`）、`validateExport` 校验 `./typert` / `./remote` 导出（`workspace.ts:116`）。加载器侧由 typert-loader 插件扫描 `./typert` 导出（`TYPERT_HOST_EXPORT = './typert'`，`packages/typert/loader/src/index.ts:40`）、校验 manifest（`validateTypertManifest`，`packages/typert/loader/src/index.ts:90`），并要求严格 codec（`requireStrictCodec`，`packages/typert/loader/src/index.ts:310`）。

**双面编译**：同一份方法契约会编译出两面——宿主面产出**调用描述**（宿主方法怎么被调用），客户端面产出 Remote 描述（`TypertClientRemote`，`packages/typert/protocol/src/types.ts:620`，用 `$mount` 为客户端挂载宿主侧契约）；远端结果统一包在 `RemoteResult<T>`（`packages/typert/protocol/src/types.ts:130`）里。本章聚焦宿主面；客户端面是它的镜像——§6 的 SDK 是手写的一个客户端，§7.1 的 `officeToPdf` 则给出宿主面的写法，它的 Client 描述符由 `api/remotes` 的客户端装配挂载：`officeToPdfRemote` 与其余 Remote 贡献一起被逐个 `ctx.remote.$mount`（`packages/api/remotes/src/client/index.ts:205-211`）。

### 3.2 InvocationDescriptor：一次调用的载体无关描述

类型图输出的最小单元是 **InvocationDescriptor（调用描述符）**：一个被导出方法的载体无关描述。源码摘录如下（TS，`packages/typert/protocol/src/types.ts:388-442`；`readonly` 表示字段只读，`kind: 'direct'` 这类是 TS 的字面量类型，意为「该字段只能取这个字符串」）：

```ts
export interface InvocationDescriptor {
  readonly id: string                 // 全局稳定生成标识
  readonly service: string            // 拥有该方法的 Cordis service key
  readonly namespace: string          // wire 命名空间，默认等于 service key
  readonly method: string             // 公共实例方法名
  readonly implementation?: string    // 导出方法名是别名时的实际成员
  readonly mode?: 'stream'            // 缺省为 unary；stream 时逐项投递返回值
  readonly invocation:
    | { readonly kind: 'direct' }
    | { readonly kind: 'context'; readonly context: string; readonly wire: string; readonly codec: TypertCodec }
  readonly scope?: { readonly context: string; readonly wire: string }  // 消费方 Context 对某个直接 lookup 参数的投影
  readonly parameters: readonly InvocationParameterDescriptor[]
  readonly uplink?: { readonly codec: TypertCodec }                     // client→host 的同流回传项
  readonly cancellation?: { readonly parameter: 'signal' }              // 传输取消
  readonly result: TypertCodec
  readonly description?: string                                         // 方法文档
  readonly sourceLocation?: InvocationSourceLocation
}
```

逐字段读：`service` 是拥有该方法的 Cordis service key（对应第 5 章 Service Definition 的 `ctx.<key>`）；`namespace` 是 wire 命名空间，默认等于 service key——**wire**：两个进程之间的序列化传输线，上面只走 JSON 文本，不走活对象；`method` 是公共实例方法名；`parameters` 是参数描述符序列；`result` 是结果的编解码策略；`invocation` 区分直接调用与 context 调用。参数的 `source: 'lookup'` 标记表示该参数是**宿主对象**——活在宿主进程里的活实例（如一个会话对象），无法序列化；wire 上只传它的**身份字段**（一个代表它的字符串），网关调用前再换回对象（§5.4）。

两种 key 容易混淆，一并定义：**typertKey** 是 schema 的标识，形如 `<pkg>#<name>`（`typertKey`，`packages/typert/registry/src/service.ts:52`）；**typertEndpoint** 是可调用端点的标识，形如 `<namespace>/<method>`（`typertEndpoint`，`packages/typert/registry/src/service.ts:71`）。前者回答「什么类型」，后者回答「调用谁」。

教学版把描述符建模为两个 frozen dataclass，保留真实 14 个字段中的 6 个（`ch16/code/typert.py`）：

```python
# ---------- 调用描述符：一条导出方法的载体无关描述 ----------

@dataclass(frozen=True)
class InvocationParameterDescriptor:
    """导出方法的单个参数：名字 + 指向类型图的 schema + 是否为宿主对象。"""

    name: str
    schema: str                 # 指向类型图里的类型节点（<pkg>#<name> 形式）
    lookup: str | None = None   # None = 普通 JSON 参数（真实 source='json'）；否则是 lookup 种类（如 "agent"）：
                                # wire 上只传身份字段，网关调用前换回宿主对象


@dataclass(frozen=True)
class InvocationDescriptor:
    """一条被导出的 RPC 方法的载体无关描述 ↔ InvocationDescriptor（packages/typert/protocol/src/types.ts:388-442）。

    [教学简化] 真实字段还有 implementation / mode / invocation / scope / uplink /
    cancellation / description / sourceLocation；教学版保留 id / service / namespace /
    method / parameters / result。
    """

    id: str                    # 全局稳定生成标识
    service: str               # 拥有该方法的 Cordis service key（对应第 5 章 Service Definition 的 ctx.<key>）
    namespace: str             # wire 命名空间，默认等于 service key
    method: str                # 公共实例方法名
    parameters: tuple          # InvocationParameterDescriptor 序列
    result: str                # 结果 codec：CODEC_STRICT / CODEC_SRC_JSON
```

[教学简化] 未保留字段见上方 docstring 所列。编解码策略对应 `TypertCodec`（`packages/typert/protocol/src/types.ts:341`），教学版定义为两个常量：

```python
# 编解码策略 ↔ TypertCodec（packages/typert/protocol/src/types.ts:341）
CODEC_STRICT = "strict"        # 严格：按 schema 校验（真实用 Zod v4，packages/typert/loader/src/index.ts:310）
CODEC_SRC_JSON = "src-json"    # 弱解析：只做 JSON 安全校验，用于无生成器场景
```

### 3.3 生成器：从服务类到类型图

真实生成器靠编译器分析 TS 源码；教学版用 `inspect` 反射 Python 签名，并把类属性 `typert_exports` 当作「./typert 导出声明」——对应 `validateExport` 的导出校验（`workspace.ts:116`）。`generate_contribution` 的入口与导出校验（`ch16/code/typert.py`，模块级函数）：

```python
def generate_contribution(cls, package, namespace=None):
    """教学版生成器：反射类的导出方法，产出类型图 + 调用描述符。

    ↔ WorkspaceTypertGenerator：discover（packages/typert/generator/src/workspace.ts:60）
    → generate（:77）→ validateExport（:116，校验 ./typert / ./remote 导出）。
    [教学简化] 教学版用 inspect 反射 Python 签名，并把类属性 typert_exports
    当作「./typert 导出声明」：没声明就拒绝生成，对应真实 validateExport 的校验。
    返回 (contribution, graph)：前者提交给注册表，后者供观察类型图形状。
    """
    exports = getattr(cls, "typert_exports", None)
    if not exports:
        raise TypertError(f"{cls.__name__} 未声明 ./typert 导出（缺 typert_exports）")
```

导出校验之后，逐个方法反射签名：每个参数按注解推一个 `TypeNode`（lookup 参数记为宿主对象），组装出描述符，最后连同类型图一起返回。下面是函数体的尾部节选（缩进 8 格；完整代码在 `typert.py`）：

```python
        invocations.append(InvocationDescriptor(
            id=typert_key(package, f"{cls.__name__}.{method_name}"),
            service=service_key, namespace=namespace, method=method_name,
            parameters=tuple(params), result=CODEC_STRICT,
        ))
    return TypertContribution(package, dict(graph.declarations), invocations), graph
```

返回值里的 `TypertContribution` 即**贡献（contribution）**：一个包一次性提交给注册表的全部内容（包记录 + schemas + 描述符）。生产端这样写（消费端是 §4 的注册表与 §5 的网关）。`ch16/code/main.py` 里的 `ShellExecutorStub` 呼应第 5 章 capability seam 的 shellExecutor：

```python
class ShellExecutorStub(Service):
    """第 5 章 capability seam 里 shellExecutor 的教学 stub。

    [教学决策] 不 import 第 5 章代码；shell seam 的词汇类型
    （ShellExecRequest / ShellRunResult）用普通 dict 表示，保持本章自包含。
    """

    service_key = "shellExecutor"
    typert_exports = ("run",)  # ./typert 导出声明：哪些方法要跨进程

    def __init__(self, ctx):
        super().__init__(ctx, self.service_key)

    def run(self, request: dict) -> dict:
        return {"exitCode": 0, "stdout": f"[stub] 执行: {request['command']}"}
```

运行 `main.py` 段 1，生成器的产出（完整输出见 §8）：

```text
== 段 1：生成器 —— 把服务契约编译成类型图 ==
类型图节点数: 1，命名声明数: 1
调用描述符: id=demo-shell#ShellExecutorStub.run
  service=shellExecutor endpoint=shellExecutor/run
  参数=[('request', 'demo-shell#ShellExecutorStub.run.request')] 结果 codec=strict
```

`request` 参数被编译成类型图节点，schema 以 typertKey 形式（`demo-shell#ShellExecutorStub.run.request`）指向它；端点是 typertEndpoint 形式（`shellExecutor/run`）。[教学简化] 真实 `TypeNodeModel` 有 20 个 variant；教学版 `TypeNode` 的 variant 只取 `primitive` / `object`，用 `detail` 细分。

类型图解决了问题 1 的前一半：契约变成数据、可以跨进程。但数据不会自己生效——运行时谁持有这些描述符？插件卸载时怎么保证「要么全注册、要么全不注册」？这就是注册表。

## 4. TypertRegistry：运行时注册表

### 4.1 注册表持有什么

**TypertRegistry（运行时注册表）**：类型图的运行时持有者，是一个 Cordis 服务（`@typert service typert`，`packages/typert/registry/src/service.ts:508`）。它持有五份存储（`service.ts:511-515`）：`localStore` / `remoteStore` 两套 `DescriptorStore`（endpoint → 描述符，类定义在 `service.ts:111`）、`lookupStore`（`service.ts:220`）、`contextStore`（`service.ts:342`）、`resourceStore`（`service.ts:452`）；此外还有 `packages`（包记录）与 `schemas`（typertKey → 类型节点）两张表。描述符校验由模块级 `validateInvocation`（`service.ts:729`）承担。对外只露契约形状的五个视图：`local` / `remotes` / `lookups` / `contexts` / `resources`（`service.ts:531-558`）。[教学简化] 教学版只保留 local 一套，lookup 移到 §5.4 讲。

### 4.2 原子注册：effect 内提交，disposer 回滚

注册的关键设计是**原子性**：一条贡献要么整体可见、要么整体不可见——注册到一半，网关可能拿到描述符却查不到它的 schema。真实实现把提交放进 Cordis 的 effect（第 1 章：立即执行、可登记清理函数的副作用），并 yield 清理函数（`register`，`packages/typert/registry/src/service.ts:568`）：

```ts
register(contribution: TypertContribution): TypertDisposer {
  const packageRecord = this.validatePackage(contribution)
  const schemaRecords = this.validateSchemas(contribution)
  const invocations = contribution.invocations
  this.localStore.validate(invocations)
  const owner = {}
  const { schemas, packages, localStore } = this
  return this.ctx.effect(function* () {
    packages.set(packageRecord.key, packageRecord)
    for (const record of schemaRecords) schemas.set(record.key, record)
    localStore.commit(owner, invocations)
    yield () => { /* 撤销本 contribution 的全部记录 */ }
  })
}
```

（TS：`this.ctx.effect(function* () {...})` 注册一个立即执行的 effect；生成器 `yield` 出的清理函数就是 disposer，卸载时调用。摘录省略了清理体里的三行删除与 `, 'typert.register()'` 这个 effect 名字；真实代码见 `packages/typert/registry/src/service.ts:568-592`。）三道校验——包记录、schemas、local 描述符——全部通过后才进 effect 提交。教学版同构复刻（`ch16/code/typert.py`，`TypertRegistry.register`，校验部分）：

```python
    def register(self, contribution):
        """原子注册一个 generated contribution ↔ register（service.ts:568）。

        先做三道校验（对应 validatePackage / validateSchemas / localStore.validate），
        全部通过后才进 ctx.effect 提交：一次性写入 packages / schemas / local，
        并返回 disposer——调用它即撤销本 contribution 的全部记录
        （对应真实 effect 里 yield 的清理函数）。
        """
        if contribution.package in self.packages:
            raise TypertError(f"包已注册: {contribution.package}")
        for descriptor in contribution.invocations:
            endpoint = typert_endpoint(descriptor.namespace, descriptor.method)
            if endpoint in self._local:
                raise TypertError(f"endpoint 已被占用: {endpoint}")
            for param in descriptor.parameters:
                if param.schema not in contribution.schemas:
                    raise TypertError(f"缺少 schema: {param.schema}")
```

校验通过后进入 `commit`：三张表一次性写入，并返回 **disposer**（第 1 章：effect 的清理函数，调用即撤销本次注册）。下面是 `commit` 的节选，末尾的嵌套 `dispose` 闭包与 `return dispose` 省略：

```python
        def commit():
            # 走到这里说明校验全过：三张表一次性写入，不出现「写了一半」
            self.packages[contribution.package] = {
                "schemas": len(contribution.schemas),
                "invocations": len(contribution.invocations),
            }
            for key, node in contribution.schemas.items():
                self.schemas[key] = node
            for descriptor in contribution.invocations:
                endpoint = typert_endpoint(descriptor.namespace, descriptor.method)
                self._local[endpoint] = descriptor
                self._seen.add(endpoint)
```

`commit` 返回的 `dispose` 逐表撤销本贡献的记录（包、schema、local 描述符）；随后 `return self.ctx.effect(commit)` 把 `commit` 登记为 effect，`effect` 的返回值——也就是那个 `dispose`——交给调用方。`main.py` 段 2 验证「注册—重复拒绝—dispose 回滚—重新注册」（完整输出见 §8）：

```text
== 段 2：注册表 —— 原子注册与回滚 ==
在册 endpoint: ['sessionService/summarize', 'shellExecutor/run']
重复注册被拒: TypertError: 包已注册: demo-shell
dispose 后 shellExecutor/run -> None
hasSeen 仍记得: True
重新注册后: ['sessionService/summarize', 'shellExecutor/run']
```

注意 `hasSeen 仍记得: True`：dispose 后描述符从 local 移除，但「曾经注册过」的事实保留在 `has_seen` 里。网关两处会消费它：§5.1 的认领判断，以及 §5.3 的 strict 路径——描述符被撤走而 `hasSeen` 仍为真时，网关拒绝退回 SRC 弱解析，直接报 `definition-unavailable`。

### 4.3 依赖倒置的注册表契约

注册表还有一个值得点名的设计：**依赖倒置的注册表契约**。网关这类消费者不 `import` typert 包，只依赖一份契约接口——`TypertRegistryContract` 把能力分成五段：`local`（本地描述符）、`remotes`（远端对象）、`lookups`（宿主对象查找）、`contexts`（context 调用）、`resources`（资源种类默认 wire 字段），`packages/typert/protocol/src/types.ts:939-945`；再用 TS 的 `declare module` 扩展把契约挂到 cordis 的 `ctx` 上（`packages/typert/protocol/src/types.ts:947-957`）：

```ts
export interface TypertRegistryContract {
  readonly local: TypertLocalRegistry
  readonly remotes: TypertRemoteRegistry
  readonly lookups: TypertLookupRegistry
  readonly contexts: TypertContextRegistry
  readonly resources: TypertResourceRegistry
}

declare module '@deepseek-ai/cordis' {
  interface Context {
    typert: TypertRegistryContract
    readonly invocation: RemoteInvocation | undefined
  }
}
```

（TS：`declare module '@deepseek-ai/cordis'` 意为「给 cordis 包的 `Context` 类型声明扩展，补一个指向注册表契约的 `typert` 字段」。第二个字段 `invocation` 让服务方法能用 `this.ctx.invocation` 读到「这次调用」；网关在 §5.2 取接收者时把它绑上去（`packages/api/gateway/src/index.ts:858` 的 `receiverContext.extend({ invocation })`）。）这意味着任何服务都能经 `ctx.typert` 读注册表，而谁也不必依赖 typert 包的实现——与第 5 章 seam「消费者只依赖契约」的精神一致。教学版网关只消费 `local` 段（`get` / `has_seen`），lookup 段由网关自己持有（§5.4）。

注册表解决了问题 1 的后一半：契约有了运行时持有者，注册原子、可回滚。但契约仍是死数据——调用真正跨进程时，谁来对照描述符校验参数？谁把身份字段换回宿主对象？这就是网关。

## 5. RPC 网关：一次 invoke

### 5.1 拦截 /api

**RPC 网关**：`api/gateway` 包的 `TypertGatewayService`（`packages/api/gateway/src/index.ts:214`），带 `static inject = ['typert', 'remoteAccess']`——注入的正是 §4.3 的注册表契约，外加一层访问控制服务 `remoteAccess`（本章不展开）。构造时它在 `connection.rpc` 上拦截全部 `/api` 端点（`packages/api/gateway/src/index.ts:248-255`）：

```ts
constructor(ctx: Context, config: Config) {
  super(ctx, 'typertGateway')
  ctx.on('internal/service', () => { this.srcClaims = undefined })
  ctx.inject(['connection'], (connectionCtx) => {
    connectionCtx.connection.rpc.intercept(
      '/api',
      endpoint => this.claimsEndpoint(endpoint),
      (endpoint, payload, signal, peer) => this.dispatchRpc(endpoint, payload, signal, peer),
    )
  })
  // …随后还有第二个 inject(['connection', 'webServer'])，挂 WebSocket mux（略）
}
```

（TS：箭头函数即匿名函数；`intercept(prefix, claim, dispatch)` 登记一个拦截器。摘录略去了构造函数后半段的 WebSocket 流多路复用与配置校验。）三个细节：`claim` 决定是否认领、`dispatch` 决定如何处理；两个函数都多了 `peer` 参数——每个 Peer 有自己的身份，网关在派发前先看「谁在调」。

`claimsEndpoint`（`packages/api/gateway/src/index.ts:380`）是 claim 的实现。它先放行网关自有的单段端点 `$events/result`（`packages/api/gateway/src/stream-protocol.ts:13`，转发事件的结果回执）；其余端点必须恰为两段，且注册表里 `get` 得到、或 `hasSeen`（§4.2 留的钩子）为真、或 SRC 绑定集合里有它，才认领。`dispatchRpc`（`packages/api/gateway/src/index.ts:491`）是 dispatch 的实现：`$events/result` 走事件回执分支，其余一律走 `invokeRpc`（`packages/api/gateway/src/index.ts:771`）。

`[教学简化]` 教学版 `RpcConnection`（`gateway.py`）复刻的是「先 claim 后派发」和「无人认领即拒」，跳过了 Peer 身份、访问控制与 `$events` 那两条网关内部流端点——所以教学版里无人认领的端点直接以 `invocation-unavailable` 拒绝。网关自己也以契约形式暴露给消费者（`TypertGateway` 接口，`packages/api/gateway/src/types.ts:154`；`declare module` 挂 `ctx.typertGateway`，`packages/api/gateway/src/types.ts:194-199`）。

### 5.2 invoke 五步

网关的入口是 `invoke`（`packages/api/gateway/src/index.ts:424`），它把实际工作交给 `prepareInvocation`（`:793`）：解析描述符（`resolveDescriptor`，`:878`）→ 校验参数（`assertExactArguments`，`:1630`）→ 取接收者（`resolveReceiverContext`，`:1014`）→ 执行（`invokePrepared`，`:428`）→ 结果编码（`encodeRpcResult`，`:1123`）。

「取接收者」不是简单地 `ctx.get(service)`。`resolveReceiverContext` 返回的是一个 **Context**：`kind: 'direct'` 时就是网关自己的 ctx，`kind: 'context'` 时先经 `contexts.getHost` 解析出调用方指定的那个 Context。然后 `prepareInvocation` 把它 `extend({ invocation })`（`packages/api/gateway/src/index.ts:858`），再从这个派生 Context 上取服务实例——这就是 §4.3 里 `ctx.invocation` 的落点：服务方法内部用 `this.ctx.invocation` 拿到的，正是本次调用的上下文。时序如下（strict 路径主干；SRC 分支见 §5.3，lookup 分支见 §5.4）：

```mermaid
sequenceDiagram
    participant C as 外部进程
    participant G as TypertGatewayService
    participant R as TypertRegistry（ctx.typert）
    participant S as 接收者服务（ctx.<service>）
    C->>G: invoke {namespace, method, args}
    G->>R: local.get(endpoint) 查描述符
    R-->>G: InvocationDescriptor
    G->>G: assertExactArguments(args, descriptor)
    G->>G: resolveReceiverContext + extend({invocation}) 取接收者 Context
    G->>S: 从派生 Context 取服务实例并执行
    S-->>G: result
    G->>G: encodeRpcResult 按 result codec 编码
    G-->>C: result / GatewayError（稳定错误码）
```

教学版 `invoke`（`ch16/code/gateway.py`，`TypertGatewayService` 方法）：

```python
    def invoke(self, request):
        """↔ invoke（packages/api/gateway/src/index.ts:424）：解析描述符 → 校验参数 → 取接收者 →
        执行 → 校验结果。

        五步全部通过才返回业务结果；任一步失败都抛带稳定错误码的 GatewayError。
        """
        endpoint = typert_endpoint(request["namespace"], request["method"])
        descriptor = self._resolve_descriptor(endpoint)
        if descriptor is None:
            raise GatewayError("definition-unavailable", f"解析不到 {endpoint} 的描述符")
        self._assert_exact_arguments(request.get("args", {}), descriptor)
        receiver = self._resolve_receiver(descriptor)
        kwargs = {}
        for param in descriptor.parameters:
            kwargs[param.name] = self._resolve_parameter(param, request["args"])
        result = getattr(receiver, descriptor.method)(**kwargs)
        # 结果 codec 校验：教学版 strict / src-json 统一为 JSON 安全检查
        self._assert_json_value(result, "结果", code="result-invalid")
        return result
```

`main.py` 段 3 走一遍 strict 路径（完整输出见 §8）：

```text
== 段 3：网关 strict 路径 —— 一次 /api 调用 ==
调用 /api/shellExecutor/run -> {'exitCode': 0, 'stdout': '[stub] 执行: ls -l'}
拼错参数名被边界拦下 -> arguments-invalid: 期望参数 ['request']，实际 ['reqeust']
多传参数被边界拦下 -> arguments-invalid: 期望参数 ['request']，实际 ['request', 'verbose']
网关错误码表共 21 个（见 GATEWAY_ERROR_CODES）
```

拼错（`reqeust`）与多传（`verbose`）都被 `assertExactArguments`（`packages/api/gateway/src/index.ts:1630`）以 `arguments-invalid` 拦下：它先按 `parameters` 的 wire 字段集合比多传、再比漏传，两处都通过才放行。对照 §1 痛 1：同样的错误，手写桥接要到业务代码深处才炸出 `KeyError('command')`，这里在边界就被稳定错误码接住。这一步的另一半是 **JSON 安全边界**（`assertJsonValue`，`packages/api/gateway/src/index.ts:1704`；`decode`，`packages/api/gateway/src/index.ts:1680`）：`decode` 先跑 codec（strict 走 `codec.create().parse`），再调 `assertJsonValue` 递归检查值里没有函数、非有限数、环、稀疏数组——过不了就报 `input-invalid`。反复出现的只有两个码：`arguments-invalid`（形状不对）与 `input-invalid`（值不能过线），下文按裸名引用。只有可 JSON 序列化的值能上 wire，痛 2 就此消解。

`[教学简化]` 教学版 `_assert_json_value`（`ch16/code/gateway.py`）对输入值默认报 `arguments-invalid`，真实网关按阶段分开：入参 decode 失败报 `input-invalid`，结果编码失败报 `result-invalid`。

### 5.3 两条描述符解析路径：strict 与 src-json

`invoke` 的第一步有两条路径，适用场景不同：

- **strict 路径**：查注册表 local（`resolveDescriptor`，`packages/api/gateway/src/index.ts:878`）。描述符来自生成器，schema 齐全，结果按严格 codec 校验。段 3 的 `shellExecutor/run` 走的就是它：

```python
    result = connection.call("/api/shellExecutor/run", {"request": {"command": "ls -l"}})
```

- **SRC 弱解析路径**：注册表查不到时走 `resolveSrcDescriptor`（`packages/api/gateway/src/index.ts:891`），再交给 `srcDescriptor`（`packages/api/gateway/src/index.ts:919`）。它先遍历所有 service，挑出带 `typertRemote` 绑定的那些（这个绑定是什么，§7.1 给出定义）。然后在候选对象的 `@Remote` 标记里按方法名匹配：命中多个报 `ambiguous-endpoint`，一个不中报 `invocation-unavailable`。接着 `srcDescriptor` **现场反射参数名**拼出描述符——`methodParameterNames`（`packages/api/gateway/src/index.ts:1586`）用 `Function.prototype.toString` 读函数源码文本、提取括号里的参数列表；教学版改用 `inspect.signature`。最后，每个名字拿去 `typert.lookups.definitions()` 里撞 lookup provider，撞上就标成 lookup 参数。结果 codec 降为 `src-json`——真实里是 `mode: 'src-json'`，教学版用常量 `CODEC_SRC_JSON` 表示（`ch16/code/typert.py`）。路径名里的 src 即 source（源码侧）：描述符不是生成器预编译的，而是从源码侧绑定现场推导的。段 5 的调用：

```python
    result = connection.call("/api/diagnostics/ping", {"tag": "hello"})
```

运行段 5（完整输出见 §8）：

```text
== 段 5：SRC 弱解析 —— 无注册表，现场反射出描述符 ==
调用 /api/diagnostics/ping -> {'pong': 'hello'}
未标记方法被拒 -> method-unavailable: diagnostics/internal 未标记 @remote
```

`DiagnosticsBridge.internal` 没标 `@remote`（教学版标记函数 `remote` 给方法挂 `__typert_remote__` 属性——给函数挂属性是合法的 Python 用法），网关以 `method-unavailable` 拒绝：弱解析路径的导出边界同样严格。

`[教学简化]` 这里的码名与真实网关不同：真实 `resolveSrcDescriptor` 在「一个候选都没有」时抛 `gateway/invocation-unavailable`（`packages/api/gateway/src/index.ts:909-911`）；`gateway/method-unavailable` 只在 marker 存在、但原型链上找不到同名方法时抛（`packages/api/gateway/src/index.ts:1600-1604`）。

### 5.4 lookup provider：身份字段换回宿主对象

`SessionService.summarize` 的 `agent` 参数是宿主对象——JSON 带不上它。网关的做法是 wire 上只传身份字段，调用前再把身份换回对象：`resolveParameter`（`packages/api/gateway/src/index.ts:1062`）读描述符里的 `source: 'lookup'`，拿着 wire 值去 **lookup provider**（`LookupStore`，`packages/typert/registry/src/service.ts:220`）解析。

真实接线在会话控制器：`ApiSessionAgentController.initialize`（`packages/api/session-controller/src/agent.ts:127`）先用 `ctx.typert.lookups.configure('agent'/'session', ...)` 注入 agent 与 session 两种身份解析，再用 `ctx.typert.contexts.configureHost('agent', ...)` 接上 §4.3 契约里的 `contexts` 段。

同一层还有一条旁路：`api/remotes` 把宿主事件转发给远端客户端。`apply()`（`packages/api/remotes/src/index.ts:41`）经 `ctx.typertGateway.registerRemoteEvents` 注册事件源，白名单是 `API_REMOTE_FORWARDED_EVENTS`（`packages/api/remotes/src/remote-events.ts:27-58`，31 个事件）。它和 lookup 无关，放在这里是因为两者都在回答同一个问题：**宿主里的东西怎么让远端看见**——一个是身份，一个是事件。

教学版分两步。登记（生产端，`main.py` 装配段）：

```python
    gateway.lookups.configure("agent", agent.identity, agent)
```

解析（网关 `_resolve_parameter`）：

```python
    def _resolve_parameter(self, param, args):
        """↔ resolveParameter（packages/api/gateway/src/index.ts:1062）：普通参数直传；lookup 参数把身份换回宿主对象。"""
        value = args[param.name]
        if param.lookup is not None:
            return self.lookups.resolve(param.lookup, value)
        return value
```

`main.py` 段 4（完整输出见 §8）：

```text
== 段 4：lookup provider —— 身份字段换回宿主对象 ==
wire 上只有身份: args = {'agent': 'agent-1', 'note': '写完第 16 章初稿'}
网关换回宿主对象后调用 -> {'agent': 'writer', 'summary': 'writer: 写完第 16 章初稿'}
未知身份 -> lookup-not-found: 身份 agent:agent-404 查不到宿主对象
```

`args` 里只有 `'agent-1'`（身份），业务方法拿到的却是 `name='writer'` 的宿主对象——这正是开篇题记的后半句：对象留在宿主进程，wire 上只走身份。

`[教学简化]` 真实描述符里这个字段叫 `source: 'lookup'`（`packages/typert/protocol/src/types.ts:372`），配一个 `lookup` 键指向 provider；教学版合并成一个 `lookup` 字段，`None` 表示普通 JSON 参数。

### 5.5 21 个稳定错误码

网关的业务失败都带**稳定错误码**，消费端按码分支、不必解析消息文本（`TypertGatewayErrorCode`，`packages/api/gateway/src/types.ts:124-145`），共 21 个，且统一带 `gateway/` 前缀：

```ts
export type TypertGatewayErrorCode =
  | 'gateway/ambiguous-endpoint' | 'gateway/arguments-invalid' | 'gateway/binding-invalid'
  | 'gateway/context-failed' | 'gateway/context-not-found' | 'gateway/context-unavailable'
  | 'gateway/definition-unavailable' | 'gateway/input-invalid' | 'gateway/interactions-forbidden'
  | 'gateway/invocation-unavailable' | 'gateway/lookup-failed' | 'gateway/lookup-not-found'
  | 'gateway/lookup-unavailable' | 'gateway/method-unavailable' | 'gateway/peer-disposed'
  | 'gateway/protocol' | 'gateway/provider-mismatch' | 'gateway/result-invalid'
  | 'gateway/service-unavailable' | 'gateway/signature-invalid' | 'gateway/uplink-overflow'
```

其中 `interactions-forbidden`（非 owner Peer 碰了它无权碰的流）、`peer-disposed`、`protocol`、`uplink-overflow`（同流回传项超出接收窗口）只在较新的网关中出现。

这 21 个之外还有两个同前缀的码，不必经 `TypertGatewayErrorCode` 声明：`gateway/internal` 是 `rpcFailure` 的兜底（`packages/api/gateway/src/index.ts:1524`），任何没归类的异常都落到它；`gateway/cancelled` 是传输取消（`packages/api/gateway/src/index.ts:1367`）。它们不是业务分支的码，所以不进联合类型。

教学版在 `GATEWAY_ERROR_CODES`（`gateway.py`）逐条列出同样的 21 个码。`[教学简化]` 教学版去掉了 `gateway/` 前缀，正文讨论时也多用 `arguments-invalid` 这类裸名；`GatewayError` 拒绝未知码构造。段 3/4/5 的输出里，`arguments-invalid`、`lookup-not-found`、`method-unavailable` 已分别露面。

网关解决了痛 2 与痛 3：参数校验对照描述符完成（不用手写校验），路由按端点认领（不用手写分支——新增方法只需多注册一条描述符）。但到目前为止，调用还是「进程内模拟」——怎么真正跨进程，让 CLI 或另一个进程调进来？这就是 SDK。

一句话边界：`packages/api/session-controller` 与 `packages/host/apiproxy` 不在本章范围内。它们是把 host 语义（会话列表、模型切换、审批）按客户端视角重新组装的 host 侧 BFF，属于产品面而不是 RPC 机制；本章只讲它们依赖的网关与描述符。


## 6. JSON-RPC SDK：把网关搬出进程（简述）

**JSON-RPC**：一种轻量远程调用协议——请求是带 `method` 与 `params` 的 JSON 对象，响应按 `id` 匹配回来。`sdk` 包用它把宿主能力暴露给子进程外的世界，传输层是 stdio 上的换行分隔 JSON-RPC 2.0（`JsonRpcLineTransport`，`packages/sdk/protocol/src/transport.ts:65`）：一行一个 JSON 对象，带 `id` 的是请求，没有 `id` 的是通知。`request` 方法（`transport.ts:126`）发请求并等待按 `id` 匹配的响应，支持 AbortSignal 中止；无 handler 返回 `-32601`，handler 失败返回 `-32603`。源码摘录（TS；`AbortSignal` 是「可中止信号」对象，`signal.aborted` 为真时请求立即被拒；`randomUUID()` 生成唯一请求 `id`，用来匹配响应）：

```ts
export class JsonRpcLineTransport implements JsonRpcTransportPeer {
  ...
  request(method: string, params: object, signal?: AbortSignal): Promise<unknown> {
    const id = `req_${randomUUID().replaceAll('-', '')}`
    const message = { jsonrpc: '2.0', id, method, params }
    return new Promise((resolve, reject) => {
      let detach = (): void => {}
      if (signal !== undefined) {
        if (signal.aborted) { reject(abortError(signal.reason)); return }
        const onAbort = (): void => { this.pending.delete(id); reject(abortError(signal.reason)) }
        // ...
      }
      // ...
    })
  }
}
```

教学版 `JsonRpcLineTransport`（`ch16/code/sdk.py`）用两个进程内对象模拟这对 stdio 管道，保留协议三要素：行帧、id 匹配、稳定错误码。接收侧的分发逻辑：

```python
    def send_line(self, line):
        """收到对端一行 JSON，按形状分发。

        无 method → 响应（按 id 放进 _pending）；
        有 method 有 id → 请求；有 method 无 id → 通知。
        """
        message = json.loads(line)
        if "method" not in message:
            self._pending[message["id"]] = message
        elif "id" in message:
            self._handle_request(message)
        else:
            handler = self._notification_handlers.get(message["method"])
            if handler is not None:
                handler(message.get("params", {}))
```

[教学简化] 真实传输是 stdio 异步 I/O 且 `request` 支持 AbortSignal；教学版是进程内同步调用，没有超时与中止。

协议两端各有一个主角。**服务端**不是一个类，而是一对东西：Cordis 插件 `packages/sdk/server/src/index.ts` 声明 `name = 'sdk-jsonrpc-server'` 与 `inject = ['agents', 'workingDirectory']`（`packages/sdk/server/src/index.ts:20-21`），在 `apply`（`packages/sdk/server/src/index.ts:45`）里建传输、再构造 `HarnessSdkJsonRpcServer`（`packages/sdk/server/src/server.ts:89`）。请求分派集中在后者的 `handleRequest`（`packages/sdk/server/src/server.ts:499`）：契约里一共 8 个请求（`HarnessSdkRequestMap`，`packages/sdk/protocol/src/types.ts:210`：`initialize`、`session/prompt`、`session/query`、`session/resume`、`session/wait`、`session/working-directory/get`、`session/working-directory/set`、`shutdown`），教学版只演示其中三个。通知方向是服务端→客户端，契约里 6 个（`HarnessSdkNotificationMap`，`packages/sdk/protocol/src/types.ts:194`：`session.event`、`session.status`、`session.query.completed`、`subagent.started`、`subagent.finished`、`user-question.cancelled`）。

**客户端** `HarnessClient`（`packages/sdk/client/src/client.ts:190`）`start()` 时 spawn 子进程（`packages/sdk/client/src/client.ts:219`），`close()`（`packages/sdk/client/src/client.ts:488`）按 **销毁阶梯** 收尾：EOF → SIGTERM → SIGKILL——先关输入流等子进程自己退出，不退再发终止信号，还不退才强杀，一级比一级强硬；Windows 上信号一律强杀，跳过 SIGTERM 这一级（`packages/sdk/client/src/dispose.ts:93`）。教学版把 `close()` 改名 `destroy()`（`ch16/code/sdk.py`），行为只保留「对端已退出即停止升级」。高层 API 是 `DeepSeekHarness`（`packages/sdk/client/src/api.ts:21`，懒启动子进程）与 `HarnessSession`（`packages/sdk/client/src/api.ts:225`），`finalResponse`（`packages/sdk/client/src/api.ts:373`）聚合最终响应。

`main.py` 段 6 把三者串起来（完整输出见 §8）：

```text
== 段 6：JSON-RPC SDK —— 行帧 + 三请求 + 通知 ==
session/prompt 收到的 chunk: ['echo: 跨进程调用', '（完成）']
finalResponse: echo: 跨进程调用（完成）
销毁阶梯: ['EOF']（优雅退出，未升级）
```

`start()` 内部自动完成 `initialize`；`session/prompt` 先收到流式 chunk 通知，再由 `finalResponse` 聚合；`shutdown` 后子进程优雅退出，销毁阶梯停在第一级 `EOF`，没有升级到 SIGTERM/SIGKILL。[教学决策] 教学版不 spawn 真实子进程，用进程内管道模拟；`SERVER_NOTIFICATIONS` 的四个名字是教学示意名，不对应上面真实的 6 个通知（真名形如 `session.event`、`session.status`）。这条 JSON-RPC 协议线还会在第 17 章继续用：Python SDK 驱动同一条协议做运行时端到端（详见第 17 章）。

## 7. 两条不同的接法：Remote 导出与 ACP

§6 的 SDK 走的是「子进程 → 网关」：契约由生成器产出，调用经网关校验后派发。

源码里还有两条接法与它不同。第一条仍在网关之内，只是契约的来路不一样：服务类**自己声明**哪些方法可以跨进程，不再需要生成器（`officeToPdf`，§7.1）。第二条完全绕过 typert 网关：编辑器这类自动化客户端要用自己的协议**整段接管**一个 agent，网关在这里帮不上忙（ACP，§7.2）。

### 7.1 Remote 服务实例：officeToPdf

§3 到 §5 讲的是「契约怎么被编译、注册、校验」，但那些描述符都是生成器算出来的。本节看另一条路：**服务类什么都不生成，直接用一个基类加两个装饰器，就声明了自己的跨进程面**。`packages/document/office-to-pdf` 正好是一个现成的例子：它只需要继承一个基类、给两个方法打上 `@Remote`，网关就能把它们当成可调用端点。

先说它解决什么。宿主上有一批 Office 文件，浏览器要给用户做预览——但浏览器进程里既没有 LibreOffice，也拿不到宿主的文件句柄。旧的做法是浏览器直接向宿主发「读文件」请求，把授权判断散在每一个调用点：谁还记得这个路径属于哪个会话、用户有没有开过这个文件？

`officeToPdf` 的做法是把「授权」与「转换」拆开。转换服务只接受**已经被授权过的**来源描述——一个 key、一个 version、一段延迟读回调；它自己不做任何授权判断。只有 Remote 入口 `render` 才去找 `workspaceFiles` 要授权——那是 `api/workspace-files` 包提供的、按会话作用域给文件访问权的服务，本教程没有单独讲它，读者只需知道 `readBytes`（`packages/api/workspace-files/src/index.ts:258`）接收「会话作用域 + 路径」并返回字节，授权判断就在这一层做。看类头（TS，`packages/document/office-to-pdf/src/index.ts:101-126`）：

```ts
export class OfficeToPdf extends TypertRemoteService {
  static Config = Config
  /** Changes whenever engine, font, or conversion configuration is replaced. */
  readonly generation: OfficeToPdfGeneration = OfficeToPdfGeneration(randomUUID())
  ...
  constructor(ctx: Context, private readonly config: Config) {
    super(ctx, 'officeToPdf')
    ...
    this.queue = new ConversionQueue(config, this.generation, ...)
```

`TypertRemoteService` 是基类（`packages/typert/protocol/src/index.ts:250`）。构造时它做两件事：照常注册 Cordis 服务；再挂一个 `typertRemote` 绑定（`packages/typert/protocol/src/index.ts:262` 调用 `bindTypertRemote`，后者在 `packages/typert/protocol/src/index.ts:236` 返回一个冻结的 `{ service, serviceKey, namespace }`）。§5.3 的 SRC 弱解析路径正是靠这个绑定找到「这个服务有哪些 `@Remote` 方法」的。

`generation` 是一个随机 UUID，代表**这一代转换器的身份**。进程重启、配置替换、字体目录换了，都会产生新的一代。它不是给服务自己用的，是给客户端判断缓存用的——下面会看到。

两个 `@Remote` 方法（`packages/document/office-to-pdf/src/index.ts:159-175`）：

```ts
@Remote
async render(
  workspaceFileScope: WorkspaceFileScope, path: string, priority: OfficeToPdfPriority, signal: AbortSignal,
): Promise<RenderedDocumentBytes> { ... }

@Remote('generation')
getGeneration(signal: AbortSignal): OfficeToPdfGeneration { signal.throwIfAborted(); return this.generation }
```

第一个是默认导出名（端点 `officeToPdf/render`），第二个带**导出名**：方法在类里叫 `getGeneration`，对外的端点却叫 `officeToPdf/generation`。`@Remote` 的两种重载就是这么分的——不传参是「方法名即端点名」，传字符串是换一个导出名（`packages/typert/protocol/src/index.ts:300-308`）。为什么客户端需要一个专门查代际的方法？因为它手里可能有一份上次渲染的 PDF；能不能复用，取决于服务端这一代是否还是那一代。用一个独立方法把 `generation` 单独暴露出来，客户端就不必先渲染一次才知道缓存是否过期。

`render` 真正碰文件的第一件事不是转换，是**授权探测**（`packages/document/office-to-pdf/src/index.ts:190`）：

```ts
const authorized = await files.readBytes(scope, path, { range: { offset: 0, length: 1 } }, signal)
const source = await files.stat(scope, path, signal)
```

它只要读 1 个字节。这 1 个字节不是为了内容，是为了触发 `workspaceFiles` 对「这个会话、这个 workspace、这个路径」的授权判断——不通过就在这里被拒，后续一个字节都不会读。拿到授权后，`render` 才通过 `files.stat` 拿到 version，再构造 `source.key`（把 sessionId、workspaceRoot、绝对路径拼起来）与延迟读回调（`packages/document/office-to-pdf/src/index.ts:200-207`）交给 `convert`。

延迟读回调是这套设计的第二半：真正的 `fs.readBytes` 发生在**队列准入之后**（`packages/document/office-to-pdf/src/index.ts:202-212`），读之前先重查 version，读完再查一次——`assertUnchanged` 一旦发现路径或 version 变了，就以 `source-changed` 拒绝。从授权到读完之间，文件可能被别的进程改掉；三次比对合起来是在确认「你授权的那个版本，和我转换的是同一个」。

最后是 `generation` 与缓存的关系。`ConversionQueue`（`packages/document/office-to-pdf/src/queue.ts:28`）用两张表：`ready` 存已转换的 PDF，`aliases` 把「来源 key + version + 扩展名」映射到 PDF 的内容 key。内容 key 形如 `${generation}:${digest}`，digest 是扩展名与源字节算出的 SHA-256（`packages/document/office-to-pdf/src/queue.ts:184`）。

**generation 在 key 里**，换代时所有旧 key 自然查不到——不用写任何「清理缓存」的代码，整代缓存就作废了。

队列的其余部分是一套**有界准入**：前台/后台两级名额、`maxReaders` 读者上限、按字节预留的来源容量、LRU 淘汰。细节见该包 README 与 `queue.ts` 本身，本章不展开。

**[教学简化]** 本章教学代码不实现 Remote 服务实例——`officeToPdf` 的转换引擎是外部 npm 包（`libreoffice-kit`），教学版复现不了。这一小节的意义是把 §3、§5 的抽象落到一个真实的类上：`TypertRemoteService` 提供绑定、`@Remote` 决定导出边界、网关按同一套描述符校验——读者可以在 `ch16/code/gateway.py` 的 SRC 路径里看到对应的教学版（`bind_src` + `remote` 标记）。

### 7.2 ACP：自动化桥（简述）

编辑器想接入这个 harness。直接让编辑器去理解 typert 的 wire 不现实：编辑器生态有自己的协议（会话怎么开、流式更新怎么推、权限怎么问），而且插件进程本来就该拿不到宿主服务的句柄。可另一头，harness 的 agent 又只认自己的 Session 与 turn 语义。两边各说各话，中间总得有人翻译。

ACP 就是这一层。**ACP（Agent Client Protocol）**：编辑器/自动化工具与 agent 之间的通信协议。源码里的实现是一个独立插件 `packages/acp/acp`，不经过 typert 网关。`apply()`（`packages/acp/acp/src/index.ts:96`）用 `createAcpAgentApp` 搭出协议服务端，把 ACP 侧的 `initialize` / `authenticate` / `session/new` / `session/list` / `session/resume` / `session/close` / `session/prompt` / `session/setConfigOption` 逐个挂到实现对象上（`packages/acp/acp/src/index.ts:378-390`），再由 `app.connect(stream)` 接上 stdio（`packages/acp/acp/src/index.ts:391`）。

生命周期上，`quiesce`（`packages/acp/acp/src/index.ts:395`）负责安静退出：同步置 `closed` 挡住后续请求，然后逐个 `AcpSession.close`（其取消是同步的，所以先取消再等 drain），最后把失败汇总成一个 `AggregateError`；`ctx.effect(() => quiesce, ...)`（`packages/acp/acp/src/index.ts:436`）让插件卸载也走这条路。

协议与 harness 语义之间的翻译很薄：`packages/acp/acp/src/codec.ts` 一共 35 行，只有一个函数 `turnEndToStopReason`（`packages/acp/acp/src/codec.ts:14`），把 turn 结束原因映射成 ACP 的 stop reason——`completed` 与 `max-tokens` 原样对应，`interrupted` 映射为 `cancelled`，其余（含被 hook 中止的 `aborted`）都落到 `end_turn`。

prompt 内容的准入不在 codec 里，而在 `content.ts`：`supportsAcpImagePrompts`（`packages/acp/acp/src/content.ts:89`）在 initialize 时如实声明「本桥能不能收图片」，`admitAcpPrompt`（`packages/acp/acp/src/content.ts:123`）把线格式的 prompt 块逐个校验、落成核心内容。教学版不实现 ACP，只画它的两条机制线：契约翻译（codec）与生命周期收尾（quiesce）。

## 8. 完整运行输出

本章完整代码在 `ch16/code/`，运行 `python3 ch16/code/main.py`（依赖第 1 章 `cordis.py`，各模块已自动把 `ch01` 加进 `sys.path`）。以下输出全部来自示例代码的 `print`（无框架日志、无第三方输出），逐行可溯源到 `main.py` 六个演示段：

```text
== 段 1：生成器 —— 把服务契约编译成类型图 ==
类型图节点数: 1，命名声明数: 1
调用描述符: id=demo-shell#ShellExecutorStub.run
  service=shellExecutor endpoint=shellExecutor/run
  参数=[('request', 'demo-shell#ShellExecutorStub.run.request')] 结果 codec=strict

== 段 2：注册表 —— 原子注册与回滚 ==
在册 endpoint: ['sessionService/summarize', 'shellExecutor/run']
重复注册被拒: TypertError: 包已注册: demo-shell
dispose 后 shellExecutor/run -> None
hasSeen 仍记得: True
重新注册后: ['sessionService/summarize', 'shellExecutor/run']

== 段 3：网关 strict 路径 —— 一次 /api 调用 ==
调用 /api/shellExecutor/run -> {'exitCode': 0, 'stdout': '[stub] 执行: ls -l'}
拼错参数名被边界拦下 -> arguments-invalid: 期望参数 ['request']，实际 ['reqeust']
多传参数被边界拦下 -> arguments-invalid: 期望参数 ['request']，实际 ['request', 'verbose']
网关错误码表共 21 个（见 GATEWAY_ERROR_CODES）

== 段 4：lookup provider —— 身份字段换回宿主对象 ==
wire 上只有身份: args = {'agent': 'agent-1', 'note': '写完第 16 章初稿'}
网关换回宿主对象后调用 -> {'agent': 'writer', 'summary': 'writer: 写完第 16 章初稿'}
未知身份 -> lookup-not-found: 身份 agent:agent-404 查不到宿主对象

== 段 5：SRC 弱解析 —— 无注册表，现场反射出描述符 ==
调用 /api/diagnostics/ping -> {'pong': 'hello'}
未标记方法被拒 -> method-unavailable: diagnostics/internal 未标记 @remote

== 段 6：JSON-RPC SDK —— 行帧 + 三请求 + 通知 ==
session/prompt 收到的 chunk: ['echo: 跨进程调用', '（完成）']
finalResponse: echo: 跨进程调用（完成）
销毁阶梯: ['EOF']（优雅退出，未升级）
```

对照开篇题记：段 3「拼错/多传参数被 `arguments-invalid` 在边界拦下」兑现了「类型编译成图，调用按图校验」；段 4「wire 上只有身份 `'agent-1'`，宿主对象从未过线」兑现了「跨进程传的是契约，不是对象」。

## 9. 源码对照

| 本章机制 | 对应源码位置 | 教学版差异概括 |
|------|------|------|
| 类型图与生成器 | `packages/typert/generator/src/model.ts:356/:440`（TypeNodeModel/TypeGraph）、`packages/typert/generator/src/workspace.ts:39/:60/:77/:116`（四步） | 教学版用 `inspect` 反射 Python 签名代替编译器分析；TypeNode 只保留 primitive/object 两类（真实 20 个 variant） |
| 调用描述符与 codec | `packages/typert/protocol/src/types.ts:388-442`（InvocationDescriptor）、`packages/typert/protocol/src/types.ts:341`（TypertCodec） | 教学版保留 14 个字段中的 6 个，不实现 mode/uplink/invocation/cancellation 等 |
| 运行时注册表 | `packages/typert/registry/src/service.ts:508`（TypertRegistry）、`:568`（register）、`:52/:71`（两种 key） | 教学版只保留 local 一套 DescriptorStore，不实现 remote/lookup/context/resource 四套 |
| 注册表契约 | `packages/typert/protocol/src/types.ts:939-945`（TypertRegistryContract）、`:947-957`（declare module） | 教学版网关经 `ctx.typert` 消费同一契约形状（get/has_seen） |
| RPC 网关 | `packages/api/gateway/src/index.ts:214/:248-255/:380/:424/:793/:878/:891/:1014/:1062/:1630/:1680/:1704`、`packages/api/gateway/src/types.ts:124-145`（21 错误码） | 教学版用进程内 `RpcConnection` 模拟 HTTP/JSON-RPC 拦截；没有 Peer 身份与访问控制；SRC 反射用 `inspect.signature` 代替 `Function.prototype.toString` |
| lookup provider | `packages/typert/registry/src/service.ts:220`（LookupStore）、`packages/api/session-controller/src/agent.ts:127`、`packages/api/remotes/src/index.ts:41`、`packages/api/remotes/src/remote-events.ts:27-58`（31 个可转发事件） | 教学版 lookup 由网关持有，只实现 agent 一种身份 |
| Remote 服务实例 | `packages/document/office-to-pdf/src/index.ts:101/:159/:174/:190/:200-212`、`packages/document/office-to-pdf/src/queue.ts:28/:57/:184`、`packages/typert/protocol/src/index.ts:250/:262/:300-308` | 教学版不实现转换；`bind_src` + `remote` 标记对应 `TypertRemoteService` + `@Remote` 的 SRC 发现侧 |
| JSON-RPC SDK | `packages/sdk/protocol/src/transport.ts:65/:126`、`packages/sdk/protocol/src/types.ts:194/:210`、`packages/sdk/server/src/server.ts:89`、`packages/sdk/client/src/client.ts:190/:219`、`packages/sdk/client/src/api.ts:21/:225/:373` | 教学版不 spawn 子进程，用进程内管道模拟 stdio；销毁阶梯只保留「对端已退出即停止升级」 |
| ACP 桥 | `packages/acp/acp/src/index.ts:96/:378-390/:391/:395/:436`、`packages/acp/acp/src/codec.ts:14`、`packages/acp/acp/src/content.ts:89/:123` | 教学版不实现，仅正文简述 |

## 10. 小结与预告

本章把第 5 章 seam 的「消费」角色延伸到了进程外：**typert** 把方法契约编译成载体无关的类型图（生成器 → InvocationDescriptor），**TypertRegistry** 以原子注册持有这些契约（effect 提交、disposer 回滚），**RPC 网关**对照契约校验并派发每一次 `/api` 调用（strict / src-json 两条路径、lookup 换回宿主对象、21 个稳定错误码）。链路之上还有两条接法：`officeToPdf` 用 `TypertRemoteService` + `@Remote` 直接声明跨进程面，走的是同一套网关与 SRC 路径，并用 `generation` 让客户端判断缓存；**SDK** 把这套能力经 JSON-RPC stdio 送出进程；**ACP** 不在网关里——它是独立插件，按编辑器侧的协议把 agent 整段接管。第 17 章回到主线：workflow 引擎把脚本交给 PTC 运行时执行，再用 Python SDK 驱动同一条 JSON-RPC 协议，做运行时端到端的完整走线。

## 11. 附录：关键概念速查表

| 概念 | 一句话解释 | 依赖 |
|------|-----------|------|
| **类型图** | 类型分析产出的载体无关中间模型（TypeGraph + TypeNode） | —（底层） |
| **InvocationDescriptor** | 一个被导出方法的载体无关描述（service/namespace/method/parameters/result） | 类型图 |
| **typertKey / typertEndpoint** | schema 标识 `<pkg>#<name>` / 可调用端点 `<namespace>/<method>` | —（底层） |
| **TypertCodec** | 结果编解码策略：strict（按 schema 校验）/ src-json（只做 JSON 安全校验） | 类型图 |
| **TypertRegistry** | 类型图的运行时持有者，原子注册（effect 提交、disposer 回滚） | InvocationDescriptor |
| **注册表契约** | TypertRegistryContract：消费者只依赖契约接口，不依赖 typert 包 | TypertRegistry |
| **RPC 网关** | TypertGatewayService：拦截 /api，按描述符校验并派发调用 | 注册表契约 |
| **lookup provider** | 把 wire 上的身份字段换回宿主对象 | RPC 网关 |
| **TypertRemoteService** | 一个服务基类：构造时注册 Cordis 服务并挂 `typertRemote` 绑定，让 `@Remote` 方法可被网关发现 | typert 协议 |
| **Remote 导出名** | `@Remote` 方法标记；不带参数用方法名做端点名，带字符串则换一个导出名（如 `@Remote('generation')`） | TypertRemoteService |
| **officeToPdf / generation** | Office→PDF 有界转换服务；配置换代即整代缓存作废，浏览器按 `generation` 判缓存 | Remote 导出名 |
| **有界准入** | `ConversionQueue` 的准入策略：前台/后台两级名额 + 读者上限 + 内容寻址缓存，字节预留先于读取 | officeToPdf |
| **JSON-RPC SDK** | stdio 上的换行分隔 JSON-RPC 2.0：行传输 + 请求/通知 + 销毁阶梯 | RPC 网关 |
| **ACP** | Agent Client Protocol 自动化桥：契约翻译 + 生命周期收尾；独立插件，不经 typert 网关 | —（独立插件） |

> 分层：底层为类型图与两种 key → 中层为描述符、codec、注册表与注册表契约 → 上层为网关（SDK 与 Remote 服务实例都经它）与旁路的 ACP。Python 教学符号与源符号映射：`generate_contribution`↔`WorkspaceTypertGenerator.generate`、`TypertRegistry`↔`TypertRegistry`、`typert_key`/`typert_endpoint`↔`typertKey`/`typertEndpoint`、`TypertGatewayService`↔`TypertGatewayService`、`RpcConnection`↔`connection.rpc`、`LookupStore`↔`LookupStore`、`JsonRpcLineTransport`↔`JsonRpcLineTransport`、`HarnessClient`↔`HarnessClient`、`remote`↔`@Remote`。
