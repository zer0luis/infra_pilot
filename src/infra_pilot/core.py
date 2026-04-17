"""Helpers for working with common cloud provider CLIs."""

from __future__ import annotations

from dataclasses import dataclass
import shlex
import shutil
import subprocess
from typing import Iterable, Mapping, Optional


@dataclass(frozen=True)
class CloudCLI:
    """Description of a supported cloud CLI."""

    provider: str
    executable: str
    aliases: tuple[str, ...]
    version_args: tuple[str, ...]


@dataclass(frozen=True)
class ProviderStatus:
    """Availability and version details for a provider CLI."""

    provider: str
    executable: str
    available: bool
    path: Optional[str]
    version: Optional[str]


@dataclass(frozen=True)
class ResourceOperation:
    """A planned mutable cloud operation."""

    provider: str
    action: str
    resource_type: str
    name: str
    command: tuple[str, ...]

    def render(self) -> str:
        """Return a shell-safe preview string for the command."""
        return shlex.join(self.command)


@dataclass(frozen=True)
class WorkflowOperation:
    """A planned session or context-management operation."""

    provider: str
    action: str
    target: str | None
    command: tuple[str, ...]
    shell_command: str | None = None
    executable: bool = True

    def render(self) -> str:
        """Return a shell-safe preview string for the operation."""
        if self.shell_command is not None:
            return self.shell_command
        return shlex.join(self.command)


SUPPORTED_CLIS = {
    "aws": CloudCLI(
        provider="aws",
        executable="aws",
        aliases=("aws", "awscli"),
        version_args=("--version",),
    ),
    "azure": CloudCLI(
        provider="azure",
        executable="az",
        aliases=("azure", "az", "azure-cli"),
        version_args=("version",),
    ),
    "gcp": CloudCLI(
        provider="gcp",
        executable="gcloud",
        aliases=("gcp", "gcloud", "google-cloud"),
        version_args=("version", "--format=value(core)"),
    ),
}

VERSION_TIMEOUT_SECONDS = 5.0

SUPPORTED_RESOURCES = {
    "aws": ("bucket",),
    "azure": ("resource-group",),
    "gcp": ("bucket",),
}

SUPPORTED_CONTEXT_TYPES = {
    "aws": ("profile",),
    "azure": ("subscription",),
    "gcp": ("account", "configuration", "project"),
}

_ALIAS_TO_PROVIDER = {
    alias: provider
    for provider, cli in SUPPORTED_CLIS.items()
    for alias in cli.aliases
}


def list_supported_providers() -> list[str]:
    """Return the canonical provider names supported by this library."""
    return list(SUPPORTED_CLIS.keys())


def list_supported_resource_types(provider: str | None = None) -> dict[str, list[str]] | list[str]:
    """Return supported mutable resource types."""
    if provider is None:
        return {
            provider_name: list(resource_types)
            for provider_name, resource_types in SUPPORTED_RESOURCES.items()
        }

    cli = resolve_provider(provider)
    return list(SUPPORTED_RESOURCES[cli.provider])


def list_supported_context_types(provider: str | None = None) -> dict[str, list[str]] | list[str]:
    """Return supported session/context targets."""
    if provider is None:
        return {
            provider_name: list(context_types)
            for provider_name, context_types in SUPPORTED_CONTEXT_TYPES.items()
        }

    cli = resolve_provider(provider)
    return list(SUPPORTED_CONTEXT_TYPES[cli.provider])


def resolve_provider(name: str) -> CloudCLI:
    """Resolve a provider alias into a canonical CLI description."""
    normalized = name.strip().lower()
    provider = _ALIAS_TO_PROVIDER.get(normalized)
    if provider is None:
        supported = ", ".join(list_supported_providers())
        raise ValueError(f"Unsupported provider '{name}'. Supported providers: {supported}")

    return SUPPORTED_CLIS[provider]


