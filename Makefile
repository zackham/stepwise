.PHONY: build-web clean-web test

build-web:
	cd web && npm install && npm run build
	rm -rf src/stepwise/_web
	cp -r web/dist src/stepwise/_web

clean-web:
	rm -rf src/stepwise/_web

test:
	uv run python -m pytest tests/ -q
