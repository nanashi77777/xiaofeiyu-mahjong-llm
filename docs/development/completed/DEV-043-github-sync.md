# DEV-043 · GitHub默认分支同步与API推送后备入口

完成日期：2026-10-31（Asia/Shanghai）。类别：实现/真实上传验证。

用户看到开发分支“had recent pushes”提示。核验后确认已上传的开发提交存在，但默认main仍停在初始化基线。该提示是GitHub分支更新提示，不能据此判断推送失败。将main非强制快进到已验证开发版本，并在本批提交后再次与本机HEAD核对。

本机Git全局代理127.0.0.1:7890没有监听，覆盖代理后的github.com直连也超时；api.github.com可用。交付tools/github_sync.py作为自有两个私库的后备传输，凭据仅在内存中从Git Credential Manager读取。同步前核对origin、归属和private=true，逐提交审计将上传的新建/修改文件，保留原作者/提交者/时间/消息与父提交。Git Data API返回的blob/tree/commit必须匹配本机原对象哈希；引用只用force=false推进，并再次读回校验。不修改全局代理、DNS或TLS验证，不上传工作区。

真实证据：麻将库14dc03b685631fda1613ea251abcaecc675abd8b上传成功，开发分支feat/initial-websocket-runtime与默认main均读回同一SHA；QQ库a46546a92b2d71ac70b0c8a076fa0045b8b2382b上传成功，feat/mahjong-readonly与main均读回同一SHA。两个仓库再次执行同步均为uploaded_commits=0、verified=true，仓库元数据均为private=true、default_branch=main。脱敏结果存本机runtime/github-sync-verification.json，不提交认证或运行数据。

复核命令：在独立麻将目录执行`.venv\Scripts\python.exe tools/github_sync.py --also-main`；QQ库加`--root <qq-project>`。仅处理本机已经存在的普通线性未签名提交；签名/扩展头、merge、submodule或远端分叉会停止，不另造历史或强推。标准Git HTTPS的网络路径仍不可达，本任务交付的是已实测的后备上传路径。远端在核验后出现新提交时也必须重新检查，不能覆盖。

实现提交：5a7d89c3342f2c401d95043f4dda3324adfea8aa。QQ配套实现：a46546a92b2d71ac70b0c8a076fa0045b8b2382b（P034）。50项Python回归、ruff及193项QQ回归沿用本批代码验证；本次另完成真实上传/读回/重复执行验证和任务视图校验。回退可停用后备脚本并恢复标准Git传输；不反向重置已快进的远端main，不触碰本机私密配置和QQ用户未提交修改。
