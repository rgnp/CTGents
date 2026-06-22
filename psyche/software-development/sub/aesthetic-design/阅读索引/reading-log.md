# 阅读日志 — 审美设计 Psyche

> 初始构建日期: 2026-06-22 | 版本: 0.1

---

## 阶段一：全景测绘 + 深度浸泡

### Material Design 3
- **日期**: 2026-06-22
- **来源**: m3.material.io + m2.material.io
- **状态**: 核心原则已读
- **卡片位置**: → knowledge/aesthetic-design/material-design-principles.md

### Apple Human Interface Guidelines
- **日期**: 2026-06-22
- **来源**: developer.apple.com/design/human-interface-guidelines
- **状态**: 核心原则已读
- **卡片位置**: → knowledge/aesthetic-design/apple-hig-principles.md

### Nielsen 10 条可用性启发式
- **日期**: 2026-06-22
- **来源**: nngroup.com
- **状态**: 已读全文
- **卡片位置**: → knowledge/aesthetic-design/nielsen-usability-heuristics.md

### UI 设计风格全景
- **日期**: 2026-06-22
- **来源**: designerup.co, setproduct.com, Medium
- **状态**: 已读核心（7 种风格）
- **卡片位置**: → knowledge/aesthetic-design/ui-design-styles.md

### 配色理论与实践
- **日期**: 2026-06-22
- **来源**: WebAIM, WCAG 2.2, 综合
- **状态**: 已读核心
- **卡片位置**: → knowledge/aesthetic-design/color-theory-and-accessibility.md

### 排版与布局
- **日期**: 2026-06-22
- **来源**: Material Design + Apple HIG + 排版基础
- **状态**: 已读核心
- **卡片位置**: → knowledge/aesthetic-design/typography-and-layout.md

### 交互与动效
- **日期**: 2026-06-22
- **来源**: Material Design Motion + Apple HIG
- **状态**: 已读核心
- **卡片位置**: → knowledge/aesthetic-design/interaction-and-motion.md

---

## 版本 0.4 更新

### 新增 Gestalt 心理学 + Aesthetic-Usability Effect + CLI/TUI 深度扩展 + 配色系统化
- **日期**: 2026-06-22
- **触发**: 用户指出审美设计"薄弱"，需要更多理论支撑 + TUI 深度
- **调研**: Gestalt 心理学（Wertheimer/Köhler 1910-1930s）、Aesthetic-Usability Effect（Kurosu & Kashimura 1995）、CLI UX 模式（Lucas F. Costa / ThoughtWorks）、Textual 框架设计文档
- **新增知识卡片**（5张）:
  1. → knowledge/aesthetic-design/aesthetic-usability-effect.md
  2. → knowledge/aesthetic-design/gestalt-principles-ui.md
  3. → knowledge/aesthetic-design/cli-ux-patterns.md
  4. → knowledge/aesthetic-design/textual-tui-patterns.md
  5. → knowledge/aesthetic-design/color-semantics-accessibility.md
- **核心更新**:
  1. 7→9 个不动点（新增: ⑧好看=好用 / ⑨Gestalt是自检工具）
  2. 3-5-3 检查 + Gestalt 镜
  3. B3 TUI 扩展：CLI UX 模式 + Textual 框架实践 + 6色系统
  4. 配色维度补充对比度目标色盲要求
  5. 负面知识增加 6 条（Gestalt非律令/审美掩盖缺陷/CLI错误码/CLI进度/剥离审美测试/感知vs实际）
  6. 所有判断显式标注 → 知识卡片路径
  7. 封面新增 知识库健康度 字段
- **知识库健康度**: 9 张旧卡 + 5 张新卡 = 14 张，全部完整

---

## 下一步待读

- [x] Gestalt 心理学 UI 应用 — 已补（→ gestalt-principles-ui.md）
- [x] Aesthetic-Usability Effect — 已补（→ aesthetic-usability-effect.md）
- [x] CLI UX 模式 — 已补（→ cli-ux-patterns.md）
- [x] Textual 框架设计模式 — 已补（→ textual-tui-patterns.md）
- [x] 配色系统化 — 已补（→ color-semantics-accessibility.md）
- [ ] Atomic Design (Brad Frost) — 设计系统方法论经典
- [ ] Refactoring UI (Adam Wathan) — 程序员友好设计书
- [ ] Don't Make Me Think (Steve Krug) — 可用性经典
- [ ] 中国/亚洲设计风格特有审美（如侘寂、极简东方美学）


---

## 版本 0.2 更新

### 扩展交互深度 + 边缘状态 + 风格指南 L3 标注
- **日期**: 2026-06-22
- **触发**: 自检发现"交互"维度偏浅（缺表单/空状态/边缘状态判断）+ 风格选择指南未标推理级别
- **修复**:
  1. 新增第 6 条认知姿态："交互的每个状态都要被设计"
  2. 六维"交互"维度增加边缘状态检查
  3. 新增 B2 交互场景检查清单（表单设计/边缘状态/手势与反馈三个 checklist）
  4. 风格选择指南标注 [L3] — 基于推理，未经用户研究验证
  5. 负面知识增加 4 条（边缘状态/表单耐心/隐藏导航/自定义手势）
- **卡片位置**: → knowledge/aesthetic-design/interaction-patterns-deep.md (新增)

---

## 版本 0.3 更新

### 新增 TUI/终端设计维度
- **日期**: 2026-06-22
- **触发**: 用 aesthetic-design v0.2 审 TUI 时发现——全部基于 GUI 原则，完全没考虑终端界面特性
- **根因**: aesthetic-design 是基于网页/GUI 设计知识构建的，TUI 是不同媒介
- **修复**:
  1. 调研 Textual 官方设计文档 + 优秀 TUI 项目 + 终端配色系统
  2. 新增 knowledge/ 卡片：tui-design-principles.md
  3. 新增第 7 条认知姿态：TUI 不是"在终端里假装 GUI"
  4. 新增 B3: TUI 设计检查清单（配色/布局/消息渲染/信息层次）
  5. 负面知识增加 5 条 TUI 专用
- **卡片位置**: → knowledge/aesthetic-design/tui-design-principles.md (新增)