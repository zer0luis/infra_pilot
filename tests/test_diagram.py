"""Tests for cloud inventory diagram helpers."""

import io
import json
import subprocess
from contextlib import redirect_stdout
import unittest
from unittest.mock import patch

from infra_pilot.cli import main
from infra_pilot.diagram import (
    CloudDiagram,
    DiagramEdge,
    DiagramNode,
    collect_tenant_diagram,
    plan_diagram_commands,
    render_diagram,
)


def _completed(payload: object) -> subprocess.CompletedProcess:
    """Create a successful JSON subprocess result."""
    return subprocess.CompletedProcess(args=[], returncode=0, stdout=json.dumps(payload), stderr="")


class DiagramTests(unittest.TestCase):
    def test_plan_diagram_commands_for_aws_profile(self) -> None:
        commands = plan_diagram_commands("aws", include=["network", "storage"], profile="dev")
        self.assertIn(("aws", "sts", "get-caller-identity", "--output", "json", "--profile", "dev"), commands)
        self.assertIn(("aws", "s3api", "list-buckets", "--output", "json", "--profile", "dev"), commands)

    @patch("infra_pilot.diagram.subprocess.run")
    def test_collect_aws_diagram_from_mocked_cli(self, run_mock) -> None:
        run_mock.side_effect = [
            _completed({"Account": "123456789012"}),
            _completed({"Regions": [{"RegionName": "us-east-1", "OptInStatus": "opt-in-not-required"}]}),
            _completed({"Buckets": [{"Name": "demo-bucket"}]}),
            _completed({"Vpcs": [{"VpcId": "vpc-123", "Tags": [{"Key": "Name", "Value": "core"}]}]}),
            _completed({"Subnets": [{"SubnetId": "subnet-123", "VpcId": "vpc-123", "Tags": [{"Key": "Name", "Value": "app"}]}]}),
            _completed(
                {
                    "Reservations": [
                        {
                            "Instances": [
                                {
                                    "InstanceId": "i-123",
                                    "SubnetId": "subnet-123",
                                    "Tags": [{"Key": "Name", "Value": "web"}],
                                }
                            ]
                        }
                    ]
                }
            ),
        ]

        diagram = collect_tenant_diagram("aws", include=["network", "compute", "storage"], profile="dev")

        node_ids = {node.id for node in diagram.nodes}
        self.assertIn("aws:account:123456789012", node_ids)
        self.assertIn("aws:bucket:demo-bucket", node_ids)
        self.assertIn("aws:vpc:vpc-123", node_ids)
        self.assertIn("aws:subnet:subnet-123", node_ids)
        self.assertIn("aws:instance:i-123", node_ids)
        self.assertTrue(any(edge.source == "aws:subnet:subnet-123" and edge.target == "aws:instance:i-123" for edge in diagram.edges))

    def test_render_mermaid_contains_expected_edges(self) -> None:
        diagram = CloudDiagram(provider="aws", title="Demo", scope="current")
        diagram.nodes = [
            DiagramNode(
                id="root",
                label="Root",
                kind="account",
                provider="aws",
            ),
            DiagramNode(
                id="child",
                label="Child",
                kind="bucket",
                provider="aws",
            ),
        ]
        diagram.edges = [
            DiagramEdge(
                source="root",
                target="child",
                label="storage",
            )
        ]

        rendered = render_diagram(diagram, format="mermaid")

        self.assertIn("flowchart TD", rendered)
        self.assertIn("-->|storage|", rendered)

    def test_cli_diagram_dry_run(self) -> None:
        output = io.StringIO()

        with redirect_stdout(output):
            exit_code = main(["diagram", "gcp", "--scope", "all", "--service", "network", "--dry-run"])

        self.assertEqual(exit_code, 0)
        rendered = output.getvalue()
        self.assertIn("gcloud auth list --format=json", rendered)
        self.assertIn("gcloud compute networks list '--project=<project-id>' --format=json", rendered)


if __name__ == "__main__":
    unittest.main()
