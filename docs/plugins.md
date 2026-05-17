# Plugin Manifests

`clanker-soul` ships a small, dependency-free plugin manifest standard for
hosts that want drag-drop runtime extensions.

## Folder Shape

```text
plugins/
└── hello_world/
    ├── plugin.json
    └── hello_world.py
```

## `plugin.json`

```json
{
  "name": "hello_world",
  "version": "1.0.0",
  "kind": "tool",
  "entrypoint": "hello_world:HelloWorldPlugin",
  "description": "Minimal reference plugin."
}
```

Required fields are `name`, `version`, `kind`, and `entrypoint`.
`parse_manifest_json()` validates the schema and returns `None` for invalid
manifests after logging a warning, so a host can skip broken plugin folders
without aborting startup.

## `plugins.toml`

```toml
[plugins.hello_world]
enabled = true
priority = 10
settings = { greeting = "hello" }
```

`priority` controls load order among independent plugins. Dependencies declared
in `plugin.json` via `depends_on` are loaded before dependents even when their
priority value is higher.

## Reference Loader

```python
from clanker_soul import PluginLoader

loader = PluginLoader(
    plugin_dir="./plugins",
    master_file="./plugins.toml",
    host_version="1.0.0",
)

for loaded in loader.load_enabled():
    print(loaded.manifest.name, loaded.settings_resolved)
```

The loader imports each enabled plugin entrypoint and returns `LoadedPlugin`
records. Hosts decide how to register each loaded instance based on
`manifest.kind`.

The standard intentionally stays small: no runtime dependencies, no background
service, and no automatic integration with `SoulPlugin`. A host can use the
loader during startup, register returned instances with its own tool/memory/UI
systems, and ignore broken or incompatible folders without taking down the
agent.
