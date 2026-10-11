"""Exports are checked for the files they claim, not believed.

Measured on 2026.0.315 (2026-10-11): ``export_api.ExportOBJ`` into a folder that did not
exist returned its path list exactly as a successful export does, and wrote nothing. The
listener therefore checks every ``Export*`` call while a script runs -- md_call's
generated source as much as a run_md_python script -- and md-mcp reports what is not
there beside the result that claimed it.

The fake host can only say whether the check reads what is on disk. Whether MD's own
calls behave as the fake's do, beyond the one measured case, is unproven here.
"""

from __future__ import annotations

import sys

import pytest

from md_mcp import bridge
from md_mcp.server import build_server
from tests.fake_md_host import running_listener
from tests.test_server import call, error_text, payload


@pytest.fixture
def md(monkeypatch):
    with running_listener(monkeypatch) as host:
        yield host


@pytest.fixture
def server():
    return build_server()


def test_an_export_that_claims_a_file_it_did_not_write_is_flagged(
    md, server, tmp_path
) -> None:
    md.exports_write = False
    target = (tmp_path / "checkpoint.zprj").as_posix()

    result = payload(call(server, "md_call", {
        "function": "export_api.ExportZPrj", "args": [target],
    }))

    assert result["result"] is True, "the fake should answer as MD does: success"
    assert result["missing_outputs"] == [target]
    assert "not there" in result["warning"]
    assert result["writes"][0]["call"] == "export_api.ExportZPrj"


def test_a_missing_parent_folder_is_made_and_said_so(md, server, tmp_path) -> None:
    # The measured failure, prevented rather than only reported: the folder that was not
    # there is the reason MD wrote nothing.
    folder = tmp_path / "not" / "yet"
    target = (folder / "shot.png").as_posix()

    result = payload(call(server, "md_call", {
        "function": "export_api.ExportSnapshot3D", "args": [target],
    }))

    assert folder.is_dir()
    assert (folder / "shot.png").is_file()
    assert [p.replace("\\", "/") for p in result["created_folders"]] == [folder.as_posix()]
    assert "missing_outputs" not in result
    assert "warning" not in result
    assert result["writes"][0]["written"]


def test_an_export_that_wrote_its_file_is_recorded_without_a_warning(
    md, server, tmp_path
) -> None:
    target = (tmp_path / "pattern.json").as_posix()

    result = payload(call(server, "md_call", {
        "function": "pattern_api.ExportPatternJSON", "args": [target],
    }))

    assert result["writes"] == [
        {"call": "pattern_api.ExportPatternJSON", "target": target, "written": [target]}
    ]
    assert "warning" not in result


def test_a_script_is_checked_however_it_reaches_the_call(md, server, tmp_path) -> None:
    # A script that imports the module itself rebinds the name to the module, and one
    # that imports the function takes it off the module -- both must still be checked.
    md.exports_write = False
    first = (tmp_path / "a.png").as_posix()
    second = (tmp_path / "b.png").as_posix()
    source = (
        "import export_api as exporter\n"
        f"exporter.ExportSnapshot3D({first!r})\n"
        "from export_api import ExportSnapshot3D\n"
        f"ExportSnapshot3D({second!r})\n"
        "print('exported')\n"
    )

    result = payload(call(server, "run_md_python", {"source": source}))

    assert result["output"].strip() == "exported"
    assert sorted(result["missing_outputs"]) == sorted([first, second])
    assert "warning" in result


def test_an_obj_export_from_a_script_is_checked_for_both_files(
    md, server, tmp_path
) -> None:
    md.exports_write = False
    target = (tmp_path / "garment.obj").as_posix()
    source = (
        "import ApiTypes, export_api\n"
        "option = ApiTypes.ImportExportOption()\n"
        f"print(export_api.ExportOBJ({target!r}, option))\n"
    )

    result = payload(call(server, "run_md_python", {"source": source}))

    assert target in result["missing_outputs"]
    assert (tmp_path / "garment.mtl").as_posix() in result["missing_outputs"]


def test_calls_that_write_nothing_are_not_reported(md, server) -> None:
    listing = payload(call(server, "md_call", {
        "function": "pattern_api.ExportObjectBrowserMaterialsList",
    }))
    count = payload(call(server, "md_call", {"function": "pattern_api.GetPatternCount"}))

    assert "Fabrics" in listing["result"]
    for result in (listing, count):
        assert set(result) == {"function", "result"}


