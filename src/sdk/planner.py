"""SDK descriptor 的确定性依赖、冲突和组合计划。

规划器不读取工程树、不调用平台 applier、不解析秘密。它只对已加载 descriptor 做
拓扑排序，检查输出/独占键所有权，并为完整计划计算内容寻址 ID，供后续 apply 和审计
记录绑定同一组输入。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from core.manifest_codec import canonical_json_bytes
from sdk.model import SdkDescriptor, SdkOperation


@dataclass(frozen=True, slots=True)
class SdkHookPlan:
    """描述已排序 SDK descriptor 和其结构化操作的不可变计划。

    参数：
        descriptors: 按依赖和 sdk ID 排序的 descriptor 集合。
        operations: 按 descriptor 顺序、目标路径顺序展开的操作集合。
        outputs: 所有 descriptor 声明的稳定输出路径集合。
        plan_id: 对规范 payload 计算的 SHA-256。

    返回：
        可供平台 apply 使用的 SDK hook 计划。

    异常：
        字段集合或摘要不符合计划不变量时抛出 ``TypeError`` 或 ``ValueError``。

    约束与副作用：
        计划不执行文件变换、不创建日志、不读取 SecretRef。
    """

    descriptors: tuple[SdkDescriptor, ...]
    operations: tuple[SdkOperation, ...]
    outputs: tuple[str, ...]
    plan_id: str

    def __post_init__(self) -> None:
        """校验计划集合和内容寻址 ID。"""
        if not isinstance(self.descriptors, tuple) or not self.descriptors:
            raise ValueError("descriptors 必须是非空 tuple")
        if not all(isinstance(item, SdkDescriptor) for item in self.descriptors):
            raise TypeError("descriptors 的每项必须是 SdkDescriptor")
        if not isinstance(self.operations, tuple) or not all(
            isinstance(item, SdkOperation) for item in self.operations
        ):
            raise TypeError("operations 必须是 tuple[SdkOperation, ...]")
        if not isinstance(self.outputs, tuple) or not all(
            isinstance(item, str) and item for item in self.outputs
        ):
            raise TypeError("outputs 必须是非空字符串 tuple")
        if len(self.plan_id) != 64 or any(char not in "0123456789abcdef" for char in self.plan_id):
            raise ValueError("plan_id 必须是 64 位小写 SHA-256")


class SdkHookPlanner:
    """生成 SDK 依赖排序、冲突校验和确定性计划。"""

    @staticmethod
    def plan(descriptors: tuple[SdkDescriptor, ...]) -> SdkHookPlan:
        """组合 descriptor 并返回确定性 hook 计划。

        参数：
            descriptors: 同一目标平台和阶段的 descriptor 集合，输入顺序任意。

        返回：
            ``SdkHookPlan``，其 descriptor 顺序满足依赖关系。

        异常：
            空集合、类型错误、平台/阶段不一致、缺依赖、循环依赖、输出路径冲突或
            独占 conflict key 冲突时抛出 ``TypeError`` 或 ``ValueError``。

        约束与副作用：
            纯内存操作；不会调用 Android/iOS/Windows applier 或修改工程目录。
        """
        if not isinstance(descriptors, tuple) or not descriptors:
            raise ValueError("descriptors 必须是非空 tuple")
        if not all(isinstance(item, SdkDescriptor) for item in descriptors):
            raise TypeError("descriptors 的每项必须是 SdkDescriptor")
        by_id: dict[str, SdkDescriptor] = {}
        for descriptor in descriptors:
            if descriptor.sdk_id in by_id:
                raise ValueError(f"SDK ID 冲突: {descriptor.sdk_id}")
            by_id[descriptor.sdk_id] = descriptor
        first = descriptors[0]
        if any(
            item.platform is not first.platform or item.stage is not first.stage
            for item in descriptors
        ):
            raise ValueError("SDK descriptor 必须属于同一平台和阶段")
        ordered = _topological_order(by_id)
        _validate_conflicts(ordered)
        operations = tuple(
            operation for descriptor in ordered for operation in descriptor.operations
        )
        outputs = tuple(
            sorted(
                {output for descriptor in ordered for output in descriptor.outputs},
                key=lambda value: value.encode("utf-8"),
            )
        )
        payload = {
            "descriptors": [
                {
                    "depends_on": list(descriptor.depends_on),
                    "inputs": [
                        {
                            "locator": blob.locator,
                            "sha256": blob.sha256,
                            "size": blob.size,
                        }
                        for blob in sorted(
                            descriptor.inputs,
                            key=lambda item: (
                                item.locator.encode("utf-8"),
                                item.sha256.encode("utf-8"),
                                item.size,
                            ),
                        )
                    ],
                    "operations": [
                        {
                            "conflict_key": operation.conflict_key,
                            "kind": operation.kind.value,
                            "target": operation.target,
                            "value": operation.value,
                        }
                        for operation in descriptor.operations
                    ],
                    "outputs": list(descriptor.outputs),
                    "platform": descriptor.platform.value,
                    "sdk_id": descriptor.sdk_id,
                    "secret_refs": list(descriptor.secret_refs),
                    "stage": descriptor.stage.value,
                    "validation_rules": list(descriptor.validation_rules),
                    "version": descriptor.version,
                }
                for descriptor in ordered
            ],
            "schema_version": 1,
        }
        plan_id = hashlib.sha256(canonical_json_bytes(payload)).hexdigest()
        return SdkHookPlan(ordered, operations, outputs, plan_id)


def _topological_order(by_id: dict[str, SdkDescriptor]) -> tuple[SdkDescriptor, ...]:
    """用稳定 Kahn 算法按依赖顺序排列 descriptor。"""
    indegree = {sdk_id: 0 for sdk_id in by_id}
    dependents: dict[str, list[str]] = {sdk_id: [] for sdk_id in by_id}
    for sdk_id, descriptor in by_id.items():
        for dependency in descriptor.depends_on:
            if dependency not in by_id:
                raise ValueError(f"SDK 缺少依赖: {sdk_id} -> {dependency}")
            indegree[sdk_id] += 1
            dependents[dependency].append(sdk_id)
    ready = sorted(
        (sdk_id for sdk_id, count in indegree.items() if count == 0),
        key=lambda value: value.encode("utf-8"),
    )
    ordered: list[SdkDescriptor] = []
    while ready:
        current = ready.pop(0)
        ordered.append(by_id[current])
        for dependent in sorted(dependents[current], key=lambda value: value.encode("utf-8")):
            indegree[dependent] -= 1
            if indegree[dependent] == 0:
                ready.append(dependent)
                ready.sort(key=lambda value: value.encode("utf-8"))
    if len(ordered) != len(by_id):
        raise ValueError("SDK descriptor 存在依赖循环")
    return tuple(ordered)


def _validate_conflicts(descriptors: tuple[SdkDescriptor, ...]) -> None:
    """检查 descriptor 输出路径和操作独占键的所有权冲突。"""
    output_owners: dict[str, str] = {}
    conflict_owners: dict[str, str] = {}
    target_owners: dict[str, str] = {}
    for descriptor in descriptors:
        for output in descriptor.outputs:
            folded = output.casefold()
            owner = output_owners.get(folded)
            if owner is not None and owner != descriptor.sdk_id:
                raise ValueError(f"SDK 输出路径冲突: {output}")
            output_owners[folded] = descriptor.sdk_id
        for operation in descriptor.operations:
            conflict_key = operation.conflict_key.casefold()
            owner = conflict_owners.get(conflict_key)
            if owner is not None and owner != descriptor.sdk_id:
                raise ValueError(f"SDK conflict key 冲突: {operation.conflict_key}")
            conflict_owners[conflict_key] = descriptor.sdk_id
            target = operation.target.casefold()
            owner = target_owners.get(target)
            if owner is not None and owner != descriptor.sdk_id:
                raise ValueError(f"SDK 操作目标冲突: {operation.target}")
            target_owners[target] = descriptor.sdk_id


__all__ = ["SdkHookPlan", "SdkHookPlanner"]
