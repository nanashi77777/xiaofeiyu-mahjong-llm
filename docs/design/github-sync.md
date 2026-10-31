# GitHub默认分支与传输后备入口

2026-10-31用户要求解决推送提示。核验发现开发分支已上传，但默认main仍在初始化提交；GitHub“had recent pushes”是分支提示。先将main非强制快进到已验证的ef144e6，不上传尚未提交的选场代码。

另外，Git全局http.proxy指向当前未监听的127.0.0.1:7890；覆盖代理直连github.com的多组IP也超时，而api.github.com可访问。不更改全局网络/代理/证书验证。新增tools/github_sync.py作为当前两个自有私库的传输后备：从Git Credential Manager在内存读取凭据，核验owner/private/origin，逐提交审计所有将上传的新建/修改文件，通过Git Data API上传修改树和原提交。已有祖先对象已经在该远端，不重复上传。blob/tree/commit返回哈希必须与本机完全一致，之后才force=false更新分支并重新读回。

仅同步本机已经存在的线性普通提交；签名/扩展头、merge、submodule等由标准Git处理。不能通过改作者、提交时间、消息或静默创建另一份历史绕过哈希不一致。主分支由--also-main明确选择且要求远端是目标的祖先，不强推。脚本不上传未提交工作区，不打印令牌或服务器原始错误体。

真实验收用本批开发提交检查：开发分支与main的远端SHA均等于本机；脚本第二次执行uploaded_commits=0。实现和实际验证记录到DEV-043，网络不可达不能冒充仓库内代码丢失。