def test_an_export_that_names_a_folder_and_returns_nothing_is_unverified(
    md, server, tmp_path
) -> None:
    # Measured: ExportTurntableImages returns [] and writes no files. With no file named
    # and none returned there is nothing to look for, and that is said rather than
    # passed off as a clean export.
    result = payload(call(server, "md_call", {
        "function": "export_api.ExportTurntableImages",
        "args": [tmp_path.as_posix(), 8, 100, 100, 0],
    }))

    assert result["result"] == []
    assert "nothing to check" in result["writes"][0]["unverified"]
    assert "warning" not in result


def test_checking_ends_with_the_script(md, tmp_path) -> None:
    # The wrapper must not outlive the run: MD's module would otherwise carry md-mcp's
    # code into the user's own scripts in the Python Editor.
    exporter = sys.modules["export_api"]
    original = exporter.ExportSnapshot3D

    bridge.run_script(f"export_api.ExportSnapshot3D({(tmp_path / 'x.png').as_posix()!r})")
    assert exporter.ExportSnapshot3D == original

    with pytest.raises(bridge.BridgeError):
        bridge.run_script(
            f"export_api.ExportZPrj({(tmp_path / 'x.zprj').as_posix()!r})\n"
            "raise RuntimeError('late')"
        )
    assert exporter.ExportSnapshot3D == original
    assert exporter.ExportZPrj == md.ExportZPrj


def test_a_failed_script_still_says_what_its_exports_left(md, tmp_path) -> None:
    md.exports_write = False
    target = (tmp_path / "lost.zprj").as_posix()

    with pytest.raises(bridge.BridgeError) as failure:
        bridge.run_script(
            f"export_api.ExportZPrj({target!r})\nraise RuntimeError('step two broke')"
        )

    message = str(failure.value)
    assert "step two broke" in message
    assert "Files checked before failure" in message
    assert target in message


def test_the_search_still_reads_export_calls_while_they_are_checked(md) -> None:
    # md_api runs through the same run op, so it sees the wrapped calls: they must keep
    # their names and documentation.
    found = [entry["call"] for entry in bridge.api("Export")]

    assert "export_api.ExportOBJ" in found
    assert "export_api.ExportSnapshot3D" in found


def test_an_older_listener_leaves_an_export_marked_unverified(
    md, server, monkeypatch, tmp_path
) -> None:
    # A listener from before the check sends no "writes". That is not "nothing was
    # written", and must not read as a clean export.
    real = bridge.request

    def older(op, timeout=bridge.QUERY_TIMEOUT, **body):
        reply = real(op, timeout=timeout, **body)
        reply.pop("writes", None)
        return reply

    monkeypatch.setattr(bridge, "request", older)
    result = payload(call(server, "md_call", {
        "function": "export_api.ExportZPrj",
        "args": [(tmp_path / "x.zprj").as_posix()],
    }))

    assert result["write_check"].startswith("unverified")
    plain = payload(call(server, "md_call", {"function": "pattern_api.GetPatternCount"}))
    assert "write_check" not in plain


def test_a_loop_of_exports_keeps_the_reply_small_and_problems_first(
    md, tmp_path
) -> None:
    # 25 clean exports, then 3 to a relative folder that does not exist -- relative, so
    # no folder is made for them and the fake writes nothing, as MD did.
    source = (
        "for k in range(25):\n"
        f"    export_api.ExportZPrj({tmp_path.as_posix()!r} + '/%d.zprj' % k)\n"
        "for k in range(3):\n"
        "    export_api.ExportZPrj('md-mcp-no-such-folder/%d.zprj' % k)\n"
    )

    reply = bridge.run_script(source)

    assert len(reply["writes"]) == 20
    assert reply["writes_omitted"] == 8
    assert [("missing_outputs" in record) for record in reply["writes"][:4]] == [
        True, True, True, False
    ]


def test_a_successful_run_says_it_checked_and_found_nothing_to_check(md) -> None:
    # Always present from this listener on, so its absence means an older listener.
    assert bridge.run_script("print(1)")["writes"] == []
