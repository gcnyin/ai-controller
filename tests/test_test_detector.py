"""测试 test_detector 模块 —— 测试命令自动发现。"""

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

_PYTHON = sys.executable
_PYTEST_CMD = [_PYTHON, "-m", "pytest", "-x", "-q"]

from ai_controller.test_detector import (
    detect_test_command,
    _check_pytest_ini,
    _check_pyproject_toml,
    _check_tox_ini,
    _check_package_json,
    _check_cargo_toml,
    _check_go_mod,
    _check_build_sbt,
    _check_deno,
    _check_makefile,
)


# ═══════════════════════════════════════════════════════════════════════
# 各检测函数独立测试
# ═══════════════════════════════════════════════════════════════════════

class TestCheckPytestIni:
    def test_found(self, tmp_path):
        (tmp_path / "pytest.ini").write_text("[pytest]\n")
        assert _check_pytest_ini(tmp_path) == _PYTEST_CMD

    def test_not_found(self, tmp_path):
        assert _check_pytest_ini(tmp_path) is None


class TestCheckPyprojectToml:
    def test_found(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text(
            "[tool.pytest.ini_options]\naddopts = \"-v\"\n",
            encoding="utf-8",
        )
        assert _check_pyproject_toml(tmp_path) == _PYTEST_CMD

    def test_not_found(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text("[build-system]\n", encoding="utf-8")
        assert _check_pyproject_toml(tmp_path) is None

    def test_no_file(self, tmp_path):
        assert _check_pyproject_toml(tmp_path) is None


class TestCheckToxIni:
    def test_found(self, tmp_path):
        (tmp_path / "tox.ini").write_text("[tox]\n")
        assert _check_tox_ini(tmp_path) == ["tox"]

    def test_not_found(self, tmp_path):
        assert _check_tox_ini(tmp_path) is None


class TestCheckPackageJson:
    def test_npm_test(self, tmp_path):
        pkg = {"scripts": {"test": "jest"}}
        (tmp_path / "package.json").write_text(json.dumps(pkg))
        assert _check_package_json(tmp_path) == ["npm", "test"]

    def test_yarn_preferred(self, tmp_path):
        pkg = {"scripts": {"test": "jest"}}
        (tmp_path / "package.json").write_text(json.dumps(pkg))
        (tmp_path / "yarn.lock").write_text("")
        assert _check_package_json(tmp_path) == ["yarn", "test"]

    def test_pnpm_preferred(self, tmp_path):
        pkg = {"scripts": {"test": "jest"}}
        (tmp_path / "package.json").write_text(json.dumps(pkg))
        (tmp_path / "pnpm-lock.yaml").write_text("")
        assert _check_package_json(tmp_path) == ["pnpm", "test"]

    def test_no_scripts_test(self, tmp_path):
        pkg = {"name": "test"}
        (tmp_path / "package.json").write_text(json.dumps(pkg))
        assert _check_package_json(tmp_path) is None

    def test_no_file(self, tmp_path):
        assert _check_package_json(tmp_path) is None


class TestCheckCargoToml:
    def test_found(self, tmp_path):
        (tmp_path / "Cargo.toml").write_text("[package]\nname = \"test\"\n")
        assert _check_cargo_toml(tmp_path) == ["cargo", "test"]

    def test_not_found(self, tmp_path):
        assert _check_cargo_toml(tmp_path) is None


class TestCheckGoMod:
    def test_found(self, tmp_path):
        (tmp_path / "go.mod").write_text("module example.com/test\n")
        assert _check_go_mod(tmp_path) == ["go", "test", "./..."]

    def test_not_found(self, tmp_path):
        assert _check_go_mod(tmp_path) is None


class TestCheckBuildSbt:
    def test_found(self, tmp_path):
        (tmp_path / "build.sbt").write_text('name := "test"\n')
        assert _check_build_sbt(tmp_path) == ["sbt", "test"]

    def test_not_found(self, tmp_path):
        assert _check_build_sbt(tmp_path) is None


class TestCheckDeno:
    def test_deno_json(self, tmp_path):
        (tmp_path / "deno.json").write_text("{}\n")
        assert _check_deno(tmp_path) == ["deno", "test"]

    def test_deno_jsonc(self, tmp_path):
        (tmp_path / "deno.jsonc").write_text("{}\n")
        assert _check_deno(tmp_path) == ["deno", "test"]

    def test_not_found(self, tmp_path):
        assert _check_deno(tmp_path) is None


class TestCheckMakefile:
    def test_found(self, tmp_path):
        (tmp_path / "Makefile").write_text("test:\n\tnpm test\n")
        assert _check_makefile(tmp_path) == ["make", "test"]

    def test_no_test_target(self, tmp_path):
        (tmp_path / "Makefile").write_text("build:\n\tgo build\n")
        assert _check_makefile(tmp_path) is None

    def test_no_file(self, tmp_path):
        assert _check_makefile(tmp_path) is None


# ═══════════════════════════════════════════════════════════════════════
# detect_test_command 集成测试
# ═══════════════════════════════════════════════════════════════════════

class TestDetectTestCommand:
    def test_pytest_ini_wins(self, tmp_path):
        """多个匹配时，优先级最高的 pytest.ini 胜出。"""
        (tmp_path / "pytest.ini").write_text("[pytest]\n")
        (tmp_path / "package.json").write_text(json.dumps({"scripts": {"test": "jest"}}))
        (tmp_path / "Cargo.toml").write_text("[package]\n")
        cmd = detect_test_command(str(tmp_path))
        assert cmd == _PYTEST_CMD

    def test_none_when_no_config(self, tmp_path):
        cmd = detect_test_command(str(tmp_path))
        assert cmd is None

    def test_override_command(self, tmp_path):
        """手动指定命令应跳过自动检测。"""
        cmd = detect_test_command(str(tmp_path), override_command="npm test -- --watch")
        assert cmd == ["npm", "test", "--", "--watch"]

    def test_override_with_detected_file(self, tmp_path):
        """override 优先于文件检测。"""
        (tmp_path / "pytest.ini").write_text("[pytest]\n")
        cmd = detect_test_command(str(tmp_path), override_command="cargo test")
        assert cmd == ["cargo", "test"]

    def test_pyproject_toml(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text(
            "[tool.pytest.ini_options]\n",
            encoding="utf-8",
        )
        cmd = detect_test_command(str(tmp_path))
        assert cmd == _PYTEST_CMD

    def test_tox_ini(self, tmp_path):
        (tmp_path / "tox.ini").write_text("[tox]\n")
        cmd = detect_test_command(str(tmp_path))
        assert cmd == ["tox"]

    def test_package_json_with_lockfile(self, tmp_path):
        (tmp_path / "package.json").write_text(json.dumps({"scripts": {"test": "jest"}}))
        (tmp_path / "pnpm-lock.yaml").write_text("")
        cmd = detect_test_command(str(tmp_path))
        assert cmd == ["pnpm", "test"]

    def test_cargo_toml(self, tmp_path):
        (tmp_path / "Cargo.toml").write_text("[package]\n")
        cmd = detect_test_command(str(tmp_path))
        assert cmd == ["cargo", "test"]

    def test_go_mod(self, tmp_path):
        (tmp_path / "go.mod").write_text("module test\n")
        cmd = detect_test_command(str(tmp_path))
        assert cmd == ["go", "test", "./..."]

    def test_build_sbt(self, tmp_path):
        (tmp_path / "build.sbt").write_text('name := "test"\n')
        cmd = detect_test_command(str(tmp_path))
        assert cmd == ["sbt", "test"]

    def test_deno_json(self, tmp_path):
        (tmp_path / "deno.json").write_text("{}\n")
        cmd = detect_test_command(str(tmp_path))
        assert cmd == ["deno", "test"]

    def test_makefile(self, tmp_path):
        (tmp_path / "Makefile").write_text("test:\n\tnpm test\n")
        cmd = detect_test_command(str(tmp_path))
        assert cmd == ["make", "test"]
