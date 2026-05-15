"""Regression tests for integration-suite xfail bookkeeping."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType


def _load_integration_conftest() -> ModuleType:
    path = Path(__file__).parents[1] / "integration" / "conftest.py"
    spec = importlib.util.spec_from_file_location("_notebooklm_integration_conftest", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_download_cli_vcr_cassette_drift_is_registered_as_xfail():
    conftest = _load_integration_conftest()
    nodeids = conftest._T8_A1_XFAIL_NODEIDS

    expected = {
        "tests/integration/cli_vcr/test_downloads.py::TestDownloadCommands::test_download[quiz-quiz.json-artifacts_download_quiz.yaml-extra_args0]",
        "tests/integration/cli_vcr/test_downloads.py::TestDownloadCommands::test_download[quiz-quiz.md-artifacts_download_quiz_markdown.yaml-extra_args1]",
        "tests/integration/cli_vcr/test_downloads.py::TestDownloadCommands::test_download[flashcards-flashcards.json-artifacts_download_flashcards.yaml-extra_args2]",
        "tests/integration/cli_vcr/test_downloads.py::TestDownloadCommands::test_download[flashcards-flashcards.md-artifacts_download_flashcards_markdown.yaml-extra_args3]",
        "tests/integration/cli_vcr/test_downloads.py::TestDownloadCommands::test_download[report-report.md-artifacts_download_report.yaml-extra_args4]",
        "tests/integration/cli_vcr/test_downloads.py::TestDownloadCommands::test_download[mind-map-mindmap.json-artifacts_download_mind_map.yaml-extra_args5]",
        "tests/integration/cli_vcr/test_downloads.py::TestDownloadCommands::test_download[data-table-data.csv-artifacts_download_data_table.yaml-extra_args6]",
    }

    assert expected <= nodeids
