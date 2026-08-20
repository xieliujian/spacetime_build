"""IL2CPP 输出归档的类型化完整性验证。

本模块验证已生成归档的内容寻址摘要、文件表和调用方声明的必需路径。它不自行解析
平台二进制格式；ELF/PE/Mach-O 的库架构、符号和运行时兼容性必须由受控
``ProcessRunner`` 工具检查，并将结果作为独立证据交给上层。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from services.il2cpp.archive import Il2CppArchive, Il2CppArchiveEntry
from services.il2cpp.model import Il2CppBuildRequest


@dataclass(frozen=True, slots=True)
class Il2CppValidationReport:
    """描述一次 IL2CPP 输出校验的稳定结果。

    参数：
        request_id: 被校验请求的公开 ID。
        valid: 是否没有发现完整性或必需文件错误。
        entries: 归档文件表。
        errors: 按 UTF-8 字节序排列的稳定诊断文本。

    返回：
        一个不可变验证报告。

    异常：
        字段类型错误时抛出 ``TypeError``；报告字段排序错误时抛出 ``ValueError``。

    约束与副作用：
        报告不保存输入绝对路径或秘密；路径只来自归档逻辑文件表。
    """

    request_id: str
    valid: bool
    entries: tuple[Il2CppArchiveEntry, ...]
    errors: tuple[str, ...]

    def __post_init__(self) -> None:
        """校验验证报告的不可变集合和 valid/errors 一致性。"""
        if not isinstance(self.request_id, str) or not self.request_id:
            raise ValueError("request_id 必须是非空字符串")
        if not isinstance(self.valid, bool):
            raise TypeError("valid 必须是 bool")
        if not isinstance(self.entries, tuple) or not all(
            isinstance(item, Il2CppArchiveEntry) for item in self.entries
        ):
            raise TypeError("entries 必须是 tuple[Il2CppArchiveEntry, ...]")
        if not isinstance(self.errors, tuple) or not all(
            isinstance(error, str) and error for error in self.errors
        ):
            raise TypeError("errors 必须是非空字符串 tuple")
        ordered_errors = tuple(sorted(set(self.errors), key=lambda error: error.encode("utf-8")))
        if ordered_errors != self.errors:
            raise ValueError("errors 必须去重并按 UTF-8 字节序排序")
        if self.valid != (not self.errors):
            raise ValueError("valid 必须与 errors 是否为空一致")


class Il2CppOutputValidator:
    """验证 IL2CPP 归档摘要、文件表和调用方声明的必需输出。"""

    @staticmethod
    def validate(
        archive: Il2CppArchive,
        request: Il2CppBuildRequest,
        *,
        required_files: tuple[str, ...],
    ) -> Il2CppValidationReport:
        """生成一次不抛业务异常的 IL2CPP 输出验证报告。

        参数：
            archive: 归档创建阶段记录的路径、Blob 和文件表。
            request: 对应的已校验构建请求，用于绑定报告身份。
            required_files: 平台适配器声明的必需逻辑路径集合，例如库和 metadata。

        返回：
            ``Il2CppValidationReport``；内容损坏、摘要不匹配或缺文件会体现在
            ``valid=False`` 和稳定 ``errors`` 中。

        异常：
            ``archive``、``request`` 或 required_files 容器类型错误时抛出 ``TypeError``；
            归档内容问题不抛出，而是返回无效报告。

        约束与副作用：
            只读取 archive.path；不执行平台二进制检查，不修改归档和请求。
        """
        if not isinstance(archive, Il2CppArchive):
            raise TypeError("archive 必须是 Il2CppArchive")
        if not isinstance(request, Il2CppBuildRequest):
            raise TypeError("request 必须是 Il2CppBuildRequest")
        if not isinstance(required_files, tuple):
            raise TypeError("required_files 必须是 tuple")
        errors: list[str] = []
        try:
            _validate_required_files(required_files)
        except ValueError as exc:
            errors.append(str(exc))

        entry_paths = {entry.path for entry in archive.entries}
        for required in required_files:
            if isinstance(required, str) and required not in entry_paths:
                errors.append(f"缺少必需文件: {required}")

        if not archive.path.is_file():
            errors.append("归档文件不存在")
        else:
            content = archive.path.read_bytes()
            digest = hashlib.sha256(content).hexdigest()
            if digest != archive.blob.sha256 or len(content) != archive.blob.size:
                errors.append("归档摘要或大小校验失败")

        normalized_errors = tuple(sorted(set(errors), key=lambda error: error.encode("utf-8")))
        return Il2CppValidationReport(
            request_id=request.request_id,
            valid=not normalized_errors,
            entries=archive.entries,
            errors=normalized_errors,
        )


def _validate_required_files(required_files: tuple[str, ...]) -> None:
    """校验必需文件集合的路径、重复项和确定性输入。"""
    seen: set[str] = set()
    for path in required_files:
        if not isinstance(path, str) or not path or "\\" in path or path.startswith("/"):
            raise ValueError("required_files 含非法逻辑路径")
        parts = path.split("/")
        if any(not part or part in {".", ".."} for part in parts):
            raise ValueError("required_files 含非法逻辑路径")
        folded = path.casefold()
        if folded in seen:
            raise ValueError(f"required_files 存在重复路径: {path}")
        seen.add(folded)


__all__ = ["Il2CppOutputValidator", "Il2CppValidationReport"]
