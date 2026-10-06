# Convenience targets (Windows: use `make` from Git Bash, or run the npm scripts directly).
.PHONY: install dev stack stack-stop test e2e screenshots lighthouse build

install:
	npm install

dev:
	npm run dev

stack:
	powershell -ExecutionPolicy Bypass -File scripts/dev.ps1

stack-stop:
	powershell -ExecutionPolicy Bypass -File scripts/dev.ps1 -Stop

test:
	npm run typecheck && npm test

e2e:
	npm run e2e

screenshots:
	npm run screenshots

lighthouse:
	npm run lighthouse

build:
	npm run build
