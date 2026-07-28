# Toulmin 论证模型 — 完整参考

> 加载: on_demand | 用途: 需要回溯术语定义或进行深度 warrant 审计时

---

## 六要素

Stephen Toulmin (1958) _The Uses of Argument_

| 要素 | 英文 | 问题 | 论文中的查找位置 |
|------|------|------|---------------|
| 主张 | Claim | 论文的核心断言是什么？ | Abstract / Introduction 末段 / Contribution list |
| 依据 | Grounds | 凭什么这么说？ | Experiments / Results / Tables |
| 理由 | Warrant | 为什么这些依据能支持主张？ | 通常不在论文中显式写出——需要推理 |
| 支撑 | Backing | 理由背后的理论/共识是什么？ | Related Work / Method motivation |
| 反驳 | Rebuttal | 作者承认了什么限制？ | Limitation / Discussion / Failure cases |
| 限定 | Qualifier | 主张的适用范围？ | Experimental setup / Assumptions |

## 七问审计法（paper-deep-read 采用）

在标准六要素之外追加第七问：

| 7 | Unexamined | 论文没有触及但应该触及的问题？ | 需要读者自己发现 |

这第七问是"找问题"的入口——Unexamined = 论文暴露的 gap。

## Warrant 的类型

| 类型 | 示例 | 脆弱点 |
|------|------|-------|
| 因果 warrant | "因为模块 A 捕获了 X，所以性能提升了" | 真的捕获了 X 吗？有可视化/量化证据吗？ |
| 类比 warrant | "类似 B 设计在 C 任务上有效，所以也适用于 D" | C 和 D 的任务差异有多大？ |
| 泛化 warrant | "因为在小规模上验证了，所以大规模也有效" | 有没有规模化瓶颈？ |
| 权威 warrant | "因为前人 F 也用了，所以合理" | F 的结论是在什么条件下成立的？ |

## 常见论证谬误

1. **循环论证**: warrant = claim 的另一种表述
2. **稻草人**: 攻击一个弱化版的 baseline 当作解决了"前人问题"
3. **虚假二择**: 只比两个方案，忽略中间可能性
4. **post hoc ergo propter hoc**: "加了模块就变好了，所以模块是原因"——但没有排除其他因素（如更多参数）
