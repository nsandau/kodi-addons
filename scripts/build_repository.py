"""Build a static Kodi repository for GitHub Pages from canonical addon manifests."""

import argparse
import hashlib
from html import escape
from pathlib import Path
from xml.etree import ElementTree

from scripts.build_release import ROOT, build, write_zip


def directory_index(directory, title, links):
    """Use simple directory-listing links that Kodi's HTTP file browser can read."""
    entries = "\n".join(
        f'<a href="{escape(url, quote=True)}">{escape(label)}</a>  -'
        for url, label in links
    )
    (directory / "index.html").write_text(
        '<!doctype html>\n<html lang="en"><head><meta charset="utf-8">'
        "<title>{0}</title></head><body><h1>{0}</h1><pre>\n{1}\n</pre></body></html>\n".format(
            escape(title), entries
        ),
        encoding="utf-8",
    )


def build_repository(output=None):
    output = Path(output) if output is not None else ROOT / "dist" / "site"
    if output.exists() and any(output.iterdir()):
        raise ValueError(
            "Repository output must be empty to avoid publishing stale or private files"
        )
    output.mkdir(parents=True, exist_ok=True)
    service = ElementTree.parse(ROOT / "addon.xml").getroot()
    repository_manifest = ROOT / "repository" / "addon.xml"
    repository = ElementTree.parse(repository_manifest).getroot()
    package_paths = []
    for addon in (service, repository):
        addon_id, version = addon.attrib["id"], addon.attrib["version"]
        folder = output / "addons" / addon_id
        if addon is service:
            package = build(folder)
        else:
            package = write_zip(
                folder / (addon_id + "-" + version + ".zip"),
                addon_id,
                {
                    "addon.xml": repository_manifest.read_bytes(),
                    "LICENSE": (ROOT / "LICENSE").read_bytes(),
                },
            )
        package_paths.append(package)
        directory_index(folder, addon.attrib["name"], [(package.name, package.name)])
    addons = ElementTree.Element("addons")
    addons.extend((service, repository))
    metadata = ElementTree.tostring(addons, encoding="utf-8", xml_declaration=True)
    (output / "addons.xml").write_bytes(metadata)
    (output / "addons.xml.sha256").write_text(
        hashlib.sha256(metadata).hexdigest() + "\n", encoding="ascii"
    )
    (output / ".nojekyll").touch()
    directory_index(
        output / "addons",
        "Addons",
        [
            (addon.attrib["id"] + "/", addon.attrib["id"] + "/")
            for addon in (repository, service)
        ],
    )
    bootstrap = package_paths[1].relative_to(output).as_posix()
    directory_index(
        output,
        "Watched Media Cleanup Repository",
        [
            (bootstrap, bootstrap),
            ("addons/", "addons/"),
            (
                "https://github.com/nsandau/kodi-watched-cleanup#readme",
                "Installation and configuration",
            ),
        ],
    )
    return output


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    print(build_repository(parser.parse_args().output))
