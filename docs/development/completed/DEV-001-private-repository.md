# DEV-001 · 独立私有仓库

类别：交付；日期：2026-10-30（Asia/Shanghai）。

已建立独立 Git 主分支及新仓库 https://github.com/nanashi77777/xiaofeiyu-mahjong-llm 。GitHub API 再次核实 owner=nanashi77777、private=true、default_branch=main；origin 指向该仓库，未复用旧项目远端。方案和忽略规则先行提交，`0785403bbad7724407a619e55f7b7e6163a38619` 已推送且本地 HEAD=origin/main。

验证：GitHub 只读仓库 API、git remote get-url origin、git rev-parse HEAD/origin/main。认证只在进程内读取，未记录凭据。本地原代理失效，推送使用命令级 http.proxy 空值，不更改用户全局配置。

忽略 runtime/、浏览器资料、密钥和运行档案。后续实现需要逐次秘密审计；本记录不代表最终验收 VAL-009 完成。

回退：保留远端历史，使用 git revert 回退具体实现；不删除仓库或强推。
