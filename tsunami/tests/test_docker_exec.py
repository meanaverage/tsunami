"""Tests for Docker execution helper logic."""

from tsunami import docker_exec


def test_parse_port_list_defaults():
    assert docker_exec.parse_port_list("") == docker_exec.DEFAULT_PORTS


def test_parse_port_list_dedupes_and_preserves_order():
    assert docker_exec.parse_port_list("5173,9876,5173") == (5173, 9876)


def test_parse_port_list_rejects_invalid_port():
    try:
        docker_exec.parse_port_list("99999")
    except ValueError as exc:
        assert "Invalid Docker port" in str(exc)
    else:
        raise AssertionError("Expected invalid port to raise ValueError")


def test_host_path_to_container_for_repo_relative_path():
    mapped = docker_exec.host_path_to_container("workspace/deliverables/demo/src/App.tsx")
    assert mapped.endswith("/workspace/deliverables/demo/src/App.tsx")
    assert mapped.startswith(docker_exec.container_root())


def test_host_path_to_container_leaves_external_paths_unchanged():
    assert docker_exec.host_path_to_container("/tmp/outside-file.txt") == "/tmp/outside-file.txt"
