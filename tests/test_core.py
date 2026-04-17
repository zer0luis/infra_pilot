"""Tests for the cloud CLI helper package."""

import io
import subprocess
from contextlib import redirect_stderr, redirect_stdout
import unittest
from unittest.mock import patch

from infra_pilot import (
    ProviderStatus,
    build_command,
    get_provider_status,
    get_version,
    plan_create_resource,
    plan_login,
    plan_set_default_region,
    plan_use_context,
    plan_update_resource,
    resolve_provider,
)
from infra_pilot.cli import main


class CoreTests(unittest.TestCase):
    def test_resolve_provider_alias(self) -> None:
        self.assertEqual(resolve_provider("az").provider, "azure")

    def test_build_command_for_gcp(self) -> None:
        self.assertEqual(
            build_command("gcloud", ["projects", "list"]),
            ["gcloud", "projects", "list"],
        )

    def test_plan_create_aws_bucket(self) -> None:
        operation = plan_create_resource("aws", "bucket", "demo-bucket", region="us-east-1")
        self.assertEqual(
            list(operation.command),
            ["aws", "s3", "mb", "s3://demo-bucket", "--region", "us-east-1"],
        )

    def test_plan_update_azure_resource_group_tags(self) -> None:
        operation = plan_update_resource(
            "azure",
            "resource-group",
            "demo-rg",
            tags={"env": "dev", "owner": "platform"},
        )
        self.assertEqual(
            list(operation.command),
            ["az", "group", "update", "--name", "demo-rg", "--set", "tags.env=dev", "tags.owner=platform"],
        )

    def test_plan_login_for_aws_profile(self) -> None:
        operation = plan_login("aws", profile="dev", use_device_code=True)
        self.assertEqual(
            list(operation.command),
            ["aws", "sso", "login", "--profile", "dev", "--use-device-code"],
        )

    def test_plan_use_aws_profile_renders_shell_export(self) -> None:
        operation = plan_use_context("aws", "dev-profile", context_type="profile")
        self.assertFalse(operation.executable)
        self.assertEqual(
            operation.render(),
            "export AWS_PROFILE=dev-profile AWS_DEFAULT_PROFILE=dev-profile",
        )

    def test_plan_set_region_for_gcp(self) -> None:
        operation = plan_set_default_region("gcp", "us-central1")
        self.assertEqual(
            list(operation.command),
            ["gcloud", "config", "set", "compute/region", "us-central1"],
        )

    @patch("infra_pilot.core.is_installed", return_value=True)
    @patch("infra_pilot.core.subprocess.run")
    def test_get_version_handles_stderr_output(self, run_mock, _installed_mock) -> None:
        run_mock.return_value = subprocess.CompletedProcess(
            args=["aws", "--version"],
            returncode=0,
            stdout="",
            stderr="aws-cli/2.15.0 Python/3.11.0 GenericPlatform\n",
        )

        version = get_version("aws")

        self.assertEqual(version, "aws-cli/2.15.0 Python/3.11.0 GenericPlatform")

    @patch("infra_pilot.core.find_executable", return_value=None)
    def test_provider_status_when_missing(self, _find_mock) -> None:
        status = get_provider_status("azure")
        self.assertFalse(status.available)
        self.assertIsNone(status.version)

    @patch("infra_pilot.core.is_installed", return_value=True)
    @patch("infra_pilot.core.subprocess.run", side_effect=subprocess.TimeoutExpired(cmd=["az", "version"], timeout=1))
    def test_get_version_timeout(self, _run_mock, _installed_mock) -> None:
        version = get_version("azure", timeout=1)
        self.assertEqual(version, "Timed out after 1s")

    @patch("infra_pilot.cli.list_supported_providers", return_value=["aws", "azure", "gcp"])
    @patch("infra_pilot.cli.resolve_provider")
    def test_cli_providers_command(self, resolve_mock, _providers_mock) -> None:
        resolve_mock.side_effect = [
            resolve_provider("aws"),
            resolve_provider("azure"),
            resolve_provider("gcp"),
        ]
        output = io.StringIO()

        with redirect_stdout(output):
            exit_code = main(["providers"])

        self.assertEqual(exit_code, 0)
        rendered = output.getvalue()
        self.assertIn("aws: executable=aws", rendered)
        self.assertIn("azure: executable=az", rendered)

    @patch("infra_pilot.cli.get_provider_status")
    def test_cli_create_dry_run(self, status_mock) -> None:
        status_mock.return_value = ProviderStatus(
            provider="aws",
            executable="aws",
            available=True,
            path="/opt/homebrew/bin/aws",
            version="aws-cli/2",
        )
        output = io.StringIO()

        with redirect_stdout(output):
            exit_code = main(
                [
                    "create",
                    "aws",
                    "bucket",
                    "demo-bucket",
                    "--region",
                    "us-east-1",
                    "--dry-run",
                ]
            )

        self.assertEqual(exit_code, 0)
        self.assertIn("aws s3 mb s3://demo-bucket --region us-east-1", output.getvalue())

    def test_cli_update_dry_run_does_not_require_installed_cli(self) -> None:
        output = io.StringIO()

        with redirect_stdout(output):
            exit_code = main(
                [
                    "update",
                    "gcp",
                    "bucket",
                    "demo-bucket",
                    "--project",
                    "my-project",
                    "--tag",
                    "env=prod",
                    "--enable-versioning",
                    "--dry-run",
                ]
            )

        self.assertEqual(exit_code, 0)
        self.assertIn(
            "gcloud storage buckets update gs://demo-bucket --project=my-project --update-labels=env=prod --versioning",
            output.getvalue(),
        )

    def test_cli_use_aws_profile_emits_shell_command(self) -> None:
        output = io.StringIO()

        with redirect_stdout(output):
            exit_code = main(["use", "aws", "dev-profile", "--kind", "profile"])

        self.assertEqual(exit_code, 0)
        self.assertEqual(
            output.getvalue().strip(),
            "export AWS_PROFILE=dev-profile AWS_DEFAULT_PROFILE=dev-profile",
        )

    def test_cli_login_dry_run_for_azure(self) -> None:
        output = io.StringIO()

        with redirect_stdout(output):
            exit_code = main(["login", "azure", "--tenant", "tenant-123", "--use-device-code", "--dry-run"])

        self.assertEqual(exit_code, 0)
        self.assertEqual(output.getvalue().strip(), "az login --tenant tenant-123 --use-device-code")

    def test_cli_set_region_dry_run_for_aws_profile(self) -> None:
        output = io.StringIO()

        with redirect_stdout(output):
            exit_code = main(["set-region", "aws", "us-east-1", "--profile", "dev", "--dry-run"])

        self.assertEqual(exit_code, 0)
        self.assertEqual(output.getvalue().strip(), "aws configure set region us-east-1 --profile dev")

    @patch("infra_pilot.cli.get_provider_status")
    def test_cli_delete_requires_confirmation(self, status_mock) -> None:
        status_mock.return_value = ProviderStatus(
            provider="azure",
            executable="az",
            available=True,
            path="/opt/homebrew/bin/az",
            version="2.76.0",
        )
        stderr_output = io.StringIO()

        with redirect_stderr(stderr_output):
            exit_code = main(["delete", "azure", "resource-group", "demo-rg"])

        self.assertEqual(exit_code, 2)
        self.assertIn("Delete requires --yes or --dry-run.", stderr_output.getvalue())


if __name__ == "__main__":
    unittest.main()
