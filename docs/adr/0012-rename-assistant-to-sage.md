# Rename assistant identity from nanobot to Sage

The assistant's LLM-facing identity (system prompt personality) and CLI display name default were "nanobot 🐈", identical to the package/CLI command name. This created ambiguity: "nanobot" referred to both the software product and the AI persona. Renaming the persona to "Sage 🦉" separates the two concerns. The package name (`nanobot` CLI command, `pip install nanobot-ai`) stays unchanged; only the assistant identity and CLI display default change.

**Scope**: SOUL.md, legacy/SOUL.md, identity.md, USER.md, MEMORY.md, HEARTBEAT.md, prompts/README.md (assistant references), config schema defaults (`bot_name`, `bot_icon`), CLI stream/terminal defaults. CLI command name, WebUI brand strings, and `__logo__` are explicitly out of scope.

**Existing users**: The legacy SOUL.md migration baseline is updated to Sage. Users with the old nanobot SOUL.md on disk will not auto-migrate (their file no longer matches the new legacy template). They can edit their SOUL.md manually. New installations get Sage by default.

**Emoji**: 🐈 (cat) → 🦉 (owl). Sage means "wise one"; the owl is the traditional symbol of wisdom (Athena's companion). The cat was a carryover from the nanobot brand with no semantic tie to the name.
