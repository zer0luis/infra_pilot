"""Cloud inventory collection and diagram rendering helpers."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
import re
import shlex
import subprocess
from typing import Any, Iterable, Mapping, Sequence

from .core import resolve_provider


DIAGRAM_TIMEOUT_SECONDS = 30.0
DEFAULT_DIAGRAM_SCOPE = {
    "aws": "current",
    "azure": "all",
    "gcp": "all",
}
SUPPORTED_DIAGRAM_FORMATS = ("mermaid", "dot", "json")
SUPPORTED_DIAGRAM_SERVICES = ("network", "compute", "storage")


@dataclass(frozen=True)
class DiagramNode:
    """A node in a generated cloud inventory diagram."""

    id: str
    label: str
    kind: str
    provider: str
    attributes: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class DiagramEdge:
    """A directional relationship in a generated cloud inventory diagram."""

    source: str
    target: str
    label: str | None = None


@dataclass
class CloudDiagram:
    """Structured diagram output before rendering."""

    provider: str
    title: str
    scope: str
    nodes: list[DiagramNode] = field(default_factory=list)
    edges: list[DiagramEdge] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable representation of the diagram."""
        return {
            "provider": self.provider,
            "title": self.title,
            "scope": self.scope,
            "nodes": [
                {
                    "id": node.id,
                    "label": node.label,
                    "kind": node.kind,
                    "provider": node.provider,
                    "attributes": node.attributes,
                }
                for node in self.nodes
            ],
            "edges": [
                {
                    "source": edge.source,
                    "target": edge.target,
                    "label": edge.label,
                }
                for edge in self.edges
            ],
            "warnings": list(self.warnings),
            "metadata": dict(self.metadata),
        }


def plan_diagram_commands(
    provider: str,
    *,
    scope: str | None = None,
    include: Iterable[str] | None = None,
    profile: str | None = None,
    subscription: str | None = None,
    project: str | None = None,
) -> list[tuple[str, ...]]:
    """Return the discovery commands used to build a provider inventory diagram."""
    canonical_provider = resolve_provider(provider).provider
    normalized_scope = _normalize_diagram_scope(canonical_provider, scope)
    services = _normalize_include(include)

    if canonical_provider == "aws":
        commands: list[tuple[str, ...]] = [
            _with_aws_profile(("aws", "sts", "get-caller-identity", "--output", "json"), profile),
        ]
        if "network" in services or "compute" in services:
            commands.append(_with_aws_profile(("aws", "ec2", "describe-regions", "--output", "json"), profile))
            if "network" in services:
                commands.append(
                    _with_aws_profile(("aws", "ec2", "describe-vpcs", "--region", "<region>", "--output", "json"), profile)
                )
                commands.append(
                    _with_aws_profile(("aws", "ec2", "describe-subnets", "--region", "<region>", "--output", "json"), profile)
                )
            if "compute" in services:
                commands.append(
                    _with_aws_profile(
                        ("aws", "ec2", "describe-instances", "--region", "<region>", "--output", "json"),
                        profile,
                    )
                )
        if "storage" in services:
            commands.append(_with_aws_profile(("aws", "s3api", "list-buckets", "--output", "json"), profile))
        return commands

    if canonical_provider == "azure":
        subscription_token = subscription or "<subscription-id>"
        commands = []
        if normalized_scope == "current" or subscription:
            commands.append(("az", "account", "show", "--output", "json"))
        else:
            commands.append(("az", "account", "list", "--all", "--output", "json"))
        commands.append(("az", "group", "list", "--subscription", subscription_token, "--output", "json"))
        if "network" in services:
            commands.append(("az", "network", "vnet", "list", "--subscription", subscription_token, "--output", "json"))
        if "compute" in services:
            commands.append(("az", "vm", "list", "--subscription", subscription_token, "--output", "json"))
        if "storage" in services:
            commands.append(("az", "storage", "account", "list", "--subscription", subscription_token, "--output", "json"))
        return commands

    if canonical_provider == "gcp":
        project_token = project or "<project-id>"
        commands = []
        if normalized_scope == "current" or project:
            commands.append(("gcloud", "config", "list", "--format=json"))
        else:
            commands.append(("gcloud", "auth", "list", "--format=json"))
            commands.append(("gcloud", "projects", "list", "--format=json"))
        if "network" in services:
            commands.append(("gcloud", "compute", "networks", "list", f"--project={project_token}", "--format=json"))
            commands.append(
                ("gcloud", "compute", "networks", "subnets", "list", f"--project={project_token}", "--format=json")
            )
        if "compute" in services:
            commands.append(("gcloud", "compute", "instances", "list", f"--project={project_token}", "--format=json"))
        if "storage" in services:
            commands.append(("gcloud", "storage", "buckets", "list", f"--project={project_token}", "--format=json"))
        return commands

    raise ValueError(f"Unsupported provider '{provider}'.")


