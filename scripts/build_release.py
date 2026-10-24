"""Build an installable Kodi ZIP from an explicit credential-free file list."""

import argparse
from pathlib import Path
from xml.etree import ElementTree
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

ROOT = Path(__file__).resolve().parents[1]
FILES = (
    "addon.xml",
    "action.py",
    "service.py",
    "cleanup.py",
    "LICENSE",
    "README.md",
    "resources/__init__.py",
    "resources/settings.xml",
    "resources/lib/__init__.py",
    "resources/lib/runtime.py",
    "resources/lib/transport.py",
    "resources/language/resource.language.en_gb/strings.po",
)


def build(output=None):
    addon = ElementTree.parse(ROOT / "addon.xml").getroot()
    addon_id, version = addon.attrib["id"], addon.attrib["version"]
    output = Path(output) if output is not None else ROOT / "dist"
    output.mkdir(parents=True, exist_ok=True)
    target = output / (addon_id + "-" + version + ".zip")
    return write_zip(
        target, addon_id, {name: (ROOT / name).read_bytes() for name in FILES}
    )


def write_zip(target, addon_id, files):
    """Keep one deterministic packaging implementation for both Kodi addons."""
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(target, "w", compression=ZIP_DEFLATED) as archive:
        for name, content in files.items():
            info = ZipInfo(addon_id + "/" + name, date_time=(2026, 1, 1, 0, 0, 0))
            info.compress_type = ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, content)
    return target


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    print(build(parser.parse_args().output))
