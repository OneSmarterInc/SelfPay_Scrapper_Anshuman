from pathlib import Path
import pytest
import snapshot_io


def test_temporary_reader_lock_is_retried(tmp_path,monkeypatch):
    dest=tmp_path/'scan.json';dest.write_text('old')
    temporary=tmp_path/'scan.tmp';temporary.write_text('new')
    original=Path.replace;calls=[]
    def replace(path,target):
        calls.append(1)
        if len(calls)<3:raise PermissionError('Sharing violation')
        return original(path,target)
    monkeypatch.setattr(Path,'replace',replace);monkeypatch.setattr(snapshot_io.time,'sleep',lambda _:None)
    snapshot_io.replace_snapshot(temporary,dest)
    assert len(calls)==3 and dest.read_text()=='new' and not temporary.exists()


def test_permanent_lock_preserves_previous_snapshot_and_new_temporary_file(tmp_path,monkeypatch):
    dest=tmp_path/'scan.json';dest.write_text('old')
    temporary=tmp_path/'scan.tmp';temporary.write_text('new');calls=[]
    def replace(path,target):calls.append(1);raise PermissionError('Locked')
    monkeypatch.setattr(Path,'replace',replace);monkeypatch.setattr(snapshot_io.time,'sleep',lambda _:None)
    with pytest.raises(PermissionError):snapshot_io.replace_snapshot(temporary,dest)
    assert len(calls)==10 and dest.read_text()=='old' and temporary.read_text()=='new'
