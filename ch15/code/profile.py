"""第 15 章：profile + boot——把 bundle 串成「栈」，再把栈拍平成 entry 列表。

真实结构（notes §5）：
- profile 是 $DSH_HOME/profiles/<name> 目录，package.json 的 dsh.profile.bundles
  是「有序的 bundle 名单」，外加用户自己的一份 cordis.patch.yml；
- loadProfile（profile.ts:371）按序把每个 bundle 的 patch 叠起来，用户 patch 是最上层；
- composeEntries（profile.ts:413）用 applyEntryPatches 把叠好的 patch 拍平成 entry 列表；
- boot（app-boot/src/index.ts:757）把 entry 列表挂进 context 并断言全部激活。

教学版把「栈」建模为有序的 bundle 名单 + 用户 patch；boot 简化为「激活未禁用条目」。
"""
from patch import apply_entry_patches


class Profile:
    """一个 profile = 名字 + 有序 bundle 名单 + 用户自己的最上层 patch。"""

    def __init__(self, name, bundles, user_patches=None):
        self.name = name
        self.bundles = list(bundles)      # 有序：先列的在栈底，后列的在栈顶
        self.user_patches = list(user_patches or [])


def load_profile(profile, bundle_registry):
    """把 profile 叠成一串 patch：每个 bundle 贡献一层，用户 patch 压在最上层。

    对应 loadProfile（profile.ts:371）：读 dsh.profile.bundles，逐个 resolveBundleDir
    取出 cordis.patch.yml，层层叠加；用户 ~/.dsh/profiles/<name>/cordis.patch.yml 最后。
    返回的列表顺序 = 应用顺序（栈底 → 栈顶）。
    """
    stacked = []
    for bundle_name in profile.bundles:
        bundle = bundle_registry[bundle_name]   # 按名字发现 bundle（类比 resolveBundleDir）
        stacked.extend(bundle.patches)          # 该 bundle 的整层 patch 追加进栈
    stacked.extend(profile.user_patches)        # 用户 patch 永远是最上层，能覆盖一切
    return stacked


def compose_entries(base_entries, stacked_patches):
    """boot 的第一步：把叠好的 patch 拍平成最终 entry 列表。

    对应 composeEntries（profile.ts:413）：从 base 配置出发，依次应用栈里全部 patch。
    """
    return apply_entry_patches(base_entries, stacked_patches)


def boot(entries):
    """boot 的第二步：加载并激活「未被禁用」的 entry，返回被激活的 entry。

    [教学简化] 真实 boot（index.ts:757）是：new Context → ctx.plugin(Loader) →
    mountRootInclude 挂整树 → loader.await() → assertEntriesActivated 断言全部激活。
    教学版只保留「跳过 disabled、激活其余」这一可观察行为。
    """
    activated = []
    for entry in entries:
        if entry["disabled"]:
            continue                            # 被某层 patch 禁用的条目，boot 不加载
        activated.append(entry)                 # 其余条目逐个激活
    return activated
