"""反例：没有类型图、没有网关 —— 手写跨进程桥接。

运行：python3 ch16/code/bad_example.py
三个痛：
痛 1：契约只在作者脑子里，拼错参数名不被边界拦下，要等业务代码深处才炸。
痛 2：没有 JSON 安全边界，不可序列化的值混进参数，直到传输序列化时才炸。
痛 3：新增方法要同步手写转发分支，忘了就调用时才发现。
"""
import json


class ShellHost:
    """宿主进程里的一个服务（业务方）。"""

    def run(self, request):
        return {"exitCode": 0, "stdout": f"[stub] 执行: {request['command']}"}


def hand_written_bridge(host, endpoint, payload):
    """手写转发：一个方法一个分支，没有任何校验 —— 契约只在作者脑子里。"""
    if endpoint == "/api/shell/run":
        return host.run(payload)   # payload 原样当 request：形状错了要到业务代码才炸
    raise KeyError(endpoint)       # 新增方法必须同步在这里加分支


def main():
    host = ShellHost()

    print("== 反例：手写跨进程桥接的三个痛 ==")
    # 痛 1：拼错参数名（request -> reqeust），桥接不做任何校验
    try:
        hand_written_bridge(host, "/api/shell/run", {"reqeust": {"command": "ls"}})
    except KeyError as exc:
        print(f"痛 1：拼错参数名不被边界拦下，业务代码深处才炸 -> KeyError({exc})")

    # 痛 2：把一个函数（非 JSON 值）混进参数，直到要序列化传输时才炸
    payload = {"request": {"command": "ls", "callback": lambda: None}}
    try:
        json.dumps(payload)
    except TypeError as exc:
        print(f"痛 2：非 JSON 值不被边界拦下，序列化时才炸 -> TypeError: {exc}")

    # 痛 3：业务侧新增 start 方法，桥接忘了加分支
    try:
        hand_written_bridge(host, "/api/shell/start", {"spec": {"command": "sleep 1"}})
    except KeyError as exc:
        print(f"痛 3：新增方法没同步到桥接，调用时才发现 -> KeyError({exc})")


if __name__ == "__main__":
    main()
