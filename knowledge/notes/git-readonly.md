# Git 只读查询

给 Coding Agent 的 Git 查询应采用明确操作枚举，而不是接受任意 git 参数。status 查看工作区，branch 返回当前分支，log 返回提交，diff 查看差异；commit、push、checkout、reset 属于修改操作，不应出现在只读工具。

repo_path 必须相对 repository_root 解析并做路径边界检查。非 Git 仓库是参数问题，命令超时是 timeout，其余临时执行故障才可能是 transient。
