# Common targets come from sowwic/makefiles, fetched into .make/ (commit it). `make update-make` fetches them again.
APP_NAME = sowwic-nyx-bridge
PACKAGE_NAME = nyx_bridge
TEST_OUTPUT_DIR = test_output
BUILD_PREREQS = update-version clean-ds-store

MAKEFILES_REF ?= v1
MAKEFILES_URL ?= https://raw.githubusercontent.com/sowwic/makefiles/$(MAKEFILES_REF)
MAKEFILES_MODULES = general/python general/repo general/pypi

.make/%.mk:
	@mkdir -p $(dir $@)
	@curl -fsSL $(MAKEFILES_URL)/$*.mk -o $@
include $(MAKEFILES_MODULES:%=.make/%.mk)

.PHONY: clean-ds-store
clean-ds-store: ## Remove macOS .DS_Store files (the build packages them)
	@find . -name .DS_Store -not -path "./.git/*" -not -path "./.venv/*" -delete

clean:: clean-ds-store
