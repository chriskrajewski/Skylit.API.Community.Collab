"""Static guards on the Terraform under infra/ that ``terraform test`` cannot assert.

``prevent_destroy`` is a lifecycle meta-argument (invisible to plan assertions),
and security-group ingress shape must hold for every file, not only the resources
a test happens to plan. Plain text checks, stdlib only.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

INFRA = Path(__file__).resolve().parents[2] / "infra"


def _tf_files() -> list[Path]:
    return sorted(p for p in INFRA.rglob("*.tf") if ".terraform" not in p.parts)


def _strip_comments(text: str) -> str:
    return "\n".join(re.sub(r"\s*#.*$", "", line) for line in text.splitlines())


def _blocks(resource_type: str) -> list[tuple[Path, str, str]]:
    """Every ``resource "<type>" "<name>" { ... }`` block as (file, name, body)."""
    found = []
    header = re.compile(rf'^resource\s+"{re.escape(resource_type)}"\s+"([^"]+)"\s*\{{', re.M)
    for path in _tf_files():
        text = _strip_comments(path.read_text())
        for match in header.finditer(text):
            depth, end = 1, match.end()
            while depth and end < len(text):
                depth += {"{": 1, "}": -1}.get(text[end], 0)
                end += 1
            found.append((path, match.group(1), text[match.end() : end - 1]))
    return found


def _block(resource_type: str, name: str, file_part: str) -> str:
    matches = [
        body
        for path, n, body in _blocks(resource_type)
        if n == name and file_part in path.relative_to(INFRA).as_posix()
    ]
    assert len(matches) == 1, f"expected one {resource_type}.{name} under {file_part}"
    return matches[0]


@pytest.mark.parametrize(
    ("resource_type", "name", "file_part"),
    [
        ("aws_ebs_volume", "data", "modules/runner/"),
        ("aws_s3_bucket", "data", "modules/data_bucket/"),
        ("aws_s3_bucket", "state", "bootstrap/"),
    ],
)
def test_stateful_resources_prevent_destroy(resource_type: str, name: str, file_part: str) -> None:
    body = _block(resource_type, name, file_part)
    assert re.search(r"lifecycle\s*\{[^}]*prevent_destroy\s*=\s*true", body)


def test_no_standalone_security_group_rules() -> None:
    declared = re.compile(
        r'^resource\s+"(aws_vpc_security_group_ingress_rule|aws_security_group_rule)"', re.M
    )
    offenders = [
        p.relative_to(INFRA).as_posix()
        for p in _tf_files()
        if declared.search(_strip_comments(p.read_text()))
    ]
    assert offenders == []


RUNNER_SG_DESCRIPTION = "fse live runner host: no inbound, HTTPS outbound"

# Allowed ingress (attribute form only; never block-style ingress { }):
# - runner: HTTPS from the CloudFront prefix list when enable_ui
# - efs: NFS from fse-batch only (batch-only Data_Cache mount targets)
# - batch (and any other SG): empty
RUNNER_INGRESS = {
    "from_port": "443",
    "to_port": "443",
    "protocol": '"tcp"',
    "cidr_blocks": "[]",
    "ipv6_cidr_blocks": "[]",
    "security_groups": "[]",
    "prefix_list_ids": "[var.ui_origin_prefix_list_id]",
    "self": "false",
}

EFS_INGRESS = {
    "from_port": "2049",
    "to_port": "2049",
    "protocol": '"tcp"',
    "cidr_blocks": "[]",
    "ipv6_cidr_blocks": "[]",
    "prefix_list_ids": "[]",
    "security_groups": "[aws_security_group.batch.id]",
    "self": "false",
}


def test_security_groups_ingress_is_locked_down() -> None:
    blocks = _blocks("aws_security_group")
    groups = {name: body for _, name, body in blocks}
    assert {"runner", "batch", "efs"} <= set(groups), (
        "expected the runner, batch and efs security groups"
    )
    for path, name, body in blocks:
        assert not re.search(r"^\s*ingress\s*\{", body, re.M), f"{path}:{name}"
        if name not in ("runner", "efs"):
            assert re.search(r"^\s*ingress\s*=\s*\[\]", body, re.M), f"{path}:{name}"

    runner = re.search(
        r"^\s*ingress\s*=\s*var\.ui_origin_prefix_list_id\s*==\s*null\s*\?\s*\[\]\s*:"
        r"\s*\[\{(?P<obj>[^{}]*)\}\]",
        groups["runner"],
        re.M,
    )
    assert runner, "runner ingress must be the enable_ui conditional with one inline object"
    fields = dict(re.findall(r"^\s*(\w+)\s*=\s*(.+?)\s*$", runner.group("obj"), re.M))
    fields.pop("description", None)
    assert fields == RUNNER_INGRESS
    assert len(re.findall(r"^\s*ingress\s*=", groups["runner"], re.M)) == 1

    efs = re.search(r"^\s*ingress\s*=\s*\[\{(?P<obj>[^{}]*)\}\]", groups["efs"], re.M)
    assert efs, "efs ingress must be one inline object (NFS from fse-batch)"
    efs_fields = dict(re.findall(r"^\s*(\w+)\s*=\s*(.+?)\s*$", efs.group("obj"), re.M))
    efs_fields.pop("description", None)
    assert efs_fields == EFS_INGRESS
    assert len(re.findall(r"^\s*ingress\s*=", groups["efs"], re.M)) == 1
    assert re.search(r"^\s*egress\s*=\s*\[\]", groups["efs"], re.M), "efs must have no egress"


def test_runner_security_group_description_is_unchanged() -> None:
    # description is ForceNew: changing it would replace the SG attached to the instance.
    body = _block("aws_security_group", "runner", "modules/network/")
    assert re.search(rf'^\s*description\s*=\s*"{re.escape(RUNNER_SG_DESCRIPTION)}"', body, re.M)


def test_origin_secret_is_write_only() -> None:
    body = _block("aws_ssm_parameter", "origin_secret", "modules/ui_edge/")
    assert re.search(r"^\s*value_wo\s*=", body, re.M)
    assert re.search(r"^\s*value_wo_version\s*=", body, re.M)
    assert not re.search(r"^\s*(value|insecure_value)\s*=", body, re.M)


def test_no_output_exposes_a_secret() -> None:
    for path in _tf_files():
        text = _strip_comments(path.read_text())
        for match in re.finditer(r'^output\s+"[^"]+"\s*\{', text, re.M):
            depth, end = 1, match.end()
            while depth and end < len(text):
                depth += {"{": 1, "}": -1}.get(text[end], 0)
                end += 1
            body = text[match.end() : end - 1]
            assert "client_secret" not in body, path
            assert "random_password" not in body, path


def test_no_nat_gateway() -> None:
    assert _blocks("aws_nat_gateway") == []


def test_no_public_ip_flag_and_no_ssh_key() -> None:
    for path in _tf_files():
        text = _strip_comments(path.read_text())
        assert not re.search(r"associate_public_ip_address\s*=\s*true", text), path
        assert not re.search(r'^resource\s+"aws_key_pair"', text, re.M), path
    instances = _blocks("aws_instance")
    assert instances, "expected the runner instance"
    for path, name, body in instances:
        assert not re.search(r"^\s*key_name\s*=", body, re.M), f"{path}:{name}"


def test_secret_parameters_are_write_only() -> None:
    # value_wo keeps the hand-set secrets out of the state file; value would not.
    body = _block("aws_ssm_parameter", "secret", "modules/params/")
    assert re.search(r"^\s*value_wo\s*=", body, re.M)
    assert re.search(r"^\s*value_wo_version\s*=", body, re.M)
    assert not re.search(r"^\s*(value|insecure_value)\s*=", body, re.M)


def test_runner_ignores_ami_changes() -> None:
    body = _block("aws_instance", "this", "modules/runner/")
    assert re.search(r"ignore_changes\s*=\s*\[\s*ami\s*\]", body)


# ------------------------------------------------------------ ui_host (design 11.8)

UI_HOST = INFRA / "modules" / "ui_host"
UI_HOST_FILES = sorted((UI_HOST / "files").iterdir())
UI_HOST_TEXT = sorted([*UI_HOST_FILES, *(UI_HOST / "templates").iterdir()])


@pytest.mark.parametrize("name", ["runner_mode", "runner_config"])
def test_console_parameters_ignore_value_changes(name: str) -> None:
    # The console writes the value; an apply must never reset it.
    body = _block("aws_ssm_parameter", name, "modules/ui_host/")
    assert re.search(r"lifecycle\s*\{[^}]*ignore_changes\s*=\s*\[\s*value\s*\]", body)


@pytest.mark.parametrize("path", UI_HOST_FILES, ids=lambda p: p.name)
def test_ui_host_files_survive_ssm_and_the_heredoc(path: Path) -> None:
    # SSM substitutes {{ ... }} in document content; FSE_EOF ends the bundle heredoc.
    text = path.read_text()
    assert "{{" not in text
    assert "FSE_EOF" not in text.splitlines()


def test_ui_host_never_uses_run_shell_script() -> None:
    for path in [*UI_HOST.glob("*.tf"), *UI_HOST_TEXT]:
        assert "AWS-RunShellScript" not in path.read_text(), path


def test_ui_host_has_one_association_and_it_is_the_bundle() -> None:
    associations = [
        (path, body)
        for path, _, body in _blocks("aws_ssm_association")
        if "modules/ui_host/" in path.relative_to(INFRA).as_posix()
    ]
    assert len(associations) == 1
    body = associations[0][1]
    assert re.search(r"^\s*name\s*=\s*aws_ssm_document\.ui_host\.name\s*$", body, re.M)
    assert re.search(r"document_version\s*=\s*aws_ssm_document\.ui_host\.latest_version", body)
    assert "ui_host_remove" not in body


def test_console_dropin_orders_state_before_alert() -> None:
    lines = [
        line.strip()
        for line in (UI_HOST / "files" / "20-console.conf").read_text().splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    assert lines == [
        "[Service]",
        "ExecStartPre=/usr/local/bin/fse-runner-mode.sh",
        "ExecStartPre=-/usr/local/bin/fse-sync-cache.sh",
        "ExecStart=",
        "ExecStart=/usr/local/bin/fse-console-start.sh",
        "ExecStartPost=-/usr/local/bin/fse-runner-state.sh started",
        "ExecStopPost=",
        "ExecStopPost=-/usr/local/bin/fse-runner-state.sh stopped",
        "ExecStopPost=/usr/local/bin/fse-alert.sh",
    ]


def test_only_data_host_string_is_the_read_only_mount() -> None:
    # Host files live in /var/lib/fse-host: /data is chowned to the container uid.
    hits = [
        (path.name, line.strip())
        for path in UI_HOST_TEXT
        for line in path.read_text().splitlines()
        if "/data/host" in line
    ]
    assert hits == [("fse-ui-start.sh", "-v /var/lib/fse-host:/data/host:ro \\")]


def _written_paths() -> set[str]:
    bundle = (UI_HOST / "templates" / "bundle.sh.tftpl").read_text()
    control = (UI_HOST / "templates" / "runner-control.yaml").read_text()
    paths = set(re.findall(r"/dev/stdin\s+(/\S+)", bundle))
    paths |= set(re.findall(r"touch\s+(/\S+)", control))
    state_script = (UI_HOST / "files" / "fse-runner-state.sh").read_text()
    paths |= set(re.findall(r"FSE_STATE_FILE:-(/[^}]+)\}", state_script))
    return paths


def test_remove_script_deletes_everything_the_bundle_and_control_write() -> None:
    paths = _written_paths()
    assert {
        "/etc/fse/ui.env",
        "/etc/systemd/system/fse-runner.service.d/20-console.conf",
        "/var/lib/fse-host/runner-held",
        "/var/lib/fse-host/runner-state.json",
    } <= paths
    # 16 bundle files (incl. the cache sync and recordings offload scripts,
    # services and timers, and fse-console-start.sh), the hold marker (control
    # document) and the state file.
    assert "/usr/local/bin/fse-console-start.sh" in paths
    assert len(paths) == 18
    remove = (UI_HOST / "templates" / "ui-host-remove.sh").read_text()
    removed = set(re.findall(r"^\s*(/\S+?)\s*\\?$", remove, re.M))
    assert paths <= removed, sorted(paths - removed)
    assert "systemctl disable --now fse-ui.service fse-edge.service" in remove
    assert "systemctl disable --now fse-cache-sync.timer fse-cache-sync.service" in remove
    assert (
        "systemctl disable --now fse-offload-recordings.timer fse-offload-recordings.service"
        in remove
    )
    assert "docker rm -f fse-ui fse-edge" in remove
    assert "rm -rf /run/fse-control" in remove


def test_bundle_installs_the_console_start_script_before_daemon_reload() -> None:
    bundle = (UI_HOST / "templates" / "bundle.sh.tftpl").read_text()
    install = "install -m 0755 -o root -g root /dev/stdin /usr/local/bin/fse-console-start.sh"
    assert install in bundle
    assert bundle.index(install) < bundle.index("systemctl daemon-reload")


def test_plugin_orders_switch_is_terraform_owned() -> None:
    # No ignore_changes: an apply resets any hand edit of /fse/control/plugin_orders.
    body = _block("aws_ssm_parameter", "plugin_orders", "modules/params/")
    assert '"/${var.name_prefix}/control/plugin_orders"' in body
    assert "ignore_changes" not in body


# The workflows are not part of this copy; add yours at .github/workflows/fse-infra.yml.
INFRA_WORKFLOW = INFRA.parent / ".github" / "workflows" / "fse-infra.yml"


@pytest.mark.skipif(not INFRA_WORKFLOW.is_file(), reason="no fse-infra workflow in this copy")
def test_infra_workflow_passes_the_plugin_orders_inputs_in_plan_and_apply() -> None:
    text = INFRA_WORKFLOW.read_text()
    for line in (
        "PLUGIN_ORDERS_ENABLED: ${{ vars.FSE_PLUGIN_ORDERS_ENABLED }}",
        "TOPSTEP_TERMS_VERIFIED_ON: ${{ vars.FSE_TOPSTEP_TERMS_VERIFIED_ON }}",
        'add TF_VAR_plugin_orders_enabled "$PLUGIN_ORDERS_ENABLED"',
        'add TF_VAR_topstep_terms_verified_on "$TOPSTEP_TERMS_VERIFIED_ON"',
    ):
        assert text.count(line) == 2, line


def test_bundle_never_restarts_the_runner() -> None:
    bundle = (UI_HOST / "templates" / "bundle.sh.tftpl").read_text()
    commands = [line for line in bundle.splitlines() if line.strip().startswith("systemctl")]
    assert commands
    assert all("fse-runner" not in line for line in commands), commands