def collect_tenant_diagram(
    provider: str,
    *,
    scope: str | None = None,
    include: Iterable[str] | None = None,
    profile: str | None = None,
    subscription: str | None = None,
    project: str | None = None,
    timeout: float = DIAGRAM_TIMEOUT_SECONDS,
) -> CloudDiagram:
    """Collect provider inventory and return a structured diagram."""
    canonical_provider = resolve_provider(provider).provider
    normalized_scope = _normalize_diagram_scope(canonical_provider, scope)
    services = _normalize_include(include)

    if canonical_provider == "aws":
        return _collect_aws_diagram(
            scope=normalized_scope,
            include=services,
            profile=profile,
            timeout=timeout,
        )
    if canonical_provider == "azure":
        return _collect_azure_diagram(
            scope=normalized_scope,
            include=services,
            subscription=subscription,
            timeout=timeout,
        )
    if canonical_provider == "gcp":
        return _collect_gcp_diagram(
            scope=normalized_scope,
            include=services,
            project=project,
            timeout=timeout,
        )

    raise ValueError(f"Unsupported provider '{provider}'.")


def render_diagram(diagram: CloudDiagram, *, format: str = "mermaid") -> str:
    """Render a diagram into Mermaid, DOT, or JSON."""
    normalized_format = format.strip().lower()
    if normalized_format not in SUPPORTED_DIAGRAM_FORMATS:
        supported = ", ".join(SUPPORTED_DIAGRAM_FORMATS)
        raise ValueError(f"Unsupported diagram format '{format}'. Supported formats: {supported}")

    if normalized_format == "mermaid":
        return _render_mermaid(diagram)
    if normalized_format == "dot":
        return _render_dot(diagram)
    return json.dumps(diagram.to_dict(), indent=2, sort_keys=True)


def write_diagram(
    diagram: CloudDiagram,
    *,
    format: str = "mermaid",
    output_path: str | Path,
) -> Path:
    """Render and write a diagram to disk."""
    rendered = render_diagram(diagram, format=format)
    path = Path(output_path)
    path.write_text(rendered + ("" if rendered.endswith("\n") else "\n"), encoding="utf-8")
    return path


