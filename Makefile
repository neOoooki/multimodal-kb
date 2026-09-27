# ============================================================
#  multimodal-kb 常用命令
# ============================================================

.PHONY: help install doctor status ingest search eval demo serve test \\
        test-deploy test-deploy-chat deploy deploy-chat deploy-down deploy-verify clean distclean fmt

help:            ## 显示本帮助
	@grep -E "^[a-zA-Z_-]+:.*?## " $(MAKEFILE_LIST) | awk "BEGIN{FS=\":.*?## \"}{printf \"  \\033[36m%-16s\\033[0m %s\\n\", \$$1, \$$2}"

# ---------- 开发 ----------
install:         ## 以可编辑模式安装（获得全局 kb 命令）
	python3 -m pip install -e .

active:          ## 检查是否拿到 API Key（从 .env 或环境变量）
	@python3 -c "from multimodal_kb import KBConfig; c=KBConfig.from_env(); print(\"DASHSCOPE_API_KEY:\", \"OK\" if c.dashscope_api_key else \"缺失\")"

doctor:          ## 全链路体检
	./kb doctor

status:          ## 系统状态
	./kb status

# ---------- 使用 ----------
ingest:          ## 入库：make ingest PDF=书.pdf [DOC_ID=book-1]
	@test -n "$(PDF)" || (echo "用法: make ingest PDF=书.pdf"; exit 1)
	python3 scripts/ingest.py "$(PDF)" $(if $(DOC_ID),--doc-id $(DOC_ID),)

serve:           ## 启动检索服务（前台）
	./kb serve

search:          ## 检索：make search Q="问题"
	@test -n "$(Q)" || (echo "用法: make search Q=\"问题\""; exit 1)
	./kb search "$(Q)"

eval:            ## 召回测试
	./kb eval

demo:            ## 交互式演示
	./kb demo

# ---------- 测试 ----------
test:            ## 冒烟测试（需要服务和 Qdrant 在跑）
	python3 tests/smoke_test.py

test-deploy:     ## 部署契约测试：在一个干净副本上做全新安装（不需要真 key）
	./scripts/deploy-test.sh

test-deploy-chat: ## 同上，外加验证 Open WebUI 集成（需本地已有 6.5GB 镜像）
	./scripts/deploy-test.sh --with-chat

# ---------- 部署 ----------
deploy:          ## 一键部署（Qdrant + 检索服务）
	cd deploy && ./deploy.sh

deploy-chat:     ## 一键部署 + Open WebUI
	cd deploy && ./deploy.sh --with-chat

deploy-verify:   ## 部署后自检
	cd deploy && ./verify.sh

deploy-down:     ## 停止所有容器（数据保留）
	cd deploy && ./deploy.sh --down

# ---------- 清理 ----------
clean:           ## 清缓存（图片描述 / 向量缓存 / pycache）
	rm -rf data/work/*.json **/__pycache__ 2>/dev/null || true
	find . -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null || true
	@echo "已清理缓存（解析产物与向量库保留）"

distclean:       ## 清空所有运行时数据（向量库 + 解析产物 + 缓存）
	@read -p "会删掉 data/ 下全部数据，确认？(yes/N) " a; [ "$$a" = "yes" ] || exit 1
	-@cd deploy && docker compose down 2>/dev/null || true
	# Qdrant 官方镜像只能以 root 运行，它写的 data/qdrant 是 root 属主，
	# 宿主机直接 rm 会 Permission denied —— 借一次性容器来删。
	@if [ -d data/qdrant ] && [ ! -w data/qdrant ]; then \
	  echo "  data/qdrant 属主是 root，用容器清理…"; \
	  docker run --rm -v "$$PWD/data:/d" alpine sh -c "rm -rf /d/qdrant/* /d/qdrant/.[!.]* 2>/dev/null; chown -R $$(id -u):$$(id -g) /d" ; \
	else \
	  rm -rf data/qdrant/* 2>/dev/null || true; \
	fi
	@rm -rf data/work/* data/parsed/* 2>/dev/null || true
	@echo "已清空运行时数据"
