"""Read-only VM disk size parsing must never invent a capacity guarantee."""
import pytest
from app.integrations import _disk_size_bytes


@pytest.mark.parametrize("value,expected", [
    ("store:vm-100-disk-0,size=20G",20*1024**3),
    ("store:vm-100-disk-0,discard=on,size=1024M",1024**3),
    ("store:vm-100-disk-0,size=1.5T",int(1.5*1024**4)),
    ("store:vm-100-disk-0,size=1048576",1048576),
    ("store:vm-100-disk-0,size=0.5G",512*1024**2),
])
def test_valid_pve_disk_sizes(value,expected):
    assert _disk_size_bytes(value)==expected


@pytest.mark.parametrize("value", [
    None, "", "store:vm-100-disk-0", "store:vm-100-disk-0,size=foo",
    "store:vm-100-disk-0,size=-1G", "store:vm-100-disk-0,size=0G",
    "store:vm-100-disk-0,size=1G,size=2G",
    "store:vm-100-disk-0,size=1000Q",
    "store:vm-100-disk-0,size=999999999999999999E",
])
def test_unknown_or_invalid_sizes_are_not_treated_as_zero(value):
    assert _disk_size_bytes(value) is None