def _collect_aws_diagram(
    *,
    scope: str,
    include: set[str],
    profile: str | None,
    timeout: float,
) -> CloudDiagram:
    """Collect a high-level AWS account inventory diagram."""
    identity = _run_json_command(
        _with_aws_profile(("aws", "sts", "get-caller-identity", "--output", "json"), profile),
        timeout=timeout,
        description="AWS caller identity",
    )
    account_id = str(identity.get("Account", "unknown-account"))
    title = f"AWS Inventory {account_id}"
    diagram = CloudDiagram(provider="aws", title=title, scope=scope)
    root_id = f"aws:account:{account_id}"
    _add_node(diagram, root_id, f"AWS Account\n{account_id}", "account")

    regions: list[str] = []
    if "network" in include or "compute" in include:
        region_data = _safe_run_json_command(
            diagram,
            _with_aws_profile(("aws", "ec2", "describe-regions", "--output", "json"), profile),
            timeout=timeout,
            description="AWS regions",
        )
        for region in region_data.get("Regions", []):
            status = region.get("OptInStatus", "opt-in-not-required")
            if status in {"opted-in", "opt-in-not-required"}:
                region_name = region.get("RegionName")
                if region_name:
                    regions.append(region_name)
                    region_id = f"aws:region:{region_name}"
                    _add_node(diagram, region_id, f"Region\n{region_name}", "region")
                    _add_edge(diagram, root_id, region_id)

    if "storage" in include:
        buckets = _safe_run_json_command(
            diagram,
            _with_aws_profile(("aws", "s3api", "list-buckets", "--output", "json"), profile),
            timeout=timeout,
            description="AWS buckets",
        )
        for bucket in buckets.get("Buckets", []):
            name = bucket.get("Name")
            if not name:
                continue
            bucket_id = f"aws:bucket:{name}"
            _add_node(diagram, bucket_id, f"S3 Bucket\n{name}", "bucket")
            _add_edge(diagram, root_id, bucket_id, "storage")

    if "network" in include:
        for region_name in regions:
            region_id = f"aws:region:{region_name}"
            vpcs = _safe_run_json_command(
                diagram,
                _with_aws_profile(("aws", "ec2", "describe-vpcs", "--region", region_name, "--output", "json"), profile),
                timeout=timeout,
                description=f"AWS VPCs in {region_name}",
            )
            for vpc in vpcs.get("Vpcs", []):
                vpc_id = vpc.get("VpcId")
                if not vpc_id:
                    continue
                label = f"VPC\n{_display_name(vpc_id, _extract_aws_name(vpc.get('Tags', [])))}"
                _add_node(diagram, f"aws:vpc:{vpc_id}", label, "vpc")
                _add_edge(diagram, region_id, f"aws:vpc:{vpc_id}")

            subnets = _safe_run_json_command(
                diagram,
                _with_aws_profile(
                    ("aws", "ec2", "describe-subnets", "--region", region_name, "--output", "json"),
                    profile,
                ),
                timeout=timeout,
                description=f"AWS subnets in {region_name}",
            )
            for subnet in subnets.get("Subnets", []):
                subnet_id = subnet.get("SubnetId")
                vpc_id = subnet.get("VpcId")
                if not subnet_id or not vpc_id:
                    continue
                label = f"Subnet\n{_display_name(subnet_id, _extract_aws_name(subnet.get('Tags', [])))}"
                _add_node(diagram, f"aws:subnet:{subnet_id}", label, "subnet")
                _add_edge(diagram, f"aws:vpc:{vpc_id}", f"aws:subnet:{subnet_id}")

    if "compute" in include:
        for region_name in regions:
            region_id = f"aws:region:{region_name}"
            instances = _safe_run_json_command(
                diagram,
                _with_aws_profile(
                    ("aws", "ec2", "describe-instances", "--region", region_name, "--output", "json"),
                    profile,
                ),
                timeout=timeout,
                description=f"AWS instances in {region_name}",
            )
            for reservation in instances.get("Reservations", []):
                for instance in reservation.get("Instances", []):
                    instance_id = instance.get("InstanceId")
                    if not instance_id:
                        continue
                    name = _extract_aws_name(instance.get("Tags", []))
                    label = f"EC2\n{_display_name(instance_id, name)}"
                    node_id = f"aws:instance:{instance_id}"
                    _add_node(diagram, node_id, label, "instance")
                    subnet_id = instance.get("SubnetId")
                    if subnet_id and _has_node(diagram, f"aws:subnet:{subnet_id}"):
                        _add_edge(diagram, f"aws:subnet:{subnet_id}", node_id)
                    else:
                        _add_edge(diagram, region_id, node_id)

    diagram.metadata["account_id"] = account_id
    return diagram


