# Typer Group Prefix

[![PyPI version](https://img.shields.io/pypi/v/typer-group-prefix)](https://pypi.org/project/typer-group-prefix/)
[![Python versions](https://img.shields.io/pypi/pyversions/typer-group-prefix)](https://pypi.org/project/typer-group-prefix/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

**typer-group-prefix** solves a common problem when building complex [Typer](https://typer.tiangolo.com/) CLI applications with reusable dependency-injection parsers: **option name collisions**.

When you want to use the same parser (e.g. a database config block) for *two different* databases in the same command, every `--host` / `HOST` option clashes.  This library lets you attach a *prefix* to every option inside a parser, so `--host` becomes `--primary-host` and `--replica-host`, while the environment variables become `PRIMARY_HOST` and `REPLICA_HOST`.

---

## Installation

```bash
pip install typer-group-prefix
```

Requires Python ≥ 3.11 and [typer](https://typer.tiangolo.com/) ≥ 0.24.

---

## Quick start

```python
import typer
from typing import Annotated
from typer_di import Depends, TyperDI
from typer_group_prefix import TyperGroup

# 1. Define a reusable parser (plain Typer-annotated function)
def parse_db(
    host: Annotated[str, typer.Option("--host", envvar="HOST")] = "localhost",
    port: Annotated[int, typer.Option("--port", envvar="PORT")] = 5432,
) -> dict:
    return {"host": host, "port": port}

# 2. Wrap it in a TyperGroup
DB = TyperGroup(panel="Database", parser=parse_db)

# 3. Use it in your Typer-DI app, applying a different prefix for each usage
app = TyperDI()

@app.command()
def run(
    primary: dict = DB.with_options(prefix="primary")(),
    replica: dict = DB.with_options(prefix="replica")(),
) -> None:
    print(f"primary: {primary}")
    print(f"replica: {replica}")

if __name__ == "__main__":
    app()
```

Running `python app.py run --help` now shows:

```
Options:
  --primary-host TEXT     [env var: PRIMARY_HOST]  [default: localhost]
  --primary-port INTEGER  [env var: PRIMARY_PORT]  [default: 5432]
  --replica-host TEXT     [env var: REPLICA_HOST]  [default: localhost]
  --replica-port INTEGER  [env var: REPLICA_PORT]  [default: 5432]
```

---

## Core concepts

### `TyperGroup`

A `TyperGroup` holds a reference to a parser function and configuration for how its options should be prefixed.  It is immutable – `with_options()` always returns a **new** instance.

```python
from typer_group_prefix import TyperGroup

DB = TyperGroup(
    panel="Database",   # Rich help-panel heading
    prefix="db",        # Default prefix for both CLI args and env vars
    parser=parse_db,
)
```

Call the group (or its `.build()` method) to obtain a `typer_di.Depends` that Typer-DI resolves automatically:

```python
@app.command()
def cmd(db: dict = DB()):
    ...
```

### `with_options()` – deriving namespaced variants

```python
primary = DB.with_options(prefix="primary")   # TyperGroup
replica = DB.with_options(prefix="replica")   # TyperGroup

@app.command()
def cmd(
    primary_db: dict = primary(),
    replica_db: dict = replica(),
):
    ...
```

You can also override CLI and env prefixes independently:

```python
# --primary-host but PRIMARY_DB_HOST
variant = DB.with_options(cli_prefix="primary", env_prefix="primary_db")
```

And you can **prepend** rather than replace:

```python
# DB has prefix="db", prepend gives prefix="app-db"
app_db = DB.with_options(prefix="app", prepend_prefix=True)
```

### `@typer_group` decorator

An alternative to constructing `TyperGroup` manually:

```python
from typer_group_prefix import typer_group

@typer_group(panel="Database", prefix="db")
def parse_db(
    host: Annotated[str, typer.Option("--host", envvar="HOST")] = "localhost",
) -> dict:
    return {"host": host}

# parse_db is now a TyperGroup
```

### `TyperGroupFromDataclass`

Automatically derives CLI options from a dataclass, saving you from writing `Annotated[..., typer.Option(...)]` annotations by hand:

```python
import dataclasses
from typer_group_prefix import TyperGroupFromDataclass

@dataclasses.dataclass
class DBConfig:
    host: str
    port: int = 5432

DB = TyperGroupFromDataclass(DBConfig, panel="Database")
# --host / HOST  and  --port / PORT
# With a prefix: DB.with_options(prefix="primary") → --primary-host / PRIMARY_HOST
```

Special handling built in:

| Field type | Behaviour |
|---|---|
| `pydantic.SecretStr` | `prompt=True`, `hide_input=True` |
| `datetime.timedelta` | Parses `"SS"`, `"MM:SS"`, `"HH:MM:SS"` strings |
| `dataclasses.InitVar` | Included with its unwrapped type |

Pass additional type→parser mappings via the `parsers` argument:

```python
DB = TyperGroupFromDataclass(
    DBConfig,
    panel="Database",
    parsers={MyType: ("METAVAR", my_parser_fn)},
)
```

---

## Prefix normalisation

Prefixes are normalised automatically:

| Input | CLI prefix (lowercase, hyphens) | Env prefix (uppercase, underscores) |
|---|---|---|
| `"my_db"` | `"my-db"` | `"MY_DB"` |
| `"MY-DB"` | `"my-db"` | `"MY_DB"` |
| `""` or `None` | `None` (no prefix) | `None` (no prefix) |

---

## `keep_short` and `keep_panels`

**`keep_short=True`**: By default, single-dash flags (e.g. `-v`) are dropped when prefixing because they can't carry a meaningful prefix. When `keep_short=True` they are included and converted to long-form prefixed names (`--prefix-v`).

**`keep_panels=True`**: By default, every option is moved into the group's `panel`. When `keep_panels=True`, options that already have a `rich_help_panel` set keep their original panel.

---

## `parse_dt` – timedelta parser

The built-in duration parser accepts three formats:

```python
from typer_group_prefix.parsers import parse_dt
from datetime import timedelta

parse_dt("90")       # timedelta(seconds=90)
parse_dt("1:30")     # timedelta(minutes=1, seconds=30)
parse_dt("1:30:00")  # timedelta(hours=1, minutes=30)
```

---

## Development

```bash
# Install dev dependencies
uv sync --group test --group example

# Run tests
uv run pytest -v

# Lint
uv run ruff check
```
