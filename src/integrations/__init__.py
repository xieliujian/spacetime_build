"""外部系统适配器的公共导出。

本包当前公开安全的本地进程执行器。具体适配器只能通过 ``ports`` 契约接入业务
层；导入本包不启动外部程序、不创建输出文件，也不配置全局日志。
"""

from integrations.factory import IntegrationFactory
from integrations.http import UrllibHttpTransport
from integrations.jenkins import JenkinsJobClient
from integrations.process import LocalProcessRunner
from integrations.secrets import ControlledFileSecretProvider, EnvironmentSecretProvider
from integrations.storage import FileSystemObjectStore
from integrations.svn import SvnSourceProvider
from integrations.unity import UnityBatchRunner
from integrations.workspace import LocalWorkspaceProvider

__all__ = [
    "ControlledFileSecretProvider",
    "EnvironmentSecretProvider",
    "FileSystemObjectStore",
    "IntegrationFactory",
    "JenkinsJobClient",
    "LocalProcessRunner",
    "LocalWorkspaceProvider",
    "SvnSourceProvider",
    "UnityBatchRunner",
    "UrllibHttpTransport",
]
