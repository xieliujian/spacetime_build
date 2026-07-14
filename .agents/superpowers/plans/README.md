# 实施计划索引

阶段计划：

- `readme/12_第一阶段实施计划.md`：工程骨架阶段记录；已完成，Python 3.10+ wheel 验证待关闭。
- `readme/13_第二阶段领域模型与DAG实施计划.md`：领域模型、确定性 manifest、任务 DAG、恢复 Frontier 和 Release 模型。
- `readme/14_第三阶段兼容协议实施计划.md`：六字段文件列表、AssetBundle 数据库、历史五库字节 diff 和旧客户端解析验收；当前迁移验收未关闭。
- `readme/15_下月续作交接.md`：当前完成状态、环境缺口、恢复命令和下次执行顺序。

执行顺序固定为 12 的 Python 3.10+ 验证缺口 → 13 → 14。Release 模型在第二阶段先完成，第三阶段 compatibility DTO 只能依赖 Release 模型。后续阶段继续建立独立计划并在此追加索引；计划正文保存在 `readme/`，本目录不复制正文。
