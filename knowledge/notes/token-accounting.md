# Token 记账

预算阶段可用字符近似估算 token，但不同模型 tokenizer 不同。模型供应商返回 usage 时，应把它作为真实输入输出 token，并注明来源；没有 usage 时才标记 estimate。

比较策略时必须使用相同问题集和模型配置，同时报告输入、输出、缓存 token、失败请求和延迟。只展示最省 token 的成功案例会造成选择偏差。