def _collect_azure_diagram(
    *,
    scope: str,
    include: set[str],
    subscription: str | None,
    timeout: float,
) -> CloudDiagram:
    """Collect a high-level Azure tenant inventory diagram."""
    subscriptions: list[dict[str, Any]]
    if subscription:
        current = _run_json_command(
            ("az", "account", "show", "--subscription", subscription, "--output", "json"),
            timeout=timeout,
            description="Azure subscription",
        )
        subscriptions = [current]
    elif scope == "current":
        current = _run_json_command(
            ("az", "account", "show", "--output", "json"),
            timeout=timeout,
            description="Azure subscription",
        )
        subscriptions = [current]
    else:
        listed = _run_json_command(
            ("az", "account", "list", "--all", "--output", "json"),
            timeout=timeout,
            description="Azure subscriptions",
        )
        subscriptions = list(listed)

    tenant_id = str(subscriptions[0].get("tenantId", "unknown-tenant")) if subscriptions else "unknown-tenant"
    title = f"Azure Inventory {tenant_id}"
    diagram = CloudDiagram(provider="azure", title=title, scope=scope)
    root_id = f"azure:tenant:{tenant_id}"
    _add_node(diagram, root_id, f"Azure Tenant\n{tenant_id}", "tenant")

    for sub in subscriptions:
        sub_id = sub.get("id")
        if not sub_id:
            continue
        sub_name = sub.get("name", sub_id)
        sub_node_id = f"azure:subscription:{sub_id}"
        _add_node(diagram, sub_node_id, f"Subscription\n{sub_name}", "subscription")
        _add_edge(diagram, root_id, sub_node_id)

        groups = _safe_run_json_command(
            diagram,
            ("az", "group", "list", "--subscription", sub_id, "--output", "json"),
            timeout=timeout,
            description=f"Azure resource groups in {sub_name}",
        )
        group_ids: dict[str, str] = {}
        for group in groups:
            group_name = group.get("name")
            if not group_name:
                continue
            group_node_id = f"azure:resource-group:{sub_id}:{group_name}"
            group_ids[group_name] = group_node_id
            _add_node(diagram, group_node_id, f"Resource Group\n{group_name}", "resource-group")
            _add_edge(diagram, sub_node_id, group_node_id)

        if "network" in include:
            vnets = _safe_run_json_command(
                diagram,
                ("az", "network", "vnet", "list", "--subscription", sub_id, "--output", "json"),
                timeout=timeout,
                description=f"Azure VNets in {sub_name}",
            )
            for vnet in vnets:
                vnet_name = vnet.get("name")
                resource_group = vnet.get("resourceGroup")
                if not vnet_name:
                    continue
                vnet_node_id = f"azure:vnet:{sub_id}:{vnet_name}"
                _add_node(diagram, vnet_node_id, f"VNet\n{vnet_name}", "vnet")
                parent_id = group_ids.get(resource_group, sub_node_id)
                _add_edge(diagram, parent_id, vnet_node_id)
                for subnet in vnet.get("subnets", []):
                    subnet_name = subnet.get("name")
                    if not subnet_name:
                        continue
                    subnet_node_id = f"azure:subnet:{sub_id}:{vnet_name}:{subnet_name}"
                    _add_node(diagram, subnet_node_id, f"Subnet\n{subnet_name}", "subnet")
                    _add_edge(diagram, vnet_node_id, subnet_node_id)

        if "compute" in include:
            vms = _safe_run_json_command(
                diagram,
                ("az", "vm", "list", "--subscription", sub_id, "--output", "json"),
                timeout=timeout,
                description=f"Azure VMs in {sub_name}",
            )
            for vm in vms:
                vm_name = vm.get("name")
                if not vm_name:
                    continue
                resource_group = vm.get("resourceGroup")
                vm_node_id = f"azure:vm:{sub_id}:{vm_name}"
                _add_node(diagram, vm_node_id, f"VM\n{vm_name}", "vm")
                parent_id = group_ids.get(resource_group, sub_node_id)
                _add_edge(diagram, parent_id, vm_node_id)

        if "storage" in include:
            accounts = _safe_run_json_command(
                diagram,
                ("az", "storage", "account", "list", "--subscription", sub_id, "--output", "json"),
                timeout=timeout,
                description=f"Azure storage accounts in {sub_name}",
            )
            for account in accounts:
                name = account.get("name")
                if not name:
                    continue
                resource_group = account.get("resourceGroup")
                storage_node_id = f"azure:storage:{sub_id}:{name}"
                _add_node(diagram, storage_node_id, f"Storage\n{name}", "storage-account")
                parent_id = group_ids.get(resource_group, sub_node_id)
                _add_edge(diagram, parent_id, storage_node_id)

    diagram.metadata["tenant_id"] = tenant_id
    diagram.metadata["subscription_count"] = len(subscriptions)
    return diagram


