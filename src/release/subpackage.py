"""分包 membership 到旧客户端 bit flag 的显式转换。"""

from __future__ import annotations

from collections.abc import Iterable

from release.entries import ResourceVariant


class SubpackagePlanner:
    """把类型化分包归属转换为旧客户端兼容 bit flag。"""

    @staticmethod
    def flag_for_membership(
        *,
        install: bool = False,
        base: bool = False,
        package_ids: Iterable[int] = (),
    ) -> int:
        """计算显式 membership 对应的 bit flag。

        参数：
            install: 是否属于安装包；安装包优先级最高，返回 ``0``。
            base: 是否属于基础包；基础包优先于普通分包，返回 ``0``。
            package_ids: 1 到 31 的分包编号，编号 n 对应 ``1 << (n - 1)``。

        返回：
            非负 Int32 分包 bit flag；INSTALL/BASE 返回 0。

        异常：
            编号不是 1..31、重复或布尔伪整数时抛出 ``ValueError``。

        约束与副作用：
            纯函数；不读取文件名、路径或环境变量。
        """
        ids = _validate_ids(package_ids)
        if not isinstance(install, bool) or not isinstance(base, bool):
            raise TypeError("install 和 base 必须是 bool")
        if install or base:
            return 0
        return sum(1 << (package_id - 1) for package_id in ids)

    @staticmethod
    def flag_for_variant(variant: ResourceVariant, *, package_ids: Iterable[int] = ()) -> int:
        """在显式变体边界下计算普通分包 flag。

        参数：
            variant: 主清或低清变体；该参数必须显式提供，不能从路径猜测。
            package_ids: 1 到 31 的分包编号。

        返回：
            与 ``flag_for_membership`` 相同的普通分包 bit flag。

        异常：
            变体或分包编号非法时抛出 ``TypeError`` / ``ValueError``。

        约束与副作用：
            当前主清与低清使用同一 bit 编码，变体只作为调用边界校验。
        """
        if not isinstance(variant, ResourceVariant):
            raise TypeError("variant 必须是 ResourceVariant")
        return SubpackagePlanner.flag_for_membership(package_ids=package_ids)


def _validate_ids(package_ids: Iterable[int]) -> tuple[int, ...]:
    """校验并稳定化分包编号。"""
    try:
        ids = tuple(package_ids)
    except TypeError as exc:
        raise TypeError("package_ids 必须是整数可迭代对象") from exc
    if len(set(ids)) != len(ids):
        raise ValueError("package_ids 不得重复")
    for package_id in ids:
        if (
            not isinstance(package_id, int)
            or isinstance(package_id, bool)
            or not 1 <= package_id <= 31
        ):
            raise ValueError("package_id 必须在 1..31 范围内")
    return tuple(sorted(ids))