def find_executable(provider: str) -> Optional[str]:
    """Return the path to a provider CLI if it is installed."""
    cli = resolve_provider(provider)
    return shutil.which(cli.executable)


def is_installed(provider: str) -> bool:
    """Return whether the provider CLI is available on PATH."""
    return find_executable(provider) is not None


def build_command(provider: str, args: Iterable[str]) -> list[str]:
    """Build a subprocess command for the chosen provider."""
    cli = resolve_provider(provider)
    return [cli.executable, *list(args)]


def run_command(
    provider: str,
    args: Iterable[str],
    *,
    capture_output: bool = False,
    check: bool = False,
    text: bool = True,
) -> subprocess.CompletedProcess:
    """Run a command through the provider CLI."""
    command = build_command(provider, args)
    return subprocess.run(command, capture_output=capture_output, check=check, text=text)


def get_version(provider: str, *, timeout: float = VERSION_TIMEOUT_SECONDS) -> Optional[str]:
    """Return the provider CLI version text, or None if not installed."""
    cli = resolve_provider(provider)
    if not is_installed(provider):
        return None

    try:
        result = subprocess.run(
            [cli.executable, *cli.version_args],
            capture_output=True,
            check=False,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return f"Timed out after {timeout:g}s"

    return _normalize_version_output(result.stdout, result.stderr)


def get_provider_status(
    provider: str,
    *,
    timeout: float = VERSION_TIMEOUT_SECONDS,
) -> ProviderStatus:
    """Return installation and version details for a provider."""
    cli = resolve_provider(provider)
    path = find_executable(provider)
    available = path is not None
    version = get_version(provider, timeout=timeout) if available else None
    return ProviderStatus(
        provider=cli.provider,
        executable=cli.executable,
        available=available,
        path=path,
        version=version,
    )


def doctor(*, timeout: float = VERSION_TIMEOUT_SECONDS) -> list[ProviderStatus]:
    """Return status information for all supported cloud CLIs."""
    return [get_provider_status(provider, timeout=timeout) for provider in list_supported_providers()]


def plan_login(
    provider: str,
    *,
    profile: str | None = None,
    tenant: str | None = None,
    account: str | None = None,
    use_device_code: bool = False,
    no_browser: bool = False,
    update_adc: bool = False,
    remote: bool = False,
    identity: bool = False,
    configuration: str | None = None,
) -> WorkflowOperation:
    """Build a provider-specific login command."""
    canonical_provider = resolve_provider(provider).provider

    if canonical_provider == "aws":
        if remote:
            raise ValueError("AWS remote console login is not supported by this helper.")
        command = ["aws", "sso", "login"]
        if profile:
            command.extend(["--profile", profile])
        if no_browser:
            command.append("--no-browser")
        if use_device_code:
            command.append("--use-device-code")
        return WorkflowOperation(
            provider=canonical_provider,
            action="login",
            target=profile,
            command=tuple(command),
        )

    if canonical_provider == "azure":
        command = ["az", "login"]
        if tenant:
            command.extend(["--tenant", tenant])
        if use_device_code:
            command.append("--use-device-code")
        if identity:
            command.append("--identity")
        return WorkflowOperation(
            provider=canonical_provider,
            action="login",
            target=tenant,
            command=tuple(command),
        )

    if canonical_provider == "gcp":
        command = ["gcloud", "auth", "login"]
        if account:
            command.append(account)
        if no_browser:
            command.append("--no-browser")
        if update_adc:
            command.append("--update-adc")
        if configuration:
            command.append(f"--configuration={configuration}")
        return WorkflowOperation(
            provider=canonical_provider,
            action="login",
            target=account,
            command=tuple(command),
        )

    raise ValueError(f"Unsupported provider '{provider}'.")


def login(
    provider: str,
    *,
    dry_run: bool = False,
    capture_output: bool = False,
    check: bool = False,
    text: bool = True,
    **kwargs: object,
) -> WorkflowOperation | subprocess.CompletedProcess:
    """Preview or execute a provider login flow."""
    operation = plan_login(provider, **kwargs)
    return _execute_workflow_operation(
        operation,
        dry_run=dry_run,
        capture_output=capture_output,
        check=check,
        text=text,
    )


def plan_logout(provider: str, *, revoke_all: bool = False) -> WorkflowOperation:
    """Build a provider-specific logout command."""
    canonical_provider = resolve_provider(provider).provider

    if canonical_provider == "aws":
        return WorkflowOperation(
            provider=canonical_provider,
            action="logout",
            target=None,
            command=("aws", "sso", "logout"),
        )

    if canonical_provider == "azure":
        return WorkflowOperation(
            provider=canonical_provider,
            action="logout",
            target=None,
            command=("az", "logout"),
        )

    if canonical_provider == "gcp":
        command = ["gcloud", "auth", "revoke"]
        if revoke_all:
            command.append("--all")
        return WorkflowOperation(
            provider=canonical_provider,
            action="logout",
            target=None,
            command=tuple(command),
        )

    raise ValueError(f"Unsupported provider '{provider}'.")


def logout(
    provider: str,
    *,
    dry_run: bool = False,
    capture_output: bool = False,
    check: bool = False,
    text: bool = True,
    revoke_all: bool = False,
) -> WorkflowOperation | subprocess.CompletedProcess:
    """Preview or execute a provider logout flow."""
    operation = plan_logout(provider, revoke_all=revoke_all)
    return _execute_workflow_operation(
        operation,
        dry_run=dry_run,
        capture_output=capture_output,
        check=check,
        text=text,
    )


def plan_list_contexts(
    provider: str,
    *,
    context_type: str | None = None,
    all_contexts: bool = False,
) -> WorkflowOperation:
    """Build a provider-specific list-contexts command."""
    canonical_provider = resolve_provider(provider).provider
    normalized_context = _normalize_context_type(canonical_provider, context_type)

    if canonical_provider == "aws":
        return WorkflowOperation(
            provider=canonical_provider,
            action="contexts",
            target=normalized_context,
            command=("aws", "configure", "list-profiles"),
        )

    if canonical_provider == "azure":
        command = ["az", "account", "list"]
        if all_contexts:
            command.append("--all")
        return WorkflowOperation(
            provider=canonical_provider,
            action="contexts",
            target=normalized_context,
            command=tuple(command),
        )

    if canonical_provider == "gcp":
        if normalized_context == "configuration":
            command = ("gcloud", "config", "configurations", "list")
        elif normalized_context == "account":
            command = ("gcloud", "auth", "list")
        else:
            command = ("gcloud", "projects", "list")
        return WorkflowOperation(
            provider=canonical_provider,
            action="contexts",
            target=normalized_context,
            command=tuple(command),
        )

    raise ValueError(f"Unsupported provider '{provider}'.")


def list_contexts(
    provider: str,
    *,
    context_type: str | None = None,
    all_contexts: bool = False,
    dry_run: bool = False,
    capture_output: bool = False,
    check: bool = False,
    text: bool = True,
) -> WorkflowOperation | subprocess.CompletedProcess:
    """Preview or execute context listing."""
    operation = plan_list_contexts(provider, context_type=context_type, all_contexts=all_contexts)
    return _execute_workflow_operation(
        operation,
        dry_run=dry_run,
        capture_output=capture_output,
        check=check,
        text=text,
    )


def plan_whoami(
    provider: str,
    *,
    profile: str | None = None,
    configuration: str | None = None,
) -> WorkflowOperation:
    """Build a provider-specific identity inspection command."""
    canonical_provider = resolve_provider(provider).provider

    if canonical_provider == "aws":
        command = ["aws", "sts", "get-caller-identity"]
        if profile:
            command.extend(["--profile", profile])
        return WorkflowOperation(
            provider=canonical_provider,
            action="whoami",
            target=profile,
            command=tuple(command),
        )

    if canonical_provider == "azure":
        return WorkflowOperation(
            provider=canonical_provider,
            action="whoami",
            target=None,
            command=("az", "account", "show"),
        )

    if canonical_provider == "gcp":
        command = ["gcloud", "auth", "list"]
        if configuration:
            command.append(f"--configuration={configuration}")
        return WorkflowOperation(
            provider=canonical_provider,
            action="whoami",
            target=configuration,
            command=tuple(command),
        )

    raise ValueError(f"Unsupported provider '{provider}'.")


def whoami(
    provider: str,
    *,
    dry_run: bool = False,
    capture_output: bool = False,
    check: bool = False,
    text: bool = True,
    profile: str | None = None,
    configuration: str | None = None,
) -> WorkflowOperation | subprocess.CompletedProcess:
    """Preview or execute identity inspection."""
    operation = plan_whoami(provider, profile=profile, configuration=configuration)
    return _execute_workflow_operation(
        operation,
        dry_run=dry_run,
        capture_output=capture_output,
        check=check,
        text=text,
    )


def plan_use_context(
    provider: str,
    target: str,
    *,
    context_type: str | None = None,
    create: bool = False,
    no_activate: bool = False,
) -> WorkflowOperation:
    """Build a provider-specific context switch command."""
    canonical_provider = resolve_provider(provider).provider
    normalized_context = _normalize_context_type(canonical_provider, context_type)

    if canonical_provider == "aws":
        if normalized_context != "profile":
            raise ValueError("AWS context switching only supports profiles.")
        shell_command = (
            f"export AWS_PROFILE={shlex.quote(target)} "
            f"AWS_DEFAULT_PROFILE={shlex.quote(target)}"
        )
        return WorkflowOperation(
            provider=canonical_provider,
            action="use",
            target=target,
            command=(),
            shell_command=shell_command,
            executable=False,
        )

    if canonical_provider == "azure":
        if normalized_context != "subscription":
            raise ValueError("Azure context switching only supports subscriptions.")
        return WorkflowOperation(
            provider=canonical_provider,
            action="use",
            target=target,
            command=("az", "account", "set", "--subscription", target),
        )

    if canonical_provider == "gcp":
        if normalized_context == "project":
            return WorkflowOperation(
                provider=canonical_provider,
                action="use",
                target=target,
                command=("gcloud", "config", "set", "project", target),
            )
        if normalized_context == "configuration":
            if create:
                command = ["gcloud", "config", "configurations", "create", target]
                if no_activate:
                    command.append("--no-activate")
            else:
                command = ["gcloud", "config", "configurations", "activate", target]
            return WorkflowOperation(
                provider=canonical_provider,
                action="use",
                target=target,
                command=tuple(command),
            )
        if normalized_context == "account":
            return WorkflowOperation(
                provider=canonical_provider,
                action="use",
                target=target,
                command=("gcloud", "config", "set", "account", target),
            )

    raise ValueError(f"Unsupported context switch: {canonical_provider} {normalized_context}")


def use_context(
    provider: str,
    target: str,
    *,
    context_type: str | None = None,
    create: bool = False,
    no_activate: bool = False,
    dry_run: bool = False,
    capture_output: bool = False,
    check: bool = False,
    text: bool = True,
) -> WorkflowOperation | subprocess.CompletedProcess:
    """Preview or execute a provider-specific context switch."""
    operation = plan_use_context(
        provider,
        target,
        context_type=context_type,
        create=create,
        no_activate=no_activate,
    )
    return _execute_workflow_operation(
        operation,
        dry_run=dry_run,
        capture_output=capture_output,
        check=check,
        text=text,
    )


def plan_set_default_region(
    provider: str,
    value: str,
    *,
    profile: str | None = None,
) -> WorkflowOperation:
    """Build a provider-specific default region/location command."""
    canonical_provider = resolve_provider(provider).provider

    if canonical_provider == "aws":
        command = ["aws", "configure", "set", "region", value]
        if profile:
            command.extend(["--profile", profile])
        return WorkflowOperation(
            provider=canonical_provider,
            action="set-region",
            target=value,
            command=tuple(command),
        )

    if canonical_provider == "azure":
        return WorkflowOperation(
            provider=canonical_provider,
            action="set-region",
            target=value,
            command=("az", "config", "set", f"defaults.location={value}"),
        )

    if canonical_provider == "gcp":
        return WorkflowOperation(
            provider=canonical_provider,
            action="set-region",
            target=value,
            command=("gcloud", "config", "set", "compute/region", value),
        )

    raise ValueError(f"Unsupported provider '{provider}'.")


def set_default_region(
    provider: str,
    value: str,
    *,
    profile: str | None = None,
    dry_run: bool = False,
    capture_output: bool = False,
    check: bool = False,
    text: bool = True,
) -> WorkflowOperation | subprocess.CompletedProcess:
    """Preview or execute a default region/location update."""
    operation = plan_set_default_region(provider, value, profile=profile)
    return _execute_workflow_operation(
        operation,
        dry_run=dry_run,
        capture_output=capture_output,
        check=check,
        text=text,
    )


def plan_create_resource(
    provider: str,
    resource_type: str,
    name: str,
    *,
    location: str | None = None,
    region: str | None = None,
    project: str | None = None,
    tags: Mapping[str, str] | None = None,
    storage_class: str | None = None,
    enable_versioning: bool | None = None,
    uniform_bucket_level_access: bool | None = None,
    requester_pays: bool | None = None,
) -> ResourceOperation:
    """Build a provider-specific create command."""
    canonical_provider = resolve_provider(provider).provider
    normalized_resource = _normalize_resource_type(resource_type)
    command = _build_mutation_command(
        canonical_provider,
        "create",
        normalized_resource,
        name,
        location=location,
        region=region,
        project=project,
        tags=tags,
        storage_class=storage_class,
        enable_versioning=enable_versioning,
        uniform_bucket_level_access=uniform_bucket_level_access,
        requester_pays=requester_pays,
        force=False,
        yes=False,
        no_wait=False,
        clear_tags=False,
    )
    return ResourceOperation(
        provider=canonical_provider,
        action="create",
        resource_type=normalized_resource,
        name=name,
        command=tuple(command),
    )


def plan_update_resource(
    provider: str,
    resource_type: str,
    name: str,
    *,
    tags: Mapping[str, str] | None = None,
    project: str | None = None,
    storage_class: str | None = None,
    enable_versioning: bool | None = None,
    requester_pays: bool | None = None,
    clear_tags: bool = False,
) -> ResourceOperation:
    """Build a provider-specific update command."""
    canonical_provider = resolve_provider(provider).provider
    normalized_resource = _normalize_resource_type(resource_type)
    command = _build_mutation_command(
        canonical_provider,
        "update",
        normalized_resource,
        name,
        location=None,
        region=None,
        project=project,
        tags=tags,
        storage_class=storage_class,
        enable_versioning=enable_versioning,
        uniform_bucket_level_access=None,
        requester_pays=requester_pays,
        force=False,
        yes=False,
        no_wait=False,
        clear_tags=clear_tags,
    )
    return ResourceOperation(
        provider=canonical_provider,
        action="update",
        resource_type=normalized_resource,
        name=name,
        command=tuple(command),
    )


def plan_delete_resource(
    provider: str,
    resource_type: str,
    name: str,
    *,
    project: str | None = None,
    region: str | None = None,
    force: bool = False,
    yes: bool = False,
    no_wait: bool = False,
) -> ResourceOperation:
    """Build a provider-specific delete command."""
    canonical_provider = resolve_provider(provider).provider
    normalized_resource = _normalize_resource_type(resource_type)
    command = _build_mutation_command(
        canonical_provider,
        "delete",
        normalized_resource,
        name,
        location=None,
        region=region,
        project=project,
        tags=None,
        storage_class=None,
        enable_versioning=None,
        uniform_bucket_level_access=None,
        requester_pays=None,
        force=force,
        yes=yes,
        no_wait=no_wait,
        clear_tags=False,
    )
    return ResourceOperation(
        provider=canonical_provider,
        action="delete",
        resource_type=normalized_resource,
        name=name,
        command=tuple(command),
    )


def create_resource(
    provider: str,
    resource_type: str,
    name: str,
    *,
    dry_run: bool = False,
    capture_output: bool = False,
    check: bool = False,
    text: bool = True,
    **kwargs: object,
) -> ResourceOperation | subprocess.CompletedProcess:
    """Preview or execute a create command."""
    operation = plan_create_resource(provider, resource_type, name, **kwargs)
    return _execute_operation(operation, dry_run=dry_run, capture_output=capture_output, check=check, text=text)


def update_resource(
    provider: str,
    resource_type: str,
    name: str,
    *,
    dry_run: bool = False,
    capture_output: bool = False,
    check: bool = False,
    text: bool = True,
    **kwargs: object,
) -> ResourceOperation | subprocess.CompletedProcess:
    """Preview or execute an update command."""
    operation = plan_update_resource(provider, resource_type, name, **kwargs)
    return _execute_operation(operation, dry_run=dry_run, capture_output=capture_output, check=check, text=text)


def delete_resource(
    provider: str,
    resource_type: str,
    name: str,
    *,
    dry_run: bool = False,
    capture_output: bool = False,
    check: bool = False,
    text: bool = True,
    **kwargs: object,
) -> ResourceOperation | subprocess.CompletedProcess:
    """Preview or execute a delete command."""
    operation = plan_delete_resource(provider, resource_type, name, **kwargs)
    return _execute_operation(operation, dry_run=dry_run, capture_output=capture_output, check=check, text=text)


def _normalize_version_output(stdout: str, stderr: str) -> str:
    """Normalize version output across CLIs that write to stdout or stderr."""
    combined = "\n".join(part.strip() for part in (stdout, stderr) if part.strip())
    return combined.strip()


def _normalize_resource_type(resource_type: str) -> str:
    """Normalize resource names across providers."""
    normalized = resource_type.strip().lower().replace("_", "-")
    aliases = {
        "group": "resource-group",
        "resourcegroup": "resource-group",
        "resource-group": "resource-group",
        "bucket": "bucket",
    }
    if normalized not in aliases:
        raise ValueError(f"Unsupported resource type '{resource_type}'.")

    return aliases[normalized]


def _normalize_context_type(provider: str, context_type: str | None) -> str:
    """Normalize context types per provider."""
    default_contexts = {
        "aws": "profile",
        "azure": "subscription",
        "gcp": "configuration",
    }
    aliases = {
        "profile": "profile",
        "profiles": "profile",
        "subscription": "subscription",
        "subscriptions": "subscription",
        "project": "project",
        "projects": "project",
        "configuration": "configuration",
        "config": "configuration",
        "configurations": "configuration",
        "account": "account",
        "accounts": "account",
    }
    normalized = default_contexts[provider] if context_type is None else context_type.strip().lower().replace("_", "-")
    normalized = aliases.get(normalized, normalized)
    supported = set(SUPPORTED_CONTEXT_TYPES[provider])
    if normalized not in supported:
        supported_list = ", ".join(sorted(supported))
        raise ValueError(
            f"Unsupported context type '{context_type}'. Supported context types for '{provider}': {supported_list}"
        )
    return normalized


def _ensure_supported_resource(provider: str, resource_type: str) -> None:
    """Validate that a resource type is supported for a provider."""
    supported = set(SUPPORTED_RESOURCES[provider])
    if resource_type not in supported:
        supported_list = ", ".join(sorted(supported))
        raise ValueError(
            f"Unsupported resource type '{resource_type}' for provider '{provider}'. "
            f"Supported resource types: {supported_list}"
        )


def _build_mutation_command(
    provider: str,
    action: str,
    resource_type: str,
    name: str,
    *,
    location: str | None,
    region: str | None,
    project: str | None,
    tags: Mapping[str, str] | None,
    storage_class: str | None,
    enable_versioning: bool | None,
    uniform_bucket_level_access: bool | None,
    requester_pays: bool | None,
    force: bool,
    yes: bool,
    no_wait: bool,
    clear_tags: bool,
) -> list[str]:
    """Dispatch to the provider-specific command builder."""
    _ensure_supported_resource(provider, resource_type)

    if action == "create":
        return _build_create_command(
            provider,
            resource_type,
            name,
            location=location,
            region=region,
            project=project,
            tags=tags,
            storage_class=storage_class,
            enable_versioning=enable_versioning,
            uniform_bucket_level_access=uniform_bucket_level_access,
            requester_pays=requester_pays,
        )
    if action == "update":
        return _build_update_command(
            provider,
            resource_type,
            name,
            tags=tags,
            project=project,
            storage_class=storage_class,
            enable_versioning=enable_versioning,
            requester_pays=requester_pays,
            clear_tags=clear_tags,
        )
    if action == "delete":
        return _build_delete_command(
            provider,
            resource_type,
            name,
            project=project,
            region=region,
            force=force,
            yes=yes,
            no_wait=no_wait,
        )

    raise ValueError(f"Unsupported action '{action}'.")


def _build_create_command(
    provider: str,
    resource_type: str,
    name: str,
    *,
    location: str | None,
    region: str | None,
    project: str | None,
    tags: Mapping[str, str] | None,
    storage_class: str | None,
    enable_versioning: bool | None,
    uniform_bucket_level_access: bool | None,
    requester_pays: bool | None,
) -> list[str]:
    """Build a provider-specific create command."""
    if provider == "aws" and resource_type == "bucket":
        command = ["aws", "s3", "mb", f"s3://{name}"]
        if region or location:
            command.extend(["--region", region or location or ""])
        return command

    if provider == "azure" and resource_type == "resource-group":
        if not location:
            raise ValueError("Azure resource-group creation requires a location.")
        command = ["az", "group", "create", "--name", name, "--location", location]
        if tags:
            command.extend(["--tags", *_format_equals_pairs(tags)])
        return command

    if provider == "gcp" and resource_type == "bucket":
        command = ["gcloud", "storage", "buckets", "create", f"gs://{name}"]
        if project:
            command.append(f"--project={project}")
        if location:
            command.append(f"--location={location}")
        if storage_class:
            command.append(f"--default-storage-class={storage_class}")
        if uniform_bucket_level_access:
            command.append("--uniform-bucket-level-access")
        if tags:
            raise ValueError("GCP bucket creation does not support labels through this helper; use update after create.")
        if requester_pays is not None:
            raise ValueError("GCP bucket creation does not support requester_pays through this helper; use update after create.")
        if enable_versioning is not None:
            raise ValueError("GCP bucket creation does not support versioning through this helper.")
        return command

    raise ValueError(f"Unsupported create operation: {provider} {resource_type}")


def _build_update_command(
    provider: str,
    resource_type: str,
    name: str,
    *,
    tags: Mapping[str, str] | None,
    project: str | None,
    storage_class: str | None,
    enable_versioning: bool | None,
    requester_pays: bool | None,
    clear_tags: bool,
) -> list[str]:
    """Build a provider-specific update command."""
    if provider == "aws" and resource_type == "bucket":
        if clear_tags:
            return ["aws", "s3api", "delete-bucket-tagging", "--bucket", name]
        if not tags:
            raise ValueError("AWS bucket updates currently support tag replacement only.")
        return [
            "aws",
            "s3api",
            "put-bucket-tagging",
            "--bucket",
            name,
            "--tagging",
            _format_aws_tagset(tags),
        ]

    if provider == "azure" and resource_type == "resource-group":
        command = ["az", "group", "update", "--name", name]
        if clear_tags:
            command.extend(["--tags", ""])
        elif tags:
            command.extend(["--set", *_format_azure_tag_updates(tags)])
        else:
            raise ValueError("Azure resource-group updates require tags or clear_tags.")
        return command

    if provider == "gcp" and resource_type == "bucket":
        command = ["gcloud", "storage", "buckets", "update", f"gs://{name}"]
        if project:
            command.append(f"--project={project}")
        if clear_tags:
            command.append("--clear-labels")
        elif tags:
            command.append(f"--update-labels={_format_csv_pairs(tags)}")
        if storage_class:
            command.append(f"--default-storage-class={storage_class}")
        if enable_versioning is True:
            command.append("--versioning")
        if enable_versioning is False:
            command.append("--no-versioning")
        if requester_pays is True:
            command.append("--requester-pays")
        if requester_pays is False:
            command.append("--no-requester-pays")
        if len(command) == 4:
            raise ValueError("GCP bucket updates require labels, storage_class, or versioning/requester_pays changes.")
        return command

    raise ValueError(f"Unsupported update operation: {provider} {resource_type}")


def _build_delete_command(
    provider: str,
    resource_type: str,
    name: str,
    *,
    project: str | None,
    region: str | None,
    force: bool,
    yes: bool,
    no_wait: bool,
) -> list[str]:
    """Build a provider-specific delete command."""
    if provider == "aws" and resource_type == "bucket":
        command = ["aws", "s3", "rb", f"s3://{name}"]
        if force:
            command.append("--force")
        if region:
            command.extend(["--region", region])
        return command

    if provider == "azure" and resource_type == "resource-group":
        command = ["az", "group", "delete", "--name", name]
        if yes:
            command.append("--yes")
        if no_wait:
            command.append("--no-wait")
        return command

    if provider == "gcp" and resource_type == "bucket":
        command = ["gcloud", "storage", "buckets", "delete", f"gs://{name}"]
        if project:
            command.append(f"--project={project}")
        if yes:
            command.append("--quiet")
        if force:
            raise ValueError("Force deletion is not supported for GCP buckets in this helper.")
        return command

    raise ValueError(f"Unsupported delete operation: {provider} {resource_type}")


def _execute_operation(
    operation: ResourceOperation,
    *,
    dry_run: bool,
    capture_output: bool,
    check: bool,
    text: bool,
) -> ResourceOperation | subprocess.CompletedProcess:
    """Preview or execute a planned operation."""
    if dry_run:
        return operation

    return subprocess.run(
        list(operation.command),
        capture_output=capture_output,
        check=check,
        text=text,
    )


def _execute_workflow_operation(
    operation: WorkflowOperation,
    *,
    dry_run: bool,
    capture_output: bool,
    check: bool,
    text: bool,
) -> WorkflowOperation | subprocess.CompletedProcess:
    """Preview or execute a workflow operation."""
    if dry_run or not operation.executable:
        return operation

    return subprocess.run(
        list(operation.command),
        capture_output=capture_output,
        check=check,
        text=text,
    )


def _format_equals_pairs(values: Mapping[str, str]) -> list[str]:
    """Format key=value pairs as separate CLI arguments."""
    return [f"{key}={value}" for key, value in values.items()]


def _format_csv_pairs(values: Mapping[str, str]) -> str:
    """Format key=value pairs as a comma-separated list."""
    return ",".join(_format_equals_pairs(values))


def _format_azure_tag_updates(values: Mapping[str, str]) -> list[str]:
    """Format Azure tag updates using the --set syntax."""
    return [f"tags.{key}={value}" for key, value in values.items()]


def _format_aws_tagset(values: Mapping[str, str]) -> str:
    """Format AWS bucket tags using shorthand syntax."""
    pairs = ",".join(f"{{Key={key},Value={value}}}" for key, value in values.items())
    return f"TagSet=[{pairs}]"
