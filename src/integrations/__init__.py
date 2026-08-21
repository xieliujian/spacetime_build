"""外部系统适配器的公共导出。

本包公开进程、工作区、HTTP 对象存储和其他外部适配器。具体适配器只能通过 ``ports``
契约接入业务层；导入本包不启动外部程序、不创建输出文件，也不配置全局日志。
"""

from integrations.factory import IntegrationFactory
from integrations.http import UrllibHttpTransport
from integrations.jenkins import JenkinsJobClient
from integrations.process import LocalProcessRunner
from integrations.secrets import ControlledFileSecretProvider, EnvironmentSecretProvider
from integrations.storage import FileSystemObjectStore, HttpObjectStore
from integrations.svn import SvnSourceProvider
from integrations.unity import UnityBatchRunner
from integrations.workspace import LocalWorkspaceProvider

__all__ = [
    "ControlledFileSecretProvider",
    "EnvironmentSecretProvider",
    "FileSystemObjectStore",
    "IntegrationFactory",
    "HttpObjectStore",
    "JenkinsJobClient",
    "LocalProcessRunner",
    "LocalWorkspaceProvider",
    "SvnSourceProvider",
    "UnityBatchRunner",
    "UrllibHttpTransport",
]
