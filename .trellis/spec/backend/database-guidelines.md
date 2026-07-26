# Database Guidelines

> Database patterns and conventions for this project.

---

## Overview

本项目没有数据库。全部状态都在远端 CPA 管理接口（auth-file 列表与正文）和内存中（`AuthFileMutationCoordinator` 的资源代际计数，进程结束即消失）。

持久化需求出现之前，此文件留空即可。若未来引入本地存储，优先评估 SQLite + 标准库，遵循 quality-guidelines 的"用现成依赖"原则。
