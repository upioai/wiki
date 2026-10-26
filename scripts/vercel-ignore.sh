#!/bin/sh
# Vercel Ignored Build Step：exit 0 = 跳过构建，exit 1 = 照常构建。
# 1) 只构建 main，PR / 分支预览不构建（本地预览用 `npx serve public`）。
# 2) main 上已有更新的提交时跳过当前这次：批量脚本逐条 push（10-22/23 两天 2100+ 次）
#    只会构建最后一个提交。查不到远端 HEAD 时照常构建，宁多勿漏。
[ -z "$VERCEL_GIT_COMMIT_SHA" ] && exit 1
if [ -n "$VERCEL_GIT_COMMIT_REF" ] && [ "$VERCEL_GIT_COMMIT_REF" != "main" ]; then
  echo "skip: branch $VERCEL_GIT_COMMIT_REF"; exit 0
fi
head=$(git ls-remote https://github.com/upioai/wiki refs/heads/main 2>/dev/null | cut -f1)
if [ -n "$head" ] && [ "$head" != "$VERCEL_GIT_COMMIT_SHA" ]; then
  echo "skip: superseded by $head"; exit 0
fi
exit 1
