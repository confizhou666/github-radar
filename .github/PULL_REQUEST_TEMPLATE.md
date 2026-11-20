## 变更摘要 (Summary)

- 简述本次 PR 解决的问题或新增的能力：
- 关联 Issue（如有）：Closes #

## 自检清单 (Verification Checklist)

- [ ] 未引入除 `PyYAML` 以外的生产运行时依赖，前端维持零构建原生代码
- [ ] `ruff check .` 静态检查零警告通过
- [ ] `node --check web/app.js` 语法检查通过
- [ ] `NO_PROXY=127.0.0.1,localhost python -m unittest discover -s tests -v`（含后端回归测试与无头浏览器冒烟测试）全部通过