def _collect_gcp_diagram(
    *,
    scope: str,
    include: set[str],
    project: str | None,
    timeout: float,
) -> CloudDiagram:
    """Collect a high-level GCP inventory diagram."""
    account_email = "unknown-account"
    projects: list[dict[str, Any]]

    if project:
        config = _run_json_command(
            ("gcloud", "config", "list", "--format=json"),
            timeout=timeout,
            description="GCP config",
        )
        core = config.get("core", {})
        account_email = str(core.get("account", account_email))
        projects = [{"projectId": project, "name": project}]
    elif scope == "current":
        config = _run_json_command(
            ("gcloud", "config", "list", "--format=json"),
            timeout=timeout,
            description="GCP config",
        )
        core = config.get("core", {})
        account_email = str(core.get("account", account_email))
        configured_project = core.get("project")
        if not configured_project:
            raise ValueError("No active GCP project is configured.")
        projects = [{"projectId": configured_project, "name": configured_project}]
    else:
        accounts = _run_json_command(
            ("gcloud", "auth", "list", "--format=json"),
            timeout=timeout,
            description="GCP accounts",
        )
        account_email = _extract_active_gcp_account(accounts) or account_email
        listed_projects = _run_json_command(
            ("gcloud", "projects", "list", "--format=json"),
            timeout=timeout,
            description="GCP projects",
        )
        projects = list(listed_projects)

    title = f"GCP Inventory {account_email}"
    diagram = CloudDiagram(provider="gcp", title=title, scope=scope)
    root_id = f"gcp:account:{account_email}"
    _add_node(diagram, root_id, f"GCP Account\n{account_email}", "account")

    for project_data in projects:
        project_id = project_data.get("projectId") or project_data.get("project_id")
        if not project_id:
            continue
        project_name = project_data.get("name", project_id)
        project_node_id = f"gcp:project:{project_id}"
        _add_node(diagram, project_node_id, f"Project\n{project_name}", "project")
        _add_edge(diagram, root_id, project_node_id)

        network_ids: dict[str, str] = {}
        if "network" in include:
            networks = _safe_run_json_command(
                diagram,
                ("gcloud", "compute", "networks", "list", f"--project={project_id}", "--format=json"),
                timeout=timeout,
                description=f"GCP networks in {project_id}",
            )
            for network in networks:
                name = network.get("name")
                if not name:
                    continue
                network_node_id = f"gcp:network:{project_id}:{name}"
                network_ids[name] = network_node_id
                _add_node(diagram, network_node_id, f"Network\n{name}", "network")
                _add_edge(diagram, project_node_id, network_node_id)

            subnets = _safe_run_json_command(
                diagram,
                (
                    "gcloud",
                    "compute",
                    "networks",
                    "subnets",
                    "list",
                    f"--project={project_id}",
                    "--format=json",
                ),
                timeout=timeout,
                description=f"GCP subnets in {project_id}",
            )
            for subnet in subnets:
                name = subnet.get("name")
                if not name:
                    continue
                network_name = _basename(subnet.get("network"))
                subnet_node_id = f"gcp:subnet:{project_id}:{name}"
                _add_node(diagram, subnet_node_id, f"Subnet\n{name}", "subnet")
                parent_id = network_ids.get(network_name, project_node_id)
                _add_edge(diagram, parent_id, subnet_node_id)

        if "compute" in include:
            instances = _safe_run_json_command(
                diagram,
                ("gcloud", "compute", "instances", "list", f"--project={project_id}", "--format=json"),
                timeout=timeout,
                description=f"GCP instances in {project_id}",
            )
            for instance in instances:
                name = instance.get("name")
                if not name:
                    continue
                node_id = f"gcp:instance:{project_id}:{name}"
                _add_node(diagram, node_id, f"VM\n{name}", "instance")
                subnet_name = _instance_subnet_name(instance)
                if subnet_name and _has_node(diagram, f"gcp:subnet:{project_id}:{subnet_name}"):
                    _add_edge(diagram, f"gcp:subnet:{project_id}:{subnet_name}", node_id)
                else:
                    _add_edge(diagram, project_node_id, node_id)

        if "storage" in include:
            buckets = _safe_run_json_command(
                diagram,
                ("gcloud", "storage", "buckets", "list", f"--project={project_id}", "--format=json"),
                timeout=timeout,
                description=f"GCP buckets in {project_id}",
            )
            for bucket in buckets:
                name = bucket.get("name")
                if not name:
                    continue
                node_id = f"gcp:bucket:{project_id}:{name}"
                _add_node(diagram, node_id, f"Bucket\n{name}", "bucket")
                _add_edge(diagram, project_node_id, node_id, "storage")

    diagram.metadata["account"] = account_email
    diagram.metadata["project_count"] = len(projects)
    return diagram


