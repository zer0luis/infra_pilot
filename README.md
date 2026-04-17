# InfraPilot GitHub Install Guide

`InfraPilot` is a Python library and CLI for multi-cloud operations across AWS,
Azure, and GCP.

This guide is for the simplest GitHub flow:

1. clone the repository
2. install it locally for your user
3. run the CLI

It does not require creating a virtual environment.


## Clone and install

Clone the repository and install the package for the current user:

```bash
git clone <your-repo-url>
cd infra-pilot
python3 -m pip install --user .
```

This installs the package without needing root access and without creating a
virtual environment.

## Verify the install

Run one of these commands:

```bash
infrapilot providers
```

If the `infrapilot` command is not on your `PATH`, run it as a Python module:

```bash
python3 -m infra_pilot.cli providers
```

You can also check provider detection:

```bash
python3 -m infra_pilot.cli doctor
```

## Basic usage

Examples:

```bash
python3 -m infra_pilot.cli providers
python3 -m infra_pilot.cli context-types
python3 -m infra_pilot.cli doctor
python3 -m infra_pilot.cli whoami aws --dry-run
python3 -m infra_pilot.cli diagram azure --format mermaid --dry-run
```

If `infrapilot` is available on your `PATH`, the same commands can be written
like this:

```bash
infrapilot providers
infrapilot doctor
```

## Update after pulling new changes

If the repository changes, update your local install from the repo root:

```bash
git pull
python3 -m pip install --user --upgrade .
```

If the version number changes and you want a clean reinstall, this also works:

```bash
python3 -m pip uninstall infra-pilot
python3 -m pip install --user .
```

## Uninstall

To remove the local install:

```bash
python3 -m pip uninstall infra-pilot
```

## Notes

- `--dry-run` lets users preview commands without changing cloud resources.
- Real execution depends on the cloud CLI for the selected provider.
- AWS profile switching may print shell exports that should be wrapped with
  `eval`.
- If users prefer not to rely on the installed command path, `python3 -m
  infra_pilot.cli ...` always works after installation.
