# Verification Loop 与失败处理

工具调用成功不等于结果正确。Verification Loop：执行 -> 结果校验 -> 失败分类 -> 重试或回退。

失败分为五类：parameter（参数错误）、permission（权限不足）、timeout（超时）、transient（瞬时错误）、internal（内部错误）。

只有 timeout 和 transient 值得重试；parameter 和 permission 重试多少次结果都一样，应该直接返回给 Agent 修正。

重试必须有上限并带退避（backoff），每次尝试记录耗时和结果，方便统计失败原因分布。

文件写入采用“写临时文件 + 原子替换 + 读回比对”，保证不会留下写了一半的文件。

命令执行使用参数数组和 shell=False，配合命令白名单、工作目录沙箱、超时和输出截断，缩小工具权限。