def _render_mermaid(diagram: CloudDiagram) -> str:
    """Render a CloudDiagram as Mermaid flowchart syntax."""
    lines = ["flowchart TD", f"%% {diagram.title}"]
    node_map = {node.id: _mermaid_id(node.id, index) for index, node in enumerate(diagram.nodes)}
    for node in diagram.nodes:
        mermaid_id = node_map[node.id]
        label = _escape_mermaid(node.label)
        lines.append(f'    {mermaid_id}["{label}"]')
    for edge in diagram.edges:
        source = node_map.get(edge.source)
        target = node_map.get(edge.target)
        if source is None or target is None:
            continue
        if edge.label:
            label = _escape_mermaid(edge.label)
            lines.append(f"    {source} -->|{label}| {target}")
        else:
            lines.append(f"    {source} --> {target}")
    for warning_index, warning in enumerate(diagram.warnings):
        warning_id = f"warn_{warning_index}"
        lines.append(f'    {warning_id}["Warning: {_escape_mermaid(warning)}"]')
        if diagram.nodes:
            lines.append(f"    {node_map[diagram.nodes[0].id]} -.-> {warning_id}")
    return "\n".join(lines)


def _render_dot(diagram: CloudDiagram) -> str:
    """Render a CloudDiagram as Graphviz DOT."""
    lines = [f'digraph "{_escape_dot(diagram.title)}" {{', "  rankdir=TB;"]
    for node in diagram.nodes:
        node_id = _dot_id(node.id)
        label = _escape_dot(node.label)
        lines.append(f'  {node_id} [label="{label}"];')
    for edge in diagram.edges:
        source = _dot_id(edge.source)
        target = _dot_id(edge.target)
        if edge.label:
            label = _escape_dot(edge.label)
            lines.append(f'  {source} -> {target} [label="{label}"];')
        else:
            lines.append(f"  {source} -> {target};")
    lines.append("}")
    return "\n".join(lines)


def _normalize_diagram_scope(provider: str, scope: str | None) -> str:
    """Normalize and validate collection scope."""
    if scope is None:
        return DEFAULT_DIAGRAM_SCOPE[provider]
    normalized = scope.strip().lower()
    if normalized not in {"current", "all"}:
        raise ValueError("Diagram scope must be either 'current' or 'all'.")
    return normalized


def _normalize_include(include: Iterable[str] | None) -> set[str]:
    """Normalize service categories included in a diagram."""
    if include is None:
        return set(SUPPORTED_DIAGRAM_SERVICES)

    normalized: set[str] = set()
    for value in include:
        candidate = value.strip().lower()
        if candidate not in SUPPORTED_DIAGRAM_SERVICES:
            supported = ", ".join(SUPPORTED_DIAGRAM_SERVICES)
            raise ValueError(f"Unsupported diagram service '{value}'. Supported services: {supported}")
        normalized.add(candidate)
    if not normalized:
        return set(SUPPORTED_DIAGRAM_SERVICES)
    return normalized


