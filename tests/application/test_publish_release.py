"""验证发布用例的上传、远端验证、CAS 激活顺序。"""

from application.publish_release import PublishReleaseUseCase


class _PipelinePart:
    """记录一个发布阶段调用。"""

    def __init__(self, name: str, calls: list[str]) -> None:
        """保存阶段名和共享调用记录。"""
        self.name = name
        self.calls = calls

    def upload(self, plan: object, *, cancellation: object = None) -> object:
        """记录上传并返回摘要。"""
        self.calls.append(self.name)
        return {"uploaded": True}

    def verify(self, bundle: object, plan: object) -> object:
        """记录验证并返回凭证。"""
        self.calls.append(self.name)
        return object()

    def activate(self, plan: object, verification: object) -> object:
        """记录激活并返回回执。"""
        self.calls.append(self.name)
        return {"activated": True}


def test_publish_use_case_enforces_upload_verify_activate_order() -> None:
    """Given 三个阶段替身，When 发布，Then 顺序固定且结果成功。"""
    calls: list[str] = []
    result = PublishReleaseUseCase(
        _PipelinePart("upload", calls),
        _PipelinePart("verify", calls),
        _PipelinePart("activate", calls),
    ).run(object(), object())

    assert calls == ["upload", "verify", "activate"]
    assert result.state.value == "succeeded"


def test_publish_use_case_does_not_activate_when_verification_fails() -> None:
    """Given 验证阶段异常，When 发布，Then 激活阶段不会被调用。"""
    calls: list[str] = []

    class _FailingVerifier(_PipelinePart):
        """让验证阶段失败的替身。"""

        def verify(self, bundle: object, plan: object) -> object:
            """记录调用后抛出异常。"""
            self.calls.append(self.name)
            raise RuntimeError("hash mismatch")

    result = PublishReleaseUseCase(
        _PipelinePart("upload", calls),
        _FailingVerifier("verify", calls),
        _PipelinePart("activate", calls),
    ).run(object(), object())

    assert calls == ["upload", "verify"]
    assert result.state.value == "failed"
