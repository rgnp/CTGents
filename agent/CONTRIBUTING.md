# 贡献指南

感谢你考虑为此项目做出贡献！

## 开发流程

1. Fork 本仓库
2. 创建功能分支：`feat/你的功能描述`
3. 提交变更（见下方提交规范）
4. 确保测试通过：`pytest`
5. 确保代码风格合规：`ruff check src/`
6. 创建 Pull Request

## 提交规范

```
feat: 添加新功能
fix: 修复 bug
docs: 文档变更
refactor: 重构（不涉及功能变更）
test: 添加/修改测试
style: 代码格式（不影响功能）
chore: 构建/工具链变更
```

示例：
```
feat: 添加 markdown 导出工具
fix: 修复 Windows 下 git 检测失败
docs: 更新 README 中的快速开始
```

## 代码风格

- Python 3.11+
- snake_case 命名，公共函数必须有类型注解和 docstring
- 行宽 120 字符
- 导入顺序：标准库 → 第三方 → 项目内部
- 运行 `ruff check src/` 检查风格

## 测试要求

- 新功能必须有测试覆盖
- 测试文件放在 `tests/` 目录
- 运行 `pytest` 确保全部通过
- 运行 `make check` 确保规范评分 ≥ 80

## 工具模块约定

详见 `docs/development.md`。

## 问题反馈

有任何问题或建议，欢迎提交 Issue。
