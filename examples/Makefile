# Build the example gallery (roadmap §6.1).
#
#   make            regenerate every example .tex and .png
#   make clean      remove the generated files
#   make check      regenerate and diff the .tex against what is committed,
#                   so CI or a reviewer can see drift
#
# The real work is in build.sh; this exists so that `make` does the obvious
# thing from either the repository root or this directory.

SHELL := /bin/bash
HERE  := $(dir $(abspath $(lastword $(MAKEFILE_LIST))))

.PHONY: all clean check

all:
	@bash "$(HERE)build.sh"

clean:
	@rm -f "$(HERE)"*.tex "$(HERE)"*.png
	@echo "cleaned $(HERE)"

# Only the .tex is compared: PNG bytes depend on the versions of the LaTeX
# toolchain and the PDF converter, so they differ between machines without the
# drawing having changed at all.
check: all
	@if git -C "$(HERE).." diff --quiet -- 'examples/*.tex'; then \
	  echo "examples/*.tex is up to date"; \
	else \
	  echo "examples/*.tex differs from what is committed:"; \
	  git -C "$(HERE).." --no-pager diff --stat -- 'examples/*.tex'; \
	  exit 1; \
	fi