def _run_json_command(
    command: Sequence[str],
    *,
    timeout: float,
    description: str,
) -> Any:
    """Run a CLI command and parse its JSON output."""
    try:
        result = subprocess.run(
            list(command),
            capture_output=True,
            check=True,
            text=True,
            timeout=timeout,
        )
    except FileNotFoundError as exc:
        raise ValueError(f"{description} failed because the CLI executable was not found.") from exc
    except subprocess.TimeoutExpired as exc:
        raise ValueError(f"{description} timed out after {timeout:g}s.") from exc
    except subprocess.CalledProcessError as exc:
        stderr = exc.stderr.strip() if exc.stderr else ""
        raise ValueError(f"{description} failed: {stderr or exc.stdout.strip() or exc}") from exc

    payload = result.stdout.strip()
    if not payload:
        return {}

    try:
        return json.loads(payload)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{description} did not return valid JSON.") from exc


def _safe_run_json_command(
    diagram: CloudDiagram,
    command: Sequence[str],
    *,
    timeout: float,
    description: str,
) -> Any:
    """Run a JSON command and capture warnings instead of failing the whole diagram."""
    try:
        return _run_json_command(command, timeout=timeout, description=description)
    except ValueError as exc:
        diagram.warnings.append(str(exc))
        return {}


def _with_aws_profile(command: Sequence[str], profile: str | None) -> tuple[str, ...]:
    """Append an AWS profile to a command if provided."""
    if profile is None:
        return tuple(command)
    return tuple([*command, "--profile", profile])


def _add_node(diagram: CloudDiagram, node_id: str, label: str, kind: str) -> None:
    """Add a node if it does not already exist."""
    if _has_node(diagram, node_id):
        return
    diagram.nodes.append(DiagramNode(id=node_id, label=label, kind=kind, provider=diagram.provider))


def _add_edge(diagram: CloudDiagram, source: str, target: str, label: str | None = None) -> None:
    """Add an edge if it does not already exist."""
    candidate = DiagramEdge(source=source, target=target, label=label)
    if candidate in diagram.edges:
        return
    diagram.edges.append(candidate)


def _has_node(diagram: CloudDiagram, node_id: str) -> bool:
    """Return whether a node exists in the diagram."""
    return any(node.id == node_id for node in diagram.nodes)


def _extract_aws_name(tags: Sequence[Mapping[str, Any]]) -> str | None:
    """Extract the Name tag from an AWS tag list."""
    for tag in tags:
        if tag.get("Key") == "Name":
            value = tag.get("Value")
            return str(value) if value else None
    return None


def _display_name(identifier: str, name: str | None) -> str:
    """Render an identifier with an optional display name."""
    if name:
        return f"{identifier}\n{name}"
    return identifier


def _extract_active_gcp_account(accounts: Any) -> str | None:
    """Extract the active account from gcloud auth list output."""
    if not isinstance(accounts, list):
        return None
    for account in accounts:
        if account.get("status") == "ACTIVE":
            value = account.get("account")
            return str(value) if value else None
    return None


def _basename(value: Any) -> str | None:
    """Return the last path element from a URL-like resource string."""
    if not value or not isinstance(value, str):
        return None
    return value.rstrip("/").split("/")[-1]


def _instance_subnet_name(instance: Mapping[str, Any]) -> str | None:
    """Extract the first subnet name from a GCP instance payload."""
    interfaces = instance.get("networkInterfaces")
    if not isinstance(interfaces, list) or not interfaces:
        return None
    subnetwork = interfaces[0].get("subnetwork")
    return _basename(subnetwork)


def _mermaid_id(raw: str, index: int) -> str:
    """Build a Mermaid-safe identifier."""
    sanitized = re.sub(r"[^a-zA-Z0-9_]", "_", raw)
    if not sanitized or sanitized[0].isdigit():
        sanitized = f"n_{sanitized}"
    return f"{sanitized}_{index}"


def _escape_mermaid(value: str) -> str:
    """Escape Mermaid label content."""
    return value.replace('"', '\\"').replace("\n", "<br/>")


def _dot_id(raw: str) -> str:
    """Return a DOT-safe node identifier."""
    sanitized = re.sub(r"[^a-zA-Z0-9_]", "_", raw)
    if not sanitized or sanitized[0].isdigit():
        sanitized = f"n_{sanitized}"
    return sanitized


def _escape_dot(value: str) -> str:
    """Escape DOT label content."""
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def shell_join(command: Sequence[str]) -> str:
    """Render a command as shell-safe text."""
    return shlex.join(list(command))
