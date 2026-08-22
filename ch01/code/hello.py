"""第 1 章 §2.2 最小例子：一个文件装下三条论断。

运行：python3 hello.py（与 cordis.py 同目录，仅标准库）。
"""
from cordis import Context, Service, ServiceNotFoundError

ctx = Context()  # 创建容器：内部有一张服务表和一张监听器表，后面的能力都注册到这里

# 为 "greet" 事件登记一个监听器：此后任何人 emit("greet", name)，这个 lambda 都会被调用。
# 监听器只认事件名，不知道是谁在 emit。
ctx.on("greet", lambda name: print(f"[event] hello, {name}"))


# 定义一个能力 Greeter：提供 greet 方法，打招呼并发出一个事件。
class Greeter(Service):
    def __init__(self, ctx, config=None):
        # 构造即注册：Service.__init__ 内部调 ctx.provide("greeting", self)，
        # 把这个实例写进服务表，此后 ctx.greeting 就指向它。
        super().__init__(ctx, "greeting")

    def greet(self, name):
        # 发出事件：容器会依次调用所有登记在 "greet" 上的监听器。
        # greet 不知道谁在监听——调用方和监听器只认事件名，互不认识（论断三）。
        self.ctx.emit("greet", name)
        return f"hello, {name}"


# 把 Greeter 作为插件注册进容器：容器负责实例化 Greeter(ctx, config)，
# 并把这次登记记为一笔「效应」——卸载时可以回滚（论断二）。
ctx.plugin(Greeter)

# 使用能力：按名字 "greeting" 从服务表里查出服务，调用它的 greet 方法，
# 全程没有全局变量（论断一）。
print(ctx.greeting.greet("cordis"))

# 注册是效应：dispose 按注册逆序回滚，把服务从服务表移除。
ctx.dispose()
try:
    ctx.greeting  # 再读一次：服务已被回滚，抛 ServiceNotFoundError
except ServiceNotFoundError:
    print("dispose 之后：ctx.greeting 已被回滚")
