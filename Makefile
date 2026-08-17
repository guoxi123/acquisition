.PHONY: test test-cov test-unit test-quick frontend-check

# 后端全量测试
test:
	cd backend && .venv/bin/python -m pytest tests/ -v

# 后端测试 + 覆盖率
test-cov:
	cd backend && .venv/bin/python -m pytest tests/ --cov=app --cov-report=term-missing -q

# 后端单元测试（不连 DB，秒级）
test-unit:
	cd backend && .venv/bin/python -m pytest tests/unit/ -v

# 后端快速跑（只看通过/失败，不输出详情）
test-quick:
	cd backend && .venv/bin/python -m pytest tests/ -q

# 前端类型检查
frontend-check:
	cd frontend && pnpm exec tsc --noEmit
